---
name: study-tui-production-hardening
description: Harden the Study TUI repository with a working GitHub CI/CD pipeline, release gates, and an adversarial production-readiness test suite. Use when Codex needs to add or repair GitHub Actions workflows, introduce or expand pytest coverage, validate packaging and entrypoints, exercise Windows-specific behavior, or aggressively probe the repo for regressions, security issues, and release blockers before shipping.
---

# Study TUI Production Hardening

## Overview

Use this skill to take the repo from "runs locally" to "can survive hostile verification." Build the pipeline, add the tests, and keep iterating until local checks and workflow checks both fail only for real defects.

## References

Read [references/repo-map.md](references/repo-map.md) first for the repo layout, sharp edges, mandatory smoke imports, and Windows constraints.

Read [references/quality-bar.md](references/quality-bar.md) before designing workflows, choosing test categories, or setting coverage thresholds.

Read `security_best_practices_report.md` and `crimson-voyager-threat-model.md` from the repo root when changes touch file loading, web fetching, exports, persistence, or provider/tool trust boundaries.

Run [scripts/hardening_gate.py](scripts/hardening_gate.py) before edits to measure the baseline and again before finishing to verify that the repo now clears the intended gate.

## Workflow

### 1. Baseline the repo honestly

- Inspect `pyproject.toml`, `.github/workflows/`, `tests/`, and the relevant source modules before proposing changes.
- Expect the starting point to be incomplete. Do not treat missing CI or missing tests as surprising.
- Run the gate script from the repo root to confirm what is absent:

```bash
python skills/study-tui-production-hardening/scripts/hardening_gate.py --require-workflows --require-tests
```

### 2. Build the minimum viable CI/CD pipeline

- Prefer GitHub Actions unless the user explicitly asks for another system.
- Add one fast CI workflow for `push` and `pull_request`.
- Run CI on at least `ubuntu-latest` and `windows-latest` because file-picker and clipboard behavior are Windows-specific.
- Test the minimum supported Python version from `pyproject.toml` and at least one current newer version supported by the dependency stack.
- Cache dependencies, but keep the workflow readable.
- Include these gates in CI:
  - dependency install for app plus test/build tooling
  - `python -m compileall src`
  - smoke imports for the required modules
  - `pytest` on the hostile suite
  - wheel and sdist build validation
- Add a release workflow that builds artifacts on tags and/or `workflow_dispatch`.
- Publish only when credentials or trusted publishing are actually configured. If publishing is not wired yet, make CD artifact-oriented and honest rather than pretending deploy is live.

### 3. Build a hostile test suite

- Default to `pytest`.
- Keep tests hermetic: no live network, no live provider calls, no OCR model downloads, no dependence on the user's real home directory.
- Favor temporary directories, monkeypatching, and stubs over hitting real APIs or heavyweight parser code.
- Cover the repo by behavior, not by file count. Hit the dangerous seams first:
  - parser/resource exhaustion and malformed inputs
  - path traversal, UNC paths, and out-of-scope file access
  - redirect safety and URL validation
  - spreadsheet export injection
  - session/history/notes persistence semantics
  - doc ID collisions and cache confusion
  - slash-command and tool-surface drift between `src/app.py` and `src/widgets/chat.py`
  - packaging and entrypoint regressions
- Add regression tests for each real bug you fix. A hardening change without a test is incomplete.
- Use Textual test helpers when UI behavior matters, but do not let UI tests become the only coverage for logic that can be unit-tested directly.

### 4. Gate for production readiness, not vanity metrics

- Set a coverage floor only after the meaningful tests exist. Do not use a low global threshold as a substitute for real risk coverage.
- Raise the bar on critical modules first: `src/app.py`, `src/agents/agent_manager.py`, `src/agents/tools.py`, `src/web_search.py`, `src/exporter.py`, `src/chat_history.py`, `src/notes.py`, and `src/parsers/`.
- Prefer explicit assertions on security-sensitive behavior over blanket line coverage.
- Fail the pipeline on packaging breakage, smoke import failures, or missing workflows.
- Keep the implementation simple. Avoid introducing large abstractions or heavyweight infrastructure just to satisfy the test runner.

### 5. Verify locally before calling it done

- Run compileall, smoke imports, pytest, and build checks locally.
- Use the gate script with build enabled once the workflow and test suite exist:

```bash
python skills/study-tui-production-hardening/scripts/hardening_gate.py \
  --require-workflows \
  --require-tests \
  --run-build \
  --pytest-arg=--cov=src \
  --pytest-arg=--cov-report=term-missing
```

- If the local gate and workflow logic disagree, fix the mismatch before finishing.

## Guardrails

- Do not claim CD is working if the repo only has CI.
- Do not add tests that depend on real API keys, real OCR model downloads, or mutable external web content.
- Do not ignore Windows behavior just because Linux CI is easier.
- Do not touch files under `src/security_audit_artifacts/` except as read-only fixture material.
- Do not replace hostile tests with shallow snapshot tests or happy-path-only CLI smoke checks.

## Example Triggers

- "Use $study-tui-production-hardening to add CI and a real test suite."
- "Harden this repo for release."
- "Set up GitHub Actions and make the tests hostile to production bugs."
- "Repair the pipeline and make sure this package is actually shippable."
