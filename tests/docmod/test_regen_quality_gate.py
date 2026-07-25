# CUI // SP-CTI
"""dmx-qa-01: regeneration quality gate.

Proves a regenerated version cannot reach ``pending_review`` without passing a
deterministic gate (citation re-validation against current evidence, internal
consistency, claim-preservation diff), and that an authorized reviewer can force
past a block with an audited override.

Two layers:
  * unit — ``evaluate_regeneration_quality`` blocking / passing / diff logic.
  * integration — the REAL ``generate_document`` hook + ``regenerate_document``
    end-to-end against a seeded SQLite DB, with search/LLM/verifier faked so the
    draft text is deterministic.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from tools.doc_modernization.regen_quality_gate import (
    BLOCK_HALLUCINATED_CITATION,
    BLOCK_MISSING_CITATIONS,
    BLOCK_UNRESOLVED_PLACEHOLDERS,
    evaluate_regeneration_quality,
)

_DDL = [
    """CREATE TABLE IF NOT EXISTS dic_documents (
        doc_id TEXT PRIMARY KEY, collection_id TEXT, source_id TEXT, filename TEXT,
        content_type TEXT, provider TEXT, title TEXT, byte_size INTEGER,
        content_sha256 TEXT, page_count INTEGER, status TEXT, origin TEXT,
        classification TEXT, tenant_id TEXT, created_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS dic_versions (
        version_id TEXT PRIMARY KEY, doc_id TEXT NOT NULL,
        version_no INTEGER NOT NULL DEFAULT 1, origin TEXT, status TEXT,
        assigned_to TEXT, review_notes TEXT, content_sha256 TEXT,
        created_at TEXT, created_by TEXT, tenant_id TEXT, classification TEXT)""",
    """CREATE TABLE IF NOT EXISTS dic_sections (
        section_id TEXT PRIMARY KEY, version_id TEXT NOT NULL,
        doc_id TEXT NOT NULL, heading TEXT NOT NULL, content TEXT,
        citations_json TEXT, status TEXT DEFAULT 'draft',
        origin TEXT DEFAULT 'ai_generated', assigned_to TEXT, reviewed_by TEXT,
        reviewed_at TEXT, created_at TEXT, created_by TEXT, tenant_id TEXT,
        classification TEXT)""",
    """CREATE TABLE IF NOT EXISTS dic_review_notes (
        note_id TEXT PRIMARY KEY, item_id TEXT, item_type TEXT, note_text TEXT,
        reviewer_id TEXT, created_at TEXT)""",
    # Canonical dic_collections column set (matches document_intelligence/db/init_db.py)
    # so ensure_collection's ON CONFLICT (collection_id) upsert works.
    """CREATE TABLE IF NOT EXISTS dic_collections (
        collection_id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT DEFAULT '',
        owner_id TEXT DEFAULT '', retention_days INTEGER DEFAULT 90,
        classification TEXT DEFAULT 'CUI', tenant_id TEXT DEFAULT 'default',
        created_at TEXT DEFAULT (datetime('now')))""",
    # docmod_findings is provided by the docmod migration (full canonical schema).
]


# Columns generate_document inserts into dic_documents. A fresh worktree's
# dic_documents (from the consolidated schema) can be a partial subset, so we
# idempotently ADD any missing ones (the same non-destructive pattern init_db
# uses) rather than depend on a particular migration state.
_DIC_DOC_COLS = [
    ("source_id", "TEXT"), ("content_type", "TEXT"), ("provider", "TEXT"),
    ("byte_size", "INTEGER"), ("content_sha256", "TEXT"), ("page_count", "INTEGER"),
]


@pytest.fixture()
def db():
    from tools.db.storage import get_connection

    conn = get_connection()
    for ddl in _DDL:
        conn.execute(ddl)
    conn.commit()
    for col, typ in _DIC_DOC_COLS:
        try:
            conn.execute(f"ALTER TABLE dic_documents ADD COLUMN {col} {typ}")
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
    conn.close()
    yield


# ── unit: gate logic ─────────────────────────────────────────────────────────

def test_gate_blocks_uncited_section():
    report = evaluate_regeneration_quality(
        new_sections=[{"heading": "Overview", "content": "The system uses TLS 1.3 everywhere."}],
        old_text="## Overview\n\nThe system used TLS 1.1.",
        allowed_sources={"c1"},
    )
    assert report["blocked"] is True
    assert BLOCK_MISSING_CITATIONS in report["reasons"]


def test_gate_blocks_hallucinated_citation():
    report = evaluate_regeneration_quality(
        new_sections=[{"heading": "Overview",
                       "content": "The system uses TLS 1.3 [source: chunk zz9]."}],
        old_text="old",
        allowed_sources={"c1"},
    )
    assert report["blocked"] is True
    assert BLOCK_HALLUCINATED_CITATION in report["reasons"]


def test_gate_blocks_unresolved_placeholder():
    report = evaluate_regeneration_quality(
        new_sections=[{"heading": "Overview",
                       "content": "Contact [POC_NAME] about TLS 1.3 [source: chunk c1]."}],
        old_text="old",
        allowed_sources={"c1"},
    )
    assert report["blocked"] is True
    assert BLOCK_UNRESOLVED_PLACEHOLDERS in report["reasons"]


def test_gate_passes_clean_cited_section():
    report = evaluate_regeneration_quality(
        new_sections=[{"heading": "Overview",
                       "content": "The system uses TLS 1.3 everywhere [source: chunk c1]."}],
        old_text="## Overview\n\nThe system used TLS 1.1.",
        allowed_sources={"c1"},
    )
    assert report["blocked"] is False
    assert report["reasons"] == []


def test_gate_skips_abstained_sections():
    report = evaluate_regeneration_quality(
        new_sections=[{"heading": "Gap", "content": "(Abstained — no evidence.)", "abstained": True}],
        old_text="old",
        allowed_sources={"c1"},
    )
    assert report["blocked"] is False


def test_gate_produces_claim_preservation_diff():
    report = evaluate_regeneration_quality(
        new_sections=[{"heading": "Overview",
                       "content": "Now uses TLS 1.3 [source: chunk c1]."}],
        old_text="## Overview\n\nUses TLS 1.1.",
        allowed_sources={"c1"},
        new_text="## Overview\n\nNow uses TLS 1.3 [source: chunk c1].",
    )
    cp = report["claim_preservation"]
    assert cp["added_lines"] >= 1
    assert cp["removed_lines"] >= 1
    assert cp["unchanged"] is False
    assert isinstance(cp["diff_summary"], str) and cp["diff_summary"]


# ── integration: regenerate_document end-to-end ──────────────────────────────

def _fake_search_result(chunk_id="c1"):
    citation = SimpleNamespace(to_dict=lambda: {"chunk_id": chunk_id, "source": "kb"})
    return SimpleNamespace(
        chunk_id=chunk_id,
        content="TLS 1.3 is the current standard for transport encryption.",
        citation=citation,
        doc_title="Evidence Doc",
        doc_id="ev1",
        page=1,
    )


@pytest.fixture()
def patched_generator(monkeypatch):
    """Patch search + LLM + verifier so generate_document yields deterministic,
    controllable section text. ``holder['section']`` sets the drafted prose."""
    import tools.document_intelligence.doc_generator as dg
    import tools.document_intelligence.search_engine as se
    import tools.document_intelligence.verifier as ver

    holder = {"section": "TLS 1.3 secures all endpoints [source: chunk c1]."}

    class FakeEngine:
        def __init__(self, *a, **k):
            pass

        def search(self, *a, **k):
            return [_fake_search_result("c1")]

    def fake_llm(prompt, **k):
        if "outline" in prompt.lower():
            return '{"title": "Modernized Doc", "sections": [{"heading": "Overview", "summary": "s"}]}'
        return holder["section"]

    def fake_verify(text, contents):
        return SimpleNamespace(abstained=False, verified_text=text, claims=[])

    monkeypatch.setattr(se, "DICSearchEngine", FakeEngine)
    monkeypatch.setattr(dg, "_llm_generate", fake_llm)
    monkeypatch.setattr(ver, "verify", fake_verify)
    return holder


def _seed_approved_doc(collection_id="col1", doc_id=None):
    from tools.db.storage import get_connection

    doc_id = doc_id or f"doc-{uuid.uuid4().hex[:8]}"
    ver_id = f"ver-{uuid.uuid4().hex[:8]}"
    conn = get_connection()
    conn.execute(
        "INSERT INTO dic_documents (doc_id, collection_id, title, tenant_id, classification, created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (doc_id, collection_id, "Legacy Doc", "default", "CUI", "2020-01-01"),
    )
    conn.execute(
        "INSERT INTO dic_versions (version_id, doc_id, version_no, origin, status, created_at, created_by, "
        "tenant_id, classification) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (ver_id, doc_id, 1, "human_authored", "approved", "2020-01-01", "author", "default", "CUI"),
    )
    conn.execute(
        "INSERT INTO dic_sections (section_id, version_id, doc_id, heading, content, status, created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (f"sec-{uuid.uuid4().hex[:8]}", ver_id, doc_id, "Overview",
         "The system uses TLS 1.1 for transport security.", "approved", "2020-01-01"),
    )
    conn.commit()
    conn.close()
    return doc_id


def _version_status(version_id):
    from tools.db.storage import get_connection

    conn = get_connection()
    row = conn.execute(
        "SELECT status FROM dic_versions WHERE version_id = %s", (version_id,)
    ).fetchone()
    conn.close()
    return dict(row)["status"] if row else None


def _notes_for(version_id):
    from tools.db.storage import get_connection

    conn = get_connection()
    rows = conn.execute(
        "SELECT note_text FROM dic_review_notes WHERE item_id = %s", (version_id,)
    ).fetchall()
    conn.close()
    return [dict(r)["note_text"] for r in rows]


def test_clean_regeneration_reaches_pending_review(db, patched_generator):
    patched_generator["section"] = "TLS 1.3 secures all endpoints [source: chunk c1]."
    from tools.doc_modernization.regen_orchestrator import regenerate_document

    doc_id = _seed_approved_doc()
    out = regenerate_document(doc_id)

    assert not out.get("error"), out
    assert out["blocked"] is False
    assert out["status"] == "pending_review"
    assert _version_status(out["new_version_id"]) == "pending_review"
    assert out["quality_gate"]["blocked"] is False
    # No block/force audit note for a clean regeneration.
    assert _notes_for(out["new_version_id"]) == []


def test_uncited_regeneration_blocked_and_withheld(db, patched_generator):
    patched_generator["section"] = "TLS 1.3 secures all endpoints."  # no [source:] tag
    from tools.doc_modernization.regen_orchestrator import regenerate_document
    from tools.doc_modernization.regen_orchestrator import BLOCKED_STATUS

    doc_id = _seed_approved_doc()
    out = regenerate_document(doc_id)

    assert out["blocked"] is True
    assert out["status"] == BLOCKED_STATUS
    assert _version_status(out["new_version_id"]) == BLOCKED_STATUS
    assert BLOCK_MISSING_CITATIONS in out["quality_gate"]["reasons"]
    # A blocking decision is audited (append-only note), never a dic_versions mutation.
    notes = _notes_for(out["new_version_id"])
    assert notes and "BLOCKED" in notes[0]


def test_force_override_promotes_and_audits(db, patched_generator):
    patched_generator["section"] = "TLS 1.3 secures all endpoints."  # still uncited → would block
    from tools.doc_modernization.regen_orchestrator import regenerate_document

    doc_id = _seed_approved_doc()
    out = regenerate_document(doc_id, force=True, reviewer="alice")

    assert out["forced"] is True
    assert out["blocked"] is False
    assert out["status"] == "pending_review"
    assert _version_status(out["new_version_id"]) == "pending_review"
    notes = _notes_for(out["new_version_id"])
    assert notes and "FORCE-REGENERATED" in notes[0]


def test_generate_document_gate_none_is_backcompat(db, patched_generator):
    """gate=None (default) preserves historical unconditional pending_review."""
    from tools.document_intelligence.doc_generator import generate_document

    res = generate_document(
        query="Draft a doc",
        collection_id="col1",
        tenant_id="default",
        classification="CUI",
    )
    assert res.status == "pending_review"
    assert _version_status(res.version_id) == "pending_review"
