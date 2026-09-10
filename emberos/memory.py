"""Bounded local conversation history. Retrieval never executes saved actions."""

from contextlib import contextmanager
from datetime import datetime, timezone
from itertools import islice
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from emberos.outcomes import ToolOutput


MAX_TURNS = 1000
MAX_DATABASE_BYTES = 16 * 1024 * 1024
MAX_RECALL_BYTES = 6000
HISTORY_SCOPES = {"all": "all saved sessions", "current_session": "the current session",
                  "previous_session": "the previous saved session"}
_ASCII_WORD_SEPARATORS = str.maketrans({chr(code): " " for code in range(128)
                                      if not chr(code).isalnum()})


def _words(text):
    """Whole-word lookup tokens only; this never classifies user intent."""
    text = text.casefold()
    if text.isascii():
        return set(text.translate(_ASCII_WORD_SEPARATORS).split())
    return set("".join(char if char.isalnum() else " " for char in text).split())


def _clip(value, limit):
    """Bound UTF-8 storage, marking previews without splitting code points."""
    text = value if isinstance(value, str) else str(value)
    encoded = text[:limit].encode("utf-8", errors="replace")
    shortened = len(text) > limit or len(encoded) > limit
    if shortened:
        return encoded[:limit - 14].decode("utf-8", errors="ignore") + " [truncated]", True
    return encoded.decode("utf-8"), False


def _argument_json(arguments):
    # Tool arguments can contain an entire document. Bound traversal as well
    # as serialization rather than copying arbitrary nested content to SQLite.
    remaining = [64]
    shortened = [False]

    def bounded(value, depth=0):
        remaining[0] -= 1
        if remaining[0] < 0 or depth > 4:
            shortened[0] = True
            return "[truncated]"
        if isinstance(value, str):
            text, clipped = _clip(value, 1024)
            shortened[0] |= clipped
            return text
        if isinstance(value, dict):
            shortened[0] |= len(value) > 16
            result = {}
            for key, item in islice(value.items(), 16):
                name, clipped = _clip(key, 128)
                shortened[0] |= clipped
                result[name] = bounded(item, depth + 1)
            return result
        if isinstance(value, (tuple, list)):
            shortened[0] |= len(value) > 16
            return [bounded(item, depth + 1) for item in islice(value, 16)]
        if value is None or isinstance(value, (bool, int, float)):
            return value
        shortened[0] = True
        return "[unsupported value]"

    serialized = json.dumps(bounded(arguments or {}), ensure_ascii=False)
    if len(serialized.encode("utf-8")) > 2048:
        # Keep JSON valid even when only a preview fits the storage budget.
        serialized = json.dumps({"_preview": _clip(serialized, 256)[0], "_truncated": True})
        shortened[0] = True
    return serialized, shortened[0]


