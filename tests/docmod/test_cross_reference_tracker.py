# CUI // SP-CTI
"""dmx-ref-01: inter-document cross-reference tracking + cascade flagging.

Covers deterministic extraction (each pattern trips + a false-positive-clean
document), resolution (fill target_doc_id / dangling-reference finding), and the
cascade on a version approval whose changed sections intersect inbound
references. All DB work uses the shared conftest SQLite backend — never the real
data/icdev.db corpus.
"""
from __future__ import annotations

import uuid

import pytest

from tools.document_intelligence import cross_reference_tracker as xrt

# dic_* tables the tracker reads/writes. docmod_* + dic_cross_references DDL come
# from tests/conftest MINIMAL_ICDEV_SCHEMA so they always match canonical shape.
# Superset shape shared with tests/docmod/test_core_engine.py and
# test_import_from_docgen.py — the first CREATE wins under IF NOT EXISTS, so all
# three files MUST agree on the columns.
_DIC_DDL = [
    """CREATE TABLE IF NOT EXISTS dic_documents (
        doc_id TEXT PRIMARY KEY, collection_id TEXT, title TEXT, filename TEXT,
        status TEXT, origin TEXT, classification TEXT, template_type TEXT,
        writeguard_mode TEXT, source_idr_session_id TEXT, source_wg_result_id TEXT,
        tenant_id TEXT, created_at TEXT)""",
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
]
_DOCMOD_KEYS = ("docmod_scan_runs", "docmod_findings")
_XREF_KEY = "dic_cross_references"


@pytest.fixture()
def db():
    from tests.conftest import MINIMAL_ICDEV_SCHEMA
    from tools.db.storage import get_connection

    conn = get_connection()
    for ddl in _DIC_DDL:
        conn.execute(ddl)
    for stmt in MINIMAL_ICDEV_SCHEMA.split(";"):
        if "CREATE" in stmt and (
            _XREF_KEY in stmt or any(k in stmt for k in _DOCMOD_KEYS)
        ):
            conn.execute(stmt)
    conn.commit()
    conn.close()
    yield


def _seed_doc(title: str, sections, doc_id=None, version_no=1, status="approved",
              tenant_id="acme", classification="CUI", filename=None):
    """Insert a document + one approved version + its sections. Returns
    (doc_id, version_id)."""
    from tools.db.storage import get_connection

    doc_id = doc_id or f"doc-{uuid.uuid4().hex[:8]}"
    version_id = f"{doc_id}_v{version_no}"
    conn = get_connection()
    if version_no == 1:
        conn.execute(
            "INSERT INTO dic_documents (doc_id, collection_id, title, filename, "
            "status, origin, classification, tenant_id, created_at) "
            "VALUES (%s,'col-t',%s,%s,'approved','human_authored',%s,%s,'2026-01-01')",
            (doc_id, title, filename or f"{title}.pdf", classification, tenant_id),
        )
    conn.execute(
        "INSERT INTO dic_versions (version_id, doc_id, version_no, origin, status, "
        "created_at, tenant_id, classification) "
        "VALUES (%s,%s,%s,'human_authored',%s,'2026-01-01',%s,%s)",
        (version_id, doc_id, version_no, status, tenant_id, classification),
    )
    for i, (heading, content) in enumerate(sections):
        conn.execute(
            "INSERT INTO dic_sections (section_id, version_id, doc_id, heading, "
            "content, created_at, tenant_id, classification) "
            "VALUES (%s,%s,%s,%s,%s,'2026-01-01',%s,%s)",
            (f"{version_id}_s{i}", version_id, doc_id, heading, content,
             tenant_id, classification),
        )
    conn.commit()
    conn.close()
    return doc_id, version_id


