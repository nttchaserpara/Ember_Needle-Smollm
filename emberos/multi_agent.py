"""Bounded multi-source retrieval plus live read-only system checks."""

from dataclasses import dataclass
import math
import re
import time
from types import MappingProxyType
from uuid import uuid4

from emberos.retrieval import retrieval_hit
from emberos.undo import READ_ONLY_TOOLS


MAX_QUERY_CHARS = 512
MAX_TOPIC_CHARS = 120
MAX_LIVE_RESULT_CHARS = 1200
MAX_HANDOFFS = 3
HANDOFF_SECONDS = 5

LIVE_READS = {
    "disk_usage": "disk space",
    "ram_status": "RAM",
    "cpu_info": "CPU information",
    "cpu_temperature": "CPU temperature",
    "system_uptime": "system uptime",
    "battery_status": "battery status",
}
_LIVE_ALIASES = {
    "disk_usage": {"disk", "disk space", "free disk space", "storage", "free storage"},
    "ram_status": {"ram", "memory", "memory usage", "available memory", "available ram", "ram usage"},
    "cpu_info": {"cpu", "processor", "cpu information", "processor information"},
    "cpu_temperature": {"cpu temperature", "cpu temp", "processor temperature"},
    "system_uptime": {"uptime", "system uptime"},
    "battery_status": {"battery", "battery status"},
}
_COMPOSITE_PREFIX = re.compile(
    r"^\s*(?:(?:please\s+)?(?:do\s+not|don't|never)\s+)?(?:please\s+)?"
    r"(?:search|find|look\s+up)\s+"
    r"(?:(?:my|our|saved|previous|current|the)\s+)?"
    r"(?:notes?|conversation\s+history|conversations?|chats?|messages?)\b.*\b(?:and|then)\b",
    re.IGNORECASE,
)
_NEGATED_COMPOSITE = re.compile(
    r"^\s*(?:(?:please\s+)?(?:do\s+not|don't|never)\s+)"
    r"(?:search|find|look\s+up)\b",
    re.IGNORECASE,
)
_MIXED_REQUEST = re.compile(
    r"^\s*(?:please\s+)?(?:search|find|look\s+up)\s+"
    r"(?P<source>(?:(?:my|our|saved|previous|current|the)\s+)?"
    r"(?:notes?|conversation\s+history|conversations?|chats?|messages?))\s+"
    r"(?:for|about)\s+(?P<topic>.+?)\s+(?:and(?:\s+then)?|then)\s+"
    r"(?:check|show|get|read)\s+(?:(?:my|the)\s+)?(?:current|live)\s+"
    r"(?P<fact>[^.!?\n]+?)"
    r"(?:\s*,?\s*(?:and\s+|then\s+)?summari[sz]e(?:\s+(?:it|the\s+results|what\s+you\s+found))?)?"
    r"\s*[?.!]*\s*$",
    re.IGNORECASE,
)
_SCOPE_SUFFIXES = (
    (re.compile(r"\s+(?:in|from)\s+(?:the\s+)?previous\s+session$", re.I), "previous_session"),
    (re.compile(r"\s+(?:in|from)\s+(?:the\s+)?last\s+session$", re.I), "previous_session"),
    (re.compile(r"\s+last\s+time$", re.I), "previous_session"),
    (re.compile(r"\s+in\s+this\s+session$", re.I), "current_session"),
)


@dataclass(frozen=True)
class MultiAgentPlan:
    source: str
    query: str
    scope: str
    state_tool: str
    state_label: str
    steps: tuple[str, ...] = ("retrieval", "os_action", "verifier")
    max_handoffs: int = MAX_HANDOFFS

    def __post_init__(self):
        if self.source not in {"notes", "conversation_history"}:
            raise ValueError("invalid retrieval source")
        if not isinstance(self.query, str) or not 1 <= len(self.query) <= MAX_TOPIC_CHARS:
            raise ValueError("invalid retrieval query")
        if self.scope not in {"all", "current_session", "previous_session"}:
            raise ValueError("invalid retrieval scope")
        if (self.state_tool not in LIVE_READS or self.state_tool not in READ_ONLY_TOOLS
                or self.state_label != LIVE_READS[self.state_tool]):
            raise ValueError("invalid live-state tool")
        if self.steps != ("retrieval", "os_action", "verifier") or self.max_handoffs != MAX_HANDOFFS:
            raise ValueError("invalid multi-agent plan")


@dataclass(frozen=True)
class RetrievedEvidence:
    source: str
    record_id: int
    title: str
    timestamp: str
    snippet: str
    score: float
    rank: int
    record_status: str = ""


