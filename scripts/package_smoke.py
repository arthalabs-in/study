from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke-test packaged Study TUI installs.")
    parser.add_argument("--wheel", required=True, help="Path or glob pattern for the wheel to test.")
    parser.add_argument(
        "--method",
        action="append",
        choices=("uv-run", "uv-install", "pipx-install", "venv-install"),
        default=[],
        help="Installation/execution method to verify. Repeat for multiple methods.",
    )
    return parser.parse_args()


def resolve_wheel(pattern: str) -> Path:
    matches = sorted(Path().glob(pattern))
    if not matches:
        raise FileNotFoundError(f"No wheel matched: {pattern}")
    return matches[-1].resolve()


def run(command: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            text=True,
            capture_output=True,
            env=env,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        output = ((exc.stdout or "") + (exc.stderr or "")).strip()
        raise RuntimeError(
            f"Command failed: {' '.join(command)}\n{output}"
        ) from exc


def assert_help_output(command: list[str], env: dict[str, str] | None = None) -> None:
    completed = run(command, env=env)
    output = (completed.stdout or "") + (completed.stderr or "")
    if "Study TUI" not in output:
        raise RuntimeError(f"Command did not print expected help text: {' '.join(command)}\n{output}")


def smoke_uv_run(wheel: Path) -> None:
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv is not installed")
    assert_help_output([uv, "tool", "run", "--from", str(wheel), "study", "--help"])
    assert_help_output([uv, "tool", "run", "--from", str(wheel), "study-tui", "--help"])


def _isolated_tool_env(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    home = root / "home"
    appdata = root / "appdata"
    localappdata = root / "localappdata"
    home.mkdir(parents=True, exist_ok=True)
    appdata.mkdir(parents=True, exist_ok=True)
    localappdata.mkdir(parents=True, exist_ok=True)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["APPDATA"] = str(appdata)
    env["LOCALAPPDATA"] = str(localappdata)
    return env


def _bin_name(name: str) -> str:
    return f"{name}.exe" if os.name == "nt" else name


def _venv_paths(root: Path) -> tuple[Path, Path]:
    scripts_dir = root / ("Scripts" if os.name == "nt" else "bin")
    python = scripts_dir / _bin_name("python")
    return python, scripts_dir


def smoke_uv_install(wheel: Path) -> None:
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv is not installed")

    with tempfile.TemporaryDirectory(prefix="study-tui-uv-smoke-") as temp_root:
        env = _isolated_tool_env(Path(temp_root))
        run([uv, "tool", "install", "--force", "--python", sys.executable, str(wheel)], env=env)
        listing = run([uv, "tool", "list", "--show-paths"], env=env)
        if "study-tui" not in listing.stdout:
            raise RuntimeError(f"uv tool install did not register study-tui:\n{listing.stdout}")
        assert_help_output([uv, "tool", "run", "--from", str(wheel), "study", "--help"], env=env)
        run([uv, "tool", "uninstall", "study-tui"], env=env)


def smoke_pipx_install(wheel: Path) -> None:
    pipx = shutil.which("pipx")
    if not pipx:
        raise RuntimeError("pipx is not installed")

    with tempfile.TemporaryDirectory(prefix="study-tui-pipx-smoke-") as temp_root:
        root = Path(temp_root)
        env = _isolated_tool_env(root)
        pipx_home = root / "pipx-home"
        pipx_bin = root / "pipx-bin"
        pipx_home.mkdir(parents=True, exist_ok=True)
        pipx_bin.mkdir(parents=True, exist_ok=True)
        env["PIPX_HOME"] = str(pipx_home)
        env["PIPX_BIN_DIR"] = str(pipx_bin)

        run([pipx, "install", "--force", "--python", sys.executable, str(wheel)], env=env)
        try:
            show = run([pipx, "runpip", "study-tui", "show", "study-tui"], env=env)
            if "Name: study-tui" not in show.stdout:
                raise RuntimeError(f"pipx did not install study-tui correctly:\n{show.stdout}")
            assert_help_output([str(pipx_bin / _bin_name("study")), "--help"], env=env)
            assert_help_output([str(pipx_bin / _bin_name("study-tui")), "--help"], env=env)
        finally:
            subprocess.run([pipx, "uninstall", "study-tui"], capture_output=True, text=True, env=env)


def smoke_venv_install(wheel: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="study-tui-venv-smoke-") as temp_root:
        root = Path(temp_root)
        env = _isolated_tool_env(root)
        venv.EnvBuilder(with_pip=True, system_site_packages=True).create(root / "venv")
        python, scripts_dir = _venv_paths(root / "venv")
        run([str(python), "-m", "pip", "install", "--no-deps", "--force-reinstall", str(wheel)], env=env)
        show = run([str(python), "-m", "pip", "show", "study-tui"], env=env)
        if "Name: study-tui" not in show.stdout:
            raise RuntimeError(f"venv install did not register study-tui:\n{show.stdout}")
        assert_help_output([str(scripts_dir / _bin_name("study")), "--help"], env=env)
        assert_help_output([str(scripts_dir / _bin_name("study-tui")), "--help"], env=env)


def main() -> int:
    args = parse_args()
    wheel = resolve_wheel(args.wheel)
    methods = args.method or ["uv-run"]
    runners = {
        "uv-run": smoke_uv_run,
        "uv-install": smoke_uv_install,
        "pipx-install": smoke_pipx_install,
        "venv-install": smoke_venv_install,
    }
    for method in methods:
        runners[method](wheel)
        print(f"[PASS] {method}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
