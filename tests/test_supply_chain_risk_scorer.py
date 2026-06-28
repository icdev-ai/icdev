# CUI // SP-CTI
"""Tests for PNA Supply Chain Risk Scorer (pna-scs-03)."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


_REPO = Path(__file__).parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn(fetchall_rows=None, fetchone_val=None):
    conn = MagicMock()
    cursor = MagicMock()
    cursor.lastrowid = 1
    cursor.fetchall.return_value = fetchall_rows or []
    cursor.fetchone.return_value = fetchone_val or (0, 0, None, 0.0)
    cursor.description = [("c1",), ("c2",)]
    conn.execute.return_value = cursor
    conn.close = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    return conn


def _nqe_result(rows):
    return {"rows": rows, "source": "nqe_api", "error": None}


def _device_row(vendor="cisco", model="ASR-9000", name="rtr-01", ip="10.0.0.1"):
    return {
        "name": name,
        "platform": {"ostype": vendor, "platform": model},
        "management": {"ip": ip},
    }


def _advisory_row():
    return (3, 2, 1, 9.8)  # total, critical_high, kev_count, max_cvss


# ---------------------------------------------------------------------------
# Unit tests: _normalize_vendor
# ---------------------------------------------------------------------------

def test_normalize_vendor_ios_to_cisco():
    from tools.network.supply_chain_risk_scorer import _normalize_vendor
    assert _normalize_vendor("ios") == "cisco"
    assert _normalize_vendor("ios-xe") == "cisco"
    assert _normalize_vendor("nxos") == "cisco"


def test_normalize_vendor_junos_to_juniper():
    from tools.network.supply_chain_risk_scorer import _normalize_vendor
    assert _normalize_vendor("junos") == "juniper"


def test_normalize_vendor_pan_os_to_palo_alto():
    from tools.network.supply_chain_risk_scorer import _normalize_vendor
    assert _normalize_vendor("pan-os") == "palo_alto"


def test_normalize_vendor_unknown_passthrough():
    from tools.network.supply_chain_risk_scorer import _normalize_vendor
    assert _normalize_vendor("some-unknown") == "some-unknown"


def test_normalize_vendor_none_returns_unknown():
    from tools.network.supply_chain_risk_scorer import _normalize_vendor
    assert _normalize_vendor(None) == "unknown"
    assert _normalize_vendor("") == "unknown"


# ---------------------------------------------------------------------------
# Unit tests: _vendor_risk_rating
# ---------------------------------------------------------------------------

def test_vendor_risk_rating_critical():
    from tools.network.supply_chain_risk_scorer import _vendor_risk_rating
    assert _vendor_risk_rating(0.90) == "critical"
    assert _vendor_risk_rating(0.75) == "critical"


def test_vendor_risk_rating_high():
    from tools.network.supply_chain_risk_scorer import _vendor_risk_rating
    assert _vendor_risk_rating(0.65) == "high"
    assert _vendor_risk_rating(0.50) == "high"


def test_vendor_risk_rating_medium():
    from tools.network.supply_chain_risk_scorer import _vendor_risk_rating
    assert _vendor_risk_rating(0.40) == "medium"
    assert _vendor_risk_rating(0.25) == "medium"


def test_vendor_risk_rating_low():
    from tools.network.supply_chain_risk_scorer import _vendor_risk_rating
    assert _vendor_risk_rating(0.10) == "low"
    assert _vendor_risk_rating(0.0) == "low"


# ---------------------------------------------------------------------------
# Unit tests: _compute_vendor_risk
# ---------------------------------------------------------------------------

def test_compute_vendor_risk_no_cves():
    from tools.network.supply_chain_risk_scorer import _compute_vendor_risk
    vendor_info = {"device_count": 5, "models": {"M1"}, "device_names": ["d1"]}
    advisory = {"cve_count": 0, "critical_high": 0, "kev_count": 0, "max_cvss": 0.0}
    result = _compute_vendor_risk(vendor_info, advisory)
    assert result["risk_score"] == 0.0
    assert "kev_norm" in result


def test_compute_vendor_risk_kev_heavy():
    from tools.network.supply_chain_risk_scorer import _compute_vendor_risk
    vendor_info = {"device_count": 3, "models": set(), "device_names": []}
    advisory = {"cve_count": 10, "critical_high": 8, "kev_count": 5, "max_cvss": 9.8}
    result = _compute_vendor_risk(vendor_info, advisory)
    assert result["risk_score"] >= 0.50


def test_compute_vendor_risk_score_within_bounds():
    from tools.network.supply_chain_risk_scorer import _compute_vendor_risk
    vendor_info = {"device_count": 10, "models": set(), "device_names": []}
    for cve_count, kev_count, crit in [(0, 0, 0), (5, 2, 3), (100, 10, 80)]:
        advisory = {"cve_count": cve_count, "critical_high": crit, "kev_count": kev_count, "max_cvss": 9.0}
        result = _compute_vendor_risk(vendor_info, advisory)
        assert 0.0 <= result["risk_score"] <= 1.0


# ---------------------------------------------------------------------------
# Integration tests: score_supply_chain_risk
# ---------------------------------------------------------------------------

def test_score_supply_chain_risk_empty_nqe():
    conn = _make_conn(fetchall_rows=[])
    with patch("tools.network.supply_chain_risk_scorer.get_connection") as mock_gc:
        mock_gc.return_value = conn

        mock_client = MagicMock()
        mock_client.run_query.return_value = {"rows": [], "source": "nqe_api"}
        with patch("tools.network.supply_chain_risk_scorer._get_nqe_device_inventory",
                   return_value=[]):
            # Also patch advisory fallback query
            conn.execute.return_value.fetchall.return_value = []
            from tools.network.supply_chain_risk_scorer import score_supply_chain_risk
            result = score_supply_chain_risk()
            assert result["vendors_scored"] == 0
            assert result["scores"] == []


def test_score_supply_chain_risk_required_keys():
    devices = [
        {"name": "rtr-01", "vendor": "cisco", "model": "ASR", "ip": "10.0.0.1"},
        {"name": "rtr-02", "vendor": "juniper", "model": "MX", "ip": "10.0.0.2"},
    ]
    conn = _make_conn(fetchone_val=(0, 0, 0, 0.0))
    conn.execute.return_value.fetchall.return_value = []

    with patch("tools.network.supply_chain_risk_scorer.get_connection") as mock_gc, \
         patch("tools.network.supply_chain_risk_scorer._get_nqe_device_inventory",
               return_value=devices):
        mock_gc.return_value = conn
        from tools.network.supply_chain_risk_scorer import score_supply_chain_risk
        result = score_supply_chain_risk()
        assert "vendors_scored" in result
        assert "by_rating" in result
        assert "scores" in result


def test_score_supply_chain_risk_groups_by_vendor():
    devices = [
        {"name": "rtr-01", "vendor": "cisco", "model": "ASR", "ip": "10.0.0.1"},
        {"name": "rtr-02", "vendor": "cisco", "model": "ISR", "ip": "10.0.0.2"},
        {"name": "sw-01", "vendor": "juniper", "model": "EX", "ip": "10.0.0.3"},
    ]
    conn = _make_conn(fetchone_val=(0, 0, 0, 0.0))
    conn.execute.return_value.fetchall.return_value = []

    with patch("tools.network.supply_chain_risk_scorer.get_connection") as mock_gc, \
         patch("tools.network.supply_chain_risk_scorer._get_nqe_device_inventory",
               return_value=devices):
        mock_gc.return_value = conn
        from tools.network.supply_chain_risk_scorer import score_supply_chain_risk
        result = score_supply_chain_risk()
        vendor_names = [s["vendor"] for s in result["scores"]]
        assert "cisco" in vendor_names
        assert "juniper" in vendor_names
        cisco_entry = next(s for s in result["scores"] if s["vendor"] == "cisco")
        assert cisco_entry["device_count"] == 2


# ---------------------------------------------------------------------------
# Integration tests: get_supply_chain_risks / get_supply_chain_summary
# ---------------------------------------------------------------------------

def test_get_supply_chain_risks_rating_filter():
    conn = _make_conn()
    with patch("tools.network.supply_chain_risk_scorer.get_connection") as mock_gc:
        mock_gc.return_value = conn
        from tools.network.supply_chain_risk_scorer import get_supply_chain_risks
        get_supply_chain_risks(rating="critical")
        sql = conn.execute.call_args[0][0]
        assert "vendor_risk_rating" in sql


def test_get_supply_chain_risks_vendor_filter():
    conn = _make_conn()
    with patch("tools.network.supply_chain_risk_scorer.get_connection") as mock_gc:
        mock_gc.return_value = conn
        from tools.network.supply_chain_risk_scorer import get_supply_chain_risks
        get_supply_chain_risks(vendor="cisco")
        sql = conn.execute.call_args[0][0]
        assert "vendor" in sql.lower()


def test_get_supply_chain_summary_empty_returns_keys():
    conn = _make_conn(fetchall_rows=[])
    with patch("tools.network.supply_chain_risk_scorer.get_connection") as mock_gc:
        mock_gc.return_value = conn
        from tools.network.supply_chain_risk_scorer import get_supply_chain_summary
        result = get_supply_chain_summary()
        assert "total_vendors" in result
        assert "critical_vendors" in result
        assert "high_risk_vendors" in result
        assert "total_kev_exposure" in result
        assert "top_risk_vendors" in result
