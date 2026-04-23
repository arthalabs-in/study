from __future__ import annotations

from pathlib import Path

import pytest

import src.motion_canvas_renderer as motion_canvas_renderer
from src.motion_canvas_renderer import (
    get_motion_canvas_dependency_error,
    render_motion_canvas_animation,
    validate_motion_canvas_code,
)


VALID_SCENE = """import {Circle, makeScene2D} from '@motion-canvas/2d';
import {all, createRef} from '@motion-canvas/core';

export default makeScene2D(function* (view) {
  const circle = createRef<Circle>();
  view.add(<Circle ref={circle} size={180} fill={'#ffb347'} />);
  yield* all(
    circle().scale(1.2, 0.8),
    circle().fill('#4dd0e1', 0.8),
  );
});
"""


def test_validate_motion_canvas_code_rejects_blocked_imports_and_calls() -> None:
    ok, error = validate_motion_canvas_code(
        "import {makeScene2D} from '@motion-canvas/2d';\n"
        "import fs from 'fs';\n"
        "export default makeScene2D(function* () {\n"
        "  fetch('https://example.com');\n"
        "});\n"
    )
    assert ok is False
    assert error is not None


def test_validate_motion_canvas_code_requires_default_scene_export() -> None:
    ok, error = validate_motion_canvas_code(
        "import {makeScene2D} from '@motion-canvas/2d';\n"
        "const scene = makeScene2D(function* () {});\n"
    )
    assert ok is False
    assert "export default" in str(error)


def test_validate_motion_canvas_code_allows_full_motion_canvas_namespace() -> None:
    ok, error = validate_motion_canvas_code(
        "import {makeScene2D} from '@motion-canvas/2d';\n"
        "import {Vector2} from '@motion-canvas/core/lib/math';\n"
        "import {Player} from '@motion-canvas/player';\n"
        "import '@motion-canvas/ui';\n"
        "export default makeScene2D(function* () {});\n"
    )
    assert ok is True
    assert error is None


def test_validate_motion_canvas_code_rejects_known_bad_symbol_source() -> None:
    ok, error = validate_motion_canvas_code(
        "import {makeScene2D, Vector2} from '@motion-canvas/2d';\n"
        "export default makeScene2D(function* () {});\n"
    )

    assert ok is False
    assert error is not None
    assert "Vector2" in error
    assert "@motion-canvas/core" in error


def test_collect_motion_canvas_packages_includes_requested_namespace_packages() -> None:
    packages = motion_canvas_renderer._collect_motion_canvas_packages(
        "import {makeScene2D} from '@motion-canvas/2d';\n"
        "import {Vector2} from '@motion-canvas/core/lib/math';\n"
        "import {Player} from '@motion-canvas/player';\n"
        "import '@motion-canvas/ui';\n"
        "export default makeScene2D(function* () {});\n"
    )

    assert "@motion-canvas/2d" in packages
    assert "@motion-canvas/core" in packages
    assert "@motion-canvas/player" in packages
    assert "@motion-canvas/ui" in packages
    assert "@motion-canvas/vite-plugin" in packages


def test_discover_official_motion_canvas_packages_parses_registry_results() -> None:
    packages = motion_canvas_renderer._discover_official_motion_canvas_packages(
        """
[
  {
    "name": "@motion-canvas/ui",
    "links": {"homepage": "https://motioncanvas.io/", "repository": "git+https://github.com/motion-canvas/motion-canvas.git"}
  },
  {
    "name": "@motion-canvas/player",
    "links": {"homepage": "https://motioncanvas.io/"}
  },
  {
    "name": "@someone-else/not-motion-canvas",
    "links": {"homepage": "https://example.com/"}
  }
]
        """.strip()
    )

    assert "@motion-canvas/ui" in packages
    assert "@motion-canvas/player" in packages
    assert "@someone-else/not-motion-canvas" not in packages


