"""Tests for personalization_engine.py."""

import pytest
from src.personalization_engine import (
    compute_profile,
    steering_summary,
    recommend_default_mode,
    recommend_session_length,
    recommend_question_mix,
    explain_recommendation,
)


class TestComputeProfile:
    def test_empty_everything(self):
        profile = compute_profile(preferences=None, events=[], progress_snapshot=None)
        assert profile["goal_bias"] == "mixed"
        assert profile["preferred_mode"] == "mixed"
        assert profile["tutoring_style"] == "direct"
        assert profile["session_length_minutes"] == 15
        assert profile["question_style_bias"] == "mixed"
        assert profile["weak_topics"] == []
        assert profile["confidence"]["preferred_mode"] < 0.5

    def test_explicit_preferences_preserved_when_no_data(self):
        prefs = {"goal": "exam", "preferred_mode": "quiz", "session_length_minutes": 25}
        profile = compute_profile(preferences=prefs, events=[], progress_snapshot=None)
        assert profile["goal_bias"] == "exam"
        assert profile["preferred_mode"] == "quiz"
        assert profile["session_length_minutes"] == 25

    def test_mode_inference_from_events(self):
        events = [
            {"event_type": "mode_completed", "payload": {"mode": "quiz"}},
            {"event_type": "mode_completed", "payload": {"mode": "quiz"}},
            {"event_type": "mode_abandoned", "payload": {"mode": "flashcards"}},
        ]
        profile = compute_profile(preferences=None, events=events, progress_snapshot=None)
        assert profile["preferred_mode"] == "quiz"

    def test_weak_topics_from_quiz(self):
        events = [
            {"event_type": "quiz_completed", "payload": {"weak_topics": ["entropy"], "strong_topics": []}},
        ]
        profile = compute_profile(preferences=None, events=events, progress_snapshot=None)
        assert "entropy" in profile["weak_topics"]

    def test_anki_affinity(self):
        events = [
            {"event_type": "flashcards_generated"},
            {"event_type": "exported_anki"},
        ]
        profile = compute_profile(preferences=None, events=events, progress_snapshot=None)
        assert profile["anki_affinity"] > 0.0


class TestSteeringSummary:
    def test_includes_key_lines(self):
        profile = compute_profile(preferences=None, events=[], progress_snapshot=None)
        summary = steering_summary(profile)
        assert "Goal bias" in summary
        assert "Preferred mode" in summary
        assert "Session length" in summary


class TestRecommendDefaultMode:
    def test_due_cards_trigger_review(self):
        snapshot = {"due_count": 10, "new_count": 0}
        assert recommend_default_mode({}, snapshot) == "review"

    def test_no_due_defaults_to_flashcards(self):
        snapshot = {"due_count": 0, "new_count": 0}
        assert recommend_default_mode({}, snapshot) == "flashcards"


class TestRecommendSessionLength:
    def test_returns_profile_value(self):
        assert recommend_session_length({"session_length_minutes": 20}) == 20


class TestRecommendQuestionMix:
    def test_recall_heavy(self):
        mix = recommend_question_mix({"question_style_bias": "recall_heavy"})
        assert mix["recall"] > mix["application"]

    def test_application_heavy(self):
        mix = recommend_question_mix({"question_style_bias": "application_heavy"})
        assert mix["application"] > mix["recall"]


class TestExplainRecommendation:
    def test_uses_reason_if_present(self):
        rec = {"reason": "Because you said so."}
        assert "Because you said so." in explain_recommendation({}, rec)

    def test_falls_back_to_weak_topics(self):
        rec = {"recommended_mode": "review"}
        profile = {"weak_topics": ["entropy"]}
        assert "entropy" in explain_recommendation(profile, rec)


class TestHostilePersonalization:
    def test_malformed_event_types_ignored(self):
        events = [
            {"event_type": "mode_completed", "payload": {"mode": "quiz"}},
            {"event_type": "unknown_event_xyz", "payload": {"mode": "quiz"}},
            {"event_type": "mode_completed"},  # missing payload
            {"event_type": "mode_abandoned", "payload": {"mode": "flashcards"}},
            {},  # completely empty event
        ]
        profile = compute_profile(preferences=None, events=events, progress_snapshot=None)
        assert profile["preferred_mode"] == "quiz"

    def test_negative_session_length_fallback(self):
        profile = compute_profile(
            preferences={"session_length_minutes": -5},
            events=[],
            progress_snapshot=None,
        )
        assert profile["session_length_minutes"] == 1  # clamped to minimum of 1

    def test_extreme_event_counts(self):
        events = [{"event_type": "quiz_completed", "payload": {"weak_topics": ["t"], "strong_topics": []}} for _ in range(500)]
        profile = compute_profile(preferences=None, events=events, progress_snapshot=None)
        assert len(profile["weak_topics"]) <= 8
        assert profile["confidence"]["preferred_mode"] >= 0.6

    def test_all_abandoned_sessions(self):
        events = [
            {"event_type": "mode_abandoned", "payload": {"mode": "quiz"}},
            {"event_type": "mode_abandoned", "payload": {"mode": "quiz"}},
        ]
        profile = compute_profile(preferences=None, events=events, progress_snapshot=None)
        assert profile["preferred_mode"] == "mixed"

    def test_empty_progress_snapshot(self):
        profile = compute_profile(preferences=None, events=[], progress_snapshot={})
        assert profile["goal_bias"] == "mixed"

    def test_sql_injection_in_preferences_ignored(self):
        profile = compute_profile(
            preferences={"goal": "exam'; DROP TABLE study_events;--"},
            events=[],
            progress_snapshot=None,
        )
        assert profile["goal_bias"] == "mixed"
