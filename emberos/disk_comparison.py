"""Bounded, synchronous agents for comparing two reported disk readings."""

from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from uuid import uuid4
import re
import time


_REQUEST = re.compile(
    r"(?:please )?compare (?:my |the )?(?:current )?(?:free )?disk space (?:with|to) "
    r"(?:the (?:disk )?report from |what we (?:saw|recorded) )?(?:last time|the previous session)[?.!]?",
    re.I,
)
_DISK_ROW = re.compile(r"(\S+)\s+\d+(?:\.\d+)?[GT]\s+\d+(?:\.\d+)?[GT]\s+(\d+(?:\.\d+)?)([GT])\s+\d+%")
_MAX_REPORT_CHARS = 4096
_MAX_QUERY_CHARS = 4096
_HANDOFF_SECONDS = 5
_PLAN_STEPS = ("retrieval", "disk_observer", "verifier")


@dataclass(frozen=True)
class DiskReading:
    mount: str
    amount: Decimal
    unit: str

    @property
    def gib(self):
        return self.amount * (1024 if self.unit == "T" else 1)


@dataclass(frozen=True)
class HistoricalReading:
    turn_id: int
    reading: DiskReading


@dataclass(frozen=True)
class ComparisonPlan:
    steps: tuple[str, ...] = _PLAN_STEPS
    max_handoffs: int = len(_PLAN_STEPS)


_TASK_FIELDS = {
    "planner": frozenset({"query"}),
    "retrieval": frozenset({"scope", "source_tool"}),
    "disk_observer": frozenset({"mount", "source_id"}),
    "verifier": frozenset({"evidence", "current"}),
}
_RESULT_STATUSES = {
    "planner": frozenset({"planned", "not_applicable", "cancelled", "unresolved"}),
    "retrieval": frozenset({"found", "no_evidence"}),
    "disk_observer": frozenset({"observed", "unavailable"}),
    "verifier": frozenset({"verified", "unavailable"}),
}


@dataclass(frozen=True)
class AgentTask:
    """A role-specific input envelope with a strict, bounded field set."""

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
        if self.role == "planner":
            query = self.payload["query"]
            if not isinstance(query, str) or len(query) > _MAX_QUERY_CHARS:
                raise ValueError("invalid planner query")
        elif self.role == "retrieval":
            if self.payload != {"scope": "previous_session", "source_tool": "disk_usage"}:
                raise ValueError("retrieval scope is restricted to a previous disk report")
        elif self.role == "disk_observer":
            if (self.payload["mount"] not in {"/", "C:\\"}
                    or type(self.payload["source_id"]) is not int or self.payload["source_id"] < 1):
                raise ValueError("invalid disk observation input")
        else:
            if (not isinstance(self.payload["evidence"], HistoricalReading)
                    or self.payload["current"] is not None and not isinstance(self.payload["current"], DiskReading)):
                raise ValueError("invalid verifier evidence")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@dataclass(frozen=True)
class AgentResult:
    """A compact role result tied to its task and auditable source, if any."""

    role: str
    task_id: str
    status: str
    output: object = None
    source_id: int | None = None
    reason: str | None = None

    def __post_init__(self):
        if self.role not in _RESULT_STATUSES or not isinstance(self.task_id, str) or not self.task_id:
            raise ValueError("invalid agent result identity")
        if self.status not in _RESULT_STATUSES[self.role]:
            raise ValueError("invalid agent result status")
        if self.source_id is not None and (type(self.source_id) is not int or self.source_id < 1):
            raise ValueError("invalid agent result source")
        if self.reason is not None and (not isinstance(self.reason, str) or len(self.reason) > 128):
            raise ValueError("invalid agent result reason")
        if self.role == "planner" and self.status == "planned" and not isinstance(self.output, ComparisonPlan):
            raise ValueError("planner must return a bounded plan")
        if self.role == "retrieval" and self.status == "found":
            if not isinstance(self.output, HistoricalReading) or self.source_id != self.output.turn_id:
                raise ValueError("retrieval result must include its source id")
        if self.role == "disk_observer" and self.status == "observed":
            if not isinstance(self.output, DiskReading) or self.source_id is None:
                raise ValueError("disk observation must include its source id")
        if self.role == "verifier" and self.status == "verified" and not isinstance(self.output, str):
            raise ValueError("verified result must include a response")


