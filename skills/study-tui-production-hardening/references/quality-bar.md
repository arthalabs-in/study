# Study TUI Quality Bar

## Goal

Ship only when the repo has both:

- a working CI pipeline that reproduces the local verification path
- a hostile test suite that attacks the riskiest runtime behavior, not just happy paths

## CI bar

- Use GitHub Actions.
- Trigger CI on `push` and `pull_request`.
- Run on `ubuntu-latest` and `windows-latest`.
- Test the minimum supported Python version from `pyproject.toml` and at least one newer supported version.
- Install project dependencies plus explicit test/build tooling in the workflow.
- Run, at minimum:
  - `python -m compileall src`
  - required smoke imports
  - `pytest`
  - package build validation
- Keep the workflow readable. One clear job is better than a maze of composite actions unless the repo already needs the complexity.

## CD and release bar

- Add a release-oriented workflow triggered by tags and/or `workflow_dispatch`.
- Build sdist and wheel artifacts in the release flow.
- Upload artifacts even when publish is not configured yet.
- Publish only when a real secret or trusted-publisher path exists.
- Prefer GitHub environments or PyPI trusted publishing over long-lived repository secrets.
- Do not say "production deploy" if the repo only builds artifacts.

## Hostile test bar

### Core modules that should not stay lightly tested

- `src/app.py`
- `src/agents/agent_manager.py`
- `src/agents/tools.py`
- `src/web_search.py`
- `src/exporter.py`
- `src/chat_history.py`
- `src/notes.py`
- `src/parsers/doc_store.py`
- `src/parsers/pdf_parser.py`
- `src/parsers/image_parser.py`

### Test categories to cover

- Unit tests:
  - note/history/export helpers
  - Pomodoro state transitions
  - doc store indexing and retrieval behavior
  - schema/tool registration invariants
- Integration tests:
  - agent manager tool dispatch
  - provider/tool-call translation
  - session persistence boundaries
  - entrypoint and package smoke paths
- UI and command-surface tests:
  - slash-command availability and help/autocomplete drift
  - approval flow behavior
  - resume/clear/session semantics
- Adversarial and security tests:
  - path traversal, UNC paths, and root escape attempts
  - redirect-based SSRF defenses
  - spreadsheet formula injection neutralization
  - same-stem document collisions
  - malformed or oversized parser inputs
  - privacy-sensitive path leakage into tool outputs
- Packaging tests:
  - wheel and sdist build
  - install/import smoke from a built artifact when practical

## Coverage guidance

- Use coverage as a gate, not as the goal.
- Start with a threshold the real suite can sustain and raise it after critical paths are covered.
- Prefer module-specific confidence over a weak repo-wide number.
- If a module is security-sensitive and hard to cover, add focused regression tests instead of gaming coverage.

## Test design rules

- Default to hermetic tests.
- Avoid live network and real provider SDK calls.
- Avoid heavyweight OCR downloads in CI.
- Use temporary directories for `~/.study-tui` and export paths.
- Mock external processes and platform-specific commands where possible.
- Keep slow parser or UI tests clearly marked so fast CI stays fast.

## Definition of done

- Local hardening gate passes.
- CI mirrors the local gate closely enough that green locally usually means green in GitHub Actions.
- The release workflow produces artifacts successfully.
- Every newly fixed bug has a regression test.
- The repo can fail loudly on real defects instead of silently shipping them.
