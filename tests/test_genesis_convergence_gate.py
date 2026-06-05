"""Tests for AnomalyDetector and updated ConvergenceGate in tools/genesis/convergence.py."""
import statistics
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.genesis.convergence import AnomalyDetector, ConvergenceGate


# ── AnomalyDetector._upper_bound ──────────────────────────────────────────────


def test_upper_bound_empty_returns_one():
    assert AnomalyDetector._upper_bound([], z=2.0) == 1.0


def test_upper_bound_single_value_returns_value():
    result = AnomalyDetector._upper_bound([0.3], z=2.0)
    assert result == pytest.approx(0.3, abs=1e-6)


def test_upper_bound_computes_mean_plus_z_std():
    values = [0.1, 0.2, 0.3, 0.4, 0.5]
    expected = min(1.0, max(0.0, statistics.mean(values) + 2.0 * statistics.stdev(values)))
    assert AnomalyDetector._upper_bound(values, z=2.0) == pytest.approx(expected, abs=1e-6)


def test_upper_bound_clamps_above_one():
    values = [0.9, 0.95, 1.0]
    result = AnomalyDetector._upper_bound(values, z=10.0)
    assert result == 1.0


def test_upper_bound_clamps_below_zero():
    values = [0.05, 0.02, 0.01]
    result = AnomalyDetector._upper_bound(values, z=-10.0)
    assert result == 0.0


# ── AnomalyDetector._fetch_history ────────────────────────────────────────────


def _make_conn(rows):
    """Create a mock connection that returns rows from genesis_convergence_log."""
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = rows
    mock_conn = MagicMock()
    mock_conn.execute.return_value = mock_cursor
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)
    return mock_conn


def test_fetch_history_returns_empty_on_db_error():
    with patch("tools.db.storage.get_connection", side_effect=Exception("db down")):
        result = AnomalyDetector._fetch_history("test_reflex")
    assert result == []


def test_fetch_history_returns_rows():
    rows = [(0.8, 0.2, 0.1), (0.7, 0.3, 0.15)]
    conn = _make_conn(rows)
    with patch("tools.db.storage.get_connection", return_value=conn):
        result = AnomalyDetector._fetch_history("test_reflex")
    assert len(result) == 2
    assert result[0]["output_similarity"] == 0.8
    assert result[0]["combined_drift"] == 0.2
    assert result[0]["ambiguity_score"] == 0.1


def test_fetch_history_replaces_null_with_zero():
    rows = [(None, None, None)]
    conn = _make_conn(rows)
    with patch("tools.db.storage.get_connection", return_value=conn):
        result = AnomalyDetector._fetch_history("test_reflex")
    assert result[0]["output_similarity"] == 0.0
    assert result[0]["combined_drift"] == 0.0
    assert result[0]["ambiguity_score"] == 0.0


# ── AnomalyDetector.adaptive_thresholds ───────────────────────────────────────


_CONFIG = {
    "convergence_similarity": 0.95,
    "max_acceptable_drift": 0.30,
    "max_ambiguity": 0.20,
}


def test_adaptive_thresholds_fallback_when_insufficient_history():
    """Returns config defaults when n < min_samples."""
    rows = [(0.8, 0.2, 0.1), (0.7, 0.3, 0.15)]  # only 2 rows, < default min_samples=5
    conn = _make_conn(rows)
    with patch("tools.db.storage.get_connection", return_value=conn):
        result = AnomalyDetector.adaptive_thresholds("r1", _CONFIG, min_samples=5)
    assert result["adaptive"] is False
    assert result["convergence_similarity"] == 0.95
    assert result["max_acceptable_drift"] == 0.30
    assert result["max_ambiguity"] == 0.20
    assert result["n_samples"] == 2


def test_adaptive_thresholds_uses_history_when_sufficient():
    """Returns adaptive values when n >= min_samples."""
    rows = [(0.5 + i * 0.05, 0.1 + i * 0.02, 0.1 + i * 0.01) for i in range(6)]
    conn = _make_conn(rows)
    with patch("tools.db.storage.get_connection", return_value=conn):
        result = AnomalyDetector.adaptive_thresholds("r1", _CONFIG, min_samples=5, z_score=2.0)
    assert result["adaptive"] is True
    assert result["n_samples"] == 6
    # Adaptive values should differ from static defaults (history has lower values)
    assert result["convergence_similarity"] < 0.95
    assert result["max_acceptable_drift"] < 0.30


