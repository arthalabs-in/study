# Study TUI Agent Guide

## Repo Overview
- This repo is a Textual-based study copilot for PDFs, images, notes, and research workflows.
- Current product surface includes:
  - chat over loaded materials
  - interactive quiz mode
  - flashcard generation and persistent review
  - summaries
  - notes and note export
  - chat / summary / flashcard / Anki export
  - optional web search
  - Pomodoro timer
  - Calibre and Zotero integrations
  - document-linked study progress and personalized review
  - debug tracing via `--debug`
- The top-level Python package is intentionally named `src`. Keep imports as `src.*` unless packaging is being redesigned on purpose.
- Entrypoints:
  - `study` -> `src.app:main`
  - `study-tui` -> `src.app:main`
  - `python -m src`
- `src.app.main()` accepts:
  - positional file path
  - `--file=...`
  - `--setup`
  - `--debug`

## Code Map
- `src/app.py`: main orchestration hub. CLI flags, provider setup, slash commands, document loading, generation workers, quiz/review startup, theme selection, privacy/export settings, debug wiring, and session handling.
- `src/widgets/chat.py`: chat rendering, streaming buffers, slash-command autocomplete, nested pickers, flashcard review UI, interactive quiz UI, thinking display, and approval picker UX.
- `src/agents/tools.py`: model-exposed tool schemas. Tool names/descriptions here must match router behavior.
- `src/agents/agent_manager.py`: tool execution router for document access, exports, notes, Pomodoro, web search, study progress, Calibre, Zotero, and subagents.
- `src/agents/provider.py`: provider registry and provider-specific chat / streaming / tool-call behavior, including Gemini/Codex quirks.
- `src/agents/model_client.py`: compatibility wrapper around `provider.py`.
- `src/context_engine.py`: prompt-state assembly, context compaction, pruning, token estimation, and context snapshots.
- `src/study_progress.py`: persistent document-linked study memory, deck storage, review queue state, and progress summaries.
- `src/debug_trace.py`: `--debug` session tracing of provider-facing context, tool calls/results, and responses.
- `src/chat_history.py`: SQLite-backed session history and compact-memory/session metadata persistence.
- `src/notes.py`: SQLite-backed notes storage and export helpers.
- `src/exporter.py`: markdown / CSV / Anki export helpers.
- `src/secure_storage.py`: settings secret storage and cross-platform encryption helpers.
- `src/parsers/doc_store.py`: in-memory document index and BM25 search.
- `src/parsers/pdf_parser.py`: PDF parsing, chunking, figure-page detection, and rendered page images.
- `src/parsers/image_parser.py`: image OCR pipeline. EasyOCR is lazy-loaded.
- `src/calibre_client.py`: local Calibre library integration helpers.
- `src/zotero_client.py`: local Zotero integration helpers.
- `src/zotero_webhook.py`: localhost-only Zotero webhook listener and validation.
- `src/web_search.py`: web search with URL safety checks.
- `src/pomodoro.py`: timer state and status transitions.
- `src/latex_render.py`: LaTeX-to-readable-text rendering helpers used by note/export paths.
- `src/theme.tcss`: shell-level theme chrome.

## Coordination Rules
- Slash-command changes must update both `src/app.py` and the command/help surfaces in `src/widgets/chat.py`.
- Tool additions or schema changes must update both `src/agents/tools.py` and `src/agents/agent_manager.py`.
- If a tool change affects model behavior or user instructions, also update the prompt and status text in `src/app.py`.
- Flashcard / quiz output-format changes usually affect all three:
  - `src/app.py`
  - `src/widgets/chat.py`
  - `src/agents/agent_manager.py`
- Study-progress or review changes usually span:
  - `src/study_progress.py`
  - `src/app.py`
  - `src/widgets/chat.py`
  - `src/agents/tools.py`
  - `src/agents/agent_manager.py`
- Context or token-usage changes should be checked across:
  - `src/context_engine.py`
  - `src/app.py`
  - `src/agents/provider.py`
  - `src/chat_history.py`
