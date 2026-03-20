"""
Calibre Client — read-only access to a Calibre library via its metadata.db.
Zero external dependencies (pure stdlib sqlite3).
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def find_calibre_library(configured_path: str | None = None) -> Path | None:
    """Auto-detect the Calibre library directory.

    Priority:
      1. Explicitly configured path
      2. CALIBRE_LIBRARY env var
      3. ~/Calibre Library  (default)
    """
    candidates: list[str | None] = [
        configured_path,
        os.environ.get("CALIBRE_LIBRARY"),
        str(Path.home() / "Calibre Library"),
    ]
    for c in candidates:
        if c and Path(c).is_dir() and (Path(c) / "metadata.db").is_file():
            return Path(c)
    return None


def search_books(
    library_path: Path,
    query: str | None = None,
    format_filter: str = "PDF",
    limit: int = 20,
) -> list[dict]:
    """Search the Calibre metadata.db for books.

    Returns a list of dicts with: id, title, authors, tags, format, file_size, path.
    """
    db_path = library_path / "metadata.db"
    if not db_path.is_file():
        return []

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        # Build the query — join books, data (formats), and authors
        sql = """
            SELECT
                b.id,
                b.title,
                b.author_sort AS authors,
                b.path        AS book_dir,
                d.format,
                d.name         AS file_stem,
                d.uncompressed_size AS file_size
            FROM books b
            JOIN data d ON d.book = b.id
            WHERE d.format = ?
        """
        params: list[object] = [format_filter.upper()]

        if query:
            sql += " AND (b.title LIKE ? OR b.author_sort LIKE ?)"
            like = f"%{query}%"
            params.extend([like, like])

        sql += " ORDER BY b.timestamp DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()

        results = []
        for row in rows:
            # Resolve the absolute PDF path
            pdf_path = library_path / row["book_dir"] / f"{row['file_stem']}.{row['format'].lower()}"
            results.append({
                "id": row["id"],
                "title": row["title"],
                "authors": row["authors"],
                "format": row["format"],
                "file_size_mb": round((row["file_size"] or 0) / (1024 * 1024), 1),
                "path": str(pdf_path),
            })

        # Fetch tags for each result
        for item in results:
            tag_rows = conn.execute(
                """
                SELECT t.name FROM tags t
                JOIN books_tags_link btl ON btl.tag = t.id
                WHERE btl.book = ?
                """,
                [item["id"]],
            ).fetchall()
            item["tags"] = [r["name"] for r in tag_rows]

        return results
    finally:
        conn.close()


def get_pdf_path(library_path: Path, book_id: int) -> Path | None:
    """Resolve a Calibre book ID to its PDF file path."""
    db_path = library_path / "metadata.db"
    if not db_path.is_file():
        return None

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT b.path AS book_dir, d.name AS file_stem, d.format
            FROM books b
            JOIN data d ON d.book = b.id
            WHERE b.id = ? AND d.format = 'PDF'
            LIMIT 1
            """,
            [book_id],
        ).fetchone()
        if not row:
            return None
        pdf = library_path / row["book_dir"] / f"{row['file_stem']}.pdf"
        return pdf if pdf.is_file() else None
    finally:
        conn.close()
