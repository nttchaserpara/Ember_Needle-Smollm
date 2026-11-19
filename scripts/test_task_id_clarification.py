"""Check that task mutations require one explicit task ID."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from needle_router import _task_id_clarification


def main():
    cases = [
        ("Complete the task.", "missing_task_id"),
        ("Mark that task as done.", "missing_task_id"),
        ("Remove the task from my list.", "missing_task_id"),
        ("Complete task number 17.", None),
        ("Complete task number 17 or 18.", "ambiguous_task_id"),
        ("Delete file task.txt", None),
        ("Delete all completed tasks.", None),
    ]
    for query, expected in cases:
        result = _task_id_clarification(query)
        actual = result["reason"] if result else None
        assert actual == expected, (query, expected, result)
        print(f"PASS {query}")


if __name__ == "__main__":
    main()
