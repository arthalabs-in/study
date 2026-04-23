"""Deterministic personalization engine — computes study profiles from behavior."""

from __future__ import annotations

import statistics
from typing import Any


_VALID_GOALS = {"exam", "understanding", "mixed"}
_VALID_MODES = {"flashcards", "quiz", "review", "explain", "mixed"}
_VALID_STYLES = {"direct", "socratic", "concept_first", "exam_first"}
_VALID_QUESTION_STYLES = {"recall_heavy", "mixed", "application_heavy"}


def compute_profile(
    *,
    preferences: dict | None,
    events: list[dict],
    progress_snapshot: dict | None,
) -> dict:
    """Compute a deterministic study profile from preferences + events + progress."""
    prefs = preferences or {}
    event_count = len(events)

    # Explicit preferences (cold-start)
    goal_bias = _first_valid(prefs.get("goal"), _VALID_GOALS, "mixed")
    explicit_preferred_mode = str(preferences.get("preferred_mode") or "").strip().lower() if preferences else ""
    explicit_tutoring_style = str(preferences.get("tutoring_style") or "").strip().lower() if preferences else ""
    explicit_session_length = preferences.get("session_length_minutes") if preferences else None
    explicit_question_style = str(preferences.get("question_style") or "").strip().lower() if preferences else ""

    preferred_mode = explicit_preferred_mode if explicit_preferred_mode in _VALID_MODES else "mixed"
    tutoring_style = explicit_tutoring_style if explicit_tutoring_style in _VALID_STYLES else "direct"
    session_length_minutes = _to_int(explicit_session_length, 15)
    question_style_bias = explicit_question_style if explicit_question_style in _VALID_QUESTION_STYLES else "mixed"

    # Behavior inference
    mode_scores = _infer_mode_scores(events)
    inferred_preferred_mode = _max_key(mode_scores) if mode_scores else preferred_mode
    inferred_session_length = _median_completed_session_length(events)
    anki_affinity = _compute_affinity(events, "anki")
    quiz_affinity = _compute_affinity(events, "quiz")
    review_affinity = _compute_affinity(events, "review")
    explanation_affinity = _compute_affinity(events, "explain")
    inferred_question_style = _infer_question_style(events)
    inferred_tutoring_style = _infer_tutoring_style(events)
    weak_topics = _infer_weak_topics(events)

    # Confidence gating
    meaningful_sessions = sum(1 for e in events if e.get("event_type") in ("quiz_completed", "review_session_finished", "flashcards_generated", "mode_completed"))
    def _conf(value: Any, inferred: Any, explicit: Any) -> float:
        if meaningful_sessions < 3:
            return 0.25
        if meaningful_sessions < 10:
            return 0.55
        return 0.82 if value == inferred else 0.65

    # Only override explicit preferences when confidence is decent
    final_preferred_mode = preferred_mode if (meaningful_sessions < 3 and explicit_preferred_mode) else inferred_preferred_mode
    final_tutoring_style = tutoring_style if (meaningful_sessions < 3 and explicit_tutoring_style) else inferred_tutoring_style
    final_session_length = inferred_session_length or session_length_minutes
    final_question_style = question_style_bias if (meaningful_sessions < 3 and explicit_question_style) else inferred_question_style

    profile = {
        "goal_bias": goal_bias,
        "preferred_mode": final_preferred_mode,
        "tutoring_style": final_tutoring_style,
        "session_length_minutes": final_session_length,
        "question_style_bias": final_question_style,
        "anki_affinity": round(anki_affinity, 4),
        "quiz_affinity": round(quiz_affinity, 4),
        "review_affinity": round(review_affinity, 4),
        "explanation_affinity": round(explanation_affinity, 4),
        "weak_topics": weak_topics,
        "confidence": {
            "preferred_mode": round(_conf(final_preferred_mode, inferred_preferred_mode, preferred_mode), 2),
            "tutoring_style": round(_conf(final_tutoring_style, inferred_tutoring_style, tutoring_style), 2),
            "session_length_minutes": round(_conf(final_session_length, inferred_session_length, session_length_minutes), 2),
        },
    }
    return profile


def steering_summary(profile: dict) -> str:
    """Produce a compact steering summary for model prompts."""
    lines = [
        f"Study profile summary:",
        f"- Goal bias: {profile.get('goal_bias', 'mixed')}",
        f"- Preferred mode: {profile.get('preferred_mode', 'mixed')}",
        f"- Session length: ~{profile.get('session_length_minutes', 15)} minutes",
        f"- Question style: {profile.get('question_style_bias', 'mixed')}",
    ]
    weak = profile.get("weak_topics") or []
    if weak:
        lines.append(f"- Weak areas: {', '.join(str(w) for w in weak[:6])}")
    integrations = []
    if profile.get("anki_affinity", 0) > 0.3:
        integrations.append("Anki enabled")
    if integrations:
        lines.append(f"- Integrations: {', '.join(integrations)}")
    lines.append(f"- Teaching style: {profile.get('tutoring_style', 'direct')}")
    return "\n".join(lines)


