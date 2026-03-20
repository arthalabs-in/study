# Manual Terminal Smoke

Use this before a public release when you want a real terminal-window pass instead of only headless Textual tests.

## Launch

Source checkout:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/manual_terminal_smoke.ps1
```

Installed package:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/manual_terminal_smoke.ps1 -UseInstalledCommand
```

Optional seeded file:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/manual_terminal_smoke.ps1 -FilePath "C:\path\to\sample.pdf"
```

The launcher opens a new PowerShell window with an isolated home directory and documents folder so the smoke pass does not pollute your real `~/.study-tui` state.

## Release Checklist

- Startup help text renders and the input box is focused.
- `/provider` opens the nested picker and switching providers works with keyboard only.
- `/model` opens a nested picker and selecting a model updates the connected label.
- `/theme` opens a nested picker and changes shell chrome immediately.
- `/history` and `/resume` open nested pickers and resume the expected session.
- `/web` opens a nested picker and toggles state correctly.
- `/load` or startup `--file` loads a real document and `/docs` shows it.
- A normal question gets a response from the configured provider.
- `/summary`, `/flashcards`, and `/quiz` all complete one happy-path run.
- Approval-gated writes pause correctly; `/approve` writes and `/deny` cancels.
- `Esc` cancels generation cleanly.
- `Ctrl+L` clears the visible log only, and `/clear` resets the active chat.
- `Shift + mouse drag` still allows text selection in the terminal.
- `Ctrl+C` exits cleanly without leaving the terminal in a broken state.

## Notes

- This pass is intentionally manual. It checks things that are difficult to prove with headless tests, especially terminal rendering, keyboard feel, and copy/select behavior.
- Run it once against source and once against an installed package before tagging a release.
