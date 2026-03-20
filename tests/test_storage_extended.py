from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

import src.chat_history as chat_history_module
import src.notes as notes_module
import src.secure_storage as secure_storage
from src.chat_history import ChatHistoryManager
from src.notes import NotesManager


def test_chat_history_bulk_save_search_title_and_close(tmp_path: Path) -> None:
    manager = ChatHistoryManager(tmp_path / "history.db")

    session_id = manager.new_session()
    manager.save(
        [
            {"role": "system", "content": "Rules"},
            {"role": "user", "content": "Explain entropy in simple words", "metadata": {"source": "test"}},
            {"role": "assistant", "content": "Entropy measures disorder."},
        ]
    )

    assert manager.session_id == session_id
    assert manager.session_title == "Explain entropy in simple words"
    assert manager.get_session_title(session_id) == "Explain entropy in simple words"
    assert manager.load_session(session_id)[1]["content"] == "Explain entropy in simple words"
    assert manager.search_messages("missing") == []
    assert manager.delete_session(session_id + 1) is False
    manager.close()


def test_chat_history_session_state_round_trip(tmp_path: Path) -> None:
    manager = ChatHistoryManager(tmp_path / "history.db")
    manager.new_session()
    state = {
        "compact_memories": [{"id": "mem_1", "summary": "Older turns", "source_count": 6}],
        "compacted_transcript_count": 4,
        "last_context_stats": {"prompt_tokens": 1234},
    }
    manager.save_session_state(state)
    loaded = manager.load_session_state(manager.session_id)
    assert loaded["compacted_transcript_count"] == 4
    assert loaded["compact_memories"][0]["id"] == "mem_1"


def test_chat_history_json_helpers_and_preview(monkeypatch, tmp_path: Path) -> None:
    manager = ChatHistoryManager(tmp_path / "history.db")

    encoded = manager._encrypt_json({"a": 1})
    assert chat_history_module.decrypt_text(encoded) == json.dumps({"a": 1})
    assert manager._decrypt_json(encoded) == {"a": 1}
    monkeypatch.setattr(chat_history_module, "decrypt_text", lambda payload: "{broken")
    assert manager._decrypt_json("anything") == {}
    assert manager._preview_title("") == "Untitled"
    assert manager._preview_title("a" * 100) == "a" * 60
    manager.close()


def test_notes_filters_delete_decode_and_close(tmp_path: Path) -> None:
    manager = NotesManager(tmp_path / "notes.db")
    first = manager.save_note("Entropy", "Tends to increase", doc_id="doc1", page=3, tags=["physics", "thermo"])
    second = manager.save_note("Momentum", "Conserved", doc_id="doc2", tags=["physics"])

    assert NotesManager._encode_tags(["a", "b"]) == '["a", "b"]'
    assert NotesManager._decode_tags("{broken") == []
    assert manager.list_notes(doc_id="doc1")[0]["id"] == first["id"]
    assert manager.list_notes(tag="thermo")[0]["id"] == first["id"]
    assert manager.get_note(second["id"])["title"] == "Momentum"
    assert manager.delete_note(second["id"]) == {"deleted": second["id"]}
    assert "not found" in manager.delete_note(9999)["error"]
    assert manager.get_note(9999) is None
    manager.close()


