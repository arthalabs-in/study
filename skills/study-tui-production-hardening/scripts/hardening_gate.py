from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path


SMOKE_IMPORTS = [
    "src.app",
    "src.widgets.chat",
    "src.agents.provider",
    "src.agents.agent_manager",
    "src.parsers.pdf_parser",
    "src.parsers.image_parser",
    "src.notes",
    "src.chat_history",
    "src.exporter",
    "src.web_search",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the Study TUI hardening gate from the repository root."
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root to validate. Defaults to the current working directory.",
    )
    parser.add_argument(
        "--require-workflows",
        action="store_true",
        help="Fail when CI and release workflows cannot be detected.",
    )
    parser.add_argument(
        "--require-tests",
        action="store_true",
        help="Fail when no tests directory exists before invoking pytest.",
    )
    parser.add_argument(
        "--run-build",
        action="store_true",
        help="Run `python -m build --sdist --wheel` as part of the gate.",
    )
    parser.add_argument(
        "--pytest-arg",
        action="append",
        default=[],
        help="Extra argument to pass through to pytest. Repeat for multiple flags.",
    )
    return parser.parse_args()


def print_header(title: str) -> None:
    print(f"\n==> {title}")


def format_command(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def run_command(title: str, command: list[str], cwd: Path, env: dict[str, str] | None = None) -> bool:
    print_header(title)
    print(f"$ {format_command(command)}")
    completed = subprocess.run(command, cwd=cwd, env=env)
    if completed.returncode == 0:
        print(f"[PASS] {title}")
        return True
    print(f"[FAIL] {title} (exit {completed.returncode})")
    return False


def detect_workflows(repo_root: Path) -> tuple[list[Path], list[Path], list[str]]:
    workflow_dir = repo_root / ".github" / "workflows"
    notes: list[str] = []
    if not workflow_dir.exists():
        notes.append("Missing .github/workflows directory.")
        return [], [], notes

    workflow_files = sorted(workflow_dir.glob("*.yml")) + sorted(workflow_dir.glob("*.yaml"))
    if not workflow_files:
        notes.append("No workflow files found under .github/workflows.")
        return [], [], notes

    ci_candidates: list[Path] = []
    release_candidates: list[Path] = []

    for workflow_file in workflow_files:
        try:
            text = workflow_file.read_text(encoding="utf-8").lower()
        except UnicodeDecodeError:
            text = workflow_file.read_text(encoding="utf-8", errors="ignore").lower()

        if "push" in text and "pull_request" in text and "setup-python" in text:
            ci_candidates.append(workflow_file)

        release_trigger = "workflow_dispatch" in text or "tags:" in text
        release_action = any(
            keyword in text
            for keyword in ("python -m build", "pypi", "upload-artifact", "artifact")
        )
        if release_trigger and release_action:
            release_candidates.append(workflow_file)

    if not ci_candidates:
        notes.append(
            "No workflow looks like CI. Expect push + pull_request + setup-python in one workflow."
        )
    if not release_candidates:
        notes.append(
            "No workflow looks like release/CD. Expect workflow_dispatch or tag trigger plus artifact/build steps."
        )

    return ci_candidates, release_candidates, notes


def check_workflows(repo_root: Path) -> bool:
    print_header("Workflow detection")
    ci_candidates, release_candidates, notes = detect_workflows(repo_root)

    if ci_candidates:
        print("CI candidates:")
        for path in ci_candidates:
            print(f"  - {path.relative_to(repo_root)}")
    if release_candidates:
        print("Release/CD candidates:")
        for path in release_candidates:
            print(f"  - {path.relative_to(repo_root)}")
    if notes:
        for note in notes:
            print(f"[WARN] {note}")

    success = bool(ci_candidates and release_candidates)
    print("[PASS] Workflow detection" if success else "[FAIL] Workflow detection")
    return success


def build_smoke_import_code() -> str:
    lines = ["import importlib"]
    for module_name in SMOKE_IMPORTS:
        lines.append(f"importlib.import_module({module_name!r})")
    lines.append("print('Imported smoke modules successfully')")
    return "; ".join(lines)


def build_env(repo_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    temp_root = repo_root / "build-tmp" / "hardening-gate"
    temp_root.mkdir(parents=True, exist_ok=True)
    env["TMP"] = str(temp_root)
    env["TEMP"] = str(temp_root)
    env["TMPDIR"] = str(temp_root)
    return env


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()

    if not (repo_root / "src").exists():
        print(f"[FAIL] Expected a src directory under {repo_root}")
        return 1

    checks: list[tuple[str, bool]] = []

    if args.require_workflows:
        checks.append(("Workflow detection", check_workflows(repo_root)))

    checks.append(
        (
            "Compileall",
            run_command(
                "Compileall",
                [sys.executable, "-m", "compileall", "src"],
                repo_root,
            ),
        )
    )
    checks.append(
        (
            "Smoke imports",
            run_command(
                "Smoke imports",
                [sys.executable, "-c", build_smoke_import_code()],
                repo_root,
            ),
        )
    )

    tests_dir = repo_root / "tests"
    if args.require_tests and not tests_dir.exists():
        print_header("Pytest")
        print("[FAIL] tests/ directory is missing.")
        checks.append(("Pytest", False))
    elif tests_dir.exists():
        pytest_command = [sys.executable, "-m", "pytest", *args.pytest_arg]
        checks.append(("Pytest", run_command("Pytest", pytest_command, repo_root)))
    else:
        print_header("Pytest")
        print("[WARN] tests/ directory not found. Skipping pytest.")

    if args.run_build:
        env = build_env(repo_root)
        checks.append(
            (
                "Build",
                run_command(
                    "Build",
                    [sys.executable, "-m", "build", "--sdist", "--wheel", "--no-isolation"],
                    repo_root,
                    env=env,
                ),
            )
        )

    failed = [name for name, passed in checks if not passed]

    print_header("Summary")
    if failed:
        print("Failed checks:")
        for name in failed:
            print(f"  - {name}")
        return 1

    print("All requested hardening checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
