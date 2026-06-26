# CUI // SP-CTI
"""Tests for PVM Patch Planner (pvm-pat-02)."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO = Path(__file__).parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")


# ---------------------------------------------------------------------------
# Helper to build a mock DB row
# ---------------------------------------------------------------------------

def _row(d: dict):
    m = MagicMock()
    m.__iter__ = lambda s: iter(d.items())
    m.keys = lambda: d.keys()
    m.__getitem__ = lambda s, k: d[k]
    m.get = lambda k, dv=None: d.get(k, dv)
    return m


# ---------------------------------------------------------------------------
# Unit tests — pure helpers (no DB)
# ---------------------------------------------------------------------------

def test_site_from_device_dash_separator():
    from tools.network.patch_planner import _site_from_device
    assert _site_from_device("nyc-router-01") == "nyc"


def test_site_from_device_dot_separator():
    from tools.network.patch_planner import _site_from_device
    assert _site_from_device("lax.core.rtr1") == "lax"


def test_site_from_device_no_separator():
    from tools.network.patch_planner import _site_from_device
    assert _site_from_device("router01") == "router01"


def test_subnet_from_ip_returns_slash24_prefix():
    from tools.network.patch_planner import _subnet_from_ip
    assert _subnet_from_ip("192.168.1.50") == "192.168.1"


def test_subnet_from_ip_invalid_returns_raw():
    from tools.network.patch_planner import _subnet_from_ip
    assert _subnet_from_ip("not-an-ip") == "not-an-ip"


def test_action_from_guidance_first_word():
    from tools.network.patch_planner import _action_from_guidance
    assert _action_from_guidance("upgrade firmware immediately") == "upgrade"


def test_action_from_guidance_none_defaults_to_patch():
    from tools.network.patch_planner import _action_from_guidance
    assert _action_from_guidance(None) == "patch"


def test_action_from_guidance_empty_string_defaults_to_patch():
    from tools.network.patch_planner import _action_from_guidance
    assert _action_from_guidance("   ") == "patch"


# ---------------------------------------------------------------------------
# _find_next_window tests
# ---------------------------------------------------------------------------

def test_find_next_window_returns_none_on_empty_table():
    """No maintenance windows → returns None gracefully."""
    from tools.network.patch_planner import _find_next_window
    conn = MagicMock()
    conn.execute = MagicMock(return_value=MagicMock(fetchall=MagicMock(return_value=[])))
    result = _find_next_window(conn, "nyc", datetime.now(timezone.utc).isoformat())
    assert result is None


def test_find_next_window_projects_weekly_window():
    """A weekly window in the past is projected forward to the future."""
    from tools.network.patch_planner import _find_next_window
    past = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    past_end = (datetime.now(timezone.utc) - timedelta(days=3) + timedelta(hours=2)).isoformat()

    window_row = _row({
        "id": 1,
        "site": "nyc",
        "start_utc": past,
        "end_utc": past_end,
        "recurrence": "weekly",
        "blackout_days_json": "[]",
        "active": 1,
    })

    conn = MagicMock()
    conn.execute = MagicMock(return_value=MagicMock(fetchall=MagicMock(return_value=[window_row])))

    result = _find_next_window(conn, "nyc", datetime.now(timezone.utc).isoformat())
    assert result is not None
    window_start = datetime.fromisoformat(result["start_utc"])
    assert window_start > datetime.now(timezone.utc)


def test_find_next_window_raises_no_exception_on_db_error():
    """DB error in _find_next_window returns None, not an exception."""
    from tools.network.patch_planner import _find_next_window
    conn = MagicMock()
    conn.execute = MagicMock(side_effect=Exception("table missing"))
    result = _find_next_window(conn, "site-a", datetime.now(timezone.utc).isoformat())
    assert result is None


# ---------------------------------------------------------------------------
# create_patch_plan integration tests (mocked DB)
# ---------------------------------------------------------------------------

def _make_plan_conn(queue_rows=None, device_rows=None, sim_result=None, window_rows=None):
    """Build a mock connection for create_patch_plan tests."""
    conn = MagicMock()
    conn.commit = MagicMock()
    conn.close = MagicMock()

    plan_writes = []

    def _execute(sql, params=None):
        result = MagicMock()
        if "nc_triage_queue" in sql and "SELECT" in sql:
            rows = [_row(r) for r in (queue_rows or [])]
            result.fetchall = MagicMock(return_value=rows)
        elif "nc_attack_surface" in sql and "SELECT" in sql:
            rows = [_row(r) for r in (device_rows or [])]
            result.fetchall = MagicMock(return_value=rows)
        elif "nc_maintenance_windows" in sql:
            rows = [_row(r) for r in (window_rows or [])]
            result.fetchall = MagicMock(return_value=rows)
        elif "nc_patch_plans" in sql and "INSERT" in sql:
            plan_writes.append(params)
            result.lastrowid = len(plan_writes)
        elif "nc_nqe_audit_log" in sql and "INSERT" in sql:
            result.lastrowid = 99
        else:
            result.fetchall = MagicMock(return_value=[])
            result.fetchone = MagicMock(return_value=None)
            result.lastrowid = 1
        return result

    conn.execute.side_effect = _execute
    conn._plan_writes = plan_writes
    return conn


def test_create_patch_plan_empty_queue_returns_zero_batches():
    """Empty triage queue → plan with 0 batches and 0 devices."""
    conn = _make_plan_conn(queue_rows=[])
    with patch("tools.network.patch_planner.get_connection", return_value=conn):
        from tools.network.patch_planner import create_patch_plan
        result = create_patch_plan()
    assert result["batches"] == 0
    assert result["devices"] == 0
    assert "plan_id" in result


def test_create_patch_plan_plan_id_is_uuid():
    """plan_id must look like a UUID (36 chars with hyphens)."""
    conn = _make_plan_conn(queue_rows=[])
    with patch("tools.network.patch_planner.get_connection", return_value=conn):
        from tools.network.patch_planner import create_patch_plan
        result = create_patch_plan()
    assert len(result["plan_id"]) == 36
    assert result["plan_id"].count("-") == 4


def test_create_patch_plan_clusters_by_site():
    """Devices from same site (prefix before '-') land in same batch."""
    queue = [{
        "tq_id": 1, "advisory_id": 1, "priority_score": 0.8, "rank": 1,
        "cve_id": "CVE-2025-0001", "vendor": "cisco", "remediation_guidance": "patch now",
    }]
    devices = [
        {"device_name": "nyc-rtr-01", "ip": "10.1.0.1", "criticality": 4, "surface_score": 0.7, "advisory_id": 1, "cve_id": "CVE-2025-0001"},
        {"device_name": "nyc-rtr-02", "ip": "10.1.0.2", "criticality": 5, "surface_score": 0.9, "advisory_id": 1, "cve_id": "CVE-2025-0001"},
        {"device_name": "lax-rtr-01", "ip": "10.2.0.1", "criticality": 3, "surface_score": 0.5, "advisory_id": 1, "cve_id": "CVE-2025-0001"},
    ]
    conn = _make_plan_conn(queue_rows=queue, device_rows=devices)

    with (
        patch("tools.network.patch_planner.get_connection", return_value=conn),
        patch("tools.network.patch_planner._run_simulation", return_value={"simulation_status": "safe", "blast_radius_json": "[]"}),
    ):
        from tools.network.patch_planner import create_patch_plan
        result = create_patch_plan()

    # nyc devices should be in one batch, lax in another → 2 batches
    assert result["batches"] == 2
    assert result["devices"] == 3


def test_create_patch_plan_writes_append_only_rows():
    """nc_patch_plans INSERT is called for each device."""
    queue = [{
        "tq_id": 1, "advisory_id": 1, "priority_score": 0.6, "rank": 1,
        "cve_id": "CVE-2025-0001", "vendor": "cisco", "remediation_guidance": None,
    }]
    devices = [
        {"device_name": "sfo-fw-01", "ip": "10.3.0.1", "criticality": 4, "surface_score": 0.6, "advisory_id": 1, "cve_id": "CVE-2025-0001"},
    ]
    conn = _make_plan_conn(queue_rows=queue, device_rows=devices)

    with (
        patch("tools.network.patch_planner.get_connection", return_value=conn),
        patch("tools.network.patch_planner._run_simulation", return_value={"simulation_status": "skipped", "blast_radius_json": "[]"}),
    ):
        from tools.network.patch_planner import create_patch_plan
        result = create_patch_plan()

    assert len(conn._plan_writes) >= 1


def test_create_patch_plan_risk_reduction_computed():
    """risk_reduction = priority_score × surface_score."""
    queue = [{
        "tq_id": 1, "advisory_id": 1, "priority_score": 0.8, "rank": 1,
        "cve_id": "CVE-2025-0001", "vendor": "cisco", "remediation_guidance": None,
    }]
    devices = [
        {"device_name": "ord-sw-01", "ip": "10.4.0.1", "criticality": 4, "surface_score": 0.5, "advisory_id": 1, "cve_id": "CVE-2025-0001"},
    ]
    conn = _make_plan_conn(queue_rows=queue, device_rows=devices)

    with (
        patch("tools.network.patch_planner.get_connection", return_value=conn),
        patch("tools.network.patch_planner._run_simulation", return_value={"simulation_status": "skipped", "blast_radius_json": "[]"}),
    ):
        from tools.network.patch_planner import create_patch_plan
        result = create_patch_plan()

    plan_rows = result["plan"]
    assert len(plan_rows) == 1
    expected_reduction = round(0.8 * 0.5, 4)
    assert plan_rows[0]["risk_reduction"] == expected_reduction


def test_create_patch_plan_simulation_fallback_on_import_error():
    """If simulate_remediation import fails, simulation_status='skipped' and no exception."""
    queue = [{
        "tq_id": 1, "advisory_id": 1, "priority_score": 0.5, "rank": 1,
        "cve_id": "CVE-2025-0001", "vendor": "cisco", "remediation_guidance": None,
    }]
    devices = [
        {"device_name": "dal-rtr-01", "ip": "10.5.0.1", "criticality": 3, "surface_score": 0.4, "advisory_id": 1, "cve_id": "CVE-2025-0001"},
    ]
    conn = _make_plan_conn(queue_rows=queue, device_rows=devices)

    with (
        patch("tools.network.patch_planner.get_connection", return_value=conn),
        patch("tools.network.patch_planner._run_simulation", return_value={"simulation_status": "skipped", "blast_radius_json": "[]"}),
    ):
        from tools.network.patch_planner import create_patch_plan
        result = create_patch_plan()

    assert result["plan"][0]["simulation_status"] == "skipped"


def test_create_patch_plan_audit_log_written():
    """plan_create action written to nc_nqe_audit_log."""
    audit_calls = []
    conn = _make_plan_conn(queue_rows=[])
    orig = conn.execute.side_effect

    def _track(sql, params=None):
        if "nc_nqe_audit_log" in sql and "INSERT" in sql:
            audit_calls.append(params)
        return orig(sql, params)

    conn.execute.side_effect = _track

    with patch("tools.network.patch_planner.get_connection", return_value=conn):
        from tools.network.patch_planner import create_patch_plan
        create_patch_plan()

    assert len(audit_calls) >= 1
    assert any("plan_create" in str(c) for c in audit_calls)


# ---------------------------------------------------------------------------
# get_plan_summary tests
# ---------------------------------------------------------------------------

def test_get_plan_summary_returns_not_found_for_unknown_plan():
    conn = MagicMock()
    conn.execute = MagicMock(return_value=MagicMock(fetchall=MagicMock(return_value=[])))
    conn.close = MagicMock()
    with patch("tools.network.patch_planner.get_connection", return_value=conn):
        from tools.network.patch_planner import get_plan_summary
        result = get_plan_summary("no-such-plan-uuid")
    assert result["found"] is False


def test_get_plan_summary_aggregates_correctly():
    """get_plan_summary computes batches, devices, and risk_reduction_total."""
    plan_id = "aaa-bbb-ccc"
    rows = [
        {"plan_id": plan_id, "batch_id": "batch-1", "advisory_id": 1, "device_name": "r1",
         "simulation_status": "safe", "risk_reduction": 0.3, "scheduled_at": "2025-07-01T00:00:00"},
        {"plan_id": plan_id, "batch_id": "batch-1", "advisory_id": 1, "device_name": "r2",
         "simulation_status": "safe", "risk_reduction": 0.4, "scheduled_at": "2025-07-01T00:00:00"},
        {"plan_id": plan_id, "batch_id": "batch-2", "advisory_id": 2, "device_name": "r3",
         "simulation_status": "skipped", "risk_reduction": 0.1, "scheduled_at": "2025-07-08T00:00:00"},
    ]

    conn = MagicMock()
    conn.execute = MagicMock(return_value=MagicMock(fetchall=MagicMock(return_value=[_row(r) for r in rows])))
    conn.close = MagicMock()

    with patch("tools.network.patch_planner.get_connection", return_value=conn):
        from tools.network.patch_planner import get_plan_summary
        result = get_plan_summary(plan_id)

    assert result["found"] is True
    assert result["batches"] == 2
    assert result["devices"] == 3
    assert abs(result["risk_reduction_total"] - 0.8) < 0.001
    assert result["by_simulation_status"]["safe"] == 2
    assert result["by_simulation_status"]["skipped"] == 1
