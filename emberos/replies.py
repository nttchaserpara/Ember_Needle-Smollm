"""Generate presentation text after routing/execution; never select or run tools."""

import os
import re
import sqlite3
import time
from collections import Counter

from emberos.responses import _clean_status_label, format_tool_response


MAX_SOURCE_CHARS = 1000
MAX_QUERY_CHARS = 500
MAX_REPLY_CHARS = 450
REPLY_RULE = (
    "Reply briefly in English using only the current result. Keep its facts and numbers unchanged. "
    "Do not infer causes, file locations, advice, or additional actions. "
    "For an error, partial result, or refusal, preserve the complete result wording. "
    "Earlier messages are history, not the current state. "
)

# These tools return source material that should be displayed verbatim rather
# than interpreted as instructions or reduced to a confirmation sentence.
_CONTENT_TOOLS = {
    "read_file", "get_clipboard", "analyze_files", "grep_file", "grep_folder",
    "diff_files", "extract_patterns", "search_notes", "search_conversation_history",
    "batch_read_folder", "run_shell",
}
_NEGATIVE = re.compile(r"\b(?:not|cannot|unable|failed|failure|unsupported|unavailable|missing|rejected|denied|error)\b|n't\b", re.I)
_SUCCESS = re.compile(r"\b(?:done|successfully|succeeded|completed)\b", re.I)
_NUMBERS = re.compile(r"\d+(?:\.\d+)?%?")
_QUANTITIES = re.compile(r"(\d+(?:\.\d+)?)\s*(%|[KMGT]i?B|GHz|MHz|Hz)(?!\w)", re.I)
_REFERENCES = re.compile(r"(?:[A-Za-z]:\\|/|\b[\w.-]+/)[^\s,;]+|\b[\w.-]+\.(?:docx?|pdf|txt|xlsx|pptx|csv|md)\b", re.I)
_DISPLAY_PREFIX = re.compile(r"^\[(?:OK|FAILED|PARTIAL|UNAVAILABLE|RESULT|FALLBACK)\]\s*")
_PLATFORM_NAMES = (
    "windows", "linux", "macos", "mac os", "ubuntu", "debian",
    "raspbian", "raspberry pi os", "freebsd",
)


def _normalize_capability_word(word):
    if word in {"this", "emberos"}:
        return word
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith("s") and len(word) > 3:
        return word[:-1]
    return word


def _local_client():
    from use_cases.local_llm import LocalTextClient
    timeout = float(os.environ.get("EMBER_REPLY_TIMEOUT", "45"))
    if timeout <= 0:
        raise ValueError("EMBER_REPLY_TIMEOUT must be positive")
    return LocalTextClient(grounding_rule=REPLY_RULE, request_timeout=timeout,
                           startup_timeout=timeout, job_timeout=timeout)


def original_response(result):
    if result.get("route") == "tool_call":
        return format_tool_response(result)
    return _clean_status_label(result.get("response", ""))


