"""Check ranking on additional topics, legacy note migration and index maintenance."""

from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from emberos.memory import ConversationMemory


def ids(notes, topic):
    return [row["id"] for row in notes.search(topic)]


def main():
    with tempfile.TemporaryDirectory(prefix="ember-rank-test-") as tmp:
        root = Path(tmp)
        os.environ["EMBER_DATA_DIR"] = tmp
        from use_cases.notes import NotesManager
        path = root / "legacy.db"
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("CREATE TABLE notes(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL,"
                       "content TEXT NOT NULL,timestamp TEXT NOT NULL,tags TEXT DEFAULT '')")
            db.execute("INSERT INTO notes VALUES(7,'Battery voltage','A measurement','original','electronics')")
        with closing(NotesManager(path)) as notes:
            original = notes.get_by_id(7)
            notes.export_text(original)
            aside = notes.add("Lecture layout", "Use battery voltage as the example.", ["slides"])
            notes.export_text(aside)

            assert ids(notes, "voltage battery") == [7, aside["id"]]
            assert ids(notes, "electronics") == [7]
            assert notes.get_by_id(7) == original
            if notes._indexed:
                # Simulate a build without FTS5 by hiding its module listing.
                notes._conn.set_authorizer(lambda action, name, *_: sqlite3.SQLITE_IGNORE
                                           if action == sqlite3.SQLITE_PRAGMA and name == "module_list"
                                           else sqlite3.SQLITE_OK)
                notes._indexed = notes._prepare_search_index()
                notes._conn.set_authorizer(None)
                assert not notes._indexed
                assert ids(notes, "voltage battery") == [7, aside["id"]]
            with notes._conn:
                notes._conn.execute("UPDATE notes SET content = 'calibration' WHERE id = 7")
        with closing(NotesManager(path)) as notes:
            assert ids(notes, "calibration") == [7]
            with notes._conn:
                notes._conn.execute("UPDATE notes SET content = 'ammeter' WHERE id = 7")
            assert ids(notes, "calibration") == [] and ids(notes, "ammeter") == [7]
            assert notes.delete(7) and ids(notes, "ammeter") == []
            notes.restore_snapshot(original)
            assert notes.get_by_id(7) == original and ids(notes, "voltage battery")[0] == 7
            if notes._indexed:
                notes._conn.execute("INSERT INTO notes_fts(notes_fts,rank) VALUES('integrity-check',1)")

        previous = ConversationMemory(root / "turns.db")
        previous.record("Inspect battery voltage", "12 V", {"status": "success"})
        current = ConversationMemory(previous.path)
        current.record("Inspect battery voltage", "11 V", {"status": "success"})
        current.record("Choose a battery voltage illustration for slides", "Blue", {"status": "success"})
        assert current.search("voltage battery", limit=1, scope="current_session")[0]["response"] == "11 V"
        assert [r["response"] for r in current.search("battery", scope="previous_session")] == ["12 V"]
        assert current.search("wind turbine") == []

        art = ConversationMemory(root / "art.db")
        art.record("Review the fire drill poster", "Increase the font size.", {"status": "success"})
        assert art.recall("fire drill").data["retrieval"] == "no_matches"
        for topic in ("poster", "fire drill poster", "font size"):
            assert art.search(topic)[0]["response"] == "Increase the font size.", topic
        art.record("Review the fire drill diagram", "The fire drill starts at noon.", {"status": "success"})
        assert [r["response"] for r in art.search("fire drill")] == ["The fire drill starts at noon."]
        art.record("Read the ferry timetable", "The first sailing is at 07:20.", {"status": "success"})
        assert art.search("ferry timetable")[0]["response"] == "The first sailing is at 07:20."
        art.record("Read the ferry timetable poster", "First sailing at 07:20. Use blue ink.", {"status": "success"})
        assert len(art.search("ferry timetable")) == 2
    print("PASS new topics, session isolation, legacy rows, updates, delete and restore")


if __name__ == "__main__":
    main()