def test_notes_export_paths(tmp_path: Path, monkeypatch) -> None:
    manager = NotesManager(tmp_path / "notes.db")
    assert manager.export_notes_markdown(path=str(tmp_path / "exports")) == {"error": "No notes to export"}
    assert manager.export_notes_pdf(path=str(tmp_path / "exports")) == {"error": "No notes to export"}

    manager.save_note("Entropy", "Disorder rises", doc_id="doc1", page=2, tags=["physics"])
    manager.save_note("Momentum", "Vector quantity", tags=["mechanics"])

    pdf_module = type(sys)("fpdf")

    class FakeFPDF:
        def __init__(self):
            self.lines = []

        def set_auto_page_break(self, **kwargs):
            self.lines.append(("page_break", kwargs))

        def add_page(self):
            self.lines.append(("page", None))

        def set_font(self, *args, **kwargs):
            self.lines.append(("font", args, kwargs))

        def cell(self, *args, **kwargs):
            self.lines.append(("cell", args, kwargs))

        def ln(self, *args, **kwargs):
            self.lines.append(("ln", args, kwargs))

        def multi_cell(self, *args, **kwargs):
            self.lines.append(("multi", args, kwargs))

        def set_draw_color(self, *args, **kwargs):
            self.lines.append(("draw", args, kwargs))

        def line(self, *args, **kwargs):
            self.lines.append(("line", args, kwargs))

        def get_y(self):
            return 42

        def output(self, path):
            Path(path).write_text("pdf", encoding="utf-8")

    pdf_module.FPDF = FakeFPDF
    monkeypatch.setitem(sys.modules, "fpdf", pdf_module)

    markdown_result = manager.export_notes_markdown(path=str(tmp_path / "exports"), doc_id="doc1")
    assert Path(markdown_result["exported"]).read_text(encoding="utf-8").count("Entropy") == 1

    pdf_result = manager.export_notes_pdf(path=str(tmp_path / "exports"))
    assert pdf_result["format"] == "pdf"
    assert Path(pdf_result["exported"]).read_text(encoding="utf-8") == "pdf"
    manager.close()


def test_secure_storage_round_trips_and_fallbacks(monkeypatch) -> None:
    monkeypatch.setattr(secure_storage.sys, "platform", "win32")
    monkeypatch.setattr(secure_storage, "_crypt_protect", lambda data: b"wrapped:" + data)
    monkeypatch.setattr(secure_storage, "_crypt_unprotect", lambda data: data.removeprefix(b"wrapped:"))

    encrypted = secure_storage.encrypt_text("hello")
    assert encrypted.startswith("dpapi:")
    assert secure_storage.decrypt_text(encrypted) == "hello"

    monkeypatch.setattr(secure_storage, "_crypt_protect", lambda data: (_ for _ in ()).throw(RuntimeError("nope")))
    monkeypatch.setattr(secure_storage, "_get_fernet", lambda: None)
    assert secure_storage.encrypt_text("hello") == "hello"

    monkeypatch.setattr(secure_storage.sys, "platform", "linux")
    class FakeFernet:
        def encrypt(self, data: bytes) -> bytes:
            return b"enc:" + data

        def decrypt(self, data: bytes) -> bytes:
            if not data.startswith(b"enc:"):
                raise ValueError("bad token")
            return data.removeprefix(b"enc:")

    monkeypatch.setattr(secure_storage, "_get_fernet", lambda: FakeFernet())
    encrypted_linux = secure_storage.encrypt_text("plain")
    assert encrypted_linux.startswith("fernet:")
    assert secure_storage.decrypt_text(encrypted_linux) == "plain"
    assert secure_storage.decrypt_text("plain") == "plain"
    assert secure_storage.decrypt_text("dpapi:bad") == ""
    assert secure_storage.decrypt_text("fernet:bad") == ""


def test_secure_storage_blob_helpers() -> None:
    blob = secure_storage._bytes_to_blob(b"abc")
    assert secure_storage._blob_to_bytes(blob) == b"abc"
    empty_blob = secure_storage._bytes_to_blob(b"")
    assert secure_storage._blob_to_bytes(empty_blob) == b""


def test_notes_manager_falls_back_to_memory_when_disk_init_fails(monkeypatch, tmp_path: Path) -> None:
    class BrokenConn:
        def __init__(self) -> None:
            self.row_factory = None

        def execute(self, *_args, **_kwargs):
            raise notes_module.sqlite3.OperationalError("disk unavailable")

        def close(self) -> None:
            pass

    monkeypatch.setattr(NotesManager, "_connect", staticmethod(lambda _path: BrokenConn()))
    manager = NotesManager(tmp_path / "notes.db")
    note = manager.save_note("Entropy", "Disorder")
    assert note["status"] == "saved"
    assert manager.get_note(note["id"])["title"] == "Entropy"
    manager.close()