class ConversationMemory:
    """At most 1,000 turns, small SQLite cache, and five recall results."""

    def __init__(self, path, *, max_turns=MAX_TURNS):
        if not 1 <= max_turns <= MAX_TURNS:
            raise ValueError(f"max_turns must be between 1 and {MAX_TURNS}")
        self.path = Path(path)
        self.max_turns = max_turns
        self.session_id = uuid4().hex
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError(f"Unsupported conversation database version: {version}")
            db.execute("""CREATE TABLE IF NOT EXISTS turns (
                id INTEGER PRIMARY KEY, session_id TEXT NOT NULL,
                created_at TEXT NOT NULL, request TEXT NOT NULL,
                response TEXT NOT NULL, route TEXT NOT NULL,
                tool TEXT NOT NULL, arguments_json TEXT NOT NULL,
                status TEXT NOT NULL, success INTEGER, confidence REAL,
                truncated INTEGER NOT NULL, is_recall INTEGER NOT NULL
            )""")
            db.execute("PRAGMA user_version = 1")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=0.25)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA cache_size = -256")
            db.execute("PRAGMA mmap_size = 0")
            db.execute("PRAGMA temp_store = FILE")
            page_size = db.execute("PRAGMA page_size").fetchone()[0]
            db.execute(f"PRAGMA max_page_count = {MAX_DATABASE_BYTES // page_size}")
            with db:
                yield db
        finally:
            db.close()

    def record(self, query, response, result):
        request, query_clipped = _clip(query, 2048)
        answer, response_clipped = _clip(response, 4096)
        arguments, args_clipped = _argument_json(result.get("arguments"))
        route = _clip(result.get("route", "unknown"), 64)[0]
        tool = _clip(result.get("tool", ""), 64)[0]
        status = _clip(result.get("status") or result.get("reason") or route, 64)[0]
        confidence = result.get("confidence", result.get("needle_confidence"))
        with self._connect() as db:
            # Prune before inserting to reuse pages; transaction rolls back
            # pruning too if the new record cannot be committed.
            db.execute("DELETE FROM turns WHERE id NOT IN "
                       "(SELECT id FROM turns ORDER BY id DESC LIMIT ?)",
                       (self.max_turns - 1,))
            db.execute("""INSERT INTO turns (
                session_id, created_at, request, response, route, tool,
                arguments_json, status, success, confidence, truncated, is_recall
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                self.session_id, datetime.now(timezone.utc).isoformat(timespec="seconds"),
                request, answer, route, tool, arguments, status,
                result.get("success"), confidence,
                query_clipped or response_clipped or args_clipped or bool(result.get("truncated")),
                tool == "search_conversation_history" or route == "memory_view",
            ))

    def search(self, query="", limit=5, *, scope="all"):
        if not isinstance(query, str):
            raise ValueError("query must be text")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5:
            raise ValueError("limit must be between 1 and 5")
        if scope not in HISTORY_SCOPES:
            raise ValueError("Unknown conversation history scope")
        if len(query) > 512:
            raise ValueError("Search topics must be at most 512 characters")
        terms = _words(query)
        if query.strip() and not terms:
            return []
        fields = "id, session_id, created_at, request, response, route, tool, arguments_json, status, success, truncated"
        best = []
        with self._connect() as db:
            where, parameters = "is_recall = 0", []
            if scope != "all":
                session = self.session_id
                if scope == "previous_session":
                    previous = db.execute("SELECT session_id FROM turns WHERE session_id != ? "
                                          "AND is_recall = 0 ORDER BY id DESC LIMIT 1",
                                          (self.session_id,)).fetchone()
                    if previous is None:
                        return []
                    session = previous[0]
                where += " AND session_id = ?"
                parameters.append(session)
            # Stream the bounded store; retain only five candidates, rather
            # than loading the database or adding an embedding model/index.
            for row in db.execute(f"SELECT {fields} FROM turns WHERE {where} ORDER BY id DESC", parameters):
                score = (True, 0, 0, row["id"])
                if terms:
                    request = _words(row["request"])
                    # Failure output can contain traceback paths and boilerplate
                    # unrelated to the subject. Retrieve failures by their inputs.
                    failed = row["success"] == 0 or row["status"] in {"error", "unsupported"}
                    answer = set() if failed else _words(row["response"])
                    arguments = _words(row["arguments_json"])
                    if not terms <= request | answer | arguments:
                        continue
                    # Prefer reported results over matching failed attempts,
                    # preserving failure labels in displayed excerpts.
                    score = (not failed, len(terms & request), len(terms & arguments), row["id"])
                identity = (row["request"], row["response"], row["status"])
                if any(candidate[1] == identity for candidate in best):
                    continue
                best.append((score, identity, dict(row)))
                best.sort(key=lambda candidate: candidate[0], reverse=True)
                del best[limit:]
                if not terms and len(best) == limit:
                    break
        return [candidate[2] for candidate in best]

    def recall(self, query="", limit=5, *, scope="all"):
        rows = self.search(query, limit, scope=scope)
        metadata = {"source": "conversation_history", "scope": scope, "query": query,
                    "retrieval": "matches" if rows else "no_matches", "turn_ids": []}
        if not rows:
            return ToolOutput(f"No matching conversation history was found in {HISTORY_SCOPES[scope]}.",
                              status="partial", data=metadata)
        blocks = [f"Saved conversation excerpts from {HISTORY_SCOPES[scope]} "
                  "(historical records; not current system state):"]
        remaining = MAX_RECALL_BYTES - len(blocks[0].encode("utf-8"))
        for row in rows:
            if remaining < 256:
                break
            request = _clip(row["request"], 400)[0]
            answer = _clip(row["response"], 650)[0]
            label = " | saved preview" if row["truncated"] else ""
            block = (f"\n\n#{row['id']} | {row['created_at']} | {row['status']}{label}\n"
                     f"You: {request}\nEmber: {answer}")
            block = _clip(block, remaining)[0]
            remaining -= len(block.encode("utf-8"))
            blocks.append(block)
            metadata["turn_ids"].append(row["id"])
        return ToolOutput("".join(blocks), data=metadata)

    def clear(self):
        with self._connect() as db:
            db.execute("PRAGMA secure_delete = ON")
            return db.execute("DELETE FROM turns").rowcount


def memory_command(query, memory):
    """Explicit CLI commands, like exit; not natural-language intent rules."""
    command, _, argument = query.partition(" ")
    if command != "/memory":
        return None
    action, _, search_text = argument.strip().partition(" ")
    if action not in ("", "search", "clear"):
        return "Usage: /memory | /memory search <topic> | /memory clear"
    if memory is None:
        return "Conversation memory is disabled or unavailable."
    if action == "clear":
        if search_text:
            return "Usage: /memory clear"
        count = memory.clear()
        return f"Cleared {count} saved conversation turns."
    if action == "search" and not search_text.strip():
        return "Usage: /memory search <topic>"
    return str(memory.recall(search_text if action else ""))
