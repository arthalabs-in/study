# Manim Animation Engine — Design Doc

**Goal**: Let the AI study agent generate 3Blue1Brown-style animated video explanations of concepts from the user's study material, using [Manim Community Edition](https://www.manim.community/) as a deterministic, code-based rendering engine.

**Tagline**: *"Your AI tutor creates 3Blue1Brown-style animations of the concepts you're struggling with."*

---

## Why This Feature

| Dimension | Value |
|-----------|-------|
| **Differentiator** | No study tool generates deterministic, code-based animated explanations. NotebookLM generates opaque AI video — Manim produces inspectable, modifiable Python code. |
| **Seamless fit** | Plugs into the existing tool-calling agent pipeline. One new module, one new tool, minimal wiring. |
| **Study loop synergy** | Weak topics from `study_progress.py` drive *what* gets animated — personalized visual explanations for concepts the user fails on. |
| **Viral potential** | "Study any PDF → struggle with a concept → your AI tutor renders a 3B1B-style animation to explain it" is a front-page-of-HN headline. |

---

## Architecture

```
User says "animate eigenvalues"   ─or─   Agent auto-suggests after quiz failure
         │
         ▼
┌─────────────────────────────────┐
│  Agent calls `animate_concept`  │  (tool in tools.py)
│  with topic + style + context   │
└──────────┬──────────────────────┘
           │
           ▼
┌─────────────────────────────────┐
│  Agent writes Manim Python code │  (agent writes the Scene class)
│  returned via tool `code` param │
└──────────┬──────────────────────┘
           │
           ▼
┌─────────────────────────────────┐
│  manim_renderer.py              │  (NEW module)
│  1. Validate imports (whitelist)│
│  2. Write code to temp .py      │
│  3. subprocess: manim render    │
│  4. Timeout: 120s max           │
│  5. Capture .mp4 output         │
│  6. Copy to exports directory   │
│  7. Return result or error      │
└──────────┬──────────────────────┘
           │
           ▼
     ┌─────┴─────┐
     │  Success   │ → {"status": "rendered", "video_path": "...", "code": "..."}
     │  Error     │ → {"status": "error", "error": "...", "attempt": N, "max_attempts": 3}
     └───────────┘
           │ on error, agent auto-retries with fixed code (up to 2 retries)
           │ after 3rd failure → user sees clear failure message
```

### Key Design Decisions

1. **The LLM writes the Manim code, not templates.** This gives maximum creative freedom — the agent can animate anything from eigenvalue decomposition to fluid dynamics. Templates are too rigid.