def test_chat_history_manager_falls_back_to_memory_when_disk_init_fails(monkeypatch, tmp_path: Path) -> None:
    class BrokenConn:
        def __init__(self) -> None:
            self.row_factory = None

        def execute(self, *_args, **_kwargs):
            raise chat_history_module.sqlite3.OperationalError("disk unavailable")

        def close(self) -> None:
            pass

    monkeypatch.setattr(ChatHistoryManager, "_connect", staticmethod(lambda _path: BrokenConn()))
    manager = ChatHistoryManager(tmp_path / "history.db")
    session_id = manager.new_session("Entropy")
    manager.save_message("user", "Explain entropy")
    assert manager.get_session_title(session_id) == "Entropy"
    assert manager.load_session(session_id)[0]["content"] == "Explain entropy"
    manager.close()


def test_notes_save_note_rejects_blank_and_normalizes_tags(tmp_path: Path) -> None:
    manager = NotesManager(tmp_path / "notes.db")
    assert manager.save_note("   ", "valid") == {"error": "Note title cannot be empty"}
    assert manager.save_note("Valid", "   ") == {"error": "Note content cannot be empty"}

    note = manager.save_note("  Entropy  ", "  Disorder rises  ", tags=["physics", "physics", " thermo "])
    assert note["title"] == "Entropy"
    assert note["tags"] == ["physics", "thermo"]
    manager.close()


def test_notes_export_pdf_sanitizes_unicode_without_unicode_font(tmp_path: Path, monkeypatch) -> None:
    manager = NotesManager(tmp_path / "notes.db")
    manager.save_note("Entropy - ΔS", "Bullet: •\nEquation: λ = h/p")

    pdf_module = type(sys)("fpdf")
    capture: dict[str, object] = {}

    class FakeFPDF:
        def __init__(self):
            self.lines = []
            capture["pdf"] = self

        def add_font(self, *args, **kwargs):
            self.lines.append(("add_font", args, kwargs))

        def set_auto_page_break(self, **kwargs):
            self.lines.append(("page_break", kwargs))

        def add_page(self):
            self.lines.append(("page", None))

        def set_font(self, *args, **kwargs):
            self.lines.append(("font", args, kwargs))

        def cell(self, *args, **kwargs):
            self.lines.append(("cell", args, kwargs))

        def ln(self, *args, **kwargs):
            self.lines.append(("ln", args, kwargs))

        def multi_cell(self, *args, **kwargs):
            self.lines.append(("multi", args, kwargs))

        def set_draw_color(self, *args, **kwargs):
            self.lines.append(("draw", args, kwargs))

        def line(self, *args, **kwargs):
            self.lines.append(("line", args, kwargs))

        def get_y(self):
            return 42

        def output(self, path):
            Path(path).write_text("pdf", encoding="utf-8")

    pdf_module.FPDF = FakeFPDF
    monkeypatch.setitem(sys.modules, "fpdf", pdf_module)
    monkeypatch.setattr(notes_module.NotesManager, "_find_unicode_pdf_font", classmethod(lambda cls: None))

    result = manager.export_notes_pdf(path=str(tmp_path / "exports"))
    assert result["format"] == "pdf"
    pdf = capture["pdf"]
    rendered = "\n".join(str(item) for item in pdf.lines)
    assert "DeltaS" in rendered
    assert "lambda = h/p" in rendered
    manager.close()