def test_adaptive_thresholds_fallback_on_db_error():
    """Returns config defaults when DB is unavailable."""
    with patch("tools.db.storage.get_connection", side_effect=RuntimeError("no db")):
        result = AnomalyDetector.adaptive_thresholds("r1", _CONFIG, min_samples=1)
    assert result["adaptive"] is False
    assert result["convergence_similarity"] == 0.95


# ── ConvergenceGate._should_trigger_retrospective ─────────────────────────────


def _gate():
    return ConvergenceGate(
        {
            "weights": {"goal_drift": 0.5, "metric_drift": 0.3, "output_similarity": 0.2},
            "thresholds": _CONFIG,
            "ambiguity_weights": {},
            "retrospective": {"trigger_every_n_cycles": 5, "trigger_on_drift_above": 0.30},
            "window_size": 10,
        }
    )


def test_retrospective_periodic_trigger():
    gate = _gate()
    assert gate._should_trigger_retrospective(5, 0.0) is True
    assert gate._should_trigger_retrospective(10, 0.0) is True


def test_retrospective_no_trigger_below_threshold():
    gate = _gate()
    assert gate._should_trigger_retrospective(3, 0.10) is False


def test_retrospective_triggers_on_high_drift():
    gate = _gate()
    assert gate._should_trigger_retrospective(3, 0.50) is True


def test_retrospective_uses_effective_threshold_over_config():
    """effective_drift_threshold from AnomalyDetector overrides config trigger_on_drift_above."""
    gate = _gate()
    # Adaptive threshold = 0.40, drift = 0.35: below adaptive → no trigger
    assert gate._should_trigger_retrospective(3, 0.35, effective_drift_threshold=0.40) is False
    # Same drift = 0.35: above config 0.30 default but effective_drift_threshold=0.40 → no trigger
    assert gate._should_trigger_retrospective(3, 0.35) is True  # config 0.30 triggers


# ── ConvergenceGate.evaluate integration ──────────────────────────────────────


def _mock_gate_db(similarity_rows=None, audit_rows=None, gkp_row=None, convergence_rows=None):
    """Return a mock get_connection factory that handles multiple tables."""
    def _conn_factory():
        mock_conn = MagicMock()

        def _execute(sql, params=()):
            mock_cur = MagicMock()
            sql_lower = sql.lower()
            if "genesis_convergence_log" in sql_lower and "select output_similarity" in sql_lower:
                rows = convergence_rows or []
                mock_cur.fetchall.return_value = rows
            elif "genesis_audit" in sql_lower:
                mock_cur.fetchall.return_value = audit_rows or []
            elif "genesis_gkp" in sql_lower:
                mock_cur.fetchone.return_value = gkp_row
            else:
                mock_cur.fetchall.return_value = []
                mock_cur.fetchone.return_value = None
            return mock_cur

        mock_conn.execute.side_effect = _execute
        return mock_conn

    return _conn_factory


def test_evaluate_includes_thresholds_used():
    """evaluate() result must contain thresholds_used with adaptive flag."""
    gate = _gate()
    with patch("tools.db.storage.get_connection", side_effect=_mock_gate_db()):
        result = gate.evaluate("reflex_a", 0.8, "some output text", generation=1)
    assert "thresholds_used" in result
    assert "adaptive" in result["thresholds_used"]
    assert result["thresholds_used"]["adaptive"] is False  # no history → fallback


def test_evaluate_thresholds_used_adaptive_true_with_history():
    """thresholds_used.adaptive is True when sufficient history exists."""
    # 6 convergence rows (> min_samples=5)
    c_rows = [(0.5 + i * 0.05, 0.1 + i * 0.02, 0.1 + i * 0.01) for i in range(6)]
    gate = _gate()

    def _conn_factory():
        mock_conn = MagicMock()

        def _execute(sql, params=()):
            mock_cur = MagicMock()
            sql_lower = sql.lower()
            if "genesis_convergence_log" in sql_lower:
                mock_cur.fetchall.return_value = c_rows
            elif "genesis_audit" in sql_lower:
                mock_cur.fetchall.return_value = []
            elif "genesis_gkp" in sql_lower:
                mock_cur.fetchone.return_value = None
            else:
                mock_cur.fetchall.return_value = []
                mock_cur.fetchone.return_value = None
            return mock_cur

        mock_conn.execute.side_effect = _execute
        return mock_conn

    with patch("tools.db.storage.get_connection", side_effect=_conn_factory):
        result = gate.evaluate("reflex_b", 0.8, "output text here", generation=2)
    assert result["thresholds_used"]["adaptive"] is True
