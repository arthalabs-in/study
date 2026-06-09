"""
Guarded Manim animation renderer.

Validates agent-written Manim code with AST checks, executes
`manim render` in an isolated temp directory with a timeout,
and collects the rendered video.
"""

from __future__ import annotations

import ast
import asyncio
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_ALLOWED_IMPORTS = {"manim", "numpy", "math"}
_BLOCKED_ROOTS = {
    "os",
    "sys",
    "subprocess",
    "socket",
    "http",
    "urllib",
    "shutil",
    "pathlib",
    "ctypes",
    "requests",
    "tempfile",
    "importlib",
    "builtins",
}
_BLOCKED_CALLS = {"open", "exec", "eval", "compile", "__import__", "input"}
_SCENE_BASES = {"Scene", "MovingCameraScene", "ThreeDScene", "ZoomedScene"}
_QUALITY_FLAGS = {
    "low": ["-ql"],
    "medium": ["-qm"],
    "high": ["-qh"],
}
_LATEX_BINARIES = ("latex", "pdflatex", "xelatex", "lualatex")
_STDERR_PREVIEW_LIMIT = 4000
_MAX_VIDEO_BYTES = 250 * 1024 * 1024


@dataclass
class RenderResult:
    success: bool
    video_path: str | None = None
    code_path: str | None = None
    error: str | None = None
    stderr: str | None = None
    duration_seconds: float = 0.0
    scene_name: str = ""