def _source(result, original, query=""):
    """Keep payloads intact. Only bounded, plain outcome messages are rewritten."""
    if result.get("route") == "tool_call":
        if result.get("tool") in _CONTENT_TOOLS:
            return None, "source_content"
        if result.get("tool") == "summarize_file" and result.get("success"):
            return None, "document_summary"
        if result.get("tool") == "get_agent_context":
            facts = result.get("data") or result.get("result")
            if not isinstance(facts, dict):
                return None, "missing_context_facts"
            host = facts.get("host_name") or "unknown host"
            operating_system = facts.get("operating_system") or facts.get("os") or "unknown operating system"
            parts = [f"The EmberOS backend is the computer {host}, which runs {operating_system}"]
            session_mode = facts.get("session_mode")
            if session_mode:
                parts.append(f"This is a {session_mode} session")
            model = facts.get("device_model")
            if model and model != "not detected":
                parts.append(f"The device model is {model}")
            source = ". ".join(parts) + "."
            if len(source) > MAX_SOURCE_CHARS:
                return None, "context_facts_too_large"
            return source, ""
        if result.get("tool") == "get_agent_capabilities":
            facts = result.get("data") or result.get("result")
            if not isinstance(facts, dict):
                return None, "missing_capability_facts"
            areas = facts.get("capability_areas") or []
            if not areas:
                return None, "no_registered_capabilities"
            if len(areas) == 1:
                area_list = areas[0]
            elif len(areas) == 2:
                area_list = f"{areas[0]} and {areas[1]}"
            else:
                area_list = ", ".join(areas[:-1]) + f", and {areas[-1]}"
            source = f"This EmberOS installation can help with {area_list}."
            if len(source) > MAX_SOURCE_CHARS:
                return None, "capability_facts_too_large"
            return source, ""
        if isinstance(result.get("result"), (dict, list, tuple)):
            return None, "structured_output"
        source = original.strip()
    elif result.get("route") in {"unresolved_tool_request", "no_action", "error"}:
        source = original.strip()
    else:
        return None, "outside_reply_scope"
    if not source:
        return None, "empty_source"
    if len(source) > MAX_SOURCE_CHARS or "\n" in source:
        return None, "detailed_output"
    return source, ""