@dataclass(frozen=True)
class LiveReading:
    tool: str
    success: bool
    status: str
    text: str


_TASK_FIELDS = {
    "planner": frozenset({"query"}),
    "retrieval": frozenset({"source", "query", "scope"}),
    "os_action": frozenset({"tool", "source_id"}),
    "verifier": frozenset({"plan", "evidence", "live"}),
}
_RESULT_STATUSES = {
    "planner": frozenset({"planned", "not_applicable", "unresolved", "cancelled"}),
    "retrieval": frozenset({"found", "no_evidence", "unavailable"}),
    "os_action": frozenset({"proposed", "unavailable"}),
    "verifier": frozenset({"verified", "partial", "unavailable"}),
}


@dataclass(frozen=True)
class AgentTask:
    role: str
    task_id: str
    payload: dict
    parent_task_id: str | None = None

    def __post_init__(self):
        if self.role not in _TASK_FIELDS or not isinstance(self.task_id, str) or not self.task_id:
            raise ValueError("invalid agent task identity")
        if self.parent_task_id is not None and (not isinstance(self.parent_task_id, str) or not self.parent_task_id):
            raise ValueError("invalid parent task identity")
        if type(self.payload) is not dict or set(self.payload) != _TASK_FIELDS[self.role]:
            raise ValueError("invalid agent task fields")
        if self.role == "planner" and (not isinstance(self.payload["query"], str)
                                        or len(self.payload["query"]) > MAX_QUERY_CHARS):
            raise ValueError("invalid planner query")
        if self.role == "retrieval":
            if (self.payload["source"] not in {"notes", "conversation_history"}
                    or not isinstance(self.payload["query"], str)
                    or not 1 <= len(self.payload["query"]) <= MAX_TOPIC_CHARS
                    or self.payload["scope"] not in {"all", "current_session", "previous_session"}):
                raise ValueError("invalid retrieval task")
        if self.role == "os_action" and (self.payload["tool"] not in LIVE_READS
                                          or type(self.payload["source_id"]) is not int
                                          or self.payload["source_id"] < 1):
            raise ValueError("invalid live-state task")
        if self.role == "verifier" and (not isinstance(self.payload["plan"], MultiAgentPlan)
                                         or self.payload["evidence"] is not None
                                         and not isinstance(self.payload["evidence"], RetrievedEvidence)
                                         or not isinstance(self.payload["live"], LiveReading)):
            raise ValueError("invalid verifier task")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@dataclass(frozen=True)
class AgentResult:
    role: str
    task_id: str
    status: str
    output: object = None
    source_id: int | None = None
    reason: str | None = None

    def __post_init__(self):
        if (self.role not in _RESULT_STATUSES or not isinstance(self.task_id, str) or not self.task_id
                or self.status not in _RESULT_STATUSES[self.role]):
            raise ValueError("invalid agent result")
        if self.source_id is not None and (type(self.source_id) is not int or self.source_id < 1):
            raise ValueError("invalid agent source ID")
        if self.reason is not None and (not isinstance(self.reason, str) or len(self.reason) > 128):
            raise ValueError("invalid agent result reason")
        if self.role == "planner" and self.status == "planned" and not isinstance(self.output, MultiAgentPlan):
            raise ValueError("planner must return a bounded plan")
        if self.role == "retrieval" and self.status == "found":
            if not isinstance(self.output, RetrievedEvidence) or self.source_id != self.output.record_id:
                raise ValueError("retrieval must return evidence with its source ID")
        if self.role == "os_action" and self.status == "proposed":
            if (type(self.output) is not dict or self.source_id is None
                    or self.output != {"name": self.output.get("name"), "arguments": {}}):
                raise ValueError("OS Action Agent must return one empty-argument proposal")
        if self.role == "verifier" and self.status in {"verified", "partial"} and not isinstance(self.output, str):
            raise ValueError("verifier must return a response")


def _new_task(role, payload, parent_task_id=None):
    return AgentTask(role, uuid4().hex, payload, parent_task_id)


def _task_matches(task, result):
    return isinstance(result, AgentResult) and result.role == task.role and result.task_id == task.task_id


def _state_tool(fact):
    key = " ".join(fact.casefold().split())
    for tool, aliases in _LIVE_ALIASES.items():
        if key in aliases:
            return tool
    return None