def test_notes_export_pdf_can_target_single_note_id(tmp_path: Path, monkeypatch) -> None:
    manager = NotesManager(tmp_path / "notes.db")
    first = manager.save_note("Entropy", "Disorder rises")
    manager.save_note("Momentum", "Mass times velocity")

    pdf_module = type(sys)("fpdf")
    capture: dict[str, object] = {}

    class FakeFPDF:
        def __init__(self):
            self.lines = []
            capture["pdf"] = self

        def add_font(self, *args, **kwargs):
            self.lines.append(("add_font", args, kwargs))

        def set_auto_page_break(self, **kwargs):
            self.lines.append(("page_break", kwargs))

        def add_page(self):
            self.lines.append(("page", None))

        def set_font(self, *args, **kwargs):
            self.lines.append(("font", args, kwargs))

        def cell(self, *args, **kwargs):
            self.lines.append(("cell", args, kwargs))

        def ln(self, *args, **kwargs):
            self.lines.append(("ln", args, kwargs))

        def multi_cell(self, *args, **kwargs):
            self.lines.append(("multi", args, kwargs))

        def set_draw_color(self, *args, **kwargs):
            self.lines.append(("draw", args, kwargs))

        def line(self, *args, **kwargs):
            self.lines.append(("line", args, kwargs))

        def get_y(self):
            return 42

        def output(self, path):
            Path(path).write_text("pdf", encoding="utf-8")

    pdf_module.FPDF = FakeFPDF
    monkeypatch.setitem(sys.modules, "fpdf", pdf_module)

    result = manager.export_notes_pdf(path=str(tmp_path / "exports"), note_id=first["id"])
    assert result["format"] == "pdf"
    assert result["count"] == 1
    assert f"note_{first['id']}_" in Path(result["exported"]).name
    rendered = "\n".join(str(item) for item in capture["pdf"].lines)
    assert "Entropy" in rendered
    assert "Momentum" not in rendered
    manager.close()


def test_notes_export_markdown_renders_latex(tmp_path: Path) -> None:
    manager = NotesManager(tmp_path / "notes.db")
    manager.save_note("Kinematics $v^2$", "Energy: $\\frac{1}{2}mv^2$ and field $$\\sum_{i=1}^{3} x_i$$")

    result = manager.export_notes_markdown(path=str(tmp_path / "exports"))
    content = Path(result["exported"]).read_text(encoding="utf-8")
    assert "Kinematics v²" in content
    assert "Energy: 1⁄2mv²" in content
    assert "∑ᵢ₌₁³ xᵢ" in content
    manager.close()


def test_notes_export_pdf_renders_latex_without_unicode_font(tmp_path: Path, monkeypatch) -> None:
    manager = NotesManager(tmp_path / "notes.db")
    manager.save_note("Kinematics $v^2$", "Energy: $\\frac{1}{2}mv^2$ and field $$\\sum_{i=1}^{3} x_i$$")

    pdf_module = type(sys)("fpdf")
    capture: dict[str, object] = {}

    class FakeFPDF:
        def __init__(self):
            self.lines = []
            capture["pdf"] = self

        def add_font(self, *args, **kwargs):
            self.lines.append(("add_font", args, kwargs))

        def set_auto_page_break(self, **kwargs):
            self.lines.append(("page_break", kwargs))

        def add_page(self):
            self.lines.append(("page", None))

        def set_font(self, *args, **kwargs):
            self.lines.append(("font", args, kwargs))

        def cell(self, *args, **kwargs):
            self.lines.append(("cell", args, kwargs))

        def ln(self, *args, **kwargs):
            self.lines.append(("ln", args, kwargs))

        def multi_cell(self, *args, **kwargs):
            self.lines.append(("multi", args, kwargs))

        def set_draw_color(self, *args, **kwargs):
            self.lines.append(("draw", args, kwargs))

        def line(self, *args, **kwargs):
            self.lines.append(("line", args, kwargs))

        def get_y(self):
            return 42

        def output(self, path):
            Path(path).write_text("pdf", encoding="utf-8")

    pdf_module.FPDF = FakeFPDF
    monkeypatch.setitem(sys.modules, "fpdf", pdf_module)
    monkeypatch.setattr(notes_module.NotesManager, "_find_unicode_pdf_font", classmethod(lambda cls: None))

    result = manager.export_notes_pdf(path=str(tmp_path / "exports"))
    assert result["format"] == "pdf"
    rendered = "\n".join(str(item) for item in capture["pdf"].lines)
    assert "Kinematics v^2" in rendered
    assert "Energy: 1/2mv^2" in rendered
    assert "sum _i=_1^3 x_i" in rendered
    manager.close()

