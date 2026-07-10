# CUI // SP-CTI
"""docmod-bridge: api_generate must persist the generated DIC doc_id on the
idr_session (root cause of the empty-scaffold Tech Writer imports)."""
from __future__ import annotations

import importlib
from types import SimpleNamespace

import flask
import pytest


@pytest.fixture()
def docgen_client():
    from tools.docgen.blueprint import docgen_bp

    app = flask.Flask(__name__)
    app.register_blueprint(docgen_bp, url_prefix="/docgen")
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_api_generate_persists_dic_doc_id(docgen_client, monkeypatch):
    sm = importlib.import_module("tools.docgen.session_manager")
    workflow = importlib.import_module("tools.docgen.workflow")
    ctx_builder = importlib.import_module("tools.docgen.context_builder")
    doc_gen = importlib.import_module("tools.document_intelligence.doc_generator")

    session = {
        "id": "ses-persist", "title": "T", "domain": "network",
        "doc_type": "runbook", "classification": "CUI",
    }
    set_field_calls: list[dict] = []

    monkeypatch.setattr(sm, "get_session", lambda sid: dict(session))
    monkeypatch.setattr(sm, "list_uploads", lambda sid: [])
    monkeypatch.setattr(sm, "list_analyses", lambda sid: [])
    monkeypatch.setattr(sm, "pending_conflict_count", lambda sid: 0, raising=False)
    monkeypatch.setattr(
        sm, "set_field",
        lambda sid, **kw: set_field_calls.append({"session_id": sid, **kw}) or True,
    )
    monkeypatch.setattr(workflow, "stage3_check_gate", lambda sid: True)
    monkeypatch.setattr(workflow, "advance", lambda sid, stage: None)
    monkeypatch.setattr(
        ctx_builder, "build_context",
        lambda **kw: {
            "query_string": "q", "session_id": "col-abc", "ace_roles": [],
            "topology_summary": {}, "config_findings": [],
            "supplemental_text": "", "kg_chunks": [],
        },
    )
    monkeypatch.setattr(
        doc_gen, "generate_document",
        lambda **kw: SimpleNamespace(
            doc_id="doc-generated-1",
            sections=[SimpleNamespace(heading="Overview", content="Body text.")],
        ),
    )

    resp = docgen_client.post("/docgen/api/sessions/ses-persist/generate", json={})
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["doc_id"] == "doc-generated-1"

    # final_doc_text persisted AND the doc id linked on the session
    flat = {k: v for call in set_field_calls for k, v in call.items()}
    assert flat.get("dic_doc_id") == "doc-generated-1"
    assert flat.get("dic_collection_id") == "col-abc"
    assert "## Overview" in (flat.get("final_doc_text") or "")
