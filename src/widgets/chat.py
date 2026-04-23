"""
Chat View — Midnight Terminal aesthetic.
Single-column chat with warm amber/gold AI accent on deep navy.
Streaming, markdown rendering, interactive quiz mode.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

from src.latex_render import render_math_in_text
from src.widgets.mascot_art import mascot_lines, MASCOT_WIDTH

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Input, RichLog, OptionList
from textual.widgets.option_list import Option
from textual.widget import Widget
from textual.timer import Timer
from rich.align import Align
from rich import box
from rich.columns import Columns
from rich.console import Group
from rich.table import Table
from rich.text import Text


# ── Color palette ──────────────────────────────────────────────────
# Midnight Terminal — Ayu Dark inspired

AMBER = "#ffb454"         # AI accent: warm gold
TEAL = "#59c2ff"          # User accent
SAGE = "#c2d94c"          # Success
ROSE = "#f07178"          # Error
LAVENDER = "#d4bfff"      # System / thinking
DIM = "#4d5566"           # Muted text
TEXT = "#e6e1cf"          # Primary text
TEXT_DIM = "#b3b1ad"      # Secondary text


def _truncate_middle(value: str, limit: int = 44) -> str:
    value = str(value or "").strip()
    if len(value) <= limit:
        return value
    keep = max(8, (limit - 1) // 2)
    return f"{value[:keep]}…{value[-keep:]}"


# ── Slash commands for autocomplete ───────────────────────────────

SLASH_COMMANDS = [
    ("/load",       "Open file picker"),
    ("/docs",       "List loaded documents"),
    ("/page",       "View a specific page"),
    ("/quiz",       "Start interactive quiz"),
    ("/review",     "Review saved flashcards"),
    ("/flashcards", "Generate flashcards"),
    ("/summary",    "Summarize loaded files"),
    ("/animate",    "Render a concept animation"),
    ("/study-now",  "Get personalized study recommendation"),
    ("/drill",      "Targeted weak-area drill"),
    ("/study-setup","Personalize study flow"),
    ("/study-prefs","Show study preferences"),
    ("/reset-profile","Reset adaptive data"),
    ("/q",          "Quote paragraphs from last response"),
    ("/theme",      "Pick or switch UI theme"),
    ("/web",        "Pick web search state"),
    ("/privacy",    "Pick remote document privacy mode"),
    ("/privacy-approve", "Allow remote doc access this session"),
    ("/key",        "Set or update API key"),
    ("/provider",   "Pick or switch AI provider"),
    ("/model",      "Pick or switch model"),
    ("/export-privacy", "Pick export privacy mode"),
    ("/continue",   "Resume previous session"),
    ("/resume",     "Pick a session to resume"),
    ("/new",        "Start new chat session"),
    ("/history",    "Browse recent sessions"),
    ("/approve",    "Approve pending write"),
    ("/deny",       "Deny pending write"),
    ("/clear",      "Clear current chat"),
    ("/copy",       "Copy last response"),
    ("/usage",      "Show token and context usage"),
    ("/context",    "Inspect prompt-state context"),
    ("/compact",    "Compact older prompt-state"),
    ("/docdir",     "Set documents folder"),
    ("/calibre-dir","Set Calibre library path"),
    ("/zotero-webhook", "Manage Zotero webhook"),
    ("/help",       "Show help"),
]

PICKER_COMMANDS = {"/provider", "/theme", "/resume", "/history", "/web", "/model", "/privacy", "/export-privacy"}


# ── Markdown → Rich Text conversion ───────────────────────────────

def _md_line(line: str, prefix: str = "  │ ") -> Text:
    """Convert a markdown-ish line to styled Rich Text with LaTeX rendering."""
    # Render any $...$ or $$...$$ math expressions to Unicode
    line = render_math_in_text(line)
    stripped = line.strip()

    # Horizontal rule
    if stripped in ("---", "___", "***", "----"):
        return Text(f"  {'━' * 46}", style=DIM)

    # Headings
    if stripped.startswith("### "):
        t = Text(style=TEXT)
        t.append(f"  │ ", style=DIM)
        t.append(stripped[4:], style=f"bold {TEXT}")
        return t
    if stripped.startswith("## "):
        t = Text(style=AMBER)
        t.append(f"  │ ", style=f"{AMBER}")
        t.append(stripped[3:], style=f"bold {AMBER}")
        return t
    if stripped.startswith("# "):
        t = Text(style=AMBER)
        t.append(f"  │ ", style=f"{AMBER}")
        t.append(stripped[2:], style=f"bold underline {AMBER}")
        return t

    # Empty line
    if not stripped:
        return Text(f"  │", style=f"{AMBER}")

    # Bold: **text**
    if "**" in line:
        text = Text(style=TEXT_DIM)
        text.append(prefix, style=f"{AMBER}")
        parts = re.split(r"(\*\*.*?\*\*)", line)
        for part in parts:
            if part.startswith("**") and part.endswith("**"):
                text.append(part[2:-2], style=f"bold {TEXT}")
            else:
                text.append(part)
        return text

    # Italic: *text*
    if re.search(r"(?<!\*)\*(?!\*)(.*?)(?<!\*)\*(?!\*)", line):
        text = Text(style=TEXT_DIM)
        text.append(prefix, style=f"{AMBER}")
        parts = re.split(r"(?<!\*)\*(?!\*)(.*?)(?<!\*)\*(?!\*)", line)
        for i, part in enumerate(parts):
            if i % 2 == 1:
                text.append(part, style="italic")
            else:
                text.append(part)
        return text

    # List items: indent with subtle marker
    if stripped.startswith("- ") or stripped.startswith("* "):
        t = Text(style=TEXT_DIM)
        t.append(prefix, style=f"{AMBER}")
        t.append("▸ ", style=f"{AMBER}")
        t.append(stripped[2:])
        return t

    # Numbered items
    num_match = re.match(r"^(\d+)\.\s+(.*)", stripped)
    if num_match:
        t = Text(style=TEXT_DIM)
        t.append(prefix, style=f"{AMBER}")
        t.append(f"{num_match.group(1)}. ", style=f"bold {AMBER}")
        t.append(num_match.group(2))
        return t

    # Default
    t = Text(style=TEXT_DIM)
    t.append(prefix, style=f"{AMBER}")
    t.append(line)
    return t

def _parse_flashcards(text: str) -> tuple[list[str], list[tuple[str, str]], list[str]] | None:
    marker_matches = list(re.finditer(r"(?is)\[flashcards\](.*?)\[/flashcards\]", text))
    for marker_match in reversed(marker_matches):
        intro_lines = [line.rstrip() for line in text[:marker_match.start()].splitlines() if line.strip()]
        outro_lines = [line.rstrip() for line in text[marker_match.end():].splitlines() if line.strip()]
        parsed_inside = _parse_flashcards(marker_match.group(1).strip())
        if parsed_inside:
            inner_intro, cards, inner_outro = parsed_inside
            return intro_lines + inner_intro, cards, inner_outro + outro_lines

    lines = text.splitlines()
    bullet_prefix = r"(?:[-*•▸▶►◇◆]\s*|\d+[\.\)]\s*)?"
    q_pattern = re.compile(rf"^{bullet_prefix}Q:\s+(.*)", re.IGNORECASE)
    a_pattern = re.compile(rf"^{bullet_prefix}A:\s+(.*)", re.IGNORECASE)
    numbered_pattern = re.compile(r"^(\d+)[\.\)]\s+(.*)")
    outro_starters = ("if ", "i can ", "let me ", "want me ", "you can ", "next, ", "next:")

    def is_question_line(stripped: str) -> bool:
        return bool(q_pattern.match(stripped) or numbered_pattern.match(stripped))

    def parse_qa_cards() -> tuple[list[str], list[tuple[str, str]], list[str]]:
        intro: list[str] = []
        cards: list[tuple[str, str]] = []
        i = 0
        while i < len(lines):
            stripped = lines[i].strip()
            if q_pattern.match(stripped):
                break
            if stripped:
                intro.append(lines[i].rstrip())
            i += 1

        first_card_index = i

        stop_cards = False
        while i < len(lines):
            q_match = q_pattern.match(lines[i].strip())
            if not q_match:
                i += 1
                continue

            question_parts = [q_match.group(1).strip()]
            i += 1
            while i < len(lines):
                stripped = lines[i].strip()
                if a_pattern.match(stripped) or q_pattern.match(stripped):
                    break
                if stripped:
                    question_parts.append(stripped)
                i += 1

            if i >= len(lines):
                break

            a_match = a_pattern.match(lines[i].strip())
            if not a_match:
                continue

            answer_parts = [a_match.group(1).strip()]
            i += 1
            while i < len(lines):
                stripped = lines[i].strip()
                if is_question_line(stripped):
                    break
                if not stripped:
                    j = i + 1
                    while j < len(lines) and not lines[j].strip():
                        j += 1
                    if j >= len(lines) or is_question_line(lines[j].strip()):
                        i = j
                        break
                    if answer_parts and lines[j].strip().lower().startswith(outro_starters):
                        i = j
                        stop_cards = True
                        break
                    i += 1
                    continue
                if stripped:
                    answer_parts.append(stripped)
                i += 1

            question = " ".join(part for part in question_parts if part).strip()
            answer = " ".join(part for part in answer_parts if part).strip()
            if question and answer:
                cards.append((question, answer))
            if stop_cards:
                break
        outro = [line.rstrip() for line in lines[i:] if line.strip()] if cards else []
        if cards and first_card_index > 0:
            intro = [line.rstrip() for line in lines[:first_card_index] if line.strip()]
        return intro, cards, outro

    def parse_numbered_cards() -> tuple[list[str], list[tuple[str, str]], list[str]]:
        intro: list[str] = []
        cards: list[tuple[str, str]] = []
        i = 0
        while i < len(lines):
            stripped = lines[i].strip()
            if numbered_pattern.match(stripped):
                break
            if stripped:
                intro.append(lines[i].rstrip())
            i += 1

        first_card_index = i

        stop_cards = False
        while i < len(lines):
            match = numbered_pattern.match(lines[i].strip())
            if not match:
                i += 1
                continue

            question = re.sub(r"^Q:\s*", "", match.group(2).strip(), flags=re.IGNORECASE)
            i += 1
            answer_parts: list[str] = []
            while i < len(lines):
                stripped = lines[i].strip()
                if is_question_line(stripped):
                    break
                if not stripped:
                    j = i + 1
                    while j < len(lines) and not lines[j].strip():
                        j += 1
                    if j >= len(lines) or is_question_line(lines[j].strip()):
                        i = j
                        break
                    if answer_parts and lines[j].strip().lower().startswith(outro_starters):
                        i = j
                        stop_cards = True
                        break
                    i += 1
                    continue
                if stripped:
                    answer_parts.append(stripped.removeprefix("A:").strip())
                i += 1

            answer = " ".join(part for part in answer_parts if part).strip()
            if question and answer:
                cards.append((question, answer))
            if stop_cards:
                break

        outro = [line.rstrip() for line in lines[i:] if line.strip()] if cards else []
        if cards and first_card_index > 0:
            intro = [line.rstrip() for line in lines[:first_card_index] if line.strip()]
        return intro, cards, outro

    intro, cards, outro = parse_qa_cards()
    if len(cards) >= 2:
        return intro, cards, outro

    intro, cards, outro = parse_numbered_cards()
    question_like = sum(1 for question, _ in cards if question.rstrip().endswith("?"))
    if len(cards) >= 2 and question_like >= min(2, len(cards)):
        return intro, cards, outro
    return None

class ChatView(Widget):
    """Single-column chat with Midnight Terminal aesthetic."""

    class UserMessage(Message):
        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    class QuizFinished(Message):
        """Emitted when a quiz session ends, carrying full results."""
        def __init__(self, score: int, total: int, results: list[dict]) -> None:
            self.score = score
            self.total = total
            self.results = results
            super().__init__()

    class QuizAnswerSubmitted(Message):
        """Emitted when a numeric quiz answer needs async verification."""
        def __init__(self, quiz_index: int, question: dict, user_answer: str) -> None:
            self.quiz_index = quiz_index
            self.question = question
            self.user_answer = user_answer
            super().__init__()

    class FlashcardReviewed(Message):
        """Emitted when a persistent review card is graded."""
        def __init__(self, card: dict, grade: str) -> None:
            self.card = card
            self.grade = grade
            super().__init__()

    class FlashcardReviewFinished(Message):
        """Emitted when a persistent review session ends."""
        def __init__(self, total: int, grades: dict[str, int]) -> None:
            self.total = total
            self.grades = grades
            super().__init__()

    DEFAULT_PLACEHOLDER = "Ask anything...    /help for commands"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._stream_buffer: str = ""
        self._full_response: str = ""
        self._is_streaming: bool = False
        self._last_response: str = ""  # for /copy
        self._user_scrolled_up: bool = False  # track if user scrolled away
        self.model_label: str = "AI"  # dynamic — set by app when provider changes
        # Typing indicator
        self._typing_timer: Timer | None = None
        self._typing_frame: int = 0
        self._SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        # Quiz state
        self._quiz_active: bool = False
        self._quiz_questions: list[dict] = []
        self._quiz_index: int = 0
        self._quiz_score: int = 0
        self._quiz_answered: bool = False
        self._quiz_results: list[dict] = []
        self._quiz_grading_pending: bool = False
        self._quiz_pending_answer: str = ""
        self._quiz_pending_index: int = -1
        # Flashcard review state
        self._flashcards_active: bool = False
        self._flashcards: list[dict] = []
        self._flashcard_index: int = 0
        self._flashcard_revealed: bool = False
        self._flashcard_intro: list[str] = []
        self._flashcard_outro: list[str] = []
        self._flashcard_review_mode: bool = False
        self._flashcard_grade_counts: dict[str, int] = {"again": 0, "hard": 0, "good": 0, "easy": 0}
        self._option_mode: str = "command"
        self._picker_submit_map: dict[str, str] = {}
        self._picker_options: list[tuple[str, str, str]] = []
        self._welcome_mascot_happy: bool = False

    def compose(self) -> ComposeResult:
        yield RichLog(
            id="chat-log",
            wrap=True,
            markup=True,
            highlight=False,
            min_width=40,
        )
        with Vertical(id="input-area"):
            yield OptionList(id="cmd-suggest")
            yield Input(
                placeholder=self.DEFAULT_PLACEHOLDER,
                id="chat-input",
            )

    # ── Smart scroll helper ───────────────────────────────────────

    def _write_line(self, log: RichLog, content) -> None:
        """Write to the log, only auto-scrolling if user is near the bottom."""
        # Check if user is near the bottom (within 3 lines)
        at_bottom = log.scroll_y >= (log.max_scroll_y - 3)
        old_auto = log.auto_scroll
        log.auto_scroll = at_bottom
        log.write(content)
        log.auto_scroll = old_auto

    # ── Welcome ────────────────────────────────────────────────────

    BANNER = [
        "███████╗████████╗██╗   ██╗██████╗ ██╗   ██╗",
        "██╔════╝╚══██╔══╝██║   ██║██╔══██╗╚██╗ ██╔╝",
        "███████╗   ██║   ██║   ██║██║  ██║ ╚████╔╝ ",
        "╚════██║   ██║   ██║   ██║██║  ██║  ╚██╔╝  ",
        "███████║   ██║   ╚██████╔╝██████╔╝   ██║   ",
        "╚══════╝   ╚═╝    ╚═════╝ ╚═════╝    ╚═╝   ",
    ]

    def _set_welcome_mascot(self, *, visible: bool, happy: bool = False, compact: bool = False) -> None:
        self._welcome_mascot_happy = happy if visible else False

    def _hide_welcome_mascot(self) -> None:
        self._welcome_mascot_happy = False

    def write_welcome(self, overview: dict | None = None) -> None:
        log = self.query_one("#chat-log", RichLog)
        overview = overview or {}
        try:
            term_cols = os.get_terminal_size(fallback=(80, 24)).columns
        except Exception:
            term_cols = 80
        viewport_width = max(
            getattr(log.size, "width", 0),
            getattr(self.size, "width", 0),
            term_cols,
            80,
        )
        available_width = max(72, viewport_width - 4)
        compact_mode = available_width < 120
        narrow_mode = available_width < 60
        self._set_welcome_mascot(visible=False, compact=compact_mode)

        banner_group = Group(*(Text(line, style=f"bold {AMBER}") for line in self.BANNER))
        alpha_badge = Text()
        alpha_badge.append("⟦", style=DIM)
        alpha_badge.append(" alpha ", style=f"bold {LAVENDER} on #131a24")
        alpha_badge.append("⟧", style=DIM)

        log.write(Text(" "))
        log.write(Align.center(banner_group, width=available_width))
        log.write(Align.center(alpha_badge, width=available_width))

        provider = str(overview.get("provider") or "not set")
        model = str(overview.get("model") or self.model_label or "AI")
        docs_dir = str(overview.get("documents_dir") or "Not configured")
        loaded_docs = list(overview.get("loaded_documents") or [])
        recent_sessions = list(overview.get("recent_sessions") or [])

        left = Table.grid(padding=(0, 0))
        left.add_column()

        def _kv(label: str, value: str, accent: str = TEXT) -> Text:
            line = Text()
            line.append(f"{label:<12}", style=DIM)
            line.append(_truncate_middle(value), style=f"bold {accent}")
            return line

        left.add_row(Text("Workspace", style=f"bold {SAGE}"))
        left.add_row(_kv("provider", provider, TEAL))
        left.add_row(_kv("model", model, AMBER))
        left.add_row(_kv("doc dir", docs_dir))
        if loaded_docs:
            left.add_row(_kv("loaded", ", ".join(_truncate_middle(doc, 18) for doc in loaded_docs[:2]), TEAL))
        else:
            left.add_row(_kv("loaded", "none", TEXT_DIM))
        left.add_row(Text(" "))
        left.add_row(Text("Recent Sessions", style=f"bold {LAVENDER}"))
        if recent_sessions:
            for session in recent_sessions[:4]:
                title = _truncate_middle(str(session.get("title") or "Untitled"), 30)
                count = int(session.get("messages") or 0)
                line = Text("• ", style=TEAL)
                line.append(title, style=TEXT)
                line.append(f" · {count} msgs", style=DIM)
                left.add_row(line)
        else:
            left.add_row(Text("No saved sessions yet", style=DIM))

        right = Table.grid(padding=(0, 0))
        right.add_column()
        right.add_row(Text("Core Workflows", style=f"bold {SAGE}"))
        features = [
            ("/load", "Open PDFs or images and ground the chat in them."),
            ("/quiz", "Run interactive quiz mode with grading and weak-point tracking."),
            ("/flashcards", "Generate cards, then keep reviewing them persistently."),
            ("/review", "Resume saved review queues tied to the document hash."),
            ("/summary", "Condense chapters, notes, and extracted context fast."),
            ("/animate", "Render concept animations with Manim when installed."),
            ("notes/export", "Save notes, export PDF/Anki, and send PDFs to Calibre or Zotero."),
            ("progress", "Track grasp, weak topics, and personalized study memory."),
        ]
        for name, desc in features:
            row = Text()
            row.append(f"{name:<14}", style=f"bold {SAGE}")
            row.append(desc, style=DIM)
            right.add_row(row)

        if compact_mode:
            shell_width = min(available_width, 96)
            shell = Table(
                box=box.ROUNDED,
                show_header=False,
                expand=False,
                width=shell_width,
                padding=(0, 2),
                border_style=DIM,
            )
            shell.add_column(ratio=1, overflow="fold")
            shell.add_row(left)
            shell.add_section()
            shell.add_row(right)
        else:
            shell_width = min(available_width, max(118, int(available_width * 0.82)))
            shell = Table(
                box=box.ROUNDED,
                show_header=False,
                expand=False,
                width=shell_width,
                padding=(1, 2),
                border_style=DIM,
            )
            shell.add_column(ratio=10, overflow="fold")
            shell.add_column(ratio=14, overflow="fold")
            shell.add_row(left, right)

        shell.title = f"[bold {AMBER}]Study Workspace[/]"
        shell.caption = f"[{DIM}]/help for full commands[/]"
        log.write(Text(" "))
        log.write(Align.center(shell, width=available_width))

        log.write(Text(" "))
        quick = Text()
        quick.append("resume ", style=DIM)
        quick.append("/resume", style=f"bold {TEAL}")
        quick.append(" · change doc folder ", style=DIM)
        quick.append("/docdir", style=f"bold {TEAL}")
        quick.append(" · full command list ", style=DIM)
        quick.append("/help", style=f"bold {TEAL}")
        log.write(Align.center(quick, width=available_width))
        tip = Text()
        tip.append("tip ", style=DIM)
        tip.append("Shift + mouse drag", style=f"bold {TEAL}")
        tip.append(" to select text from the transcript", style=DIM)
        log.write(Align.center(tip, width=available_width))
        log.write(Text(" "))

    # ── Message rendering ──────────────────────────────────────────

    def add_user_message(self, text: str) -> None:
        self._hide_welcome_mascot()
        log = self.query_one("#chat-log", RichLog)
        log.write(Text(" "))
        t = Text()
        t.append("  ❯ ", style=f"bold {TEAL}")
        t.append(text, style=f"bold {TEXT}")
        log.write(t)

    def add_assistant_message(self, text: str) -> None:
        self._hide_welcome_mascot()
        parsed_flashcards = _parse_flashcards(text)
        if parsed_flashcards:
            self._add_flashcards_message(text, parsed_flashcards)
            return

        log = self.query_one("#chat-log", RichLog)
        log.write(Text(" "))
        t = Text()
        t.append("  ◆ ", style=f"bold {AMBER}")
        t.append(self.model_label, style=f"bold {AMBER}")
        log.write(t)
        for line in text.split("\n"):
            log.write(_md_line(line))
        log.write(Text(" "))
        self._last_response = text

    def _add_flashcards_message(
        self,
        raw_text: str,
        parsed: tuple[list[str], list[tuple[str, str]], list[str]],
    ) -> None:
        self._hide_welcome_mascot()
        intro_lines, cards, outro_lines = parsed
        log = self.query_one("#chat-log", RichLog)
        log.write(Text(" "))

        header = Text()
        header.append("  ◆ ", style=f"bold {AMBER}")
        header.append(self.model_label, style=f"bold {AMBER}")
        log.write(header)

        for line in intro_lines:
            log.write(_md_line(line))
        if intro_lines:
            log.write(Text(" "))

        for index, (question, answer) in enumerate(cards, start=1):
            title = Text()
            title.append("  ┌─ ", style=f"{AMBER}")
            title.append(f"Card {index}", style=f"bold {AMBER}")
            self._write_line(log, title)

            q_line = Text()
            q_line.append("  │ ", style=f"{AMBER}")
            q_line.append("Q ", style=f"bold {AMBER}")
            q_line.append(render_math_in_text(question), style=TEXT)
            self._write_line(log, q_line)

            hint_line = Text()
            hint_line.append("  │ ", style=f"{DIM}")
            hint_line.append("answer hidden — run /flashcards to review or ask me to export these cards.", style=DIM)
            self._write_line(log, hint_line)

            self._write_line(log, Text(f"  └{'─' * 42}", style=DIM))
            if index != len(cards):
                self._write_line(log, Text(" "))
        if outro_lines:
            log.write(Text(" "))
            for line in outro_lines:
                log.write(_md_line(line))
        log.write(Text(" "))
        self._last_response = raw_text

    def start_flashcards(
        self,
        cards: list[dict],
        intro_lines: list[str] | None = None,
        outro_lines: list[str] | None = None,
        review_mode: bool = False,
    ) -> None:
        self._hide_welcome_mascot()
        self._flashcards_active = True
        self._flashcards = list(cards)
        self._flashcard_index = 0
        self._flashcard_revealed = False
        self._flashcard_intro = list(intro_lines or [])
        self._flashcard_outro = list(outro_lines or [])
        self._flashcard_review_mode = review_mode
        self._flashcard_grade_counts = {"again": 0, "hard": 0, "good": 0, "easy": 0}

        log = self.query_one("#chat-log", RichLog)
        log.write(Text(" "))
        header = Text()
        header.append(f"  ━━━━━ ", style=f"{AMBER}")
        header.append("🗂 FLASHCARDS", style=f"bold {AMBER}")
        header.append(f" · {len(cards)} cards ", style=f"{DIM}")
        header.append("━━━━━━━━━━━━━━━━━━", style=f"{AMBER}")
        log.write(header)
        for line in self._flashcard_intro:
            log.write(_md_line(line))
        if self._flashcard_review_mode:
            log.write(Text("  press enter ↵ to reveal · 1 again · 2 hard · 3 good · 4 easy · p for previous · /quit to exit", style=DIM))
        else:
            log.write(Text("  press enter ↵ to reveal · next card after reveal · p for previous · /quit to exit", style=DIM))

        inp = self.query_one("#chat-input", Input)
        inp.placeholder = "enter ↵ to reveal"
        self._show_flashcard()

    def _show_flashcard(self) -> None:
        if self._flashcard_index >= len(self._flashcards):
            self._finish_flashcards()
            return

        card = self._flashcards[self._flashcard_index]
        question = render_math_in_text(str(card.get("question", "")).strip())
        answer = render_math_in_text(str(card.get("answer", "")).strip())
        log = self.query_one("#chat-log", RichLog)

        log.write(Text(" "))
        title = Text()
        title.append("  ┌─ ", style=f"{AMBER}")
        title.append(f"Card {self._flashcard_index + 1}/{len(self._flashcards)}", style=f"bold {AMBER}")
        log.write(title)

        q_line = Text()
        q_line.append("  │ ", style=f"{AMBER}")
        q_line.append("Q ", style=f"bold {AMBER}")
        q_line.append(question, style=TEXT)
        log.write(q_line)

        if self._flashcard_revealed:
            a_line = Text()
            a_line.append("  │ ", style=f"{TEAL}")
            a_line.append("A ", style=f"bold {TEAL}")
            a_line.append(answer, style=TEXT_DIM)
            log.write(a_line)
            if self._flashcard_review_mode:
                log.write(Text("  │ rate it: 1 again · 2 hard · 3 good · 4 easy", style=DIM))
            log.write(Text("  └──────────────────────────────────────────", style=DIM))
        else:
            log.write(Text("  │ answer hidden — press enter ↵ to reveal", style=DIM))
            log.write(Text("  └──────────────────────────────────────────", style=DIM))

        inp = self.query_one("#chat-input", Input)
        if not self._flashcard_revealed:
            inp.placeholder = "enter ↵ to reveal"
        elif self._flashcard_review_mode:
            inp.placeholder = "1 again · 2 hard · enter/3 good · 4 easy"
        else:
            inp.placeholder = "enter ↵ for next card"

    def _handle_flashcard_input(self, text: str) -> None:
        command = text.strip().lower()
        if command == "/quit":
            self._finish_flashcards()
            return
        if command in {"p", "prev", "previous", "back"}:
            if self._flashcard_index > 0:
                self._flashcard_index -= 1
            self._flashcard_revealed = False
            self._show_flashcard()
            return
        if not self._flashcard_revealed:
            self._flashcard_revealed = True
            self._show_flashcard()
            return
        if self._flashcard_review_mode:
            grade_map = {
                "": "good",
                "1": "again",
                "a": "again",
                "again": "again",
                "2": "hard",
                "h": "hard",
                "hard": "hard",
                "3": "good",
                "g": "good",
                "good": "good",
                "4": "easy",
                "e": "easy",
                "easy": "easy",
            }
            grade = grade_map.get(command)
            if not grade:
                log = self.query_one("#chat-log", RichLog)
                log.write(Text("  │ use 1/2/3/4 or again/hard/good/easy", style=DIM))
                return
            self._flashcard_grade_counts[grade] = self._flashcard_grade_counts.get(grade, 0) + 1
            self.post_message(self.FlashcardReviewed(dict(self._flashcards[self._flashcard_index]), grade))
        self._flashcard_index += 1
        self._flashcard_revealed = False
        self._show_flashcard()

    def _finish_flashcards(self) -> None:
        log = self.query_one("#chat-log", RichLog)
        if self._flashcard_outro:
            log.write(Text(" "))
            for line in self._flashcard_outro:
                log.write(_md_line(line))
        self._flashcards_active = False
        self._flashcards = []
        self._flashcard_index = 0
        self._flashcard_revealed = False
        self._flashcard_intro = []
        self._flashcard_outro = []
        review_mode = self._flashcard_review_mode
        review_total = sum(self._flashcard_grade_counts.values())
        review_grades = dict(self._flashcard_grade_counts)
        self._flashcard_review_mode = False
        self._flashcard_grade_counts = {"again": 0, "hard": 0, "good": 0, "easy": 0}
        inp = self.query_one("#chat-input", Input)
        inp.placeholder = self.DEFAULT_PLACEHOLDER
        log.write(Text("  ✓ Flashcard review complete.", style=f"{SAGE}"))
        if review_mode and review_total:
            summary = Text("  ↳ ")
            summary.append(f"again {review_grades['again']}", style=ROSE)
            summary.append(" · ", style=DIM)
            summary.append(f"hard {review_grades['hard']}", style=AMBER)
            summary.append(" · ", style=DIM)
            summary.append(f"good {review_grades['good']}", style=TEAL)
            summary.append(" · ", style=DIM)
            summary.append(f"easy {review_grades['easy']}", style=SAGE)
            log.write(summary)
            self.post_message(self.FlashcardReviewFinished(review_total, review_grades))

    def add_system_message(self, text: str) -> None:
        log = self.query_one("#chat-log", RichLog)
        log.write(Text(f"  {text}", style=LAVENDER))

    def add_tool_start(self, text: str) -> None:
        log = self.query_one("#chat-log", RichLog)
        t = Text()
        t.append("  ◇ ", style=DIM)
        t.append(text, style=DIM)
        self._write_line(log, t)

    def add_tool_done(self, text: str) -> None:
        log = self.query_one("#chat-log", RichLog)
        t = Text()
        t.append("  ◆ ", style=f"{SAGE}")
        t.append(text, style=f"{SAGE}")
        self._write_line(log, t)

    # ── Reasoning / thinking trace ─────────────────────────────────

    def start_thinking(self) -> None:
        """Show the start of a reasoning trace section."""
        log = self.query_one("#chat-log", RichLog)
        self._write_line(log, Text(" "))
        t = Text()
        t.append("  ◆ ", style=f"bold {LAVENDER}")
        t.append(self.model_label, style=f"bold {AMBER}")
        self._write_line(log, t)
        t2 = Text()
        t2.append("  │ ", style=LAVENDER)
        t2.append("🧠 Reasoning...", style=f"italic {LAVENDER}")
        self._write_line(log, t2)
        self._thinking_buffer = ""
        self._is_thinking = True

    def stream_thinking_token(self, token: str) -> None:
        """Stream a thinking/reasoning token."""
        if not getattr(self, "_is_thinking", False):
            return
        self._thinking_buffer += token
        while "\n" in self._thinking_buffer:
            line, self._thinking_buffer = self._thinking_buffer.split("\n", 1)
            log = self.query_one("#chat-log", RichLog)
            t = Text()
            t.append("  │ ", style=LAVENDER)
            t.append(line, style=f"italic {DIM}")
            self._write_line(log, t)

    def end_thinking(self) -> None:
        """Close the reasoning trace section."""
        if not getattr(self, "_is_thinking", False):
            return
        log = self.query_one("#chat-log", RichLog)
        if self._thinking_buffer.strip():
            t = Text()
            t.append("  │ ", style=LAVENDER)
            t.append(self._thinking_buffer, style=f"italic {DIM}")
            self._write_line(log, t)
        t2 = Text()
        t2.append("  └─── ", style=LAVENDER)
        t2.append("done thinking", style=f"italic {DIM}")
        self._write_line(log, t2)
        self._thinking_buffer = ""
        self._is_thinking = False

    def add_error(self, text: str) -> None:
        log = self.query_one("#chat-log", RichLog)
        t = Text()
        t.append("  ✦ ", style=f"bold {ROSE}")
        t.append(text, style=f"{ROSE}")
        log.write(t)

    def add_info_block(self, title: str, lines: list[str]) -> None:
        log = self.query_one("#chat-log", RichLog)
        log.write(Text(" "))
        # Top line
        t = Text()
        t.append(f"  ┌─ ", style=f"{TEAL}")
        t.append(title, style=f"bold {TEAL}")
        log.write(t)
        # Body
        for line in lines:
            t = Text()
            t.append("  │ ", style=f"{TEAL}")
            t.append(line, style=TEXT_DIM)
            log.write(t)
        log.write(Text(f"  └{'─' * 42}", style=DIM))
        log.write(Text(" "))

    # ── Typing indicator ──────────────────────────────────────────

    def show_typing(self) -> None:
        """Show animated typing indicator in the input bar."""
        inp = self.query_one("#chat-input", Input)
        inp.disabled = True
        self._typing_frame = 0
        inp.placeholder = f"{self._SPINNER[0]}  {self.model_label} is thinking..."
        self._typing_timer = self.set_interval(0.08, self._animate_typing)

    def _animate_typing(self) -> None:
        """Cycle the spinner in the input placeholder."""
        self._typing_frame = (self._typing_frame + 1) % len(self._SPINNER)
        char = self._SPINNER[self._typing_frame]
        try:
            inp = self.query_one("#chat-input", Input)
            inp.placeholder = f"{char}  {self.model_label} is thinking..."
        except Exception:
            pass

    def hide_typing(self) -> None:
        """Stop typing indicator and restore input."""
        if self._typing_timer:
            self._typing_timer.stop()
            self._typing_timer = None
        try:
            inp = self.query_one("#chat-input", Input)
            inp.disabled = False
            inp.placeholder = self.DEFAULT_PLACEHOLDER
        except Exception:
            pass

    # ── Streaming ──────────────────────────────────────────────────

    def start_response(self) -> None:
        self._hide_welcome_mascot()
        self.hide_typing()
        log = self.query_one("#chat-log", RichLog)
        self._write_line(log, Text(" "))
        t = Text()
        t.append("  ◆ ", style=f"bold {AMBER}")
        t.append(self.model_label, style=f"bold {AMBER}")
        self._write_line(log, t)
        self._stream_buffer = ""
        self._full_response = ""
        self._is_streaming = True

    def stream_token(self, token: str) -> None:
        if not self._is_streaming:
            return
        self._stream_buffer += token
        self._full_response += token
        while "\n" in self._stream_buffer:
            line, self._stream_buffer = self._stream_buffer.split("\n", 1)
            log = self.query_one("#chat-log", RichLog)
            self._write_line(log, _md_line(line))

    def end_response(self) -> str:
        self.hide_typing()  # safety: ensure indicator is gone
        log = self.query_one("#chat-log", RichLog)
        if self._stream_buffer.strip():
            self._write_line(log, _md_line(self._stream_buffer))
        self._write_line(log, Text(" "))
        self._is_streaming = False
        # Snap to bottom after response is fully done
        log.auto_scroll = True
        log.scroll_end(animate=False)
        full = self._full_response
        self._last_response = full
        self._stream_buffer = ""
        self._full_response = ""
        return full

    # ── Interactive Quiz ───────────────────────────────────────────

    def start_quiz(self, questions: list[dict]) -> None:
        self._quiz_active = True
        self._quiz_questions = questions
        self._quiz_index = 0
        self._quiz_score = 0
        self._quiz_answered = False
        self._quiz_results = []

        log = self.query_one("#chat-log", RichLog)
        log.write(Text(" "))
        # Elegant header line
        t = Text()
        t.append(f"  ━━━━━ ", style=f"{AMBER}")
        t.append("📝 QUIZ", style=f"bold {AMBER}")
        t.append(f" · {len(questions)} questions ", style=f"{DIM}")
        t.append("━━━━━━━━━━━━━━━━━━━━━", style=f"{AMBER}")
        log.write(t)
        log.write(Text(f"  type /quit to exit", style=DIM))

        inp = self.query_one("#chat-input", Input)
        inp.placeholder = "Your answer..."
        self._show_question()

    def _show_question(self) -> None:
        if self._quiz_index >= len(self._quiz_questions):
            self._finish_quiz()
            return

        q = self._quiz_questions[self._quiz_index]
        log = self.query_one("#chat-log", RichLog)
        num = self._quiz_index + 1
        total = len(self._quiz_questions)
        qtype = q.get("type", "mcq").upper()

        log.write(Text(" "))
        # Question header
        t = Text()
        t.append(f"  ◆ ", style=f"bold {TEAL}")
        t.append(f"Q{num}/{total}", style=f"bold {TEAL}")
        t.append(f"  [{qtype}]", style=DIM)
        log.write(t)
        log.write(Text(" "))

        # Question text
        for line in q["question"].split("\n"):
            log.write(Text(f"    {line}", style=f"{TEXT}"))

        # Options for MCQ
        if q.get("options"):
            log.write(Text(" "))
            for opt in q["options"]:
                t = Text()
                t.append("    ")
                # Highlight the letter
                if opt and opt[0].isalpha() and len(opt) > 1 and opt[1] == ")":
                    t.append(opt[:2], style=f"bold {TEAL}")
                    t.append(opt[2:], style=TEXT_DIM)
                else:
                    t.append(opt, style=TEXT_DIM)
                log.write(t)

        log.write(Text(" "))
        qtype_raw = q.get("type", "mcq")
        if qtype_raw == "mcq":
            hint = "enter a, b, c, or d"
        elif qtype_raw == "numeric":
            hint = "type the numeric answer"
        else:
            hint = "type your answer"
        log.write(Text(f"    {hint}", style=DIM))

    def _handle_quiz_input(self, answer: str) -> None:
        if answer.lower() == "/quit":
            self._finish_quiz()
            return

        if self._quiz_grading_pending:
            return

        if self._quiz_answered:
            self._quiz_answered = False
            self._quiz_index += 1
            self._show_question()
            return

        q = self._quiz_questions[self._quiz_index]
        log = self.query_one("#chat-log", RichLog)

        correct_answer = q.get("answer", "").strip().lower()
        user_answer = answer.strip().lower()
        qtype = q.get("type", "mcq")

        if qtype == "numeric":
            self._quiz_grading_pending = True
            self._quiz_pending_answer = answer.strip()
            self._quiz_pending_index = self._quiz_index
            inp = self.query_one("#chat-input", Input)
            inp.disabled = True
            inp.placeholder = "Checking numeric answer..."
            log.write(Text(" "))
            log.write(Text("  ◇ Checking your numeric answer...", style=f"{DIM}"))
            self.post_message(self.QuizAnswerSubmitted(self._quiz_index, q, answer.strip()))
            return

        is_correct = False
        if qtype == "mcq":
            correct_letter = correct_answer[0] if correct_answer else ""
            user_letter = user_answer[0] if user_answer else ""
            is_correct = (user_letter == correct_letter)
        else:
            is_correct = self._fuzzy_match(user_answer, correct_answer)

        self._finalize_quiz_answer(q, answer.strip(), is_correct)

    def complete_pending_numeric_answer(
        self,
        quiz_index: int,
        is_correct: bool,
        feedback: str = "",
    ) -> None:
        if (
            not self._quiz_active
            or not self._quiz_grading_pending
            or quiz_index != self._quiz_pending_index
            or quiz_index != self._quiz_index
        ):
            return

        inp = self.query_one("#chat-input", Input)
        inp.disabled = False
        inp.placeholder = "Your answer..."

        q = self._quiz_questions[self._quiz_index]
        pending_answer = self._quiz_pending_answer
        self._quiz_grading_pending = False
        self._quiz_pending_answer = ""
        self._quiz_pending_index = -1
        self._finalize_quiz_answer(q, pending_answer, is_correct, feedback=feedback)

    def _finalize_quiz_answer(
        self,
        q: dict,
        user_answer: str,
        is_correct: bool,
        feedback: str = "",
    ) -> None:
        log = self.query_one("#chat-log", RichLog)

        self._quiz_results.append({
            "question": q["question"],
            "type": q.get("type", "mcq"),
            "correct": is_correct,
            "user_answer": user_answer,
            "expected_answer": q.get("answer", ""),
            "explanation": q.get("explanation", ""),
            "grading_feedback": feedback,
        })

        log.write(Text(" "))
        if is_correct:
            self._quiz_score += 1
            log.write(Text(f"  ◆ Correct!", style=f"bold {SAGE}"))
        else:
            log.write(Text(f"  ✦ Incorrect", style=f"bold {ROSE}"))
            ans_display = q.get("answer", "")
            log.write(Text(f"    → {ans_display}", style=f"{AMBER}"))

        # Explanation
        explanation = q.get("explanation", "")
        if explanation:
            log.write(Text(" "))
            for line in explanation.split("\n"):
                log.write(Text(f"    {line}", style=DIM))
        if feedback:
            log.write(Text(" "))
            for line in feedback.split("\n"):
                if line.strip():
                    log.write(Text(f"    {line}", style=DIM))

        # Progress bar — thin amber/dim gradient
        done = self._quiz_index + 1
        total = len(self._quiz_questions)
        bar_width = 24
        filled = int((done / total) * bar_width)
        bar = "▰" * filled + "▱" * (bar_width - filled)
        log.write(Text(" "))

        t = Text()
        t.append(f"  {bar} ", style=f"{AMBER}")
        t.append(f"{done}/{total}", style=DIM)
        t.append(f"  score: {self._quiz_score}/{done}", style=f"{TEAL}")
        log.write(t)
        log.write(Text("  press enter ↵", style=DIM))

        self._quiz_answered = True

    def _fuzzy_match(self, user: str, correct: str) -> bool:
        user_norm = self._normalize_quiz_text(user)
        correct_norm = self._normalize_quiz_text(correct)
        if not user_norm or not correct_norm:
            return False
        if user_norm == correct_norm:
            return True
        if len(user_norm) >= 5 and (user_norm in correct_norm or correct_norm in user_norm):
            return True

        ratio = self._similarity_ratio(user_norm, correct_norm)
        if ratio >= 0.84:
            return True

        correct_words = set(correct_norm.split())
        user_words = set(user_norm.split())
        if not correct_words:
            return False
        overlap = correct_words & user_words
        return len(overlap) / len(correct_words) >= 0.75

    @staticmethod
    def _normalize_quiz_text(value: str) -> str:
        value = value.lower().strip()
        value = re.sub(r"[^a-z0-9\s.%-]", " ", value)
        value = re.sub(r"\s+", " ", value)
        return value.strip()

    @staticmethod
    def _similarity_ratio(left: str, right: str) -> float:
        distance = ChatView._levenshtein_distance(left, right)
        return 1.0 - (distance / max(len(left), len(right), 1))

    @staticmethod
    def _levenshtein_distance(left: str, right: str) -> int:
        if left == right:
            return 0
        if not left:
            return len(right)
        if not right:
            return len(left)

        previous = list(range(len(right) + 1))
        for i, left_char in enumerate(left, start=1):
            current = [i]
            for j, right_char in enumerate(right, start=1):
                insert_cost = current[j - 1] + 1
                delete_cost = previous[j] + 1
                replace_cost = previous[j - 1] + (left_char != right_char)
                current.append(min(insert_cost, delete_cost, replace_cost))
            previous = current
        return previous[-1]

    def _finish_quiz(self) -> None:
        log = self.query_one("#chat-log", RichLog)
        total = len(self._quiz_questions)
        score = self._quiz_score
        answered = min(self._quiz_index + 1, total) if self._quiz_answered else self._quiz_index
        pct = int((score / max(answered, 1)) * 100)

        log.write(Text(" "))
        # Results header
        t = Text()
        t.append(f"  ━━━━━ ", style=f"{AMBER}")
        t.append("📊 RESULTS", style=f"bold {AMBER}")
        t.append(f" ━━━━━━━━━━━━━━━━━━━━━━━━", style=f"{AMBER}")
        log.write(t)
        log.write(Text(" "))

        # Score display
        t = Text()
        t.append(f"  {score}/{answered}", style=f"bold {TEXT}")
        t.append(f"  ({pct}%)", style=f"{AMBER}")
        log.write(t)

        # Grade
        if pct >= 90:
            log.write(Text("  🌟 outstanding", style=f"bold {SAGE}"))
        elif pct >= 70:
            log.write(Text("  ✓ solid work", style=f"{SAGE}"))
        elif pct >= 50:
            log.write(Text("  ◇ review the gaps", style=f"{AMBER}"))
        else:
            log.write(Text("  ✦ keep grinding", style=f"{ROSE}"))

        log.write(Text(" "))

        results = list(self._quiz_results)

        self._quiz_active = False
        self._quiz_questions = []
        self._quiz_index = 0
        self._quiz_score = 0
        self._quiz_answered = False
        self._quiz_results = []
        self._quiz_grading_pending = False
        self._quiz_pending_answer = ""
        self._quiz_pending_index = -1

        inp = self.query_one("#chat-input", Input)
        inp.disabled = False
        inp.placeholder = "Ask anything...    /help for commands"

        self.post_message(self.QuizFinished(score, answered, results))

    # ── Input handling ─────────────────────────────────────────────

    def show_nested_picker(self, prompt: str, options: list[tuple[str, str, str]]) -> None:
        """Show a contextual picker whose selection submits a command."""
        self._option_mode = "picker"
        self._picker_options = list(options)

        inp = self.query_one("#chat-input", Input)
        inp.value = ""
        inp.placeholder = prompt
        inp.focus()
        self._show_picker_matches("")

    def _show_picker_matches(self, query: str) -> None:
        ol = self.query_one("#cmd-suggest", OptionList)
        normalized = query.strip().lower()
        ol.clear_options()
        self._picker_submit_map = {}

        for option_id, label, submit_text in self._picker_options:
            haystacks = (label.lower(), submit_text.lower(), option_id.lower())
            if normalized and not any(normalized in hay for hay in haystacks):
                continue
            self._picker_submit_map[option_id] = submit_text
            ol.add_option(Option(label, id=option_id))

        if ol.option_count == 0:
            ol.display = False
            return

        ol.display = True
        ol.styles.height = min(max(ol.option_count + 2, 3), 8)
        ol.highlighted = 0

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "chat-input":
            try:
                ol = self.query_one("#cmd-suggest", OptionList)
                if ol.display and ol.highlighted is not None:
                    event.prevent_default()
                    if self._option_mode == "picker":
                        self._submit_selected_option()
                    else:
                        self._complete_suggestion()
                    return
            except Exception:
                pass

            text = event.value.strip()
            event.input.value = ""
            self._hide_suggestions()

            if self._flashcards_active:
                self._handle_flashcard_input(text)
            elif self._quiz_active:
                self._handle_quiz_input(text)
            elif text:
                self.post_message(self.UserMessage(text))

    def on_input_changed(self, event: Input.Changed) -> None:
        """Show/hide autocomplete suggestions as user types."""
        if event.input.id != "chat-input":
            return
        text = event.value
        if self._option_mode == "picker":
            if text.startswith("/") and not self._quiz_active and not self._flashcards_active:
                self._hide_suggestions()
                self._show_suggestions(text)
                return
            self._show_picker_matches(text)
            return
        if text.startswith("/") and not self._quiz_active and not self._flashcards_active:
            self._show_suggestions(text)
        else:
            self._hide_suggestions()

    def _show_suggestions(self, prefix: str) -> None:
        """Filter and display matching slash commands."""
        ol = self.query_one("#cmd-suggest", OptionList)
        matches = [
            (cmd, desc) for cmd, desc in SLASH_COMMANDS
            if cmd.startswith(prefix) or prefix == "/"
        ]
        if not matches:
            self._hide_suggestions()
            return
        if len(matches) == 1 and matches[0][0] == prefix and prefix not in PICKER_COMMANDS:
            self._hide_suggestions()
            return

        self._option_mode = "command"
        self._picker_submit_map = {}
        ol.clear_options()
        for cmd, desc in matches:
            ol.add_option(Option(f"{cmd:14s} {desc}", id=cmd))

        ol.display = True
        ol.styles.height = min(max(len(matches) + 2, 3), 8)
        if ol.option_count > 0:
            ol.highlighted = 0

    def _hide_suggestions(self) -> None:
        """Hide the autocomplete dropdown."""
        was_picker = self._option_mode == "picker"
        self._option_mode = "command"
        self._picker_submit_map = {}
        self._picker_options = []
        try:
            ol = self.query_one("#cmd-suggest", OptionList)
            ol.display = False
        except Exception:
            pass
        if was_picker:
            try:
                inp = self.query_one("#chat-input", Input)
                if not self._quiz_active and not inp.disabled:
                    inp.placeholder = self.DEFAULT_PLACEHOLDER
            except Exception:
                pass

    def _complete_suggestion(self) -> None:
        """Fill input with the highlighted suggestion or launch a nested picker."""
        try:
            ol = self.query_one("#cmd-suggest", OptionList)
            if not ol.display or ol.highlighted is None:
                return
            opt = ol.get_option_at_index(ol.highlighted)
            cmd = opt.id
            inp = self.query_one("#chat-input", Input)
            self._hide_suggestions()
            inp.focus()
            if cmd in PICKER_COMMANDS:
                inp.value = ""
                self.post_message(self.UserMessage(cmd))
                return
            inp.value = cmd + " "
            inp.cursor_position = len(inp.value)
        except Exception:
            pass

    def _submit_selected_option(self) -> None:
        """Submit the currently highlighted picker option."""
        try:
            ol = self.query_one("#cmd-suggest", OptionList)
            if not ol.display or ol.highlighted is None:
                return
            opt = ol.get_option_at_index(ol.highlighted)
            submit_text = self._picker_submit_map.get(opt.id or "", "")
            self._hide_suggestions()
            inp = self.query_one("#chat-input", Input)
            inp.focus()
            if submit_text:
                self.post_message(self.UserMessage(submit_text))
        except Exception:
            pass

    def _submit_exact_picker_command(self) -> bool:
        """Launch a nested picker when the input already contains an exact picker command."""
        try:
            inp = self.query_one("#chat-input", Input)
        except Exception:
            return False
        cmd = inp.value.strip()
        if self._quiz_active or self._flashcards_active or cmd not in PICKER_COMMANDS:
            return False
        inp.value = ""
        self.post_message(self.UserMessage(cmd))
        return True

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Handle click on a suggestion or nested picker option."""
        if event.option_list.id != "cmd-suggest":
            return
        if self._option_mode == "picker":
            submit_text = self._picker_submit_map.get(event.option.id or "", "")
            self._hide_suggestions()
            inp = self.query_one("#chat-input", Input)
            inp.focus()
            if submit_text:
                self.post_message(self.UserMessage(submit_text))
            return
        cmd = event.option.id
        inp = self.query_one("#chat-input", Input)
        self._hide_suggestions()
        inp.focus()
        if cmd in PICKER_COMMANDS:
            inp.value = ""
            self.post_message(self.UserMessage(cmd))
            return
        inp.value = cmd + " "
        inp.cursor_position = len(inp.value)

    def on_key(self, event) -> None:
        """Handle Tab and arrow keys for autocomplete navigation."""
        ol = self.query_one("#cmd-suggest", OptionList)

        if event.key == "tab" and not ol.display:
            if self._submit_exact_picker_command():
                event.prevent_default()
                event.stop()
            return

        if not ol.display:
            return

        if event.key in {"tab", "enter"}:
            event.prevent_default()
            event.stop()
            if self._option_mode == "picker":
                self._submit_selected_option()
            else:
                self._complete_suggestion()
        elif event.key == "down":
            event.prevent_default()
            event.stop()
            if ol.highlighted is not None and ol.highlighted < ol.option_count - 1:
                ol.highlighted = ol.highlighted + 1
        elif event.key == "up":
            event.prevent_default()
            event.stop()
            if ol.highlighted is not None and ol.highlighted > 0:
                ol.highlighted = ol.highlighted - 1
        elif event.key == "escape":
            event.prevent_default()
            event.stop()
            self._hide_suggestions()

    # ── Utils ──────────────────────────────────────────────────────

    @property
    def quiz_active(self) -> bool:
        return self._quiz_active

    @property
    def flashcards_active(self) -> bool:
        return self._flashcards_active

    def clear_log(self) -> None:
        log = self.query_one("#chat-log", RichLog)
        log.clear()
        self._hide_welcome_mascot()

    def focus_input(self) -> None:
        try:
            self.query_one("#chat-input", Input).focus()
        except Exception:
            pass










