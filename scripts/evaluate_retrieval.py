"""Evaluate the first five note and conversation hits with synthetic binary relevance labels."""

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import platform
import sqlite3
from statistics import mean
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from emberos.memory import ConversationMemory


# Stable fixture keys keep judgments independent of SQLite IDs and timestamps.
NOTES = [
    ("restore", "Backup restore checklist", "Verify the archive before recovery.", ["recovery"]),
    ("capacity", "Disk space audit", "The system drive has 27 GiB free.", ["storage"]),
    ("backup_limit", "Backup capacity", "Reserve disk space for the nightly archive.", ["storage"]),
    ("poster", "Typography review", "The sample poster uses the words 'disk space' as filler.", ["design"]),
    ("meeting", "Scholarship deadline", "Submit the application on Friday.", ["education"]),
    ("garden", "Garden plan", "The backup volunteer will water the seedlings.", ["horticulture"]),
    ("choir", "Choir rehearsal", "The backup singer arrives at noon.", ["music"]),
    ("team", "Team selection", "Name a backup goalkeeper on Friday.", ["football"]),
    ("power", "Generator inspection", "Test the backup power supply.", ["electrical"]),
]
# Oldest session first. All requests and answers are disposable.
SESSIONS = {
    "archive": [
        ("old_drive", "Check disk space", "31 GiB available on the drive."),
    ],
    "previous_session": [
        ("drive", "Check disk space", "27 GiB available on the drive."),
        ("grant", "Review scholarship deadline", "The application is due Friday."),
        ("paper", "Review documents", "We reviewed the scholarship proposal."),
    ],
    "current_session": [
        ("today_drive", "Check disk space", "24 GiB available on the drive."),
        ("poster_chat", "Review the disk space poster", "Approve the font size."),
        ("trace", 'File "/sample/Documents/tools.py", line 4, in main', "An import failed."),
        ("paper_today", "Review documents", "We reviewed the garden proposal."),
    ],
}
# Each judgment describes the information need independently of what the search returns.
# An empty relevant set marks a query where an empty result is the desired outcome.
CASES = [
    ("notes", None, "disk space", "Capacity planning and measurements", ["capacity", "backup_limit"]),
    ("notes", None, "recovery", "Recovering a backup", ["restore"]),
    ("notes", None, "storage", "Notes tagged for storage planning", ["capacity", "backup_limit"]),
    ("notes", None, "free drive capacity", "Amount of available space on the system drive", ["capacity"]),
    ("notes", None, "deadline scholarship", "Scholarship submission deadline", ["meeting"]),
    ("notes", None, "restore backup", "Looking for instructions to restore a data backup", ["restore"]),
    ("notes", None, "ocean tides", "Unrepresented topic", []),
    ("history", "all", "disk space", "Measurements of free space on the drive", ["old_drive", "drive", "today_drive"]),
    ("history", "previous_session", "disk space", "Previous session's disk measurement", ["drive"]),
    ("history", "current_session", "documents", "Documents reviewed this session", ["paper_today"]),
    ("history", "all", "documents", "Document reviews across sessions", ["paper", "paper_today"]),
    ("history", "all", "grant due date", "Deadline for the scholarship application", ["grant"]),
    ("history", "current_session", "scholarship", "Current-session discussion of the scholarship", []),
]


def score(ranked, relevant):
    hits = [rank for rank, key in enumerate(ranked, start=1) if key in relevant]
    return {
        "precision_at_5": len(hits) / 5,
        "reciprocal_rank_at_5": 1 / hits[0] if hits else 0,
        "recall_at_5": len(hits) / len(relevant),
    }


def evaluate(directory, notes_fixture=NOTES, sessions=SESSIONS, cases=CASES):
    # notes.py resolves its default data directory on import.
    os.environ["EMBER_DATA_DIR"] = str(directory)
    from use_cases.notes import NotesManager

    results = []
    with closing(NotesManager(directory / "notes.sqlite3")) as notes:
        note_keys = {}
        for key, title, content, tags in notes_fixture:
            note = notes.add(title, content, tags)
            notes.export_text(note)
            note_keys[note["id"]] = key

        turn_keys = {}
        for session, turns in sessions.items():
            memory = ConversationMemory(directory / "history.sqlite3")
            for key, request, answer in turns:
                memory.record(request, answer, {"status": "success"})
                turn_keys[memory.session_id, request] = (key, session)

        for source, scope, query, need, relevant in cases:
            outside_scope = []
            if source == "notes":
                rows = notes.search(query, limit=5)
                ranked = [note_keys[row["id"]] for row in rows]
                known = set(note_keys.values())
            else:
                rows = memory.search(query, limit=5, scope=scope)
                turns = [turn_keys[row["session_id"], row["request"]] for row in rows]
                ranked = [key for key, session in turns]
                outside_scope = [key for key, session in turns if scope != "all" and session != scope]
                known = {key for key, session in turn_keys.values() if scope == "all" or session == scope}
            if not set(relevant) <= known or len(relevant) != len(set(relevant)):
                raise ValueError(f"Invalid relevance labels for {query!r}")
            if len(ranked) > 5 or len(set(ranked)) != len(ranked):
                raise ValueError(f"Invalid ranked list for {query!r}")
            scores = score(ranked, relevant) if relevant else None
            passed = (bool(ranked) and ranked[0] in relevant) if relevant else not ranked
            results.append({"source": source, "scope": scope, "query": query, "need": need,
                            "relevant": relevant, "ranked": ranked, "scores": scores,
                            "outside_scope": outside_scope, "passed": bool(passed and not outside_scope)})
    return results


def summarize(rows):
    positive = [row["scores"] for row in rows if row["relevant"]]
    empty = [row for row in rows if not row["relevant"]]
    return {
        "queries_with_relevant_records": len(positive),
        "precision_at_5": mean(s["precision_at_5"] for s in positive),
        "mrr_at_5": mean(s["reciprocal_rank_at_5"] for s in positive),
        "recall_at_5": mean(s["recall_at_5"] for s in positive),
        "empty_correct": sum(not row["ranked"] for row in empty),
        "empty_queries": len(empty),
        "scope_leaks": sum(len(row["outside_scope"]) for row in rows),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--suite", choices=("original", "transfer"), default="original")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="ember-ir-") as temp:
        if args.suite == "transfer":
            fixture_path = Path(__file__).parent / "fixtures" / "retrieval_transfer.json"
            fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
            rows = evaluate(Path(temp), fixture["notes"], fixture["sessions"], fixture["cases"])
        else:
            rows = evaluate(Path(temp))
    summaries = {source: summarize([row for row in rows if row["source"] == source])
                 for source in ("notes", "history")}
    summaries["all"] = summarize(rows)
    passed = sum(row["passed"] for row in rows)
    report = {"suite": args.suite, "python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
              "passed": passed, "total": len(rows), "summary": summaries, "results": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for source, summary in summaries.items():
        print(f"{source}: P@5={summary['precision_at_5']:.3f} MRR@5={summary['mrr_at_5']:.3f} "
              f"Recall@5={summary['recall_at_5']:.3f} empty={summary['empty_correct']}/{summary['empty_queries']} "
              f"scope leaks={summary['scope_leaks']}")
    for row in rows:
        if not row["passed"]:
            print(f"FAIL {row['source']} [{row['scope'] or 'notes'}] {row['query']!r}: {row['ranked']}")
    print(f"{passed}/{len(rows)} first-hit or empty-result checks passed. Report: {args.output}")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
