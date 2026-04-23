"""Tests for retention_engine.py."""

import pytest
from src.retention_engine import (
    build_quiz_recovery_plan,
    recommend_study_now,
    build_targeted_drill,
    recommend_flashcard_generation,
    recommend_quiz_generation,
)


class TestBuildQuizRecoveryPlan:
    def test_basic_recovery(self):
        plan = build_quiz_recovery_plan(
            score=3,
            total=5,
            results=[],
            weak_topics=["thermodynamics"],
            strong_topics=["kinematics"],
        )
        assert plan["recommended_action"] == "recovery_cards"
        assert "thermodynamics" in plan["weak_topics"]
        assert plan["suggested_card_count"] > 0

    def test_low_misses_suggest_mixed(self):
        plan = build_quiz_recovery_plan(
            score=4,
            total=5,
            results=[],
            weak_topics=["a"],
            strong_topics=["b"],
        )
        assert plan["suggested_mode"] == "mixed"


class TestRecommendStudyNow:
    def test_due_cards_recommend_review(self):
        rec = recommend_study_now(progress_snapshot={"due_count": 5, "new_count": 2, "weak_topics": ["entropy"]})
        assert rec["recommended_mode"] == "review"
        assert rec["due_count"] == 5
        assert any(a["action"] == "review_due_cards" for a in rec["next_actions"])

    def test_no_due_but_cards_exist(self):
        rec = recommend_study_now(progress_snapshot={"due_count": 0, "new_count": 3, "review_card_count": 10, "meaningful_sessions": 2})
        assert rec["recommended_mode"] == "review"

    def test_nothing_defaults_to_flashcards(self):
        rec = recommend_study_now(progress_snapshot={"due_count": 0, "new_count": 0, "review_card_count": 0, "meaningful_sessions": 0})
        assert rec["recommended_mode"] == "flashcards"


class TestBuildTargetedDrill:
    def test_topic_match(self):
        cards = [
            {"card_key": "1", "question": "What is entropy?", "answer": "Disorder", "due_at": 0, "last_grade": "again"},
            {"card_key": "2", "question": "What is 2+2?", "answer": "4", "due_at": 0, "last_grade": "good"},
        ]
        drill = build_targeted_drill(
            progress_snapshot={"weak_topics": ["entropy"], "_now": 1},
            review_queue={"cards": cards},
            topic="entropy",
        )
        assert drill["topic"] == "entropy"
        assert len(drill["cards"]) == 1
        assert drill["cards"][0]["card_key"] == "1"

    def test_no_topic_uses_weak_areas(self):
        cards = [
            {"card_key": "1", "question": "Entropy?", "answer": "Disorder", "due_at": 0, "last_grade": "again"},
        ]
        drill = build_targeted_drill(
            progress_snapshot={"weak_topics": ["entropy"], "_now": 1},
            review_queue={"cards": cards},
        )
        assert drill["selected_count"] == 1

    def test_due_cards_are_ranked_ahead_of_future_cards(self):
        cards = [
            {"card_key": "future", "question": "Future card", "answer": "A", "due_at": 100, "last_grade": "again"},
            {"card_key": "due", "question": "Due card", "answer": "A", "due_at": 0, "last_grade": "good"},
        ]
        drill = build_targeted_drill(
            progress_snapshot={"weak_topics": [], "_now": 1},
            review_queue={"cards": cards},
            count=2,
        )
        assert [card["card_key"] for card in drill["cards"]] == ["due", "future"]


class TestRecommendFlashcardGeneration:
    def test_weak_area_focus(self):
        rec = recommend_flashcard_generation(
            progress_snapshot={"weak_topics": ["entropy"]},
            profile=None,
            requested_topic="entropy",
        )
        assert rec["recommended_focus"] == "weak_area"

    def test_new_material_for_unrelated_topic(self):
        rec = recommend_flashcard_generation(
            progress_snapshot={"weak_topics": ["entropy"]},
            profile=None,
            requested_topic="kinematics",
        )
        assert rec["recommended_focus"] == "new_material"


class TestRecommendQuizGeneration:
    def test_exam_bias_hard(self):
        rec = recommend_quiz_generation(
            progress_snapshot=None,
            profile={"goal_bias": "exam"},
            requested_topic="physics",
        )
        assert rec["recommended_difficulty"] == "hard"

    def test_understanding_bias_medium(self):
        rec = recommend_quiz_generation(
            progress_snapshot=None,
            profile={"goal_bias": "understanding"},
            requested_topic="physics",
        )
        assert rec["recommended_difficulty"] == "medium"


class TestHostileRetention:
    def test_quiz_recovery_zero_total(self):
        plan = build_quiz_recovery_plan(
            score=0,
            total=0,
            results=[],
            weak_topics=[],
            strong_topics=[],
        )
        assert plan["recommended_action"] == "recovery_cards"
        assert plan["suggested_card_count"] == 4

    def test_study_now_missing_keys(self):
        rec = recommend_study_now(progress_snapshot={}, personalization_profile=None)
        assert rec["recommended_mode"] in ("review", "flashcards")

    def test_drill_empty_queue(self):
        drill = build_targeted_drill(
            progress_snapshot={"weak_topics": []},
            review_queue={"cards": []},
        )
        assert drill["selected_count"] == 0

    def test_drill_extremely_high_count(self):
        cards = [{"card_key": str(i), "question": f"Q{i}", "answer": f"A{i}", "due_at": 0, "last_grade": "again"} for i in range(100)]
        drill = build_targeted_drill(
            progress_snapshot={"weak_topics": [], "_now": 1},
            review_queue={"cards": cards},
            count=1000,
        )
        assert drill["selected_count"] == 50  # clamped to max 50

    def test_recommend_flashcard_generation_none_snapshot(self):
        rec = recommend_flashcard_generation(
            progress_snapshot=None,
            profile=None,
            requested_topic="anything",
        )
        assert rec["recommended_focus"] == "new_material"

    def test_recommend_quiz_generation_none_profile(self):
        rec = recommend_quiz_generation(
            progress_snapshot=None,
            profile=None,
            requested_topic="anything",
        )
        assert rec["recommended_difficulty"] == "medium"
