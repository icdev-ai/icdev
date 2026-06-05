# CUI // SP-CTI
"""Tests for genesis publish reflex anomaly detection (aiify-rm-ff651-phase-5335).

Covers _compute_quality_threshold() — the adaptive WriteGuard score floor that
tracks the historical readability distribution rather than staying fixed at a
hardcoded value.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _config(enabled=True, floor=80, lookback=30, min_history=5,
            z_score_floor=-1.5, absolute_floor=60):
    return {
        "writeguard_min_score": floor,
        "anomaly_detection": {
            "enabled": enabled,
            "lookback_days": lookback,
            "min_history": min_history,
            "z_score_floor": z_score_floor,
            "absolute_floor": absolute_floor,
        },
    }


def _make_conn(scores):
    """Return a mock DB connection whose fetchall() yields the given scores."""
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = [(s,) for s in scores]
    return conn


# ---------------------------------------------------------------------------
# anomaly detection disabled  →  static floor
# ---------------------------------------------------------------------------

class TestComputeQualityThresholdDisabled:
    def test_returns_static_floor_when_disabled(self):
        from tools.genesis.reflexes.publish import _compute_quality_threshold
        cfg = _config(enabled=False, floor=80)
        assert _compute_quality_threshold(cfg) == 80.0

    def test_returns_custom_floor_value_when_disabled(self):
        from tools.genesis.reflexes.publish import _compute_quality_threshold
        cfg = _config(enabled=False, floor=65)
        assert _compute_quality_threshold(cfg) == 65.0

    def test_returns_floor_when_anomaly_detection_key_missing(self):
        from tools.genesis.reflexes.publish import _compute_quality_threshold
        cfg = {"writeguard_min_score": 75}  # no anomaly_detection key at all
        assert _compute_quality_threshold(cfg) == 75.0


# ---------------------------------------------------------------------------
# anomaly detection enabled  —  insufficient history
# ---------------------------------------------------------------------------

class TestComputeQualityThresholdInsufficientHistory:
    def test_returns_floor_when_no_rows(self):
        from tools.genesis.reflexes.publish import _compute_quality_threshold
        cfg = _config(enabled=True, floor=80, min_history=5)
        with patch("tools.genesis.reflexes.publish.get_connection",
                   return_value=_make_conn([])):
            result = _compute_quality_threshold(cfg)
        assert result == 80.0

    def test_returns_floor_when_below_min_history(self):
        from tools.genesis.reflexes.publish import _compute_quality_threshold
        cfg = _config(enabled=True, floor=80, min_history=5)
        with patch("tools.genesis.reflexes.publish.get_connection",
                   return_value=_make_conn([85.0, 90.0, 78.0])):  # only 3, need 5
            result = _compute_quality_threshold(cfg)
        assert result == 80.0

    def test_returns_floor_on_db_exception(self):
        from tools.genesis.reflexes.publish import _compute_quality_threshold
        cfg = _config(enabled=True, floor=80)
        conn = MagicMock()
        conn.execute.side_effect = Exception("DB unavailable")
        with patch("tools.genesis.reflexes.publish.get_connection",
                   return_value=conn):
            result = _compute_quality_threshold(cfg)
        assert result == 80.0


# ---------------------------------------------------------------------------
# anomaly detection enabled  —  sufficient history
# ---------------------------------------------------------------------------

class TestComputeQualityThresholdDynamic:
    def test_dynamic_threshold_below_static_floor(self):
        """With high-variance history, dynamic threshold may differ from floor."""
        from tools.genesis.reflexes.publish import _compute_quality_threshold
        # mean=80, std≈10 → dynamic = 80 + (-1.5 * 10) = 65 → clamped to absolute_floor=60
        scores = [70.0, 75.0, 80.0, 85.0, 90.0, 95.0]
        cfg = _config(enabled=True, floor=80, min_history=5,
                      z_score_floor=-1.5, absolute_floor=60)
        with patch("tools.genesis.reflexes.publish.get_connection",
                   return_value=_make_conn(scores)):
            result = _compute_quality_threshold(cfg)
        # dynamic = mean + z*std; must be >= absolute_floor
        mean = sum(scores) / len(scores)
        variance = sum((s - mean) ** 2 for s in scores) / len(scores)
        std = variance ** 0.5
        expected = max(60.0, mean + (-1.5) * std)
        assert abs(result - expected) < 0.01

    def test_respects_absolute_floor(self):
        """Even when dynamic is very low, absolute_floor is never breached."""
        from tools.genesis.reflexes.publish import _compute_quality_threshold
        # High std → dynamic would go far below absolute_floor
        scores = [10.0, 20.0, 90.0, 95.0, 100.0, 100.0]
        cfg = _config(enabled=True, floor=80, min_history=5,
                      z_score_floor=-2.0, absolute_floor=55)
        with patch("tools.genesis.reflexes.publish.get_connection",
                   return_value=_make_conn(scores)):
            result = _compute_quality_threshold(cfg)
        assert result >= 55.0

    def test_uniform_scores_returns_floor(self):
        """Zero std → cannot compute dynamic, fall back to floor."""
        from tools.genesis.reflexes.publish import _compute_quality_threshold
        scores = [80.0, 80.0, 80.0, 80.0, 80.0]
        cfg = _config(enabled=True, floor=80, min_history=5)
        with patch("tools.genesis.reflexes.publish.get_connection",
                   return_value=_make_conn(scores)):
            result = _compute_quality_threshold(cfg)
        assert result == 80.0

    def test_threshold_is_float(self):
        from tools.genesis.reflexes.publish import _compute_quality_threshold
        scores = [70.0, 80.0, 90.0, 85.0, 75.0, 88.0]
        cfg = _config(enabled=True, floor=80, min_history=5)
        with patch("tools.genesis.reflexes.publish.get_connection",
                   return_value=_make_conn(scores)):
            result = _compute_quality_threshold(cfg)
        assert isinstance(result, float)

    def test_exact_min_history_activates_dynamic(self):
        """Exactly min_history records should enable the dynamic path."""
        from tools.genesis.reflexes.publish import _compute_quality_threshold
        # 5 rows, min_history=5 → should compute dynamic, not return floor
        scores = [70.0, 75.0, 80.0, 85.0, 90.0]
        cfg = _config(enabled=True, floor=80, min_history=5,
                      z_score_floor=-1.0, absolute_floor=60)
        with patch("tools.genesis.reflexes.publish.get_connection",
                   return_value=_make_conn(scores)):
            result = _compute_quality_threshold(cfg)
        # With z=-1.0 and std≈7.07, dynamic ≈ 80 - 7.07 = 72.93
        assert result != 80.0  # dynamic path was taken
        assert result >= 60.0  # absolute floor honoured

    def test_z_score_floor_zero_returns_mean(self):
        """z_score_floor=0 → threshold equals the mean."""
        from tools.genesis.reflexes.publish import _compute_quality_threshold
        scores = [70.0, 80.0, 90.0, 85.0, 75.0, 80.0]
        mean = sum(scores) / len(scores)
        cfg = _config(enabled=True, floor=80, min_history=5,
                      z_score_floor=0.0, absolute_floor=60)
        with patch("tools.genesis.reflexes.publish.get_connection",
                   return_value=_make_conn(scores)):
            result = _compute_quality_threshold(cfg)
        assert abs(result - mean) < 0.01


# ---------------------------------------------------------------------------
# config key coverage
# ---------------------------------------------------------------------------

class TestConfigKeysCoverage:
    def test_default_writeguard_min_score_used_when_missing(self):
        """If writeguard_min_score absent, falls back to 80."""
        from tools.genesis.reflexes.publish import _compute_quality_threshold
        # no writeguard_min_score key → default 80
        cfg = {"anomaly_detection": {"enabled": False}}
        result = _compute_quality_threshold(cfg)
        assert result == 80.0

    def test_min_history_config_respected(self):
        """Setting min_history=2 should activate dynamic with only 2 rows."""
        from tools.genesis.reflexes.publish import _compute_quality_threshold
        scores = [70.0, 90.0]  # only 2 rows, but min_history=2
        cfg = _config(enabled=True, floor=80, min_history=2,
                      z_score_floor=-1.0, absolute_floor=60)
        with patch("tools.genesis.reflexes.publish.get_connection",
                   return_value=_make_conn(scores)):
            result = _compute_quality_threshold(cfg)
        # std = 10, dynamic = 80 - 10 = 70 → above absolute_floor
        assert result != 80.0  # dynamic path taken

    def test_custom_lookback_days_passed_to_query(self):
        """lookback_days is forwarded to the SQL query parameter."""
        from tools.genesis.reflexes.publish import _compute_quality_threshold
        cfg = _config(enabled=True, floor=80, min_history=5, lookback=14)
        conn = _make_conn([80.0] * 3)  # < min_history → floor returned
        with patch("tools.genesis.reflexes.publish.get_connection",
                   return_value=conn):
            _compute_quality_threshold(cfg)
        # The execute call should include "-14 days"
        call_args = conn.execute.call_args
        assert "-14 days" in call_args[0][1][0]