class _SafetyVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.errors: list[str] = []

    def visit_Import(self, node: ast.Import) -> Any:
        for alias in node.names:
            root = alias.name.split(".")[0]
            if root in _BLOCKED_ROOTS:
                self.errors.append(f"Blocked import: {alias.name}")
            elif root not in _ALLOWED_IMPORTS:
                self.errors.append(f"Unsupported import: {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> Any:
        root = (node.module or "").split(".")[0]
        if not root:
            self.errors.append("Relative imports are not allowed.")
        elif root in _BLOCKED_ROOTS:
            self.errors.append(f"Blocked import: {node.module}")
        elif root not in _ALLOWED_IMPORTS:
            self.errors.append(f"Unsupported import: {node.module}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> Any:
        if isinstance(node.func, ast.Name) and node.func.id in _BLOCKED_CALLS:
            self.errors.append(f"Blocked call: {node.func.id}()")
        elif isinstance(node.func, ast.Attribute):
            root = _attribute_root(node.func)
            if root in _BLOCKED_ROOTS:
                self.errors.append(f"Blocked call root: {root}")
        self.generic_visit(node)


def _attribute_root(node: ast.Attribute) -> str:
    current: ast.AST = node
    while isinstance(current, ast.Attribute):
        current = current.value
    return current.id if isinstance(current, ast.Name) else ""


def _scene_base_name(base: ast.expr) -> str:
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        return base.attr
    if isinstance(base, ast.Subscript):
        return _scene_base_name(base.value)
    return ""


def validate_code(code: str) -> tuple[bool, str | None, str | None]:
    stripped = (code or "").strip()
    if not stripped:
        return False, "Code is empty.", None

    try:
        tree = ast.parse(stripped, mode="exec")
    except SyntaxError as exc:
        return False, f"Python syntax error: {exc.msg} (line {exc.lineno})", None

    visitor = _SafetyVisitor()
    visitor.visit(tree)
    if visitor.errors:
        return False, visitor.errors[0], None

    scene_classes: list[ast.ClassDef] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            base_names = {_scene_base_name(base) for base in node.bases}
            if base_names & _SCENE_BASES:
                scene_classes.append(node)

    if not scene_classes:
        return False, "No Scene subclass found. Define exactly one class inheriting from Scene.", None
    if len(scene_classes) != 1:
        return False, "Define exactly one Scene subclass per animation tool call.", None

    scene = scene_classes[0]
    has_construct = any(isinstance(item, ast.FunctionDef) and item.name == "construct" for item in scene.body)
    if not has_construct:
        return False, f"Scene class '{scene.name}' is missing a construct(self) method.", None

    return True, None, scene.name


async def render_animation(
    code: str,
    *,
    export_dir: str | Path | None = None,
    quality: str = "low",
    timeout: int = 120,
) -> RenderResult:
    start = time.monotonic()
    deps_error = get_animation_dependency_error()
    if deps_error:
        return RenderResult(success=False, error=deps_error, duration_seconds=time.monotonic() - start)
    valid, error, scene_name = validate_code(code)
    if not valid:
        return RenderResult(success=False, error=error, duration_seconds=time.monotonic() - start)

    work_dir = Path(tempfile.mkdtemp(prefix="study-tui-manim-"))
    scene_file = work_dir / "scene.py"
    try:
        scene_file.write_text(code, encoding="utf-8")
    except Exception as exc:
        _cleanup(work_dir)
        return RenderResult(success=False, error=f"Failed to write scene file: {exc}", duration_seconds=time.monotonic() - start)

    cmd = ["manim", "render", *_QUALITY_FLAGS.get(str(quality).lower(), _QUALITY_FLAGS["low"]), str(scene_file), scene_name or ""]
    try:
        completed = await asyncio.wait_for(_run_subprocess(cmd, cwd=work_dir), timeout=timeout)
    except asyncio.TimeoutError:
        code_path = _save_code_snapshot(code, scene_name or "Animation", export_dir, suffix="_FAILED")
        _cleanup(work_dir)
        return RenderResult(
            success=False,
            error=f"Render timed out after {timeout} seconds. Try simpler animations or lower quality.",
            code_path=code_path,
            scene_name=scene_name or "",
            duration_seconds=time.monotonic() - start,
        )
    except FileNotFoundError:
        code_path = _save_code_snapshot(code, scene_name or "Animation", export_dir, suffix="_FAILED")
        _cleanup(work_dir)
        return RenderResult(
            success=False,
            error=get_animation_dependency_error() or "Animation dependencies are not installed or not on PATH. Install study-tui[animation], Manim, and a LaTeX + dvisvgm toolchain.",
            code_path=code_path,
            scene_name=scene_name or "",
            duration_seconds=time.monotonic() - start,
        )
    except Exception as exc:
        code_path = _save_code_snapshot(code, scene_name or "Animation", export_dir, suffix="_FAILED")
        _cleanup(work_dir)
        return RenderResult(
            success=False,
            error=f"Render subprocess failed: {exc}",
            code_path=code_path,
            scene_name=scene_name or "",
            duration_seconds=time.monotonic() - start,
        )

    if completed.returncode != 0:
        stderr_text = (completed.stderr or "").strip()
        stdout_text = (completed.stdout or "").strip()
        diagnostic_text = stderr_text or stdout_text
        code_path = _save_code_snapshot(code, scene_name or "Animation", export_dir, suffix="_FAILED")
        _cleanup(work_dir)
        return RenderResult(
            success=False,
            error=_extract_error_summary(diagnostic_text),
            stderr=diagnostic_text[-_STDERR_PREVIEW_LIMIT:] if diagnostic_text else None,
            code_path=code_path,
            scene_name=scene_name or "",
            duration_seconds=time.monotonic() - start,
        )

    video_src = _find_rendered_video(work_dir)
    if not video_src:
        code_path = _save_code_snapshot(code, scene_name or "Animation", export_dir, suffix="_FAILED")
        _cleanup(work_dir)
        return RenderResult(
            success=False,
            error="Render completed but no video file was found.",
            code_path=code_path,
            scene_name=scene_name or "",
            duration_seconds=time.monotonic() - start,
        )

    try:
        if video_src.stat().st_size > _MAX_VIDEO_BYTES:
            raise ValueError("Rendered video is too large to keep safely.")
    except Exception as exc:
        code_path = _save_code_snapshot(code, scene_name or "Animation", export_dir, suffix="_FAILED")
        _cleanup(work_dir)
        return RenderResult(
            success=False,
            error=f"Rendered output could not be accepted: {exc}",
            code_path=code_path,
            scene_name=scene_name or "",
            duration_seconds=time.monotonic() - start,
        )

    video_path, code_path = _copy_to_exports(video_src, code, scene_name or "Animation", export_dir)
    _cleanup(work_dir)
    return RenderResult(
        success=True,
        video_path=str(video_path) if video_path else None,
        code_path=str(code_path) if code_path else None,
        scene_name=scene_name or "",
        duration_seconds=time.monotonic() - start,
    )


async def _run_subprocess(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: _run_subprocess_sync(cmd, cwd),
    )


def _run_subprocess_sync(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=False,
        timeout=180,
        env=_sandboxed_env(cwd),
    )
    return subprocess.CompletedProcess(
        completed.args,
        completed.returncode,
        _decode_subprocess_output(completed.stdout),
        _decode_subprocess_output(completed.stderr),
    )


def _decode_subprocess_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return value.decode(encoding)
        except UnicodeDecodeError:
            continue
    return value.decode("utf-8", errors="replace")


def _sandboxed_env(work_dir: Path) -> dict[str, str]:
    extra_path_dirs: list[str] = []
    for binary_name in ("manim", "latex", "pdflatex", "xelatex", "lualatex", "dvisvgm"):
        directory = _binary_dir(binary_name)
        if directory and directory not in extra_path_dirs:
            extra_path_dirs.append(directory)
    joined_path = os.pathsep.join(extra_path_dirs + [os.environ.get("PATH", "")])
    env: dict[str, str] = {
        "PATH": joined_path,
        "HOME": os.environ.get("HOME", str(work_dir)),
        "USERPROFILE": os.environ.get("USERPROFILE", str(work_dir)),
        "TMP": os.environ.get("TMP", str(work_dir)),
        "TEMP": os.environ.get("TEMP", str(work_dir)),
        "MANIM_DISABLE_CACHING": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    for key in ("SystemRoot", "APPDATA", "LOCALAPPDATA"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


def _find_rendered_video(work_dir: Path) -> Path | None:
    media_dir = work_dir / "media"
    if not media_dir.exists():
        return None
    videos = sorted(media_dir.rglob("*.mp4"), key=lambda item: item.stat().st_mtime)
    return videos[-1] if videos else None


def _ensure_export_dir(path: str | Path | None = None) -> Path:
    export_dir = Path(path) if path else Path.home() / "Documents" / "StudyTUI-Exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    return export_dir


def _copy_to_exports(video_src: Path, code: str, scene_name: str, export_dir: str | Path | None) -> tuple[Path | None, Path | None]:
    target_dir = _ensure_export_dir(export_dir)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in scene_name).strip("_")[:60] or "animation"
    base_name = f"{safe_name}_{timestamp}"
    video_dest = target_dir / f"{base_name}.mp4"
    code_dest = target_dir / f"{base_name}.py"
    try:
        shutil.copy2(str(video_src), str(video_dest))
    except Exception:
        video_dest = None
    try:
        code_dest.write_text(code, encoding="utf-8")
    except Exception:
        code_dest = None
    return video_dest, code_dest


def _save_code_snapshot(code: str, scene_name: str, export_dir: str | Path | None, suffix: str = "") -> str | None:
    target_dir = _ensure_export_dir(export_dir)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in scene_name).strip("_")[:60] or "animation"
    path = target_dir / f"{safe_name}_{timestamp}{suffix}.py"
    try:
        path.write_text(code, encoding="utf-8")
        return str(path)
    except Exception:
        return None


def _extract_error_summary(output: str) -> str:
    if not output:
        return "Render failed with no error output."
    lowered = output.lower()
    if any(
        token in lowered
        for token in (
            "no such file or directory: 'latex'",
            "no such file or directory: 'pdflatex'",
            "no such file or directory: 'xelatex'",
            "no such file or directory: 'lualatex'",
            "no such file or directory: 'dvisvgm'",
            "filenotfounderror",
            "missing latex",
            "missing pdflatex",
            "missing dvisvgm",
            "is not recognized as an internal or external command",
            "command not found: latex",
            "command not found: pdflatex",
            "command not found: dvisvgm",
            "a latex engine is required",
            "dvisvgm is required",
        )
    ):
        return (
            "Render error: Missing required TeX dependency. Install a LaTeX engine and dvisvgm."
        )
    if "latex compilation error" in lowered or "misplaced alignment tab character &" in lowered:
        return (
            "Render error: LaTeX compilation failed inside the animation. "
            "Escape special characters like &, %, _, #, and avoid TeX-backed helpers such as "
            "BulletedList for plain text."
        )
    if any(token in lowered for token in ("tex_file_writing", "tex_mobject.py", "tex_to_svg_file")):
        return (
            "Render error: TeX rendering failed inside the animation. "
            "The scene likely contains invalid TeX content rather than a missing dependency."
        )
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    for line in reversed(lines):
        if any(token in line for token in ("Error", "Exception", "Traceback")):
            return f"Render error: {line}"
    return f"Render error: {lines[-1]}" if lines else "Render failed with unknown error."


def _cleanup(work_dir: Path) -> None:
    try:
        shutil.rmtree(work_dir, ignore_errors=True)
    except Exception:
        pass


def _candidate_binary_dirs() -> list[Path]:
    dirs: list[Path] = []
    for env_key in ("LOCALAPPDATA", "APPDATA", "ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(env_key)
        if not root:
            continue
        base = Path(root)
        dirs.extend(
            [
                base / "Programs" / "MiKTeX" / "miktex" / "bin" / "x64",
                base / "MiKTeX" / "miktex" / "bin" / "x64",
                base / "MiKTeX" / "miktex" / "bin",
            ]
        )
    seen: list[Path] = []
    for path in dirs:
        if path.exists() and path not in seen:
            seen.append(path)
    return seen


def _find_binary(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for directory in _candidate_binary_dirs():
        for suffix in ("", ".exe"):
            candidate = directory / f"{name}{suffix}"
            if candidate.exists():
                return str(candidate)
    return None


def _binary_dir(name: str) -> str | None:
    binary = _find_binary(name)
    if not binary:
        return None
    try:
        return str(Path(binary).resolve().parent)
    except Exception:
        return str(Path(binary).parent)


def is_manim_available() -> bool:
    return _find_binary("manim") is not None


def _find_latex_binary() -> str | None:
    for name in _LATEX_BINARIES:
        binary = _find_binary(name)
        if binary:
            return binary
    return None


def is_tex_available() -> bool:
    return _find_latex_binary() is not None and _find_binary("dvisvgm") is not None


def get_animation_dependency_error() -> str | None:
    if not is_manim_available():
        return "Manim is required for animations. Install study-tui[animation] or pip install manim."
    if not _find_latex_binary():
        return "A LaTeX engine is required for animations. Install LaTeX (latex, pdflatex, xelatex, or lualatex)."
    if _find_binary("dvisvgm") is None:
        return "dvisvgm is required for animations. Install dvisvgm and keep it on PATH."
    return None
