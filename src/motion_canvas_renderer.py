"""
Experimental Motion Canvas renderer.

This backend keeps Manim as the default animation path while letting Study TUI
render Motion Canvas scenes through a minimal custom editor loaded in a
headless Chromium browser. The runtime is provisioned lazily into the user's
Study TUI home directory so repeated renders can reuse installed Node packages.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from src.manim_renderer import RenderResult


_BLOCKED_IMPORT_PATTERNS = (
    r"""from\s+['"](?:node:)?fs['"]""",
    r"""from\s+['"](?:node:)?path['"]""",
    r"""from\s+['"](?:node:)?os['"]""",
    r"""from\s+['"](?:node:)?http[s]?['"]""",
    r"""from\s+['"](?:node:)?net['"]""",
    r"""from\s+['"](?:node:)?tls['"]""",
    r"""from\s+['"](?:node:)?dgram['"]""",
    r"""from\s+['"](?:node:)?child_process['"]""",
    r"""from\s+['"](?:node:)?worker_threads['"]""",
)
_BLOCKED_CALL_PATTERNS = (
    r"""process\.exit\s*\(""",
    r"""fetch\s*\(""",
    r"""XMLHttpRequest\s*\(""",
    r"""WebSocket\s*\(""",
)
_DEFAULT_MOTION_CANVAS_PACKAGES = {
    "@motion-canvas/2d",
    "@motion-canvas/core",
    "@motion-canvas/create",
    "@motion-canvas/ffmpeg",
    "@motion-canvas/player",
    "@motion-canvas/ui",
    "@motion-canvas/vite-plugin",
}
_IMPORT_FROM_PATTERN = re.compile(
    r"""^\s*import(?:\s+type)?(?:[\s\S]*?)from\s+['"]([^'"]+)['"]""",
    flags=re.MULTILINE,
)
_SIDE_EFFECT_IMPORT_PATTERN = re.compile(
    r"""^\s*import\s+['"]([^'"]+)['"]""",
    flags=re.MULTILINE,
)
_NAMED_IMPORT_PATTERN = re.compile(
    r"""^\s*import(?:\s+type)?(?:[\w*\s,]*?)\{([^}]*)\}\s*from\s+['"]([^'"]+)['"]""",
    flags=re.MULTILINE,
)
_QUALITY_PRESETS: dict[str, dict[str, int]] = {
    "low": {"fps": 24, "width": 1280, "height": 720},
    "medium": {"fps": 30, "width": 1280, "height": 720},
    "high": {"fps": 60, "width": 1920, "height": 1080},
}
_MOTION_CANVAS_VERSION = "3.17.2"
_STDERR_PREVIEW_LIMIT = 4000
_MAX_VIDEO_BYTES = 250 * 1024 * 1024
_KNOWN_SYMBOL_PACKAGES = {
    "Vector2": "@motion-canvas/core",
    "all": "@motion-canvas/core",
    "chain": "@motion-canvas/core",
    "createRef": "@motion-canvas/core",
    "createSignal": "@motion-canvas/core",
    "easeInOutCubic": "@motion-canvas/core",
    "waitFor": "@motion-canvas/core",
    "Circle": "@motion-canvas/2d",
    "Layout": "@motion-canvas/2d",
    "Line": "@motion-canvas/2d",
    "makeScene2D": "@motion-canvas/2d",
    "Node": "@motion-canvas/2d",
    "Rect": "@motion-canvas/2d",
    "Txt": "@motion-canvas/2d",
}


def _discover_official_motion_canvas_packages(npm_search_output: str | None = None) -> set[str]:
    if npm_search_output is None:
        npm_binary = _find_binary("npm") or "npm"
        try:
            completed = subprocess.run(
                [npm_binary, "search", "@motion-canvas", "--json"],
                check=True,
                capture_output=True,
                text=True,
                timeout=20,
            )
            npm_search_output = completed.stdout
        except Exception:
            return set()
    try:
        payload = json.loads(npm_search_output or "[]")
    except json.JSONDecodeError:
        return set()
    packages: set[str] = set()
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name.startswith("@motion-canvas/"):
            continue
        links = item.get("links")
        homepage = ""
        repository = ""
        if isinstance(links, dict):
            homepage = str(links.get("homepage") or "").strip().lower()
            repository = str(links.get("repository") or "").strip().lower()
        if "motioncanvas.io" in homepage or "github.com/motion-canvas/motion-canvas" in repository:
            packages.add(name)
    return packages


