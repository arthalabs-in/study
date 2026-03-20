from __future__ import annotations

from pathlib import Path

from src.parsers.doc_store import DocStore
from src.parsers.pdf_parser import Chunk, Document, build_document_id


def test_build_document_id_avoids_same_stem_collisions(tmp_path: Path) -> None:
    left = tmp_path / 'a' / 'chapter-one.pdf'
    right = tmp_path / 'b' / 'chapter-one.pdf'
    left.parent.mkdir()
    right.parent.mkdir()
    left.write_bytes(b'left')
    right.write_bytes(b'right')

    assert build_document_id(left) != build_document_id(right)


def test_doc_store_search_and_remove(tmp_path: Path) -> None:
    store = DocStore()
    doc = Document(
        id='physics_doc',
        title='Physics',
        path=str(tmp_path / 'physics.pdf'),
        total_pages=1,
        chunks=[
            Chunk(id='physics_doc_c1', doc_id='physics_doc', page=1, text='Entropy is a measure of disorder.', summary='Entropy basics'),
            Chunk(id='physics_doc_c2', doc_id='physics_doc', page=1, text='Momentum is mass times velocity.', summary='Momentum basics'),
        ],
    )

    store.add_document(doc)
    results = store.search_chunks('entropy disorder')
    assert results[0]['chunk_id'] == 'physics_doc_c1'
    assert store.get_chunk_by_id('physics_doc_c2')['summary'] == 'Momentum basics'

    store.remove_document('physics_doc')
    assert store.search_chunks('entropy') == []
