# CUI // SP-CTI
"""Tests for PVM Attack Surface Mapper (pvm-asm-02)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO = Path(__file__).parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")


# ---------------------------------------------------------------------------
# Tests for pure helper functions (no DB)
# ---------------------------------------------------------------------------

def test_criticality_from_cvss_critical():
    from tools.network.attack_surface_mapper import _criticality_from_cvss
    assert _criticality_from_cvss(9.0) == 5
    assert _criticality_from_cvss(10.0) == 5


def test_criticality_from_cvss_high():
    from tools.network.attack_surface_mapper import _criticality_from_cvss
    assert _criticality_from_cvss(7.0) == 4
    assert _criticality_from_cvss(8.9) == 4


def test_criticality_from_cvss_medium():
    from tools.network.attack_surface_mapper import _criticality_from_cvss
    assert _criticality_from_cvss(5.0) == 3
    assert _criticality_from_cvss(6.9) == 3


def test_criticality_from_cvss_none_defaults_to_3():
    from tools.network.attack_surface_mapper import _criticality_from_cvss
    assert _criticality_from_cvss(None) == 3


def test_surface_score_formula():
    """surface_score = cvss/10*0.5 + reachable*0.3 + bgp_exposed*0.2."""
    from tools.network.attack_surface_mapper import _surface_score
    score = _surface_score(8.0, reachable=True, bgp_exposed=True)
    expected = round(0.8 * 0.5 + 1.0 * 0.3 + 1.0 * 0.2, 4)
    assert score == expected


def test_surface_score_clipped_to_unit():
    from tools.network.attack_surface_mapper import _surface_score
    score = _surface_score(10.0, reachable=True, bgp_exposed=True)
    assert score <= 1.0


def test_surface_score_unreachable_no_bgp():
    """Non-reachable, non-BGP device: only CVSS contributes."""
    from tools.network.attack_surface_mapper import _surface_score
    score = _surface_score(6.0, reachable=False, bgp_exposed=False)
    expected = round(0.6 * 0.5, 4)
    assert score == expected


def test_exposure_type_bgp_exposed():
    from tools.network.attack_surface_mapper import _exposure_type
    assert _exposure_type(bgp_exposed=True, plugin_name=None) == "network"


def test_exposure_type_local_plugin():
    from tools.network.attack_surface_mapper import _exposure_type
    assert _exposure_type(bgp_exposed=False, plugin_name="local security check") == "local"


def test_exposure_type_unknown_plugin():
    from tools.network.attack_surface_mapper import _exposure_type
    assert _exposure_type(bgp_exposed=False, plugin_name=None) == "unknown"


# ---------------------------------------------------------------------------
# Tests for _build_device_map
# ---------------------------------------------------------------------------

def test_build_device_map_reachable_flag_set_from_interfaces():
    from tools.network.attack_surface_mapper import _build_device_map
    nqe = {
        "devices": [{"name": "router1", "managementIp": "10.0.0.1"}],
        "interfaces": [{"ip": "10.0.0.1"}],
        "bgp_down": [],
    }
    dmap = _build_device_map(nqe)
    assert dmap["10.0.0.1"]["reachable"] is True


def test_build_device_map_bgp_exposed_flag():
    from tools.network.attack_surface_mapper import _build_device_map
    nqe = {
        "devices": [{"name": "router1", "managementIp": "10.0.0.1"}],
        "interfaces": [],
        "bgp_down": [{"peerAddress": "10.0.0.1"}],
    }
    dmap = _build_device_map(nqe)
    assert dmap["10.0.0.1"]["bgp_exposed"] is True


def test_build_device_map_unknown_device_skipped():
    """Devices without ip or name are silently dropped."""
    from tools.network.attack_surface_mapper import _build_device_map
    nqe = {
        "devices": [{}],  # no name, no ip
        "interfaces": [],
        "bgp_down": [],
    }
    dmap = _build_device_map(nqe)
    assert len(dmap) == 0


# ---------------------------------------------------------------------------
# Tests for advisory model-match logic
# ---------------------------------------------------------------------------

def _make_mock_conn(surface_rows=None, advisory_rows=None, scan_row=None):
    """Build a minimal mock connection for attack_surface_mapper tests."""
    conn = MagicMock()
    conn.commit = MagicMock()
    conn.close = MagicMock()

    def _execute(sql, params=None):
        result = MagicMock()
        if "nc_attack_surface" in sql and "INSERT" in sql:
            result.lastrowid = 1
        elif "nc_attack_surface" in sql and "SELECT" in sql:
            rows = surface_rows or []
            result.fetchall = MagicMock(return_value=[dict_to_mock(r) for r in rows])
            result.fetchone = MagicMock(return_value=dict_to_mock(rows[0]) if rows else None)
        elif "nc_advisories" in sql:
            rows = advisory_rows or []
            result.fetchall = MagicMock(return_value=[dict_to_mock(r) for r in rows])
        elif "nc_vuln_scans" in sql:
            result.fetchone = MagicMock(return_value=dict_to_mock(scan_row) if scan_row else None)
        elif "nc_nqe_audit_log" in sql and "INSERT" in sql:
            result.lastrowid = 99
        else:
            result.fetchall = MagicMock(return_value=[])
            result.fetchone = MagicMock(return_value=None)
        return result

    conn.execute = MagicMock(side_effect=_execute)
    return conn


def dict_to_mock(d):
    m = MagicMock()
    m.__iter__ = lambda s: iter(d.items())
    m.keys = lambda: d.keys()
    m.__getitem__ = lambda s, k: d[k]
    m.get = lambda k, dv=None: d.get(k, dv)
    m.__getitem__.__func__ = lambda s, k: d[k]
    return m


def test_process_advisory_model_match_writes_row():
    """When advisory.affected_models_json contains device name substring, row is written."""
    from tools.network.attack_surface_mapper import _process_advisory_model_match

    advisories = {
        "CVE-2025-1234": {
            "id": 1,
            "cve_id": "CVE-2025-1234",
            "cvss_score": 8.5,
            "affected_models_json": '["cisco-router"]',
        }
    }
    device_map = {
        "10.0.0.1": {
            "name": "cisco-router-01",
            "ip": "10.0.0.1",
            "platform": "ios",
            "reachable": True,
            "bgp_exposed": False,
        }
    }
    conn = MagicMock()
    conn.execute = MagicMock(return_value=MagicMock(lastrowid=1))

    count = _process_advisory_model_match(conn, device_map, advisories, "local_mapping")
    assert count >= 1
    assert conn.execute.called


def test_process_advisory_model_match_no_affected_models_skips():
    """Advisory with empty affected_models_json writes nothing."""
    from tools.network.attack_surface_mapper import _process_advisory_model_match

    advisories = {
        "CVE-2025-0001": {
            "id": 1,
            "cve_id": "CVE-2025-0001",
            "cvss_score": 7.0,
            "affected_models_json": "[]",
        }
    }
    device_map = {
        "10.0.0.1": {"name": "router-1", "ip": "10.0.0.1", "platform": "", "reachable": True, "bgp_exposed": False}
    }
    conn = MagicMock()
    conn.execute = MagicMock()

    count = _process_advisory_model_match(conn, device_map, advisories, "local_mapping")
    assert count == 0
    conn.execute.assert_not_called()


# ---------------------------------------------------------------------------
# get_surface_summary / get_attack_surface — DB-patched tests
# ---------------------------------------------------------------------------

def test_get_surface_summary_returns_dict_keys():
    """get_surface_summary always returns dict with required keys."""
    conn = MagicMock()

    def _exec(sql, params=None):
        result = MagicMock()
        if "COUNT(*)" in sql and "WHERE reachable=1" in sql:
            result.fetchone = MagicMock(return_value=[7])
        elif "COUNT(*)" in sql and "WHERE criticality=5" in sql:
            result.fetchone = MagicMock(return_value=[2])
        elif "COUNT(*)" in sql:
            result.fetchone = MagicMock(return_value=[15])
        elif "GROUP BY criticality" in sql:
            result.fetchall = MagicMock(return_value=[(4, 5), (5, 2)])
        else:
            result.fetchone = MagicMock(return_value=[0])
            result.fetchall = MagicMock(return_value=[])
        return result

    conn.execute.side_effect = _exec
    conn.close = MagicMock()

    with patch("tools.network.attack_surface_mapper._conn", return_value=conn):
        from tools.network.attack_surface_mapper import get_surface_summary
        summary = get_surface_summary()

    assert "total_entries" in summary
    assert "reachable_count" in summary
    assert "critical_count" in summary
    assert "by_criticality" in summary


def test_get_attack_surface_filters_by_cve():
    """get_attack_surface passes cve_id filter in query."""
    conn = MagicMock()
    called_params = []

    def _exec(sql, params=None):
        called_params.append((sql, params))
        result = MagicMock()
        result.fetchall = MagicMock(return_value=[])
        return result

    conn.execute.side_effect = _exec
    conn.close = MagicMock()

    with patch("tools.network.attack_surface_mapper._conn", return_value=conn):
        from tools.network.attack_surface_mapper import get_attack_surface
        get_attack_surface(cve_id="CVE-2025-1234")

    assert any("CVE-2025-1234" in str(p) for _, p in called_params)


def test_map_attack_surface_returns_success_keys():
    """map_attack_surface always returns success/device/advisory count keys."""
    nqe_result = {"rows": [], "source": "local_mapping"}

    conn = MagicMock()
    conn.commit = MagicMock()
    conn.close = MagicMock()

    def _exec(sql, params=None):
        result = MagicMock()
        result.fetchall = MagicMock(return_value=[])
        result.fetchone = MagicMock(return_value=None)
        result.lastrowid = 1
        return result

    conn.execute.side_effect = _exec

    with (
        patch("tools.network.attack_surface_mapper._conn", return_value=conn),
        patch("tools.network.attack_surface_mapper._run_nqe_queries", return_value={
            "devices": [], "interfaces": [], "bgp_down": [], "source": "local_mapping"
        }),
    ):
        from tools.network.attack_surface_mapper import map_attack_surface
        result = map_attack_surface()

    assert "success" in result
    assert result["success"] is True
    assert "total_rows_written" in result