def _collect_imported_modules(code: str) -> set[str]:
    stripped = code or ""
    imported_modules = {
        match.group(1).strip()
        for match in _IMPORT_FROM_PATTERN.finditer(stripped)
    }
    imported_modules.update(
        match.group(1).strip()
        for match in _SIDE_EFFECT_IMPORT_PATTERN.finditer(stripped)
    )
    return {module_name for module_name in imported_modules if module_name}


def _motion_canvas_package_name(module_name: str) -> str | None:
    if not module_name.startswith("@motion-canvas/"):
        return None
    parts = module_name.split("/")
    if len(parts) < 2:
        return None
    return "/".join(parts[:2])


def _collect_named_imports(code: str) -> list[tuple[str, str]]:
    bindings: list[tuple[str, str]] = []
    for match in _NAMED_IMPORT_PATTERN.finditer(code or ""):
        module_name = match.group(2).strip()
        for raw_symbol in match.group(1).split(","):
            symbol = raw_symbol.strip()
            if not symbol:
                continue
            original_name = symbol.split(" as ", 1)[0].strip()
            if original_name:
                bindings.append((original_name, module_name))
    return bindings


def _collect_motion_canvas_packages(code: str) -> set[str]:
    packages = set(_DEFAULT_MOTION_CANVAS_PACKAGES)
    for module_name in _collect_imported_modules(code):
        package_name = _motion_canvas_package_name(module_name)
        if package_name:
            packages.add(package_name)
    return packages


def validate_motion_canvas_code(code: str) -> tuple[bool, str | None]:
    stripped = (code or "").strip()
    if not stripped:
        return False, "Code is empty."
    if "makeScene2D" not in stripped:
        return False, "Motion Canvas code must export a scene built with makeScene2D."
    if "export default" not in stripped:
        return False, "Motion Canvas code must contain an export default scene."
    lowered = stripped.lower()
    imported_modules = _collect_imported_modules(stripped)
    for module_name in sorted(imported_modules):
        if _motion_canvas_package_name(module_name):
            continue
        return (
            False,
            "Motion Canvas code may import only from the @motion-canvas/* namespace.",
        )
    for symbol_name, module_name in _collect_named_imports(stripped):
        expected_package = _KNOWN_SYMBOL_PACKAGES.get(symbol_name)
        if expected_package and _motion_canvas_package_name(module_name) != expected_package:
            return (
                False,
                f"Motion Canvas symbol '{symbol_name}' should be imported from {expected_package}, not {module_name}.",
            )
    for pattern in _BLOCKED_IMPORT_PATTERNS:
        if re.search(pattern, stripped, flags=re.IGNORECASE):
            return False, "Blocked Node import found in Motion Canvas scene code."
    for pattern in _BLOCKED_CALL_PATTERNS:
        if re.search(pattern, stripped, flags=re.IGNORECASE):
            return False, "Blocked network or process call found in Motion Canvas scene code."
    if any(token in lowered for token in ("import.meta.env", "navigator.sendbeacon")):
        return False, "Blocked environment or network access found in Motion Canvas scene code."
    return True, None


def _package_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _playwright_module_available() -> bool:
    return _package_available("playwright")