2. **Robust error recovery with retries.** See [Error Handling](#error-handling) below for the full flow.

3. **Approval required.** Animation rendering executes arbitrary code in a subprocess. Like `save_note` and `export_content`, the tool requires explicit user approval before execution. Approval is requested once — retries after the initial approval do NOT re-prompt.

4. **Code is saved alongside video.** The `.py` source is saved next to the `.mp4` so the user can inspect, modify, and re-render it manually. This is the "deterministic, not AI slop" philosophy.

5. **User-driven quality correction.** After a successful render, the user can see the video and ask the agent to improve it ("make the axes labels bigger", "slow down the rotation"). The agent edits the code and re-renders — this is a new tool call, not a retry, so it goes through normal approval flow.

---

## Error Handling

This is the core robustness mechanism. Three layers of error defense:

### Layer 1: Pre-execution validation (instant, no render cost)

- **Import whitelist** — regex scan rejects code with blocked imports *before* any subprocess is spawned
- **Scene class detection** — rejects code that doesn't define a `Scene` subclass
- **Empty/trivial code** — rejects blank or suspiciously short code

If pre-validation fails, the error is returned immediately with a clear message. The agent can fix and retry.

### Layer 2: Render retry loop (automatic, up to 3 attempts)

```
Attempt 1: Agent writes code → render
  ├─ Success → done, video saved ✓
  └─ Fail → error + full stderr returned to agent
           ↓
Attempt 2: Agent rewrites code with error context → render
  ├─ Success → done, video saved ✓
  └─ Fail → error + full stderr returned to agent
           ↓
Attempt 3 (final): Agent rewrites code → render
  ├─ Success → done, video saved ✓
  └─ Fail → HARD STOP:
           User sees: "⚠️ Animation failed after 3 attempts.
                       Error: [specific error message]
                       The last attempted code has been saved to [path].
                       You can edit it manually and run: manim render -ql [file]"
```

The retry loop is managed inside `agent_manager.py`, NOT inside `manim_renderer.py`. The renderer is a single-shot executor. The agent manager:

1. Calls `render_animation` with the agent's code
2. If it fails, feeds the error back to the agent as a tool result with `{"retry": true, "attempt": N, "error": "..."}` 
3. The agent sees the error and generates fixed code in its next tool call
4. After the 3rd failure, returns a user-facing error with the saved `.py` file path

**What the user sees during retries:**
- `🎬 Rendering animation... (attempt 1/3)`
- `⚠️ Render failed, agent is fixing the code... (attempt 2/3)`
- `⚠️ Render failed, agent is retrying... (attempt 3/3)`
- On final failure: `❌ Animation failed after 3 attempts. [error details + saved code path]`

### Layer 3: User-driven quality correction (post-render)

After a successful render, the user might not be satisfied with the output. The correction flow:

```
User: "The animation is too fast, slow it down"
  → Agent reads the previously saved .py source
  → Agent modifies the code (adjusts run_time, adds Wait, etc.)
  → Agent calls animate_concept with updated code
  → Normal approval + render cycle
  → New .mp4 replaces or coexists with the old one
```

This is NOT automatic — it's just the user chatting normally. The agent already has the code context from the previous tool result, so it can make targeted edits. No special infrastructure needed.

---

## Proposed Changes

### New Module

#### [NEW] `src/manim_renderer.py`

Core rendering engine. Responsibilities:
- **Import validation**: Whitelist of allowed imports (`manim`, `numpy`, `math`). Blocks `os`, `sys`, `subprocess`, `shutil`, `pathlib` writes, `socket`, `http`, etc.
- **Scene file generation**: Writes validated code to a temp `.py` file.
- **Subprocess execution**: Runs `manim render -ql scene.py <SceneName>` with:
  - 120-second timeout
  - Temp working directory (isolated)
  - `stdout`/`stderr` captured for error reporting
- **Output collection**: Locates the rendered `.mp4` from Manim's media output directory, copies to the exports folder.
- **Cleanup**: Removes temp directory after render.

Public API:
```python
@dataclass
class RenderResult:
    success: bool
    video_path: str | None    # absolute path to .mp4
    code_path: str | None     # absolute path to saved .py source
    error: str | None         # error message if failed
    duration_seconds: float   # wall-clock render time
    scene_name: str           # detected Scene class name

async def render_animation(
    code: str,
    *,
    export_dir: str | Path | None = None,
    quality: str = "low",         # low/medium/high
    timeout: int = 120,
) -> RenderResult:
```

---

### Tool Layer

#### [MODIFY] `src/agents/tools.py`

Add `ANIMATION_TOOLS`:

```python
ANIMATION_TOOLS = [
    {
        "name": "animate_concept",
        "description": (
            "Generate a 3Blue1Brown-style animated video explaining a concept. "
            "Write a complete Manim Community Edition Python scene in the 'code' parameter. "
            "The scene will be rendered to a video file and saved. "
            "Use this when the user asks to animate or visualize a concept, "
            "or when you detect the user is struggling with a topic and a visual explanation would help. "
            "The code must define exactly one class inheriting from Scene with a construct method. "
            "Only import from: manim, numpy, math. "
            "If rendering fails, you will receive the error and can retry with fixed code. "
            "This tool requires explicit user approval before rendering."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "The concept being animated (for labeling the output).",
                },
                "code": {
                    "type": "string",
                    "description": "Complete Manim Python code defining a Scene class.",
                },
                "quality": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": "Render quality (default: low for speed).",
                    "default": "low",
                },
            },
            "required": ["topic", "code"],
        },
    },
]
```

Add `ANIMATION_TOOLS` to `ALL_TOOLS`.

---

#### [MODIFY] `src/agents/agent_manager.py`

- Import `render_animation` from `src.manim_renderer`
- Add `animate_concept` to `_APPROVAL_REQUIRED_TOOLS`
- Add routing in `execute_tool`:

```python
elif name == "animate_concept":
    approved = await self._ensure_tool_approval(name, args)
    if not approved:
        return {"status": "denied", "error": "User denied approval for animation rendering."}
    return await self._handle_animate(args)
```

- Add `_handle_animate` method that calls `render_animation` and returns the result.
- Add status label for `animate_concept` in `_tool_status_message`.

---

#### [MODIFY] `src/app.py`

- Add `/animate` slash command as a convenience shortcut (same as how `/quiz` and `/flashcards` work — user can also just say "animate eigenvalues" in natural language)
- Include `ANIMATION_TOOLS` in the tool set sent to the provider
- Add auto-suggest hint in the system prompt after quiz failures: *"If the user struggled with a topic, consider offering to create an animated visual explanation using animate_concept."*
- Wire `default_export_dir` for animation output

---

#### [MODIFY] `src/widgets/chat.py`

- Detect animation tool results in the chat stream and render a status message: `🎬 Animation rendered: eigenvalues.mp4 (saved to ~/Documents/StudyTUI-Exports/)`
- Hyperlink or show the file path for easy access

---

#### [MODIFY] `pyproject.toml`

Add optional `animation` extra:
```toml
[project.optional-dependencies]
animation = ["manim>=0.18"]
```

---

## Security

| Concern | Mitigation |
|---------|-----------|
| **Arbitrary code execution** | Import whitelist: only `manim`, `numpy`, `math` allowed. Regex scan before execution. |
| **Resource exhaustion** | 120s subprocess timeout. Temp working directory. |
| **User consent** | Tool is in `_APPROVAL_REQUIRED_TOOLS` — explicit approve/deny before any render. |
| **File system safety** | Subprocess runs in isolated temp dir. Output copied to exports dir only. |
| **Network access** | No network-capable imports allowed. Manim itself doesn't need network. |

---

## Auto-Suggest Flow (V2 addition, wired in V1)

```
Quiz grading → weak_topics detected → agent system prompt includes:
  "The user is struggling with [topic]. If a visual/animated explanation
   would help, offer to create one using animate_concept."
→ Agent proposes: "Want me to create an animated explanation of [topic]?"
→ User approves → agent writes Manim code → render → saved
```

This requires no new infrastructure — the system prompt update and existing study progress tools handle it.

---

## Verification Plan

### Automated Tests

#### New: `tests/test_manim_renderer.py`

1. **Import validation test**: Verify that code containing blocked imports (`os`, `sys`, `subprocess`) is rejected before execution.
2. **Scene detection test**: Verify the renderer correctly identifies the Scene class name from code.
3. **Render failure handling**: Mock subprocess to simulate render failure, verify error message is returned.
4. **Render success handling**: Mock subprocess to simulate successful render, verify `RenderResult` fields.
5. **Timeout test**: Mock subprocess with a delay, verify timeout is enforced.

Run: `python -m pytest tests/test_manim_renderer.py -v`

#### Existing: `tests/test_agent_manager.py` pattern

6. **Tool routing test**: Verify `animate_concept` is routed correctly and requires approval (follow the `test_save_note_requires_approval` pattern).

Run: `python -m pytest tests/test_agent_manager.py tests/test_agent_manager_extended.py -v`

#### Existing: compile check

7. **Syntax pass**: `python -m compileall src tests scripts`

### Manual Verification

> [!IMPORTANT]  
> Requires `manim` to be installed: `pip install manim`

1. Launch Study TUI, load a PDF
2. Type: "animate the concept of a sine wave" or `/animate sine wave`
3. Verify the approval prompt appears
4. Approve → verify a `.mp4` and `.py` are saved to the exports directory
5. Open the `.mp4` — verify it contains a Manim animation
6. Open the `.py` — verify it contains valid, readable Manim code
7. Type: "animate a concept that doesn't make sense" → verify error recovery (agent retries)
