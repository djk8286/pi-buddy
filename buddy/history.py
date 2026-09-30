"""Every conversation is logged to SQLite so the buddy can search past chats."""
import sqlite3
import threading
import time
from pathlib import Path

from .config import DB_PATH

TOOL_SPEC = {
    "name": "search_history",
    "description": (
        "Search the log of past spoken conversations with the user (older than the current conversation). "
        "Use when the user refers to something discussed before that isn't in memory, "
        "e.g. 'what did I say about the router last week?'. Returns matching lines with timestamps."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Keywords to search for"},
            "limit": {"type": "integer", "description": "Max results (default 10)"},
        },
        "required": ["query"],
    },
}


class History:
    def __init__(self, path: Path = DB_PATH):
        self.lock = threading.Lock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS messages(
                id INTEGER PRIMARY KEY, ts REAL, session TEXT, role TEXT, text TEXT);
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                text, content='messages', content_rowid='id');
            CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
                INSERT INTO messages_fts(rowid, text) VALUES (new.id, new.text);
            END;
            """
        )

    def add(self, session: str, role: str, text: str):
        if not text.strip():
            return
        with self.lock:
            self.db.execute(
                "INSERT INTO messages(ts, session, role, text) VALUES (?,?,?,?)",
                (time.time(), session, role, text),
            )
            self.db.commit()

    def search(self, query: str, limit: int = 10, exclude_session: str | None = None) -> str:
        # Quote each word so punctuation can't break FTS syntax; OR them for recall.
        words = [w for w in "".join(c if c.isalnum() else " " for c in query).split() if w]
        if not words:
            return "No search terms given."
        fts = " OR ".join(f'"{w}"' for w in words)
        with self.lock:
            rows = self.db.execute(
                """SELECT m.ts, m.role, m.text FROM messages_fts f JOIN messages m ON m.id = f.rowid
                   WHERE messages_fts MATCH ? AND m.session != ? ORDER BY rank LIMIT ?""",
                (fts, exclude_session or "", int(limit or 10)),
            ).fetchall()
        if not rows:
            return "No matching past conversations."
        out = []
        for ts, role, text in rows:
            when = time.strftime("%a %b %d %Y %I:%M %p", time.localtime(ts))
            who = "User" if role == "user" else "You"
            out.append(f"[{when}] {who}: {text[:300]}")
        return "\n".join(out)
