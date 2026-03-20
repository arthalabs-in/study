# Release Checklist

Use this before tagging a public release. The goal is simple: prove the advertised features work from source, from an installed package, and with the providers you claim to support.

## 1. Fast Automated Gate

Run these first:

```powershell
python -m pytest -q
python -m compileall src tests scripts
python -m src --help
```

Expected result:
- tests are green
- no syntax failures
- CLI help shows `--file` and `--setup`

## 2. Source UX Pass

Open a clean terminal session with isolated app state:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/manual_terminal_smoke.ps1
```

Optional seeded document:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/manual_terminal_smoke.ps1 -FilePath "C:\path\to\sample.pdf"
```

## 3. Installed Package UX Pass

Build first:

```powershell
python -m build --sdist --wheel --no-isolation
```

Then validate install paths:

```powershell
python scripts/package_smoke.py --wheel "dist/*.whl" --method uv-run --method uv-install
```

If you also want `pipx` coverage:

```powershell
python scripts/package_smoke.py --wheel "dist/*.whl" --method pipx-install
```

Then run a real installed-session smoke pass:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/manual_terminal_smoke.ps1 -UseInstalledCommand
```

## 4. Feature Checklist

Mark each item pass or fail.

### Startup and Setup

- `study --setup` or `python -m src --setup` saves provider, model, documents directory, theme, and web default.
- First launch shows welcome/help text clearly.
- Input is focused immediately after startup.
- Startup with `--file` loads the file automatically.

### Provider and Model UX

- `/provider` opens a nested picker.
- Provider picker works with arrow keys, `Tab`, and `Enter`.
- `/model` opens a nested picker for the current provider.
- Changing the model updates the connected label immediately.
- Missing-key and missing-auth states show clear guidance instead of crashing.

### Document Loading and Navigation

- `/load` opens the Windows picker.
- `/load <path>` loads a real PDF.
- `/docs` lists loaded documents.
- `/page 1` works.
- `/docdir` shows the current documents directory.
- `/docdir <path>` updates the documents directory.

### Core Study Features

- Ask one normal question and get a useful answer.
- `/summary` completes a happy-path run.
- `/flashcards` completes a happy-path run.
- `/quiz` starts, accepts answers, and shows results.
- If the loaded document has figures, image/page explanation flow works.

### Notes and Export

- Ask the assistant to save a note and confirm it pauses for approval.
- `/deny` cancels the pending write.
- Ask again and use `/approve`; confirm the note is actually saved.
- Ask the assistant to export summary, chat, or flashcards and confirm approval appears.
- `/approve` writes the export successfully.

### Session UX

- `/new` starts a fresh session.
- `/history` opens a nested picker.
- `/resume` opens a nested picker.
- `/continue` resumes the latest previous session when one exists.

### UI and Interaction

- `/theme` opens a nested picker and updates shell chrome.
- `/web` opens a nested picker and toggles state correctly.
- `Esc` cancels generation.
- `Ctrl+L` clears the visible log only.
- `/clear` resets the active chat history.
- `Shift + mouse drag` still allows selection/copy in the terminal.
- `Ctrl+C` exits cleanly.

## 5. Provider Matrix

Only claim a provider is working if it passes one live round-trip.

### Remote API Providers

- `openai`
- `anthropic`
- `gemini`
- `kimi`

### Local/OpenAI-Compatible Providers

- `ollama`
- `llamacpp`
- `lmstudio`

### Codex OAuth Path

- `openai-codex`

Live smoke test:

```powershell
$env:RUN_LIVE_PROVIDER_TESTS="1"
python -m pytest tests/test_provider_live.py -m live_provider -q -rs
```

Set the relevant provider env vars before running it.

## 6. Release Bar

Do not tag a release unless all of these are true:

- automated gate is green
- source UX pass is green
- installed-package UX pass is green
- every advertised provider has passed a live round-trip
- at least one full end-to-end study flow has passed:
  - setup
  - load doc
  - ask question
  - summary
  - flashcards
  - quiz
  - save/export with approval
  - resume previous session

## 7. Notes

- The automated suite is strong on logic and headless UX, but it does not replace a real terminal-window pass.
- Live provider tests are intentionally opt-in because they require real credentials or running local backends.
- If a feature is advertised in the README, it should be represented in this checklist.
