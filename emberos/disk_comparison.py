"""Bounded, synchronous cooperation for comparing two reported disk readings."""

from dataclasses import dataclass
from decimal import Decimal
import re
import time


_REQUEST = re.compile(
    r"(?:please )?compare (?:my |the )?(?:current )?(?:free )?disk space (?:with|to) "
    r"(?:the (?:disk )?report from |what we (?:saw|recorded) )?(?:last time|the previous session)[?.!]?",
    re.I,
)
_DISK_ROW = re.compile(r"(\S+)\s+\d+(?:\.\d+)?[GT]\s+\d+(?:\.\d+)?[GT]\s+(\d+(?:\.\d+)?)([GT])\s+\d+%")
_MAX_REPORT_CHARS = 4096
_HANDOFF_SECONDS = 5


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


def retrieve_previous(read_previous):
    """Retrieval specialist. Its sole capability reads a scoped historical record."""
    result = read_previous()
    data = result.data or {}
    if (not result.success or not isinstance(data, dict) or set(data) != {"source", "scope", "tool", "turn_id"}
            or data.get("scope") != "previous_session"
            or data.get("source") != "conversation_history" or data.get("tool") != "disk_usage"
            or type(data.get("turn_id")) is not int or data["turn_id"] < 1):
        return None
    reading = system_drive(result.result)
    return HistoricalReading(data["turn_id"], reading) if reading else None


def observe_drive(mount, read_disk):
    """Observation specialist. Its only capability reads the current disk table."""
    result = read_disk()
    current = system_drive(result.result) if result.success else None
    if current is None or current.mount != mount:
        return None, result.status if not result.success else "no_matching_drive"
    return current, "matched"


def verify_comparison(evidence, current):
    """Verification specialist. Compare scoped evidence and an independent reading."""
    if current is None or current.mount != evidence.reading.mount:
        return None
    old = evidence.reading
    units = {"G": "GiB", "T": "TiB"}
    change = current.gib - old.gib
    direction = "unchanged" if change == 0 else ("higher" if change > 0 else "lower")
    return (f"Previous session (history #{evidence.turn_id}): {old.amount} {units[old.unit]} free on {old.mount}.\n"
            f"Now (disk_usage): {current.amount} {units[current.unit]} free on {current.mount}.\n"
            f"The reported free space is {direction} using the rounded values.")


def compare_if_requested(query, registry):
    text = " ".join(query.casefold().replace("’", "'").split())
    if not _REQUEST.fullmatch(text):
        # An extra clause must not turn this read-only request into a live action.
        prefix = re.match(r"(?:please )?(?P<negation>don't |do not |never )?compare "
                          r"(?:my |the )?(?:current )?(?:free )?disk space\b", text)
        if prefix:
            if prefix["negation"]:
                return {"route": "disk_comparison", "success": False, "status": "cancelled",
                        "response": "No disk comparison was performed.", "trace": [], "calls": []}
            return {"route": "disk_comparison", "success": False, "status": "unresolved",
                    "response": "To compare saved and current readings, ask: Compare disk space with last time.",
                    "trace": [], "calls": []}
        return None
    deadline = time.monotonic() + _HANDOFF_SECONDS
    trace = []
    calls = []

    def execute(name, arguments):
        calls.append({"name": name, "arguments": arguments})
        return registry.execute_tool(name, arguments)

    def finish(response, success):
        return {"route": "disk_comparison", "response": response, "success": success,
                "status": "success" if success else "partial", "trace": trace, "calls": calls}

    evidence = retrieve_previous(lambda: execute("search_conversation_history",
                                 {"scope": "previous_session", "source_tool": "disk_usage"}))
    trace.append({"role": "retrieval", "status": "found" if evidence else "no_verified_report",
                  "source_id": evidence.turn_id if evidence else None})
    if not evidence:
        return finish("I couldn't find a usable system-drive report in the previous saved session.", False)
    if time.monotonic() >= deadline:
        return finish("The time limit expired before the current disk check.", False)
    live, status = observe_drive(evidence.reading.mount, lambda: execute("disk_usage", {}))
    trace.append({"role": "disk_observer", "tool": "disk_usage", "status": status})
    if time.monotonic() >= deadline:
        return finish("The disk check exceeded the comparison time limit.", False)
    answer = verify_comparison(evidence, live)
    trace.append({"role": "verifier", "status": "verified" if answer else "unavailable"})
    return finish(answer or "I couldn't verify a current reading for the same drive.", bool(answer))