def _build_plan(query):
    match = _MIXED_REQUEST.fullmatch(query)
    if not match:
        return None
    source_text = " ".join(match["source"].casefold().split())
    source = "notes" if "note" in source_text else "conversation_history"
    scope = "all"
    topic = " ".join(match["topic"].split()).strip(" \t\r\n\"'")
    for suffix, value in _SCOPE_SUFFIXES:
        if suffix.search(topic):
            topic = suffix.sub("", topic).strip()
            scope = value
            break
    if source_text.startswith("previous "):
        scope = "previous_session"
    elif source_text.startswith("current "):
        scope = "current_session"
    if source == "notes" and scope != "all":
        return None
    state_tool = _state_tool(match["fact"])
    if not topic or len(topic) > MAX_TOPIC_CHARS or state_tool is None:
        return None
    if any(ord(character) < 32 for character in topic):
        return None
    return MultiAgentPlan(source, topic, scope, state_tool, LIVE_READS[state_tool])


def plan_request(task):
    """Deterministic coordinator: accept only explicit retrieval + live-read phrasing."""
    if task.role != "planner":
        raise ValueError("planner received a task for another role")
    query = task.payload["query"]
    if not _COMPOSITE_PREFIX.search(query):
        return AgentResult("planner", task.task_id, "not_applicable")
    if _NEGATED_COMPOSITE.match(query):
        return AgentResult("planner", task.task_id, "cancelled", reason="negated_request")
    plan = _build_plan(query)
    if plan is None:
        return AgentResult("planner", task.task_id, "unresolved", reason="unsupported_or_ambiguous_plan")
    return AgentResult("planner", task.task_id, "planned", plan)


def retrieve_evidence(task, execute):
    """Read notes or history and require a source-checked structured result."""
    if task.role != "retrieval":
        raise ValueError("retrieval agent received a task for another role")
    source, query, scope = (task.payload[name] for name in ("source", "query", "scope"))
    if source == "notes":
        name, arguments = "search_notes", {"query": query}
    else:
        name, arguments = "search_conversation_history", {"query": query, "scope": scope, "source_tool": ""}
    result = execute(name, arguments)
    if result is None:
        return AgentResult("retrieval", task.task_id, "unavailable", reason="empty_tool_result")
    data = getattr(result, "data", None)
    if not isinstance(data, dict):
        return AgentResult("retrieval", task.task_id, "unavailable", reason="missing_retrieval_metadata")
    expected_data_fields = {"source", "query", "scope", "status", "retrieval", "results"}
    if source == "conversation_history":
        expected_data_fields.add("turn_ids")
    if set(data) != expected_data_fields:
        return AgentResult("retrieval", task.task_id, "no_evidence", reason="invalid_retrieval_metadata")
    if (data.get("source") != source or data.get("query") != query or data.get("scope") != scope
            or data.get("retrieval") != data.get("status")):
        return AgentResult("retrieval", task.task_id, "no_evidence", reason="source_or_scope_mismatch")
    if data.get("status") == "no_matches" and data.get("results") == []:
        return AgentResult("retrieval", task.task_id, "no_evidence", reason="no_matching_evidence")
    hits = data.get("results")
    if (getattr(result, "success", False) is not True or data.get("status") != "matches"
            or not isinstance(hits, list) or not 1 <= len(hits) <= 5):
        return AgentResult("retrieval", task.task_id, "unavailable", reason="invalid_retrieval_result")
    expected_hit_fields = {"source", "record_id", "title", "timestamp", "snippet", "score", "rank", "status"}
    if source == "conversation_history":
        expected_hit_fields.add("record_status")
    for rank, hit in enumerate(hits, start=1):
        score = hit.get("score") if isinstance(hit, dict) else None
        if (not isinstance(hit, dict) or set(hit) != expected_hit_fields
                or hit.get("source") != source or type(hit.get("record_id")) is not int
                or hit["record_id"] < 1 or type(hit.get("rank")) is not int or hit["rank"] != rank
                or hit.get("status") != "matched" or not isinstance(hit.get("title"), str)
                or len(hit["title"]) > 200 or not isinstance(hit.get("timestamp"), str)
                or len(hit["timestamp"]) > 64 or not isinstance(hit.get("snippet"), str)
                or len(hit["snippet"]) > 240 or isinstance(score, bool)
                or not isinstance(score, (int, float)) or not math.isfinite(score) or score < 0
                or source == "conversation_history" and (not isinstance(hit.get("record_status"), str)
                                                           or len(hit["record_status"]) > 64)):
            return AgentResult("retrieval", task.task_id, "no_evidence", reason="invalid_evidence_envelope")
    hit = hits[0]
    if source == "conversation_history":
        ids = data.get("turn_ids")
        if not isinstance(ids, list) or ids != [item["record_id"] for item in hits]:
            return AgentResult("retrieval", task.task_id, "no_evidence", reason="source_id_mismatch")
    evidence = RetrievedEvidence(
        source, hit["record_id"], hit["title"], hit["timestamp"], hit["snippet"],
        float(hit["score"]), 1, hit.get("record_status", ""),
    )
    return AgentResult("retrieval", task.task_id, "found", evidence, source_id=evidence.record_id)


