"""
Chat History - SQLite-backed persistent session storage.
Saves chat sessions and messages to ~/.study-tui/history.db.
Sensitive titles, message bodies, and metadata are encrypted at rest on Windows.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from src.secure_storage import decrypt_text, encrypt_text


DB_PATH = Path.home() / ".study-tui" / "history.db"


class ChatHistoryManager:
    """Manages persistent chat sessions in SQLite."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db_path = Path(db_path) if db_path else DB_PATH
        self._conn = self._init_connection(self._db_path)

        self._session_id: int | None = None
        self._title: str = "Untitled"

    def _init_connection(self, db_path: Path) -> sqlite3.Connection:
        conn = self._connect(db_path)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._conn = conn
            self._init_schema()
            return conn
        except sqlite3.Error:
            try:
                conn.close()
            except Exception:
                pass
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            self._conn = conn
            self._init_schema()
            return conn

    @staticmethod
    def _connect(db_path: Path) -> sqlite3.Connection:
        try:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            return sqlite3.connect(str(db_path))
        except sqlite3.Error:
            return sqlite3.connect(":memory:")

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT    NOT NULL DEFAULT 'Untitled',
                created_at  REAL    NOT NULL,
                updated_at  REAL    NOT NULL,
                metadata    TEXT    DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                role        TEXT    NOT NULL,
                content     TEXT    NOT NULL,
                timestamp   REAL    NOT NULL,
                metadata    TEXT    DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_messages_session
                ON messages(session_id);
        """)

    @staticmethod
    def _encrypt_json(payload: dict | None) -> str:
        return encrypt_text(json.dumps(payload or {}))

    @staticmethod
    def _decrypt_json(payload: str) -> dict:
        raw = decrypt_text(payload)
        try:
            return json.loads(raw or "{}")
        except Exception:
            return {}

    @staticmethod
    def _preview_title(content: str, limit: int = 60) -> str:
        content = (content or "").strip()
        if not content:
            return "Untitled"
        return content[:limit]

    def _save_session_title(self, title: str, now: float) -> None:
        if not self._session_id:
            return
        self._conn.execute(
            "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
            (encrypt_text(title), now, self._session_id),
        )

    def _load_session_metadata(self, session_id: int) -> dict:
        row = self._conn.execute(
            "SELECT metadata FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not row:
            return {}
        return self._decrypt_json(row["metadata"] or "{}")

    def _save_session_metadata(self, session_id: int, metadata: dict, now: float | None = None) -> None:
        timestamp = now if now is not None else time.time()
        self._conn.execute(
            "UPDATE sessions SET metadata = ?, updated_at = ? WHERE id = ?",
            (self._encrypt_json(metadata), timestamp, session_id),
        )

    # Session lifecycle

    def new_session(self, title: str = "Untitled") -> int:
        """Create a new session. Returns session ID."""
        now = time.time()
        cur = self._conn.execute(
            "INSERT INTO sessions (title, created_at, updated_at, metadata) VALUES (?, ?, ?, ?)",
            (encrypt_text(title), now, now, self._encrypt_json({})),
        )
        self._conn.commit()
        self._session_id = cur.lastrowid
        self._title = title
        return self._session_id

    def save_message(self, role: str, content: str, metadata: dict | None = None) -> None:
        """Append a single message to the current session."""
        if not self._session_id:
            self.new_session()

        now = time.time()
        self._conn.execute(
            "INSERT INTO messages (session_id, role, content, timestamp, metadata) VALUES (?, ?, ?, ?, ?)",
            (self._session_id, role, encrypt_text(content), now, self._encrypt_json(metadata)),
        )

        if self._title == "Untitled" and role == "user" and content.strip():
            self._title = self._preview_title(content)
            self._save_session_title(self._title, now)
        else:
            self._conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, self._session_id),
            )

        self._conn.commit()

    def save(self, messages: list[dict], title: str | None = None) -> None:
        """Bulk-save messages (replaces all messages in current session)."""
        if not self._session_id:
            self.new_session(title or "Untitled")

        now = time.time()

        if (self._title == "Untitled" or title) and messages:
            if title:
                self._title = title
            else:
                for msg in messages:
                    if msg.get("role") == "user" and msg.get("content", "").strip():
                        self._title = self._preview_title(msg["content"])
                        break

        self._conn.execute("DELETE FROM messages WHERE session_id = ?", (self._session_id,))
        for msg in messages:
            self._conn.execute(
                "INSERT INTO messages (session_id, role, content, timestamp, metadata) VALUES (?, ?, ?, ?, ?)",
                (
                    self._session_id,
                    msg.get("role", "user"),
                    encrypt_text(msg.get("content", "")),
                    msg.get("timestamp", now),
                    self._encrypt_json(msg.get("metadata", {})),
                ),
            )

        self._save_session_title(self._title, now)
        self._conn.commit()

    def load_session(self, session_id: int) -> list[dict]:
        """Load all messages from a session."""
        rows = self._conn.execute(
            "SELECT role, content, timestamp, metadata FROM messages WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()

        self._session_id = session_id
        row = self._conn.execute(
            "SELECT title FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        self._title = decrypt_text(row["title"]) if row else "Untitled"

        return [
            {
                "role": r["role"],
                "content": decrypt_text(r["content"]),
            }
            for r in rows
        ]

    def load_latest(self) -> tuple[list[dict], int] | None:
        """Load the most recent session. Returns (messages, session_id) or None."""
        row = self._conn.execute(
            "SELECT id FROM sessions ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        messages = self.load_session(row["id"])
        return messages, row["id"]

    def load_session_state(self, session_id: int) -> dict:
        """Load session-level context metadata."""
        metadata = self._load_session_metadata(session_id)
        context_state = metadata.get("context_state")
        return context_state if isinstance(context_state, dict) else {}

    def save_session_state(self, state: dict) -> None:
        """Persist session-level context metadata for the current session."""
        if not self._session_id:
            self.new_session()
        metadata = self._load_session_metadata(self._session_id)
        metadata["context_state"] = state
        self._save_session_metadata(self._session_id, metadata)
        self._conn.commit()

    # Listing

    def list_sessions(self, limit: int = 20) -> list[dict]:
        """List saved sessions, newest first."""
        rows = self._conn.execute(
            """SELECT s.id, s.title, s.updated_at, s.created_at,
                      COUNT(m.id) as msg_count
               FROM sessions s
               LEFT JOIN messages m ON m.session_id = s.id
               GROUP BY s.id
               ORDER BY s.updated_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()

        return [
            {
                "id": r["id"],
                "title": decrypt_text(r["title"]),
                "messages": r["msg_count"],
                "updated": r["updated_at"],
                "created": r["created_at"],
            }
            for r in rows
        ]

    def delete_session(self, session_id: int) -> bool:
        """Delete a session and its messages."""
        cur = self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def search_messages(self, query: str, limit: int = 20) -> list[dict]:
        """Search decrypted messages in Python to avoid storing plaintext."""
        rows = self._conn.execute(
            """SELECT m.role, m.content, m.timestamp, m.metadata, s.id as session_id, s.title
               FROM messages m
               JOIN sessions s ON s.id = m.session_id
               ORDER BY m.timestamp DESC"""
        ).fetchall()

        query_lower = query.lower()
        matches: list[dict] = []
        for row in rows:
            content = decrypt_text(row["content"])
            if query_lower not in content.lower():
                continue
            matches.append(
                {
                    "session_id": row["session_id"],
                    "session_title": decrypt_text(row["title"]),
                    "role": row["role"],
                    "content": content[:200],
                    "timestamp": row["timestamp"],
                }
            )
            if len(matches) >= limit:
                break
        return matches

    # Properties

    def get_session_title(self, session_id: int) -> str | None:
        """Get a session's title by ID."""
        row = self._conn.execute(
            "SELECT title FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return decrypt_text(row["title"]) if row else None

    @property
    def session_id(self) -> int | None:
        return self._session_id

    @property
    def session_title(self) -> str:
        return self._title

    def close(self) -> None:
        self._conn.close()