def recommend_default_mode(profile: dict, progress_snapshot: dict | None) -> str:
    mode = str(profile.get("preferred_mode", "mixed"))
    if mode != "mixed":
        return mode
    if progress_snapshot:
        due = int(progress_snapshot.get("due_count", 0) or 0)
        if due > 5:
            return "review"
    return "flashcards"


def recommend_session_length(profile: dict) -> int:
    return int(profile.get("session_length_minutes", 15))


def recommend_question_mix(profile: dict) -> dict:
    style = str(profile.get("question_style_bias", "mixed"))
    if style == "recall_heavy":
        return {"recall": 0.7, "application": 0.2, "mixed": 0.1}
    if style == "application_heavy":
        return {"recall": 0.2, "application": 0.7, "mixed": 0.1}
    return {"recall": 0.4, "application": 0.4, "mixed": 0.2}


def explain_recommendation(profile: dict, recommendation: dict) -> str:
    reason = recommendation.get("reason", "")
    if reason:
        return reason
    mode = recommendation.get("recommended_mode", "review")
    weak = profile.get("weak_topics", [])
    if weak:
        return f"Recommended {mode} because weak areas were identified: {', '.join(str(w) for w in weak[:3])}."
    return f"Recommended {mode} based on your study profile."


# ── Internal helpers ─────────────────────────────────────────────

def _first_valid(value: Any, valid_set: set, default: str) -> str:
    v = str(value or "").strip().lower()
    return v if v in valid_set else default


def _to_int(value: Any, default: int) -> int:
    try:
        return max(1, int(value))
    except Exception:
        return default


def _infer_mode_scores(events: list[dict]) -> dict[str, float]:
    scores: dict[str, float] = {"flashcards": 0.0, "quiz": 0.0, "review": 0.0, "explain": 0.0}
    for e in events:
        et = str(e.get("event_type", "")).strip().lower()
        if et == "mode_completed":
            payload = e.get("payload") or {}
            mode = str(payload.get("mode", "")).strip().lower()
            if mode in scores:
                scores[mode] += 1.0
        elif et == "mode_started":
            payload = e.get("payload") or {}
            mode = str(payload.get("mode", "")).strip().lower()
            if mode in scores:
                scores[mode] += 0.4
        elif et == "mode_abandoned":
            payload = e.get("payload") or {}
            mode = str(payload.get("mode", "")).strip().lower()
            if mode in scores:
                scores[mode] -= 0.8
        elif et in ("flashcards_generated",):
            scores["flashcards"] += 0.3
        elif et in ("quiz_completed",):
            scores["quiz"] += 0.3
        elif et in ("review_session_finished",):
            scores["review"] += 0.3
    return scores


def _max_key(scores: dict[str, float]) -> str:
    if not scores:
        return "mixed"
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else "mixed"


def _median_completed_session_length(events: list[dict]) -> int | None:
    lengths: list[int] = []
    for e in events:
        if e.get("event_type") == "mode_completed":
            payload = e.get("payload") or {}
            mins = payload.get("session_length_minutes")
            if mins is not None:
                try:
                    lengths.append(max(1, int(mins)))
                except Exception:
                    pass
    if not lengths:
        return None
    try:
        return int(statistics.median(lengths))
    except Exception:
        return lengths[len(lengths) // 2]


def _compute_affinity(events: list[dict], label: str) -> float:
    meaningful = sum(
        1 for e in events
        if e.get("event_type") in ("quiz_completed", "review_session_finished", "flashcards_generated")
    )
    if meaningful == 0:
        return 0.0
    matches = sum(
        1 for e in events
        if e.get("event_type") in (f"exported_{label}", f"{label}_sync_completed")
    )
    return min(1.0, matches / max(meaningful, 1))


def _infer_question_style(events: list[dict]) -> str:
    recall = sum(1 for e in events if e.get("event_type") == "exported_anki")
    applied = sum(1 for e in events if e.get("event_type") == "quiz_completed")
    if recall > applied * 1.5:
        return "recall_heavy"
    if applied > recall * 1.5:
        return "application_heavy"
    return "mixed"


def _infer_tutoring_style(events: list[dict]) -> str:
    simpler = sum(1 for e in events if e.get("event_type") == "asked_for_simpler_explanation")
    deeper = sum(1 for e in events if e.get("event_type") == "asked_for_more_depth")
    if simpler > deeper:
        return "direct"
    if deeper > simpler:
        return "concept_first"
    return "direct"


def _infer_weak_topics(events: list[dict]) -> list[str]:
    topic_weights: dict[str, float] = {}
    for e in events:
        et = e.get("event_type")
        payload = e.get("payload") or {}
        weight = 0.0
        if et == "quiz_completed":
            for t in payload.get("weak_topics", []):
                weight = 2.0
                topic_weights[t] = topic_weights.get(t, 0.0) + weight
            for t in payload.get("strong_topics", []):
                topic_weights[t] = topic_weights.get(t, 0.0) - 1.0
        elif et == "flashcard_reviewed" and payload.get("grade") == "again":
            # best-effort topic hint from tags if available; here we just weight the card_key lightly
            pass
    sorted_topics = sorted(topic_weights.items(), key=lambda x: x[1], reverse=True)
    return [t for t, w in sorted_topics if w > 0][:8]
