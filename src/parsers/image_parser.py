"""
Image Parser - OCR text extraction from images using EasyOCR.
Falls back to Pillow metadata if OCR fails.
"""

from __future__ import annotations

from pathlib import Path

from src.parsers.pdf_parser import Chunk, Document, build_document_id

SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tiff"}
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000

# Lazy-load EasyOCR reader to avoid 200MB download on import
_reader = None


def _get_reader():
    global _reader
    if _reader is None:
        import easyocr
        _reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    return _reader


def _validate_image_bounds(path: Path) -> None:
    if path.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError(f"Image is too large to parse safely ({path.stat().st_size} bytes). Limit is {MAX_IMAGE_BYTES} bytes.")
    from PIL import Image

    with Image.open(path) as img:
        width, height = img.size
    if width * height > MAX_IMAGE_PIXELS:
        raise ValueError(f"Image has too many pixels to OCR safely ({width}x{height}). Limit is {MAX_IMAGE_PIXELS} total pixels.")


def parse_image(path: str | Path) -> Document:
    """Parse an image file via OCR into a Document with chunks."""
    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {path}")
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported image format: {path.suffix}")

    _validate_image_bounds(path)
    doc_id = build_document_id(path)

    try:
        reader = _get_reader()
        results = reader.readtext(str(path), detail=0, paragraph=True)
        text = "\n".join(results)
    except Exception:
        try:
            from PIL import Image
            with Image.open(path) as img:
                text = str(img.info) if img.info else ""
        except Exception:
            text = ""

    if not text.strip():
        text = f"[No text could be extracted from {path.name}]"

    chunks = [
        Chunk(
            id=f"{doc_id}_c0",
            doc_id=doc_id,
            page=1,
            text=text.strip(),
            summary=text[:80] + ("..." if len(text) > 80 else ""),
        )
    ]

    return Document(
        id=doc_id,
        title=path.stem,
        path=str(path),
        total_pages=1,
        chunks=chunks,
        doc_type="image",
    )
