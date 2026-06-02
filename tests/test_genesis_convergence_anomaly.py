"""Tests for AnomalyDetector in tools/genesis/convergence.py."""
import statistics
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.genesis.convergence import (
    AnomalyDetector,
    ConvergenceGate,
    _CLARITY_KEYWORD_DIVISOR,
    _DEFAULT_CONVERGENCE_SIMILARITY,
    _DEFAULT_MAX_ACCEPTABLE_DRIFT,
    _DEFAULT_MAX_AMBIGUITY,
    _SLOPE_NORMALIZER,
)


# ── AnomalyDetector._upper_bound ──────────────────────────────────────────


def test_upper_bound_empty_returns_one():
    assert AnomalyDetector._upper_bound([], z=2.0) == 1.0


def test_upper_bound_single_value_no_std():
    result = AnomalyDetector._upper_bound([0.3], z=2.0)
    assert result == pytest.approx(0.3, abs=1e-6)


def test_upper_bound_clamps_above_one():
    result = AnomalyDetector._upper_bound([0.9, 0.95, 0.99], z=10.0)
    assert result == 1.0


def test_upper_bound_clamps_below_zero():
    result = AnomalyDetector._upper_bound([0.01, 0.02], z=-10.0)
    assert result == 0.0


def test_upper_bound_computes_mean_plus_z_std():
    values = [0.1, 0.2, 0.3, 0.4, 0.5]
    mean = statistics.mean(values)
    std = statistics.stdev(values)
    expected = mean + 2.0 * std
    assert AnomalyDetector._upper_bound(values, z=2.0) == pytest.approx(expected, abs=1e-6)


# ── AnomalyDetector._fetch_history ────────────────────────────────────────


def _mock_conn(rows):
    """Return a mock connection whose execute().fetchall() returns rows."""
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = rows
    return conn


def test_fetch_history_returns_empty_on_db_error():
    with patch("tools.db.storage.get_connection", side_effect=Exception("no db")):
        result = AnomalyDetector._fetch_history("test_reflex")
    assert result == []


def test_fetch_history_maps_rows_to_dicts():
    rows = [(0.1, 0.2, 0.3), (0.4, 0.5, 0.6)]
    conn = _mock_conn(rows)
    with patch("tools.db.storage.get_connection", return_value=conn):
        result = AnomalyDetector._fetch_history("r1")
    assert len(result) == 2
    assert result[0] == {"output_similarity": 0.1, "combined_drift": 0.2, "ambiguity_score": 0.3}
    assert result[1] == {"output_similarity": 0.4, "combined_drift": 0.5, "ambiguity_score": 0.6}


def test_fetch_history_replaces_none_with_zero():
    rows = [(None, None, None)]
    conn = _mock_conn(rows)
    with patch("tools.db.storage.get_connection", return_value=conn):
        result = AnomalyDetector._fetch_history("r1")
    assert result[0] == {"output_similarity": 0.0, "combined_drift": 0.0, "ambiguity_score": 0.0}


# ── AnomalyDetector.adaptive_thresholds ───────────────────────────────────

CONFIG = {
    "convergence_similarity": 0.95,
    "max_acceptable_drift": 0.30,
    "max_ambiguity": 0.20,
}


def test_adaptive_thresholds_falls_back_when_insufficient_history():
    with patch.object(AnomalyDetector, "_fetch_history", return_value=[]):
        result = AnomalyDetector.adaptive_thresholds("r", CONFIG, min_samples=5)
    assert result["adaptive"] is False
    assert result["n_samples"] == 0
    assert result["convergence_similarity"] == 0.95
    assert result["max_acceptable_drift"] == 0.30
    assert result["max_ambiguity"] == 0.20


def test_adaptive_thresholds_activates_when_enough_history():
    history = [
        {"output_similarity": 0.1, "combined_drift": 0.05, "ambiguity_score": 0.1}
    ] * 5
    with patch.object(AnomalyDetector, "_fetch_history", return_value=history):
        result = AnomalyDetector.adaptive_thresholds("r", CONFIG, min_samples=5, z_score=2.0)
    assert result["adaptive"] is True
    assert result["n_samples"] == 5


