# CUI // SP-CTI
"""docmod-bridge: docgen -> Tech Writer bridge tests.

Covers the api_import_from_docgen rewrite (Path A: reuse the DIC document
generation created; Path B: rebuild sections from final_doc_text; fallback:
empty scaffold) plus the heading fuzzy-mapper and template resolution.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import flask
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

_DIC_DDL = [
    """CREATE TABLE IF NOT EXISTS dic_collections (
        collection_id TEXT PRIMARY KEY, name TEXT, tenant_id TEXT,
        classification TEXT, created_at TEXT)""",
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
    # idr_sessions matches tests/conftest MINIMAL_ICDEV_SCHEMA (subset used here);
    # created locally because the shared schema is only applied by the opt-in
    # icdev_db fixture, which does not wire get_connection().
    """CREATE TABLE IF NOT EXISTS idr_sessions (
        id TEXT PRIMARY KEY, title TEXT NOT NULL,
        domain TEXT NOT NULL DEFAULT 'network',
        doc_type TEXT NOT NULL DEFAULT 'runbook',
        template_id TEXT, stage INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'setup',
        dic_collection_id TEXT, wg_result_id TEXT, final_doc_text TEXT,
        dic_doc_id TEXT, source_dic_doc_id TEXT,
        created_by TEXT, tenant_id TEXT, classification TEXT DEFAULT 'CUI',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP)""",
]


@pytest.fixture()
def dic_db():
    from tools.db.storage import get_connection

    conn = get_connection()
    for ddl in _DIC_DDL:
        conn.execute(ddl)
    conn.commit()
    conn.close()
    yield


@pytest.fixture()
def client():
    from tools.document_intelligence.blueprint import dic_bp

    app = flask.Flask(__name__)
    app.register_blueprint(dic_bp, url_prefix="/document-intelligence")
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _seed_session(**overrides):
    """Insert an idr_sessions row (schema from tests/conftest MINIMAL_ICDEV_SCHEMA)."""
    from tools.db.storage import get_connection

    row = {
        "id": overrides.get("id", f"ses-{uuid.uuid4().hex[:8]}"),
        "title": "Bridge Test",
        "domain": "network",
        "doc_type": "runbook",
        "stage": 5,
        "status": "generating",
        "dic_collection_id": None,
        "wg_result_id": None,
        "final_doc_text": None,
        "dic_doc_id": None,
    }
    row.update(overrides)
    conn = get_connection()
    conn.execute(
        """INSERT INTO idr_sessions
           (id, title, domain, doc_type, stage, status, dic_collection_id,
            wg_result_id, final_doc_text, dic_doc_id)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            row["id"], row["title"], row["domain"], row["doc_type"], row["stage"],
            row["status"], row["dic_collection_id"], row["wg_result_id"],
            row["final_doc_text"], row["dic_doc_id"],
        ),
    )
    conn.commit()
    conn.close()
    return row


def _fetch(sql, params=()):
    from tools.db.storage import get_connection

    conn = get_connection()
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


# ── _split_generated_doc ──────────────────────────────────────────────────────

def test_split_maps_fuzzy_headings_onto_template():
    from tools.document_intelligence.blueprint import _split_generated_doc

    text = (
        "## Overview\nGenerated overview text.\n\n"
        "## Pre-flight Checks\nCheck A.\n\n"
        "## Totally Novel Section\nExtra content.\n"
    )
    template = ["Overview", "Prerequisites", "Pre-flight Checks"]
    pairs = _split_generated_doc(text, template)

    assert pairs[0] == ("Overview", "Generated overview text.")
    assert pairs[1] == ("Prerequisites", "")  # empty stub for unmatched template heading
    assert pairs[2] == ("Pre-flight Checks", "Check A.")
    # unmatched generated content is appended, never dropped
    assert ("Totally Novel Section", "Extra content.") in pairs[3:]


def test_split_handles_empty_text():
    from tools.document_intelligence.blueprint import _split_generated_doc

    pairs = _split_generated_doc("", ["Purpose", "Scope"])
    assert pairs == [("Purpose", ""), ("Scope", "")]


# ── Route: template resolution ────────────────────────────────────────────────

def test_import_resolves_template_from_session_doc_type(client, dic_db):
    ses = _seed_session(doc_type="sop", final_doc_text="## Purpose\nWhy we exist.")
    resp = client.post(
        "/document-intelligence/api/import-from-docgen",
        json={"session_id": ses["id"], "title": "SOP Import"},
    )
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()
    assert data["template_type"] == "SOP"
    assert data["writeguard_mode"] == "sop_runbook"


def test_import_invalid_template_type_still_400(client):
    resp = client.post(
        "/document-intelligence/api/import-from-docgen",
        json={"session_id": "nope", "title": "x", "template_type": "INVALID"},
    )
    assert resp.status_code == 400


# ── Route: Path A (reuse generated document) ──────────────────────────────────

