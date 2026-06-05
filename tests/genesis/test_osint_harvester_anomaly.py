# CUI // SP-CTI
"""Unit tests for osint_harvester adaptive anomaly detection.

Covers:
- _get_rolling_stats: mean, std_dev, sample_size from DB rows
- _compute_zscore: correct Z-score and None when std_dev == 0
- _zscore_check fallback alias (via _detect_anomalies with no_baseline)
- _detect_anomalies end-to-end: no_baseline, below_min, LLM path, fallback path
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.genesis.reflexes.strategos.osint_harvester import (
    _compute_zscore,
    _detect_anomalies,
    _get_rolling_stats,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_audit_rows(counts: List[int]):
    """Return mock rows as list-of-dicts for get_connection().execute().fetchall()."""
    return [{"signals_harvested": c} for c in counts]


def _mock_conn(rows):
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = rows
    return conn


def _ad_config(extra: Dict[str, Any] = None) -> Dict[str, Any]:
    cfg: Dict[str, Any] = {
        "enabled": True,
        "model": "claude-haiku-4-5-20251001",
        "zscore_threshold": 2.5,
        "spike_threshold": 3.0,
        "min_signals_for_detection": 5,
        "baseline_window": 10,
        "sample_size": 10,
        "prompt_titles": 5,
        "title_truncate_len": 80,
    }
    if extra:
        cfg.update(extra)
    return {"osint": {"anomaly_detection": cfg}}


# ── _get_rolling_stats ────────────────────────────────────────────────────────

class TestGetRollingStats:
    def test_empty_rows_returns_none(self):
        with patch("tools.genesis.reflexes.strategos.osint_harvester.get_connection") as mock_gc, \
             patch("tools.genesis.reflexes.strategos.osint_harvester.is_pg", return_value=False):
            mock_gc.return_value = _mock_conn([])
            mean, std, n = _get_rolling_stats(10)
        assert mean is None and std is None and n == 0

    def test_single_row_std_is_zero(self):
        with patch("tools.genesis.reflexes.strategos.osint_harvester.get_connection") as mock_gc, \
             patch("tools.genesis.reflexes.strategos.osint_harvester.is_pg", return_value=False):
            mock_gc.return_value = _mock_conn(_make_audit_rows([42]))
            mean, std, n = _get_rolling_stats(10)
        assert mean == 42.0
        assert std == 0.0
        assert n == 1

    def test_multiple_rows_correct_stats(self):
        counts = [10, 20, 30, 40, 50]  # mean=30, sample std ≈ 15.811
        with patch("tools.genesis.reflexes.strategos.osint_harvester.get_connection") as mock_gc, \
             patch("tools.genesis.reflexes.strategos.osint_harvester.is_pg", return_value=False):
            mock_gc.return_value = _mock_conn(_make_audit_rows(counts))
            mean, std, n = _get_rolling_stats(10)
        assert mean == pytest.approx(30.0)
        assert std == pytest.approx(15.811, rel=1e-3)
        assert n == 5

    def test_identical_counts_std_is_zero(self):
        counts = [100, 100, 100, 100]
        with patch("tools.genesis.reflexes.strategos.osint_harvester.get_connection") as mock_gc, \
             patch("tools.genesis.reflexes.strategos.osint_harvester.is_pg", return_value=False):
            mock_gc.return_value = _mock_conn(_make_audit_rows(counts))
            mean, std, n = _get_rolling_stats(10)
        assert mean == 100.0
        assert std == 0.0
        assert n == 4

    def test_db_exception_returns_none(self):
        with patch("tools.genesis.reflexes.strategos.osint_harvester.get_connection", side_effect=Exception("db down")):
            mean, std, n = _get_rolling_stats(10)
        assert mean is None and std is None and n == 0


# ── _compute_zscore ───────────────────────────────────────────────────────────

class TestComputeZscore:
    def test_normal_zscore(self):
        # mean=30, std=10 → harvested=50 → z=2.0
        z = _compute_zscore(50, 30.0, 10.0)
        assert z == pytest.approx(2.0)

    def test_below_mean_negative_zscore(self):
        z = _compute_zscore(10, 30.0, 10.0)
        assert z == pytest.approx(-2.0)

    def test_std_zero_returns_none(self):
        assert _compute_zscore(100, 100.0, 0.0) is None

    def test_zscore_rounds_to_two_decimals(self):
        z = _compute_zscore(33, 30.0, 10.0)
        assert z == 0.30


# ── _detect_anomalies ─────────────────────────────────────────────────────────

class TestDetectAnomalies:
    def _mock_stats(self, mean, std, n):
        return patch(
            "tools.genesis.reflexes.strategos.osint_harvester._get_rolling_stats",
            return_value=(mean, std, n),
        )

    def _mock_titles(self, titles=None):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            {"title": t} for t in (titles or [])
        ]
        return patch(
            "tools.genesis.reflexes.strategos.osint_harvester.get_connection",
            return_value=conn,
        )

    def test_disabled_returns_early(self):
        cfg = {"osint": {"anomaly_detection": {"enabled": False}}}
        result = _detect_anomalies(cfg, 100, "internet")
        assert result == {"enabled": False}

    def test_below_min_signals_skips(self):
        result = _detect_anomalies(_ad_config(), 3, "internet")
        assert result["skipped"] == "below_min_signals"
        assert result["harvested"] == 3

    def test_no_baseline_skips(self):
        with self._mock_stats(None, None, 0):
            result = _detect_anomalies(_ad_config(), 50, "internet")
        assert result["skipped"] == "no_baseline"

    def test_result_includes_statistical_fields(self):
        """Result dict always carries z_score, baseline_std, sample_size."""
        fake_llm_response = json.dumps({
            "verdict": "normal",
            "is_anomaly": False,
            "reason": "within normal range",
        })
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=fake_llm_response)]

        with self._mock_stats(30.0, 10.0, 8), \
             self._mock_titles(["Title A", "Title B"]), \
             patch("anthropic.Anthropic") as mock_anthropic:
            mock_anthropic.return_value.messages.create.return_value = mock_msg
            result = _detect_anomalies(_ad_config(), 50, "internet")

        assert result["baseline_avg"] == 30.0
        assert result["baseline_std"] == 10.0
        assert result["sample_size"] == 8
        assert result["z_score"] == pytest.approx(2.0)
        assert result["verdict"] == "normal"

    def test_fallback_uses_zscore_when_llm_fails(self):
        """When LLM raises, fallback uses Z-score gate (not fixed ratio)."""
        with self._mock_stats(30.0, 5.0, 5), \
             self._mock_titles([]), \
             patch("anthropic.Anthropic", side_effect=ImportError("no anthropic")):
            # harvested=50 → z=(50-30)/5=4.0 > zscore_threshold=2.5 → anomaly
            result = _detect_anomalies(_ad_config(), 50, "file_inbox")

        assert result["is_anomaly"] is True
        assert result["verdict"] == "spike_detected"
        assert result["z_score"] == pytest.approx(4.0)

    def test_fallback_normal_when_zscore_below_threshold(self):
        with self._mock_stats(30.0, 5.0, 5), \
             self._mock_titles([]), \
             patch("anthropic.Anthropic", side_effect=ImportError("no anthropic")):
            # harvested=35 → z=(35-30)/5=1.0 < 2.5 → normal
            result = _detect_anomalies(_ad_config(), 35, "file_inbox")

        assert result["is_anomaly"] is False
        assert result["verdict"] == "normal"

    def test_fallback_uses_ratio_when_std_is_zero(self):
        """When std_dev==0, fallback uses spike_threshold ratio."""
        with self._mock_stats(20.0, 0.0, 3), \
             self._mock_titles([]), \
             patch("anthropic.Anthropic", side_effect=ImportError("no anthropic")):
            # z_score is None (std==0); mean=20, spike_threshold=3.0 → 70 > 60 → anomaly
            result = _detect_anomalies(_ad_config(), 70, "gitlab")

        assert result["is_anomaly"] is True
        assert result["z_score"] is None

    def test_llm_verdict_overrides_statistical_spike(self):
        """LLM verdict='normal' should set is_anomaly=False even on a statistical spike."""
        fake_llm_response = json.dumps({
            "verdict": "normal",
            "is_anomaly": False,
            "reason": "expected surge due to breaking news",
        })
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=fake_llm_response)]

        with self._mock_stats(10.0, 2.0, 10), \
             self._mock_titles(["Breaking: Event"]), \
             patch("anthropic.Anthropic") as mock_anthropic:
            mock_anthropic.return_value.messages.create.return_value = mock_msg
            # z = (100-10)/2 = 45 — huge spike, but LLM says normal
            result = _detect_anomalies(_ad_config(), 100, "internet")

        assert result["verdict"] == "normal"
        assert result["is_anomaly"] is False
        assert result["reason"] == "expected surge due to breaking news"
