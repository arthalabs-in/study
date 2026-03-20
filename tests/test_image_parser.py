from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image

import src.parsers.image_parser as image_parser


def test_get_reader_caches(monkeypatch) -> None:
    created = []
    fake_module = type(sys)("easyocr")

    class FakeReader:
        def __init__(self, langs, gpu=False, verbose=False):
            created.append((tuple(langs), gpu, verbose))

    fake_module.Reader = FakeReader
    monkeypatch.setitem(sys.modules, "easyocr", fake_module)
    monkeypatch.setattr(image_parser, "_reader", None)

    first = image_parser._get_reader()
    second = image_parser._get_reader()

    assert first is second
    assert created == [(("en",), False, False)]


def test_validate_image_bounds_errors(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "big.png"
    path.write_bytes(b"data")

    class BigStat:
        st_size = image_parser.MAX_IMAGE_BYTES + 1

    monkeypatch.setattr(Path, "stat", lambda self: BigStat())
    with pytest.raises(ValueError, match="too large"):
        image_parser._validate_image_bounds(path)

    class SmallStat:
        st_size = 10

    monkeypatch.setattr(Path, "stat", lambda self: SmallStat())

    class FakeImage:
        size = (image_parser.MAX_IMAGE_PIXELS + 1, 1)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(Image, "open", lambda _: FakeImage())
    with pytest.raises(ValueError, match="too many pixels"):
        image_parser._validate_image_bounds(path)


def test_parse_image_success_and_fallbacks(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "study.png"
    Image.new("RGB", (10, 10), "white").save(path)

    class FakeReader:
        def readtext(self, *args, **kwargs):
            return ["Entropy", "Momentum"]

    monkeypatch.setattr(image_parser, "_get_reader", lambda: FakeReader())
    document = image_parser.parse_image(path)
    assert document.doc_type == "image"
    assert document.total_pages == 1
    assert "Entropy" in document.chunks[0].text

    class FailingReader:
        def readtext(self, *args, **kwargs):
            raise RuntimeError("ocr failed")

    monkeypatch.setattr(image_parser, "_get_reader", lambda: FailingReader())
    with Image.open(path) as opened:
        opened.info["Description"] = "fallback metadata"
        monkeypatch.setattr(Image, "open", lambda _: opened)
        fallback_document = image_parser.parse_image(path)
    assert "Description" in fallback_document.chunks[0].text

    monkeypatch.setattr(image_parser, "_validate_image_bounds", lambda _: None)
    monkeypatch.setattr(Image, "open", lambda _: (_ for _ in ()).throw(RuntimeError("bad image")))
    placeholder_document = image_parser.parse_image(path)
    assert placeholder_document.chunks[0].text.startswith("[No text could be extracted")


def test_parse_image_validation_errors(tmp_path: Path) -> None:
    missing = tmp_path / "missing.png"
    with pytest.raises(FileNotFoundError):
        image_parser.parse_image(missing)

    wrong = tmp_path / "notes.txt"
    wrong.write_text("hi", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported image format"):
        image_parser.parse_image(wrong)
