# CUI // SP-CTI
"""Ingest chunk-link integrity (dic-ingest-link-01).

THE DEFECT THIS GUARDS. ``ingest_file`` writes a ``dic_chunk_links`` row per chunk pointing at the
``rag_chunks`` id the vector store persisted. That id came from ``_embed_and_store``'s ``out_id_map``, which
recorded the freshly-generated ``chunk.chunk_id`` for every embedded chunk. But ``PgVectorStore.upsert``
DEDUPS by ``content_hash`` and keeps the EXISTING row under its ORIGINAL id — so when a chunk's content was
already in the store (a re-ingest, or the same text seen before), its real id is not ``chunk.chunk_id`` and
the link dangled: ``chunks_for_version`` joined it away, returning nothing, and a document that ingested
"successfully" could never be cited (a paper added on /research showed ``chunks: N`` yet 0 verifiable
claims). The fix records ONLY an id the store confirms is in ``rag_chunks`` (``get_by_content_hash``), and
``ingest_file`` writes NO link when there is no confirmed id — an honest 0, never a dangling one.
"""
from __future__ import annotations

import types

import pytest

from tools.document_intelligence import ingest_orchestrator as io


class _FakeProvider:
    def embed(self, text: str):
        return [0.1, 0.2, 0.3]


class _FakeStore:
    """content_hash dedup, exactly as PgVectorStore behaves:
      H_DUP  — already stored under a DIFFERENT id ('existing-1'); found BEFORE embedding.
      H_NEW  — absent before upsert, persisted under 'new-1' AFTER upsert.
      H_SKIP — never persists (e.g. upsert dedup-skipped it); get_by_content_hash always None.
    """

    def __init__(self):
        self._upserted = False

    def get_by_content_hash(self, h):
        if h == "H_DUP":
            return types.SimpleNamespace(chunk_id="existing-1", embedding=[0.1])
        if h == "H_NEW":
            return types.SimpleNamespace(chunk_id="new-1", embedding=None) if self._upserted else None
        return None  # H_SKIP and anything else

    def upsert(self, chunks):
        self._upserted = True
        return len(chunks)


def _chunk(chunk_id, content_hash):
    return types.SimpleNamespace(chunk_id=chunk_id, content=f"body {chunk_id}", content_hash=content_hash, embedding=None)


@pytest.fixture
def _patched(monkeypatch):
    store = _FakeStore()
    monkeypatch.setattr("tools.llm.get_embedding_provider", lambda: _FakeProvider())
    monkeypatch.setattr("tools.rag.vector_store_factory.VectorStoreFactory.create", staticmethod(lambda **k: store))
    return store


def test_out_id_map_records_only_ids_the_store_confirms(_patched):
    chunks = [_chunk("c0", "H_DUP"), _chunk("c1", "H_NEW"), _chunk("c2", "H_SKIP")]
    out: dict = {}
    errors: list[str] = []
    io._embed_and_store(chunks, "default", errors, out_id_map=out)
    # H_DUP resolves to the EXISTING id (found before embedding) -- never the new c.chunk_id
    assert out.get(0) == "existing-1"
    # H_NEW resolves to the id the store persisted on upsert -- never the raw c.chunk_id ('c1')
    assert out.get(1) == "new-1"
    # H_SKIP was never persisted -> NO entry, so ingest_file writes no dangling link
    assert 2 not in out
    # the map never contains a freshly-generated chunk id the store did not confirm
    assert "c1" not in out.values() and "c2" not in out.values()


def test_a_chunk_the_store_cannot_confirm_gets_no_link_id(_patched):
    # every chunk is dedup-skipped (never persisted) -> out_id_map is empty, not full of dangling ids
    chunks = [_chunk("c0", "H_SKIP"), _chunk("c1", "H_SKIP")]
    out: dict = {}
    io._embed_and_store(chunks, "default", errors=[], out_id_map=out)
    assert out == {}
