# CUI // SP-CTI
"""Tests for config-driven anomaly-detection threshold parameters in quality.py."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.genesis.reflexes.quality import (
    _compute_adaptive_thresholds,
    _compute_adaptive_gate_score_threshold,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _snap_rows(uqs_scores: list, findings: list | None = None):
    """Build mock DB rows shaped like quality_snapshots."""
    if findings is None:
        findings = [0] * len(uqs_scores)
    rows = []
    for uqs, f in zip(uqs_scores, findings):
        row = MagicMock()
        row.__getitem__ = lambda self, key, _u=uqs, _f=f: _u if key == "uqs_score" else _f
        rows.append(row)
    return rows


def _gate_rows(scores_per_snap: list):
    """Build mock DB rows with gate_results JSON."""
    import json
    rows = []
    for scores in scores_per_snap:
        gates = [{"score": s} for s in scores]
        row = MagicMock()
        row.__getitem__ = lambda self, key, _g=json.dumps(gates): _g
        rows.append(row)
    return rows


def _mock_conn_for_thresholds(uqs_scores: list, findings: list | None = None):
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = _snap_rows(uqs_scores, findings)
    return conn


def _mock_conn_for_gate_scores(scores_per_snap: list):
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = _gate_rows(scores_per_snap)
    return conn


BASE_CFG = {
    "uqs_regression_threshold": -5.0,
    "uqs_critical_threshold": -15.0,
    "findings_increase_threshold": 5,
    "anomaly_detection": {
        "enabled": True,
        "min_samples": 3,
        "sigma_multiplier": 1.0,
        "adaptive_bounds": {
            "uqs_regression_floor": -20.0,
            "findings_increase_floor": 1,
            "gate_score_floor": 50.0,
        },
    },
}


# ---------------------------------------------------------------------------
# Tests: _compute_adaptive_thresholds
# ---------------------------------------------------------------------------

class TestCriticalSigmaMultiplierFromConfig:
    def test_default_critical_sigma_is_two_times_sigma(self):
        """Without critical_sigma_multiplier in config, critical uses 2x sigma."""
        uqs = [80.0, 78.0, 79.0, 77.0, 76.0]
        conn = _mock_conn_for_thresholds(uqs)
        cfg = dict(BASE_CFG)
        with patch("tools.genesis.reflexes.quality._load_reflex_config", return_value=cfg):
            result = _compute_adaptive_thresholds(conn)
        # critical should be lower than regression (2x vs 1x sigma)
        assert result["uqs_critical_threshold"] <= result["uqs_regression_threshold"]

    def test_custom_critical_sigma_multiplier_honoured(self):
        """critical_sigma_multiplier=3.0 in config pushes critical further below regression."""
        uqs = [80.0, 78.0, 79.0, 77.0, 76.0]
        conn_3x = _mock_conn_for_thresholds(uqs)
        conn_2x = _mock_conn_for_thresholds(uqs)

        cfg_3x = dict(BASE_CFG)
        cfg_3x["anomaly_detection"] = dict(BASE_CFG["anomaly_detection"])
        cfg_3x["anomaly_detection"]["critical_sigma_multiplier"] = 3.0

        with patch("tools.genesis.reflexes.quality._load_reflex_config", return_value=cfg_3x):
            result_3x = _compute_adaptive_thresholds(conn_3x)

        with patch("tools.genesis.reflexes.quality._load_reflex_config", return_value=BASE_CFG):
            result_2x = _compute_adaptive_thresholds(conn_2x)

        assert result_3x["uqs_critical_threshold"] < result_2x["uqs_critical_threshold"]

    def test_adaptive_flag_set_when_enough_samples(self):
        """Returns adaptive=True when enough history exists."""
        uqs = [80.0, 78.0, 79.0, 77.0, 76.0]
        conn = _mock_conn_for_thresholds(uqs)
        with patch("tools.genesis.reflexes.quality._load_reflex_config", return_value=BASE_CFG):
            result = _compute_adaptive_thresholds(conn)
        assert result["adaptive"] is True


class TestCriticalFloorMultiplierFromConfig:
    def test_default_critical_floor_is_two_times_uqs_floor(self):
        """Without critical_floor_multiplier, critical floor = uqs_regression_floor * 2."""
        # Force adaptive threshold to be very negative so floor kicks in
        uqs = [10.0, 60.0, 10.0, 60.0, 10.0]  # high variance → deep critical
        conn = _mock_conn_for_thresholds(uqs)
        cfg = dict(BASE_CFG)
        floor = cfg["anomaly_detection"]["adaptive_bounds"]["uqs_regression_floor"]
        with patch("tools.genesis.reflexes.quality._load_reflex_config", return_value=cfg):
            result = _compute_adaptive_thresholds(conn)
        assert result["uqs_critical_threshold"] >= floor * 2

    def test_custom_critical_floor_multiplier_respected(self):
        """critical_floor_multiplier=3.0 lowers the critical floor (more permissive).

        uqs_regression_floor=-20, so:
          2x multiplier → floor = -40 → critical never below -40
          3x multiplier → floor = -60 → critical never below -60 (more permissive)
        High-variance data forces the floor to kick in, so 3x result < 2x result.
        """
        uqs = [10.0, 60.0, 10.0, 60.0, 10.0]
        conn_3x = _mock_conn_for_thresholds(uqs)
        conn_2x = _mock_conn_for_thresholds(uqs)

        cfg_3x = dict(BASE_CFG)
        cfg_3x["anomaly_detection"] = dict(BASE_CFG["anomaly_detection"])
        cfg_3x["anomaly_detection"]["critical_floor_multiplier"] = 3.0

        with patch("tools.genesis.reflexes.quality._load_reflex_config", return_value=cfg_3x):
            result_3x = _compute_adaptive_thresholds(conn_3x)

        with patch("tools.genesis.reflexes.quality._load_reflex_config", return_value=BASE_CFG):
            result_2x = _compute_adaptive_thresholds(conn_2x)

        # 3x floor = -60; 2x floor = -40 → 3x result is more negative
        assert result_3x["uqs_critical_threshold"] < result_2x["uqs_critical_threshold"]


class TestFetchBufferFromConfig:
    def test_fetch_buffer_controls_row_limit_in_adaptive_thresholds(self):
        """fetch_buffer=5 means the LIMIT passed to DB is min_samples + 5."""
        cfg = dict(BASE_CFG)
        cfg["anomaly_detection"] = dict(BASE_CFG["anomaly_detection"])
        cfg["anomaly_detection"]["fetch_buffer"] = 5
        min_s = cfg["anomaly_detection"]["min_samples"]  # 3

        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = _snap_rows(
            [80.0, 78.0, 79.0, 77.0, 76.0]
        )

        with patch("tools.genesis.reflexes.quality._load_reflex_config", return_value=cfg):
            _compute_adaptive_thresholds(conn)

        call_args = conn.execute.call_args
        limit = call_args[0][1][0] if call_args[0][1] else call_args[1].get("params", [None])[0]
        assert limit == min_s + 5

    def test_fetch_buffer_controls_row_limit_in_gate_score_threshold(self):
        """fetch_buffer=7 reflected in gate score threshold DB query LIMIT."""
        cfg = dict(BASE_CFG)
        cfg["anomaly_detection"] = dict(BASE_CFG["anomaly_detection"])
        cfg["anomaly_detection"]["fetch_buffer"] = 7
        min_s = cfg["anomaly_detection"]["min_samples"]

        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []

        with patch("tools.genesis.reflexes.quality._load_reflex_config", return_value=cfg):
            _compute_adaptive_gate_score_threshold(conn)

        call_args = conn.execute.call_args
        limit = call_args[0][1][0] if call_args[0][1] else call_args[1].get("params", [None])[0]
        assert limit == min_s + 7


class TestStaticFallback:
    def test_static_values_returned_when_adaptive_disabled(self):
        """With anomaly_detection.enabled=False, static thresholds are returned."""
        cfg = dict(BASE_CFG)
        cfg["anomaly_detection"] = {"enabled": False}
        conn = MagicMock()

        with patch("tools.genesis.reflexes.quality._load_reflex_config", return_value=cfg):
            result = _compute_adaptive_thresholds(conn)

        assert result["adaptive"] is False
        assert result["uqs_regression_threshold"] == -5.0
        assert result["uqs_critical_threshold"] == -15.0

    def test_static_gate_score_returned_when_adaptive_disabled(self):
        """Gate score returns static fallback when adaptive disabled."""
        cfg = dict(BASE_CFG)
        cfg["gate_score_threshold"] = 75.0
        cfg["anomaly_detection"] = {"enabled": False}
        conn = MagicMock()

        with patch("tools.genesis.reflexes.quality._load_reflex_config", return_value=cfg):
            result = _compute_adaptive_gate_score_threshold(conn)

        assert result == 75.0
