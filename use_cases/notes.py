"""Notes manager for EmberOS-Windows."""

import json
import logging
import os
import re
import sqlite3
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

from emberos.config import ROOT_DIR

logger = logging.getLogger("emberos.use_cases.notes")

_DB_PATH = ROOT_DIR / "data" / "ember.db"


class NotesManager:
    """SQLite-backed tagged notes system."""

    def __init__(self, db_path: str = None):
        path = Path(db_path) if db_path else _DB_PATH
        self._notes_dir = path.resolve().parent / "notes"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        self._create_table()

    def _create_table(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                tags TEXT DEFAULT ''
            )
        """)
        self._conn.commit()

    def add(self, title: str, content: str, tags: list[str] = None) -> dict:
        ts = datetime.now(timezone.utc).isoformat()
        tags_str = ",".join(tags) if tags else ""
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO notes (title, content, timestamp, tags) VALUES (?, ?, ?, ?)",
                (title, content, ts, tags_str),
            )
            self._conn.commit()
            return {"id": cur.lastrowid, "title": title, "content": content,
                    "timestamp": ts, "tags": tags or []}

    def search(self, query: str, limit: int = 10) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, title, content, timestamp, tags FROM notes "
                "WHERE content LIKE ? OR title LIKE ? OR tags LIKE ? "
                "ORDER BY id DESC LIMIT ?",
                (f"%{query}%", f"%{query}%", f"%{query}%", limit),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def export_text(self, note: dict) -> Path:
        """Export a saved note without overwriting existing files or edits."""
        title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", note["title"])
        title = title[:80].strip(" .") or "Note"
        # The numeric prefix also makes reserved Windows names (e.g. CON) safe.
        stem = f"{note['id']}-{title}"
        self._notes_dir.mkdir(parents=True, exist_ok=True)
        suffix = 0
        while True:
            path = self._notes_dir / f"{stem}{f'-{suffix}' if suffix else ''}.txt"
            try:
                with path.open("x", encoding="utf-8", newline="\r\n") as stream:
                    text = f"{note['title']}\n\n{note['content']}"
                    stream.write(text.replace("\r\n", "\n").replace("\r", "\n"))
                return path
            except FileExistsError:
                suffix += 1

    def get_by_id(self, note_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, title, content, timestamp, tags FROM notes WHERE id = ?",
                (note_id,),
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def get_recent(self, n: int = 10) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, title, content, timestamp, tags FROM notes ORDER BY id DESC LIMIT ?",
                (n,),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def update_tags(self, note_id: int, tags: list[str]):
        tags_str = ",".join(tags)
        with self._lock:
            self._conn.execute("UPDATE notes SET tags = ? WHERE id = ?", (tags_str, note_id))
            self._conn.commit()

    def delete(self, note_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    @staticmethod
    def _row_to_dict(row) -> dict:
        tags_str = row[4] or ""
        return {
            "id": row[0],
            "title": row[1],
            "content": row[2],
            "timestamp": row[3],
            "tags": [t.strip() for t in tags_str.split(",") if t.strip()],
        }


def open_in_notepad(path: Path) -> None:
    """Open the saved file explicitly in Notepad, regardless of associations."""
    if os.name != "nt":
        raise OSError("Notepad is only available on Windows")
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "notepad.exe"
    subprocess.Popen([str(executable), str(path.resolve())], shell=False)


def time_ago(iso_ts: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        if delta.days > 0:
            return f"{delta.days}d ago"
        hours = delta.seconds // 3600
        if hours > 0:
            return f"{hours}h ago"
        minutes = delta.seconds // 60
        return f"{minutes}m ago" if minutes > 0 else "just now"
    except Exception:
        return iso_ts
