# CUI // SP-CTI
"""Functional route / engine / security / reflex tests for the DSOC canvas.

Covers cnr-dsoc-01..05:
  - security: fail-closed auth (401 on mutating/API, 302 on page GET)
  - routes:   list APIs graceful, threats/mitigations round-trip, LEFT JOIN
  - engine:   trigger_rtbh + auto_expire_rtbh
  - iqe:      endpoint reads 'question' (and legacy 'q')
  - mcp:      dsoc_rtbh_trigger denied without a role (fail-closed)
  - reflex:   bgp_hijack_monitor.run() dry-run + real RTBH expiry
"""
import importlib
import os
from datetime import datetime, timedelta, timezone

import pytest

os.environ["ICDEV_STORAGE_BACKEND"] = "sqlite"
os.environ["DSOC_STORAGE_BACKEND"] = "sqlite"


@pytest.fixture(scope="module")
def dsoc_init(tmp_path_factory):
    """Configure a temp SQLite DSOC DB and (re)load init_db so its module-level
    backend/path constants pick up the env, then create the schema."""
    db_path = tmp_path_factory.mktemp("dsoc") / "dsoc_canvas.db"
    os.environ["DSOC_STORAGE_BACKEND"] = "sqlite"
    os.environ["DSOC_DB_PATH"] = str(db_path)
    import tools.dsoc_canvas.db.init_db as init_mod
    importlib.reload(init_mod)
    init_mod.init_db()
    return init_mod