def _find_binary(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    if os.name != "nt":
        return None
    program_files = [
        os.environ.get("ProgramFiles", ""),
        os.environ.get("ProgramFiles(x86)", ""),
        os.environ.get("LOCALAPPDATA", ""),
    ]
    suffixes = {
        "msedge": [
            Path("Microsoft") / "Edge" / "Application" / "msedge.exe",
        ],
        "chrome": [
            Path("Google") / "Chrome" / "Application" / "chrome.exe",
        ],
        "brave": [
            Path("BraveSoftware") / "Brave-Browser" / "Application" / "brave.exe",
        ],
        "node": [Path("nodejs") / "node.exe"],
        "npm": [Path("nodejs") / "npm.cmd", Path("nodejs") / "npm.exe"],
    }
    for root in program_files:
        if not root:
            continue
        for relative in suffixes.get(name, []):
            candidate = Path(root) / relative
            if candidate.exists():
                return str(candidate)
    return None


def _find_browser_executable() -> str | None:
    for name in ("msedge", "chrome", "brave"):
        found = _find_binary(name)
        if found:
            return found
    return None


def is_motion_canvas_available() -> bool:
    return get_motion_canvas_dependency_error() is None


def get_motion_canvas_runtime_probe() -> dict[str, bool]:
    return {
        "node": _find_binary("node") is not None,
        "npm": _find_binary("npm") is not None,
        "playwright": _playwright_module_available(),
        "browser": _find_browser_executable() is not None,
    }


def get_motion_canvas_dependency_error() -> str | None:
    probe = get_motion_canvas_runtime_probe()
    if not probe["node"]:
        return "Motion Canvas requires Node.js on PATH. Install Node.js 20+ to enable this backend."
    if not probe["npm"]:
        return "Motion Canvas requires npm on PATH. Install Node.js with npm to enable this backend."
    if not probe["playwright"]:
        return (
            "Motion Canvas requires Playwright for headless browser rendering. "
            "Install study-tui[animation] or pip install playwright."
        )
    return None


async def render_motion_canvas_animation(
    code: str,
    *,
    export_dir: str | Path | None = None,
    quality: str = "low",
    timeout: int = 240,
) -> RenderResult:
    start = time.monotonic()
    deps_error = get_motion_canvas_dependency_error()
    if deps_error:
        return RenderResult(success=False, error=deps_error, duration_seconds=time.monotonic() - start)

    valid, error = validate_motion_canvas_code(code)
    if not valid:
        return RenderResult(success=False, error=error, duration_seconds=time.monotonic() - start)

    quality_key = str(quality or "low").strip().lower()
    preset = _QUALITY_PRESETS.get(quality_key, _QUALITY_PRESETS["low"])
    runtime_dir = _ensure_runtime(_collect_motion_canvas_packages(code))
    work_dir = _create_run_dir(runtime_dir)
    log_path = work_dir / "vite.log"

    try:
        _write_project_files(work_dir=work_dir, scene_code=code, preset=preset)
        port = _pick_free_port()
        process = _start_vite_server(work_dir, port, log_path)
    except Exception as exc:
        code_path = _save_code_snapshot(code, export_dir, suffix="_FAILED")
        _cleanup(work_dir)
        return RenderResult(
            success=False,
            error=f"Motion Canvas setup failed: {exc}",
            code_path=code_path,
            scene_name="MotionCanvasScene",
            duration_seconds=time.monotonic() - start,
        )

    try:
        preflight_timeout = max(5, min(20, int(timeout)))
        preflight_result = _run_browser_preflight(
            url=f"http://127.0.0.1:{port}/?study_mode=preflight",
            timeout=preflight_timeout,
            log_path=log_path,
        )
        if asyncio.iscoroutine(preflight_result):
            preflight_result = await asyncio.wait_for(preflight_result, timeout=preflight_timeout)
        if preflight_result.get("status") != "success":
            code_path = _save_code_snapshot(code, export_dir, suffix="_FAILED")
            preview = str(preflight_result.get("message") or "").strip()
            return RenderResult(
                success=False,
                error=preview or "Motion Canvas preflight failed.",
                stderr=preview[-_STDERR_PREVIEW_LIMIT:] if preview else None,
                code_path=code_path,
                scene_name="MotionCanvasScene",
                duration_seconds=time.monotonic() - start,
            )
        browser_result = _run_browser_render(
            url=f"http://127.0.0.1:{port}/",
            timeout=timeout,
            log_path=log_path,
        )
        if asyncio.iscoroutine(browser_result):
            browser_result = await asyncio.wait_for(browser_result, timeout=timeout)
        if browser_result.get("status") != "success":
            code_path = _save_code_snapshot(code, export_dir, suffix="_FAILED")
            preview = str(browser_result.get("message") or "").strip()
            return RenderResult(
                success=False,
                error=preview or "Motion Canvas render failed.",
                stderr=preview[-_STDERR_PREVIEW_LIMIT:] if preview else None,
                code_path=code_path,
                scene_name="MotionCanvasScene",
                duration_seconds=time.monotonic() - start,
            )

        video_src = _find_rendered_video(work_dir)
        if not video_src:
            code_path = _save_code_snapshot(code, export_dir, suffix="_FAILED")
            log_preview = _read_log_preview(log_path)
            return RenderResult(
                success=False,
                error="Motion Canvas render completed but no video file was found.",
                stderr=log_preview or None,
                code_path=code_path,
                scene_name="MotionCanvasScene",
                duration_seconds=time.monotonic() - start,
            )
        if video_src.stat().st_size > _MAX_VIDEO_BYTES:
            code_path = _save_code_snapshot(code, export_dir, suffix="_FAILED")
            return RenderResult(
                success=False,
                error="Rendered Motion Canvas video is too large to keep safely.",
                code_path=code_path,
                scene_name="MotionCanvasScene",
                duration_seconds=time.monotonic() - start,
            )
        video_path, code_path = _copy_to_exports(video_src, code, export_dir)
        return RenderResult(
            success=True,
            video_path=str(video_path) if video_path else None,
            code_path=str(code_path) if code_path else None,
            scene_name="MotionCanvasScene",
            duration_seconds=time.monotonic() - start,
        )
    except asyncio.TimeoutError:
        code_path = _save_code_snapshot(code, export_dir, suffix="_FAILED")
        return RenderResult(
            success=False,
            error=f"Motion Canvas render timed out after {timeout} seconds.",
            code_path=code_path,
            scene_name="MotionCanvasScene",
            duration_seconds=time.monotonic() - start,
        )
    finally:
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
        _cleanup(work_dir)


def _runtime_package_data(required_packages: set[str] | None = None) -> dict[str, Any]:
    official_packages = _discover_official_motion_canvas_packages()
    dependencies = {
        package_name: _MOTION_CANVAS_VERSION
        for package_name in sorted((required_packages or set()) | _DEFAULT_MOTION_CANVAS_PACKAGES | official_packages)
    }
    dependencies.update(
        {
            "typescript": "^5.6.0",
            "vite": "^5.4.0",
        }
    )
    return {
        "name": "study-tui-motion-canvas-runtime",
        "private": True,
        "type": "module",
        "dependencies": dependencies,
    }


def _ensure_runtime(required_packages: set[str] | None = None) -> Path:
    runtime_dir = Path.home() / ".study-tui" / "motion-canvas-runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    package_json = runtime_dir / "package.json"
    package_data = _runtime_package_data(required_packages)
    desired = json.dumps(package_data, indent=2) + "\n"
    package_changed = not package_json.exists() or package_json.read_text(encoding="utf-8") != desired
    if package_changed:
        package_json.write_text(desired, encoding="utf-8")
    if package_changed or not (runtime_dir / "node_modules").exists():
        subprocess.run(
            [_find_binary("npm") or "npm", "install", "--no-fund", "--no-audit"],
            cwd=str(runtime_dir),
            check=True,
            capture_output=True,
            text=True,
        )
    return runtime_dir


def _create_run_dir(base_dir: Path) -> Path:
    runs_dir = base_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="render-", dir=str(runs_dir)))


