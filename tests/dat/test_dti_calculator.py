#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for DTI Calculator — tools/dat/dti_calculator.py.

Acceptance criterion: compute_dti_from_manifest() returns a float in [0.0, 1.0]
when provided with valid sample data for cables, schedules, and metadata.
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE_DIR))

from tools.dat.dti_calculator import (  # noqa: E402
    compute_dti_from_manifest,
    _score_cables,
    _score_schedules,
    _score_metadata,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = "2026-05-16T12:00:00+00:00"


def _manifest(cables=None, schedules=None, metadata=None) -> dict:
    return {
        "cables": cables or [],
        "schedules": schedules or [],
        "metadata": metadata or [],
    }


# ---------------------------------------------------------------------------
# Acceptance criterion
# ---------------------------------------------------------------------------


def test_returns_float_in_unit_interval_with_sample_data():
    """Core acceptance test: valid sample data → float in [0.0, 1.0]."""
    manifest = _manifest(
        cables=[
            {"tension_level": "high", "received_at": _NOW},
            {"tension_level": "medium", "received_at": _NOW},
        ],
        schedules=[
            {"emergency": True, "veto_cast": False, "walkout": False},
            {"emergency": False, "veto_cast": True, "walkout": False},
        ],
        metadata=[
            {"escalation_flag": True, "communication_breakdown": False, "frequency_delta": -0.5},
            {"escalation_flag": False, "communication_breakdown": True, "frequency_delta": 0.1},
        ],
    )
    score = compute_dti_from_manifest(manifest)
    assert isinstance(score, float)
    assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_manifest_returns_zero():
    assert compute_dti_from_manifest(_manifest()) == 0.0


def test_none_values_treated_as_empty():
    assert compute_dti_from_manifest({"cables": None, "schedules": None, "metadata": None}) == 0.0


def test_missing_keys_returns_zero():
    assert compute_dti_from_manifest({}) == 0.0


def test_missing_fields_inside_records_handled_gracefully():
    manifest = _manifest(cables=[{}], schedules=[{}], metadata=[{}])
    score = compute_dti_from_manifest(manifest)
    assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# Tension direction
# ---------------------------------------------------------------------------


def test_critical_cables_yield_higher_score_than_low():
    low = compute_dti_from_manifest(
        _manifest(cables=[{"tension_level": "low", "received_at": _NOW}])
    )
    critical = compute_dti_from_manifest(
        _manifest(cables=[{"tension_level": "critical", "received_at": _NOW}])
    )
    assert critical > low


def test_emergency_veto_walkout_raises_score():
    baseline = compute_dti_from_manifest(_manifest(schedules=[{"emergency": False, "veto_cast": False, "walkout": False}]))
    elevated = compute_dti_from_manifest(_manifest(schedules=[{"emergency": True, "veto_cast": True, "walkout": True}]))
    assert elevated > baseline


def test_escalation_raises_backchannel_score():
    calm = _score_metadata([{"escalation_flag": False, "communication_breakdown": False, "frequency_delta": 0.5}])
    tense = _score_metadata([{"escalation_flag": True, "communication_breakdown": True, "frequency_delta": -0.8}])
    assert tense > calm


# ---------------------------------------------------------------------------
# Boundary / saturation
# ---------------------------------------------------------------------------


def test_maximum_tension_stays_at_or_below_one():
    manifest = _manifest(
        cables=[{"tension_level": "critical", "received_at": _NOW}] * 10,
        schedules=[{"emergency": True, "veto_cast": True, "walkout": True}] * 10,
        metadata=[{"escalation_flag": True, "communication_breakdown": True, "frequency_delta": -1.0}] * 10,
    )
    score = compute_dti_from_manifest(manifest)
    assert score <= 1.0


def test_minimum_tension_stays_at_or_above_zero():
    manifest = _manifest(
        cables=[{"tension_level": "low", "received_at": _NOW}],
        schedules=[{"emergency": False, "veto_cast": False, "walkout": False}],
        metadata=[{"escalation_flag": False, "communication_breakdown": False, "frequency_delta": 1.0}],
    )
    score = compute_dti_from_manifest(manifest)
    assert score >= 0.0


# ---------------------------------------------------------------------------
# Per-component scorers
# ---------------------------------------------------------------------------


def test_score_cables_empty():
    assert _score_cables([]) == 0.0


def test_score_cables_critical_near_one():
    score = _score_cables([{"tension_level": "critical", "received_at": _NOW}])
    assert score > 0.7


def test_score_schedules_empty():
    assert _score_schedules([]) == 0.0


def test_score_schedules_single_emergency_nonzero():
    score = _score_schedules([{"emergency": True, "veto_cast": False, "walkout": False}])
    assert score > 0.0


def test_score_metadata_empty():
    assert _score_metadata([]) == 0.0


def test_score_metadata_full_escalation_near_one():
    score = _score_metadata(
        [{"escalation_flag": True, "communication_breakdown": True, "frequency_delta": -1.0}] * 5
    )
    assert score >= 0.95


# ---------------------------------------------------------------------------
# Individual source isolation
# ---------------------------------------------------------------------------


def test_cables_only_produces_nonzero():
    score = compute_dti_from_manifest(_manifest(
        cables=[{"tension_level": "high", "received_at": _NOW}]
    ))
    assert 0.0 < score < 1.0


def test_schedules_only_produces_nonzero():
    score = compute_dti_from_manifest(_manifest(
        schedules=[{"emergency": True, "veto_cast": False, "walkout": False}]
    ))
    assert 0.0 < score < 1.0


def test_metadata_only_produces_nonzero():
    score = compute_dti_from_manifest(_manifest(
        metadata=[{"escalation_flag": True, "communication_breakdown": False, "frequency_delta": -0.5}]
    ))
    assert 0.0 < score < 1.0
