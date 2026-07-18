# CUI // SP-CTI
"""Tests for the ACE confidence-gate HITL resolve surface (hcx-ace-09).

Covers:
  * _pending_confidence_hitl() surfaces an unresolved hitl_pending audit row and
    stops surfacing it once a matching hitl_resolved row exists (the data behind
    the coworker/instance.html approval banner).
  * POST /api/ace/<id>/hitl with approved=true resolves the confidence gate
    (inserts hitl_resolved via HITLGate.resolve) so the coworker thread resumes.
  * approved=false inserts hitl_rejected and also clears the pending banner.

Uses the real storage layer via ICDEV_ACE_DB_URL (not a raw sqlite3 stub) so the
%s placeholders HITLGate.resolve emits are translated to ? for SQLite.

Run: pytest tests/test_ace_hitl_resolve_endpoint.py -v
"""
from __future__ import annotations

import importlib

import pytest
from flask import Flask


@pytest.fixture()
def ace_env(tmp_path, monkeypatch):
    """Point ICDEV_ACE_DB_URL at a fresh temp SQLite DB with ACE tables."""
    db_path = tmp_path / "ace_hitl_endpoint.db"
    monkeypatch.setenv("ICDEV_ACE_DB_URL", str(db_path))
    from icdev.tools.ace.db.init_db import init as init_ace_db

    init_ace_db()
    return db_path


def _conn():
    from icdev.tools.db.storage import get_canvas_connection

    return get_canvas_connection("ICDEV_ACE_DB_URL")


def _seed_pending(instance_id: str, coworker_id: str, detail: str) -> None:
    """Insert an instance + coworker + one unresolved hitl_pending audit row."""
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO ace_instances (id, name, role_id, state, trust_tier) "
            "VALUES (?, ?, 'ai_developer', 'active', 'yellow')",
            (instance_id, instance_id),
        )
        conn.execute(
            "INSERT INTO ace_coworkers (id, instance_id, role_id, display_name, state, trust_tier) "
            "VALUES (?, ?, 'ai_developer', 'AI Developer', 'hitl_pending', 'yellow')",
            (coworker_id, instance_id),
        )
        conn.execute(
            "INSERT INTO ace_audit_log (instance_id, coworker_id, action, detail, actor, created_at) "
            "VALUES (?, ?, 'hitl_pending', ?, 'coworker_thread', '2026-07-17T00:00:00')",
            (instance_id, coworker_id, detail),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def client(ace_env):
    """Minimal Flask app mounting only the ACE blueprints (no dashboard auth).

    Mirrors tests/test_ace_nova_state_endpoint.py: a bare Flask app avoids the
    dashboard's API-key before_request hook, and uses the REAL storage layer via
    ICDEV_ACE_DB_URL so HITLGate.resolve's %s placeholders are translated to ?
    for SQLite.
    """
    _bp_mod = importlib.import_module("icdev.tools.ace.blueprint")
    _bp_mod._state["db_ready"] = True  # tables already created by ace_env

    app = Flask(__name__)
    app.config["TESTING"] = True

    from icdev.tools.ace.blueprint import ace_api_bp, ace_bp

    app.register_blueprint(ace_bp)  # record_once auto-mounts ace_api_bp
    if "ace_api" not in app.blueprints:
        app.register_blueprint(ace_api_bp)

    with app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Banner data source
# ---------------------------------------------------------------------------


def test_pending_confidence_hitl_surfaces_then_clears(ace_env):
    """The banner query returns the pending row, then nothing once resolved."""
    from icdev.tools.ace.blueprint import _pending_confidence_hitl
    from icdev.tools.ace.coworker_thread import HITLGate

    instance_id, coworker_id = "ace-bnr", "cw-bnr"
    detail = "low_confidence: trust_score=0.50 (supervised band, threshold=0.6)"
    _seed_pending(instance_id, coworker_id, detail)

    conn = _conn()
    try:
        pending = _pending_confidence_hitl(conn, instance_id)
    finally:
        conn.close()
    assert len(pending) == 1
    assert pending[0]["coworker_id"] == coworker_id
    assert "low_confidence" in pending[0]["detail"]

    # Resolve, then the banner query must return nothing.
    HITLGate.resolve(coworker_id, detail, instance_id)
    conn = _conn()
    try:
        assert _pending_confidence_hitl(conn, instance_id) == []
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Resolve endpoint
# ---------------------------------------------------------------------------


def test_hitl_resolve_endpoint_approves_confidence_gate(client):
    """POST /api/ace/<id>/hitl approved=true clears the pending confidence gate."""
    from icdev.tools.ace.blueprint import _pending_confidence_hitl
    from icdev.tools.ace.coworker_thread import HITLGate

    instance_id, coworker_id = "ace-ep-ok", "cw-ep-ok"
    detail = "low_confidence: trust_score=0.50 (supervised band, threshold=0.6)"
    _seed_pending(instance_id, coworker_id, detail)

    assert HITLGate.get_pending(coworker_id), "precondition: gate is pending"

    resp = client.post(
        f"/api/ace/{instance_id}/hitl",
        json={"coworker_id": coworker_id, "detail": detail, "approved": True},
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["resolved"] is True and data["approved"] is True

    # The gate is now cleared for both the thread poller and the banner.
    assert HITLGate.get_pending(coworker_id) == []
    conn = _conn()
    try:
        assert _pending_confidence_hitl(conn, instance_id) == []
    finally:
        conn.close()


def test_hitl_resolve_endpoint_rejects_confidence_gate(client):
    """approved=false inserts hitl_rejected and also clears the pending banner."""
    from icdev.tools.ace.blueprint import _pending_confidence_hitl

    instance_id, coworker_id = "ace-ep-no", "cw-ep-no"
    detail = "low_confidence: trust_score=0.50 (supervised band, threshold=0.6)"
    _seed_pending(instance_id, coworker_id, detail)

    resp = client.post(
        f"/api/ace/{instance_id}/hitl",
        json={"coworker_id": coworker_id, "detail": detail, "approved": False},
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["approved"] is False

    conn = _conn()
    try:
        assert _pending_confidence_hitl(conn, instance_id) == []
    finally:
        conn.close()


def test_hitl_resolve_endpoint_requires_fields(client):
    """Missing coworker_id/detail returns 400."""
    resp = client.post(
        "/api/ace/whatever/hitl", json={"approved": True}, content_type="application/json"
    )
    assert resp.status_code == 400