def test_path_a_reuses_generated_document(client, dic_db):
    from tools.db.storage import get_connection

    doc_id = f"doc-{uuid.uuid4().hex[:8]}"
    conn = get_connection()
    conn.execute(
        """INSERT INTO dic_documents
           (doc_id, collection_id, title, status, origin, classification, created_at)
           VALUES (%s,%s,%s,'pending_review','ai_generated','CUI','2026-01-01')""",
        (doc_id, "col-gen", "Generated Doc"),
    )
    conn.commit()
    conn.close()

    ses = _seed_session(doc_type="runbook", dic_doc_id=doc_id, wg_result_id="wg-123")
    resp = client.post(
        "/document-intelligence/api/import-from-docgen",
        json={"session_id": ses["id"], "title": "Reuse Me"},
    )
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()
    assert data["path"] == "reused_generated_doc"
    assert data["doc_id"] == doc_id
    assert data["collection_id"] == "col-gen"

    row = _fetch("SELECT * FROM dic_documents WHERE doc_id = %s", (doc_id,))[0]
    assert row["template_type"] == "RUNBOOK"
    assert row["source_idr_session_id"] == ses["id"]
    assert row["source_wg_result_id"] == "wg-123"
    # No duplicate scaffold created for this session — the only doc linked to it
    # is the reused generated one (DB file is shared across tests, so scope by session).
    linked = _fetch(
        "SELECT doc_id FROM dic_documents WHERE source_idr_session_id = %s", (ses["id"],)
    )
    assert [r["doc_id"] for r in linked] == [doc_id]


# ── Route: Path B (rebuild from final_doc_text) ───────────────────────────────

def test_path_b_rebuilds_sections_pending_review(client, dic_db):
    text = "## Purpose\nKeep TLS 1.2+ everywhere. [source: chunk 3]\n\n## Procedure\nDo the steps."
    ses = _seed_session(doc_type="sop", final_doc_text=text, dic_collection_id="col-ses")
    resp = client.post(
        "/document-intelligence/api/import-from-docgen",
        json={"session_id": ses["id"], "title": "Legacy Import"},
    )
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()
    assert data["path"] == "rebuilt_from_text"
    # collection reused from the session — no docgen-import-* minting
    assert data["collection_id"] == "col-ses"

    doc = _fetch("SELECT * FROM dic_documents WHERE doc_id = %s", (data["doc_id"],))[0]
    assert doc["origin"] == "ai_generated"
    assert doc["status"] == "pending_review"

    versions = _fetch("SELECT * FROM dic_versions WHERE doc_id = %s", (data["doc_id"],))
    assert len(versions) == 1 and versions[0]["origin"] == "ai_generated"

    sections = _fetch(
        "SELECT * FROM dic_sections WHERE doc_id = %s ORDER BY section_id",
        (data["doc_id"],),
    )
    by_heading = {s["heading"]: s for s in sections}
    assert "[source: chunk 3]" in by_heading["Purpose"]["content"]  # citations preserved
    assert by_heading["Purpose"]["status"] == "pending_review"
    assert by_heading["Purpose"]["origin"] == "ai_generated"
    assert by_heading["Procedure"]["content"] == "Do the steps."
    # every section carries the version (old code violated NOT NULL here)
    assert all(s["version_id"] for s in sections)


# ── Route: fallback scaffold ──────────────────────────────────────────────────

def test_fallback_scaffold_is_human_authored_with_version(client, dic_db):
    resp = client.post(
        "/document-intelligence/api/import-from-docgen",
        json={"session_id": "", "title": "Blank", "template_type": "RUNBOOK"},
    )
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()
    assert data["path"] == "empty_scaffold"

    doc = _fetch("SELECT * FROM dic_documents WHERE doc_id = %s", (data["doc_id"],))[0]
    assert doc["origin"] == "human_authored"
    sections = _fetch("SELECT * FROM dic_sections WHERE doc_id = %s", (data["doc_id"],))
    assert sections and all(s["version_id"] for s in sections)
    assert all((s["content"] or "") == "" for s in sections)


# ── Frontend: client map removed, button un-gated ─────────────────────────────

def test_template_has_no_client_map_or_gate():
    tpl = (REPO_ROOT / "tools" / "dashboard" / "templates" / "docgen" / "index.html").read_text(
        encoding="utf-8"
    )
    assert "var _DOCTYPE_TO_TEMPLATE" not in tpl  # client-side map removed
    assert "session.doc_type in ('runbook','design_doc','standard_guide')" not in tpl
    assert "openInTechWriter" in tpl


def test_doctype_map_covers_all_templates():
    from tools.document_intelligence.constants import (
        DOCGEN_DOCTYPE_TO_TEMPLATE,
        TEMPLATE_TYPES,
    )

    assert set(DOCGEN_DOCTYPE_TO_TEMPLATE.values()) <= set(TEMPLATE_TYPES)
    # every Tech Writer template is reachable from at least one docgen doc_type
    assert set(DOCGEN_DOCTYPE_TO_TEMPLATE.values()) == set(TEMPLATE_TYPES)
