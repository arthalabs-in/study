"""Retention engine — pure logic for study recommendations."""

from __future__ import annotations

from typing import Any


def build_quiz_recovery_plan(
    *,
    score: int,
    total: int,
    results: list[dict],
    weak_topics: list[str],
    strong_topics: list[str],
    profile: dict | None = None,
) -> dict:
    """Build a recovery plan after a quiz attempt."""
    ratio = (score / total) if total else 0.0
    wrong_count = total - score
    weak = list(weak_topics) if weak_topics else []
    strong = list(strong_topics) if strong_topics else []

    suggested_mode = "cloze" if profile and profile.get("question_style_bias") == "application_heavy" else "basic"
    if wrong_count <= 2:
        suggested_mode = "mixed"

    return {
        "recommended_action": "recovery_cards",
        "summary": (
            f"You missed mostly application-heavy questions around {', '.join(weak[:3])}."
            if weak
            else "You missed a few questions — a quick recovery set should lock these in."
        ),
        "weak_topics": weak,
        "strong_topics": strong,
        "suggested_card_count": min(max(wrong_count * 2, 4), 12),
        "suggested_mode": suggested_mode,
        "prompt_hint": (
            f"Create applied recall cards focused on {', '.join(weak[:2])}."
            if weak
            else "Create concise review cards for the missed concepts."
        ),
    }


def recommend_study_now(
    *,
    progress_snapshot: dict,
    personalization_profile: dict | None = None,
) -> dict:
    """Recommend what to study right now."""
    due_count = int(progress_snapshot.get("due_count", 0) or 0)
    new_count = int(progress_snapshot.get("new_count", 0) or 0)
    weak_topics = list(progress_snapshot.get("weak_topics", []) or [])
    review_card_count = int(progress_snapshot.get("review_card_count", 0) or 0)
    meaningful_sessions = int(progress_snapshot.get("meaningful_sessions", 0) or 0)

    session_len = 15
    if personalization_profile:
        session_len = int(personalization_profile.get("session_length_minutes", 15))

    if due_count > 0:
        recommended = "review"
        reason = f"{due_count} card{'s' if due_count != 1 else ''} are due"
        if weak_topics:
            reason += f" and recent weak areas cluster around {', '.join(str(w) for w in weak_topics[:3])}"
        reason += "."
        next_actions = [{"action": "review_due_cards", "count": min(due_count, 10)}]
        if weak_topics:
            next_actions.append({"action": "generate_recovery_cards", "topic": weak_topics[0], "count": 6})
        return {
            "recommended_mode": recommended,
            "reason": reason,
            "due_count": due_count,
            "new_count": new_count,
            "weak_topics": weak_topics,
            "suggested_session_length_minutes": session_len,
            "next_actions": next_actions,
        }

    if review_card_count > 0 and meaningful_sessions > 0:
        return {
            "recommended_mode": "review",
            "reason": "You have saved cards but none are due yet. A quick warm-up review is still useful.",
            "due_count": due_count,
            "new_count": new_count,
            "weak_topics": weak_topics,
            "suggested_session_length_minutes": max(5, session_len // 2),
            "next_actions": [{"action": "review_new_cards", "count": min(new_count or 5, 10)}],
        }

    return {
        "recommended_mode": "flashcards",
        "reason": "No cards are due yet. Generate new material or load a document to get started.",
        "due_count": due_count,
        "new_count": new_count,
        "weak_topics": weak_topics,
        "suggested_session_length_minutes": session_len,
        "next_actions": [{"action": "generate_flashcards", "topic": "current document", "count": 5}],
    }


def build_targeted_drill(
    *,
    progress_snapshot: dict,
    review_queue: dict,
    topic: str | None = None,
    count: int = 10,
    profile: dict | None = None,
) -> dict:
    """Build a targeted drill from review queue + weak topics."""
    cards = list(review_queue.get("cards", []) or [])
    weak_topics = list(progress_snapshot.get("weak_topics", []) or [])
    target_topic = (topic or (weak_topics[0] if weak_topics else None) or "").lower().strip()

    if target_topic:
        matched = [
            c for c in cards
            if target_topic in f"{c.get('question', '')} {c.get('answer', '')}".lower()
        ]
    else:
        matched = cards

    # Prioritize again/hard grades and due cards
    def _priority(c: dict) -> tuple[int, int]:
        grade = str(c.get("last_grade", "")).lower()
        due_rank = 0 if float(c.get("due_at", 0)) <= progress_snapshot.get("_now", 0) else 1
        grade_rank = {"again": 0, "hard": 1, "good": 2, "easy": 3}.get(grade, 2)
        return (due_rank, grade_rank)

    matched = sorted(matched, key=_priority)
    selected = matched[: max(1, min(int(count), 50))]

    return {
        "topic": target_topic or "weak areas",
        "selected_count": len(selected),
        "cards": selected,
        "reason": f"Drill focuses on '{target_topic or 'weak areas'}' with {len(selected)} cards.",
    }


def recommend_flashcard_generation(
    *,
    progress_snapshot: dict | None,
    profile: dict | None,
    requested_topic: str,
) -> dict:
    """Recommend parameters for flashcard generation."""
    weak_topics = list(progress_snapshot.get("weak_topics", []) if progress_snapshot else [])
    focus = "weak_area" if weak_topics and requested_topic.lower() in [w.lower() for w in weak_topics] else "new_material"
    mode = "mixed"
    if profile:
        qb = profile.get("question_style_bias", "mixed")
        if qb == "recall_heavy":
            mode = "basic"
        elif qb == "application_heavy":
            mode = "cloze"
    return {
        "recommended_focus": focus,
        "recommended_mode": mode,
        "recommended_count": 8,
        "focus_topics": [requested_topic] + weak_topics[:2],
        "reason": f"Generating {mode} cards for {requested_topic} with focus={focus}.",
    }


def recommend_quiz_generation(
    *,
    progress_snapshot: dict | None,
    profile: dict | None,
    requested_topic: str,
) -> dict:
    """Recommend parameters for quiz generation."""
    weak_topics = list(progress_snapshot.get("weak_topics", []) if progress_snapshot else [])
    difficulty = "medium"
    if profile:
        goal = profile.get("goal_bias", "mixed")
        if goal == "exam":
            difficulty = "hard"
        elif goal == "understanding":
            difficulty = "medium"
    return {
        "recommended_difficulty": difficulty,
        "recommended_count": 5,
        "focus_topics": [requested_topic] + weak_topics[:2],
        "reason": f"Generating {difficulty} quiz for {requested_topic}.",
    }
