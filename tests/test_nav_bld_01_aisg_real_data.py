# CUI // SP-CTI
"""nav-bld-01: AISG ROI + Executive views must render real data, never canned
demo numbers.

Guards against the fabricated-metrics regression where empty tables rendered a
hardcoded 342.5 hrs / $51k "demo fallback" as if real, and monthly_trend /
cost_breakdown / agent_activity / maturity were always canned.

Shared conftest, SQLite backend. Each test points ICDEV_DB_PATH at a tmp DB and
creates an empty aisg_roi_events table so the modules exercise the real
read/aggregate path.
"""
from __future__ import annotations

import importlib
import json

import pytest

# The exact canned constants that must NEVER appear in a real/empty response.
_FORBIDDEN = ("342.5", "51375", "51,375", "51000", "51_375")

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS aisg_roi_events (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    action_type          TEXT    NOT NULL,
    time_saved_minutes   NUMERIC NOT NULL,
    description          TEXT,
    triggered_by         TEXT,
    occurred_at          TEXT    NOT NULL
)
"""


def _roi_module():
    return importlib.import_module("tools.aisg.roi_tracker")


def _exec_module():
    return importlib.import_module("tools.aisg.executive_view")


@pytest.fixture
def aisg_db(tmp_path, monkeypatch):
    """Empty aisg_roi_events table in an isolated SQLite DB."""
    db_path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        conn.execute(_CREATE_SQL)
        conn.commit()
    finally:
        conn.close()
    return db_path


def _seed(rows):
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        for r in rows:
            conn.execute(
                "INSERT INTO aisg_roi_events "
                "(action_type, time_saved_minutes, description, triggered_by, occurred_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                r,
            )
        conn.commit()
    finally:
        conn.close()


def _assert_no_canned(payload: dict):
    blob = json.dumps(payload, default=str)
    for token in _FORBIDDEN:
        assert token not in blob, f"canned demo value {token!r} leaked into payload"


# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------

def test_roi_summary_empty_state(aisg_db):
    summary = _roi_module().get_roi_summary()
    assert summary["state"] == "empty"
    assert summary["empty"] is True
    assert summary["error"] is False
    assert summary["hours_saved"] == 0.0
    assert summary["cost_avoided"] == 0.0
    assert summary["total_events"] == 0
    assert summary["recent_events"] == []
    assert summary["monthly_trend"] == []
    assert summary["breakdown"] == []
    assert "No ROI events" in summary["message"]
    _assert_no_canned(summary)


def test_executive_data_empty_state(aisg_db):
    data = _exec_module().get_executive_data()
    assert data["state"] == "empty"
    assert data["empty"] is True
    assert data["hours_saved"] == 0.0
    assert data["cost_avoided"] == 0.0
    assert data["total_tasks_automated"] == 0
    assert data["agents_active"] == 0
    assert data["cost_breakdown"] == []
    assert data["agent_activity"] == []
    assert data["maturity_trend"] == []
    assert data["maturity_available"] is False
    assert data["maturity_score"] is None
    _assert_no_canned(data)


# ---------------------------------------------------------------------------
# Seeded / computed aggregates
# ---------------------------------------------------------------------------

def test_roi_summary_computed_aggregates(aisg_db):
    # 30 + 60 + 90 min in Apr; 45 + 15 min in May 2026.
    _seed([
        ("self_heal", 30, "a", "coherence_checker", "2026-04-10T09:00:00"),
        ("security_scan", 60, "b", "icdev-secure", "2026-04-20T09:00:00"),
        ("evidence_collect", 90, "c", "icdev-comply", "2026-04-25T09:00:00"),
        ("compliance_check", 45, "d", "icdev-comply", "2026-05-05T09:00:00"),
        ("test_run", 15, "e", "test_orchestrator", "2026-05-06T09:00:00"),
    ])
    summary = _roi_module().get_roi_summary()

    assert summary["state"] == "ok"
    # Total minutes = 240 -> 4.0 hours.
    assert summary["hours_saved"] == 4.0
    assert summary["cost_avoided"] == 600.0  # 4.0 * 150
    assert summary["total_events"] == 5

    # monthly_trend derived from data: Apr = 180min=3.0h, May = 60min=1.0h.
    trend = {m["key"]: m["hours"] for m in summary["monthly_trend"]}
    assert trend == {"2026-04": 3.0, "2026-05": 1.0}
    # chronological order
    assert [m["key"] for m in summary["monthly_trend"]] == ["2026-04", "2026-05"]

    # breakdown by action_type, largest first (evidence_collect 1.5h).
    top = summary["breakdown"][0]
    assert top["action_type"] == "evidence_collect"
    assert top["hours"] == 1.5

    _assert_no_canned(summary)


def test_executive_data_computed_aggregates(aisg_db):
    _seed([
        ("self_heal", 30, "a", "coherence_checker", "2026-04-10T09:00:00"),
        ("security_scan", 60, "b", "icdev-secure", "2026-04-20T09:00:00"),
        ("security_scan", 60, "c", "icdev-secure", "2026-04-21T09:00:00"),
    ])
    data = _exec_module().get_executive_data()

    assert data["state"] == "ok"
    # 150 min -> 2.5 h
    assert data["hours_saved"] == 2.5
    assert data["cost_avoided"] == 375.0
    assert data["total_tasks_automated"] == 3

    # agent_activity from triggered_by: icdev-secure has 2 tasks (top).
    top_agent = data["agent_activity"][0]
    assert top_agent["agent"] == "icdev-secure"
    assert top_agent["tasks_completed"] == 2

    # cost_breakdown: security_scan = 120min=2.0h is the largest category.
    top_cat = data["cost_breakdown"][0]
    assert top_cat["category"] == "Security Scans"
    assert top_cat["hours"] == 2.0

    # maturity remains explicitly unavailable — never fabricated.
    assert data["maturity_available"] is False
    assert data["maturity_score"] is None

    _assert_no_canned(data)


# ---------------------------------------------------------------------------
# DB failure -> degraded error payload (no canned numbers)
# ---------------------------------------------------------------------------

def test_roi_summary_db_failure(monkeypatch):
    mod = _roi_module()

    def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(mod, "get_connection", _boom)
    summary = mod.get_roi_summary()

    assert summary["state"] == "error"
    assert summary["error"] is True
    assert summary["empty"] is False
    assert summary["hours_saved"] == 0.0
    assert summary["cost_avoided"] == 0.0
    assert summary["total_events"] == 0
    _assert_no_canned(summary)


def test_executive_data_db_failure(monkeypatch):
    mod = _exec_module()

    def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(mod, "get_connection", _boom)
    data = mod.get_executive_data()

    assert data["state"] == "error"
    assert data["error"] is True
    assert data["hours_saved"] == 0.0
    assert data["cost_avoided"] == 0.0
    assert data["total_tasks_automated"] == 0
    assert data["maturity_score"] is None
    _assert_no_canned(data)
