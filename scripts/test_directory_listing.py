#!/usr/bin/env python3
"""Check natural home/Downloads folder requests use read-only listing."""

from pathlib import PurePosixPath
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import needle_router
from emberos.tools import ToolRegistry, ToolResult


def main() -> None:
    home = "/home/emberos-smoke"
    registry = ToolRegistry()
    with (
        patch("needle_router.Path.home", return_value=PurePosixPath(home)),
        patch.object(
            needle_router, "_user_folder_path",
            side_effect=lambda folder: f"{home}/{folder}",
        ),
        patch.object(
            registry, "execute_tool",
            return_value=ToolResult(True, result=["fixture.txt"], status="success"),
        ) as execute,
        patch.object(needle_router.needle_agent, "complete") as needle_complete,
        patch("emberos.history_routing.select_history") as history_select,
    ):
        cases = (
            ("List the files and folders in my home directory.", home),
            ("What's in my Downloads folder?", f"{home}/Downloads"),
        )
        for query, expected_path in cases:
            result = needle_router.route_and_execute(query, registry)
            assert result["route"] == "tool_call", result
            assert result["tool"] == "list_dir", result
            assert result["arguments"] == {"path": expected_path}, result

        assert execute.call_count == len(cases), execute.call_args_list
        needle_complete.assert_not_called()
        history_select.assert_not_called()

    for query in (
        "Don't list the files and folders in my home directory.",
        "How do I list the files and folders in my home directory?",
        "List the files in Downloads and then read the report.",
    ):
        assert needle_router._known_directory_listing_call(query) is None, query

    print("PASS home and Downloads prompts route directly to read-only list_dir")


if __name__ == "__main__":
    main()