@pytest.fixture
def app(dsoc_init):
    from flask import Flask
    from tools.dsoc_canvas.blueprint import create_dsoc_blueprint
    application = Flask(__name__)
    application.secret_key = "test-secret"
    application.register_blueprint(create_dsoc_blueprint())
    return application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth_client(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = "tester"
    return c


# ── Security: fail-closed auth (cnr-dsoc-01) ────────────────────────────────

def test_unauth_post_flowspec_401(client):
    r = client.post("/api/dsoc/flowspec", json={"rule_name": "r", "action": "drop"})
    assert r.status_code == 401


def test_unauth_post_rtbh_401(client):
    r = client.post("/api/dsoc/rtbh", json={"prefix": "1.2.3.0/24", "trigger_reason": "manual"})
    assert r.status_code == 401


def test_unauth_get_api_401(client):
    assert client.get("/api/dsoc/threats").status_code == 401


def test_unauth_page_redirects(client):
    r = client.get("/dsoc")
    assert r.status_code in (301, 302)


def test_auth_bypass_env(monkeypatch, app):
    monkeypatch.setenv("ICDEV_AUTH_BYPASS", "1")
    c = app.test_client()
    assert c.get("/api/dsoc/overview").status_code == 200


# ── Routes: list APIs + round-trip (cnr-dsoc-03/04) ─────────────────────────

def test_list_apis_return_json_arrays(auth_client):
    for path in ("/api/dsoc/flowspec", "/api/dsoc/rtbh", "/api/dsoc/scrubbing",
                 "/api/dsoc/threats", "/api/dsoc/mitigations"):
        r = auth_client.get(path)
        assert r.status_code == 200, path
        assert isinstance(r.get_json(), list), path


def test_threat_create_and_list(auth_client):
    r = auth_client.post("/api/dsoc/threats", json={
        "source_prefix": "203.0.113.0/24", "threat_type": "scanner", "confidence_pct": 88,
    })
    assert r.status_code == 201
    rows = auth_client.get("/api/dsoc/threats").get_json()
    assert any(t["source_prefix"] == "203.0.113.0/24" for t in rows)


def test_mitigation_left_join_columns(auth_client):
    r = auth_client.post("/api/dsoc/mitigations", json={
        "target_prefix": "198.51.100.0/24", "mitigation_type": "scrubbing",
        "peak_traffic_gbps": 42.0,
    })
    assert r.status_code == 201
    rows = auth_client.get("/api/dsoc/mitigations").get_json()
    assert rows
    # LEFT JOIN aliases present even when FK is NULL
    assert "scrubbing_center_name" in rows[0]
    assert "flowspec_rule_name" in rows[0]
    assert any(m["peak_traffic_gbps"] == 42.0 for m in rows)


# ── IQE endpoint contract (cnr-dsoc-03) ─────────────────────────────────────

def test_iqe_reads_question(auth_client):
    r = auth_client.post("/api/dsoc/iqe-query", json={"question": "show active rtbh"})
    assert r.status_code == 200
    assert r.get_json()["collection"] == "dsoc.rtbh_entries"


def test_iqe_legacy_q_alias(auth_client):
    r = auth_client.post("/api/dsoc/iqe-query", json={"q": "show high confidence threats"})
    assert r.status_code == 200
    assert r.get_json()["collection"] == "dsoc.threats"


def test_iqe_missing_question_400(auth_client):
    r = auth_client.post("/api/dsoc/iqe-query", json={})
    assert r.status_code == 400


# ── Engine: RTBH trigger + auto-expiry (cnr-dsoc-05) ────────────────────────

def test_trigger_and_auto_expire_rtbh(dsoc_init):
    from tools.dsoc_canvas.rtbh_manager import (
        auto_expire_rtbh, get_active_blackholes, trigger_rtbh,
    )
    conn = dsoc_init.get_connection()
    try:
        # Fresh entry with a 60-min window should NOT expire.
        trigger_rtbh(conn, prefix="192.0.2.0/24", reason="manual", auto_withdraw_minutes=60)
        # Backdated entry with a 1-min window SHOULD expire.
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        conn.execute(
            "INSERT INTO dsoc_rtbh_entries (prefix, trigger_reason, status, "
            "auto_withdraw_minutes, created_at) VALUES (%s,%s,'active',%s,%s)",
            ("192.0.2.128/25", "manual", 1, old),
        )
        conn.commit()
        before = len(get_active_blackholes(conn))
        expired = auto_expire_rtbh(conn)
        after = len(get_active_blackholes(conn))
        assert expired >= 1
        assert after == before - expired
    finally:
        conn.close()


# ── MCP tool authorization gate (cnr-dsoc-01) ───────────────────────────────

def test_mcp_rtbh_denied_without_role(monkeypatch, dsoc_init):
    monkeypatch.delenv("ICDEV_MCP_AUTHZ_BYPASS", raising=False)
    from tools.mcp import gap_handlers
    out = gap_handlers.dsoc_rtbh_trigger({"prefix": "1.2.3.0/24", "trigger_reason": "manual"})
    assert out.get("authorized") is False
    assert "authorization" in out.get("error", "").lower()


def test_mcp_flowspec_denied_without_role(monkeypatch, dsoc_init):
    monkeypatch.delenv("ICDEV_MCP_AUTHZ_BYPASS", raising=False)
    from tools.mcp import gap_handlers
    out = gap_handlers.dsoc_flowspec_activate({"rule_id": 1})
    assert out.get("authorized") is False


def test_mcp_bypass_allows(monkeypatch, dsoc_init):
    monkeypatch.setenv("ICDEV_MCP_AUTHZ_BYPASS", "1")
    from tools.mcp import gap_handlers
    out = gap_handlers.dsoc_rtbh_trigger({"prefix": "1.2.3.0/24", "trigger_reason": "manual"})
    # gate passes → real handler runs → record created (mode label set)
    assert out.get("authorized") is not False
    assert out.get("mode") == "simulation-record-only"


# ── Reflex: bgp_hijack_monitor (cnr-dsoc-05) ────────────────────────────────

def test_reflex_dry_run(dsoc_init):
    from tools.genesis.reflexes import bgp_hijack_monitor
    out = bgp_hijack_monitor.run({"dry_run": True})
    assert out["success"] is True
    assert out["details"]["rtbh_expired"] == 0  # dry-run performs no writes


def test_reflex_expires_backdated_rtbh(dsoc_init):
    from tools.genesis.reflexes import bgp_hijack_monitor
    conn = dsoc_init.get_connection()
    try:
        old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        conn.execute(
            "INSERT INTO dsoc_rtbh_entries (prefix, trigger_reason, status, "
            "auto_withdraw_minutes, created_at) VALUES (%s,%s,'active',%s,%s)",
            ("192.0.2.64/26", "manual", 1, old),
        )
        conn.commit()
    finally:
        conn.close()
    out = bgp_hijack_monitor.run({})
    assert out["success"] is True
    assert out["details"]["rtbh_expired"] >= 1
