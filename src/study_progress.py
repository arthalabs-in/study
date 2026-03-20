"""
Persistent study progress linked to stable document file hashes.
Stores decks, quiz outcomes, linked notes, and agent-authored progress notes.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from src.secure_storage import decrypt_text, encrypt_text


DB_PATH = Path.home() / ".study-tui" / "progress.db"


def compute_file_hash(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).expanduser().open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class StudyProgressManager:
    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db_path = Path(db_path) if db_path else DB_PATH
        self._conn = self._init_connection(self._db_path)

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
            CREATE TABLE IF NOT EXISTS sources (
                source_hash TEXT PRIMARY KEY,
                doc_id      TEXT,
                title       TEXT NOT NULL,
                path        TEXT NOT NULL,
                created_at  REAL NOT NULL,
                updated_at  REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS progress_profiles (
                source_hash     TEXT PRIMARY KEY REFERENCES sources(source_hash) ON DELETE CASCADE,
                doc_id          TEXT,
                title           TEXT NOT NULL,
                grasp_level     REAL DEFAULT 0,
                review_count    INTEGER DEFAULT 0,
                last_quiz_score REAL DEFAULT NULL,
                weak_topics     TEXT DEFAULT '[]',
                strong_topics   TEXT DEFAULT '[]',
                updated_at      REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS flashcard_decks (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                source_hash     TEXT NOT NULL REFERENCES sources(source_hash) ON DELETE CASCADE,
                doc_id          TEXT,
                title           TEXT NOT NULL,
                card_count      INTEGER NOT NULL,
                sample_questions TEXT DEFAULT '[]',
                payload         TEXT NOT NULL,
                created_at      REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS quiz_attempts (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                source_hash     TEXT NOT NULL REFERENCES sources(source_hash) ON DELETE CASCADE,
                doc_id          TEXT,
                title           TEXT NOT NULL,
                score           INTEGER NOT NULL,
                total           INTEGER NOT NULL,
                weak_topics     TEXT DEFAULT '[]',
                strong_topics   TEXT DEFAULT '[]',
                payload         TEXT NOT NULL,
                created_at      REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS progress_notes (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                source_hash     TEXT NOT NULL REFERENCES sources(source_hash) ON DELETE CASCADE,
                doc_id          TEXT,
                title           TEXT NOT NULL,
                note            TEXT NOT NULL,
                author          TEXT NOT NULL DEFAULT 'agent',
                grasp_level     REAL DEFAULT NULL,
                weak_topics     TEXT DEFAULT '[]',
                strong_topics   TEXT DEFAULT '[]',
                metadata        TEXT DEFAULT '{}',
                created_at      REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS linked_notes (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                source_hash     TEXT NOT NULL REFERENCES sources(source_hash) ON DELETE CASCADE,
                note_id         INTEGER NOT NULL,
                doc_id          TEXT,
                title           TEXT NOT NULL,
                page            INTEGER,
                tags            TEXT DEFAULT '[]',
                created_at      REAL NOT NULL,
                UNIQUE(source_hash, note_id)
            );

            CREATE TABLE IF NOT EXISTS card_states (
                source_hash      TEXT NOT NULL REFERENCES sources(source_hash) ON DELETE CASCADE,
                card_key         TEXT NOT NULL,
                doc_id           TEXT,
                title            TEXT NOT NULL,
                question         TEXT NOT NULL,
                answer           TEXT NOT NULL,
                ease             REAL NOT NULL DEFAULT 2.3,
                interval_days    REAL NOT NULL DEFAULT 0,
                review_count     INTEGER NOT NULL DEFAULT 0,
                lapse_count      INTEGER NOT NULL DEFAULT 0,
                last_grade       TEXT,
                last_reviewed_at REAL,
                due_at           REAL NOT NULL,
                created_at       REAL NOT NULL,
                updated_at       REAL NOT NULL,
                PRIMARY KEY (source_hash, card_key)
            );

            CREATE TABLE IF NOT EXISTS flashcard_reviews (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                source_hash      TEXT NOT NULL REFERENCES sources(source_hash) ON DELETE CASCADE,
                card_key         TEXT NOT NULL,
                doc_id           TEXT,
                title            TEXT NOT NULL,
                grade            TEXT NOT NULL,
                interval_days    REAL NOT NULL,
                due_at           REAL NOT NULL,
                created_at       REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_sources_doc_id ON sources(doc_id);
            CREATE INDEX IF NOT EXISTS idx_flashcard_source ON flashcard_decks(source_hash, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_quiz_source ON quiz_attempts(source_hash, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_progress_notes_source ON progress_notes(source_hash, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_linked_notes_source ON linked_notes(source_hash, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_card_states_source_due ON card_states(source_hash, due_at ASC);
            CREATE INDEX IF NOT EXISTS idx_flashcard_reviews_source ON flashcard_reviews(source_hash, created_at DESC);
        """)

    @staticmethod
    def _encode_list(values: list[str] | None) -> str:
        return json.dumps([str(value).strip() for value in (values or []) if str(value).strip()])

    @staticmethod
    def _decode_list(payload: str) -> list[str]:
        try:
            data = json.loads(payload or "[]")
        except Exception:
            return []
        return [str(value) for value in data if str(value).strip()]

    @staticmethod
    def _encrypt_json(payload: Any) -> str:
        return encrypt_text(json.dumps(payload, ensure_ascii=False))

    @staticmethod
    def _decrypt_json(payload: str, default: Any) -> Any:
        raw = decrypt_text(payload)
        try:
            return json.loads(raw or "null")
        except Exception:
            return default

    @staticmethod
    def _normalize_topic_lines(values: list[str] | None, limit: int = 6) -> list[str]:
        cleaned: list[str] = []
        for value in values or []:
            text = " ".join(str(value or "").split()).strip()
            if text and text not in cleaned:
                cleaned.append(text[:180])
            if len(cleaned) >= limit:
                break
        return cleaned

    @staticmethod
    def _card_key(question: str, answer: str) -> str:
        payload = f"{question.strip()}\n{answer.strip()}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:24]

    @staticmethod
    def _merge_topics(existing_payload: str, new_topics: list[str], limit: int = 8) -> str:
        merged: list[str] = []
        for topic in StudyProgressManager._decode_list(existing_payload):
            if topic not in merged:
                merged.append(topic)
        for topic in StudyProgressManager._normalize_topic_lines(new_topics, limit=limit):
            if topic not in merged:
                merged.append(topic)
        return StudyProgressManager._encode_list(merged[:limit])

    def upsert_source(self, *, source_hash: str, doc_id: str | None, title: str, path: str) -> None:
        now = time.time()
        self._conn.execute(
            """
            INSERT INTO sources (source_hash, doc_id, title, path, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_hash) DO UPDATE SET
                doc_id=excluded.doc_id,
                title=excluded.title,
                path=excluded.path,
                updated_at=excluded.updated_at
            """,
            (source_hash, doc_id, title, path, now, now),
        )
        self._conn.execute(
            """
            INSERT INTO progress_profiles (source_hash, doc_id, title, grasp_level, review_count, updated_at)
            VALUES (?, ?, ?, 0, 0, ?)
            ON CONFLICT(source_hash) DO UPDATE SET
                doc_id=excluded.doc_id,
                title=excluded.title,
                updated_at=excluded.updated_at
            """,
            (source_hash, doc_id, title, now),
        )
        self._conn.commit()

    def source_hash_for_doc(self, doc_id: str | None) -> str | None:
        if not doc_id:
            return None
        row = self._conn.execute(
            "SELECT source_hash FROM sources WHERE doc_id = ? ORDER BY updated_at DESC LIMIT 1",
            (doc_id,),
        ).fetchone()
        return str(row["source_hash"]) if row else None

    def record_flashcards(
        self,
        *,
        source_hash: str,
        doc_id: str | None,
        title: str,
        cards: list[dict[str, str]],
    ) -> dict:
        now = time.time()
        sample_questions = self._normalize_topic_lines([card.get("question", "") for card in cards], limit=5)
        self._conn.execute(
            """
            INSERT INTO flashcard_decks (source_hash, doc_id, title, card_count, sample_questions, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_hash,
                doc_id,
                title,
                len(cards),
                self._encode_list(sample_questions),
                self._encrypt_json(cards),
                now,
            ),
        )
        for card in cards:
            question = str(card.get("question", "")).strip()
            answer = str(card.get("answer", "")).strip()
            if not question or not answer:
                continue
            card_key = self._card_key(question, answer)
            self._conn.execute(
                """
                INSERT INTO card_states (
                    source_hash, card_key, doc_id, title, question, answer, due_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_hash, card_key) DO UPDATE SET
                    doc_id=excluded.doc_id,
                    title=excluded.title,
                    question=excluded.question,
                    answer=excluded.answer,
                    updated_at=excluded.updated_at
                """,
                (
                    source_hash,
                    card_key,
                    doc_id,
                    title,
                    question,
                    answer,
                    now,
                    now,
                    now,
                ),
            )
        self._conn.commit()
        return {"status": "saved", "type": "flashcards", "count": len(cards)}

    def record_quiz_attempt(
        self,
        *,
        source_hash: str,
        doc_id: str | None,
        title: str,
        score: int,
        total: int,
        results: list[dict[str, Any]],
    ) -> dict:
        now = time.time()
        weak_topics = self._normalize_topic_lines([item.get("question", "") for item in results if not item.get("correct")])
        strong_topics = self._normalize_topic_lines([item.get("question", "") for item in results if item.get("correct")])
        self._conn.execute(
            """
            INSERT INTO quiz_attempts (source_hash, doc_id, title, score, total, weak_topics, strong_topics, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_hash,
                doc_id,
                title,
                int(score),
                int(total),
                self._encode_list(weak_topics),
                self._encode_list(strong_topics),
                self._encrypt_json(results),
                now,
            ),
        )
        row = self._conn.execute(
            "SELECT grasp_level, review_count FROM progress_profiles WHERE source_hash = ?",
            (source_hash,),
        ).fetchone()
        previous_grasp = float(row["grasp_level"]) if row else 0.0
        previous_reviews = int(row["review_count"]) if row else 0
        ratio = (float(score) / float(total)) if total else 0.0
        new_grasp = ((previous_grasp * previous_reviews) + ratio) / max(previous_reviews + 1, 1)
        self._conn.execute(
            """
            UPDATE progress_profiles
            SET doc_id = ?, title = ?, grasp_level = ?, review_count = ?, last_quiz_score = ?,
                weak_topics = ?, strong_topics = ?, updated_at = ?
            WHERE source_hash = ?
            """,
            (
                doc_id,
                title,
                round(new_grasp, 4),
                previous_reviews + 1,
                ratio,
                self._encode_list(weak_topics),
                self._encode_list(strong_topics),
                now,
                source_hash,
            ),
        )
        self._conn.commit()
        return {
            "status": "saved",
            "type": "quiz_attempt",
            "score": score,
            "total": total,
            "weak_topics": weak_topics,
            "strong_topics": strong_topics,
            "grasp_level": round(new_grasp, 4),
        }

    def record_flashcard_review(
        self,
        *,
        source_hash: str,
        card_key: str,
        grade: str,
        doc_id: str | None = None,
        title: str | None = None,
    ) -> dict:
        grade_key = str(grade or "").strip().lower()
        if grade_key not in {"again", "hard", "good", "easy"}:
            return {"error": f"Unknown review grade: {grade}"}

        row = self._conn.execute(
            """
            SELECT doc_id, title, question, answer, ease, interval_days, review_count, lapse_count
            FROM card_states WHERE source_hash = ? AND card_key = ?
            """,
            (source_hash, card_key),
        ).fetchone()
        if not row:
            return {"error": "No stored review card was found for that document."}

        now = time.time()
        ease = float(row["ease"] or 2.3)
        interval_days = float(row["interval_days"] or 0.0)
        review_count = int(row["review_count"] or 0)
        lapse_count = int(row["lapse_count"] or 0)
        question = str(row["question"] or "").strip()

        if grade_key == "again":
            new_ease = max(1.3, ease - 0.2)
            new_interval = 0.01
            due_at = now + 10 * 60
            lapse_count += 1
        elif grade_key == "hard":
            new_ease = max(1.3, ease - 0.05)
            new_interval = max(1.0, interval_days * 1.2 if interval_days > 0 else 1.0)
            due_at = now + (new_interval * 86400)
        elif grade_key == "easy":
            new_ease = min(3.2, ease + 0.1)
            new_interval = max(3.0, interval_days * (ease + 0.4) if interval_days > 0 else 3.0)
            due_at = now + (new_interval * 86400)
        else:
            new_ease = min(3.0, ease + 0.03)
            new_interval = max(1.0, interval_days * ease if interval_days > 0 else 1.0)
            due_at = now + (new_interval * 86400)

        review_count += 1
        resolved_doc_id = doc_id if doc_id is not None else row["doc_id"]
        resolved_title = title or str(row["title"] or "Review")

        self._conn.execute(
            """
            UPDATE card_states
            SET doc_id = ?, title = ?, ease = ?, interval_days = ?, review_count = ?, lapse_count = ?,
                last_grade = ?, last_reviewed_at = ?, due_at = ?, updated_at = ?
            WHERE source_hash = ? AND card_key = ?
            """,
            (
                resolved_doc_id,
                resolved_title,
                round(new_ease, 4),
                round(new_interval, 4),
                review_count,
                lapse_count,
                grade_key,
                now,
                due_at,
                now,
                source_hash,
                card_key,
            ),
        )
        self._conn.execute(
            """
            INSERT INTO flashcard_reviews (source_hash, card_key, doc_id, title, grade, interval_days, due_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_hash,
                card_key,
                resolved_doc_id,
                resolved_title,
                grade_key,
                round(new_interval, 4),
                due_at,
                now,
            ),
        )

        profile_row = self._conn.execute(
            """
            SELECT grasp_level, review_count, weak_topics, strong_topics
            FROM progress_profiles WHERE source_hash = ?
            """,
            (source_hash,),
        ).fetchone()
        profile_reviews = int(profile_row["review_count"]) if profile_row else 0
        profile_grasp = float(profile_row["grasp_level"]) if profile_row else 0.0
        grade_score = {"again": 0.15, "hard": 0.45, "good": 0.75, "easy": 0.95}[grade_key]
        updated_grasp = ((profile_grasp * profile_reviews) + grade_score) / max(profile_reviews + 1, 1)
        existing_weak = str(profile_row["weak_topics"]) if profile_row else "[]"
        existing_strong = str(profile_row["strong_topics"]) if profile_row else "[]"
        weak_payload = existing_weak
        strong_payload = existing_strong
        if grade_key in {"again", "hard"}:
            weak_payload = self._merge_topics(existing_weak, [question])
        else:
            strong_payload = self._merge_topics(existing_strong, [question])
        self._conn.execute(
            """
            UPDATE progress_profiles
            SET doc_id = ?, title = ?, grasp_level = ?, review_count = ?, weak_topics = ?, strong_topics = ?, updated_at = ?
            WHERE source_hash = ?
            """,
            (
                resolved_doc_id,
                resolved_title,
                round(updated_grasp, 4),
                profile_reviews + 1,
                weak_payload,
                strong_payload,
                now,
                source_hash,
            ),
        )
        self._conn.commit()
        return {
            "status": "saved",
            "type": "flashcard_review",
            "card_key": card_key,
            "question": question,
            "grade": grade_key,
            "due_at": due_at,
            "interval_days": round(new_interval, 4),
            "grasp_level": round(updated_grasp, 4),
        }

    def record_progress_note(
        self,
        *,
        source_hash: str,
        doc_id: str | None,
        title: str,
        note: str,
        weak_topics: list[str] | None = None,
        strong_topics: list[str] | None = None,
        grasp_level: float | None = None,
        author: str = "agent",
        metadata: dict[str, Any] | None = None,
    ) -> dict:
        now = time.time()
        clean_weak = self._normalize_topic_lines(weak_topics)
        clean_strong = self._normalize_topic_lines(strong_topics)
        note_text = str(note or "").strip()
        if not note_text:
            return {"error": "Progress note cannot be empty"}
        self._conn.execute(
            """
            INSERT INTO progress_notes (source_hash, doc_id, title, note, author, grasp_level, weak_topics, strong_topics, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_hash,
                doc_id,
                title,
                encrypt_text(note_text),
                author,
                grasp_level,
                self._encode_list(clean_weak),
                self._encode_list(clean_strong),
                self._encrypt_json(metadata or {}),
                now,
            ),
        )
        if grasp_level is not None or clean_weak or clean_strong:
            row = self._conn.execute(
                "SELECT grasp_level FROM progress_profiles WHERE source_hash = ?",
                (source_hash,),
            ).fetchone()
            existing_grasp = float(row["grasp_level"]) if row else 0.0
            updated_grasp = existing_grasp if grasp_level is None else max(0.0, min(float(grasp_level), 1.0))
            self._conn.execute(
                """
                UPDATE progress_profiles
                SET doc_id = ?, title = ?, grasp_level = ?, weak_topics = ?, strong_topics = ?, updated_at = ?
                WHERE source_hash = ?
                """,
                (
                    doc_id,
                    title,
                    updated_grasp,
                    self._encode_list(clean_weak) if clean_weak else self._conn.execute(
                        "SELECT weak_topics FROM progress_profiles WHERE source_hash = ?",
                        (source_hash,),
                    ).fetchone()["weak_topics"],
                    self._encode_list(clean_strong) if clean_strong else self._conn.execute(
                        "SELECT strong_topics FROM progress_profiles WHERE source_hash = ?",
                        (source_hash,),
                    ).fetchone()["strong_topics"],
                    now,
                    source_hash,
                ),
            )
        self._conn.commit()
        return {"status": "saved", "type": "progress_note"}

    def link_note(
        self,
        *,
        source_hash: str,
        note_id: int,
        doc_id: str | None,
        title: str,
        page: int | None,
        tags: list[str] | None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO linked_notes (source_hash, note_id, doc_id, title, page, tags, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_hash, note_id) DO UPDATE SET
                doc_id=excluded.doc_id,
                title=excluded.title,
                page=excluded.page,
                tags=excluded.tags
            """,
            (source_hash, note_id, doc_id, title, page, self._encode_list(tags), time.time()),
        )
        self._conn.commit()

    def get_progress(self, *, source_hash: str | None = None, doc_id: str | None = None) -> dict:
        resolved_hash = source_hash or self.source_hash_for_doc(doc_id)
        if not resolved_hash:
            return {"error": "No stored study progress found for that document."}

        source_row = self._conn.execute(
            "SELECT source_hash, doc_id, title, path, updated_at FROM sources WHERE source_hash = ?",
            (resolved_hash,),
        ).fetchone()
        if not source_row:
            return {"error": "No stored study progress found for that document."}

        profile_row = self._conn.execute(
            "SELECT * FROM progress_profiles WHERE source_hash = ?",
            (resolved_hash,),
        ).fetchone()
        deck_count = self._conn.execute(
            "SELECT COUNT(*) FROM flashcard_decks WHERE source_hash = ?",
            (resolved_hash,),
        ).fetchone()[0]
        quiz_count = self._conn.execute(
            "SELECT COUNT(*) FROM quiz_attempts WHERE source_hash = ?",
            (resolved_hash,),
        ).fetchone()[0]
        linked_note_count = self._conn.execute(
            "SELECT COUNT(*) FROM linked_notes WHERE source_hash = ?",
            (resolved_hash,),
        ).fetchone()[0]

        recent_notes = self._conn.execute(
            """
            SELECT title, note, author, grasp_level, weak_topics, strong_topics, created_at
            FROM progress_notes WHERE source_hash = ? ORDER BY id DESC LIMIT 5
            """,
            (resolved_hash,),
        ).fetchall()
        recent_quizzes = self._conn.execute(
            """
            SELECT score, total, weak_topics, strong_topics, created_at
            FROM quiz_attempts WHERE source_hash = ? ORDER BY id DESC LIMIT 5
            """,
            (resolved_hash,),
        ).fetchall()

        return {
            "source_hash": resolved_hash,
            "doc_id": source_row["doc_id"],
            "title": source_row["title"],
            "grasp_level": round(float(profile_row["grasp_level"]), 4) if profile_row else 0.0,
            "review_count": int(profile_row["review_count"]) if profile_row else 0,
            "last_quiz_score": float(profile_row["last_quiz_score"]) if profile_row and profile_row["last_quiz_score"] is not None else None,
            "weak_topics": self._decode_list(profile_row["weak_topics"]) if profile_row else [],
            "strong_topics": self._decode_list(profile_row["strong_topics"]) if profile_row else [],
            "linked_counts": {
                "flashcard_decks": int(deck_count),
                "quiz_attempts": int(quiz_count),
                "notes": int(linked_note_count),
            },
            "recent_progress_notes": [
                {
                    "title": row["title"],
                    "note": decrypt_text(row["note"]),
                    "author": row["author"],
                    "grasp_level": row["grasp_level"],
                    "weak_topics": self._decode_list(row["weak_topics"]),
                    "strong_topics": self._decode_list(row["strong_topics"]),
                    "created_at": row["created_at"],
                }
                for row in recent_notes
            ],
            "recent_quizzes": [
                {
                    "score": row["score"],
                    "total": row["total"],
                    "weak_topics": self._decode_list(row["weak_topics"]),
                    "strong_topics": self._decode_list(row["strong_topics"]),
                    "created_at": row["created_at"],
                }
                for row in recent_quizzes
            ],
        }

    def get_review_queue(
        self,
        *,
        source_hash: str | None = None,
        doc_id: str | None = None,
        limit: int = 20,
    ) -> dict:
        resolved_hash = source_hash or self.source_hash_for_doc(doc_id)
        if not resolved_hash:
            return {"error": "No stored review deck found for that document."}

        card_rows = self._conn.execute(
            """
            SELECT card_key, question, answer, due_at, review_count, last_grade, interval_days, title
            FROM card_states
            WHERE source_hash = ?
            ORDER BY updated_at DESC
            """,
            (resolved_hash,),
        ).fetchall()
        if not card_rows:
            return {"error": "No stored review deck found for that document."}

        profile_row = self._conn.execute(
            "SELECT weak_topics FROM progress_profiles WHERE source_hash = ?",
            (resolved_hash,),
        ).fetchone()
        weak_topics = self._decode_list(profile_row["weak_topics"]) if profile_row else []
        now = time.time()
        cards = [
            {
                "card_key": str(row["card_key"]),
                "question": str(row["question"]),
                "answer": str(row["answer"]),
                "due_at": float(row["due_at"] or 0.0),
                "review_count": int(row["review_count"] or 0),
                "last_grade": str(row["last_grade"] or ""),
                "interval_days": float(row["interval_days"] or 0.0),
            }
            for row in card_rows
        ]
        deck_title = str(card_rows[0]["title"]) if card_rows else "Review Deck"

        def _priority(item: dict[str, Any]) -> tuple[int, int, float, int]:
            haystack = f"{item.get('question', '')} {item.get('answer', '')}".lower()
            hits = sum(1 for topic in weak_topics if topic.lower() in haystack)
            due_rank = 0 if float(item.get("due_at", 0.0)) <= now else 1
            return (due_rank, -hits, float(item.get("due_at", 0.0)), int(item.get("review_count", 0)))

        ranked = sorted(cards, key=_priority)
        due_count = sum(1 for card in cards if float(card.get("due_at", 0.0)) <= now)
        new_count = sum(1 for card in cards if int(card.get("review_count", 0)) == 0)
        return {
            "title": deck_title,
            "cards": ranked[: max(1, min(int(limit), 50))],
            "card_count": len(cards),
            "due_count": due_count,
            "new_count": new_count,
            "weak_topics": weak_topics,
        }

    def close(self) -> None:
        self._conn.close()
