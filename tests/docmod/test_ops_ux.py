# CUI // SP-CTI
"""docmod ops-01 + ux-03/ux-04: sweep reflex registration, YAML template
gallery with fallback, session refresh endpoint, techwriter enrichment."""
from __future__ import annotations

from pathlib import Path

import flask
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


# ── ops-01: reflex ────────────────────────────────────────────────────────────

def test_reflex_registered_in_all_three_points():
    daemon = (REPO_ROOT / "tools" / "genesis" / "daemon.py").read_text(encoding="utf-8")
    assert '"doc_modernization_sweep"' in daemon

    import yaml
    cfg = yaml.safe_load(
        (REPO_ROOT / "args" / "genesis_config.yaml").read_text(encoding="utf-8")
    )
    block = cfg.get("reflexes", {}).get("doc_modernization_sweep")
    assert block and block.get("enabled") is True
    assert block.get("interval_seconds") == 86400

    registry = (REPO_ROOT / "tools" / "genesis" / "reflex_registry.py").read_text(encoding="utf-8")
    assert "doc_modernization_sweep" in registry


def test_reflex_dry_run_returns_contract():
    from tools.genesis.reflexes.doc_modernization_sweep import run

    out = run({"dry_run": True})
    assert out["success"] is True
    assert "metric_value" in out
    details = out["details"]
    assert details["dry_run"] is True
    # dry run refreshes evidence but never scans/drafts/cards
    assert "scan" not in details and "redlines" not in details


def test_reflex_module_dispatchable_by_daemon_convention():
    import importlib

    module = importlib.import_module("tools.genesis.reflexes.doc_modernization_sweep")
    assert callable(getattr(module, "run"))
    assert getattr(module, "IMPLEMENTATION_STATUS") == "full"


# ── ux-03: YAML template gallery ─────────────────────────────────────────────

def test_gallery_loads_from_yaml_with_new_cards():
    from tools.docgen.domain_profiles import get_template, get_template_gallery

    gallery = get_template_gallery()
    ids = {t["id"] for t in gallery}
    # original five preserved
    assert {"tpl-network-runbook", "tpl-ir-playbook", "tpl-config-baseline",
            "tpl-ato-package", "tpl-change-request"} <= ids
    # new cards (docmod-ux-03)
    assert {"tpl-sop", "tpl-policy", "tpl-standard-guide"} <= ids

    sop = get_template("tpl-sop")
    assert sop["doc_type"] == "sop"
    policy = get_template("tpl-policy")
    assert policy["doc_type"] == "policy_doc"


def test_gallery_falls_back_to_python_list(monkeypatch, tmp_path):
    from tools.docgen import domain_profiles as dp

    monkeypatch.setattr(dp, "_TEMPLATES_PATH", tmp_path / "absent.yaml")
    monkeypatch.setattr(dp, "_tpl_cache", [], raising=False)
    monkeypatch.setattr(dp, "_tpl_cache_mtime", -1.0, raising=False)
    gallery = dp.get_template_gallery()
    assert gallery == dp.TEMPLATE_GALLERY  # Python fallback


# ── ux-03: session refresh endpoint ──────────────────────────────────────────

@pytest.fixture()
def docgen_client():
    from tools.docgen.blueprint import docgen_bp

    app = flask.Flask(__name__)
    app.register_blueprint(docgen_bp, url_prefix="/docgen")
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_refresh_clones_session_with_uploads(docgen_client, monkeypatch):
    import importlib

    sm = importlib.import_module("tools.docgen.session_manager")
    workflow = importlib.import_module("tools.docgen.workflow")

    old = {
        "id": "ses-old", "title": "Network Standard", "domain": "network",
        "doc_type": "runbook", "template_id": None, "created_by": "u1",
        "tenant_id": None, "classification": "CUI",
        "source_dic_doc_id": "doc-9", "dic_collection_id": "col-9",
    }
    created, added_uploads, set_fields, hashed = {}, [], [], []

    monkeypatch.setattr(sm, "get_session", lambda sid: dict(old) if sid == "ses-old" else None)
    monkeypatch.setattr(sm, "create_session", lambda **kw: created.update(kw) or {"id": "ses-new", **kw})
    monkeypatch.setattr(sm, "list_uploads", lambda sid: [
        {"filename": "topo.vsdx", "upload_type": "diagram", "file_path": "/x/topo.vsdx",
         "file_hash": "h1", "tenant_id": None},
        {"filename": "core.cfg", "upload_type": "config", "file_path": "/x/core.cfg",
         "file_hash": "h2", "tenant_id": None},
    ])
    monkeypatch.setattr(sm, "add_upload", lambda sid, **kw: added_uploads.append((sid, kw)))
    monkeypatch.setattr(sm, "set_field", lambda sid, **kw: set_fields.append((sid, kw)) or True)
    monkeypatch.setattr(workflow, "record_source_hash", lambda sid, paths: hashed.append((sid, paths)) or "hash")

    resp = docgen_client.post("/docgen/api/sessions/ses-old/refresh")
    assert resp.status_code == 201, resp.get_json()
    data = resp.get_json()
    assert data["session_id"] == "ses-new"
    assert data["uploads"] == 2

    assert created["title"].startswith("Network Standard")
    assert [u[0] for u in added_uploads] == ["ses-new", "ses-new"]
    assert hashed and hashed[0][0] == "ses-new" and len(hashed[0][1]) == 2
    # bridge linkage carried over
    assert set_fields and set_fields[0][1]["source_dic_doc_id"] == "doc-9"


def test_refresh_unknown_session_404(docgen_client, monkeypatch):
    import importlib

    sm = importlib.import_module("tools.docgen.session_manager")
    monkeypatch.setattr(sm, "get_session", lambda sid: None)
    assert docgen_client.post("/docgen/api/sessions/nope/refresh").status_code == 404


# ── ux-03/04: template markup ────────────────────────────────────────────────

def test_stepper_and_nav_markup():
    idx = (REPO_ROOT / "tools" / "dashboard" / "templates" / "docgen" / "index.html").read_text(
        encoding="utf-8"
    )
    for label in ("Setup", "Resolve Conflicts", "Quality Gate", "Review & Publish"):
        assert label in idx, f"stepper label {label!r} missing"
    assert "refreshSession" in idx
    assert "Tech Writer →" in idx

    tw = (REPO_ROOT / "tools" / "dashboard" / "templates" / "document_intelligence" / "techwriter.html").read_text(
        encoding="utf-8"
    )
    assert "needs-my-review" in tw
    assert "freshness_state" in tw
    # The invariant is the outbound link to the DocGen wizard; the label was
    # reworded ("DocGen Wizard" -> "Rebuild from sources") because the old text
    # gave no clue what DocGen is for.
    assert "/docgen/new" in tw
    assert "Rebuild from sources (DocGen) →" in tw
    assert "Standards Catalog →" in tw
    assert "'%%.0f'|format" not in tw and "'%%.0f'|format" not in idx
