# CUI // SP-CTI
"""Tests for adaptive cooldown via anomaly detection in dat_refresh.

Verifies that COOLDOWN_HOURS is no longer a static constant but is derived
from statistical analysis of recent DTI snapshot history.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.genesis.reflexes.dat_refresh import (
    COOLDOWN_HOURS_DEFAULT,
    _compute_adaptive_cooldown,
    _load_anomaly_config,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def default_anomaly_cfg():
    return {
        "enabled": True,
        "min_samples": 4,
        "z_score_threshold": 2.0,
        "cooldown_hours_min": 1,
        "cooldown_hours_max": 12,
        "fallback_to_static": True,
    }


def _make_snapshot_rows(scores):
    """Return list of (dti_score, computed_at) tuples from a score list."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    rows = []
    for i, score in enumerate(reversed(scores)):
        ts = (now - timedelta(hours=(len(scores) - i) * 6)).isoformat()
        rows.append((score, ts))
    return rows


# ---------------------------------------------------------------------------
# _load_anomaly_config
# ---------------------------------------------------------------------------

class TestLoadAnomalyConfig:
    def test_returns_dict_with_required_keys(self):
        cfg = _load_anomaly_config()
        assert "enabled" in cfg
        assert "min_samples" in cfg
        assert "z_score_threshold" in cfg
        assert "cooldown_hours_min" in cfg
        assert "cooldown_hours_max" in cfg
        assert "fallback_to_static" in cfg

    def test_defaults_are_sensible(self):
        cfg = _load_anomaly_config()
        assert cfg["min_samples"] >= 2
        assert cfg["z_score_threshold"] > 0
        assert cfg["cooldown_hours_min"] < COOLDOWN_HOURS_DEFAULT
        assert cfg["cooldown_hours_max"] >= COOLDOWN_HOURS_DEFAULT


# ---------------------------------------------------------------------------
# _compute_adaptive_cooldown
# ---------------------------------------------------------------------------

class TestComputeAdaptiveCooldown:

    # --- no data / too little data → fallback ---

    def test_no_snapshots_returns_static_cooldown(self, default_anomaly_cfg):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        result = _compute_adaptive_cooldown(conn, cfg=default_anomaly_cfg)
        assert result == COOLDOWN_HOURS_DEFAULT

    def test_insufficient_samples_returns_static_cooldown(self, default_anomaly_cfg):
        # Only 2 rows, min_samples=4
        rows = _make_snapshot_rows([50.0, 52.0])
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = rows
        result = _compute_adaptive_cooldown(conn, cfg=default_anomaly_cfg)
        assert result == COOLDOWN_HOURS_DEFAULT

    # --- stable signal → long cooldown ---

    def test_stable_dti_returns_max_cooldown(self, default_anomaly_cfg):
        # All scores nearly identical → z-score near 0 → no anomaly
        rows = _make_snapshot_rows([50.0, 50.1, 49.9, 50.2, 50.0, 50.1])
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = rows
        result = _compute_adaptive_cooldown(conn, cfg=default_anomaly_cfg)
        assert result == default_anomaly_cfg["cooldown_hours_max"]

    # --- anomalous spike → short cooldown ---

    def test_anomalous_dti_spike_returns_min_cooldown(self, default_anomaly_cfg):
        # Historical mean ~50, then sudden jump to 90 (high z-score)
        rows = _make_snapshot_rows([50.0, 50.5, 49.8, 51.0, 50.2, 90.0])
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = rows
        result = _compute_adaptive_cooldown(conn, cfg=default_anomaly_cfg)
        assert result == default_anomaly_cfg["cooldown_hours_min"]

    def test_anomalous_dti_drop_returns_min_cooldown(self, default_anomaly_cfg):
        # Historical mean ~70, then sudden drop to 10
        rows = _make_snapshot_rows([70.0, 71.0, 69.5, 70.3, 70.8, 10.0])
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = rows
        result = _compute_adaptive_cooldown(conn, cfg=default_anomaly_cfg)
        assert result == default_anomaly_cfg["cooldown_hours_min"]

    # --- disabled → always static ---

    def test_disabled_returns_static_cooldown(self, default_anomaly_cfg):
        default_anomaly_cfg["enabled"] = False
        rows = _make_snapshot_rows([50.0, 50.1, 49.9, 50.2, 50.0, 90.0])
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = rows
        result = _compute_adaptive_cooldown(conn, cfg=default_anomaly_cfg)
        assert result == COOLDOWN_HOURS_DEFAULT

    # --- zero variance edge case ---

    def test_zero_variance_does_not_divide_by_zero(self, default_anomaly_cfg):
        # All identical scores → std=0, should not raise ZeroDivisionError
        rows = _make_snapshot_rows([50.0, 50.0, 50.0, 50.0, 50.0])
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = rows
        result = _compute_adaptive_cooldown(conn, cfg=default_anomaly_cfg)
        # Stable (std=0, z-score=0) → max cooldown
        assert result == default_anomaly_cfg["cooldown_hours_max"]

    # --- fallback_to_static=False → still returns min/max not hardcoded ---

    def test_fallback_false_insufficient_still_uses_static(self, default_anomaly_cfg):
        default_anomaly_cfg["fallback_to_static"] = False
        rows = _make_snapshot_rows([50.0])
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = rows
        result = _compute_adaptive_cooldown(conn, cfg=default_anomaly_cfg)
        # With 1 sample < min_samples=4, fallback_to_static=False → still return static
        assert result == COOLDOWN_HOURS_DEFAULT


# ---------------------------------------------------------------------------
# Integration: COOLDOWN_HOURS_DEFAULT constant still present and correct
# ---------------------------------------------------------------------------

class TestBackwardsCompat:
    def test_cooldown_hours_default_is_6(self):
        assert COOLDOWN_HOURS_DEFAULT == 6

    def test_cooldown_hours_deprecated_alias(self):
        """COOLDOWN_HOURS kept as alias to avoid breaking external imports."""
        from tools.genesis.reflexes import dat_refresh
        assert hasattr(dat_refresh, "COOLDOWN_HOURS")
        assert dat_refresh.COOLDOWN_HOURS == COOLDOWN_HOURS_DEFAULT
