"""Tests for study_progress retention additions."""

import pytest
import tempfile
from pathlib import Path

from src.study_progress import StudyProgressManager


@pytest.fixture
def temp_mgr():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    mgr = StudyProgressManager(db_path=path)
    yield mgr
    mgr.close()
    Path(path).unlink(missing_ok=True)


class TestSchemaMigration:
    def test_new_tables_exist(self, temp_mgr):
        tables = temp_mgr._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = {t["name"] for t in tables}
        assert "card_metadata" in names
        assert "anki_sync_state" in names
        assert "study_preferences" in names
        assert "study_events" in names


class TestCardMetadata:
    def test_upsert_and_get(self, temp_mgr):
        temp_mgr.upsert_source(source_hash="sh1", doc_id="d1", title="T1", path="/tmp")
        temp_mgr.upsert_card_metadata(
            source_hash="sh1",
            card_key="ck1",
            doc_id="d1",
            title="T1",
            card_type="cloze",
            cloze_text="{{c1::hello}}",
            source_refs=[{"doc_id": "d1", "page": 1}],
            tags=["a", "b"],
            focus="weak_area",
            difficulty="hard",
            payload_hash="abc",
        )
        temp_mgr._conn.commit()
        meta = temp_mgr.get_card_metadata(source_hash="sh1", card_key="ck1")
        assert meta is not None
        assert meta["card_type"] == "cloze"
        assert meta["focus"] == "weak_area"
        assert meta["source_refs"] == [{"doc_id": "d1", "page": 1}]

    def test_list_metadata(self, temp_mgr):
        temp_mgr.upsert_source(source_hash="sh1", doc_id="d1", title="T1", path="/tmp")
        temp_mgr.upsert_card_metadata(
            source_hash="sh1", card_key="ck1", doc_id="d1", title="T1",
            card_type="basic", cloze_text=None, source_refs=[], tags=[],
            focus="new_material", difficulty=None, payload_hash="h1",
        )
        temp_mgr._conn.commit()
        items = temp_mgr.list_card_metadata(source_hash="sh1")
        assert len(items) == 1
        assert items[0]["card_key"] == "ck1"


class TestAnkiSyncState:
    def test_upsert_and_get(self, temp_mgr):
        temp_mgr.upsert_source(source_hash="sh1", doc_id="d1", title="T1", path="/tmp")
        temp_mgr.upsert_anki_sync_state(
            source_hash="sh1", card_key="ck1", anki_note_id="123",
            deck_name="D", note_type="basic", payload_hash="h1",
        )
        state = temp_mgr.get_anki_sync_state(source_hash="sh1", card_key="ck1")
        assert state is not None
        assert state["deck_name"] == "D"


class TestPreferences:
    def test_save_and_get(self, temp_mgr):
        temp_mgr.save_preferences("default", {"goal": "exam", "session_length_minutes": 20})
        prefs = temp_mgr.get_preferences("default")
        assert prefs is not None
        assert prefs["goal"] == "exam"
        assert prefs["session_length_minutes"] == 20
        assert prefs["adaptive_enabled"] is True

    def test_partial_update_preserves_existing_preferences(self, temp_mgr):
        temp_mgr.save_preferences(
            "default",
            {
                "goal": "exam",
                "preferred_mode": "quiz",
                "tutoring_style": "direct",
                "session_length_minutes": 20,
                "question_style": "recall_heavy",
                "integrations_json": ["anki"],
                "adaptive_enabled": False,
            },
        )
        temp_mgr.save_preferences("default", {"session_length_minutes": 45})

        prefs = temp_mgr.get_preferences("default")
        assert prefs["goal"] == "exam"
        assert prefs["preferred_mode"] == "quiz"
        assert prefs["tutoring_style"] == "direct"
        assert prefs["session_length_minutes"] == 45
        assert prefs["question_style"] == "recall_heavy"
        assert prefs["integrations_json"] == ["anki"]
        assert prefs["adaptive_enabled"] is False

    def test_get_missing_returns_none(self, temp_mgr):
        assert temp_mgr.get_preferences("default") is None


class TestEvents:
    def test_record_and_list(self, temp_mgr):
        temp_mgr.record_event(profile_id="default", event_type="quiz_completed", payload={"score": 5})
        events = temp_mgr.list_events(profile_id="default")
        assert len(events) == 1
        assert events[0]["event_type"] == "quiz_completed"
        assert events[0]["payload"]["score"] == 5

    def test_list_since(self, temp_mgr):
        import time
        now = time.time()
        temp_mgr.record_event(profile_id="default", event_type="a", payload={})
        events = temp_mgr.list_events(profile_id="default", since=now + 10)
        assert len(events) == 0


class TestRetentionSnapshot:
    def test_returns_expected_keys(self, temp_mgr):
        temp_mgr.upsert_source(source_hash="sh1", doc_id="d1", title="Doc", path="/tmp")
        temp_mgr.record_flashcards(source_hash="sh1", doc_id="d1", title="Doc", cards=[{"question": "Q", "answer": "A"}])
        snap = temp_mgr.get_retention_snapshot(source_hash="sh1", doc_id="d1", profile_id="default")
        assert "due_count" in snap
        assert "preferences" in snap
        assert "meaningful_sessions" in snap


