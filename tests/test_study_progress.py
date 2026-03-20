from __future__ import annotations

from pathlib import Path

from src.study_progress import StudyProgressManager, compute_file_hash


def test_compute_file_hash_is_stable(tmp_path: Path) -> None:
    path = tmp_path / "doc.pdf"
    path.write_bytes(b"abc123")
    assert compute_file_hash(path) == compute_file_hash(path)


def test_study_progress_round_trip(tmp_path: Path) -> None:
    manager = StudyProgressManager(tmp_path / "progress.db")
    source_hash = "hash123"
    manager.upsert_source(
        source_hash=source_hash,
        doc_id="doc1",
        title="Thermodynamics",
        path=str(tmp_path / "thermo.pdf"),
    )
    manager.record_flashcards(
        source_hash=source_hash,
        doc_id="doc1",
        title="Thermodynamics",
        cards=[{"question": "What is heat?", "answer": "Energy transfer."}],
    )
    manager.record_quiz_attempt(
        source_hash=source_hash,
        doc_id="doc1",
        title="Thermodynamics",
        score=3,
        total=5,
        results=[
            {"question": "What is heat?", "correct": True},
            {"question": "What is entropy?", "correct": False},
        ],
    )
    manager.link_note(
        source_hash=source_hash,
        note_id=7,
        doc_id="doc1",
        title="Entropy note",
        page=3,
        tags=["thermo"],
    )
    manager.record_progress_note(
        source_hash=source_hash,
        doc_id="doc1",
        title="Thermodynamics",
        note="Still shaky on entropy definitions.",
        weak_topics=["What is entropy?"],
        strong_topics=["What is heat?"],
        grasp_level=0.6,
    )

    result = manager.get_progress(doc_id="doc1")
    assert result["doc_id"] == "doc1"
    assert result["linked_counts"]["flashcard_decks"] == 1
    assert result["linked_counts"]["quiz_attempts"] == 1
    assert result["linked_counts"]["notes"] == 1
    assert result["weak_topics"]
    assert result["recent_progress_notes"][0]["note"] == "Still shaky on entropy definitions."


def test_review_queue_prioritizes_weak_topics(tmp_path: Path) -> None:
    manager = StudyProgressManager(tmp_path / "progress.db")
    source_hash = "hash123"
    manager.upsert_source(
        source_hash=source_hash,
        doc_id="doc1",
        title="Kinematics",
        path=str(tmp_path / "kinematics.pdf"),
    )
    manager.record_flashcards(
        source_hash=source_hash,
        doc_id="doc1",
        title="Kinematics",
        cards=[
            {"question": "What is displacement?", "answer": "Change in position."},
            {"question": "Define instantaneous velocity.", "answer": "Velocity at an instant."},
        ],
    )
    manager.record_progress_note(
        source_hash=source_hash,
        doc_id="doc1",
        title="Kinematics",
        note="Weak on instantaneous velocity.",
        weak_topics=["instantaneous velocity"],
    )

    queue = manager.get_review_queue(doc_id="doc1")
    assert queue["cards"][0]["question"] == "Define instantaneous velocity."


def test_review_queue_and_review_grades_persist_schedule(tmp_path: Path) -> None:
    manager = StudyProgressManager(tmp_path / "progress.db")
    source_hash = "hash123"
    manager.upsert_source(
        source_hash=source_hash,
        doc_id="doc1",
        title="Thermodynamics",
        path=str(tmp_path / "thermo.pdf"),
    )
    manager.record_flashcards(
        source_hash=source_hash,
        doc_id="doc1",
        title="Thermodynamics",
        cards=[
            {"question": "What is heat?", "answer": "Energy transfer."},
            {"question": "What is entropy?", "answer": "Measure of disorder."},
        ],
    )

    queue = manager.get_review_queue(doc_id="doc1")
    assert queue["card_count"] == 2
    assert queue["due_count"] == 2
    first_card = queue["cards"][0]

    review = manager.record_flashcard_review(
        source_hash=source_hash,
        doc_id="doc1",
        title="Thermodynamics",
        card_key=first_card["card_key"],
        grade="easy",
    )
    assert review["status"] == "saved"
    assert review["grade"] == "easy"
    assert review["interval_days"] >= 3.0

    next_queue = manager.get_review_queue(doc_id="doc1")
    assert next_queue["due_count"] == 1
    progress = manager.get_progress(doc_id="doc1")
    assert progress["review_count"] >= 1
