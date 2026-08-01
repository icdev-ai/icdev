# CUI // SP-CTI
"""Unit tests for the Workflow Forms Canvas (WFC) production-readiness pass.

Covers the four cnr-wfc hardening tasks:

  * cnr-wfc-01 — api_regen_download path-traversal containment (legit download
    works; backslash/absolute traversal returns 404 and never serves a file
    outside the artifact dir).
  * cnr-wfc-02 — api_processify_workflow routes the LLM call through the
    governed Cortex facade with untrusted content, and rejects oversize uploads.
  * cnr-wfc-03 — submit_form validates submissions against the stored form
    schema (required + per-type) and the routes return 400; the dead
    form_node.py module has been removed.
  * cnr-wfc-04 — export_engine._get_branding reads branding from the WFC canvas
    connection (unified with the writer), and the today-count range query is
    sargable.

Run: pytest tests/test_wfc_canvas_hardening.py -v
"""
from __future__ import annotations

import importlib
import io
import sqlite3
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask

_DDL = """
CREATE TABLE IF NOT EXISTS studio_forms (
    form_id     TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    schema_json TEXT NOT NULL,
    created_by  TEXT,
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now')),
    version     INTEGER DEFAULT 1,
    status      TEXT DEFAULT 'draft'
);
CREATE TABLE IF NOT EXISTS studio_form_submissions (
    submission_id TEXT PRIMARY KEY,
    form_id       TEXT NOT NULL,
    data_json     TEXT NOT NULL,
    submitted_by  TEXT,
    submitted_at  TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS wfc_branding (
    id               TEXT PRIMARY KEY,
    entity_type      TEXT NOT NULL,
    entity_id        TEXT NOT NULL,
    org_name         TEXT,
    logo_data        TEXT,
    primary_color    TEXT DEFAULT '#1a365d',
    secondary_color  TEXT DEFAULT '#c8a951',
    header_html      TEXT,
    footer_html      TEXT,
    show_classification INTEGER DEFAULT 1,
    created_at       TEXT DEFAULT (datetime('now')),
    updated_at       TEXT DEFAULT (datetime('now')),
    UNIQUE(entity_type, entity_id)
);
"""


@pytest.fixture
def wfc_env(tmp_path, monkeypatch):
    """Point the main + canvas storage at a fresh temp SQLite db with WFC tables."""
    db = tmp_path / "wfc.db"
    monkeypatch.setenv("ICDEV_DB_PATH", str(db))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    # ICDEV_WFC_ENABLED must NOT look like a .db path in sqlite-pinned canvas mode.
    monkeypatch.delenv("ICDEV_WFC_ENABLED", raising=False)
    conn = sqlite3.connect(str(db))
    conn.executescript(_DDL)
    conn.commit()
    conn.close()
    return db


@pytest.fixture
def wfc_app(wfc_env, monkeypatch):
    from tools.workflow_canvas import blueprint as wfc_bp
    # Skip the before_request migration bootstrap — schema is already provisioned.
    monkeypatch.setattr(wfc_bp, "_INIT_DONE", True)
    app = Flask(__name__)
    app.config.update(TESTING=True)
    app.register_blueprint(wfc_bp.create_wfc_blueprint())
    return app, wfc_bp


@pytest.fixture
def client(wfc_app):
    app, _ = wfc_app
    return app.test_client()


# ── cnr-wfc-01: path traversal ────────────────────────────────────────────

def test_regen_download_legit_and_traversal(client, tmp_path, monkeypatch):
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    (artifact_dir / "report.pdf").write_bytes(b"REPORT-BYTES")
    # Secret file OUTSIDE the artifact dir that traversal would try to reach.
    (tmp_path / "secret.txt").write_bytes(b"TOP-SECRET")

    fake_dr = types.ModuleType("tools.workflow_canvas.doc_regenerator")
    fake_dr.get_job_status = lambda job_id: {"status": "done", "artifact_dir": str(artifact_dir)}
    monkeypatch.setitem(sys.modules, "tools.workflow_canvas.doc_regenerator", fake_dr)

    # Legit download works.
    ok = client.get("/workflow-canvas/api/regen-jobs/j1/download/report.pdf")
    assert ok.status_code == 200
    assert ok.data == b"REPORT-BYTES"

    # Windows-style backslash traversal must be rejected and never leak the secret.
    bad = client.get("/workflow-canvas/api/regen-jobs/j1/download/..%5C..%5Csecret.txt")
    assert bad.status_code == 404
    assert b"TOP-SECRET" not in bad.data

    # A name that resolves to the parent dir itself is not a file → 404.
    bad2 = client.get("/workflow-canvas/api/regen-jobs/j1/download/..%5Csecret.txt")
    assert bad2.status_code == 404
    assert b"TOP-SECRET" not in bad2.data


# ── cnr-wfc-02: governed Cortex + extraction cap ──────────────────────────