class TestRecordFlashcardsNormalization:
    def test_records_metadata(self, temp_mgr):
        temp_mgr.upsert_source(source_hash="sh1", doc_id="d1", title="Doc", path="/tmp")
        result = temp_mgr.record_flashcards(
            source_hash="sh1", doc_id="d1", title="Doc",
            cards=[{"question": "Q", "answer": "A", "card_type": "cloze", "cloze_text": "{{c1::x}}"}],
        )
        assert result["count"] == 1
        meta = temp_mgr.get_card_metadata(source_hash="sh1", card_key=result["count"])
        # card_key is derived, so just check at least one metadata row exists
        metas = temp_mgr.list_card_metadata(source_hash="sh1")
        assert len(metas) == 1
        assert metas[0]["card_type"] == "cloze"

    def test_review_queue_preserves_structured_source_refs(self, temp_mgr):
        temp_mgr.upsert_source(source_hash="sh1", doc_id="d1", title="Doc", path="/tmp")
        temp_mgr.record_flashcards(
            source_hash="sh1",
            doc_id="d1",
            title="Doc",
            cards=[{"question": "Q", "answer": "A", "source_refs": [{"doc_id": "d1", "page": 2, "chunk_id": "c9"}]}],
        )
        queue = temp_mgr.get_review_queue(source_hash="sh1", doc_id="d1")
        assert queue["cards"][0]["source_refs"] == [{"doc_id": "d1", "page": 2, "chunk_id": "c9"}]

    def test_null_source_ref_fields_are_dropped(self, temp_mgr):
        temp_mgr.upsert_source(source_hash="sh1", doc_id="d1", title="Doc", path="/tmp")
        temp_mgr.record_flashcards(
            source_hash="sh1",
            doc_id="d1",
            title="Doc",
            cards=[
                {
                    "question": "Q",
                    "answer": "A",
                    "source_refs": [{"doc_id": None, "page": 3, "chunk_id": None}],
                }
            ],
        )

        queue = temp_mgr.get_review_queue(source_hash="sh1", doc_id="d1")
        assert queue["cards"][0]["source_refs"] == [{"page": 3}]


class TestResetProfileData:
    def test_clears_events_and_prefs(self, temp_mgr):
        temp_mgr.save_preferences("default", {"goal": "exam"})
        temp_mgr.record_event(profile_id="default", event_type="x", payload={})
        temp_mgr.reset_profile_data("default")
        assert temp_mgr.get_preferences("default") is None
        assert temp_mgr.list_events(profile_id="default") == []


class TestHostileStudyProgress:
    def test_sql_injection_in_tags_sanitized(self, temp_mgr):
        temp_mgr.upsert_source(source_hash="sh1", doc_id="d1", title="T1", path="/tmp")
        temp_mgr.record_flashcards(
            source_hash="sh1",
            doc_id="d1",
            title="T1",
            cards=[{"question": "Q", "answer": "A", "tags": ["a'; DROP TABLE card_metadata;--"]}],
        )
        metas = temp_mgr.list_card_metadata(source_hash="sh1")
        assert len(metas) == 1
        assert "drop table" in metas[0]["tags"][0]  # stored as literal text, not executed

    def test_unicode_and_very_long_strings(self, temp_mgr):
        temp_mgr.upsert_source(source_hash="sh1", doc_id="d1", title="T1", path="/tmp")
        long_q = "Q" * 5000
        long_a = "A" * 5000
        temp_mgr.record_flashcards(
            source_hash="sh1", doc_id="d1", title="T1",
            cards=[{"question": long_q, "answer": long_a}],
        )
        queue = temp_mgr.get_review_queue(source_hash="sh1")
        assert queue["card_count"] == 1
        assert queue["cards"][0]["question"] == long_q

    def test_rapid_repeated_operations(self, temp_mgr):
        temp_mgr.upsert_source(source_hash="sh1", doc_id="d1", title="T1", path="/tmp")
        for i in range(50):
            temp_mgr.record_event(profile_id="default", event_type="quiz_completed", payload={"score": i})
        events = temp_mgr.list_events(profile_id="default", limit=1000)
        assert len(events) == 50

    def test_preferences_preserve_adaptive_disabled(self, temp_mgr):
        temp_mgr.save_preferences("default", {"adaptive_enabled": False})
        prefs = temp_mgr.get_preferences("default")
        assert prefs["adaptive_enabled"] is False

    def test_retention_snapshot_with_no_source(self, temp_mgr):
        snap = temp_mgr.get_retention_snapshot(source_hash="nonexistent", doc_id="none")
        assert "error" not in snap or snap.get("error") is None
        assert snap.get("due_count", 0) == 0

    def test_card_metadata_update_idempotent(self, temp_mgr):
        temp_mgr.upsert_source(source_hash="sh1", doc_id="d1", title="T1", path="/tmp")
        for _ in range(10):
            temp_mgr.upsert_card_metadata(
                source_hash="sh1", card_key="ck1", doc_id="d1", title="T1",
                card_type="basic", cloze_text=None, source_refs=[], tags=[],
                focus="new_material", difficulty=None, payload_hash="h1",
            )
        metas = temp_mgr.list_card_metadata(source_hash="sh1")
        assert len(metas) == 1
        assert metas[0]["card_type"] == "basic"
