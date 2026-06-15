#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for NOVA TRUST — tools/ace/trust_calibrator.py

Validates: import, get_trust_band boundaries, initial score,
Bayesian deltas, clamping, ledger write (append-only),
get_dispatch_config, unknown event_type handling.
All tests run on the SQLite test backend (forced by conftest).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest


# ---------------------------------------------------------------------------
# Import check
# ---------------------------------------------------------------------------

def test_trust_calibrator_imports():
    """Module imports without error."""
    import tools.ace.trust_calibrator  # noqa: F401


def test_trust_calibrator_constants():
    """Key constants are present with expected values."""
    from tools.ace.trust_calibrator import (
        TRUST_PROBATIONARY, TRUST_SUPERVISED, TRUST_TRUSTED,
        INITIAL_TRUST, TRUST_MIN, TRUST_MAX,
    )
    assert TRUST_PROBATIONARY == 0.3
    assert TRUST_SUPERVISED == 0.6
    assert TRUST_TRUSTED == 0.8
    assert INITIAL_TRUST == 0.5
    assert TRUST_MIN > 0.0
    assert TRUST_MAX < 1.0


# ---------------------------------------------------------------------------
# get_trust_band
# ---------------------------------------------------------------------------

def test_trust_band_probationary():
    """Score < 0.3 → probationary band."""
    from tools.ace.trust_calibrator import get_trust_band
    band = get_trust_band(0.1)
    assert band.name == "probationary"
    assert band.hitl_mode == "all"
    assert band.max_parallel == 0
    assert band.auto_approve_routine is False


def test_trust_band_supervised():
    """Score 0.3–0.6 → supervised band."""
    from tools.ace.trust_calibrator import get_trust_band
    band = get_trust_band(0.45)
    assert band.name == "supervised"
    assert band.hitl_mode == "risky"
    assert band.max_parallel == 1
    assert band.auto_approve_routine is False


def test_trust_band_trusted():
    """Score 0.6–0.8 → trusted band."""
    from tools.ace.trust_calibrator import get_trust_band
    band = get_trust_band(0.7)
    assert band.name == "trusted"
    assert band.hitl_mode == "irreversible"
    assert band.max_parallel == 2
    assert band.auto_approve_routine is True


def test_trust_band_autonomous():
    """Score >= 0.8 → autonomous band."""
    from tools.ace.trust_calibrator import get_trust_band
    band = get_trust_band(0.9)
    assert band.name == "autonomous"
    assert band.hitl_mode == "none"
    assert band.max_parallel == 4
    assert band.auto_approve_routine is True


def test_trust_band_at_boundaries():
    """Boundary scores map to correct bands (exclusive lower, inclusive upper)."""
    from tools.ace.trust_calibrator import get_trust_band
    assert get_trust_band(0.3).name == "supervised"    # exactly 0.3 → supervised
    assert get_trust_band(0.6).name == "trusted"       # exactly 0.6 → trusted
    assert get_trust_band(0.8).name == "autonomous"    # exactly 0.8 → autonomous
    assert get_trust_band(0.299).name == "probationary"


# ---------------------------------------------------------------------------
# get_trust_score (initial value)
# ---------------------------------------------------------------------------

def test_get_trust_score_unknown_role_returns_initial():
    """Unknown role_id returns INITIAL_TRUST (0.5)."""
    from tools.ace.trust_calibrator import get_trust_score, INITIAL_TRUST
    score = get_trust_score("nova-test-role-unknown-zzz")
    assert score == INITIAL_TRUST


# ---------------------------------------------------------------------------
# record_trust_event — Bayesian deltas
# ---------------------------------------------------------------------------

def _fresh_role() -> str:
    """Return a unique role_id so tests don't share state."""
    import uuid
    return f"nova-test-role-{uuid.uuid4().hex[:8]}"


def test_record_trust_event_success_increases_score():
    """success event increases trust by +0.05."""
    from tools.ace.trust_calibrator import record_trust_event, INITIAL_TRUST
    role = _fresh_role()
    result = record_trust_event(role, "success", "task-001")
    assert result.get("old_score") == INITIAL_TRUST
    assert abs(result.get("new_score") - (INITIAL_TRUST + 0.05)) < 0.001
    assert result.get("delta") == pytest.approx(+0.05)


def test_record_trust_event_failure_decreases_score():
    """failure event decreases trust by -0.10."""
    from tools.ace.trust_calibrator import record_trust_event, INITIAL_TRUST
    role = _fresh_role()
    result = record_trust_event(role, "failure", "task-002")
    assert result.get("delta") == pytest.approx(-0.10)
    assert result.get("new_score") == pytest.approx(INITIAL_TRUST - 0.10)


def test_record_trust_event_hitl_escalation():
    """hitl_escalation decreases trust by -0.03."""
    from tools.ace.trust_calibrator import record_trust_event
    role = _fresh_role()
    result = record_trust_event(role, "hitl_escalation")
    assert result.get("delta") == pytest.approx(-0.03)


def test_record_trust_event_timeout():
    """timeout decreases trust by -0.05."""
    from tools.ace.trust_calibrator import record_trust_event
    role = _fresh_role()
    result = record_trust_event(role, "timeout")
    assert result.get("delta") == pytest.approx(-0.05)