def _new_task(role, payload, parent_task_id=None):
    return AgentTask(role, uuid4().hex, payload, parent_task_id)


def _task_matches(task, result):
    return isinstance(result, AgentResult) and result.role == task.role and result.task_id == task.task_id


def _trace_result(task, result, **details):
    item = {"role": task.role, "task_id": task.task_id, "parent_task_id": task.parent_task_id,
            "status": result.status, "reason": result.reason, "source_id": result.source_id}
    item.update(details)
    return item


def system_drive(report):
    """Read the rounded Free column in the existing disk_usage table format."""
    if not isinstance(report, str) or len(report) > _MAX_REPORT_CHARS:
        return None
    lines = report.splitlines()
    if not lines or lines[0].split() != ["Drive", "Size", "Used", "Free", "Use%"]:
        return None
    found = []
    for line in lines[1:]:
        match = _DISK_ROW.fullmatch(line.strip())
        if match and match[1].upper() in {"/", "C:\\"}:
            found.append(DiskReading(match[1].upper(), Decimal(match[2]), match[3]))
    return found[0] if len(found) == 1 else None


def plan_comparison(task):
    """Coordinator/planner: accept one narrow request and return a fixed role plan."""
    if task.role != "planner":
        raise ValueError("planner received a task for another role")
    text = " ".join(task.payload["query"].casefold().replace("’", "'").split())
    if _REQUEST.fullmatch(text):
        return AgentResult("planner", task.task_id, "planned", ComparisonPlan())
    prefix = re.match(r"(?:please )?(?P<negation>don't |do not |never )?compare "
                      r"(?:my |the )?(?:current )?(?:free )?disk space\b", text)
    if not prefix:
        return AgentResult("planner", task.task_id, "not_applicable")
    if prefix["negation"]:
        return AgentResult("planner", task.task_id, "cancelled", reason="negated_request")
    return AgentResult("planner", task.task_id, "unresolved", reason="extra_or_ambiguous_clause")


def retrieve_previous(task, read_previous):
    """Retrieval specialist: use only the scoped history-read capability."""
    if task.role != "retrieval":
        raise ValueError("retrieval specialist received a task for another role")
    result = read_previous()
    data = result.data or {}
    if (not result.success or not isinstance(data, dict) or set(data) != {"source", "scope", "tool", "turn_id"}
            or data.get("scope") != task.payload["scope"]
            or data.get("source") != "conversation_history" or data.get("tool") != task.payload["source_tool"]
            or type(data.get("turn_id")) is not int or data["turn_id"] < 1):
        return AgentResult("retrieval", task.task_id, "no_evidence", reason="source_or_scope_mismatch")
    reading = system_drive(result.result)
    if not reading:
        return AgentResult("retrieval", task.task_id, "no_evidence", reason="no_usable_disk_report")
    evidence = HistoricalReading(data["turn_id"], reading)
    return AgentResult("retrieval", task.task_id, "found", evidence, source_id=evidence.turn_id)


def observe_drive(task, read_disk):
    """OS observation specialist: receives a mount and source id, never history text."""
    if task.role != "disk_observer":
        raise ValueError("disk observer received a task for another role")
    result = read_disk()
    current = system_drive(result.result) if result.success else None
    if current is None:
        reason = result.status if not result.success else "invalid_disk_report"
        return AgentResult("disk_observer", task.task_id, "unavailable", source_id=task.payload["source_id"],
                           reason=reason)
    if current.mount != task.payload["mount"]:
        return AgentResult("disk_observer", task.task_id, "unavailable", source_id=task.payload["source_id"],
                           reason="drive_mismatch")
    return AgentResult("disk_observer", task.task_id, "observed", current, source_id=task.payload["source_id"])


def verify_comparison(task):
    """Verifier: compare only typed readings from the same mount and cite history ID."""
    if task.role != "verifier":
        raise ValueError("verifier received a task for another role")
    evidence = task.payload["evidence"]
    current = task.payload["current"]
    if current is None or current.mount != evidence.reading.mount:
        return AgentResult("verifier", task.task_id, "unavailable", source_id=evidence.turn_id,
                           reason="same_drive_reading_unavailable")
    old = evidence.reading
    units = {"G": "GiB", "T": "TiB"}
    change = current.gib - old.gib
    direction = "unchanged" if change == 0 else ("higher" if change > 0 else "lower")
    response = (f"Previous session (history #{evidence.turn_id}): {old.amount} {units[old.unit]} free on {old.mount}.\n"
                f"Now (disk_usage): {current.amount} {units[current.unit]} free on {current.mount}.\n"
                f"The reported free space is {direction} using the rounded values.")
    return AgentResult("verifier", task.task_id, "verified", response, source_id=evidence.turn_id)