def test_runtime_package_data_includes_discovered_motion_canvas_packages(monkeypatch) -> None:
    monkeypatch.setattr(
        motion_canvas_renderer,
        "_discover_official_motion_canvas_packages",
        lambda npm_search_output=None: {"@motion-canvas/ui", "@motion-canvas/player"},
    )

    package_data = motion_canvas_renderer._runtime_package_data({"@motion-canvas/2d"})
    dependencies = package_data["dependencies"]

    assert "@motion-canvas/ui" in dependencies
    assert "@motion-canvas/player" in dependencies
    assert "@motion-canvas/2d" in dependencies
    assert "vite" in dependencies


def test_get_motion_canvas_dependency_error_reports_missing_runtime(monkeypatch) -> None:
    monkeypatch.setattr(motion_canvas_renderer, "_find_binary", lambda name: None)
    monkeypatch.setattr(motion_canvas_renderer, "_playwright_module_available", lambda: False)
    monkeypatch.setattr(motion_canvas_renderer, "_find_browser_executable", lambda: None)
    error = get_motion_canvas_dependency_error()
    assert error is not None
    assert "Node.js" in error or "Playwright" in error


def test_write_project_files_normalizes_plugin_exports(tmp_path: Path) -> None:
    work_dir = tmp_path / "motion-canvas-project"
    motion_canvas_renderer._write_project_files(
        work_dir=work_dir,
        scene_code=VALID_SCENE,
        preset={"fps": 30, "width": 1280, "height": 720},
    )

    vite_config = (work_dir / "vite.config.ts").read_text(encoding="utf-8")
    assert "normalizePluginFactory" in vite_config
    assert "import * as motionCanvasModule" in vite_config
    assert "import * as ffmpegModule" in vite_config
    assert "typeof (mod as {default?: unknown}).default === 'function'" in vite_config
    assert "const motionCanvas = normalizePluginFactory" in vite_config
    assert "const ffmpeg = normalizePluginFactory" in vite_config


def test_extract_vite_failure_detects_compile_errors(tmp_path: Path) -> None:
    log_path = tmp_path / "vite.log"
    log_path.write_text(
        "Internal server error: Failed to resolve import \"@motion-canvas/core/lib/math\" from \"src/scene.tsx\".\n",
        encoding="utf-8",
    )

    error = motion_canvas_renderer._extract_vite_failure(log_path)

    assert error is not None
    assert "Failed to resolve import" in error


def test_extract_browser_failure_prefers_page_errors() -> None:
    error = motion_canvas_renderer._extract_browser_failure(
        page_errors=[
            "The requested module '/@fs/.../@motion-canvas_2d.js' does not provide an export named 'Vector2'"
        ],
        console_errors=["Failed to load resource: the server responded with a status of 404 (Not Found)"],
        request_failures=[],
        body_text='{"status":"booting"}',
    )

    assert error is not None
    assert "does not provide an export named 'Vector2'" in error


def test_extract_browser_failure_reports_booting_stall_with_console_errors() -> None:
    error = motion_canvas_renderer._extract_browser_failure(
        page_errors=[],
        console_errors=["Failed to load resource: the server responded with a status of 404 (Not Found)"],
        request_failures=[],
        body_text='{"status":"booting"}',
    )

    assert error is not None
    assert "Failed to load resource" in error


@pytest.mark.asyncio
async def test_render_motion_canvas_animation_returns_dependency_failure(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        motion_canvas_renderer,
        "get_motion_canvas_dependency_error",
        lambda: "Motion Canvas requires Node.js, npm, Playwright, and a Chromium browser.",
    )

    result = await render_motion_canvas_animation(VALID_SCENE, export_dir=tmp_path)
    assert result.success is False
    assert "Motion Canvas requires" in str(result.error)


