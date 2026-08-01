"""Tests for re_enrich_metadata — the DIC post-update signal handler analog.

aiify-opp-89: metadata_extraction -> llm_generation.  The external scan flagged
paperless-ngx src/documents/signals/handlers.py — Django signals that fire after
a Document save and trigger LLM classifier re-runs.  The augmentation lands in
the analogous ICDEV subsystem (DIC) as ``re_enrich_metadata``.

Pins:
* doc not found → None
* no rag_chunks → {"proposals": {}}
* LLM unavailable → {"proposals": {}} (degrades gracefully)
* LLM returns valid metadata → proposals dict populated
* extract_identifiers=False skips identifier extraction
* extract_correspondence=False skips correspondence extraction
"""
from __future__ import annotations

import importlib
import json

import pytest

from tools.db.storage import get_connection

ingest = importlib.import_module("tools.document_intelligence.ingest_orchestrator")
router_mod = importlib.import_module("tools.llm.router")


# ── LLM stub ──────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, content):
        self.content = content


class _Router:
    _content: str = "{}"

    def __init__(self, *a, **k):
        pass

    def invoke(self, function, request):
        return _Resp(self._content)


def _patch_router(monkeypatch, content: str = "{}"):
    import sys
    _Router._content = content
    monkeypatch.setattr(router_mod, "LLMRouter", _Router)
    import icdev.tools.llm.router as _icdev_mod
    monkeypatch.setattr(_icdev_mod, "LLMRouter", _Router)
    for key, mod in list(sys.modules.items()):
        if "llm.router" in key and hasattr(mod, "LLMRouter"):
            monkeypatch.setattr(mod, "LLMRouter", _Router)


# ── Fixtures ──────────────────────────────────────────────────────────────────

_DOC_ID = "dic_doc_re_enrich_test"
_CHUNK_TEXT = "This is a security policy document requiring MFA for all access."

#: rag_chunks belongs to the RAG layer; DIC's `_ensure_schema` correctly does
#: not own its DDL. On a database that has never had a RAG ingest (a fresh
#: checkout or worktree, where icdev.db is empty) the table is simply absent.
#:
#: Deliberately PERMISSIVE — no NOT NULL, no CHECK. Other test fixtures issue
#: their own `CREATE TABLE IF NOT EXISTS rag_chunks` against this same database
#: with a narrower column list, and whichever runs first wins. A strict shape
#: here would make THEIR inserts fail with a constraint they never declared,
#: turning one fixture's convenience into another file's mystery failure.
_RAG_CHUNKS_DDL = """
CREATE TABLE IF NOT EXISTS rag_chunks (
    id TEXT PRIMARY KEY,
    content TEXT,
    content_hash TEXT,
    embedding BLOB,
    source_type TEXT,
    source_id TEXT DEFAULT '',
    source_table TEXT DEFAULT '',
    chunk_index INTEGER DEFAULT 0,
    total_chunks INTEGER DEFAULT 1,
    metadata TEXT DEFAULT '{}',
    tier TEXT DEFAULT 'hot',
    tenant_id TEXT DEFAULT '',
    project_id TEXT DEFAULT '',
    classification TEXT DEFAULT 'CUI',
    sign_bits BLOB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    embedding_vec BLOB,
    kg_node_ids TEXT
);
"""