def compare_if_requested(query, registry):
    planner_task = _new_task("planner", {"query": query})
    plan_result = plan_comparison(planner_task)
    if not _task_matches(planner_task, plan_result):
        return None
    if plan_result.status == "not_applicable":
        return None

    trace = [_trace_result(planner_task, plan_result,
                           planned_roles=list(plan_result.output.steps) if plan_result.status == "planned" else [])]
    calls = []
    if plan_result.status in {"cancelled", "unresolved"}:
        response = ("No disk comparison was performed." if plan_result.status == "cancelled" else
                    "To compare saved and current readings, ask: Compare disk space with last time.")
        return {"route": "disk_comparison", "success": False, "status": plan_result.status,
                "response": response, "trace": trace, "calls": calls}
    plan = plan_result.output
    if plan.steps != _PLAN_STEPS or plan.max_handoffs != len(_PLAN_STEPS):
        return {"route": "disk_comparison", "success": False, "status": "unresolved",
                "response": "I couldn't safely plan this comparison.", "trace": trace, "calls": calls}

    deadline = time.monotonic() + _HANDOFF_SECONDS

    def execute(name, arguments):
        calls.append({"name": name, "arguments": arguments})
        return registry.execute_tool(name, arguments)

    def finish(response, success):
        return {"route": "disk_comparison", "response": response, "success": success,
                "status": "success" if success else "partial", "trace": trace, "calls": calls}

    retrieval_task = _new_task("retrieval", {"scope": "previous_session", "source_tool": "disk_usage"},
                               planner_task.task_id)
    evidence_result = retrieve_previous(
        retrieval_task,
        lambda: execute("search_conversation_history", {"scope": "previous_session", "source_tool": "disk_usage"}),
    )
    if not _task_matches(retrieval_task, evidence_result):
        trace.append({"role": "retrieval", "task_id": retrieval_task.task_id,
                      "parent_task_id": retrieval_task.parent_task_id, "status": "invalid_result",
                      "reason": "contract_violation", "source_id": None})
        return finish("I couldn't verify a usable previous disk report.", False)
    evidence = evidence_result.output if evidence_result.status == "found" else None
    trace.append(_trace_result(retrieval_task, evidence_result))
    if evidence is None:
        return finish("I couldn't find a usable system-drive report in the previous saved session.", False)
    if time.monotonic() >= deadline:
        return finish("The time limit expired before the current disk check.", False)

    observer_task = _new_task("disk_observer", {"mount": evidence.reading.mount, "source_id": evidence.turn_id},
                              retrieval_task.task_id)
    observation = observe_drive(observer_task, lambda: execute("disk_usage", {}))
    if not _task_matches(observer_task, observation):
        trace.append({"role": "disk_observer", "task_id": observer_task.task_id,
                      "parent_task_id": observer_task.parent_task_id, "status": "invalid_result",
                      "reason": "contract_violation", "source_id": evidence.turn_id, "tool": "disk_usage"})
        return finish("I couldn't verify a current reading for the same drive.", False)
    trace.append(_trace_result(observer_task, observation, tool="disk_usage"))
    if time.monotonic() >= deadline:
        return finish("The disk check exceeded the comparison time limit.", False)

    verifier_task = _new_task("verifier", {"evidence": evidence, "current": observation.output},
                              observer_task.task_id)
    verification = verify_comparison(verifier_task)
    if not _task_matches(verifier_task, verification):
        trace.append({"role": "verifier", "task_id": verifier_task.task_id,
                      "parent_task_id": verifier_task.parent_task_id, "status": "invalid_result",
                      "reason": "contract_violation", "source_id": evidence.turn_id})
        return finish("I couldn't verify a current reading for the same drive.", False)
    trace.append(_trace_result(verifier_task, verification))
    return finish(verification.output or "I couldn't verify a current reading for the same drive.",
                  verification.status == "verified")
