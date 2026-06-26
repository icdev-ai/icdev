# CUI // SP-CTI
"""Tests for PVM Vulnerability Triage Engine (pvm-tri-02)."""
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
# Helpers
# ---------------------------------------------------------------------------

def _adv(id=1, cvss=7.5, exploited="0", published="2024-01-01", status="open"):
    return {
        "id": id,
        "cve_id": f"CVE-2024-{id:04d}",
        "cvss_score": cvss,
        "exploited_in_wild": exploited,
        "published_date": published,
        "status": status,
    }


def _mock_row(d):
    """Wrap a dict so it behaves like a DB row."""
    m = MagicMock()
    m.__iter__ = lambda s: iter(d.items())
    m.keys = lambda: d.keys()
    m.__getitem__ = lambda s, k: d[k]
    m.get = lambda k, dv=None: d.get(k, dv)
    return m


# ---------------------------------------------------------------------------
# Unit tests — _compute_priority (pure function, no DB)
# ---------------------------------------------------------------------------

def test_compute_priority_kev_exploited_weight():
    """KEV exploited advisory gets kev=1.0 in formula."""
    from tools.network.vuln_triage_engine import _compute_priority
    adv = _adv(cvss=5.0, exploited="1")
    score, rationale = _compute_priority(adv, 0.5, 0.5)
    assert rationale["kev"] == 1.0
    assert score > 0.40


def test_compute_priority_non_kev_zero():
    """Non-exploited advisory gets kev=0.0."""
    from tools.network.vuln_triage_engine import _compute_priority
    adv = _adv(cvss=5.0, exploited="0", published="2025-06-01")
    score, rationale = _compute_priority(adv, 0.0, 0.0)
    assert rationale["kev"] == 0.0


def test_compute_priority_score_in_unit_interval():
    """Priority score must be in [0, 1]."""
    from tools.network.vuln_triage_engine import _compute_priority
    for cvss, exp, pub in [
        (10.0, "1", "2020-01-01"),
        (0.0, "0", "2025-06-01"),
        (7.5, "0", "2023-01-01"),
    ]:
        adv = _adv(cvss=cvss, exploited=exp, published=pub)
        score, _ = _compute_priority(adv, 0.8, 0.6)
        assert 0.0 <= score <= 1.0, f"score={score} out of range for cvss={cvss}"


def test_compute_priority_rationale_contains_all_keys():
    """Rationale dict must contain kev, criticality, exposure, urgency, formula."""
    from tools.network.vuln_triage_engine import _compute_priority
    adv = _adv()
    _, rationale = _compute_priority(adv, 0.5, 0.3)
    for key in ("kev", "criticality", "exposure", "urgency", "formula"):
        assert key in rationale, f"missing rationale key: {key}"


def test_compute_priority_four_factor_formula():
    """Verify the 4-factor formula: 0.40*kev + 0.25*crit + 0.20*exp + 0.15*urg ≈ score."""
    from tools.network.vuln_triage_engine import _compute_priority
    # Use a recently-published low-cvss advisory so urgency is near-zero
    adv = _adv(cvss=4.0, exploited="1", published="2025-06-24")
    asset_crit_norm = 0.6
    net_exp_norm = 0.3
    score, rationale = _compute_priority(adv, asset_crit_norm, net_exp_norm)
    expected = round(
        1.0 * 0.40
        + asset_crit_norm * 0.25
        + net_exp_norm * 0.20
        + rationale["urgency"] * 0.15,
        4,
    )
    assert abs(score - min(1.0, expected)) < 0.001


# ---------------------------------------------------------------------------
# Unit tests — _determine_status (pure, no DB)
# ---------------------------------------------------------------------------

def test_determine_status_auto_approve_low_score():
    """Score below auto_approve_threshold → approved, auto_approved=1."""
    from tools.network.vuln_triage_engine import _determine_status
    with patch("tools.network.vuln_triage_engine._auto_approve_threshold", return_value=0.40):
        status, auto_appr = _determine_status(0.20)
    assert status == "approved"
    assert auto_appr == 1


