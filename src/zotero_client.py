"""
Zotero Client — access to a local Zotero library via pyzotero.
Requires: pip install pyzotero
Requires: Zotero running with local API enabled (Settings > Advanced).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

try:
    from pyzotero import zotero as _zotero  # type: ignore[import-untyped]
    _HAS_PYZOTERO = True
except ImportError:
    _HAS_PYZOTERO = False


ITEM_KEY_RE = re.compile(r"^[A-Z0-9]{8}$")


def is_available() -> bool:
    """Check if pyzotero is installed and the Zotero local API responds."""
    if not _HAS_PYZOTERO:
        return False
    try:
        import urllib.request
        req = urllib.request.Request("http://127.0.0.1:23119/api/users/0/items?limit=1")
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def _get_zotero_data_dir() -> Path | None:
    """Find the Zotero storage directory from its profile config."""
    if os.name == "nt":
        zotero_dir = Path(os.environ.get("APPDATA", "")) / "Zotero" / "Zotero"
    else:
        zotero_dir = Path.home() / ".zotero" / "zotero"

    profiles_ini = zotero_dir / "profiles.ini"
    if not profiles_ini.exists():
        # Try the direct data directory
        data_dir = Path.home() / "Zotero"
        if data_dir.is_dir():
            return data_dir
        return None

    # Parse profiles.ini to find the data directory
    # Fallback: the default data dir is ~/Zotero
    data_dir = Path.home() / "Zotero"
    return data_dir if data_dir.is_dir() else None


def search_items(
    query: str | None = None,
    collection: str | None = None,
    tag: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Search Zotero items via the local API.

    Returns a list of dicts with: key, title, authors, year, item_type, tags, has_pdf.
    """
    if not _HAS_PYZOTERO:
        return []

    try:
        # Connect to local Zotero API
        zot = _zotero.Zotero(library_id="0", library_type="user", api_key="", local=True)

        kwargs: dict = {"limit": limit}
        if query:
            kwargs["q"] = query
        if tag:
            kwargs["tag"] = tag

        if collection:
            # Search within a specific collection
            collections = zot.collections()
            col_key = None
            for col in collections:
                if collection.lower() in col["data"].get("name", "").lower():
                    col_key = col["key"]
                    break
            if col_key:
                items = zot.collection_items(col_key, **kwargs)
            else:
                items = zot.items(**kwargs)
        else:
            items = zot.items(**kwargs)

        results = []
        for item in items:
            data = item.get("data", {})
            item_type = data.get("itemType", "")

            # Skip attachments and notes at the top level
            if item_type in ("attachment", "note"):
                continue

            # Extract authors
            creators = data.get("creators", [])
            authors = ", ".join(
                f"{c.get('lastName', '')}" for c in creators
                if c.get("creatorType") in ("author", "editor")
            )

            # Check for PDF attachment
            has_pdf = False
            try:
                children = zot.children(item["key"])
                for child in children:
                    cd = child.get("data", {})
                    if cd.get("contentType") == "application/pdf":
                        has_pdf = True
                        break
            except Exception:
                pass

            results.append({
                "key": item["key"],
                "title": data.get("title", "Untitled"),
                "authors": authors,
                "year": data.get("date", "")[:4] if data.get("date") else "",
                "item_type": item_type,
                "tags": [t.get("tag", "") for t in data.get("tags", [])],
                "has_pdf": has_pdf,
            })

        return results
    except Exception:
        return []


def list_collections() -> list[dict]:
    """List all Zotero collections."""
    if not _HAS_PYZOTERO:
        return []
    try:
        zot = _zotero.Zotero(library_id="0", library_type="user", api_key="", local=True)
        collections = zot.collections()
        return [
            {
                "key": col["key"],
                "name": col["data"].get("name", ""),
                "num_items": col["meta"].get("numItems", 0),
            }
            for col in collections
        ]
    except Exception:
        return []


def get_pdf_path(item_key: str) -> Path | None:
    """Find the PDF attachment path for a Zotero item."""
    if not _HAS_PYZOTERO:
        return None
    if not ITEM_KEY_RE.fullmatch((item_key or "").strip().upper()):
        return None

    data_dir = _get_zotero_data_dir()
    if not data_dir:
        return None

    try:
        zot = _zotero.Zotero(library_id="0", library_type="user", api_key="", local=True)
        children = zot.children(item_key)
        for child in children:
            cd = child.get("data", {})
            if cd.get("contentType") == "application/pdf":
                # The file is in <data_dir>/storage/<attachment_key>/<filename>
                attachment_key = child["key"]
                storage_dir = data_dir / "storage" / attachment_key
                if storage_dir.is_dir():
                    for f in storage_dir.iterdir():
                        if f.suffix.lower() == ".pdf":
                            return f
        return None
    except Exception:
        return None