def test_adaptive_thresholds_computes_from_history():
    sims = [0.1, 0.2, 0.1, 0.15, 0.12]
    drifts = [0.1, 0.15, 0.12, 0.08, 0.11]
    ambigs = [0.05, 0.08, 0.06, 0.07, 0.09]
    history = [
        {"output_similarity": s, "combined_drift": d, "ambiguity_score": a}
        for s, d, a in zip(sims, drifts, ambigs)
    ]
    with patch.object(AnomalyDetector, "_fetch_history", return_value=history):
        result = AnomalyDetector.adaptive_thresholds("r", CONFIG, min_samples=5, z_score=2.0)
    assert result["adaptive"] is True
    expected_sim = min(1.0, statistics.mean(sims) + 2.0 * statistics.stdev(sims))
    assert result["convergence_similarity"] == pytest.approx(expected_sim, abs=1e-6)


def test_adaptive_thresholds_boundary_exactly_min_samples():
    history = [
        {"output_similarity": 0.1, "combined_drift": 0.1, "ambiguity_score": 0.1}
    ] * 4
    with patch.object(AnomalyDetector, "_fetch_history", return_value=history):
        result = AnomalyDetector.adaptive_thresholds("r", CONFIG, min_samples=5)
    assert result["adaptive"] is False  # 4 < 5, still falls back

    history_5 = history + [{"output_similarity": 0.1, "combined_drift": 0.1, "ambiguity_score": 0.1}]
    with patch.object(AnomalyDetector, "_fetch_history", return_value=history_5):
        result = AnomalyDetector.adaptive_thresholds("r", CONFIG, min_samples=5)
    assert result["adaptive"] is True  # exactly 5 → adaptive activates


# ── Named constants ────────────────────────────────────────────────────────


def test_slope_normalizer_is_ten():
    assert _SLOPE_NORMALIZER == 10.0


def test_clarity_keyword_divisor_is_three():
    assert _CLARITY_KEYWORD_DIVISOR == 3


# ── ConvergenceGate.__init__ reads anomaly_detection config ───────────────


def test_convergence_gate_reads_anomaly_config():
    cfg = {
        "anomaly_detection": {"min_samples": 8, "z_score": 3.0},
    }
    gate = ConvergenceGate(cfg)
    assert gate._anomaly_min_samples == 8
    assert gate._anomaly_z_score == 3.0


def test_convergence_gate_defaults_when_no_anomaly_config():
    gate = ConvergenceGate({})
    assert gate._anomaly_min_samples == AnomalyDetector.DEFAULT_MIN_SAMPLES
    assert gate._anomaly_z_score == AnomalyDetector.DEFAULT_Z_SCORE


# ── ConvergenceGate.evaluate result includes thresholds_used ──────────────


def _make_gate(min_samples=99):
    """Gate that always falls back to config thresholds (min_samples impossibly large)."""
    return ConvergenceGate(
        {
            "weights": {"goal_drift": 0.5, "metric_drift": 0.3, "output_similarity": 0.2},
            "thresholds": {"convergence_similarity": 0.95, "max_acceptable_drift": 0.30, "max_ambiguity": 0.20},
            "ambiguity_weights": {"problem_clarity": 0.4, "solution_clarity": 0.3, "verification_clarity": 0.3},
            "retrospective": {"trigger_every_n_cycles": 5, "trigger_on_drift_above": 0.30},
            "window_size": 10,
            "anomaly_detection": {"min_samples": min_samples, "z_score": 2.0},
        }
    )


def _stub_db_calls(mock_conn_fn, metric_rows=None, gkp_row=None, convergence_rows=None):
    """Configure get_connection mock for all three DB reads in evaluate()."""
    calls = []

    def _get_conn():
        conn = MagicMock()
        calls.append(conn)
        # Default: no prior metric data, no GKP hash, no convergence history
        conn.execute.return_value.fetchall.return_value = metric_rows or []
        conn.execute.return_value.fetchone.return_value = gkp_row
        return conn

    mock_conn_fn.side_effect = _get_conn
    return calls