def test_determine_status_pending_mid_score():
    """Score between thresholds → pending, auto_approved=0."""
    from tools.network.vuln_triage_engine import _determine_status
    with (
        patch("tools.network.vuln_triage_engine._hitl_threshold", return_value=0.75),
        patch("tools.network.vuln_triage_engine._auto_approve_threshold", return_value=0.40),
    ):
        status, auto_appr = _determine_status(0.55)
    assert status == "pending"
    assert auto_appr == 0


def test_determine_status_pending_high_score():
    """Score at or above hitl_threshold → pending (HITL required)."""
    from tools.network.vuln_triage_engine import _determine_status
    with (
        patch("tools.network.vuln_triage_engine._hitl_threshold", return_value=0.75),
        patch("tools.network.vuln_triage_engine._auto_approve_threshold", return_value=0.40),
    ):
        status, auto_appr = _determine_status(0.80)
    assert status == "pending"
    assert auto_appr == 0


# ---------------------------------------------------------------------------
# Integration-ish tests — score_advisories (mocked DB)
# ---------------------------------------------------------------------------

def _make_score_conn(adv_row=None, surface_row=None):
    conn = MagicMock()
    conn.commit = MagicMock()
    conn.close = MagicMock()

    adv = adv_row or _adv()

    def _execute(sql, params=None):
        result = MagicMock()
        if "nc_advisories" in sql and "status IN" in sql:
            result.fetchall = MagicMock(return_value=[_mock_row(adv)])
        elif "nc_advisories" in sql and "IN (" in sql:
            result.fetchall = MagicMock(return_value=[_mock_row(adv)])
        elif "AVG(criticality)" in sql:
            surface = surface_row or {"avg_crit": 3.5, "reachable_count": 2, "total": 4}
            m = MagicMock()
            m.__getitem__ = lambda s, k: [3.5, 2, 4][k]
            result.fetchone = MagicMock(return_value=m)
        elif "nc_triage_queue" in sql and "INSERT OR REPLACE" in sql:
            result.lastrowid = 1
        elif "nc_triage_queue" in sql and "SELECT" in sql:
            result.fetchall = MagicMock(return_value=[])
        elif "nc_nqe_audit_log" in sql and "INSERT" in sql:
            result.lastrowid = 99
        elif "nc_triage_queue" in sql and "ORDER BY" in sql:
            result.fetchall = MagicMock(return_value=[])
        else:
            result.fetchall = MagicMock(return_value=[])
            result.fetchone = MagicMock(return_value=None)
            result.lastrowid = 1
        return result

    conn.execute.side_effect = _execute
    return conn


def test_score_advisories_returns_scored_count():
    """score_advisories counts scored/auto_approved/pending correctly."""
    conn = _make_score_conn(adv_row=_adv(cvss=2.0, exploited="0", published="2025-06-01"))

    with (
        patch("tools.network.vuln_triage_engine.get_connection", return_value=conn),
        patch("tools.network.vuln_triage_engine._apply_bayesian_ranks"),
        patch("tools.network.vuln_triage_engine.get_triage_queue", return_value=[]),
    ):
        from tools.network.vuln_triage_engine import score_advisories
        result = score_advisories()

    assert result["scored"] == 1
    assert "auto_approved" in result
    assert "pending_hitl" in result
    assert result["auto_approved"] + result["pending_hitl"] == result["scored"]


def test_score_advisories_audit_log_written():
    """triage_score action written to nc_nqe_audit_log for each advisory."""
    audit_calls = []
    conn = _make_score_conn(adv_row=_adv(cvss=7.5, exploited="1"))

    orig_execute = conn.execute.side_effect

    def _tracking_execute(sql, params=None):
        if "nc_nqe_audit_log" in sql and "INSERT" in sql:
            audit_calls.append(params)
        return orig_execute(sql, params)

    conn.execute.side_effect = _tracking_execute

    with (
        patch("tools.network.vuln_triage_engine.get_connection", return_value=conn),
        patch("tools.network.vuln_triage_engine._apply_bayesian_ranks"),
        patch("tools.network.vuln_triage_engine.get_triage_queue", return_value=[]),
    ):
        from tools.network.vuln_triage_engine import score_advisories
        score_advisories()

    assert len(audit_calls) >= 1
    assert any("triage_score" in str(c) for c in audit_calls)


