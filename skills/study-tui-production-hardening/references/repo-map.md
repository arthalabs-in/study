# Study TUI Repo Map

## Package and entrypoints

- The Python package name is intentionally `src`. Keep imports as `src.*`.
- Entrypoints:
  - `study`
  - `study-tui`
  - `python -m src`
- `src.app.main()` also accepts a positional file path or `--file=...` to load a document at startup.

## Important modules

- `src/app.py`: main orchestrator, slash commands, provider setup, document loading, session flow, approvals, theme selection.
- `src/widgets/chat.py`: chat rendering, slash-command autocomplete, streaming/thinking UI, quiz state.
- `src/agents/tools.py`: model-exposed tool schemas.
- `src/agents/agent_manager.py`: tool execution for documents, exports, notes, Pomodoro, autoloading, web search, subagents.
- `src/agents/provider.py`: provider registry plus streaming and tool-call abstraction.
- `src/agents/model_client.py`: compatibility wrapper over provider logic.
- `src/parsers/doc_store.py`: in-memory document index and BM25 search.
- `src/parsers/pdf_parser.py`: PDF parsing, chunking, figure-page detection, rendered page images.
- `src/parsers/image_parser.py`: image OCR path.
- `src/notes.py`: SQLite-backed notes storage and export helpers.
- `src/chat_history.py`: SQLite-backed chat/session history.
- `src/exporter.py`: markdown and CSV export helpers.
- `src/web_search.py`: DuckDuckGo-backed web search with URL safety checks.
- `src/pomodoro.py`: timer state and transitions.
- `src/theme.tcss`: shell-level theme chrome.

## Coordination rules

- Keep slash-command changes aligned between `src/app.py` and `src/widgets/chat.py`.
- Keep tool-schema changes aligned between `src/agents/tools.py` and `src/agents/agent_manager.py`.
- If tool changes alter prompts or user instructions, update the relevant prompt/status text in `src/app.py`.
- Check both in-memory chat history handling in `src/app.py` and SQLite persistence in `src/chat_history.py` when changing session semantics.
- Parser or retrieval changes often span `src/parsers/pdf_parser.py`, `src/parsers/image_parser.py`, `src/parsers/doc_store.py`, and the tool layer.

## Runtime paths

- Settings: `~/.study-tui/settings.json`
- Chat history: `~/.study-tui/history.db`
- Notes: `~/.study-tui/notes.db`
- Rendered page images: `~/.study-tui/images/`
- Default exports: `~/Documents/StudyTUI-Exports/`
- Document discovery default: `~/Documents`
- Override document discovery with `STUDY_DOCS_DIR`

## Platform notes

- The native file picker is Windows-specific because it uses PowerShell plus `System.Windows.Forms.OpenFileDialog`.
- Clipboard copy is Windows-specific because it uses `clip.exe`.
- Preserve Windows behavior unless cross-platform support is the explicit task.

## Sharp edges worth testing hard

- `DocStore` is memory-only.
- Document IDs come from lowercase file stems, so same-stem files can collide.
- Startup creates a fresh session before optionally resuming a previous one.
- Some non-streaming flows update `_chat_history` without immediately saving to SQLite.
- `Ctrl+L` clears only the visible log; `/clear` resets the active chat history.
- `/resume` consumes the numbered list shown by `/history`, not a literal session ID typed by the user.
- Theme switching mostly changes shell chrome; chat message colors stay hardcoded in `src/widgets/chat.py`.
- The repo may be mostly or entirely untracked. Inspect `git status` before leaning on history.

## Security-sensitive hotspots

- `src/agents/agent_manager.py` and `src/agents/tools.py` expose local file access to the model.
- `src/web_search.py` is sensitive to SSRF and redirect handling.
- `src/exporter.py` writes spreadsheet-compatible files and must defend against formula injection.
- `src/parsers/pdf_parser.py` and `src/parsers/image_parser.py` need resource-budget tests.
- `src/chat_history.py` and `src/notes.py` hold local persistence behavior.

Read the repo-root security artifacts when touching those paths:
- `security_best_practices_report.md`
- `crimson-voyager-threat-model.md`

## Mandatory fast verification

Run this syntax pass:

```bash
python -m compileall src
```

Use this import smoke set:

```python
import src.app
import src.widgets.chat
import src.agents.provider
import src.agents.agent_manager
import src.parsers.pdf_parser
import src.parsers.image_parser
import src.notes
import src.chat_history
import src.exporter
import src.web_search
```

## Testing stance

- Prefer `pytest`.
- Keep tests isolated from real user directories by overriding home/documents paths.
- Mock OCR, LLM providers, and web traffic.
- Treat `src/security_audit_artifacts/` as read-only fixtures; do not rewrite it.
