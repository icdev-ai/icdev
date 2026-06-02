"""Tests for tools/genesis/goal_learner.py — hardcoded_threshold → anomaly_detection (aiify-5226)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.genesis.goal_learner import (
    DEFAULT_NOVELTY_THRESHOLD,
    _REFLEX_DEFAULTS,
    _AdaptiveThreshold,
    _cluster_into_domains,
    _compute_quality_score,
    _novelty_score,
    _tokenize,
)


# ---------------------------------------------------------------------------
# _novelty_score
# ---------------------------------------------------------------------------


def test_novelty_score_all_novel():
    domain_kws = ["quantum", "blockchain", "photon"]
    goal_kws = ["python", "flask", "sqlite"]
    assert _novelty_score(domain_kws, goal_kws) == 1.0


def test_novelty_score_no_novel():
    kws = ["python", "flask"]
    assert _novelty_score(kws, kws) == 0.0


def test_novelty_score_partial():
    domain_kws = ["python", "quantum", "photon"]
    goal_kws = ["python", "flask"]
    score = _novelty_score(domain_kws, goal_kws)
    assert abs(score - 2 / 3) < 1e-6


def test_novelty_score_empty_domain():
    assert _novelty_score([], ["python"]) == 0.0


# ---------------------------------------------------------------------------
# _AdaptiveThreshold — static fallback when insufficient samples
# ---------------------------------------------------------------------------


def _fake_conn_with_rows(rows):
    """Return a mock connection whose fetchall returns the given rows."""
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = rows
    return conn


@patch("tools.genesis.goal_learner.get_connection")
def test_adaptive_threshold_returns_static_when_few_samples(mock_gc):
    mock_gc.return_value = _fake_conn_with_rows([(0.7,), (0.8,)])  # only 2 rows
    result = _AdaptiveThreshold.compute(static_threshold=0.6, min_samples=5, z_score=2.0)
    assert result == 0.6  # falls back to static


@patch("tools.genesis.goal_learner.get_connection")
def test_adaptive_threshold_computes_from_history(mock_gc):
    # 10 identical scores → mean=0.5, std=0, threshold=0.5
    rows = [(0.5,)] * 10
    mock_gc.return_value = _fake_conn_with_rows(rows)
    result = _AdaptiveThreshold.compute(static_threshold=0.6, min_samples=5, z_score=2.0)
    assert abs(result - 0.5) < 1e-6


@patch("tools.genesis.goal_learner.get_connection")
def test_adaptive_threshold_clamped_to_unit_interval(mock_gc):
    # Very high scores → mean + z*std > 1.0, should clamp to 1.0
    rows = [(0.95,)] * 10
    mock_gc.return_value = _fake_conn_with_rows(rows)
    result = _AdaptiveThreshold.compute(static_threshold=0.6, min_samples=5, z_score=2.0)
    assert result <= 1.0


@patch("tools.genesis.goal_learner.get_connection")
def test_adaptive_threshold_falls_back_on_db_error(mock_gc):
    mock_gc.side_effect = Exception("DB error")
    result = _AdaptiveThreshold.compute(static_threshold=0.6, min_samples=5, z_score=2.0)
    assert result == 0.6


# ---------------------------------------------------------------------------
# _cluster_into_domains — novelty + evidence_count filters
# ---------------------------------------------------------------------------


def _make_audit_event(text: str) -> Dict[str, Any]:
    return {"id": "ev-1", "event_type": text, "reflex_name": text, "details": None}


def _make_task(text: str, tid: str = "t-1") -> Dict[str, Any]:
    return {"id": tid, "title": text, "description": "", "task_type": "feature"}


@patch("tools.genesis.goal_learner._NLPExtractor.extract", side_effect=lambda t: _tokenize(t))
def test_cluster_filters_by_novelty_threshold(mock_extract):
    # Low-novelty event — all keywords already in goal vocab
    goal_kws = ["python", "flask", "database", "query", "connection"]
    events = [_make_audit_event("python flask database query connection"), _make_audit_event("python flask")]
    tasks = []
    domains = _cluster_into_domains(events, tasks, goal_kws, novelty_threshold=0.9, min_evidence_count=1)
    # All terms overlap → novelty close to 0 → filtered out
    assert all(d["novelty_score"] >= 0.9 for d in domains)


@patch("tools.genesis.goal_learner._NLPExtractor.extract", side_effect=lambda t: _tokenize(t))
def test_cluster_filters_by_min_evidence_count(mock_extract):
    """Domains with only 1 supporting event should be excluded when min_evidence_count=2."""
    goal_kws = ["common"]
    events = [_make_audit_event("quantum photonic entanglement")]  # 1 event
    tasks = []
    domains = _cluster_into_domains(events, tasks, goal_kws, novelty_threshold=0.0, min_evidence_count=2)
    # Only 1 piece of evidence → filtered out by min_evidence_count
    assert len(domains) == 0


@patch("tools.genesis.goal_learner._NLPExtractor.extract", side_effect=lambda t: _tokenize(t))
def test_cluster_includes_domain_with_sufficient_evidence(mock_extract):
    """Domains with >= min_evidence_count events pass when novelty is high enough."""
    goal_kws = ["common"]
    events = [
        _make_audit_event("quantum photonic entanglement"),
        _make_audit_event("quantum photonic resonance"),
    ]
    tasks = []
    domains = _cluster_into_domains(events, tasks, goal_kws, novelty_threshold=0.0, min_evidence_count=2)
    assert len(domains) >= 1
    assert all(d["evidence_count"] >= 2 for d in domains)


@patch("tools.genesis.goal_learner._NLPExtractor.extract", side_effect=lambda t: _tokenize(t))
def test_cluster_default_min_evidence_count_is_two(mock_extract):
    """Verify the default min_evidence_count=2 is applied when omitted."""
    goal_kws = []
    events = [_make_audit_event("novel domain discovery")]  # only 1 event
    tasks = []
    # Call without explicit min_evidence_count — should use the default of 2
    domains = _cluster_into_domains(events, tasks, goal_kws, novelty_threshold=0.0)
    assert len(domains) == 0


# ---------------------------------------------------------------------------
# _compute_quality_score
# ---------------------------------------------------------------------------


def test_quality_score_perfect():
    overall, _, _, _ = _compute_quality_score(1.0, 100, ["alpha"] * 10)
    assert 0.0 <= overall <= 1.0


def test_quality_score_zero_novelty_zero_evidence():
    overall, ev_norm, _, _ = _compute_quality_score(0.0, 1, [])
    assert overall >= 0.0
    assert ev_norm == 0.0


def test_quality_score_uses_custom_weights():
    weights = {"novelty_score": 1.0, "evidence_count": 0.0, "domain_clarity": 0.0, "tool_coverage": 0.0}
    overall, _, _, _ = _compute_quality_score(0.5, 5, ["abc"], quality_weights=weights)
    assert abs(overall - 0.5) < 1e-6


# ---------------------------------------------------------------------------
# _REFLEX_DEFAULTS — ensure new keys are present
# ---------------------------------------------------------------------------


def test_reflex_defaults_has_min_evidence_count():
    assert "min_evidence_count" in _REFLEX_DEFAULTS
    assert _REFLEX_DEFAULTS["min_evidence_count"] == 2


def test_reflex_defaults_has_anomaly_detection_block():
    ad = _REFLEX_DEFAULTS.get("anomaly_detection", {})
    assert "min_samples" in ad
    assert "z_score" in ad