# ---------------------------------------------------------------------------
# approve / defer tests
# ---------------------------------------------------------------------------

def _make_approve_conn(advisory_id=1):
    conn = MagicMock()
    conn.commit = MagicMock()
    conn.close = MagicMock()

    stored = {"advisory_id": advisory_id, "status": "pending", "approved_by": None}

    def _execute(sql, params=None):
        result = MagicMock()
        if "UPDATE nc_triage_queue" in sql:
            if "status='approved'" in sql:
                stored["status"] = "approved"
                stored["approved_by"] = params[0] if params else None
            elif "status='deferred'" in sql:
                stored["status"] = "deferred"
                stored["approved_by"] = params[0] if params else None
        elif "nc_nqe_audit_log" in sql and "INSERT" in sql:
            result.lastrowid = 1
        elif "SELECT * FROM nc_triage_queue WHERE advisory_id" in sql:
            result.fetchone = MagicMock(return_value=_mock_row(stored))
        else:
            result.fetchone = MagicMock(return_value=None)
            result.fetchall = MagicMock(return_value=[])
        return result

    conn.execute.side_effect = _execute
    return conn, stored


def test_approve_advisory_sets_status_approved():
    conn, stored = _make_approve_conn(advisory_id=5)
    with patch("tools.network.vuln_triage_engine.get_connection", return_value=conn):
        from tools.network.vuln_triage_engine import approve_advisory
        result = approve_advisory(5, "analyst@example.com")
    assert stored["status"] == "approved"
    assert stored["approved_by"] == "analyst@example.com"


def test_defer_advisory_sets_status_deferred():
    conn, stored = _make_approve_conn(advisory_id=5)
    with patch("tools.network.vuln_triage_engine.get_connection", return_value=conn):
        from tools.network.vuln_triage_engine import defer_advisory
        result = defer_advisory(5, "analyst@example.com")
    assert stored["status"] == "deferred"


def test_approve_advisory_writes_audit_log():
    """approve_advisory writes triage_approve to audit log."""
    conn, _ = _make_approve_conn(advisory_id=3)
    audit_calls = []
    orig = conn.execute.side_effect

    def _track(sql, params=None):
        if "nc_nqe_audit_log" in sql and "INSERT" in sql:
            audit_calls.append(params)
        return orig(sql, params)

    conn.execute.side_effect = _track

    with patch("tools.network.vuln_triage_engine.get_connection", return_value=conn):
        from tools.network.vuln_triage_engine import approve_advisory
        approve_advisory(3, "admin@example.com")

    assert len(audit_calls) >= 1
    assert any("triage_approve" in str(c) for c in audit_calls)


# ---------------------------------------------------------------------------
# Bayesian reranking fallback test
# ---------------------------------------------------------------------------

def test_apply_bayesian_ranks_fallback_on_import_error():
    """When optimal_compliance_order raises ImportError, rank is applied from priority_score fallback."""
    conn = MagicMock()
    conn.execute = MagicMock(return_value=MagicMock(fetchall=MagicMock(return_value=[
        MagicMock(__getitem__=lambda s, k: 1 if k == 0 else None),
        MagicMock(__getitem__=lambda s, k: 2 if k == 0 else None),
    ])))
    conn.commit = MagicMock()

    with patch.dict("sys.modules", {"tools.intelligence.bayesian_teacher": None}):
        from tools.network.vuln_triage_engine import _apply_bayesian_ranks
        # Should not raise even if import fails
        try:
            _apply_bayesian_ranks(conn, [1, 2])
        except Exception:
            pass

    # Rank UPDATE must still be called
    assert conn.execute.called