# ── Extraction ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, exp_doc, exp_section", [
    ("Restore steps: see Section 3 of the Backup SOP before proceeding.",
     "Backup SOP", "3"),
    ("Escalate per the Incident Response Policy §4.2 within one hour.",
     "Incident Response Policy", "4.2"),
    ("For rollback, refer to the Change Management Plan, Section 2.",
     "Change Management Plan", "2"),
    ("Recovery is as described in the Disaster Recovery Runbook.",
     "Disaster Recovery Runbook", ""),
])
def test_each_pattern_trips(text, exp_doc, exp_section):
    refs = xrt.extract_references(text, "doc-src", source_section="Body")
    assert len(refs) == 1, refs
    assert refs[0]["target_doc_ref"] == exp_doc
    assert refs[0]["target_section"] == exp_section
    assert refs[0]["source_doc_id"] == "doc-src"


def test_false_positive_clean():
    clean = (
        "The team reviews backups every night and confirms the schedule. "
        "Operators verify results and record any anomalies in the log."
    )
    assert xrt.extract_references(clean, "doc-src") == []


def test_extraction_dedupes_repeats():
    text = ("see Section 3 of the Backup SOP ... later ... "
            "see Section 3 of the Backup SOP again")
    refs = xrt.extract_references(text, "doc-src", source_section="S")
    assert len(refs) == 1


# ── Store + idempotency ─────────────────────────────────────────────────────────

def test_store_is_idempotent_and_rls_stamped(db):
    from tools.db.storage import get_connection

    conn = get_connection()
    text = "Restore per the Backup SOP: see Section 3 of the Backup SOP."
    first = xrt.store_references_from_text(
        conn, "doc-citing", text, source_section="Restore",
        tenant_id="acme", classification="CUI")
    assert first == 1
    second = xrt.store_references_from_text(
        conn, "doc-citing", text, source_section="Restore",
        tenant_id="acme", classification="CUI")
    assert second == 0  # deterministic id -> no duplicate row
    row = conn.execute(
        "SELECT tenant_id, classification, target_doc_id FROM dic_cross_references "
        "WHERE source_doc_id='doc-citing'"
    ).fetchone()
    assert row["tenant_id"] == "acme"
    assert row["classification"] == "CUI"
    assert row["target_doc_id"] is None  # unresolved until resolution pass
    conn.close()


# ── Resolution ──────────────────────────────────────────────────────────────────

def test_resolution_fills_target_doc_id(db):
    from tools.db.storage import get_connection

    target_id, _ = _seed_doc("Backup SOP", [("1. Overview", "text")])
    citing_id, _ = _seed_doc("Runbook One", [("1. Steps", "steps")])
    conn = get_connection()
    xrt.store_references_from_text(
        conn, citing_id, "see Section 3 of the Backup SOP", source_section="Steps",
        tenant_id="acme", classification="CUI")
    conn.commit()
    conn.close()

    out = xrt.resolve_references()
    assert out["resolved"] >= 1

    conn = get_connection()
    row = conn.execute(
        "SELECT target_doc_id FROM dic_cross_references WHERE source_doc_id=%s",
        (citing_id,),
    ).fetchone()
    assert row["target_doc_id"] == target_id
    conn.close()


def test_dangling_reference_becomes_finding(db):
    from tools.db.storage import get_connection

    citing_id, _ = _seed_doc("Ops Guide", [("1. Intro", "intro")])
    conn = get_connection()
    xrt.store_references_from_text(
        conn, citing_id, "as described in the Nonexistent Ghost Runbook",
        source_section="Intro", tenant_id="acme", classification="CUI")
    conn.commit()
    conn.close()

    out = xrt.resolve_references()
    assert out["dangling"] >= 1

    conn = get_connection()
    finding = conn.execute(
        "SELECT finding_type, tenant_id, classification, state FROM docmod_findings "
        "WHERE doc_id=%s AND finding_type='dangling_reference'",
        (citing_id,),
    ).fetchone()
    assert finding is not None
    assert finding["state"] == "open"
    assert finding["tenant_id"] == "acme"
    # Idempotent: a second resolve does not double-raise the finding.
    row = conn.execute(
        "SELECT id FROM dic_cross_references WHERE source_doc_id=%s", (citing_id,)
    ).fetchone()
    assert row is not None
    conn.close()
    xrt.resolve_references()  # second pass must not double-raise
    conn = get_connection()
    n = conn.execute(
        "SELECT COUNT(*) AS c FROM docmod_findings WHERE doc_id=%s "
        "AND finding_type='dangling_reference' AND state='open'",
        (citing_id,),
    ).fetchone()
    assert n["c"] == 1
    conn.close()


