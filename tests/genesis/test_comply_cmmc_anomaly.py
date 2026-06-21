# CUI // SP-CTI
"""Tests for the SPRS anomaly-detection helper + threshold wiring in comply_cmmc.py.

Covers the AI-ify modernization that replaced the hardcoded `cmmc_level < 2` and
`sprs_score < 110` thresholds with config-driven, adaptive anomaly detection.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.proposal_genesis.reflexes.comply_cmmc import (
    _check_teaming_cmmc,
    _compute_sprs_threshold,
    _MIN_CMMC_LEVEL,
    _MIN_SPRS_SCORE,
    _OPP_PROCESS_LIMIT,
)


def _mock_conn_single(row_dict):
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = row_dict
    return conn


# ─────────────────────────────────────────────────────────────────
# _compute_sprs_threshold
# ─────────────────────────────────────────────────────────────────

class TestComputeSprsThreshold:

    def test_disabled_returns_fallback(self):
        cfg = {"enabled": False, "fallback_sprs_threshold": 95}
        assert _compute_sprs_threshold(cfg) == 95.0

    def test_insufficient_history_returns_fallback(self):
        cfg = {"enabled": True, "min_samples": 15, "sigma_multiplier": 1.0,
               "fallback_sprs_threshold": 110,
               "adaptive_bounds": {"sprs_floor": 70, "sprs_ceil": 110}}
        with patch("tools.proposal_genesis.reflexes.comply_cmmc.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn_single({"mean_s": 100.0, "var_s": 25.0, "n": 3})
            result = _compute_sprs_threshold(cfg)
        assert result == 110.0  # fallback — not enough history

    def test_sufficient_history_computes_lower_control_limit(self):
        cfg = {"enabled": True, "min_samples": 3, "sigma_multiplier": 1.0,
               "fallback_sprs_threshold": 110,
               "adaptive_bounds": {"sprs_floor": 70, "sprs_ceil": 110}}
        # mean=100, var=100 → std=10 → threshold = 100 - 1*10 = 90
        with patch("tools.proposal_genesis.reflexes.comply_cmmc.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn_single({"mean_s": 100.0, "var_s": 100.0, "n": 20})
            result = _compute_sprs_threshold(cfg)
        assert result == pytest.approx(90.0, abs=0.1)

    def test_ceil_respected(self):
        # Zero variance → mean - 0 = 105 but clamp ceil keeps it <= 110.
        cfg = {"enabled": True, "min_samples": 3, "sigma_multiplier": 1.0,
               "fallback_sprs_threshold": 110,
               "adaptive_bounds": {"sprs_floor": 70, "sprs_ceil": 110}}
        with patch("tools.proposal_genesis.reflexes.comply_cmmc.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn_single({"mean_s": 130.0, "var_s": 0.0, "n": 20})
            result = _compute_sprs_threshold(cfg)
        assert result <= 110.0

    def test_floor_respected(self):
        # Huge sigma drives the limit far below the floor → clamped up to floor.
        cfg = {"enabled": True, "min_samples": 3, "sigma_multiplier": 10.0,
               "fallback_sprs_threshold": 110,
               "adaptive_bounds": {"sprs_floor": 70, "sprs_ceil": 110}}
        with patch("tools.proposal_genesis.reflexes.comply_cmmc.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn_single({"mean_s": 90.0, "var_s": 100.0, "n": 20})
            result = _compute_sprs_threshold(cfg)
        assert result >= 70.0

    def test_db_error_returns_fallback(self):
        cfg = {"enabled": True, "min_samples": 5, "fallback_sprs_threshold": 110}
        with patch("tools.proposal_genesis.reflexes.comply_cmmc.get_connection",
                   side_effect=Exception("DB error")):
            result = _compute_sprs_threshold(cfg)
        assert result == 110.0

    def test_none_cfg_uses_defaults(self):
        with patch("tools.proposal_genesis.reflexes.comply_cmmc.get_connection",
                   side_effect=Exception("no DB")):
            result = _compute_sprs_threshold(None)
        assert result == _MIN_SPRS_SCORE


# ─────────────────────────────────────────────────────────────────
# _check_teaming_cmmc — threshold parameterization
# ─────────────────────────────────────────────────────────────────

class TestCheckTeamingThresholds:

    def _mock_partners(self, partners):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = partners
        return conn

    def test_default_thresholds_flag_imperfect_sprs(self):
        # Default sprs_threshold = 110, so SPRS 105 is flagged.
        partners = [{"id": "p1", "partner_name": "Acme", "role": "sub",
                     "cmmc_level": 2, "sprs_score": 105}]
        with patch("tools.proposal_genesis.reflexes.comply_cmmc.get_connection") as mock_gc:
            mock_gc.return_value = self._mock_partners(partners)
            result = _check_teaming_cmmc("opp-1")
        assert result["non_compliant_count"] == 1

    def test_adaptive_threshold_passes_above_limit(self):
        # With an adaptive threshold of 90, SPRS 105 is no longer flagged.
        partners = [{"id": "p1", "partner_name": "Acme", "role": "sub",
                     "cmmc_level": 2, "sprs_score": 105}]
        with patch("tools.proposal_genesis.reflexes.comply_cmmc.get_connection") as mock_gc:
            mock_gc.return_value = self._mock_partners(partners)
            result = _check_teaming_cmmc("opp-1", min_cmmc_level=2, sprs_threshold=90.0)
        assert result["non_compliant_count"] == 0

    def test_cmmc_level_below_floor_flagged(self):
        partners = [{"id": "p1", "partner_name": "Acme", "role": "sub",
                     "cmmc_level": 1, "sprs_score": 110}]
        with patch("tools.proposal_genesis.reflexes.comply_cmmc.get_connection") as mock_gc:
            mock_gc.return_value = self._mock_partners(partners)
            result = _check_teaming_cmmc("opp-1", min_cmmc_level=2, sprs_threshold=90.0)
        assert result["non_compliant_count"] == 1
        assert any("CMMC level" in i for i in result["non_compliant"][0]["issues"])

    def test_missing_data_flagged(self):
        partners = [{"id": "p1", "partner_name": "Acme", "role": "sub",
                     "cmmc_level": 0, "sprs_score": None}]
        with patch("tools.proposal_genesis.reflexes.comply_cmmc.get_connection") as mock_gc:
            mock_gc.return_value = self._mock_partners(partners)
            result = _check_teaming_cmmc("opp-1", min_cmmc_level=2, sprs_threshold=90.0)
        assert result["non_compliant_count"] == 1


class TestComplyCmmcConstants:
    def test_constants_sane(self):
        assert _MIN_CMMC_LEVEL >= 1
        assert 0 < _MIN_SPRS_SCORE <= 110
        assert _OPP_PROCESS_LIMIT > 0
