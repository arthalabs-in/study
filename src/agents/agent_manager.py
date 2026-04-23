"""
Agent Manager — orchestrates tool execution and subagent spawning.
Bridges LLM tool calls to DocStore methods and spawns subagents for complex tasks.
"""

from __future__ import annotations

import asyncio
import base64
import difflib
import json
import os
import re
import uuid
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from src.agents.provider import LLMProvider
from src.agents.tools import ALL_TOOLS, DOCUMENT_TOOLS
from src.exporter import export_flashcards, export_summary, export_chat
from src.manim_renderer import (
    RenderResult,
    get_animation_dependency_error,
    render_animation,
)
from src.motion_canvas_renderer import (
    get_motion_canvas_dependency_error,
    render_motion_canvas_animation,
)
from src.notes import NotesManager
from src.parsers.doc_store import DocStore
from src.parsers.image_parser import SUPPORTED_EXTENSIONS as IMG_EXTENSIONS
from src.pomodoro import PomodoroTimer
from src.study_progress import StudyProgressManager
from src.web_search import web_search
from src import calibre_client, zotero_client
from src.personalization_engine import compute_profile, steering_summary
from src.retention_engine import recommend_flashcard_generation, recommend_quiz_generation
from src.card_formats import normalize_cards


@dataclass
class AgentManager:
    """
    Manages tool execution and subagent spawning.

    Routes LLM tool calls to the appropriate handlers:
    - Document tools → DocStore methods
    - spawn_subagent → creates a new LLM chat with doc access
    - Study tools → specialized prompts via the LLM
    """

    doc_store: DocStore
    provider: LLMProvider
    on_status: Callable[[str], None] | None = None
    documents_dir: str | Path | None = None
    file_loader: Callable[[str], Any] | None = None  # async callback from app
    notes_manager: NotesManager = field(default_factory=NotesManager)
    pomodoro: PomodoroTimer = field(default_factory=PomodoroTimer)
    chat_history_ref: list | None = None  # reference to app's chat history for export
    flashcards_ref: list | None = None  # most recently generated flashcards for export
    allow_web_tools: bool = False
    calibre_library: str | Path | None = None
    request_tool_approval: Callable[[str, dict], Awaitable[bool]] | None = None
    default_export_dir: str | Path | None = None
    progress_manager: StudyProgressManager | None = None
    source_hash_resolver: Callable[[str | None], str | None] | None = None

    # Supported file extensions for autoloader
    _LOADABLE = {".pdf"} | set(IMG_EXTENSIONS)
    _APPROVAL_REQUIRED_TOOLS = {"save_note", "export_content", "animate_concept", "anki_sync_recent"}
    _ZOTERO_ITEM_KEY_RE = re.compile(r"^[A-Z0-9]{8}$")

    async def execute_tool(self, name: str, args: dict) -> Any:
        """Route a tool call to its handler. Returns the result."""
        self._emit_status(self._tool_status_message(name, args))

        # Document tools
        if name == "search_chunks":
            top_k = self._clamp_int(args.get("top_k", 5), default=5, minimum=1, maximum=20)
            return self.doc_store.search_chunks(
                query=args["query"],
                top_k=top_k,
            )

        elif name == "get_chunk_by_id":
            result = self.doc_store.get_chunk_by_id(args["chunk_id"])
            return result or {"error": f"Chunk not found: {args['chunk_id']}"}

        elif name == "get_chunks_by_page":
            return self.doc_store.get_chunks_by_page(
                doc_id=args["doc_id"],
                page_number=args["page_number"],
            )

        elif name == "list_documents":
            return self.doc_store.list_documents()

        elif name == "get_document_outline":
            return self.doc_store.get_document_outline(args["doc_id"])

        # Image tools
        elif name == "get_document_images":
            return self.doc_store.get_document_images(args["doc_id"])

        elif name == "get_page_image":
            img_meta = self.doc_store.get_page_image(
                doc_id=args["doc_id"],
                page_number=args["page_number"],
            )
            if not img_meta:
                return {"error": f"Could not render page {args['page_number']}"}
            img_path = Path(img_meta["path"])
            if not img_path.exists():
                return {"error": f"Image file missing: {img_path}"}
            try:
                img_bytes = img_path.read_bytes()
                b64 = base64.b64encode(img_bytes).decode("utf-8")
                return {
                    "page": img_meta["page"],
                    "figure_count": img_meta.get("figure_count", 0),
                    "media_type": "image/jpeg",
                    "base64_data": b64,
                }
            except Exception as e:
                return {"error": f"Failed to read image: {e}"}

        # Subagent spawning
        elif name == "spawn_subagent":
            return await self._spawn_subagent(
                task=args["task"],
                context=args.get("context", ""),
            )

        # Study tools — handled by prompting the model
        elif name in ("generate_flashcards", "generate_quiz", "summarize_document"):
            return await self._study_tool(name, args)
        elif name == "get_recent_flashcards":
            return self._get_recent_flashcards(args)

        # Autoloader tools
        elif name == "list_available_files":
            return self._list_available_files(args.get("filter"))

        elif name == "load_file":
            return await self._autoload_file(args["file_path"])

        # Web search
        elif name == "web_search":
            if not self.allow_web_tools:
                return {"error": "Web search is disabled. Use /web on to enable it."}
            self._emit_status("🌐 Searching the web...")
            max_results = self._clamp_int(
                args.get("max_results", 5),
                default=5,
                minimum=1,
                maximum=10,
            )
            return web_search(args["query"], max_results)

        # Note tools
        elif name == "save_note":
            approved = await self._ensure_tool_approval(name, args)
            if not approved:
                return {"status": "denied", "error": "User denied approval for save_note."}
            result = self.notes_manager.save_note(
                title=args["title"],
                content=args["content"],
                doc_id=args.get("doc_id"),
                page=args.get("page"),
                tags=args.get("tags"),
            )
            if "id" in result:
                self._link_saved_note(
                    note_id=int(result["id"]),
                    title=str(result.get("title", args.get("title", "Untitled note"))),
                    doc_id=args.get("doc_id"),
                    page=args.get("page"),
                    tags=args.get("tags"),
                )
            return result

        elif name == "list_notes":
            return self.notes_manager.list_notes(
                doc_id=args.get("doc_id"),
                tag=args.get("tag"),
            )

        elif name == "search_notes":
            return self.notes_manager.search_notes(args["query"])

        elif name == "get_study_progress":
            return self._get_study_progress(args.get("doc_id"))

        elif name == "save_progress_note":
            return self._save_progress_note(
                doc_id=args.get("doc_id"),
                note=args.get("note", ""),
                weak_topics=args.get("weak_topics"),
                strong_topics=args.get("strong_topics"),
                grasp_level=args.get("grasp_level"),
            )

        elif name == "get_review_queue":
            return self._get_review_queue(args.get("doc_id"), args.get("count", 20))

        elif name == "get_study_preferences":
            return self._get_study_preferences()

        elif name == "save_study_preferences":
            return self._save_study_preferences(args)

        elif name == "get_retention_snapshot":
            return self._get_retention_snapshot(args.get("doc_id"))

        elif name == "anki_sync_recent":
            approved = await self._ensure_tool_approval(name, args)
            if not approved:
                return {"status": "denied", "error": "User denied approval for anki_sync_recent."}
            return await self._sync_recent_to_anki(args)

        elif name == "animate_concept":
            approved = await self._ensure_tool_approval(name, args)
            if not approved:
                return {"status": "denied", "error": "User denied approval for animate_concept."}
            return await self._handle_animate(args)

        # Export
        elif name == "export_content":
            approved = await self._ensure_tool_approval(name, args)
            if not approved:
                return {"status": "denied", "error": "User denied approval for export_content."}
            return self._handle_export(args)

        # Pomodoro
        elif name == "pomodoro_start":
            return self.pomodoro.start(args.get("work_mins"))

        elif name == "pomodoro_status":
            return self.pomodoro.status()

        elif name == "pomodoro_stop":
            return self.pomodoro.stop()

        # Calibre tools
        elif name == "calibre_search":
            return self._calibre_search(args.get("query", ""))

        elif name == "calibre_load":
            return await self._calibre_load(args["book_id"])

        # Zotero tools
        elif name == "zotero_search":
            return self._zotero_search(
                query=args.get("query", ""),
                tag=args.get("tag"),
                collection=args.get("collection"),
            )

        elif name == "zotero_load":
            return await self._zotero_load(args["item_key"])

        elif name == "zotero_collections":
            try:
                return zotero_client.list_collections()
            except Exception as e:
                return {"error": str(e)}

        else:
            return self._unknown_tool_error(name)

    def _unknown_tool_error(self, attempted_name: str) -> dict[str, Any]:
        available_tools = [str(tool.get("name", "")).strip() for tool in ALL_TOOLS if tool.get("name")]
        close_matches = difflib.get_close_matches(attempted_name, available_tools, n=5, cutoff=0.45)
        lines = [
            f"Unknown tool: {attempted_name}.",
            "Retry with one of the valid tool names below.",
        ]
        if close_matches:
            lines.append("Closest matches: " + ", ".join(close_matches))
        lines.append("Available tools: " + ", ".join(available_tools))
        return {
            "error": "\n".join(lines),
            "attempted_tool": attempted_name,
            "closest_tools": close_matches,
            "available_tools": available_tools,
        }

    async def _spawn_subagent(self, task: str, context: str = "") -> dict:
        """Spawn a subagent with its own LLM chat and doc tool access."""
        agent_id = f"agent_{uuid.uuid4().hex[:8]}"
        self._emit_status(f"🤖 Spawning subagent {agent_id}: {task[:60]}...")

        system_prompt = (
            "You are a research sub-agent. You have access to a document store "
            "with loaded study materials. Your job is to complete the following task "
            "by searching and reading the relevant document chunks.\n\n"
            "Be thorough and cite specific chunks when providing information.\n"
            "Keep your response concise and focused on the task."
        )

        messages = [
            {"role": "user", "content": f"Task: {task}\n\nContext: {context}" if context else f"Task: {task}"},
        ]

        try:
            result = await self.provider.chat(
                messages=messages,
                tools=DOCUMENT_TOOLS,
                tool_executor=self.execute_tool,
                system=system_prompt,
                max_tokens=2048,
            )
            self._emit_status(f"✅ Subagent {agent_id} complete")
            return {
                "agent_id": agent_id,
                "task": task,
                "result": result,
                "success": True,
            }
        except Exception as e:
            self._emit_status(f"❌ Subagent {agent_id} failed: {e}")
            return {
                "agent_id": agent_id,
                "task": task,
                "result": f"Error: {str(e)}",
                "success": False,
            }

    def _profile_steering_summary(self, doc_id: str | None = None) -> str:
        if not self.progress_manager:
            return ""
        try:
            source_hash = self._resolve_source_hash(doc_id)
            prefs = self.progress_manager.get_preferences("default") or {}
            events = self.progress_manager.list_events(profile_id="default", limit=200)
            snapshot = self.progress_manager.get_retention_snapshot(source_hash=source_hash, doc_id=doc_id, profile_id="default")
            profile = compute_profile(preferences=prefs, events=events, progress_snapshot=snapshot)
            return steering_summary(profile)
        except Exception:
            return ""

    async def _study_tool(self, name: str, args: dict) -> dict:
        """Handle study tools by prompting the LLM with specialized instructions."""
        doc_id = args.get("doc_id")
        steering = self._profile_steering_summary(doc_id)
        if steering:
            steering = f"\n\n{steering}\n"

        mode = str(args.get("mode", "basic") or "basic").strip().lower()
        focus_topics = args.get("focus_topics")
        focus_mode = str(args.get("focus_mode", "new_material") or "new_material").strip().lower()
        include_source_refs = bool(args.get("include_source_refs", False))

        flashcard_prompt = (
            f"Generate {args.get('count', 5)} study flashcards about '{args.get('topic', 'the document')}'. "
            f"First, use search_chunks to find relevant content, then create flashcards.{steering}\n"
        )
        if focus_topics:
            flashcard_prompt += f"Emphasize these subtopics: {', '.join(str(t) for t in focus_topics)}.\n"
        if focus_mode != "new_material":
            flashcard_prompt += f"Focus mode: {focus_mode}. Tailor cards accordingly.\n"
        if include_source_refs:
            flashcard_prompt += "Include source references (doc_id/page/chunk_id) for each card when possible.\n"

        if mode == "basic":
            flashcard_prompt += (
                "Return the deck in this exact format so the host app can enter flashcard mode:\n"
                "[FLASHCARDS]\n"
                "Q: [question]\n"
                "A: [answer]\n\n"
                "Q: [question]\n"
                "A: [answer]\n"
                "[/FLASHCARDS]\n"
                "You may include at most one short intro line before [FLASHCARDS] and one short follow-up line after [/FLASHCARDS].\n"
                "Inside the [FLASHCARDS] block, use only repeated Q:/A: pairs. No bullets, no numbering, no markdown emphasis, and no extra commentary."
            )
        else:
            flashcard_prompt += (
                "Return the deck as a JSON array of card objects. Each object must have:\n"
                '- "question": string\n'
                '- "answer": string\n'
                '- "card_type": "basic" or "cloze"\n'
                '- "cloze_text": string (only for cloze cards, with {{c1::...}} style clozes)\n'
                '- "source_refs": array of {doc_id, page, chunk_id} (optional)\n'
                '- "tags": array of strings (optional)\n'
                '- "focus": "new_material" | "weak_area" | "exam_cram" | "review" (optional)\n'
                '- "difficulty": "easy" | "medium" | "hard" (optional)\n'
                "No markdown, no prose outside the JSON."
            )

        prompts = {
            "generate_flashcards": flashcard_prompt,
            "generate_quiz": (
                f"Generate {args.get('count', 5)} {args.get('difficulty', 'medium')}-difficulty quiz questions "
                f"about '{args.get('topic', 'the document')}'. "
                f"First, use search_chunks to find relevant content.{steering}\n"
                "You MUST output ONLY a valid JSON array. No other text, no markdown, no explanation outside the JSON.\n"
                "Each element is a question object with these fields:\n"
                '- "type": either "mcq", "short", or "numeric"\n'
                '- "question": the question text\n'
                '- "options": (MCQ only) array of 4 strings like ["a) option", "b) option", "c) option", "d) option"]\n'
                '- "answer": for MCQ the letter like "b", for short a brief correct answer, for numeric a grounded numeric answer\n'
                '- "explanation": 1-2 sentence explanation of why\n'
                'Use "numeric" only if the source clearly supports a quantitative question with a grounded numeric answer. '
                'Otherwise use another "short" question instead.\n'
                f"Generate exactly {args.get('count', 5)} questions. Mix question types when the source supports it.\n"
                "Output ONLY the JSON array. Start with [ and end with ]."
            ),
            "summarize_document": (
                f"Create a comprehensive summary"
                + (f" of document '{args['doc_id']}'" if args.get("doc_id") else " of all loaded documents")
                + (f", focusing on: {args['section']}" if args.get("section") else "")
                + ". First use list_documents and get_document_outline to understand the structure, "
                "then search for key content. Produce a well-organized summary with key points."
            ),
        }

        prompt = prompts.get(name, f"Execute study tool: {name}")

        messages = [
            {"role": "user", "content": prompt},
        ]

        result = await self.provider.chat(
            messages=messages,
            tools=DOCUMENT_TOOLS,
            tool_executor=self.execute_tool,
            system="You are a study assistant. Help the student with their studies.",
        )

        return {"tool": name, "result": result}

    def _get_recent_flashcards(self, args: dict) -> dict:
        cards = list(self.flashcards_ref or [])
        if not cards:
            return {
                "error": "No recent flashcards are available in this session yet. Generate flashcards first.",
                "count": 0,
            }
        limit = self._clamp_int(args.get("limit", 20), default=20, minimum=1, maximum=200)
        return {
            "count": len(cards[:limit]),
            "total_count": len(cards),
            "cards": cards[:limit],
        }

    # ── Autoloader ─────────────────────────────────────────────────

    def _documents_root(self) -> Path | None:
        if not self.documents_dir:
            return None
        try:
            return Path(self.documents_dir).expanduser().resolve()
        except Exception:
            return None

    def _resolve_documents_file(self, relative_path: str) -> tuple[Path | None, str | None]:
        docs_root = self._documents_root()
        if not docs_root:
            return None, "No documents folder configured. Use /docdir <path> to set one."
        if not docs_root.exists():
            return None, "Configured documents folder was not found."

        requested = (relative_path or "").strip().strip('"').strip("'")
        if not requested:
            return None, "No file path provided."
        if requested.startswith('\\'):
            return None, "UNC paths are not allowed for model-triggered file loads."

        candidate = Path(requested)
        if candidate.is_absolute():
            return None, "Use the relative_path returned by list_available_files, not an absolute path."

        try:
            resolved = (docs_root / candidate).resolve(strict=False)
        except Exception as exc:
            return None, f"Invalid file path: {exc}"

        try:
            resolved.relative_to(docs_root)
        except ValueError:
            return None, "Requested path escapes the configured documents folder."

        if not resolved.exists():
            return None, f"File not found in documents folder: {requested}"

        if resolved.suffix.lower() not in self._LOADABLE:
            return None, f"Unsupported file type: {resolved.suffix}. Supported: {list(self._LOADABLE)}"
        return resolved, None

    def _list_available_files(self, filter_kw: str | None = None) -> list[dict]:
        """Scan documents_dir recursively for loadable files."""
        docs_path = self._documents_root()
        if not docs_path:
            return {"error": "No documents folder configured. Use /docdir <path> to set one."}
        if not docs_path.exists():
            return {"error": "Configured documents folder was not found."}

        files = []
        for p in sorted(docs_path.rglob("*")):
            if not p.is_file():
                continue
            if p.suffix.lower() not in self._LOADABLE:
                continue
            if filter_kw and filter_kw.lower() not in p.name.lower():
                continue

            stat = p.stat()
            size_kb = stat.st_size / 1024
            modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            rel = str(p.relative_to(docs_path))

            files.append({
                "name": p.name,
                "relative_path": rel,
                "size": f"{size_kb:.0f} KB" if size_kb < 1024 else f"{size_kb/1024:.1f} MB",
                "modified": modified,
                "file_type": p.suffix.lower(),
            })

        if not files:
            msg = "No loadable files found in the configured documents folder"
            if filter_kw:
                msg += f" matching '{filter_kw}'"
            return {"info": msg, "supported": list(self._LOADABLE)}

        return {"files": files, "count": len(files)}

    async def _autoload_file(self, file_path: str) -> dict:
        """Load a file via the app's callback."""
        if not self.file_loader:
            return {"error": "File loading not available."}

        resolved_path, error = self._resolve_documents_file(file_path)
        if error:
            return {"error": error}
        if not resolved_path:
            return {"error": "Unable to resolve requested file."}

        try:
            relative_path = str(resolved_path.relative_to(self._documents_root()))
            self._emit_status(f"📄 Loading {resolved_path.name}...")
            await self.file_loader(str(resolved_path))
            return {"success": True, "loaded": resolved_path.name, "relative_path": relative_path}
        except Exception as e:
            return {"error": f"Failed to load {resolved_path.name}: {e}"}

    def _resolve_source_hash(self, doc_id: str | None) -> str | None:
        if self.source_hash_resolver:
            try:
                resolved = self.source_hash_resolver(doc_id)
                if resolved:
                    return resolved
            except Exception:
                pass
        if self.progress_manager and doc_id:
            return self.progress_manager.source_hash_for_doc(doc_id)
        return None

    def _get_study_progress(self, doc_id: str | None) -> dict:
        if not self.progress_manager:
            return {"error": "Study progress persistence is not available."}
        source_hash = self._resolve_source_hash(doc_id)
        return self.progress_manager.get_progress(source_hash=source_hash, doc_id=doc_id)

    def _save_progress_note(
        self,
        *,
        doc_id: str | None,
        note: str,
        weak_topics: list[str] | None,
        strong_topics: list[str] | None,
        grasp_level: Any,
    ) -> dict:
        if not self.progress_manager:
            return {"error": "Study progress persistence is not available."}
        source_hash = self._resolve_source_hash(doc_id)
        if not source_hash:
            return {"error": "No linked document hash is available for that study progress note."}
        documents = self.doc_store.list_documents()
        title = next((doc["title"] for doc in documents if doc.get("doc_id") == doc_id), None)
        if not title and len(documents) == 1:
            title = documents[0]["title"]
        return self.progress_manager.record_progress_note(
            source_hash=source_hash,
            doc_id=doc_id,
            title=title or "Study Progress",
            note=note,
            weak_topics=weak_topics,
            strong_topics=strong_topics,
            grasp_level=float(grasp_level) if grasp_level is not None else None,
        )

    def _get_review_queue(self, doc_id: str | None, count: Any) -> dict:
        if not self.progress_manager:
            return {"error": "Study progress persistence is not available."}
        source_hash = self._resolve_source_hash(doc_id)
        return self.progress_manager.get_review_queue(
            source_hash=source_hash,
            doc_id=doc_id,
            limit=self._clamp_int(count, default=20, minimum=1, maximum=50),
        )

    def _get_study_preferences(self) -> dict:
        if not self.progress_manager:
            return {"error": "Study progress persistence is not available."}
        prefs = self.progress_manager.get_preferences("default")
        if not prefs:
            return {"info": "No study preferences saved yet."}
        return prefs

    def _save_study_preferences(self, args: dict) -> dict:
        if not self.progress_manager:
            return {"error": "Study progress persistence is not available."}
        payload = {k: v for k, v in args.items() if v is not None}
        self.progress_manager.save_preferences("default", payload)
        return {"status": "saved", "preferences": payload}

    def _get_retention_snapshot(self, doc_id: str | None) -> dict:
        if not self.progress_manager:
            return {"error": "Study progress persistence is not available."}
        source_hash = self._resolve_source_hash(doc_id)
        return self.progress_manager.get_retention_snapshot(source_hash=source_hash, doc_id=doc_id, profile_id="default")

    async def _sync_recent_to_anki(self, args: dict) -> dict:
        try:
            from src.anki_client import AnkiClient
        except Exception as e:
            return {"error": f"Anki client unavailable: {e}"}
        if not self.progress_manager:
            return {"error": "Study progress persistence is not available."}
        client = AnkiClient()
        if not client.is_available():
            return {"error": "AnkiConnect is not available. Make sure Anki is running with the AnkiConnect add-on."}
        deck_name = str(args.get("deck_name", "Study TUI")).strip()
        note_type = str(args.get("note_type", "basic") or "basic").strip().lower()
        tags = [str(t).strip() for t in (args.get("tags") or []) if str(t).strip()]
        limit = self._clamp_int(args.get("limit", 50), default=50, minimum=1, maximum=200)
        # Get recent flashcards from session ref if available
        cards = list(self.flashcards_ref or [])
        if not cards:
            return {"error": "No recent flashcards available to sync. Generate flashcards first."}
        normalized = normalize_cards(cards[:limit])
        try:
            result = client.create_deck(deck_name)
            if "error" in result:
                return result
        except Exception as e:
            return {"error": f"Failed to create Anki deck: {e}"}
        sync_result = client.add_or_update_notes(
            cards=normalized,
            deck_name=deck_name,
            note_type=note_type,
            tags=tags,
        )
        # Persist sync state
        source_hash = None
        try:
            source_hash = self._resolve_source_hash(None)
        except Exception:
            pass
        if source_hash:
            for card in normalized:
                ckey = card.get("card_key")
                phash = card.get("payload_hash")
                if ckey:
                    self.progress_manager.upsert_anki_sync_state(
                        source_hash=source_hash,
                        card_key=ckey,
                        anki_note_id=None,
                        deck_name=deck_name,
                        note_type=note_type,
                        payload_hash=phash,
                    )
        return sync_result

    def _link_saved_note(
        self,
        *,
        note_id: int,
        title: str,
        doc_id: str | None,
        page: int | None,
        tags: list[str] | None,
    ) -> None:
        if not self.progress_manager or not doc_id:
            return
        source_hash = self._resolve_source_hash(doc_id)
        if not source_hash:
            return
        try:
            self.progress_manager.link_note(
                source_hash=source_hash,
                note_id=note_id,
                doc_id=doc_id,
                title=title,
                page=page,
                tags=tags,
            )
        except Exception:
            return

    async def _handle_animate(self, args: dict[str, Any]) -> dict[str, Any]:
        topic = str(args.get("topic", "")).strip()
        code = str(args.get("code", "")).strip()
        backend = str(args.get("backend", "manim") or "manim").strip().lower().replace("-", "_")
        quality = str(args.get("quality", "high") or "high").strip().lower()
        attempt = self._clamp_int(args.get("attempt", 1), default=1, minimum=1, maximum=3)

        if not topic:
            return {
                "status": "error",
                "retryable": False,
                "attempt": attempt,
                "error": "Animation topic is required.",
            }
        if not code:
            return {
                "status": "error",
                "retryable": False,
                "attempt": attempt,
                "error": "Animation code is required.",
            }
        if backend not in {"manim", "motion_canvas"}:
            backend = "manim"
        if quality not in {"low", "medium", "high"}:
            quality = "high"

        if backend == "motion_canvas":
            dependency_error = get_motion_canvas_dependency_error()
        else:
            dependency_error = get_animation_dependency_error()
        if dependency_error:
            return {
                "status": "error",
                "retryable": False,
                "attempt": attempt,
                "topic": topic,
                "backend": backend,
                "error": dependency_error,
            }

        if backend == "motion_canvas":
            result = await render_motion_canvas_animation(
                code,
                export_dir=self.default_export_dir,
                quality=quality,
            )
        else:
            result = await render_animation(
                code,
                export_dir=self.default_export_dir,
                quality=quality,
            )
        return self._format_animation_result(
            topic=topic,
            attempt=attempt,
            quality=quality,
            backend=backend,
            result=result,
        )

    def _format_animation_result(
        self,
        *,
        topic: str,
        attempt: int,
        quality: str,
        backend: str,
        result: RenderResult,
    ) -> dict[str, Any]:
        if result.success:
            return {
                "status": "success",
                "topic": topic,
                "attempt": attempt,
                "retryable": False,
                "quality": quality,
                "backend": backend,
                "scene_name": result.scene_name,
                "duration_seconds": round(float(result.duration_seconds), 2),
                "video_path": result.video_path,
                "code_path": result.code_path,
            }

        preview = (result.stderr or "").strip()
        if len(preview) > 600:
            preview = preview[:597] + "..."
        retry_guidance = self._animation_retry_guidance(
            backend=backend,
            error=result.error or "",
            stderr=result.stderr or "",
        )
        return {
            "status": "error",
            "topic": topic,
            "attempt": attempt,
            "retryable": attempt < 3,
            "quality": quality,
            "backend": backend,
            "scene_name": result.scene_name,
            "duration_seconds": round(float(result.duration_seconds), 2),
            "error": result.error or "Animation render failed.",
            "stderr_preview": preview or None,
            "code_path": result.code_path,
            "retry_guidance": retry_guidance,
        }

    def _animation_retry_guidance(self, *, backend: str, error: str, stderr: str) -> str | None:
        combined = "\n".join(part for part in (error, stderr) if part).strip()
        if not combined:
            return None
        if backend == "motion_canvas":
            missing_export = re.search(r"does not provide an export named ['\"]([^'\"]+)['\"]", combined, flags=re.IGNORECASE)
            if missing_export:
                symbol = missing_export.group(1)
                known_sources = {
                    "Vector2": "@motion-canvas/core",
                    "all": "@motion-canvas/core",
                    "chain": "@motion-canvas/core",
                    "createRef": "@motion-canvas/core",
                    "createSignal": "@motion-canvas/core",
                    "easeInOutCubic": "@motion-canvas/core",
                    "waitFor": "@motion-canvas/core",
                    "Circle": "@motion-canvas/2d",
                    "Layout": "@motion-canvas/2d",
                    "Line": "@motion-canvas/2d",
                    "makeScene2D": "@motion-canvas/2d",
                    "Node": "@motion-canvas/2d",
                    "Rect": "@motion-canvas/2d",
                    "Txt": "@motion-canvas/2d",
                }
                expected = known_sources.get(symbol)
                if expected:
                    return f"Fix the import source for {symbol}: import it from {expected}, then retry with the supported scaffold."
                return f"Fix the import source for {symbol} before retrying the Motion Canvas scene."
            if "failed to resolve import" in combined.lower():
                return "Remove or replace the unresolved import, and stay close to the supported Motion Canvas scaffold before retrying."
            if "booting" in combined.lower():
                return "The scene likely failed during module load. Recheck imports and keep the scene close to the supported Motion Canvas scaffold before retrying."
        if backend == "manim" and "latex" in combined.lower():
            return "Use plain Text for ordinary prose, keep MathTex/Tex only for true formulas, and escape TeX-sensitive characters before retrying."
        return None

    def _handle_export(self, args: dict) -> dict:
        """Route export_content tool calls to the appropriate exporter."""
        export_type = args.get("type", "")
        fmt = args.get("format", "markdown")
        destination = str(args.get("destination", "default_exports"))
        if destination in {"calibre", "zotero"} and export_type != "notes_pdf":
            return {"error": "Direct Calibre/Zotero delivery currently supports notes_pdf exports only."}
        export_dir = self.documents_dir if destination == "documents_dir" and self.documents_dir else args.get("export_dir")
        if not export_dir and destination in {"default_exports", "calibre", "zotero"} and self.default_export_dir:
            export_dir = self.default_export_dir

        if export_type == "flashcards":
            cards = args.get("cards") or self.flashcards_ref or []
            if not cards:
                return {"error": "No flashcards available to export. Generate flashcards first or pass cards as an array of {question, answer}."}
            return export_flashcards(
                cards,
                fmt=fmt,
                export_dir=export_dir,
                deck_name=args.get("deck_name"),
                note_type=str(args.get("note_type", "basic") or "basic").strip().lower(),
                tags=args.get("tags"),
                include_source_refs=bool(args.get("include_source_refs", False)),
            )

        elif export_type == "notes":
            return self.notes_manager.export_notes_markdown(path=export_dir)

        elif export_type == "notes_pdf":
            result = self.notes_manager.export_notes_pdf(
                path=export_dir,
                note_id=args.get("note_id"),
            )
            return self._deliver_exported_pdf(result, args, destination)

        elif export_type == "summary":
            content = args.get("content", "")
            if not content:
                return {"error": "No content provided for summary export."}
            return export_summary(content, export_dir=export_dir)

        elif export_type == "chat":
            msgs = self.chat_history_ref or []
            if not msgs:
                return {"error": "No chat history to export."}
            return export_chat(msgs, export_dir=export_dir)

        return {"error": f"Unknown export type: {export_type}. Use: flashcards, notes, notes_pdf, summary, chat"}

    def _deliver_exported_pdf(self, result: dict, args: dict, destination: str) -> dict:
        if "error" in result or destination not in {"calibre", "zotero"}:
            return result
        exported = result.get("exported")
        if not exported:
            return result
        pdf_path = Path(str(exported))
        if destination == "calibre":
            book_id = self._clamp_int(args.get("calibre_book_id"), default=0, minimum=0, maximum=10**9)
            if book_id <= 0:
                return {"error": "destination=calibre requires calibre_book_id for the target book.", "exported": str(pdf_path)}
            library = self._resolve_calibre_library()
            if not library:
                return {"error": "Calibre library not found. Configure the Calibre library path first.", "exported": str(pdf_path)}
            delivery = calibre_client.attach_exported_pdf(library, book_id, pdf_path)
            if "error" in delivery:
                delivery["exported"] = str(pdf_path)
                return delivery
            return {**result, "delivery": "calibre", "calibre": delivery}
        zotero_item_key = str(args.get("zotero_item_key", "")).strip().upper()
        if not self._ZOTERO_ITEM_KEY_RE.fullmatch(zotero_item_key):
            return {"error": "destination=zotero requires a valid zotero_item_key.", "exported": str(pdf_path)}
        delivery = zotero_client.attach_exported_pdf(zotero_item_key, pdf_path)
        if "error" in delivery:
            delivery["exported"] = str(pdf_path)
            return delivery
        return {**result, "delivery": "zotero", "zotero": delivery}


    async def _ensure_tool_approval(self, name: str, args: dict) -> bool:
        if name not in self._APPROVAL_REQUIRED_TOOLS:
            return True
        if not self.request_tool_approval:
            return False
        return await self.request_tool_approval(name, args)

    def _tool_status_message(self, name: str, args: dict) -> str:
        if name == "save_note":
            title = self._truncate(str(args.get("title", "Untitled note")), 40)
            return f"📝 Requesting approval to save note \"{title}\"..."

        if name == "export_content":
            export_type = str(args.get("type", "content"))
            fmt = str(args.get("format", "markdown"))
            details = []
            cards = args.get("cards")
            if isinstance(cards, list):
                details.append(f"{len(cards)} cards")
            content = args.get("content")
            if isinstance(content, str) and content.strip():
                details.append(f"{len(content)} chars")
            destination = str(args.get("destination", "default_exports"))
            if destination == "documents_dir":
                details.append("documents_dir")
            elif self.default_export_dir:
                details.append(str(self.default_export_dir))
            suffix = f" ({', '.join(details)})" if details else ""
            return f"💾 Requesting approval to export {export_type} as {fmt}{suffix}..."

        if name == "animate_concept":
            topic = self._truncate(str(args.get("topic", "concept")), 48)
            attempt = self._clamp_int(args.get("attempt", 1), default=1, minimum=1, maximum=3)
            quality = str(args.get("quality", "high") or "high").strip().lower()
            if attempt <= 1:
                return f"🎬 Rendering animation for \"{topic}\" ({quality}, attempt 1/3)..."
            return f"⚠️ Render failed, retrying animation for \"{topic}\" ({quality}, attempt {attempt}/3)..."

        labels = {
            "search_chunks": lambda a: f"Searching documents for \"{a.get('query', '')}\"...",
            "get_chunk_by_id": lambda a: f"Reading chunk {a.get('chunk_id', '')}...",
            "get_chunks_by_page": lambda a: f"Reading page {a.get('page_number', '')} of {a.get('doc_id', '')}...",
            "list_documents": lambda a: "Listing loaded documents...",
            "get_document_outline": lambda a: f"Getting outline of {a.get('doc_id', '')}...",
            "spawn_subagent": lambda a: f"Spawning sub-agent: {self._truncate(str(a.get('task', '')), 50)}...",
            "generate_flashcards": lambda a: f"Creating {a.get('count', '')} flashcards on \"{a.get('topic', '')}\"...",
            "generate_quiz": lambda a: f"Generating {a.get('difficulty', '')} quiz on \"{a.get('topic', '')}\"...",
            "summarize_document": lambda a: f"Summarizing {a.get('doc_id', a.get('section', 'document'))}...",
            "get_recent_flashcards": lambda a: "Loading the latest generated flashcards...",
            "list_available_files": lambda a: "Browsing available files...",
            "load_file": lambda a: f"Loading {Path(str(a.get('file_path', a.get('relative_path', '')))).name or a.get('file_path', a.get('relative_path', ''))}...",
            "web_search": lambda a: f"Searching the web for \"{a.get('query', '')}\"...",
            "list_notes": lambda a: "Listing saved notes...",
            "search_notes": lambda a: f"Searching notes for \"{a.get('query', '')}\"...",
            "get_study_progress": lambda a: "Loading stored study progress...",
            "save_progress_note": lambda a: "Updating long-term study progress...",
            "get_review_queue": lambda a: "Loading personalized review queue...",
            "get_retention_snapshot": lambda a: "Loading retention snapshot...",
            "get_study_preferences": lambda a: "Loading study preferences...",
            "save_study_preferences": lambda a: "Saving study preferences...",
            "anki_sync_recent": lambda a: f"Syncing recent cards to Anki deck '{a.get('deck_name', '')}'...",
            "pomodoro_start": lambda a: f"Starting {a.get('work_mins', 25)}-minute focus session...",
            "pomodoro_status": lambda a: "Checking timer status...",
            "pomodoro_stop": lambda a: "Stopping timer...",
            "calibre_search": lambda a: f"Searching Calibre for \"{a.get('query', '')}\"...",
            "calibre_load": lambda a: f"Loading book #{a.get('book_id', '')} from Calibre...",
            "zotero_search": lambda a: f"Searching Zotero for \"{a.get('query', '')}\"...",
            "zotero_load": lambda a: f"Loading Zotero item {a.get('item_key', '')}...",
            "zotero_collections": lambda a: "Listing Zotero collections...",
        }
        fn = labels.get(name)
        if fn:
            return fn(args)

        arg_preview = ", ".join(f"{k}={v!r}" for k, v in args.items())
        return f"Running {name}({arg_preview})" if arg_preview else f"Running {name}..."

    @staticmethod
    def _truncate(value: str, limit: int) -> str:
        return value if len(value) <= limit else value[: limit - 3] + "..."

    def _emit_status(self, msg: str) -> None:
        if self.on_status:
            self.on_status(msg)

    @staticmethod
    def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(parsed, maximum))

    # ── Calibre helpers ────────────────────────────────────────────────────

    def _resolve_calibre_library(self) -> Path | None:
        """Return configured calibre library path, or auto-detect."""
        if self.calibre_library:
            p = Path(self.calibre_library)
            if (p / "metadata.db").exists():
                return p
        return calibre_client.find_calibre_library()

    def _calibre_search(self, query: str) -> dict:
        lib = self._resolve_calibre_library()
        if not lib:
            return {
                "error": (
                    "Calibre library not found. Configure it with /calibre-dir <path> "
                    "or set the CALIBRE_LIBRARY environment variable."
                )
            }
        try:
            query = (query or "").strip()[:200]
            results = calibre_client.search_books(lib, query)
            if not results:
                return {"info": f"No PDF books found in Calibre matching '{query}'."}
            return {"books": results, "count": len(results)}
        except Exception as e:
            return {"error": f"Calibre search failed: {e}"}

    async def _calibre_load(self, book_id: int) -> dict:
        if int(book_id) <= 0:
            return {"error": "Calibre book ID must be a positive integer."}
        lib = self._resolve_calibre_library()
        if not lib:
            return {"error": "Calibre library not found."}
        try:
            pdf_path = calibre_client.get_pdf_path(lib, int(book_id))
        except Exception as e:
            return {"error": f"Calibre lookup failed: {e}"}
        if not pdf_path:
            return {"error": f"No PDF found for Calibre book ID {book_id}."}
        if not self.file_loader:
            return {"error": "File loading not available."}
        self._emit_status(f"📚 Loading {pdf_path.name} from Calibre...")
        try:
            await self.file_loader(str(pdf_path))
            return {"success": True, "loaded": pdf_path.name, "source": "calibre"}
        except Exception as e:
            return {"error": f"Failed to load {pdf_path.name}: {e}"}

    # ── Zotero helpers ─────────────────────────────────────────────────────

    def _zotero_search(self, query: str, tag: str | None = None, collection: str | None = None) -> dict:
        if not zotero_client.is_available():
            return {
                "error": (
                    "Zotero is not running or its local API is not enabled. "
                    "Open Zotero → Settings → Advanced → "
                    "check 'Allow other applications to communicate with Zotero'."
                )
            }
        try:
            results = zotero_client.search_items(
                query=(query or "").strip()[:200],
                tag=(tag or "").strip()[:100] or None,
                collection=(collection or "").strip()[:120] or None,
            )
            if not results:
                return {"info": f"No items found in Zotero matching '{query}'."}
            return {"items": results, "count": len(results)}
        except Exception as e:
            return {"error": f"Zotero search failed: {e}"}

    async def _zotero_load(self, item_key: str) -> dict:
        if not zotero_client.is_available():
            return {"error": "Zotero is not running."}
        normalized_key = (item_key or "").strip().upper()
        if not self._ZOTERO_ITEM_KEY_RE.fullmatch(normalized_key):
            return {"error": "Invalid Zotero item key."}
        try:
            pdf_path = zotero_client.get_pdf_path(normalized_key)
        except Exception as e:
            return {"error": f"Zotero lookup failed: {e}"}
        if not pdf_path:
            return {
                "error": (
                    f"No PDF attachment found for Zotero item {item_key}. "
                    "Make sure the item has an attached PDF in Zotero."
                )
            }
        if not self.file_loader:
            return {"error": "File loading not available."}
        self._emit_status(f"📖 Loading {pdf_path.name} from Zotero...")
        try:
            await self.file_loader(str(pdf_path))
            return {"success": True, "loaded": pdf_path.name, "source": "zotero"}
        except Exception as e:
            return {"error": f"Failed to load {pdf_path.name}: {e}"}
