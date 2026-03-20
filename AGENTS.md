# Study TUI Agent Guide

## Repo Overview
- This repo is a Textual-based study copilot for PDFs and images. It supports chat over loaded documents, quiz mode, flashcards, summaries, notes, exports, optional web search, and a Pomodoro timer.
- The top-level Python package is intentionally named `src`. Keep imports as `src.*` unless packaging is being redesigned on purpose.
- Entrypoints:
- `study` -> `src.app:main`
- `study-tui` -> `src.app:main`
- `python -m src`
- `src.app.main()` also accepts a positional file path or `--file=...` to load a document at startup.

## Code Map
- `src/app.py`: main orchestration hub. Provider setup, slash commands, document loading, generation workers, theme selection, and session handling all live here.
- `src/widgets/chat.py`: chat rendering, streaming buffers, slash-command autocomplete, typing and thinking UI, and interactive quiz state.
- `src/agents/tools.py`: tool schemas exposed to the model.
- `src/agents/agent_manager.py`: tool execution router for document access, exports, notes, Pomodoro, autoloading, web search, and subagents.
- `src/agents/provider.py`: provider registry plus Anthropic-style and OpenAI-compatible streaming/tool-call abstraction.
- `src/agents/model_client.py`: compatibility wrapper around `provider.py`.
- `src/parsers/doc_store.py`: in-memory document index and BM25 search.
- `src/parsers/pdf_parser.py`: PDF parsing, chunking, figure-page detection, and rendered page images.
- `src/parsers/image_parser.py`: image OCR pipeline.
- `src/notes.py`: SQLite-backed notes storage and export helpers.
- `src/chat_history.py`: SQLite-backed session history.
- `src/exporter.py`: markdown and CSV export helpers.
- `src/web_search.py`: DuckDuckGo-backed web search with URL safety checks.
- `src/pomodoro.py`: timer state and status transitions.
- `src/theme.tcss`: shell-level theme chrome for the four built-in themes.

## Coordination Rules
- Slash-command changes must update both `src/app.py` and the command/help surfaces in `src/widgets/chat.py`.
- Tool additions or schema changes must update both `src/agents/tools.py` and `src/agents/agent_manager.py`.
- If a tool change affects model behavior or user instructions, also update the prompt and status text in `src/app.py`.
- Theme work may require both `src/theme.tcss` and the hardcoded color constants in `src/widgets/chat.py`.
- Changes to chat or session semantics must be checked against both in-memory `_chat_history` usage in `src/app.py` and SQLite persistence in `src/chat_history.py`.
- Changes to document ingestion or retrieval often span `src/parsers/pdf_parser.py`, `src/parsers/image_parser.py`, `src/parsers/doc_store.py`, and the tool layer in `src/agents/*`.

## State and Runtime Paths
- Settings live at `~/.study-tui/settings.json`.
- Chat history lives at `~/.study-tui/history.db`.
- Notes live at `~/.study-tui/notes.db`.
- Rendered page images live at `~/.study-tui/images/`.
- Default export output goes to `~/Documents/StudyTUI-Exports/`.
- Document discovery defaults to `~/Documents`, but `STUDY_DOCS_DIR` can override the folder used by autoload tools.

## Dependencies
- Declared in `pyproject.toml`: `textual`, `pymupdf`, `easyocr`, `anthropic`, `openai`, `Pillow`, and `rich`.
- Optional but currently used by code paths:
- `keyring` for secure API key persistence
- `ddgs` or `duckduckgo_search` for web search
- `fpdf2` for notes PDF export
- EasyOCR is lazy-loaded; first use may be slow or require heavyweight model setup.

## Generated and Fixture Paths
- Do not edit these by default:
- `src/.ruff_cache/`
- any `__pycache__/` directory
- both `study_tui.egg-info/` directories
- bundled PDFs in the repo root and `Documents/`
- `src.rar`
- `src/security_audit_artifacts/`
- Treat `src/security_audit_artifacts/` as audit or fixture material. It may intentionally contain sensitive-looking sample files and should not be rewritten casually.

## Platform Notes
- The current native file picker is Windows-specific because it uses PowerShell plus `System.Windows.Forms.OpenFileDialog`.
- Clipboard copy is Windows-specific because it uses `clip.exe`.
- Keep Windows behavior intact unless cross-platform support is the explicit task.

## Sharp Edges
- `src` as the package name is intentional. Do not "normalize" it without checking packaging, entrypoints, and imports.
- `DocStore` is memory-only. Parsed documents are not persisted across launches.
- Document IDs come from lowercase file stems, so two files with the same stem can collide.
- App startup creates a fresh session before optionally resuming a previous one.
- Normal streamed chat persists history, but some non-streaming flows update `_chat_history` without immediately saving to SQLite.
- `Ctrl+L` clears the visible log only. `/clear` resets the active chat history.
- `/resume` works from the numbered list shown by `/history`, not from a literal session ID string typed by the user.
- Theme switching mainly changes shell chrome. Message colors inside `ChatView` are still hardcoded.
- This workspace is a git repo, but the current worktree may be mostly or entirely untracked. Inspect `git status` before relying on history or assuming a clean baseline.

## Verification
- Use `python -m compileall src` for a fast syntax pass.
- Use a Python import smoke test for the main modules:
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
- There is currently no `tests/` directory, so do not assume a test suite exists.
