"""
Document Store — in-memory index of all parsed document chunks.
Methods are designed to be exposed as LLM tool-call targets.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from src.parsers.pdf_parser import Chunk, Document, ImageInfo


@dataclass
class DocStore:
    """In-memory document store with BM25 search over chunks."""

    documents: dict[str, Document] = field(default_factory=dict)
    _chunks_index: dict[str, Chunk] = field(default_factory=dict)
    _images_index: dict[str, ImageInfo] = field(default_factory=dict)
    _idf_cache: dict[str, float] = field(default_factory=dict)
    _tf_cache: dict[str, Counter] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Document lifecycle
    # ------------------------------------------------------------------

    def add_document(self, doc: Document) -> None:
        """Add a parsed document to the store."""
        self.documents[doc.id] = doc
        for chunk in doc.chunks:
            self._chunks_index[chunk.id] = chunk
        for img in doc.images:
            self._images_index[img.id] = img
        self._rebuild_search_index()

    def remove_document(self, doc_id: str) -> None:
        """Remove a document and its chunks."""
        doc = self.documents.pop(doc_id, None)
        if doc:
            for chunk in doc.chunks:
                self._chunks_index.pop(chunk.id, None)
            self._rebuild_search_index()

    # ------------------------------------------------------------------
    # Tool-call targets (these map 1:1 to LLM tools)
    # ------------------------------------------------------------------

    def search_chunks(self, query: str, top_k: int = 5) -> list[dict]:
        """BM25 search over all chunks. Returns top_k results."""
        if not query.strip() or not self._chunks_index:
            return []

        query_terms = self._tokenize(query)
        scores: list[tuple[str, float]] = []

        for chunk_id, chunk in self._chunks_index.items():
            score = self._bm25_score(chunk_id, query_terms)
            if score > 0:
                scores.append((chunk_id, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        results = []
        for chunk_id, score in scores[:top_k]:
            chunk = self._chunks_index[chunk_id]
            results.append({
                "chunk_id": chunk.id,
                "doc_id": chunk.doc_id,
                "page": chunk.page,
                "score": round(score, 3),
                "summary": chunk.summary,
                "text": chunk.text,
            })
        return results

    def get_chunk_by_id(self, chunk_id: str) -> dict | None:
        """Retrieve a specific chunk by its ID."""
        chunk = self._chunks_index.get(chunk_id)
        if not chunk:
            return None
        return {
            "chunk_id": chunk.id,
            "doc_id": chunk.doc_id,
            "page": chunk.page,
            "text": chunk.text,
            "summary": chunk.summary,
        }

    def get_chunks_by_page(self, doc_id: str, page_number: int) -> list[dict]:
        """Get all chunks from a specific page of a document."""
        doc = self.documents.get(doc_id)
        if not doc:
            return []
        return [
            {
                "chunk_id": c.id,
                "page": c.page,
                "text": c.text,
                "summary": c.summary,
            }
            for c in doc.get_chunks_by_page(page_number)
        ]

    def list_documents(self) -> list[dict]:
        """List all loaded documents with metadata."""
        return [
            {
                "doc_id": doc.id,
                "title": doc.title,
                "source_name": Path(doc.path).name,
                "total_pages": doc.total_pages,
                "total_chunks": len(doc.chunks),
                "total_images": len(doc.images),
                "doc_type": doc.doc_type,
            }
            for doc in self.documents.values()
        ]

    def get_document_outline(self, doc_id: str) -> list[dict]:
        """Get a summary outline of all chunks in a document."""
        doc = self.documents.get(doc_id)
        if not doc:
            return []
        return [
            {"chunk_id": c.id, "page": c.page, "summary": c.summary}
            for c in doc.chunks
        ]

    def get_document_images(self, doc_id: str) -> list[dict]:
        """List pages with figures from a document."""
        doc = self.documents.get(doc_id)
        if not doc:
            return []
        return [
            {
                "image_id": img.id,
                "page": img.page,
                "figure_count": img.figure_count,
                "width": img.width,
                "height": img.height,
                "size_kb": round(img.size_bytes / 1024, 1),
            }
            for img in doc.images
        ]

    def get_page_image(self, doc_id: str, page_number: int) -> dict | None:
        """Get a rendered page image. Pre-rendered for figure pages, on-demand for others."""
        doc = self.documents.get(doc_id)
        if not doc:
            return None

        # Check pre-rendered figure pages first
        for img in doc.images:
            if img.page == page_number:
                return {
                    "image_id": img.id,
                    "doc_id": doc_id,
                    "page": page_number,
                    "path": img.path,
                    "figure_count": img.figure_count,
                }

        # On-demand render for non-figure pages
        from src.parsers.pdf_parser import render_page
        img_info = render_page(doc.path, page_number, doc_id)
        if not img_info:
            return None
        return {
            "image_id": img_info.id,
            "doc_id": doc_id,
            "page": page_number,
            "path": img_info.path,
            "figure_count": 0,
        }

    # ------------------------------------------------------------------
    # BM25 internals
    # ------------------------------------------------------------------

    def _tokenize(self, text: str) -> list[str]:
        return re.findall(r"\w+", text.lower())

    def _rebuild_search_index(self) -> None:
        """Rebuild TF and IDF caches."""
        self._tf_cache.clear()
        self._idf_cache.clear()

        doc_count = len(self._chunks_index)
        if doc_count == 0:
            return

        df: Counter = Counter()

        for chunk_id, chunk in self._chunks_index.items():
            tokens = self._tokenize(chunk.text)
            tf = Counter(tokens)
            self._tf_cache[chunk_id] = tf
            for term in set(tokens):
                df[term] += 1

        for term, freq in df.items():
            self._idf_cache[term] = math.log(
                (doc_count - freq + 0.5) / (freq + 0.5) + 1
            )

    def _bm25_score(
        self, chunk_id: str, query_terms: list[str], k1: float = 1.5, b: float = 0.75
    ) -> float:
        tf = self._tf_cache.get(chunk_id, Counter())
        if not tf:
            return 0.0

        doc_len = sum(tf.values())
        avg_dl = sum(sum(c.values()) for c in self._tf_cache.values()) / max(
            len(self._tf_cache), 1
        )

        score = 0.0
        for term in query_terms:
            idf = self._idf_cache.get(term, 0.0)
            term_tf = tf.get(term, 0)
            numerator = term_tf * (k1 + 1)
            denominator = term_tf + k1 * (1 - b + b * doc_len / avg_dl)
            score += idf * numerator / denominator

        return score
