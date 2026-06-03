"""Tests for anomaly-detection helpers in genesis/reflexes/learn.py."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.genesis.reflexes.learn import (
    _compute_fine_tune_threshold,
    _generate_pairs_from_source,
)


# ---------------------------------------------------------------------------
# _compute_fine_tune_threshold
# ---------------------------------------------------------------------------


def _make_row(value):
    """Return a sqlite3-style tuple row for a metric_value."""
    return (value,)


def _mock_conn(metric_values):
    conn = MagicMock()
    rows = [_make_row(v) for v in metric_values]
    conn.execute.return_value.fetchall.return_value = rows
    return conn


class TestComputeFineTuneThreshold:
    def test_disabled_returns_fallback(self):
        cfg = {"enabled": False, "fallback_min_pairs": 75}
        assert _compute_fine_tune_threshold(cfg) == 75.0

    def test_insufficient_history_returns_fallback(self):
        cfg = {
            "enabled": True,
            "min_samples": 5,
            "fallback_min_pairs": 100,
            "sigma_multiplier": 1.0,
            "adaptive_bounds": {"min_pairs_floor": 20},
        }
        with patch("tools.genesis.reflexes.learn.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn([10, 12, 15])  # only 3 rows < min_samples=5
            result = _compute_fine_tune_threshold(cfg)
        assert result == 100.0

    def test_sufficient_history_uses_adaptive_threshold(self):
        cfg = {
            "enabled": True,
            "min_samples": 3,
            "fallback_min_pairs": 100,
            "sigma_multiplier": 1.0,
            "adaptive_bounds": {"min_pairs_floor": 20},
        }
        # mean=50, std=0 → adaptive=50+1*0=50, threshold=max(20,50)=50
        with patch("tools.genesis.reflexes.learn.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn([50, 50, 50, 50, 50])
            result = _compute_fine_tune_threshold(cfg)
        assert result == 50.0

    def test_floor_enforced_when_adaptive_below_floor(self):
        cfg = {
            "enabled": True,
            "min_samples": 3,
            "fallback_min_pairs": 100,
            "sigma_multiplier": 0.0,  # threshold = mean + 0*std = mean
            "adaptive_bounds": {"min_pairs_floor": 30},
        }
        # mean=5, adaptive=5, floor=30 → result=30
        with patch("tools.genesis.reflexes.learn.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn([5, 5, 5, 5, 5])
            result = _compute_fine_tune_threshold(cfg)
        assert result == 30.0

    def test_db_error_returns_fallback(self):
        cfg = {
            "enabled": True,
            "min_samples": 5,
            "fallback_min_pairs": 50,
            "sigma_multiplier": 1.0,
            "adaptive_bounds": {"min_pairs_floor": 10},
        }
        with patch("tools.genesis.reflexes.learn.get_connection", side_effect=Exception("db fail")):
            result = _compute_fine_tune_threshold(cfg)
        assert result == 50.0

    def test_empty_config_uses_defaults(self):
        with patch("tools.genesis.reflexes.learn.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn([])
            result = _compute_fine_tune_threshold({})
        assert result == 100.0  # fallback_min_pairs default

    def test_sigma_multiplier_scales_threshold(self):
        cfg = {
            "enabled": True,
            "min_samples": 3,
            "fallback_min_pairs": 100,
            "sigma_multiplier": 2.0,
            "adaptive_bounds": {"min_pairs_floor": 20},
        }
        # mean=50, std=10 → adaptive = 50 + 2*10 = 70
        values = [40, 50, 60] * 4  # std ≈ 8.2
        with patch("tools.genesis.reflexes.learn.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn(values)
            result_2x = _compute_fine_tune_threshold(cfg)

        cfg_1x = dict(cfg, sigma_multiplier=1.0)
        with patch("tools.genesis.reflexes.learn.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn(values)
            result_1x = _compute_fine_tune_threshold(cfg_1x)

        assert result_2x > result_1x

    def test_none_metric_values_are_skipped(self):
        cfg = {
            "enabled": True,
            "min_samples": 3,
            "fallback_min_pairs": 100,
            "sigma_multiplier": 1.0,
            "adaptive_bounds": {"min_pairs_floor": 20},
        }
        with patch("tools.genesis.reflexes.learn.get_connection") as mock_gc:
            conn = MagicMock()
            conn.execute.return_value.fetchall.return_value = [
                (None,), (50,), (50,), (50,), (50,)
            ]
            mock_gc.return_value = conn
            # 4 non-None values >= min_samples=3 → adaptive path
            result = _compute_fine_tune_threshold(cfg)
        assert result >= 20.0  # at least the floor


# ---------------------------------------------------------------------------
# _generate_pairs_from_source — pass-rate params wired from config
# ---------------------------------------------------------------------------


class TestGeneratePairsFromSource:
    def _run_and_capture_cmd(self, **kwargs):
        """Call _generate_pairs_from_source and capture the subprocess command."""
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            m = MagicMock()
            m.stdout = '{"pairs": []}'
            m.returncode = 0
            return m

        with patch("tools.genesis.reflexes.learn.subprocess.run", side_effect=fake_run):
            _generate_pairs_from_source("research_signals", **kwargs)
        return captured.get("cmd", [])

    def test_default_pass_rates_in_command(self):
        cmd = self._run_and_capture_cmd()
        assert "--min-pass-rate" in cmd
        idx = cmd.index("--min-pass-rate")
        assert cmd[idx + 1] == "0.2"
        assert cmd[cmd.index("--max-pass-rate") + 1] == "0.8"

    def test_custom_pass_rates_in_command(self):
        cmd = self._run_and_capture_cmd(min_pass_rate=0.3, max_pass_rate=0.9)
        assert cmd[cmd.index("--min-pass-rate") + 1] == "0.3"
        assert cmd[cmd.index("--max-pass-rate") + 1] == "0.9"

    def test_custom_limit_in_command(self):
        cmd = self._run_and_capture_cmd(limit=42)
        assert cmd[cmd.index("--num-attempts") + 1] == "42"

    def test_subprocess_error_returns_empty_list(self):
        with patch("tools.genesis.reflexes.learn.subprocess.run", side_effect=Exception("fail")):
            result = _generate_pairs_from_source("research_signals")
        assert result == []

    def test_invalid_json_returns_empty_list(self):
        m = MagicMock()
        m.stdout = "not json"
        with patch("tools.genesis.reflexes.learn.subprocess.run", return_value=m):
            result = _generate_pairs_from_source("research_signals")
        assert result == []
