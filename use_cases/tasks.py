"""Task manager for EmberOS-Windows."""

import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from emberos.config import ROOT_DIR

logger = logging.getLogger("emberos.use_cases.tasks")

_DB_PATH = ROOT_DIR / "data" / "ember.db"


class TaskManager:
    """SQLite-backed task list with priorities and due dates."""

    def __init__(self, db_path: str = None):
        path = Path(db_path) if db_path else _DB_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        self._create_table()

    def _create_table(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                due_date TEXT DEFAULT NULL,
                priority TEXT DEFAULT 'normal',
                created_at TEXT NOT NULL,
                completed INTEGER DEFAULT 0,
                completed_at TEXT DEFAULT NULL
            )
        """)
        self._conn.commit()

    def add(self, title: str, due_date: str = None, priority: str = "normal") -> dict:
        created_at = datetime.now(timezone.utc).isoformat()
        priority = priority.lower() if priority else "normal"
        if priority not in ("low", "normal", "high"):
            priority = "normal"
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO tasks (title, due_date, priority, created_at) VALUES (?, ?, ?, ?)",
                (title, due_date, priority, created_at),
            )
            self._conn.commit()
            return {
                "id": cur.lastrowid,
                "title": title,
                "due_date": due_date,
                "priority": priority,
                "created_at": created_at,
                "completed": False,
            }

    def list_pending(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, title, due_date, priority, created_at, completed, completed_at "
                "FROM tasks WHERE completed = 0 "
                "ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, created_at ASC "
                "LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def list_all(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, title, due_date, priority, created_at, completed, completed_at "
                "FROM tasks ORDER BY completed ASC, created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def complete(self, task_id: int) -> dict | None:
        completed_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE tasks SET completed = 1, completed_at = ? WHERE id = ? AND completed = 0",
                (completed_at, task_id),
            )
            self._conn.commit()
            if cur.rowcount == 0:
                return None
            row = self._conn.execute(
                "SELECT id, title, due_date, priority, created_at, completed, completed_at "
                "FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def remove(self, task_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def clear_completed(self) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM tasks WHERE completed = 1")
            self._conn.commit()
            return cur.rowcount

    def clear_all(self) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM tasks")
            self._conn.commit()
            return cur.rowcount

    def count_pending(self) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE completed = 0"
            ).fetchone()[0]

    def search(self, query: str, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, title, due_date, priority, created_at, completed, completed_at "
                "FROM tasks WHERE title LIKE ? ORDER BY completed ASC, created_at DESC LIMIT ?",
                (f"%{query}%", limit),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    @staticmethod
    def _row_to_dict(row) -> dict:
        return {
            "id": row[0],
            "title": row[1],
            "due_date": row[2],
            "priority": row[3],
            "created_at": row[4],
            "completed": bool(row[5]),
            "completed_at": row[6],
        }


def time_until_due(iso_ts: str) -> str:
    """Return a human-readable string for how far until (or past) a due date."""
    try:
        dt = datetime.fromisoformat(iso_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = dt - datetime.now(timezone.utc)
        total_seconds = int(delta.total_seconds())
        if total_seconds < 0:
            # overdue
            abs_s = abs(total_seconds)
            if abs_s >= 86400:
                return f"{abs_s // 86400}d overdue"
            if abs_s >= 3600:
                return f"{abs_s // 3600}h overdue"
            return f"{abs_s // 60}m overdue"
        if total_seconds >= 86400:
            return f"due in {total_seconds // 86400}d"
        if total_seconds >= 3600:
            return f"due in {total_seconds // 3600}h"
        if total_seconds >= 60:
            return f"due in {total_seconds // 60}m"
        return "due very soon"
    except Exception:
        return iso_ts