# ── Cascade ─────────────────────────────────────────────────────────────────────

def _insert_resolved_ref(source_doc_id, target_doc_id, target_section, ref_text,
                         tenant_id="acme", classification="CUI"):
    from tools.db.storage import get_connection

    conn = get_connection()
    conn.execute(
        "INSERT INTO dic_cross_references (id, source_doc_id, source_section, "
        "target_doc_ref, target_doc_id, target_section, ref_text, tenant_id, "
        "classification, extracted_at) "
        "VALUES (%s,%s,'Body',%s,%s,%s,%s,%s,%s,'2026-01-01')",
        (f"xref-{uuid.uuid4().hex[:12]}", source_doc_id, "ref", target_doc_id,
         target_section, ref_text, tenant_id, classification),
    )
    conn.commit()
    conn.close()


def test_cascade_on_changed_section(db):
    from tools.db.storage import get_connection

    # Target v1 approved, then v2 approved with Section 2 content changed.
    target_id, _ = _seed_doc(
        "Backup SOP",
        [("1. Overview", "overview text"), ("2. Backup", "old backup steps")],
    )
    _, v2 = _seed_doc(
        "Backup SOP",
        [("1. Overview", "overview text"), ("2. Backup", "NEW backup steps")],
        doc_id=target_id, version_no=2,
    )
    citing_changed, _ = _seed_doc("Cites Sec 2", [("1", "x")])
    citing_unchanged, _ = _seed_doc("Cites Sec 1", [("1", "y")])
    _insert_resolved_ref(citing_changed, target_id, "2",
                         "see Section 2 of the Backup SOP")
    _insert_resolved_ref(citing_unchanged, target_id, "1",
                         "see Section 1 of the Backup SOP")

    out = xrt.cascade_on_version_approval(v2)
    assert out["target_doc_id"] == target_id
    assert "2" in out["changed_sections"]
    assert "1" not in out["changed_sections"]
    assert out["cascaded"] == 1

    conn = get_connection()
    hit = conn.execute(
        "SELECT finding_type, currency_verdict FROM docmod_findings "
        "WHERE doc_id=%s AND finding_type='cross_reference_cascade'",
        (citing_changed,),
    ).fetchone()
    assert hit is not None
    assert hit["currency_verdict"] == "divergent"
    miss = conn.execute(
        "SELECT COUNT(*) AS c FROM docmod_findings WHERE doc_id=%s",
        (citing_unchanged,),
    ).fetchone()
    assert miss["c"] == 0  # its cited section did not change
    conn.close()


def test_cascade_is_idempotent(db):
    from tools.db.storage import get_connection

    target_id, _ = _seed_doc("Recovery Plan", [("2. Steps", "old")])
    _, v2 = _seed_doc("Recovery Plan", [("2. Steps", "new")],
                      doc_id=target_id, version_no=2)
    citing, _ = _seed_doc("Cites It", [("1", "z")])
    _insert_resolved_ref(citing, target_id, "2",
                         "refer to the Recovery Plan, Section 2")

    first = xrt.cascade_on_version_approval(v2)
    second = xrt.cascade_on_version_approval(v2)
    assert first["cascaded"] == 1
    assert second["cascaded"] == 0  # stable dedupe_key -> no re-raise

    conn = get_connection()
    n = conn.execute(
        "SELECT COUNT(*) AS c FROM docmod_findings WHERE doc_id=%s "
        "AND finding_type='cross_reference_cascade' AND state='open'",
        (citing,),
    ).fetchone()
    assert n["c"] == 1
    conn.close()
