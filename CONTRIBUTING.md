# Contributing

Thanks for taking a look at Study TUI.

## Local setup

```bash
uv sync --extra dev
```

Optional extras:

```bash
uv sync --extra anki --extra zotero --extra animation --extra dev
```

## Before opening a PR

Run the same checks we use as the release floor:

```bash
python -m compileall src tests scripts
python -m pytest -q
```

If you touch packaging or release paths, also run:

```bash
python -m build --sdist --wheel
```

## Scope guidance

- Keep changes small and explicit.
- Prefer behavior fixes, test coverage, and UX clarity over large refactors.
- If you change a tool schema, keep `src/agents/tools.py`, `src/agents/agent_manager.py`, and the user-facing prompt/help text in sync.
- If you change slash commands, keep `src/app.py` and `src/widgets/chat.py` in sync.

## Good launch-path PRs

- reliability fixes in the load -> ask -> quiz -> flashcards -> progress flow
- setup / install / provider UX improvements
- export correctness
- demoability and README clarity
- regression tests for real bugs
