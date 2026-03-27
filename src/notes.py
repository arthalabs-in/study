"""
Notes Manager - SQLite-backed study notes for the Study TUI.
Notes are linked to documents and searchable by tags/content.
Sensitive note titles and bodies are encrypted at rest on Windows.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import tempfile
import time
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path
import re

from src.latex_render import render_math_in_text
from src.secure_storage import decrypt_text, encrypt_text


DB_PATH = Path.home() / ".study-tui" / "notes.db"
_DISPLAY_MATH_RE = re.compile(r"^\s*\$\$(.+?)\$\$\s*$", re.DOTALL)
_INLINE_MATH_RE = re.compile(r"^\s*\$(.+?)\$\s*$", re.DOTALL)


class NotesManager:
    """Manages persistent study notes in SQLite."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db_path = Path(db_path) if db_path else DB_PATH
        self._conn = self._init_connection(self._db_path)

    def _init_connection(self, db_path: Path) -> sqlite3.Connection:
        conn = self._connect(db_path)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            self._conn = conn
            self._init_schema()
            return conn
        except sqlite3.Error:
            try:
                conn.close()
            except Exception:
                pass
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            self._conn = conn
            self._init_schema()
            return conn

    @staticmethod
    def _connect(db_path: Path) -> sqlite3.Connection:
        try:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            return sqlite3.connect(str(db_path))
        except sqlite3.Error:
            return sqlite3.connect(":memory:")

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS notes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id      TEXT,
                page        INTEGER,
                title       TEXT    NOT NULL,
                content     TEXT    NOT NULL,
                tags        TEXT    DEFAULT '[]',
                created_at  REAL    NOT NULL,
                updated_at  REAL    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_notes_doc ON notes(doc_id);
            CREATE INDEX IF NOT EXISTS idx_notes_title ON notes(title);
        """)

    @staticmethod
    def _encode_tags(tags: list[str] | None) -> str:
        return json.dumps(tags or [])

    @staticmethod
    def _decode_tags(value: str) -> list[str]:
        try:
            parsed = json.loads(value or "[]")
        except Exception:
            return []
        return [str(tag) for tag in parsed if str(tag).strip()]

    @staticmethod
    def _normalize_tags(tags: list[str] | None) -> list[str]:
        cleaned: list[str] = []
        for tag in tags or []:
            value = str(tag).strip()
            if value and value not in cleaned:
                cleaned.append(value)
        return cleaned

    @staticmethod
    def _candidate_pdf_fonts() -> list[Path]:
        fonts_dir = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        return [
            fonts_dir / "segoeui.ttf",
            fonts_dir / "arial.ttf",
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
            Path("/Library/Fonts/Arial Unicode.ttf"),
            Path("/Library/Fonts/Arial.ttf"),
        ]

    @classmethod
    def _find_unicode_pdf_font(cls) -> Path | None:
        for candidate in cls._candidate_pdf_fonts():
            if candidate.exists():
                return candidate
        return None

    @staticmethod
    def _render_note_text(value: str) -> str:
        return render_math_in_text(str(value or ""))

    @staticmethod
    def _candidate_tex_dirs() -> list[Path]:
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
        return [path for path in dirs if path.exists()]

    @staticmethod
    def _find_tex_engine() -> str | None:
        for name in ("pdflatex", "xelatex", "lualatex", "latex"):
            found = shutil.which(name)
            if found:
                return found
            binary_name = f"{name}.exe" if os.name == "nt" else name
            for directory in NotesManager._candidate_tex_dirs():
                candidate = directory / binary_name
                if candidate.exists():
                    return str(candidate)
        return None

    @staticmethod
    def _is_math_only_block(value: str) -> tuple[bool, str, bool]:
        text = str(value or "").strip()
        if not text:
            return False, "", False
        display = _DISPLAY_MATH_RE.fullmatch(text)
        if display:
            return True, display.group(1).strip(), True
        inline = _INLINE_MATH_RE.fullmatch(text)
        if inline:
            return True, inline.group(1).strip(), False
        return False, "", False

    @classmethod
    def _render_latex_block_image(cls, latex: str, *, display: bool) -> Path | None:
        try:
            import fitz  # type: ignore
        except Exception:
            return None
        tex_engine = cls._find_tex_engine()
        if not tex_engine:
            return None

        work_dir = Path(tempfile.mkdtemp(prefix="study-tui-note-latex-"))
        tex_path = work_dir / "snippet.tex"
        pdf_path = work_dir / "snippet.pdf"
        png_path = work_dir / "snippet.png"
        body = f"$$\n{latex}\n$$" if display else f"${latex}$"
        tex_source = (
            "\\documentclass{article}\n"
            "\\usepackage[margin=0.3in]{geometry}\n"
            "\\pagestyle{empty}\n"
            "\\begin{document}\n"
            f"{body}\n"
            "\\end{document}\n"
        )
        try:
            tex_path.write_text(tex_source, encoding="utf-8")
            completed = subprocess.run(
                [tex_engine, "-interaction=nonstopmode", "-halt-on-error", str(tex_path.name)],
                cwd=str(work_dir),
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0 or not pdf_path.exists():
                return None
            document = fitz.open(str(pdf_path))
            page = document.load_page(0)
            pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
            pix.save(str(png_path))
            document.close()
            return png_path if png_path.exists() else None
        except Exception:
            return None

    @staticmethod
    def _pdf_safe_text(value: str, unicode_font: bool) -> str:
        text = str(value or "")
        if unicode_font:
            return text

        replacements = {
            "•": "- ",
            "–": "-",
            "—": "-",
            "−": "-",
            "’": "'",
            "“": '"',
            "”": '"',
            "…": "...",
            "Δ": "Delta",
            "δ": "delta",
            "λ": "lambda",
            "μ": "mu",
            "π": "pi",
            "∑": "sum ",
            "√": "sqrt",
            "⁄": "/",
            "⁰": "^0",
            "¹": "^1",
            "²": "^2",
            "³": "^3",
            "⁴": "^4",
            "⁵": "^5",
            "⁶": "^6",
            "⁷": "^7",
            "⁸": "^8",
            "⁹": "^9",
            "⁺": "+",
            "⁻": "-",
            "⁼": "=",
            "₀": "_0",
            "₁": "_1",
            "₂": "_2",
            "₃": "_3",
            "₄": "_4",
            "₅": "_5",
            "₆": "_6",
            "₇": "_7",
            "₈": "_8",
            "₉": "_9",
            "₊": "+",
            "₋": "-",
            "₌": "=",
            "ᵢ": "_i",
            "ₓ": "_x",
            "°": " deg",
            "×": "x",
            "÷": "/",
        }
        for src, dest in replacements.items():
            text = text.replace(src, dest)
        normalized = unicodedata.normalize("NFKD", text)
        return normalized.encode("ascii", "ignore").decode("ascii")

    # CRUD

    def save_note(
        self,
        title: str,
        content: str,
        doc_id: str | None = None,
        page: int | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        """Create a new note. Returns the note with its ID."""
        clean_title = str(title or "").strip()
        clean_content = str(content or "").strip()
        clean_tags = self._normalize_tags(tags)
        if not clean_title:
            return {"error": "Note title cannot be empty"}
        if not clean_content:
            return {"error": "Note content cannot be empty"}

        now = time.time()
        cur = self._conn.execute(
            """INSERT INTO notes (doc_id, page, title, content, tags, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (doc_id, page, encrypt_text(clean_title), encrypt_text(clean_content), self._encode_tags(clean_tags), now, now),
        )
        self._conn.commit()
        return {
            "id": cur.lastrowid,
            "title": clean_title,
            "doc_id": doc_id,
            "page": page,
            "tags": clean_tags,
            "status": "saved",
        }

    def list_notes(self, doc_id: str | None = None, tag: str | None = None, limit: int = 20) -> list[dict]:
        """List notes, optionally filtered by doc_id or tag."""
        query = "SELECT * FROM notes"
        params: list = []
        conditions = []

        if doc_id:
            conditions.append("doc_id = ?")
            params.append(doc_id)
        if tag:
            conditions.append("tags LIKE ?")
            params.append(f'%"{tag}"%')

        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def search_notes(self, query: str, limit: int = 10) -> list[dict]:
        """Search decrypted note titles and content in Python."""
        rows = self._conn.execute("SELECT * FROM notes ORDER BY updated_at DESC").fetchall()
        query_lower = query.lower()
        matches: list[dict] = []
        for row in rows:
            note = self._row_to_dict(row)
            haystacks = [note.get("title", ""), note.get("content", "")]
            if any(query_lower in value.lower() for value in haystacks):
                matches.append(note)
            if len(matches) >= limit:
                break
        return matches

    def delete_note(self, note_id: int) -> dict:
        """Delete a note by ID."""
        cur = self._conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        self._conn.commit()
        if cur.rowcount > 0:
            return {"deleted": note_id}
        return {"error": f"Note {note_id} not found"}

    def get_note(self, note_id: int) -> dict | None:
        """Get a single note by ID."""
        row = self._conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        return self._row_to_dict(row) if row else None

    # Export

    def export_notes_markdown(self, path: str | None = None, doc_id: str | None = None) -> dict:
        """Export notes as a Markdown file."""
        notes = self.list_notes(doc_id=doc_id, limit=1000)
        if not notes:
            return {"error": "No notes to export"}

        lines = ["# Study Notes\n"]
        lines.append(f"*Exported {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n")

        for n in notes:
            lines.append(f"## {self._render_note_text(n['title'])}\n")
            if n.get("doc_id"):
                lines.append(f"*Document: {n['doc_id']}*")
                if n.get("page"):
                    lines[-1] += f" | *Page {n['page']}*"
                lines.append("")
            if n.get("tags"):
                lines.append(f"Tags: {', '.join(n['tags'])}\n")
            lines.append(self._render_note_text(n["content"]))
            lines.append("\n---\n")

        export_dir = Path(path) if path else Path.home() / "Documents" / "StudyTUI-Exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        out_file = export_dir / f"notes_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        out_file.write_text("\n".join(lines), encoding="utf-8")
        return {"exported": str(out_file), "count": len(notes)}

    def export_notes_pdf(
        self,
        path: str | None = None,
        doc_id: str | None = None,
        note_id: int | None = None,
    ) -> dict:
        """Export notes as a PDF file using FPDF."""
        if note_id is not None:
            note = self.get_note(note_id)
            if not note:
                return {"error": f"Note {note_id} not found"}
            notes = [note]
        else:
            notes = self.list_notes(doc_id=doc_id, limit=1000)
        if not notes:
            return {"error": "No notes to export"}

        try:
            from fpdf import FPDF
        except ImportError:
            return {"error": "PDF export requires fpdf2. Install with: pip install fpdf2"}

        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)

        font_family = "Helvetica"
        unicode_font = False
        font_path = self._find_unicode_pdf_font()
        if font_path and hasattr(pdf, "add_font"):
            try:
                pdf.add_font("StudyTUI", fname=str(font_path))
                font_family = "StudyTUI"
                unicode_font = True
            except Exception:
                font_family = "Helvetica"
                unicode_font = False

        pdf.add_page()
        pdf.set_font(font_family, size=18)
        title = "Study Note" if note_id is not None else "Study Notes"
        pdf.cell(0, 10, self._pdf_safe_text(title, unicode_font), new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.set_font(font_family, size=10)
        pdf.cell(0, 8, self._pdf_safe_text(f"Exported {datetime.now().strftime('%Y-%m-%d %H:%M')}", unicode_font), new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.ln(5)

        for n in notes:
            pdf.set_font(font_family, size=14)
            pdf.cell(0, 10, self._pdf_safe_text(self._render_note_text(n["title"]), unicode_font), new_x="LMARGIN", new_y="NEXT")
            if n.get("doc_id"):
                pdf.set_font(font_family, size=9)
                meta = f"Document: {n['doc_id']}"
                if n.get("page"):
                    meta += f" | Page {n['page']}"
                pdf.cell(0, 6, self._pdf_safe_text(meta, unicode_font), new_x="LMARGIN", new_y="NEXT")
            if n.get("tags"):
                pdf.set_font(font_family, size=9)
                pdf.cell(0, 6, self._pdf_safe_text(f"Tags: {', '.join(n['tags'])}", unicode_font), new_x="LMARGIN", new_y="NEXT")
            pdf.set_font(font_family, size=11)
            pdf.ln(2)
            self._write_pdf_note_body(
                pdf,
                str(n["content"]),
                unicode_font=unicode_font,
            )
            pdf.ln(3)
            pdf.set_draw_color(200, 200, 200)
            pdf.line(10, pdf.get_y(), 200, pdf.get_y())
            pdf.ln(5)

        export_dir = Path(path) if path else Path.home() / "Documents" / "StudyTUI-Exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        prefix = f"note_{note_id}" if note_id is not None else "notes"
        out_file = export_dir / f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        try:
            pdf.output(str(out_file))
        except Exception as e:
            return {"error": f"Failed to export notes PDF: {e}"}
        return {"exported": str(out_file), "count": len(notes), "format": "pdf"}

    def _write_pdf_note_body(self, pdf, content: str, *, unicode_font: bool) -> None:
        for raw_block in str(content or "").split("\n"):
            block = raw_block.rstrip()
            if not block.strip():
                pdf.ln(4)
                continue
            is_math_only, latex, display = self._is_math_only_block(block)
            if is_math_only and hasattr(pdf, "image"):
                image_path = self._render_latex_block_image(latex, display=display)
                if image_path and image_path.exists():
                    try:
                        self._reset_pdf_cursor(pdf)
                        render_width = min(140, self._effective_pdf_width(pdf))
                        pdf.image(str(image_path), w=render_width)
                        self._reset_pdf_cursor(pdf)
                        pdf.ln(2)
                        continue
                    except Exception:
                        pass
            self._reset_pdf_cursor(pdf)
            pdf.multi_cell(
                self._effective_pdf_width(pdf),
                6,
                self._pdf_safe_text(self._render_note_text(block), unicode_font),
            )

    @staticmethod
    def _effective_pdf_width(pdf) -> float:
        value = getattr(pdf, "epw", None)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
        width = getattr(pdf, "w", None)
        left = getattr(pdf, "l_margin", None)
        right = getattr(pdf, "r_margin", None)
        if all(isinstance(v, (int, float)) for v in (width, left, right)):
            computed = float(width) - float(left) - float(right)
            if computed > 0:
                return computed
        return 180.0

    @staticmethod
    def _reset_pdf_cursor(pdf) -> None:
        if hasattr(pdf, "set_x"):
            left = getattr(pdf, "l_margin", 10)
            try:
                pdf.set_x(left)
            except Exception:
                return

    # Internal

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        d["title"] = decrypt_text(d.get("title", ""))
        d["content"] = decrypt_text(d.get("content", ""))
        d["tags"] = self._decode_tags(d.get("tags", "[]"))
        ts = d.get("created_at", 0)
        d["created"] = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else ""
        return d

    def close(self) -> None:
        self._conn.close()