def propose_live_read(task):
    """OS Action Agent proposes only the planned read-only tool with empty args."""
    if task.role != "os_action":
        raise ValueError("OS Action Agent received a task for another role")
    return AgentResult("os_action", task.task_id, "proposed",
                       {"name": task.payload["tool"], "arguments": {}},
                       source_id=task.payload["source_id"])


def verify_and_respond(task):
    """Verifier checks evidence/tool identity, then returns a source-labelled summary."""
    if task.role != "verifier":
        raise ValueError("verifier received a task for another role")
    plan = task.payload["plan"]
    evidence = task.payload["evidence"]
    live = task.payload["live"]
    if evidence is None:
        return AgentResult("verifier", task.task_id, "unavailable", reason="no_retrieved_evidence")
    if evidence.source != plan.source or live.tool != plan.state_tool:
        return AgentResult("verifier", task.task_id, "unavailable", source_id=evidence.record_id,
                           reason="source_or_tool_mismatch")
    source_label = "saved note" if evidence.source == "notes" else "historical conversation"
    saved = (f"{source_label.title()} #{evidence.record_id}: {evidence.title}"
             f" ({evidence.timestamp or 'time unavailable'}).\nSaved excerpt: {evidence.snippet}")
    if not live.success or not live.text:
        response = (f"{saved}\n\nI couldn't verify current {plan.state_label} "
                    f"({plan.state_tool}: {live.status}). No comparison was made.")
        return AgentResult("verifier", task.task_id, "partial", response,
                           source_id=evidence.record_id, reason="live_state_unavailable")
    response = (f"{saved}\n\nCurrent {plan.state_label} ({plan.state_tool}):\n{live.text}\n\n"
                "The saved excerpt is historical evidence; the system reading is current.")
    return AgentResult("verifier", task.task_id, "verified", response,
                       source_id=evidence.record_id)


def _trace(task, result, **extra):
    item = {"role": task.role, "task_id": task.task_id,
            "parent_task_id": task.parent_task_id, "status": result.status,
            "reason": result.reason, "source_id": result.source_id}
    item.update(extra)
    return item


