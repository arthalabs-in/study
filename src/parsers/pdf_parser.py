"""
PDF Parser — extracts text and images from PDFs and splits into semantic chunks.
Uses PyMuPDF (fitz) for fast, accurate extraction.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF

# Directory for extracted images. Use the system temp directory so rendered pages are session-scoped cache files rather than long-lived user-profile artifacts.
IMAGES_DIR = Path(tempfile.gettempdir()) / "study-tui" / "images"
MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_PDF_PAGES = 400
MAX_RENDERED_FIGURE_PAGES = 24


def build_document_id(path: str | Path) -> str:
    resolved = Path(path).expanduser().resolve()
    stem = re.sub(r"[^a-z0-9]+", "_", resolved.stem.lower()).strip("_") or "document"
    stat = resolved.stat()
    digest_input = f"{resolved}|{stat.st_size}|{stat.st_mtime_ns}"
    digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:10]
    return f"{stem}_{digest}"


@dataclass
class Chunk:
    """A single chunk of document text."""
    id: str
    doc_id: str
    page: int
    text: str
    summary: str = ""
    heading: str = ""

    def __repr__(self) -> str:
        return f"Chunk(id={self.id!r}, page={self.page}, len={len(self.text)})"


@dataclass
class ImageInfo:
    """Metadata about a rendered PDF page that contains figures."""
    id: str
    doc_id: str
    page: int
    path: str          # absolute path to the saved image file
    width: int
    height: int
    size_bytes: int
    figure_count: int = 0  # number of real figure objects detected on this page

    def __repr__(self) -> str:
        return f"ImageInfo(id={self.id!r}, page={self.page}, {self.width}x{self.height}, figures={self.figure_count})"


@dataclass
class Document:
    """Parsed document with metadata, chunks, and extracted images."""
    id: str
    title: str
    path: str
    total_pages: int
    chunks: list[Chunk] = field(default_factory=list)
    images: list[ImageInfo] = field(default_factory=list)
    doc_type: str = "pdf"

    @property
    def full_text(self) -> str:
        return "\n\n".join(c.text for c in self.chunks)

    def get_chunks_by_page(self, page: int) -> list[Chunk]:
        return [c for c in self.chunks if c.page == page]

    def get_images_by_page(self, page: int) -> list[ImageInfo]:
        return [img for img in self.images if img.page == page]


# ---------------------------------------------------------------------------
# Chunking helpers
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(
    r"^(?:(?:Chapter|Section|Part)\s+\d+[.:]\s*|"
    r"\d+(?:\.\d+)*\s+[A-Z]|"
    r"[A-Z][A-Z\s]{4,}$)",
    re.MULTILINE,
)

MAX_CHUNK_CHARS = 2000  # ~500 tokens
OVERLAP_CHARS = 200


def _split_into_paragraphs(text: str) -> list[str]:
    """Split text on double newlines or heading patterns."""
    parts = re.split(r"\n{2,}", text)
    return [p.strip() for p in parts if p.strip()]


def _merge_paragraphs_into_chunks(
    paragraphs: list[str],
    doc_id: str,
    page: int,
    start_idx: int,
) -> tuple[list[Chunk], int]:
    """Merge small paragraphs into ~500-token chunks with overlap."""
    chunks: list[Chunk] = []
    buffer = ""
    idx = start_idx

    for para in paragraphs:
        # If a single paragraph exceeds max, force-split it
        if len(para) > MAX_CHUNK_CHARS:
            if buffer.strip():
                chunks.append(Chunk(
                    id=f"{doc_id}_c{idx}",
                    doc_id=doc_id,
                    page=page,
                    text=buffer.strip(),
                ))
                idx += 1
                buffer = ""
            # Hard-split the long paragraph
            for i in range(0, len(para), MAX_CHUNK_CHARS - OVERLAP_CHARS):
                slice_text = para[i : i + MAX_CHUNK_CHARS]
                chunks.append(Chunk(
                    id=f"{doc_id}_c{idx}",
                    doc_id=doc_id,
                    page=page,
                    text=slice_text.strip(),
                ))
                idx += 1
            continue

        if len(buffer) + len(para) + 2 > MAX_CHUNK_CHARS:
            if buffer.strip():
                chunks.append(Chunk(
                    id=f"{doc_id}_c{idx}",
                    doc_id=doc_id,
                    page=page,
                    text=buffer.strip(),
                ))
                idx += 1
            # Carry overlap
            buffer = buffer[-OVERLAP_CHARS:] + "\n\n" + para if buffer else para
        else:
            buffer = buffer + "\n\n" + para if buffer else para

    if buffer.strip():
        chunks.append(Chunk(
            id=f"{doc_id}_c{idx}",
            doc_id=doc_id,
            page=page,
            text=buffer.strip(),
        ))
        idx += 1

    return chunks, idx


# ---------------------------------------------------------------------------
# Figure detection & page rendering
# ---------------------------------------------------------------------------

RENDER_DPI = 150  # Good balance of quality vs file size
MIN_FIGURE_DIM = 80  # Ignore embedded images smaller than 80px


def _detect_figure_pages(doc: fitz.Document) -> dict[int, int]:
    """Scan embedded images to detect which pages contain real figures.

    Returns {page_number (1-indexed): figure_count}.
    Filters out repeating background/template images by:
    1. xref reuse (same image object on many pages)
    2. dimension pattern (same WxH appearing on many pages = template)
    """
    total_pages = len(doc)
    bg_threshold = max(3, int(total_pages * 0.4))

    # First pass: collect all image info per page
    page_images: dict[int, list[tuple[int, int, int]]] = {}  # page -> [(xref, w, h)]
    xref_count: dict[int, int] = {}
    dim_page_count: dict[tuple[int, int], int] = {}  # (w,h) -> how many pages have it

    for page_num in range(total_pages):
        page = doc[page_num]
        images = []
        dims_seen_this_page: set[tuple[int, int]] = set()

        for img_info in page.get_images(full=True):
            xref = img_info[0]
            xref_count[xref] = xref_count.get(xref, 0) + 1
            try:
                pix = fitz.Pixmap(doc, xref)
                w, h = pix.width, pix.height
                images.append((xref, w, h))
                dim_key = (w, h)
                if dim_key not in dims_seen_this_page:
                    dims_seen_this_page.add(dim_key)
                    dim_page_count[dim_key] = dim_page_count.get(dim_key, 0) + 1
            except Exception:
                continue

        page_images[page_num] = images

    # Build set of background xrefs and background dimensions
    bg_xrefs = {xref for xref, cnt in xref_count.items() if cnt >= bg_threshold}
    bg_dims = {dim for dim, cnt in dim_page_count.items() if cnt >= bg_threshold}

    # Second pass: count real figures per page
    figure_pages: dict[int, int] = {}
    for page_num, images in page_images.items():
        real_figures = 0
        for xref, w, h in images:
            if xref in bg_xrefs:
                continue
            if (w, h) in bg_dims:
                continue
            if w < MIN_FIGURE_DIM or h < MIN_FIGURE_DIM:
                continue
            real_figures += 1
        if real_figures > 0:
            figure_pages[page_num + 1] = real_figures  # 1-indexed

    return figure_pages


def _render_figure_pages(
    doc: fitz.Document, doc_id: str, figure_pages: dict[int, int]
) -> list[ImageInfo]:
    """Render a bounded set of pages that have figures as JPEG images."""
    img_dir = IMAGES_DIR / doc_id
    img_dir.mkdir(parents=True, exist_ok=True)

    images: list[ImageInfo] = []
    zoom = RENDER_DPI / 72  # fitz default is 72 DPI
    mat = fitz.Matrix(zoom, zoom)
    selected_pages = sorted(
        sorted(figure_pages.items(), key=lambda item: (-item[1], item[0]))[:MAX_RENDERED_FIGURE_PAGES],
        key=lambda item: item[0],
    )

    for page_num, fig_count in selected_pages:
        page = doc[page_num - 1]  # 0-indexed
        pix = page.get_pixmap(matrix=mat)

        img_path = img_dir / f"page_{page_num}.jpg"
        pix.save(str(img_path))

        images.append(ImageInfo(
            id=f"{doc_id}_p{page_num}",
            doc_id=doc_id,
            page=page_num,
            path=str(img_path),
            width=pix.width,
            height=pix.height,
            size_bytes=img_path.stat().st_size,
            figure_count=fig_count,
        ))

    return images


def render_page(pdf_path: str, page_num: int, doc_id: str) -> ImageInfo | None:
    """Render a single page on demand (for pages not pre-rendered)."""
    img_dir = IMAGES_DIR / doc_id
    img_dir.mkdir(parents=True, exist_ok=True)

    # Check if already rendered
    img_path = img_dir / f"page_{page_num}.jpg"
    if img_path.exists():
        from PIL import Image
        try:
            with Image.open(img_path) as im:
                w, h = im.size
        except Exception:
            w, h = 0, 0
        return ImageInfo(
            id=f"{doc_id}_p{page_num}",
            doc_id=doc_id,
            page=page_num,
            path=str(img_path),
            width=w,
            height=h,
            size_bytes=img_path.stat().st_size,
            figure_count=0,
        )

    try:
        doc = fitz.open(pdf_path)
        if page_num < 1 or page_num > len(doc):
            doc.close()
            return None
        page = doc[page_num - 1]
        zoom = RENDER_DPI / 72
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        pix.save(str(img_path))
        doc.close()

        return ImageInfo(
            id=f"{doc_id}_p{page_num}",
            doc_id=doc_id,
            page=page_num,
            path=str(img_path),
            width=pix.width,
            height=pix.height,
            size_bytes=img_path.stat().st_size,
            figure_count=0,
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_pdf(path: str | Path) -> Document:
    """Parse a PDF file into a Document with semantic chunks and rendered figure pages."""
    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")
    if path.stat().st_size > MAX_PDF_BYTES:
        raise ValueError(f"PDF is too large to parse safely ({path.stat().st_size} bytes). Limit is {MAX_PDF_BYTES} bytes.")

    doc = fitz.open(str(path))
    total_pages = len(doc)
    if total_pages > MAX_PDF_PAGES:
        doc.close()
        raise ValueError(f"PDF has too many pages to parse safely ({total_pages}). Limit is {MAX_PDF_PAGES} pages.")

    doc_id = build_document_id(path)
    title = doc.metadata.get("title") or path.stem

    all_chunks: list[Chunk] = []
    chunk_idx = 0

    for page_num in range(total_pages):
        page = doc[page_num]
        text = page.get_text("text")
        if not text.strip():
            continue

        paragraphs = _split_into_paragraphs(text)
        page_chunks, chunk_idx = _merge_paragraphs_into_chunks(
            paragraphs, doc_id, page_num + 1, chunk_idx
        )
        all_chunks.extend(page_chunks)

    for chunk in all_chunks:
        first_line = chunk.text.split("\n")[0][:80]
        chunk.summary = first_line + ("..." if len(chunk.text) > 80 else "")

    figure_pages = _detect_figure_pages(doc)
    rendered_images = _render_figure_pages(doc, doc_id, figure_pages)
    doc.close()

    return Document(
        id=doc_id,
        title=title,
        path=str(path),
        total_pages=total_pages,
        chunks=all_chunks,
        images=rendered_images,
        doc_type="pdf",
    )