@pytest.mark.asyncio
async def test_render_motion_canvas_animation_collects_output(monkeypatch, tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    run_dir = runtime_dir / "runs" / "render-001"
    output_dir = run_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    video_path = output_dir / "Scene.mp4"
    video_path.write_bytes(b"video")

    monkeypatch.setattr(motion_canvas_renderer, "get_motion_canvas_dependency_error", lambda: None)
    monkeypatch.setattr(motion_canvas_renderer, "_ensure_runtime", lambda required_packages=None: runtime_dir)
    monkeypatch.setattr(motion_canvas_renderer, "_create_run_dir", lambda base_dir: run_dir)
    monkeypatch.setattr(motion_canvas_renderer, "_write_project_files", lambda **kwargs: None)
    monkeypatch.setattr(motion_canvas_renderer, "_pick_free_port", lambda: 9010)

    class DummyProcess:
        def __init__(self) -> None:
            self.returncode = None

        def poll(self) -> None:
            return self.returncode

        def terminate(self) -> None:
            self.returncode = 0

        def wait(self, timeout: float | None = None) -> int:
            self.returncode = 0
            return 0

        def kill(self) -> None:
            self.returncode = -9

    dummy_process = DummyProcess()
    monkeypatch.setattr(
        motion_canvas_renderer,
        "_start_vite_server",
        lambda work_dir, port, log_path: dummy_process,
    )
    monkeypatch.setattr(
        motion_canvas_renderer,
        "_run_browser_preflight",
        lambda **kwargs: {"status": "success", "message": "preflight ok"},
    )
    monkeypatch.setattr(
        motion_canvas_renderer,
        "_run_browser_render",
        lambda **kwargs: {"status": "success", "message": "done"},
    )

    result = await render_motion_canvas_animation(VALID_SCENE, export_dir=tmp_path, quality="medium")
    assert result.success is True
    assert result.video_path and Path(result.video_path).exists()
    assert result.code_path and Path(result.code_path).exists()
    assert result.scene_name == "MotionCanvasScene"


@pytest.mark.asyncio
async def test_render_motion_canvas_animation_stops_on_preflight_failure(monkeypatch, tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    run_dir = runtime_dir / "runs" / "render-002"
    run_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(motion_canvas_renderer, "get_motion_canvas_dependency_error", lambda: None)
    monkeypatch.setattr(motion_canvas_renderer, "_ensure_runtime", lambda required_packages=None: runtime_dir)
    monkeypatch.setattr(motion_canvas_renderer, "_create_run_dir", lambda base_dir: run_dir)
    monkeypatch.setattr(motion_canvas_renderer, "_write_project_files", lambda **kwargs: None)
    monkeypatch.setattr(motion_canvas_renderer, "_pick_free_port", lambda: 9011)

    class DummyProcess:
        def __init__(self) -> None:
            self.returncode = None

        def poll(self) -> None:
            return self.returncode

        def terminate(self) -> None:
            self.returncode = 0

        def wait(self, timeout: float | None = None) -> int:
            self.returncode = 0
            return 0

        def kill(self) -> None:
            self.returncode = -9

    dummy_process = DummyProcess()
    monkeypatch.setattr(
        motion_canvas_renderer,
        "_start_vite_server",
        lambda work_dir, port, log_path: dummy_process,
    )

    calls: list[str] = []

    async def fake_preflight(**kwargs):
        calls.append("preflight")
        return {
            "status": "error",
            "message": "Motion Canvas browser error: does not provide an export named 'Vector2'",
        }

    async def fake_render(**kwargs):
        calls.append("render")
        return {"status": "success", "message": "done"}

    monkeypatch.setattr(motion_canvas_renderer, "_run_browser_preflight", fake_preflight)
    monkeypatch.setattr(motion_canvas_renderer, "_run_browser_render", fake_render)

    result = await render_motion_canvas_animation(VALID_SCENE, export_dir=tmp_path, quality="medium")

    assert result.success is False
    assert "Vector2" in str(result.error)
    assert calls == ["preflight"]