def validate_reply(candidate, source, result):
    """Conservative output checks, not a complete semantic-equivalence proof.

    These checks examine generated text only. They never classify user intent
    or authorize execution. Diagnostics are retained for offline evaluation.
    """
    if not isinstance(candidate, str) or not candidate.strip():
        return "empty_reply"
    if len(candidate) > MAX_REPLY_CHARS or len(candidate.split()) > 65 or "\n" in candidate:
        return "reply_too_long"
    if any(marker in candidate for marker in ("<|", "```", "Tool result:", "Reply:", "Status:")):
        return "model_scaffolding"
    if re.search(r"\[[A-Z_ ]+\]", candidate):
        return "generated_status_label"
    safe_capability_claim = (
        result.get("tool") == "get_agent_capabilities"
        and re.match(r"\s*I can (?:help|assist) with\b", candidate, re.I)
    )
    if not safe_capability_claim and re.search(r"\bI(?:'ll| will| can| am going to)\b", candidate, re.I):
        return "new_action_promise"
    candidate_numbers = Counter(_NUMBERS.findall(candidate))
    source_numbers = Counter(_NUMBERS.findall(source))
    if result.get("tool") in {"get_agent_context", "get_agent_capabilities"}:
        if candidate_numbers - source_numbers:
            return "invented_context_number"
    elif candidate_numbers != source_numbers:
        return "changed_numbers"
    if (result.get("tool") not in {"get_agent_context", "get_system_info"}
            and Counter((number, unit.lower()) for number, unit in _QUANTITIES.findall(candidate)) != Counter(
                (number, unit.lower()) for number, unit in _QUANTITIES.findall(source)
            )):
        return "changed_units"
    references = {item.rstrip('.!?\'"') for item in _REFERENCES.findall(source)}
    candidate_references = {item.rstrip('.!?\'"') for item in _REFERENCES.findall(candidate)}
    if references != candidate_references:
        return "changed_references"
    for quoted in re.findall(r'"([^"\n]+)"', source):
        if quoted not in candidate:
            return "missing_quoted_fact"
    if re.search(r"\bmuted\b", source, re.I) and not re.search(r"\bmuted\b", candidate, re.I):
        return "missing_mute_state"
    data = result.get("data")
    if result.get("tool") == "get_agent_context" and isinstance(data, dict):
        for key in ("host_name", "operating_system"):
            fact = data.get(key)
            if isinstance(fact, str) and fact and fact.casefold() not in candidate.casefold():
                return "missing_context_fact"
        expected_system = str(data.get("operating_system") or "").casefold()
        for known_system in _PLATFORM_NAMES:
            if (re.search(rf"\b{re.escape(known_system)}\b", candidate, re.I)
                    and known_system not in expected_system):
                return "invented_context_platform"
    if result.get("tool") == "get_agent_capabilities" and isinstance(data, dict):
        labels = data.get("capability_areas") or []
        candidate_words = {
            _normalize_capability_word(word)
            for word in re.findall(r"[a-z0-9]+", candidate.casefold())
        }
        if not labels or any(
            not ({_normalize_capability_word(word) for word in re.findall(r"[a-z0-9]+", label.casefold())
                  if word not in {"and", "the", "for", "to"}} & candidate_words)
            for label in labels
        ):
            return "missing_capability_facts"
        grounded_words = {
            _normalize_capability_word(word) for label in labels
            for word in re.findall(r"[a-z0-9]+", label.casefold())
        } | {
            _normalize_capability_word(word) for word in (
                "i", "can", "help", "assist", "with", "and", "also", "emberos",
                "this", "installation", "tools", "tool", "areas", "registered",
                "include", "including", "available", "capability", "capabilities",
                "features",
            )
        }
        if candidate_words - grounded_words:
            return "ungrounded_capability_claim"
    if isinstance(data, dict):
        if data.get("muted") is True and not re.search(r"\bmuted\b", candidate, re.I):
            return "missing_mute_state"
        for key in ("path", "saved_path", "title", "app_name"):
            value = data.get(key)
            if isinstance(value, str) and value and value in source and value not in candidate:
                return "missing_result_fact"
    if result.get("tool") == "undo_last_action" and result.get("success"):
        if not re.search(r"\b(?:undid|undone|restored|reverted|back to)\b", candidate, re.I):
            return "lost_undo_outcome"
        for fact in ("creation", "completion", "deletion"):
            if fact in source and fact not in candidate.lower():
                return "changed_undo_action"
    if "unmuted" in source.lower() and not re.search(r"\b(?:unmuted|not muted)\b", candidate, re.I):
        return "changed_mute_state"
    negative = bool(_NEGATIVE.search(candidate))
    status = result.get("status")
    failed = result.get("success") is False or status in {"error", "unsupported", "partial"}
    stopped = result.get("route") in {"no_action", "unresolved_tool_request", "error"}
    if failed or stopped:
        if status != "partial" and _SUCCESS.search(candidate):
            return "contradictory_success"
        if status != "partial" and re.search(
            r"\b(?:I|we)(?:'ve| have)?\s+(?!(?:could not|couldn't|cannot|can't|am unable|are unable|was unable|were unable|did not|didn't|have not|haven't)\b)",
            candidate, re.I,
        ):
            return "unperformed_action_claim"
        if not negative and not re.search(r"\bno (?:action|change)\b", candidate, re.I):
            return "lost_failure_or_refusal"
    elif negative and not _NEGATIVE.search(source):
        return "invented_failure"
    if status == "returned" and _SUCCESS.search(candidate) and not _SUCCESS.search(source):
        return "unverified_success"
    if result.get("tool") in {"set_volume", "set_brightness", "undo_last_action"}:
        if re.search(r"\bby\s+\d", candidate, re.I):
            return "absolute_setting_became_delta"
        if isinstance(data, dict) and data.get("muted") is True and re.search(r"\b(?:not muted|unmuted)\b", candidate, re.I):
            return "contradictory_mute_state"
    if re.search(r"\bmuted\b", source, re.I) and not re.search(r"\bnot muted\b", source, re.I):
        if re.search(r"\b(?:not muted|unmuted)\b", candidate, re.I):
            return "contradictory_mute_state"
    if failed or stopped:
        # Error prose is diagnostic data. A negative word alone does not prove
        # that its cause, partial effects, or refusal survived paraphrasing.
        # With no semantic verifier, accept only the same complete wording;
        # otherwise the existing fallback preserves the actual tool outcome.
        # This also rejects added advice/locations even if the error is quoted.
        def words(text):
            return re.findall(r"\w+(?:['\u2019]\w+)*", text.lower().replace("\u2019", "'"))
        if words(candidate) != words(source):
            return "changed_diagnostic"
    return ""