def test_record_trust_event_phantom_completion():
    """phantom_completion decreases trust by -0.08."""
    from tools.ace.trust_calibrator import record_trust_event
    role = _fresh_role()
    result = record_trust_event(role, "phantom_completion")
    assert result.get("delta") == pytest.approx(-0.08)


def test_record_trust_event_unknown_type_skipped():
    """Unknown event_type returns {skipped: True} and does not write to ledger."""
    from tools.ace.trust_calibrator import record_trust_event
    role = _fresh_role()
    result = record_trust_event(role, "nonexistent_event_type")
    assert result.get("skipped") is True


# ---------------------------------------------------------------------------
# Trust score clamping
# ---------------------------------------------------------------------------

def test_trust_clamped_at_minimum():
    """Score never drops below TRUST_MIN even after many failures."""
    from tools.ace.trust_calibrator import record_trust_event, get_trust_score, TRUST_MIN
    role = _fresh_role()
    for _ in range(20):
        record_trust_event(role, "failure")
    score = get_trust_score(role)
    assert score >= TRUST_MIN, f"score {score} is below TRUST_MIN {TRUST_MIN}"


def test_trust_clamped_at_maximum():
    """Score never exceeds TRUST_MAX even after many successes."""
    from tools.ace.trust_calibrator import record_trust_event, get_trust_score, TRUST_MAX
    role = _fresh_role()
    for _ in range(30):
        record_trust_event(role, "success")
    score = get_trust_score(role)
    assert score <= TRUST_MAX, f"score {score} exceeds TRUST_MAX {TRUST_MAX}"


# ---------------------------------------------------------------------------
# Ledger persistence (append-only)
# ---------------------------------------------------------------------------

def test_record_trust_event_writes_ledger_row():
    """record_trust_event inserts a row into ace_trust_ledger."""
    from tools.ace.trust_calibrator import record_trust_event
    from tools.db.storage import get_connection

    role = _fresh_role()
    result = record_trust_event(role, "success", "task-ledger-001")

    conn = get_connection()
    rows = conn.execute(
        "SELECT id, role_id, delta, reason, new_score "
        "FROM ace_trust_ledger WHERE role_id = ?",
        (role,),
    ).fetchall()
    conn.close()

    assert len(rows) >= 1, "No ledger row written"
    row = rows[-1]
    if isinstance(row, dict):
        assert row["reason"] == "success"
        assert row["new_score"] == pytest.approx(result["new_score"])
    else:
        assert row[3] == "success"
        assert row[4] == pytest.approx(result["new_score"])


def test_trust_ledger_accumulates_events():
    """Multiple events produce multiple ledger rows (append-only)."""
    from tools.ace.trust_calibrator import record_trust_event
    from tools.db.storage import get_connection

    role = _fresh_role()
    for evt in ("success", "failure", "success"):
        record_trust_event(role, evt)

    conn = get_connection()
    count = conn.execute(
        "SELECT COUNT(*) FROM ace_trust_ledger WHERE role_id = ?",
        (role,),
    ).fetchone()[0]
    conn.close()

    assert count == 3, f"Expected 3 ledger rows, got {count}"


# ---------------------------------------------------------------------------
# get_dispatch_config
# ---------------------------------------------------------------------------

def test_get_dispatch_config_returns_expected_keys():
    """get_dispatch_config returns all required keys."""
    from tools.ace.trust_calibrator import get_dispatch_config
    role = _fresh_role()
    cfg = get_dispatch_config(role)
    for key in ("role_id", "trust_score", "band", "hitl_mode",
                "max_parallel", "auto_approve_routine"):
        assert key in cfg, f"Missing key: {key}"


def test_get_dispatch_config_reflects_trust_score():
    """get_dispatch_config band matches the current trust score."""
    from tools.ace.trust_calibrator import (
        get_dispatch_config, record_trust_event, get_trust_band,
    )
    role = _fresh_role()
    # Drive score up past 0.8 threshold
    for _ in range(8):
        record_trust_event(role, "success")

    cfg = get_dispatch_config(role)
    expected_band = get_trust_band(cfg["trust_score"]).name
    assert cfg["band"] == expected_band


def test_get_dispatch_config_new_role_supervised():
    """A brand-new role starts in supervised band (score = 0.5)."""
    from tools.ace.trust_calibrator import get_dispatch_config
    role = _fresh_role()
    cfg = get_dispatch_config(role)
    assert cfg["band"] == "supervised"
    assert cfg["trust_score"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# result shape from record_trust_event
# ---------------------------------------------------------------------------

def test_record_trust_event_result_shape():
    """record_trust_event result has expected keys and types."""
    from tools.ace.trust_calibrator import record_trust_event
    role = _fresh_role()
    result = record_trust_event(role, "success", "task-shape-001")

    assert "role_id" in result
    assert "event_type" in result
    assert "old_score" in result
    assert "delta" in result
    assert "new_score" in result
    assert "band" in result
    assert "hitl_mode" in result
    assert "max_parallel" in result
    assert isinstance(result["new_score"], float)
    assert isinstance(result["band"], str)
