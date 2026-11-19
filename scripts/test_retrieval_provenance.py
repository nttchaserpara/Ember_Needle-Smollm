"""Check structured source, ID, score, rank and scope in retrieval results."""

import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    with TemporaryDirectory(prefix="ember-retrieval-provenance-") as temporary:
        os.environ["EMBER_DATA_DIR"] = temporary
        from emberos.memory import ConversationMemory
        from emberos.tools import ToolRegistry
        from use_cases.notes import NotesManager

        root = Path(temporary)
        notes = NotesManager(root / "notes.sqlite3")
        content = ("General maintenance background. " * 20
                   + "Replace the bicycle battery before winter. "
                   + "Additional storage instructions. " * 20)
        note = notes.add("Battery replacement", content, ["transport"])
        notes.export_text(note)

        memory_path = root / "conversation.sqlite3"
        previous = ConversationMemory(memory_path)
        previous.record(
            "When should I replace the battery?",
            "The bicycle battery replacement is planned before winter.",
            {"route": "tool_call", "tool": "search_notes", "arguments": {},
             "success": True, "status": "success"},
        )
        previous.record(
            "How much free disk space do I have?",
            "Filesystem Size Used Avail Use% Mounted on\n/dev/root 28G 11G 16G 42% /",
            {"route": "tool_call", "tool": "disk_usage", "arguments": {"path": "/"},
             "success": True, "status": "success"},
        )
        current = ConversationMemory(memory_path)
        registry = ToolRegistry(memory=current)

        with patch("emberos.tools._get_notes_manager", return_value=notes):
            note_result = registry.execute_tool("search_notes", {"query": "battery"})
        assert note_result.success, note_result
        assert note_result.result.startswith("Found 1 note(s):"), note_result.result
        note_data = note_result.data
        assert note_data["source"] == "notes" and note_data["status"] == "matches", note_data
        assert note_data["scope"] == "all" and note_data["results"], note_data
        note_hit = note_data["results"][0]
        assert {"record_id", "title", "timestamp", "snippet", "score", "rank", "status"} <= note_hit.keys()
        assert note_hit["record_id"] == note["id"] and note_hit["rank"] == 1, note_hit
        assert "battery" in note_hit["snippet"].casefold() and note_hit["score"] > 0, note_hit
        assert len(note_hit["snippet"]) <= 240, note_hit

        history_result = registry.execute_tool("search_conversation_history", {
            "query": "battery", "scope": "previous_session", "source_tool": ""
        })
        assert history_result.success, history_result
        assert "Saved conversation excerpts" in history_result.result, history_result.result
        history_data = history_result.data
        assert history_data["source"] == "conversation_history", history_data
        assert history_data["scope"] == "previous_session", history_data
        assert history_data["status"] == history_data["retrieval"] == "matches", history_data
        history_hit = history_data["results"][0]
        assert history_hit["record_id"] == history_data["turn_ids"][0], history_data
        assert {"title", "timestamp", "snippet", "score", "rank", "status", "record_status"} <= history_hit.keys()
        assert history_hit["rank"] == 1 and history_hit["score"] > 0, history_hit
        assert "battery" in history_hit["snippet"].casefold(), history_hit

        disk_result = registry.execute_tool("search_conversation_history", {
            "query": "", "scope": "previous_session", "source_tool": "disk_usage"
        })
        assert disk_result.data["turn_id"] == disk_result.data["results"][0]["record_id"], disk_result.data
        assert disk_result.data["tool"] == "disk_usage", disk_result.data
        assert disk_result.data["results"][0]["rank"] == 1, disk_result.data

        empty = registry.execute_tool("search_conversation_history", {
            "query": "nonexistent topic", "scope": "previous_session", "source_tool": ""
        })
        assert empty.data["status"] == empty.data["retrieval"] == "no_matches", empty.data
        assert empty.data["results"] == [], empty.data
        assert current.search("battery", scope="current_session") == []
        notes.close()

    print("PASS note and history provenance, scores, ranks, no-match status and session scope")


if __name__ == "__main__":
    main()