- Theme work may require both `src/theme.tcss` and hardcoded color constants in `src/widgets/chat.py`.
- Changes to chat or session semantics must be checked against both in-memory transcript/model-history handling in `src/app.py` and SQLite persistence in `src/chat_history.py`.
- Changes to document ingestion or retrieval often span `src/parsers/pdf_parser.py`, `src/parsers/image_parser.py`, `src/parsers/doc_store.py`, and the tool layer in `src/agents/*`.
- Calibre/Zotero changes must keep the tool layer, routing, local clients, and security assumptions in sync.

## State and Runtime Paths
- Settings live at `~/.study-tui/settings.json`.
- Secrets live at `~/.study-tui/secrets.json`.
- Chat history lives at `~/.study-tui/history.db`.
- Notes live at `~/.study-tui/notes.db`.
- Study progress lives at `~/.study-tui/study_progress.db`.
- Debug traces from `--debug` live at `~/.study-tui/debug/`.
- Rendered page images live under a temp cache, not the repo.
- Default export output goes to `~/Documents/StudyTUI-Exports/`.
- Private export mode writes under `~/.study-tui/exports/`.
- Document discovery defaults to `~/Documents`, but `STUDY_DOCS_DIR` can override the folder used by autoload tools.

## Dependencies
- Declared in `pyproject.toml`:
  - `textual`
  - `pymupdf`
  - `easyocr`
  - `anthropic`
  - `openai`
  - `tiktoken`
  - `cryptography`
  - `Pillow`
  - `rich`
  - `tomli` on Python `<3.11`
- Optional extras:
  - `anki` -> `genanki`
  - `zotero` -> `pyzotero`
  - `dev` -> test/build tooling
- Optional but still relevant runtime deps:
  - `keyring` for secure API key persistence if available
  - `ddgs` or `duckduckgo_search` for web search
  - `fpdf2` for notes PDF export
- EasyOCR is lazy-loaded; first OCR use may be slow or require heavyweight model setup.

## Generated and Fixture Paths
- Do not edit these by default:
  - `src/.ruff_cache/`
  - any `__pycache__/` directory
  - `src/study_tui.egg-info/`
  - bundled PDFs in the repo root and `Documents/`
  - `src.rar`
  - `src/security_audit_artifacts/`
- Treat `src/security_audit_artifacts/` as audit or fixture material. It may intentionally contain sensitive-looking sample files and should not be rewritten casually.
- Local `skills/` content is intentionally ignored and should not be recommitted.

## Platform Notes
- The native file picker is currently Windows-specific because it uses PowerShell plus `System.Windows.Forms.OpenFileDialog`.
- Clipboard copy is Windows-specific because it uses `clip.exe`.
- The Zotero webhook is intended to remain localhost-only and single-user.
- Keep Windows behavior intact unless cross-platform support is the explicit task.

## Sharp Edges
- `src` as the package name is intentional. Do not "normalize" it without checking packaging, entrypoints, and imports.
- `DocStore` remains memory-only. Parsed documents are not persisted across launches.
- Document IDs come from normalized file stems plus a digest; source-linked long-term study memory is keyed by file hash, not just `doc_id`.
- App startup creates a fresh session before optionally resuming a previous one.
- `_chat_history` is the visible transcript; provider-facing prompt state is separately compacted through `src/context_engine.py`.
- `Ctrl+L` clears the visible log only. `/clear` resets the active chat history.
- `/resume` works from the numbered list shown by `/history`, not from a literal session ID typed by the user.
- Theme switching mainly changes shell chrome. Some message colors inside `ChatView` are still hardcoded.
- `--debug` writes sensitive provider-facing context and responses to disk. Do not leave it enabled casually.
- Linux CI packaging smoke is intentionally lighter than Windows because heavyweight OCR deps can exhaust runner disk.

## Verification
- Fast syntax pass:
  - `python -m compileall src tests scripts`
- Full test suite:
  - `python -m pytest -q`
- Targeted packaging smoke helper:
  - `python scripts/package_smoke.py --wheel "dist/*.whl" --method pipx-install`
  - `python scripts/package_smoke.py --wheel "dist/*.whl" --method venv-install`
- Import smoke targets:
  - `src.app`
  - `src.widgets.chat`
  - `src.agents.provider`
  - `src.agents.agent_manager`
  - `src.parsers.pdf_parser`
  - `src.parsers.image_parser`
  - `src.notes`
  - `src.chat_history`
  - `src.exporter`
  - `src.web_search`
- There is a real `tests/` directory now. Do not assume this repo is still testless.
