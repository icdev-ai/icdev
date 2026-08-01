# CUI // SP-CTI
"""docmod-regen-01/02: 'Regenerate in DocGen' — prefill, session linkage,
generation targeting the same document, diff-view entry points."""
from __future__ import annotations

import importlib
import uuid
from pathlib import Path
from types import SimpleNamespace

import flask
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

_DDL = [
    """CREATE TABLE IF NOT EXISTS dic_documents (
        doc_id TEXT PRIMARY KEY, collection_id TEXT, source_id TEXT, filename TEXT,
        content_type TEXT, provider TEXT, title TEXT, byte_size INTEGER,
        content_sha256 TEXT, page_count INTEGER, status TEXT, origin TEXT,
        classification TEXT, template_type TEXT, writeguard_mode TEXT,
        source_idr_session_id TEXT, source_wg_result_id TEXT,
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
    # matches tests/conftest MINIMAL_ICDEV_SCHEMA idr_sessions (subset used here)
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
def db():
    from tools.db.storage import get_connection

    conn = get_connection()
    for ddl in _DDL:
        conn.execute(ddl)
    conn.commit()
    conn.close()
    yield


@pytest.fixture()
def client():
    from tools.docgen.blueprint import docgen_bp

    app = flask.Flask(
        __name__,
        template_folder=str(REPO_ROOT / "tools" / "dashboard" / "templates"),
    )
    app.register_blueprint(docgen_bp, url_prefix="/docgen")
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _seed_doc(template_type="STANDARD_GUIDE", content="Use TLS 1.1 everywhere."):
    from tools.db.storage import get_connection

    doc_id = f"doc-{uuid.uuid4().hex[:8]}"
    conn = get_connection()
    conn.execute(
        "INSERT INTO dic_documents (doc_id, collection_id, title, classification, "
        "template_type, created_at) VALUES (%s,'col-r','Network Standard','CUI',%s,'2026-01-01')",
        (doc_id, template_type),
    )
    conn.execute(
        "INSERT INTO dic_versions (version_id, doc_id, version_no, origin, status, created_at) "
        "VALUES (%s,%s,1,'human_authored','approved','2026-01-01')",
        (f"{doc_id}_v1", doc_id),
    )
    conn.execute(
        "INSERT INTO dic_sections (section_id, version_id, doc_id, heading, content, created_at) "
        "VALUES (%s,%s,%s,'Security',%s,'2026-01-01')",
        (f"{doc_id}-s0", f"{doc_id}_v1", doc_id, content),
    )
    conn.commit()
    conn.close()
    return doc_id


# ── regen-01: prefill ─────────────────────────────────────────────────────────
# base.html needs dashboard app context (nav_tree), so page tests capture the
# render context instead of rendering.

@pytest.fixture()
def captured_render(monkeypatch):
    bp_mod = importlib.import_module("tools.docgen.blueprint")
    captured = {}

    def fake_render(template, **ctx):
        captured["template"] = template
        captured.update(ctx)
        return ""

    monkeypatch.setattr(bp_mod, "render_template", fake_render)
    return captured


def test_new_page_prefills_from_source_doc(client, db, captured_render):
    doc_id = _seed_doc(template_type="STANDARD_GUIDE")
    resp = client.get(f"/docgen/new?source_doc_id={doc_id}")
    assert resp.status_code == 200
    sd = captured_render["source_doc"]
    assert sd["doc_id"] == doc_id
    assert sd["title"] == "Network Standard"
    assert sd["doc_type"] == "standard_guide"     # reverse-mapped from template_type


def test_new_page_unknown_source_doc_still_renders(client, db, captured_render):
    resp = client.get("/docgen/new?source_doc_id=doc-nope")
    assert resp.status_code == 200
    assert captured_render["source_doc"] is None


# ── regen-01: session linkage ────────────────────────────────────────────────

def test_create_session_persists_source_link(client, db, monkeypatch):
    doc_id = _seed_doc()
    resp = client.post("/docgen/api/sessions", json={
        "title": "Regen: Network Standard", "domain": "network",
        "doc_type": "standard_guide", "source_dic_doc_id": doc_id,
    })
    assert resp.status_code == 201, resp.get_json()
    session = resp.get_json()
    assert session["source_dic_doc_id"] == doc_id
    assert session["dic_collection_id"] == "col-r"  # evidence collection reused


# ── regen-01: generation targets the same doc ────────────────────────────────

def test_generate_passes_target_doc_and_regen_context(client, db, monkeypatch):
    doc_id = _seed_doc(content="All transport uses TLS 1.1 per legacy policy.")

    sm = importlib.import_module("tools.docgen.session_manager")
    workflow = importlib.import_module("tools.docgen.workflow")
    ctx_builder = importlib.import_module("tools.docgen.context_builder")
    doc_gen = importlib.import_module("tools.document_intelligence.doc_generator")

    session = {
        "id": "ses-regen", "title": "Regen", "domain": "network",
        "doc_type": "standard_guide", "classification": "CUI",
        "source_dic_doc_id": doc_id,
    }
    captured = {}
    monkeypatch.setattr(sm, "get_session", lambda sid: dict(session))
    monkeypatch.setattr(sm, "list_uploads", lambda sid: [])
    monkeypatch.setattr(sm, "list_analyses", lambda sid: [])
    monkeypatch.setattr(sm, "set_field", lambda sid, **kw: True)
    monkeypatch.setattr(workflow, "stage3_check_gate", lambda sid: True)
    monkeypatch.setattr(workflow, "advance", lambda sid, stage: None)
    monkeypatch.setattr(
        ctx_builder, "build_context",
        lambda **kw: {
            "query_string": "q", "session_id": "col-r", "ace_roles": [],
            "topology_summary": {}, "config_findings": [],
            "supplemental_text": "operator notes", "kg_chunks": [],
        },
    )

    def fake_gen(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(doc_id=doc_id, version_id="ver-2", sections=[])

    monkeypatch.setattr(doc_gen, "generate_document", fake_gen)

    resp = client.post("/docgen/api/sessions/ses-regen/generate", json={})
    assert resp.status_code == 200, resp.get_json()

    assert captured["target_doc_id"] == doc_id
    supp = captured["supplemental_text"]
    assert "operator notes" in supp                      # operator context preserved
    assert "CURRENT APPROVED DOCUMENT" in supp
    assert "TLS 1.1" in supp                             # old text threaded in


def test_generate_without_source_doc_unchanged(client, db, monkeypatch):
    sm = importlib.import_module("tools.docgen.session_manager")
    workflow = importlib.import_module("tools.docgen.workflow")
    ctx_builder = importlib.import_module("tools.docgen.context_builder")
    doc_gen = importlib.import_module("tools.document_intelligence.doc_generator")

    captured = {}
    monkeypatch.setattr(sm, "get_session", lambda sid: {
        "id": sid, "title": "T", "domain": "network", "doc_type": "runbook",
        "classification": "CUI",
    })
    monkeypatch.setattr(sm, "list_uploads", lambda sid: [])
    monkeypatch.setattr(sm, "list_analyses", lambda sid: [])
    monkeypatch.setattr(sm, "set_field", lambda sid, **kw: True)
    monkeypatch.setattr(workflow, "stage3_check_gate", lambda sid: True)
    monkeypatch.setattr(workflow, "advance", lambda sid, stage: None)
    monkeypatch.setattr(
        ctx_builder, "build_context",
        lambda **kw: {
            "query_string": "q", "session_id": "col-x", "ace_roles": [],
            "topology_summary": {}, "config_findings": [],
            "supplemental_text": "", "kg_chunks": [],
        },
    )
    monkeypatch.setattr(
        doc_gen, "generate_document",
        lambda **kw: captured.update(kw) or SimpleNamespace(doc_id="d", version_id="v", sections=[]),
    )
    resp = client.post("/docgen/api/sessions/ses-plain/generate", json={})
    assert resp.status_code == 200
    assert captured["target_doc_id"] is None


# ── regen-02: UI entry points ────────────────────────────────────────────────

def test_doc_detail_has_regen_button_and_diff_modal():
    tpl = (REPO_ROOT / "tools" / "dashboard" / "templates" / "document_intelligence"
           / "doc_detail.html").read_text(encoding="utf-8")
    assert "regen-in-docgen-btn" in tpl
    assert "/docgen/new?source_doc_id=" in tpl
    # side-by-side comparison already exists — the regen flow reuses it
    assert "openDiffModal" in tpl and "runDiff" in tpl
