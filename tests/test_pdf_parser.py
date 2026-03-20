from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest
from PIL import Image

import src.parsers.pdf_parser as pdf_parser


def _make_pdf_with_text_and_figure(pdf_path: Path, image_path: Path) -> None:
    doc = fitz.open()
    page1 = doc.new_page()
    page1.insert_text(
        (72, 72),
        "SECTION 1\n\nEntropy is a measure of disorder.\n\nThis paragraph should become searchable.",
    )
    page2 = doc.new_page()
    page2.insert_text((72, 72), "FIGURES\n\nThis page contains a diagram.")
    page2.insert_image(fitz.Rect(72, 120, 320, 320), filename=str(image_path))
    doc.set_metadata({"title": "Physics Notes"})
    doc.save(str(pdf_path))
    doc.close()


def test_build_document_id_and_chunk_helpers(tmp_path: Path) -> None:
    path = tmp_path / "My Notes.pdf"
    path.write_bytes(b"pdf-bytes")
    doc_id = pdf_parser.build_document_id(path)
    assert doc_id.startswith("my_notes_")
    assert len(doc_id.split("_")[-1]) == 10

    paragraphs = pdf_parser._split_into_paragraphs("A\n\nB\n\n\nC")
    assert paragraphs == ["A", "B", "C"]

    long_para = "x" * (pdf_parser.MAX_CHUNK_CHARS + 100)
    chunks, next_idx = pdf_parser._merge_paragraphs_into_chunks(
        [long_para, "tail"],
        "doc1",
        1,
        0,
    )
    assert len(chunks) >= 2
    assert next_idx == len(chunks)
    assert all(chunk.doc_id == "doc1" for chunk in chunks)


def test_detect_figure_pages_filters_backgrounds(monkeypatch) -> None:
    class FakePage:
        def __init__(self, images):
            self._images = images

        def get_images(self, full=True):
            return self._images

    class FakeDoc:
        def __init__(self, pages):
            self._pages = pages

        def __len__(self):
            return len(self._pages)

        def __getitem__(self, index):
            return self._pages[index]

    dims = {
        1: (1200, 1600),  # repeated background
        2: (640, 480),    # real figure
        3: (60, 60),      # too small
    }

    class FakePixmap:
        def __init__(self, doc, xref):
            self.width, self.height = dims[xref]

    monkeypatch.setattr(pdf_parser.fitz, "Pixmap", FakePixmap)
    doc = FakeDoc(
        [
            FakePage([(1,)]),
            FakePage([(1,)]),
            FakePage([(1,)]),
            FakePage([(1,)]),
            FakePage([(1,), (2,), (3,)]),
        ]
    )

    assert pdf_parser._detect_figure_pages(doc) == {5: 1}


def test_render_page_and_parse_pdf_end_to_end(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pdf_parser, "IMAGES_DIR", tmp_path / "rendered")

    image_path = tmp_path / "figure.png"
    Image.new("RGB", (220, 180), "white").save(image_path)

    pdf_path = tmp_path / "physics.pdf"
    _make_pdf_with_text_and_figure(pdf_path, image_path)

    document = pdf_parser.parse_pdf(pdf_path)
    assert document.title == "Physics Notes"
    assert document.total_pages == 2
    assert document.doc_type == "pdf"
    assert document.chunks
    assert any("Entropy" in chunk.text for chunk in document.chunks)
    assert any(img.page == 2 and Path(img.path).exists() for img in document.images)

    rendered = pdf_parser.render_page(str(pdf_path), 1, document.id)
    assert rendered is not None
    assert rendered.page == 1
    assert Path(rendered.path).exists()
    assert pdf_parser.render_page(str(pdf_path), 99, document.id) is None


def test_parse_pdf_validation_guards(tmp_path: Path, monkeypatch) -> None:
    with pytest.raises(FileNotFoundError):
        pdf_parser.parse_pdf(tmp_path / "missing.pdf")

    pdf_path = tmp_path / "too_big.pdf"
    pdf_path.write_bytes(b"small")

    real_stat = Path.stat

    class BigStat:
        st_size = pdf_parser.MAX_PDF_BYTES + 1
        st_mtime_ns = 1

    def fake_stat(self, *args, **kwargs):
        if self == pdf_path:
            return BigStat()
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fake_stat)
    with pytest.raises(ValueError, match="too large"):
        pdf_parser.parse_pdf(pdf_path)

    monkeypatch.setattr(Path, "stat", real_stat)

    class FakeLargeDoc:
        metadata = {}

        def __len__(self):
            return pdf_parser.MAX_PDF_PAGES + 1

        def close(self):
            self.closed = True

    monkeypatch.setattr(pdf_parser.fitz, "open", lambda path: FakeLargeDoc())
    with pytest.raises(ValueError, match="too many pages"):
        pdf_parser.parse_pdf(pdf_path)
