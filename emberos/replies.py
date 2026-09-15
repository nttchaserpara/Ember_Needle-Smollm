"""Generate presentation text after routing/execution; never select or run tools."""

import os
import re
import sqlite3
import time
from collections import Counter

from emberos.responses import format_tool_response


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


def _local_client():
    from use_cases.local_llm import LocalTextClient
    timeout = float(os.environ.get("EMBER_REPLY_TIMEOUT", "45"))
    if timeout <= 0:
        raise ValueError("EMBER_REPLY_TIMEOUT must be positive")
    return LocalTextClient(grounding_rule=REPLY_RULE, request_timeout=timeout,
                           startup_timeout=timeout, job_timeout=timeout)


def original_response(result):
    return format_tool_response(result) if result.get("route") == "tool_call" else result.get("response", "")


def _source(result, original):
    """Keep payloads intact. Only bounded, plain outcome messages are rewritten."""
    if result.get("route") == "tool_call":
        if result.get("tool") in _CONTENT_TOOLS:
            return None, "source_content"
        if result.get("tool") == "summarize_file" and result.get("success"):
            return None, "document_summary"
        if isinstance(result.get("result"), (dict, list, tuple)):
            return None, "structured_output"
        source = original.partition("]")[2].strip()
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
    if re.search(r"\bI(?:'ll| will| can| am going to)\b", candidate, re.I):
        return "new_action_promise"
    if Counter(_NUMBERS.findall(candidate)) != Counter(_NUMBERS.findall(source)):
        return "changed_numbers"
    if Counter((number, unit.lower()) for number, unit in _QUANTITIES.findall(candidate)) != Counter(
        (number, unit.lower()) for number, unit in _QUANTITIES.findall(source)
    ):
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
        source, reason = _source(result, original)
        if source is None:
            rendered["reason"] = reason
            return rendered
        started = time.perf_counter()
        try:
            messages = []
            if memory is not None and result.get("route") == "tool_call":
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
            request = f'Respond to "{query}" using this result: ' if query and len(query) <= MAX_QUERY_CHARS else "Respond using this result: "
            messages.append({"role": "user", "content": request + source})
            with self.client_factory() as client:
                try:
                    candidate = client.chat(messages, max_tokens=80)
                finally:
                    rendered["generation"] = client.last_generation
            candidate = candidate.strip()
            rendered["candidate"] = candidate
            reason = validate_reply(candidate, source, result)
            if reason:
                rendered["reason"] = reason
            else:
                prefix = original.partition("]")[0] + "] " if result.get("route") == "tool_call" else ""
                rendered.update(text=prefix + candidate, source="smollm2", reason="")
        except KeyboardInterrupt:
            rendered["reason"] = "generation_cancelled"
        except Exception as exc:
            # Presentation errors cannot retry an action or change its outcome.
            rendered.update(reason="generation_failed", error=str(exc)[:300])
        finally:
            rendered["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        return rendered