def orchestrate_if_requested(query, registry):
    """Run a narrow, general retrieval + live-state workflow; otherwise defer to Needle."""
    if not isinstance(query, str) or len(query) > MAX_QUERY_CHARS or not _COMPOSITE_PREFIX.search(query):
        return None
    planner_task = _new_task("planner", {"query": query})
    planner = plan_request(planner_task)
    if planner.status == "not_applicable":
        return None
    trace = [_trace(planner_task, planner,
                    planned_roles=list(planner.output.steps) if planner.status == "planned" else [])]
    if planner.status == "cancelled":
        return {"route": "multi_agent", "success": False, "status": "cancelled",
                "reason": "negated_request", "response": "No action taken.", "trace": trace, "calls": []}
    if planner.status != "planned":
        return {"route": "multi_agent", "success": False,
                "status": "clarification_required", "reason": planner.reason,
                "response": ("I couldn't safely plan that combined request. Use one clear saved-source "
                             "search and one current read-only check (RAM, disk, CPU, uptime, or battery). "
                             "No tools were called."), "trace": trace, "calls": []}

    plan = planner.output
    if plan.max_handoffs != MAX_HANDOFFS or len(plan.steps) > MAX_HANDOFFS:
        return {"route": "multi_agent", "success": False,
                "status": "unresolved", "reason": "handoff_limit_exceeded",
                "response": "I couldn't safely plan that combined request. No tools were called.",
                "trace": trace, "calls": []}
    deadline = time.monotonic() + HANDOFF_SECONDS
    calls = []

    def execute(name, arguments):
        if name not in READ_ONLY_TOOLS:
            raise ValueError("tool is outside the multi-agent read-only allowlist")
        if name == "search_notes":
            allowed_arguments = {"query": plan.query}
        elif name == "search_conversation_history":
            allowed_arguments = {"query": plan.query, "scope": plan.scope, "source_tool": ""}
        elif name in LIVE_READS:
            allowed_arguments = {}
        else:
            raise ValueError("tool is outside the multi-agent read-only allowlist")
        if arguments != allowed_arguments:
            raise ValueError("tool arguments do not match the bounded plan")
        registry.validate_arguments(name, arguments)
        calls.append({"name": name, "arguments": dict(arguments)})
        return registry.execute_tool(name, arguments)

    retrieval_task = _new_task("retrieval", {"source": plan.source, "query": plan.query,
                                              "scope": plan.scope}, planner_task.task_id)
    try:
        retrieval_result = retrieve_evidence(retrieval_task, execute)
    except (TypeError, ValueError):
        retrieval_result = AgentResult("retrieval", retrieval_task.task_id,
                                       "unavailable", reason="retrieval_tool_rejected")
    source_conflict = (
        isinstance(retrieval_result, AgentResult)
        and retrieval_result.status == "found"
        and (not isinstance(retrieval_result.output, RetrievedEvidence)
             or retrieval_result.source_id != retrieval_result.output.record_id
             or retrieval_result.output.source != plan.source
             or type(retrieval_result.output.record_id) is not int
             or retrieval_result.output.record_id < 1)
    )
    if not _task_matches(retrieval_task, retrieval_result):
        retrieval_result = AgentResult("retrieval", retrieval_task.task_id,
                                       "unavailable", reason="contract_violation")
    elif source_conflict:
        retrieval_result = AgentResult("retrieval", retrieval_task.task_id,
                                       "unavailable", reason="source_id_mismatch")
    trace.append(_trace(retrieval_task, retrieval_result, source=plan.source, scope=plan.scope))
    evidence = retrieval_result.output if retrieval_result.status == "found" else None
    if evidence is None:
        if plan.source == "notes":
            not_found = f"No matching notes were found for '{plan.query}'."
        else:
            not_found = f"No matching conversation history was found for '{plan.query}'."
        message = f"{not_found} I did not check current {plan.state_label}."
        return {"route": "multi_agent", "success": False, "status": "partial",
                "reason": retrieval_result.reason, "response": message,
                "trace": trace, "calls": calls}
    if time.monotonic() >= deadline:
        return {"route": "multi_agent", "success": False, "status": "partial",
                "reason": "handoff_timeout", "response": "The multi-agent time limit expired before the live check.",
                "trace": trace, "calls": calls}

    action_task = _new_task("os_action", {"tool": plan.state_tool, "source_id": evidence.record_id},
                            retrieval_task.task_id)
    action = propose_live_read(action_task)
    if (not _task_matches(action_task, action) or action.status != "proposed"
            or not isinstance(action.output, dict)
            or action.output != {"name": plan.state_tool, "arguments": {}}
            or action.source_id != evidence.record_id):
        trace.append(_trace(action_task, action, tool=plan.state_tool, decision="rejected"))
        return {"route": "multi_agent", "success": False, "status": "partial",
                "reason": "tool_proposal_rejected", "response": "The live read proposal was rejected; no OS tool ran.",
                "trace": trace, "calls": calls}
    try:
        live_result = execute(plan.state_tool, {})
    except (TypeError, ValueError):
        live_result = None
    trace.append(_trace(action_task, action, tool=plan.state_tool, arguments={}, decision="accepted"))
    if live_result is None:
        live = LiveReading(plan.state_tool, False, "invalid_arguments", "")
    else:
        raw = getattr(live_result, "result", None)
        text = raw if isinstance(raw, str) else "" if raw is None else str(raw)
        live = LiveReading(plan.state_tool, getattr(live_result, "success", False) is True,
                           str(getattr(live_result, "status", "error"))[:64],
                           text[:MAX_LIVE_RESULT_CHARS])
    if time.monotonic() >= deadline:
        return {"route": "multi_agent", "success": False, "status": "partial",
                "reason": "handoff_timeout", "response": "The multi-agent time limit expired during the live check.",
                "trace": trace, "calls": calls}

    verifier_task = _new_task("verifier", {"plan": plan, "evidence": evidence, "live": live},
                              action_task.task_id)
    verification = verify_and_respond(verifier_task)
    if not _task_matches(verifier_task, verification):
        verification = AgentResult("verifier", verifier_task.task_id,
                                   "unavailable", source_id=evidence.record_id,
                                   reason="contract_violation")
    trace.append(_trace(verifier_task, verification))
    return {"route": "multi_agent", "success": verification.status == "verified",
            "status": "success" if verification.status == "verified" else "partial",
            "response": verification.output or "I couldn't verify both sources.",
            "trace": trace, "calls": calls,
            "source": {"type": evidence.source, "id": evidence.record_id,
                       "title": evidence.title, "timestamp": evidence.timestamp}}
