from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest


@pytest.fixture()
def tmp_path() -> Path:
    root = Path.cwd() / '_test_tmp'
    root.mkdir(exist_ok=True)
    created = root / f'case_{uuid.uuid4().hex}'
    created.mkdir(parents=True, exist_ok=False)
    try:
        yield created
    finally:
        shutil.rmtree(created, ignore_errors=True)


@pytest.fixture()
def documents_dir(tmp_path: Path) -> Path:
    docs = tmp_path / 'Documents'
    docs.mkdir()
    return docs