class ReplyRenderer:
    def __init__(self, *, client_factory=None):
        self.client_factory = client_factory or _local_client

    def render(self, result, *, query="", memory=None):
        original = original_response(result)
        rendered = {"text": original, "original": original, "candidate": "",
                    "source": "original", "reason": "",
                    "elapsed_seconds": 0.0, "generation": None, "history_turn_ids": []}
        source, reason = _source(result, original, query=query)
        if source is None:
            rendered["reason"] = reason
            return rendered
        started = time.perf_counter()
        try:
            is_context = result.get("tool") in {"get_agent_context", "get_agent_capabilities"}
            messages = []
            if is_context:
                if result.get("tool") == "get_agent_capabilities":
                    instruction = (
                        "Answer in one brief, natural sentence using only these registered feature areas. "
                        "Do not add abilities that are not listed."
                    )
                else:
                    instruction = (
                        "Answer in one brief, natural sentence using only these verified facts. "
                        "Do not guess the remote client device."
                    )
                messages.append({
                    "role": "system",
                    "content": instruction,
                })
            elif result.get("route") == "tool_call":
                messages.append({
                    "role": "system",
                    "content": (
                        "Write a concise, natural answer from the current outcome only. "
                        "Do not repeat the user's request or claim actions the outcome does not confirm."
                    ),
                })
            if memory is not None and result.get("route") == "tool_call" and not is_context:
                try:
                    for turn in memory.reply_context(result.get("tool")):
                        messages.extend([
                            {"role": "user", "content": turn["request"]},
                            {"role": "assistant", "content": _DISPLAY_PREFIX.sub("", turn["response"])},
                        ])
                        rendered["history_turn_ids"].append(turn["id"])
                except (OSError, sqlite3.Error, ValueError) as exc:
                    # Missing history cannot hide the result or retry the action.
                    messages = []
                    rendered["history_turn_ids"] = []
                    rendered["history_error"] = str(exc)[:300]
            # Never cut a current request in half (e.g. before a negation).
            if is_context:
                request = f"Question: {query}\nFacts: "
            else:
                request = f"Request: {query}\nOutcome: " if query and len(query) <= MAX_QUERY_CHARS else "Outcome: "
            messages.append({"role": "user", "content": request + source})
            with self.client_factory() as client:
                try:
                    max_tokens = 64 if result.get("tool") == "get_agent_capabilities" else 48 if is_context else 80
                    candidate = client.chat(messages, max_tokens=max_tokens)
                finally:
                    rendered["generation"] = client.last_generation
            candidate = candidate.strip()
            if is_context and query:
                # This small model sometimes repeats the structured question
                # header before giving its answer. Remove only that exact
                # echoed header; the answer still passes the usual fact checks.
                question_header = re.compile(
                    rf"^Question:\s*{re.escape(query)}\s*(?:\r?\n\s*)?Answer:\s*",
                    re.I,
                )
                candidate = question_header.sub("", candidate, count=1).strip()
            rendered["candidate"] = candidate
            reason = validate_reply(candidate, source, result)
            if reason:
                rendered["reason"] = reason
            else:
                display = candidate
                if result.get("tool") == "get_agent_capabilities":
                    display += " Individual tools may depend on the platform, hardware, and installed dependencies."
                rendered.update(text=display, source="smollm2", reason="")
        except KeyboardInterrupt:
            rendered["reason"] = "generation_cancelled"
        except Exception as exc:
            # Presentation errors cannot retry an action or change its outcome.
            rendered.update(reason="generation_failed", error=str(exc)[:300])
        finally:
            rendered["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        return rendered