def _write_project_files(*, work_dir: Path, scene_code: str, preset: dict[str, int]) -> None:
    src_dir = work_dir / "src"
    editor_dir = work_dir / "render-editor"
    src_dir.mkdir(parents=True, exist_ok=True)
    editor_dir.mkdir(parents=True, exist_ok=True)

    (src_dir / "scene.tsx").write_text(scene_code, encoding="utf-8")
    (src_dir / "project.ts").write_text(
        "import {makeProject} from '@motion-canvas/core';\n"
        "import scene from './scene?scene';\n\n"
        "export default makeProject({\n"
        "  scenes: [scene],\n"
        "});\n",
        encoding="utf-8",
    )
    (work_dir / "tsconfig.json").write_text(
        json.dumps(
            {
                "compilerOptions": {
                    "target": "ES2020",
                    "module": "ESNext",
                    "moduleResolution": "Bundler",
                    "jsx": "react-jsx",
                    "jsxImportSource": "@motion-canvas/2d",
                    "strict": False,
                    "skipLibCheck": True,
                    "allowSyntheticDefaultImports": True,
                    "esModuleInterop": True,
                    "types": ["vite/client"],
                },
                "include": ["src/**/*", "render-editor/**/*", "vite.config.ts"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    editor_main = editor_dir / "main.js"
    editor_html = editor_dir / "editor.html"
    editor_css = editor_dir / "style.css"
    scene_name = "study_motion_canvas"
    editor_main.write_text(
        (
            "import {Renderer, Vector2} from '@motion-canvas/core';\n\n"
            "const root = () => document.getElementById('status');\n"
            "function setStatus(status, payload = {}) {\n"
            "  document.body.dataset.renderStatus = status;\n"
            "  root().textContent = JSON.stringify({status, ...payload});\n"
            "}\n\n"
            "window.addEventListener('error', event => {\n"
            "  setStatus('error', {message: event.error?.message || event.message || 'Unknown browser error'});\n"
            "});\n"
            "window.addEventListener('unhandledrejection', event => {\n"
            "  setStatus('error', {message: event.reason?.message || String(event.reason || 'Unhandled rejection')});\n"
            "});\n\n"
            "export async function editor(project) {\n"
            "  setStatus('working', {message: 'rendering'});\n"
            "  try {\n"
            "    const params = new URLSearchParams(window.location.search);\n"
            "    if (params.get('study_mode') === 'preflight') {\n"
            "      setStatus('success', {message: 'preflight ok'});\n"
            "      return;\n"
            "    }\n"
            "    const renderer = new Renderer(project);\n"
            "    const base = project.meta.getFullRenderingSettings();\n"
            "    await renderer.render({\n"
            "      ...base,\n"
            f"      name: {json.dumps(scene_name)},\n"
            f"      fps: {preset['fps']},\n"
            f"      size: new Vector2({preset['width']}, {preset['height']}),\n"
            "      resolutionScale: 1,\n"
            "      exporter: {\n"
            "        name: '@motion-canvas/ffmpeg',\n"
            "        options: {fastStart: true, includeAudio: false},\n"
            "      },\n"
            "    });\n"
            "    setStatus('success', {message: 'render complete'});\n"
            "  } catch (error) {\n"
            "    setStatus('error', {message: error?.message || String(error), stack: error?.stack || ''});\n"
            "  }\n"
            "}\n\n"
            "export function index(projects) {\n"
            "  if (projects && projects[0]) {\n"
            "    window.location.pathname = `/${projects[0].url}`;\n"
            "    return;\n"
            "  }\n"
            "  setStatus('error', {message: 'No Motion Canvas project found.'});\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    editor_html.write_text(
        "<!doctype html>\n"
        "<html>\n"
        "  <head>\n"
        "    <meta charset=\"utf-8\" />\n"
        "    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />\n"
        "    <link rel=\"stylesheet\" href=\"{{style}}\" />\n"
        "  </head>\n"
        "  <body>\n"
        "    <pre id=\"status\">{\"status\":\"booting\"}</pre>\n"
        "    <script type=\"module\" src=\"{{source}}\"></script>\n"
        "  </body>\n"
        "</html>\n",
        encoding="utf-8",
    )
    editor_css.write_text(
        "html, body {\n"
        "  margin: 0;\n"
        "  background: #0b1220;\n"
        "  color: #d6e3ff;\n"
        "  font: 14px ui-monospace, Consolas, monospace;\n"
        "}\n"
        "#status {\n"
        "  padding: 12px;\n"
        "  white-space: pre-wrap;\n"
        "}\n",
        encoding="utf-8",
    )
    editor_main_path = editor_main.resolve().as_posix()
    (work_dir / "vite.config.ts").write_text(
        "import {defineConfig} from 'vite';\n"
        "import * as motionCanvasModule from '@motion-canvas/vite-plugin';\n"
        "import * as ffmpegModule from '@motion-canvas/ffmpeg';\n\n"
        "function normalizePluginFactory(mod: unknown) {\n"
        "  const candidate = (typeof mod === 'function')\n"
        "    ? mod\n"
        "    : (mod && typeof (mod as {default?: unknown}).default === 'function')\n"
        "      ? (mod as {default: unknown}).default\n"
        "      : (mod\n"
        "          && (mod as {default?: {default?: unknown}}).default\n"
        "          && typeof (mod as {default: {default?: unknown}}).default.default === 'function')\n"
        "        ? (mod as {default: {default: unknown}}).default.default\n"
        "        : null;\n"
        "  if (!candidate) {\n"
        "    throw new TypeError('Motion Canvas plugin module did not expose a callable factory.');\n"
        "  }\n"
        "  return candidate as (...args: unknown[]) => unknown;\n"
        "}\n\n"
        "const motionCanvas = normalizePluginFactory(motionCanvasModule);\n"
        "const ffmpeg = normalizePluginFactory(ffmpegModule);\n\n"
        "export default defineConfig({\n"
        "  plugins: [\n"
        "    ...motionCanvas({\n"
        "      project: './src/project.ts',\n"
        "      output: './output',\n"
        f"      editor: {json.dumps(editor_main_path)},\n"
        "    }),\n"
        "    ffmpeg(),\n"
        "  ],\n"
        "  logLevel: 'error',\n"
        "});\n",
        encoding="utf-8",
    )


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(sock.getsockname()[1])


def _start_vite_server(work_dir: Path, port: int, log_path: Path) -> subprocess.Popen[str]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            _find_binary("npm") or "npm",
            "exec",
            "vite",
            "--",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--strictPort",
        ],
        cwd=str(work_dir),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(_read_log_preview(log_path) or "Motion Canvas dev server exited early.")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return process
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("Timed out while starting the Motion Canvas dev server.")


async def _run_browser_render(*, url: str, timeout: int, log_path: Path) -> dict[str, Any]:
    return await _run_browser_session(url=url, timeout=timeout, log_path=log_path)


async def _run_browser_preflight(*, url: str, timeout: int, log_path: Path) -> dict[str, Any]:
    return await _run_browser_session(url=url, timeout=timeout, log_path=log_path)


async def _run_browser_session(*, url: str, timeout: int, log_path: Path) -> dict[str, Any]:
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:  # pragma: no cover - protected by dependency probe
        return {"status": "error", "message": f"Playwright import failed: {exc}"}

    browser = None
    page_errors: list[str] = []
    console_errors: list[str] = []
    request_failures: list[str] = []
    try:
        async with async_playwright() as playwright:
            launch_attempts: list[tuple[str | None, str | None]] = [
                ("msedge", None),
                ("chrome", None),
                (None, _find_browser_executable()),
                (None, None),
            ]
            last_error = None
            for channel, executable in launch_attempts:
                try:
                    kwargs: dict[str, Any] = {
                        "headless": True,
                        "args": ["--enable-webgl", "--ignore-gpu-blocklist"],
                    }
                    if channel:
                        kwargs["channel"] = channel
                    if executable:
                        kwargs["executable_path"] = executable
                    browser = await playwright.chromium.launch(**kwargs)
                    break
                except Exception as exc:
                    last_error = exc
                    browser = None
            if browser is None:
                return {"status": "error", "message": f"Could not launch a Chromium browser for Motion Canvas: {last_error}"}

            page = await browser.new_page(viewport={"width": 1600, "height": 900})
            page.on("pageerror", lambda exc: page_errors.append(str(exc)))
            page.on(
                "console",
                lambda msg: console_errors.append(msg.text) if getattr(msg, "type", "") == "error" else None,
            )
            page.on(
                "requestfailed",
                lambda req: request_failures.append(
                    f"{req.url}: {(req.failure or {}).get('errorText', 'request failed')}"
                ),
            )
            await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                vite_failure = _extract_vite_failure(log_path)
                if vite_failure:
                    return {"status": "error", "message": vite_failure}
                body_text = (await page.text_content("body")) or ""
                browser_failure = _extract_browser_failure(
                    page_errors=page_errors,
                    console_errors=console_errors,
                    request_failures=request_failures,
                    body_text=body_text,
                )
                if browser_failure:
                    return {"status": "error", "message": browser_failure}
                try:
                    status = await page.evaluate("document.body.dataset.renderStatus || ''")
                except Exception:
                    status = ""
                if status in {"success", "error"}:
                    break
                await page.wait_for_timeout(500)
            else:
                vite_failure = _extract_vite_failure(log_path)
                body_text = (await page.text_content("body")) or ""
                browser_failure = _extract_browser_failure(
                    page_errors=page_errors,
                    console_errors=console_errors,
                    request_failures=request_failures,
                    body_text=body_text,
                )
                return {
                    "status": "error",
                    "message": vite_failure
                    or browser_failure
                    or body_text.strip()
                    or "Timed out waiting for Motion Canvas render status.",
                }
            raw_status = await page.text_content("#status")
            if raw_status:
                try:
                    return json.loads(raw_status)
                except json.JSONDecodeError:
                    return {"status": "error", "message": raw_status.strip()}
            return {"status": "error", "message": "Motion Canvas page returned no render status."}
    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass


def _find_rendered_video(work_dir: Path) -> Path | None:
    output_dir = work_dir / "output"
    if not output_dir.exists():
        return None
    videos = sorted(output_dir.rglob("*.mp4"), key=lambda item: item.stat().st_mtime)
    return videos[-1] if videos else None


def _ensure_export_dir(path: str | Path | None = None) -> Path:
    export_dir = Path(path) if path else Path.home() / "Documents" / "StudyTUI-Exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    return export_dir


def _copy_to_exports(video_src: Path, code: str, export_dir: str | Path | None) -> tuple[Path | None, Path | None]:
    target_dir = _ensure_export_dir(export_dir)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    base_name = f"motion_canvas_scene_{timestamp}"
    video_dest = target_dir / f"{base_name}.mp4"
    code_dest = target_dir / f"{base_name}.tsx"
    try:
        shutil.copy2(str(video_src), str(video_dest))
    except Exception:
        video_dest = None
    try:
        code_dest.write_text(code, encoding="utf-8")
    except Exception:
        code_dest = None
    return video_dest, code_dest


def _save_code_snapshot(code: str, export_dir: str | Path | None, suffix: str = "") -> str | None:
    target_dir = _ensure_export_dir(export_dir)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    path = target_dir / f"motion_canvas_scene_{timestamp}{suffix}.tsx"
    try:
        path.write_text(code, encoding="utf-8")
        return str(path)
    except Exception:
        return None


def _read_log_preview(log_path: Path) -> str:
    try:
        if not log_path.exists():
            return ""
        return log_path.read_text(encoding="utf-8", errors="replace")[-_STDERR_PREVIEW_LIMIT:]
    except Exception:
        return ""


def _extract_vite_failure(log_path: Path) -> str | None:
    preview = _read_log_preview(log_path)
    if not preview:
        return None
    markers = (
        "failed to resolve import",
        "pre-transform error",
        "internal server error",
        "error when starting dev server",
        "transform failed",
    )
    lowered = preview.lower()
    for marker in markers:
        index = lowered.rfind(marker)
        if index >= 0:
            return preview[index:].strip()
    return None


def _extract_browser_failure(
    *,
    page_errors: list[str],
    console_errors: list[str],
    request_failures: list[str],
    body_text: str,
) -> str | None:
    for message in page_errors:
        text = str(message or "").strip()
        if text:
            return f"Motion Canvas browser error: {text}"
    lowered_body = (body_text or "").lower()
    if "booting" in lowered_body:
        for message in console_errors:
            text = str(message or "").strip()
            if text:
                return f"Motion Canvas browser console error: {text}"
        for message in request_failures:
            text = str(message or "").strip()
            if text:
                return f"Motion Canvas browser request failed: {text}"
    return None


def _cleanup(work_dir: Path) -> None:
    try:
        shutil.rmtree(work_dir, ignore_errors=True)
    except Exception:
        pass
