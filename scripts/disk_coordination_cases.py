"""Labeled end-to-end cases for the disk comparison coordinator."""

from contextlib import nullcontext
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from emberos import disk_comparison
from emberos.tools import ToolRegistry
from web_ember import WebSafeToolRegistry


ROLES = ("planner", "retrieval", "os_action", "disk_observer", "verifier")
TOOLS = ("search_conversation_history", "disk_usage")


@dataclass(frozen=True)
class Case:
    label: str
    kind: str
    roles: tuple
    tools: tuple
    answer_fragments: tuple
    old: str | None
    now: str | None
    query: str
    fault: str = ""


def evaluate(query, table, run_case):
    old, now = table("40G"), table("33G")
    stopped = ("couldn't verify",)
    cases = [
        Case("decrease", "compare", ROLES, TOOLS, ("history #1): 40 GiB", "Now (disk_usage): 33 GiB", "lower"), old, now, query),
        Case("unchanged", "compare", ROLES, TOOLS, ("history #1): 40 GiB", "Now (disk_usage): 40 GiB", "unchanged"), old, old, query),
        Case("missing history", "stop", ROLES[:2], TOOLS[:1], ("previous saved session",), None, now, query),
        Case("invalid history", "stop", ROLES[:2], TOOLS[:1], ("previous saved session",), "unknown", now, query),
        Case("live read failed", "stop", ROLES, TOOLS, stopped, old, None, query),
        Case("drive conflict", "stop", ROLES, TOOLS, stopped, old, table("33G", "C:\\"), query),
        Case("observer source conflict", "stop", ROLES[:4], TOOLS, stopped, old, now, query, "observe_drive"),
        Case("verifier source conflict", "stop", ROLES, TOOLS, stopped, old, now, query, "verify_comparison"),
        Case("negation", "stop", ROLES[:1], (), ("No disk comparison",), old, now, "Don't compare disk space with last time."),
        Case("history only", "direct", (), TOOLS[:1], ("historical records", "97G"), old, now,
             "Show my recent conversations."),
    ]
    rows = []
    for registry in (ToolRegistry, WebSafeToolRegistry):
        for case in cases:
            injection = nullcontext()
            if case.fault:
                specialist = getattr(disk_comparison, case.fault)

                def wrong_source(task):
                    result = specialist(task)
                    return replace(result, source_id=result.source_id + 1)

                injection = patch.object(disk_comparison, case.fault, side_effect=wrong_source)
            with TemporaryDirectory() as directory, injection:
                result = run_case(registry, Path(directory), case.query, case.old, case.now)
            trace = result["trace"]
            roles = tuple(item["role"] for item in trace)
            ids = [item.get("task_id") for item in trace]
            linked = all(ids) and len(set(ids)) == len(ids) and all(
                item.get("parent_task_id") == (ids[i-1] if i else None) for i, item in enumerate(trace)
            )
            route = "tool_call" if case.kind == "direct" else "disk_comparison"
            delegated = roles == case.roles and linked and result["route"] == route
            calls_ok = tuple(result["calls"]) == case.tools
            answer_ok = all(fragment in result["answer"] for fragment in case.answer_fragments)
            outcome_ok = result["success"] == (case.kind != "stop") and answer_ok
            checks = {"delegation": delegated, "tool_sequence": calls_ok, "outcome": outcome_ok}
            if case.kind == "compare":
                checks["source"] = ([item.get("source_id") for item in trace[1:]] == [1, 1, 1, 1]
                                    and answer_ok and "97 GiB" not in result["answer"])
            if case.kind == "stop":
                checks["safe_stop"] = (outcome_ok and calls_ok and "Now (disk_usage)" not in result["answer"]
                                       and (not case.fault or bool(trace) and trace[-1]["status"] == "invalid_result"))
            rows.append({"case": case.label, "registry": registry.__name__, "kind": case.kind,
                         "checks": checks, "passed": all(checks.values()), **result})

    def score(values):
        return {"passed": sum(values), "total": len(values)}

    metrics = {key: score([r["checks"][key] for r in rows if key in r["checks"]])
               for key in ("delegation", "source", "safe_stop")}
    metrics["completion"] = score([r["passed"] for r in rows if r["kind"] == "compare"])
    return {"passed": all(r["passed"] for r in rows), "cases": score([r["passed"] for r in rows]),
            "metrics": metrics, "results": rows}