def test_evaluate_includes_thresholds_used_key():
    gate = _make_gate()
    with patch("tools.db.storage.get_connection") as mock_conn:
        _stub_db_calls(mock_conn)
        result = gate.evaluate("reflex_a", 0.5, "some output text", generation=1)
    assert "thresholds_used" in result
    tu = result["thresholds_used"]
    assert "convergence_similarity" in tu
    assert "max_acceptable_drift" in tu
    assert "max_ambiguity" in tu
    assert tu["adaptive"] is False  # min_samples=99, no history


def test_evaluate_thresholds_used_adaptive_true_when_history_present():
    gate = _make_gate(min_samples=2)
    history = [
        {"output_similarity": 0.1, "combined_drift": 0.1, "ambiguity_score": 0.1},
        {"output_similarity": 0.15, "combined_drift": 0.12, "ambiguity_score": 0.08},
    ]
    with patch("tools.db.storage.get_connection") as mock_conn, \
         patch.object(AnomalyDetector, "_fetch_history", return_value=history):
        _stub_db_calls(mock_conn)
        result = gate.evaluate("reflex_b", 0.5, "output", generation=2)
    assert result["thresholds_used"]["adaptive"] is True


# ── _should_trigger_retrospective uses effective threshold ─────────────────


def test_retrospective_uses_effective_threshold():
    gate = _make_gate()
    # High drift but below config default (0.30), above a tighter effective threshold (0.15)
    assert gate._should_trigger_retrospective(3, 0.20, effective_drift_threshold=0.15) is True
    # Same drift, effective threshold is looser
    assert gate._should_trigger_retrospective(3, 0.20, effective_drift_threshold=0.25) is False


def test_retrospective_falls_back_to_config_when_no_effective():
    gate = _make_gate()
    # combined_drift=0.35 > config trigger_on_drift_above=0.30
    assert gate._should_trigger_retrospective(3, 0.35, effective_drift_threshold=None) is True
    assert gate._should_trigger_retrospective(3, 0.25, effective_drift_threshold=None) is False


# ── Named threshold constants ─────────────────────────────────────────────────


def test_default_convergence_similarity_constant():
    assert _DEFAULT_CONVERGENCE_SIMILARITY == pytest.approx(0.95, abs=1e-9)


def test_default_max_acceptable_drift_constant():
    assert _DEFAULT_MAX_ACCEPTABLE_DRIFT == pytest.approx(0.30, abs=1e-9)


def test_default_max_ambiguity_constant():
    assert _DEFAULT_MAX_AMBIGUITY == pytest.approx(0.20, abs=1e-9)


# ── ConvergenceGate.__init__ seeds thresholds from named defaults ─────────────


def test_convergence_gate_empty_config_uses_named_defaults():
    gate = ConvergenceGate({})
    assert gate.thresholds["convergence_similarity"] == _DEFAULT_CONVERGENCE_SIMILARITY
    assert gate.thresholds["max_acceptable_drift"] == _DEFAULT_MAX_ACCEPTABLE_DRIFT
    assert gate.thresholds["max_ambiguity"] == _DEFAULT_MAX_AMBIGUITY


def test_convergence_gate_config_thresholds_override_defaults():
    cfg = {
        "thresholds": {
            "convergence_similarity": 0.80,
            "max_acceptable_drift": 0.50,
            "max_ambiguity": 0.10,
        }
    }
    gate = ConvergenceGate(cfg)
    assert gate.thresholds["convergence_similarity"] == pytest.approx(0.80, abs=1e-9)
    assert gate.thresholds["max_acceptable_drift"] == pytest.approx(0.50, abs=1e-9)
    assert gate.thresholds["max_ambiguity"] == pytest.approx(0.10, abs=1e-9)


def test_convergence_gate_partial_config_merges_with_defaults():
    """Partial config overrides only specified keys; others remain at defaults."""
    cfg = {"thresholds": {"max_acceptable_drift": 0.45}}
    gate = ConvergenceGate(cfg)
    assert gate.thresholds["max_acceptable_drift"] == pytest.approx(0.45, abs=1e-9)
    assert gate.thresholds["convergence_similarity"] == _DEFAULT_CONVERGENCE_SIMILARITY
    assert gate.thresholds["max_ambiguity"] == _DEFAULT_MAX_AMBIGUITY
