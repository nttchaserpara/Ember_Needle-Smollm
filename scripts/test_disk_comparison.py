"""Synthetic CLI and web checks for scoped evidence and bounded disk comparisons."""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evaluate_disk_comparison import QUERY, run_case, table
from emberos.disk_comparison import compare_if_requested
from emberos.tools import ToolRegistry
from web_ember import WebSafeToolRegistry


def main():
    for registry in (ToolRegistry, WebSafeToolRegistry):
        for label, query, old, now, expected_calls, fragment in (
            ("verified", QUERY, table("40G"), table("33G"), 2, "lower"),
            ("Windows drive", QUERY, table("10G", "C:\\"), table("20G", "C:\\"), 2, "higher"),
            ("no previous report", QUERY, None, table("33G"), 1, "previous saved session"),
            ("unusable source", QUERY, "Check a different drive", table("33G"), 1, "previous saved session"),
            ("current check failed", QUERY, table("40G"), None, 2, "couldn't verify"),
            ("different drive", QUERY, table("40G"), table("33G", "C:\\"), 2, "couldn't verify"),
            ("negation", "Don't compare disk space with last time.", table("40G"), table("33G"), 0, "No disk comparison"),
            ("extra action", QUERY.rstrip(".") + " and delete files.", None, None, 0, "compare"),
        ):
            with TemporaryDirectory() as directory:
                outcome = run_case(registry, Path(directory), query, old, now)
            assert outcome["route"] == "disk_comparison", outcome
            assert outcome["calls"] == ["search_conversation_history", "disk_usage"][:expected_calls], outcome
            assert fragment in outcome["answer"] and "97 GiB" not in outcome["answer"], outcome
            assert outcome["success"] == (label in {"verified", "Windows drive"}), outcome
            if outcome["success"]:
                assert [item["role"] for item in outcome["trace"]] == ["retrieval", "disk_observer", "verifier"]
                assert outcome["trace"][0]["source_id"] == 1
            print("PASS", registry.__name__, label)
    with patch("web_ember.WEB_SAFE_TOOLS", {"search_conversation_history"}), TemporaryDirectory() as directory:
        blocked = run_case(WebSafeToolRegistry, Path(directory), old=table("40G"), now=table("33G"))
        assert not blocked["success"] and blocked["trace"][1]["status"] == "unsupported", blocked
    assert compare_if_requested("How much disk space is free?", None) is None
    print("PASS web policy and simple request boundary")


if __name__ == "__main__":
    main()