def test_processify_routes_through_cortex(client, monkeypatch):
    captured = {}

    def _fake_complete(prompt, function=None, ctx=None, **kwargs):
        captured["function"] = function
        captured["ctx"] = ctx
        return types.SimpleNamespace(
            text='{"workflow_name":"Test WF","workflow_description":"d",'
                 '"industry":"General","steps":[{"id":"step-1","title":"A","form_fields":[]}]}'
        )

    import tools.cortex.api as capi
    monkeypatch.setattr(capi, "complete", _fake_complete)

    resp = client.post(
        "/workflow-canvas/api/workflows/processify",
        data={"text": "Step 1: do a thing. Step 2: review it.", "industry": "General"},
    )
    assert resp.status_code == 200, resp.data
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["workflow"]["workflow_name"] == "Test WF"
    # The governed facade was used, with untrusted content (trusted_content False).
    assert captured["function"] == "process_digitization"
    assert getattr(captured["ctx"], "trusted_content", None) is False


def test_processify_rejects_oversize_upload(client, wfc_app, monkeypatch):
    _, wfc_bp = wfc_app
    monkeypatch.setattr(wfc_bp, "MAX_UPLOAD_BYTES", 5)
    resp = client.post(
        "/workflow-canvas/api/workflows/processify",
        data={"file": (io.BytesIO(b"way more than five bytes"), "proc.txt")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 413
    assert "too large" in resp.get_json()["error"].lower()


# ── cnr-wfc-03: submission validation + dead module removed ────────────────

def _make_form():
    from tools.studio.form_builder import create_form
    return create_form(
        name="Intake",
        fields=[
            {"id": "f1", "type": "text", "label": "Name", "required": True},
            {"id": "f2", "type": "email", "label": "Email"},
            {"id": "f3", "type": "select", "label": "Level", "options": ["A", "B"]},
            {"id": "f4", "type": "number", "label": "Count"},
        ],
        created_by="test",
        status="published",
    )["form_id"]


def test_submit_form_validation(wfc_env):
    from tools.studio.form_builder import submit_form, list_submissions
    fid = _make_form()

    # Missing required field → rejected, nothing stored.
    r = submit_form(fid, {"f2": "a@b.com"})
    assert r["status"] == "error"
    assert r["validation_errors"]
    assert any("required" in e.lower() for e in r["validation_errors"])
    assert list_submissions(fid) == []

    # Bad email / bad select option / non-numeric number → rejected.
    assert submit_form(fid, {"f1": "x", "f2": "not-an-email"})["status"] == "error"
    assert submit_form(fid, {"f1": "x", "f3": "Z"})["status"] == "error"
    assert submit_form(fid, {"f1": "x", "f4": "abc"})["status"] == "error"

    # Fully valid → stored.
    ok = submit_form(fid, {"f1": "Jane", "f2": "jane@example.com", "f3": "A", "f4": 7})
    assert ok["status"] == "ok"
    assert len(list_submissions(fid)) == 1


def test_submit_route_returns_400_on_invalid(client, wfc_env):
    fid = _make_form()
    resp = client.post(
        f"/workflow-canvas/api/forms/{fid}/submit",
        json={"data": {"f2": "a@b.com"}},  # missing required f1
    )
    assert resp.status_code == 400
    assert resp.get_json()["validation_errors"]


def test_submit_route_missing_form_returns_404(client, wfc_env):
    resp = client.post(
        "/workflow-canvas/api/forms/frm-does-not-exist/submit",
        json={"data": {"f1": "x"}},
    )
    assert resp.status_code == 404


def test_form_node_module_removed():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("tools.workflow_canvas.form_node")


# ── cnr-wfc-04: branding unify + count range query ────────────────────────

def test_export_branding_reads_from_canvas_connection(wfc_env):
    from tools.db.storage import get_canvas_connection, sql_placeholder
    from tools.workflow_canvas.export_engine import _get_branding

    conn = get_canvas_connection("ICDEV_WFC_ENABLED")
    try:
        ph = sql_placeholder(conn)
        conn.execute(
            f"INSERT INTO wfc_branding (id, entity_type, entity_id, org_name) "
            f"VALUES ({ph},{ph},{ph},{ph})",
            ("brd-1", "form", "frm-x", "Acme Corp"),
        )
        conn.commit()
    finally:
        conn.close()

    # _get_branding must open its own canvas connection (ignoring any passed
    # conn) and return the branding — previously it read via a raw %s query on
    # the main conn, which silently returned {} on SQLite.
    branding = _get_branding("form", "frm-x")
    assert branding.get("org_name") == "Acme Corp"


def test_submissions_today_range_query(wfc_env):
    from tools.db.storage import get_connection, sql_placeholder

    now = datetime.now(timezone.utc)
    today_iso = now.strftime("%Y-%m-%dT10:00:00")
    yday_iso = (now - timedelta(days=1)).strftime("%Y-%m-%dT10:00:00")

    conn = get_connection()
    try:
        ph = sql_placeholder(conn)
        for i, ts in enumerate([today_iso, today_iso, yday_iso]):
            conn.execute(
                f"INSERT INTO studio_form_submissions "
                f"(submission_id, form_id, data_json, submitted_by, submitted_at) "
                f"VALUES ({ph},{ph},{ph},{ph},{ph})",
                (f"sub-{i}", "frm-x", "{}", "t", ts),
            )
        conn.commit()
        start = now.strftime("%Y-%m-%dT00:00:00")
        end = (now + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")
        row = conn.execute(
            f"SELECT COUNT(*) FROM studio_form_submissions "
            f"WHERE submitted_at >= {ph} AND submitted_at < {ph}",
            (start, end),
        ).fetchone()
        assert row[0] == 2  # two today, one yesterday excluded
    finally:
        conn.close()
