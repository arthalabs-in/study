from __future__ import annotations

from src.context_engine import (
    build_context_snapshot,
    compact_assistant_context,
    compact_model_history,
    compact_tool_result,
    make_model_history_entry,
    make_tool_artifact,
    prune_tool_artifacts,
)


def test_compact_assistant_context_detects_flashcards() -> None:
    summary, category = compact_assistant_context(
        "Here are cards\n[FLASHCARDS]\nQ: What is heat?\nA: Energy transfer.\nQ: What is entropy?\nA: Disorder.\n[/FLASHCARDS]"
    )
    assert category == "flashcards"
    assert "generated 2 flashcards" in summary.lower()
    assert "What is heat?" in summary
    assert "Energy transfer." not in summary


def test_build_context_snapshot_tracks_memory_and_contributors() -> None:
    model_history = [
        make_model_history_entry("user", "Explain entropy simply."),
        make_model_history_entry("assistant", "Entropy is a measure of disorder in a system."),
    ]
    artifact = make_tool_artifact("list_documents", [{"doc_id": "doc1", "title": "Thermo"}], 1)
    snapshot = build_context_snapshot(
        model_history=[entry for entry in model_history if entry],
        compact_memories=[{"id": "mem_1", "summary": "Earlier thermodynamics discussion", "source_count": 4}],
        tool_artifacts=[artifact] if artifact else [],
        current_turn_index=2,
        transcript_messages=6,
        model_name="gpt-4o",
        system_prompt="system",
        context_limit=128000,
        tool_result_chars=321,
        selected_tool_count=2,
        tool_schema_tokens=150,
    )
    assert snapshot.compact_memory_blocks == 1
    assert snapshot.tool_result_chars == 321
    assert snapshot.category_sizes["memory"] > 0
    assert snapshot.category_sizes["chat"] > 0
    assert snapshot.category_sizes["tool_results"] == 321
    assert snapshot.category_sizes["tool_schemas"] == 150
    assert snapshot.gist_artifact_count == 1
    assert snapshot.selected_tool_count == 2
    assert snapshot.largest_contributors
    assert any("compacted session memory" in msg["content"].lower() for msg in snapshot.messages)
    assert any("Recent tool context" in msg["content"] for msg in snapshot.messages)


def test_build_context_snapshot_emits_internal_context_as_system_messages() -> None:
    model_history = [make_model_history_entry("user", "load keph102 and animate velocity")]
    artifact = make_tool_artifact("list_available_files", [], 1)
    snapshot = build_context_snapshot(
        model_history=[entry for entry in model_history if entry],
        compact_memories=[{"id": "mem_1", "summary": "Earlier context", "source_count": 1}],
        tool_artifacts=[artifact] if artifact else [],
        current_turn_index=2,
        transcript_messages=3,
        model_name="gpt-4o",
        system_prompt="system",
        context_limit=128000,
    )
    internal_messages = [msg for msg in snapshot.messages if "Host internal context" in msg["content"]]
    assert internal_messages
    assert all(msg["role"] == "system" for msg in internal_messages)


def test_compact_model_history_keeps_recent_tail() -> None:
    model_history = []
    for idx in range(12):
        model_history.append(make_model_history_entry("user", f"user {idx}"))
        model_history.append(make_model_history_entry("assistant", "assistant " + ("x" * 800)))

    result = compact_model_history(
        model_history=[entry for entry in model_history if entry],
        compact_memories=[],
        compacted_transcript_count=0,
    )
    assert result.compacted is True
    assert result.memory_block is not None
    assert len(result.kept_model_history) == 8
    assert result.compacted_count == len(model_history) - 8


def test_compact_tool_result_limits_large_lists_and_text() -> None:
    compacted = compact_tool_result(
        "search_chunks",
        {"chunks": [{"text": "x" * 2000}] * 10, "results": [{"content": "y" * 2000}]},
    )
    assert len(compacted["chunks"]) == 5
    assert "chars omitted" in compacted["chunks"][0]["text"]
    assert "chars omitted" in compacted["results"][0]["content"]


def test_compact_assistant_context_detects_library_metadata() -> None:
    summary, category = compact_assistant_context(
        "Zotero results: found 12 library matches for thermodynamics with notes and attachments."
    )
    assert category == "library_metadata"
    assert "Zotero results" in summary


def test_compact_tool_result_uses_stricter_limits_for_library_sources() -> None:
    compacted = compact_tool_result(
        "zotero_search",
        {"results": [{"content": "z" * 1200}] * 8},
    )
    assert len(compacted["results"]) == 5
    assert "chars omitted" in compacted["results"][0]["content"]


def test_compact_tool_result_preserves_generate_quiz_json_structure() -> None:
    raw = {
        "tool": "generate_quiz",
        "result": (
            '[{"type":"mcq","question":"What is SI unit?","options":["a) force","b) energy","c) electric current","d) density"],'
            '"answer":"c","explanation":"Electric current is a base SI quantity."}]'
        ),
    }

    compacted = compact_tool_result("generate_quiz", raw)

    assert isinstance(compacted["result"], list)
    assert compacted["result"][0]["type"] == "mcq"
    assert compacted["result"][0]["question"] == "What is SI unit?"


def test_compact_tool_result_preserves_animation_metadata() -> None:
    compacted = compact_tool_result(
        "animate_concept",
        {
            "status": "error",
            "topic": "entropy",
            "attempt": 2,
            "retryable": True,
            "quality": "low",
            "scene_name": "EntropyScene",
            "duration_seconds": 4.25,
            "video_path": "/tmp/entropy.mp4",
            "code_path": "/tmp/entropy.py",
            "error": "x" * 400,
            "stderr_preview": "y" * 400,
            "ignored": "nope",
        },
    )
    assert compacted["topic"] == "entropy"
    assert compacted["retryable"] is True
    assert compacted["scene_name"] == "EntropyScene"
    assert "ignored" not in compacted


def test_prune_tool_artifacts_keeps_chunk_context_for_the_active_conversation() -> None:
    artifact = make_tool_artifact(
        "search_chunks",
        {"chunks": [{"doc_id": "doc1", "page": 2, "text": "Entropy is disorder."}]},
        3,
    )
    assert artifact is not None

    age_zero = prune_tool_artifacts([artifact], 3)
    assert age_zero.full_count == 1
    assert age_zero.gist_count == 0

    age_one = prune_tool_artifacts([artifact], 4)
    assert age_one.full_count == 0
    assert age_one.gist_count == 1

    age_two = prune_tool_artifacts([artifact], 5)
    assert len(age_two.kept) == 1
    assert age_two.gist_count == 1
    assert age_two.dropped_count == 0


def test_prune_tool_artifacts_still_drops_ephemeral_file_listings() -> None:
    artifact = make_tool_artifact(
        "list_available_files",
        [{"relative_path": "keph102.pdf", "source_name": "keph102.pdf"}],
        3,
    )
    assert artifact is not None

    age_two = prune_tool_artifacts([artifact], 5)
    assert age_two.kept == []
    assert age_two.dropped_count == 1
