from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

import src.manim_renderer as manim_renderer
from src.manim_renderer import (
    _decode_subprocess_output,
    _extract_error_summary,
    get_animation_dependency_error,
    render_animation,
    validate_code,
)


VALID_SCENE = """from manim import *

class DemoScene(Scene):
    def construct(self):
        self.wait()
"""


def test_validate_code_rejects_blocked_imports_and_calls() -> None:
    ok, error, scene_name = validate_code(
        "import os\nfrom manim import *\nclass DemoScene(Scene):\n    def construct(self):\n        open('x')"
    )
    assert ok is False
    assert error is not None
    assert scene_name is None


def test_validate_code_requires_exactly_one_scene() -> None:
    ok, error, _scene_name = validate_code("from manim import *\nvalue = 1")
    assert ok is False
    assert "No Scene subclass found" in str(error)

    ok, error, _scene_name = validate_code(
        "from manim import *\n"
        "class A(Scene):\n    def construct(self):\n        self.wait()\n"
        "class B(Scene):\n    def construct(self):\n        self.wait()\n"
    )
    assert ok is False
    assert "exactly one Scene subclass" in str(error)


def test_validate_code_extracts_scene_name() -> None:
    ok, error, scene_name = validate_code(VALID_SCENE)
    assert ok is True
    assert error is None
    assert scene_name == "DemoScene"


@pytest.mark.asyncio
async def test_render_animation_forwards_subprocess_error(monkeypatch, tmp_path: Path) -> None:
    work_dir = tmp_path / "work-error"
    work_dir.mkdir()
    monkeypatch.setattr(manim_renderer.tempfile, "mkdtemp", lambda prefix="study-tui-manim-": str(work_dir))
    monkeypatch.setattr(manim_renderer, "get_animation_dependency_error", lambda: None)

    async def fake_run(cmd, cwd):
        return subprocess.CompletedProcess(cmd, 1, "", "ValueError: broken scene")

    monkeypatch.setattr(manim_renderer, "_run_subprocess", fake_run)
    result = await render_animation(VALID_SCENE, export_dir=tmp_path)
    assert result.success is False
    assert "Render error" in str(result.error)
    assert result.code_path and result.code_path.endswith(".py")
    assert result.stderr == "ValueError: broken scene"


@pytest.mark.asyncio
async def test_render_animation_uses_stdout_when_stderr_is_empty(monkeypatch, tmp_path: Path) -> None:
    work_dir = tmp_path / "work-stdout-error"
    work_dir.mkdir()
    monkeypatch.setattr(manim_renderer.tempfile, "mkdtemp", lambda prefix="study-tui-manim-": str(work_dir))
    monkeypatch.setattr(manim_renderer, "get_animation_dependency_error", lambda: None)

    async def fake_run(cmd, cwd):
        return subprocess.CompletedProcess(cmd, 1, "FileNotFoundError: [WinError 2] missing latex", "")

    monkeypatch.setattr(manim_renderer, "_run_subprocess", fake_run)
    result = await render_animation(VALID_SCENE, export_dir=tmp_path)
    assert result.success is False
    assert "Missing required TeX dependency" in str(result.error)
    assert result.stderr == "FileNotFoundError: [WinError 2] missing latex"


@pytest.mark.asyncio
async def test_render_animation_collects_output(monkeypatch, tmp_path: Path) -> None:
    work_dir = tmp_path / "work-success"
    work_dir.mkdir()
    monkeypatch.setattr(manim_renderer.tempfile, "mkdtemp", lambda prefix="study-tui-manim-": str(work_dir))
    monkeypatch.setattr(manim_renderer, "get_animation_dependency_error", lambda: None)

    async def fake_run(cmd, cwd):
        media = Path(cwd) / "media" / "videos" / "scene" / "480p15"
        media.mkdir(parents=True, exist_ok=True)
        (media / "DemoScene.mp4").write_bytes(b"video-bytes")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(manim_renderer, "_run_subprocess", fake_run)
    result = await render_animation(VALID_SCENE, export_dir=tmp_path)
    assert result.success is True
    assert result.video_path and Path(result.video_path).exists()
    assert result.code_path and Path(result.code_path).exists()
    assert result.scene_name == "DemoScene"


@pytest.mark.asyncio
async def test_render_animation_enforces_timeout(monkeypatch, tmp_path: Path) -> None:
    work_dir = tmp_path / "work-timeout"
    work_dir.mkdir()
    monkeypatch.setattr(manim_renderer.tempfile, "mkdtemp", lambda prefix="study-tui-manim-": str(work_dir))
    monkeypatch.setattr(manim_renderer, "get_animation_dependency_error", lambda: None)

    async def fake_run(cmd, cwd):
        await asyncio.sleep(0.05)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(manim_renderer, "_run_subprocess", fake_run)
    result = await render_animation(VALID_SCENE, export_dir=tmp_path, timeout=0)
    assert result.success is False
    assert "timed out" in str(result.error).lower()


def test_get_animation_dependency_error_requires_tex_and_dvisvgm(monkeypatch) -> None:
    monkeypatch.setattr(manim_renderer, "_candidate_binary_dirs", lambda: [])

    def fake_which(name: str) -> str | None:
        if name == "manim":
            return "C:/bin/manim"
        if name == "pdflatex":
            return "C:/tex/pdflatex"
        if name == "dvisvgm":
            return None
        return None

    monkeypatch.setattr(manim_renderer.shutil, "which", fake_which)
    assert "dvisvgm is required" in str(get_animation_dependency_error())


def test_get_animation_dependency_error_finds_miktex_binaries_outside_path(monkeypatch, tmp_path: Path) -> None:
    bin_dir = tmp_path / "Programs" / "MiKTeX" / "miktex" / "bin" / "x64"
    bin_dir.mkdir(parents=True)
    for name in ("latex.exe", "dvisvgm.exe"):
        (bin_dir / name).write_text("", encoding="utf-8")

    def fake_which(name: str) -> str | None:
        if name == "manim":
            return "C:/bin/manim.exe"
        return None

    monkeypatch.setattr(manim_renderer.shutil, "which", fake_which)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert get_animation_dependency_error() is None


def test_extract_error_summary_distinguishes_tex_compile_error_from_missing_dependency() -> None:
    message = _extract_error_summary(
        "ERROR LaTeX compilation error: Misplaced alignment tab character &.\n"
        "Context of error: tex_file_writing.py"
    )
    assert "LaTeX compilation failed" in message
    assert "Missing required TeX dependency" not in message


def test_decode_subprocess_output_handles_non_utf8_bytes() -> None:
    value = _decode_subprocess_output(b"bad-byte-\x81-tail")
    assert "bad-byte" in value