@pytest.fixture()
def _db_with_doc():
    """Insert a minimal dic_documents row + one rag_chunk + dic_chunk_links row."""
    conn = get_connection()
    try:
        ingest._ensure_schema(conn)
        # `_ensure_schema` creates the dic_* tables only — rag_chunks belongs to
        # the RAG layer and DIC correctly does not own its DDL. On a database
        # that has never had a RAG ingest (a fresh checkout or worktree, where
        # icdev.db is empty) the table is simply absent and the cleanup DELETE
        # below fails at setup, which reads as a broken test rather than an
        # unseeded database.
        conn.executescript(_RAG_CHUNKS_DDL)

        conn.execute("DELETE FROM dic_chunk_links WHERE doc_id = ?", (_DOC_ID,))
        conn.execute("DELETE FROM dic_documents WHERE doc_id = ?", (_DOC_ID,))
        conn.execute(
            "DELETE FROM rag_chunks WHERE id = ?",
            (_DOC_ID + "_chunk_0",),
        )

        conn.execute(
            """INSERT OR REPLACE INTO dic_documents
               (doc_id, collection_id, source_id, filename, content_type,
                provider, byte_size, content_sha256, page_count, created_at,
                tenant_id, classification)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (_DOC_ID, "col_test", _DOC_ID, "policy.md", "text/plain",
             "builtin-text", 64, "abc123", 1, "2026-01-01T00:00:00Z",
             "default", "CUI"),
        )

        chunk_id = _DOC_ID + "_chunk_0"
        import hashlib as _hl
        content_hash = _hl.sha256(_CHUNK_TEXT.encode()).hexdigest()
        conn.execute(
            """INSERT OR REPLACE INTO rag_chunks
               (id, content, content_hash, source_id, source_type,
                tenant_id, classification)
               VALUES (?,?,?,?,?,?,?)""",
            (chunk_id, _CHUNK_TEXT, content_hash, _DOC_ID,
             "dic_document", "default", "CUI"),
        )

        conn.execute(
            """INSERT OR REPLACE INTO dic_chunk_links
               (link_id, doc_id, version_id, rag_chunk_id, collection_id,
                chunk_index, created_at, tenant_id, classification)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (_DOC_ID + "_link_0", _DOC_ID, _DOC_ID + "_v1", chunk_id,
             "col_test", 0, "2026-01-01T00:00:00Z", "default", "CUI"),
        )
        conn.commit()
    finally:
        conn.close()

    yield

    conn = get_connection()
    try:
        conn.execute("DELETE FROM dic_chunk_links WHERE doc_id = ?", (_DOC_ID,))
        conn.execute("DELETE FROM dic_documents WHERE doc_id = ?", (_DOC_ID,))
        conn.execute("DELETE FROM rag_chunks WHERE id = ?", (_DOC_ID + "_chunk_0",))
        conn.commit()
    finally:
        conn.close()


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_unknown_doc_returns_none(monkeypatch):
    _patch_router(monkeypatch)
    result = ingest.re_enrich_metadata("dic_doc_nonexistent_xyz")
    assert result is None


def test_no_chunks_returns_empty_proposals(_db_with_doc, monkeypatch):
    _patch_router(monkeypatch)
    # Remove the chunk link so text reconstruction yields nothing.
    conn = get_connection()
    try:
        conn.execute("DELETE FROM dic_chunk_links WHERE doc_id = ?", (_DOC_ID,))
        conn.commit()
    finally:
        conn.close()

    result = ingest.re_enrich_metadata(_DOC_ID)
    assert result is not None
    assert result["doc_id"] == _DOC_ID
    assert result["proposals"] == {}


def test_llm_unavailable_degrades_gracefully(_db_with_doc, monkeypatch):
    # LLM returns empty JSON → _ai_metadata_extraction returns None → proposals {}
    _patch_router(monkeypatch, content="{}")
    result = ingest.re_enrich_metadata(_DOC_ID)
    assert result is not None
    assert result["doc_id"] == _DOC_ID
    assert result["filename"] == "policy.md"
    assert isinstance(result["proposals"], dict)


def test_valid_llm_response_populates_proposals(_db_with_doc, monkeypatch):
    metadata_json = json.dumps({
        "document_type": "policy",
        "tags": ["security", "mfa"],
        "date": "2026-06-01",
        "confidence": 0.91,
    })
    _patch_router(monkeypatch, content=metadata_json)
    result = ingest.re_enrich_metadata(
        _DOC_ID,
        extract_identifiers=False,
        extract_correspondence=False,
    )
    assert result is not None
    assert result["proposals"]["document_type"] == "policy"
    assert "security" in result["proposals"]["tags"]
    assert result["proposals"]["confidence"] >= ingest._METADATA_MIN_CONFIDENCE


def test_skip_identifiers_flag(_db_with_doc, monkeypatch):
    metadata_json = json.dumps({
        "document_type": "report",
        "tags": [],
        "date": None,
        "confidence": 0.85,
    })
    _patch_router(monkeypatch, content=metadata_json)
    result = ingest.re_enrich_metadata(
        _DOC_ID,
        extract_identifiers=False,
        extract_correspondence=False,
    )
    assert result is not None
    assert "identifiers" not in result["proposals"]
    assert "correspondence" not in result["proposals"]
