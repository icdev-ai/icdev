# CUI // SP-CTI
"""Tests for the Insider-Risk UBA (lite) engine — card crx-sec-01.

Synthesizes telemetry rows (PUBLIC repo — no real user data) into a temp DB and
asserts the deterministic rules fire with a score. conftest MINIMAL_ICDEV_SCHEMA
is not applied to a bare get_connection(db_path=...), so the fixture self-creates
the source + derived tables.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest

from tools.db.storage import get_connection
from tools.security import insider_risk


def _iso(dt: datetime) -> str:
    return dt.isoformat()


@pytest.fixture()
def conn(tmp_path):
    c = get_connection(db_path=str(tmp_path / "uba.db"))
    # Source telemetry tables (read-only inputs).
    c.execute(
        "CREATE TABLE IF NOT EXISTS audit_trail ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, event_type TEXT, "
        "actor TEXT, action TEXT, details TEXT, classification TEXT, created_at TEXT)"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS usage_events ("
        "id TEXT, route TEXT, method TEXT, status_code INTEGER, duration_ms INTEGER, "
        "skill_invoked TEXT, user_session TEXT, ip_hash TEXT, feature_tag TEXT, "
        "occurred_at TEXT, classification TEXT)"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS hook_events ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, hook_type TEXT, "
        "tool_name TEXT, project_id TEXT, payload TEXT, classification TEXT, created_at TEXT)"
    )
    c.commit()
    return c


def _cfg():
    return copy.deepcopy(insider_risk._DEFAULT_CONFIG)


def test_off_hours_bulk_export_fires(conn):
    """A user doing many exports at 02:00 trips the off-hours bulk-export rule."""
    base = datetime.now(timezone.utc).replace(hour=2, minute=0, second=0, microsecond=0) - timedelta(days=1)
    for i in range(6):  # threshold is 5
        conn.execute(
            "INSERT INTO usage_events (id, route, method, user_session, feature_tag, occurred_at, classification) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (f"u{i}", "/api/export/report", "GET", "mallory", "export",
             _iso(base + timedelta(minutes=i)), "CUI"),
        )
    conn.commit()

    result = insider_risk.run_scan(_cfg(), conn=conn)
    findings = {f["account_id"]: f for f in result["findings"]}
    assert "mallory" in findings, result
    f = findings["mallory"]
    assert "off_hours_bulk_export" in f["rules_fired"]
    assert f["risk_score"] > 0
    assert f["risk_band"] in ("elevated", "high")

    # Persisted as derived score with RLS columns present.
    row = conn.execute(
        "SELECT account_id, risk_score, tenant_id, classification FROM insider_risk_scores "
        "WHERE account_id=%s", ("mallory",)
    ).fetchone()
    assert row is not None
    assert row["classification"] == "CUI"


def test_dormant_account_activity_fires(conn):
    """An account silent for >30d that reactivates trips the dormancy rule."""
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=90)      # long-ago activity ...
    recent = now - timedelta(hours=2)   # ... then a sudden reactivation
    for etype, ts in (("code_generated", old), ("code_generated", recent)):
        conn.execute(
            "INSERT INTO audit_trail (event_type, actor, action, created_at, classification) "
            "VALUES (%s,%s,%s,%s,%s)",
            (etype, "sleeper", "x", _iso(ts), "CUI"),
        )
    conn.commit()

    result = insider_risk.run_scan(_cfg(), conn=conn, persist=False)
    findings = {f["account_id"]: f for f in result["findings"]}
    assert "sleeper" in findings
    assert "dormant_account_activity" in findings["sleeper"]["rules_fired"]


def test_privilege_change_burst_fires(conn):
    """Several privilege/config changes within an hour trip the burst rule."""
    base = datetime.now(timezone.utc) - timedelta(hours=3)
    for i, et in enumerate(
        ["config_changed", "secret_rotated", "classification_changed", "approval_granted"]
    ):
        conn.execute(
            "INSERT INTO audit_trail (event_type, actor, action, created_at, classification) "
            "VALUES (%s,%s,%s,%s,%s)",
            (et, "admin_x", "y", _iso(base + timedelta(minutes=i * 5)), "CUI"),
        )
    conn.commit()

    result = insider_risk.run_scan(_cfg(), conn=conn, persist=False)
    findings = {f["account_id"]: f for f in result["findings"]}
    assert "admin_x" in findings
    assert "privilege_change_burst" in findings["admin_x"]["rules_fired"]


def test_quiet_user_no_findings(conn):
    """Normal daytime activity produces no anomaly."""
    base = datetime.now(timezone.utc).replace(hour=14) - timedelta(days=1)
    conn.execute(
        "INSERT INTO audit_trail (event_type, actor, action, created_at, classification) "
        "VALUES (%s,%s,%s,%s,%s)",
        ("code_generated", "alice", "z", _iso(base), "CUI"),
    )
    conn.commit()
    result = insider_risk.run_scan(_cfg(), conn=conn, persist=False)
    assert all(f["account_id"] != "alice" for f in result["findings"])


def test_default_disabled():
    """Feature ships default OFF."""
    assert insider_risk._DEFAULT_CONFIG["enabled"] is False
    assert insider_risk.is_enabled({"enabled": False}) is False


def test_off_hours_wraps_midnight():
    dt2am = datetime(2026, 1, 1, 2, tzinfo=timezone.utc)
    dt2pm = datetime(2026, 1, 1, 14, tzinfo=timezone.utc)
    assert insider_risk._is_off_hours(dt2am, 22, 6) is True
    assert insider_risk._is_off_hours(dt2pm, 22, 6) is False
