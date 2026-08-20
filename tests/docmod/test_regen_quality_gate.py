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
    # docmod_scan_runs + docmod_findings: created here so the fixture is
    # self-sufficient on a COLD data/icdev.db. Relying on "the docmod migration
    # provides them" made the end-to-end tests pass only when a warm DB already
    # had the tables — and, worse, the missing-table error inside
    # regenerate_document leaked an open write-lock that deadlocked a later
    # test file's DELETE-FROM cleanup. Canonical shape mirrors conftest
    # MINIMAL_ICDEV_SCHEMA.
    """CREATE TABLE IF NOT EXISTS docmod_scan_runs (
        run_id TEXT PRIMARY KEY, scope_type TEXT NOT NULL DEFAULT 'all',
        scope_id TEXT, pack_ids TEXT DEFAULT '[]', evidence_hash TEXT,
        docs_scanned INTEGER NOT NULL DEFAULT 0, findings_new INTEGER NOT NULL DEFAULT 0,
        findings_resolved INTEGER NOT NULL DEFAULT 0,
        started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, finished_at TIMESTAMP,
        triggered_by TEXT NOT NULL DEFAULT 'manual', tenant_id TEXT,
        classification TEXT DEFAULT 'CUI')""",
    """CREATE TABLE IF NOT EXISTS docmod_findings (
        finding_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, doc_id TEXT NOT NULL,
        version_id TEXT, chunk_link_id TEXT, section_heading TEXT, page INTEGER,
        pack_id TEXT NOT NULL, entity_label TEXT NOT NULL, entity_type TEXT NOT NULL,
        finding_type TEXT NOT NULL, currency_verdict TEXT NOT NULL DEFAULT 'unknown',
        severity TEXT NOT NULL DEFAULT 'medium', rationale TEXT,
        evidence_json TEXT DEFAULT '[]', recommended_replacement TEXT,
        replacement_evidence_json TEXT DEFAULT '[]', confidence REAL NOT NULL DEFAULT 0.0,
        state TEXT NOT NULL DEFAULT 'open', redline_suggestion_id TEXT,
        prediction_id TEXT, dedupe_key TEXT NOT NULL, supersedes_id TEXT,
        tenant_id TEXT, classification TEXT DEFAULT 'CUI',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
]


def _dic_doc_cols():
    """Every dic_documents column in `_DDL`, DERIVED rather than re-listed.

    `CREATE TABLE IF NOT EXISTS` never alters an existing table, so on a database
    that already carries a partial `dic_documents` — which is exactly what CI's
    consolidated schema provides — the DDL above is a no-op and the INSERT fails
    on whatever column is missing. The fixture therefore ADDs the missing ones
    idempotently, the same non-destructive pattern init_db uses.

    This list used to be maintained by hand and had drifted: it omitted
    `filename`, so this file passed locally (where the table is created fresh,
    WITH the column) and failed the moment CI ran it — "table dic_documents has
    no column named filename". Deriving it from `_DDL` means the two cannot
    disagree again, which is the only reason the hand-written version was wrong.
    """

    ddl = next(d for d in _DDL if "dic_documents" in d)
    body = ddl[ddl.index("(") + 1: ddl.rindex(")")]
    cols = []
    for part in body.split(","):
        tokens = part.strip().split()
        if len(tokens) >= 2 and tokens[0] != "PRIMARY":
            name, typ = tokens[0], tokens[1]
            if name == "doc_id":       # the primary key always exists
                continue
            cols.append((name, typ))
    return cols


_DIC_DOC_COLS = _dic_doc_cols()


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
        """Model the REAL verifier closely enough that confidence is real.

        Two attributes were missing and each broke the suite SILENTLY.

        `verified`: f1cef3f37 ("resurrect the claim verifier") made
        doc_generator read `vr.verified`. A stub without it raises
        AttributeError inside doc_generator's `except Exception`, which drops
        confidence to 0.

        `claims[].method`: `_compute_section_confidence` counts ONLY claims
        whose method is not "uncited", and returns 0.0 when none qualify. A stub
        returning `claims=[]` therefore scored EVERY section 0.0, which abstains
        it, and `_section_dicts` DROPS abstained sections - so the citation
        check this file exists to exercise never ran on anything.

        Both were invisible twice over: doc_generator swallows the error into a
        warning, and `icdev_logger` detaches from the root logger, so nothing
        reached pytest output. The tests just reported `blocked is False`.
        """
        cited = "[source:" in (text or "")
        claims = [SimpleNamespace(method="cited", supported=True, text=text)] if cited else []
        return SimpleNamespace(abstained=False, verified=cited,
                               verified_text=text, claims=claims)

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


def _version_text(version_id):
    """Reassembled markdown of a persisted version, so a test can assert what
    actually REACHED the document rather than what a flag said about it.

    Reads dic_sections the same way `_approved_text` does; dic_versions has
    no prose column.
    """
    from tools.db.storage import get_connection

    conn = get_connection()
    rows = conn.execute(
        "SELECT heading, content FROM dic_sections WHERE version_id = %s "
        "ORDER BY section_id", (version_id,)
    ).fetchall()
    conn.close()
    parts = []
    for r in rows:
        row = dict(r)
        parts.append("## " + str(row["heading"]) + "\n\n" + str(row["content"]))
    return "\n\n".join(parts)


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


def test_uncited_regeneration_is_withheld_by_abstention(db, patched_generator):
    """Uncited prose must never reach the document. The MECHANISM moved.

    This asserted `blocked is True` and had been failing on main. The protection
    did not disappear; it changed shape, and the assertion was left describing
    the old one.

    `_compute_section_confidence` counts only CITED claims and returns 0.0 when
    there are none, so an uncited section scores 0.0, the confidence band
    ABSTAINS it, and the prose is replaced with the "(Abstained - ...)" sentinel
    before anything is persisted. `_section_dicts` then drops abstained
    sections, which is why `missing_citations` is unreachable through this path:
    no section can arrive at the citation check both uncited and non-abstained.

    So the guarantee worth asserting is the strong one - the uncited sentence is
    NOT IN THE DOCUMENT - rather than the flag that used to carry it.
    """
    patched_generator["section"] = "TLS 1.3 secures all endpoints."  # no [source:]
    from tools.doc_modernization.regen_orchestrator import regenerate_document

    out = regenerate_document(_seed_approved_doc())
    assert not out.get("error"), out
    text = _version_text(out["new_version_id"]) or ""
    assert "TLS 1.3 secures all endpoints." not in text, (
        "uncited prose reached the persisted version")
    assert "Abstained" in text, "the section should carry the abstention sentinel"


def test_the_gate_says_when_it_examined_NOTHING(db, patched_generator):
    """A draft whose every section abstained came back `blocked: False,
    reasons: []` - a clean bill of health for a document nobody examined.

    The counters make the two zeroes distinguishable: "checked, found nothing"
    versus "there was nothing to check".
    """
    patched_generator["section"] = "TLS 1.3 secures all endpoints."  # uncited
    from tools.doc_modernization.regen_orchestrator import regenerate_document

    out = regenerate_document(_seed_approved_doc())
    citation = (out.get("quality_gate") or {}).get("citation") or {}
    assert citation.get("sections_submitted", 0) > 0, "sections were drafted"
    assert citation.get("sections_examined") == 0, (
        "every section abstained, so none reached the citation check")


def test_force_override_promotes_and_audits(db, patched_generator):
    """`force=True` promotes past a blocking gate and audits the override.

    Driven by a HALLUCINATED citation rather than an uncited one. A citation to
    a chunk outside the evidence backing this regeneration is a defect the gate
    can actually SEE: the section is cited, so it scores confidence and is not
    abstained, so it reaches `_citation_findings`. An uncited draft abstains
    first and never blocks, which is why this test could not use one and had
    been failing on main.
    """
    patched_generator["section"] = "TLS 1.3 secures all endpoints [source: chunk c9]."
    from tools.doc_modernization.regen_orchestrator import BLOCKED_STATUS
    from tools.doc_modernization.regen_orchestrator import regenerate_document

    doc_id = _seed_approved_doc()
    blocked_run = regenerate_document(doc_id)
    assert blocked_run["blocked"] is True, blocked_run.get("quality_gate")
    assert "hallucinated_citation" in (blocked_run["quality_gate"] or {})["reasons"]
    assert _version_status(blocked_run["new_version_id"]) == BLOCKED_STATUS

    forced = regenerate_document(doc_id, force=True)
    assert forced["blocked"] is False and forced["forced"] is True
    assert _version_status(forced["new_version_id"]) == "pending_review"


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
