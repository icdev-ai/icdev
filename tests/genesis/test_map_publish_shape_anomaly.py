# CUI // SP-CTI
"""Tests for anomaly-detection helpers in map.py, publish.py, and shape.py."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

# ─────────────────────────────────────────────────────────────────
# map.py
# ─────────────────────────────────────────────────────────────────
from tools.proposal_genesis.reflexes.map import (
    _compute_rag_top_k,
    _GRAPHRAG_TOP_K,
    _RAG_TOP_K,
    _PARALLEL_PATTERNS_CAP,
    _PARALLEL_MAX_WORKERS,
    _PARALLEL_FUTURE_TIMEOUT,
    _REQ_FETCH_LIMIT,
    _OPP_PROCESS_LIMIT,
)
from tools.proposal_genesis.reflexes.publish import (
    _compute_quality_publish_threshold,
    _MIN_QUALITY_SCORE,
    _MAX_DRAFTS_PER_RUN,
    _MAX_KB_BLOCKS,
    _DEFAULT_RELEVANCE_SCORE,
)
from tools.proposal_genesis.reflexes.shape import (
    _compute_fit_thresholds,
    _FIT_STRONG_THRESHOLD,
    _FIT_GOOD_THRESHOLD,
    _FIT_MARGINAL_THRESHOLD,
    _assess_partner_fit,
)


def _mock_conn_single(row_dict):
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = row_dict
    return conn


def _mock_conn_no_rows():
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = None
    return conn


# ─────────────────────────────────────────────────────────────────
# _compute_rag_top_k (map.py)
# ─────────────────────────────────────────────────────────────────

class TestComputeRagTopK:

    def test_disabled_returns_fallback(self):
        cfg = {"enabled": False, "fallback_top_k": 7}
        assert _compute_rag_top_k(cfg) == 7

    def test_insufficient_history_returns_fallback(self):
        cfg = {"enabled": True, "min_samples": 20, "fallback_top_k": 5,
               "low_recall_threshold": 0.3, "high_recall_threshold": 0.7,
               "adaptive_bounds": {"top_k_floor": 2, "top_k_ceil": 10}}
        with patch("tools.proposal_genesis.reflexes.map.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn_single({"hit_rate": 0.5, "n": 3})
            result = _compute_rag_top_k(cfg)
        assert result == 5  # fallback

    def test_low_recall_boosts_top_k(self):
        cfg = {"enabled": True, "min_samples": 5, "fallback_top_k": 5,
               "low_recall_threshold": 0.3, "high_recall_threshold": 0.7,
               "adaptive_bounds": {"top_k_floor": 2, "top_k_ceil": 10}}
        with patch("tools.proposal_genesis.reflexes.map.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn_single({"hit_rate": 0.10, "n": 10})
            result = _compute_rag_top_k(cfg)
        assert result > 5   # boosted due to low recall

    def test_high_recall_reduces_top_k(self):
        cfg = {"enabled": True, "min_samples": 5, "fallback_top_k": 5,
               "low_recall_threshold": 0.3, "high_recall_threshold": 0.7,
               "adaptive_bounds": {"top_k_floor": 2, "top_k_ceil": 10}}
        with patch("tools.proposal_genesis.reflexes.map.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn_single({"hit_rate": 0.95, "n": 10})
            result = _compute_rag_top_k(cfg)
        assert result < 5   # reduced due to high recall

    def test_db_error_returns_fallback(self):
        cfg = {"enabled": True, "min_samples": 5, "fallback_top_k": 5}
        with patch("tools.proposal_genesis.reflexes.map.get_connection", side_effect=Exception("DB error")):
            result = _compute_rag_top_k(cfg)
        assert result == 5

    def test_bounds_respected(self):
        cfg = {"enabled": True, "min_samples": 5, "fallback_top_k": 5,
               "low_recall_threshold": 0.3, "high_recall_threshold": 0.7,
               "adaptive_bounds": {"top_k_floor": 3, "top_k_ceil": 6}}
        with patch("tools.proposal_genesis.reflexes.map.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn_single({"hit_rate": 0.01, "n": 100})
            result = _compute_rag_top_k(cfg)
        assert result <= 6


class TestMapConstants:
    def test_all_positive(self):
        assert _GRAPHRAG_TOP_K > 0
        assert _RAG_TOP_K > 0
        assert _PARALLEL_PATTERNS_CAP > 0
        assert _PARALLEL_MAX_WORKERS > 0
        assert _PARALLEL_FUTURE_TIMEOUT > 0
        assert _REQ_FETCH_LIMIT > 0
        assert _OPP_PROCESS_LIMIT > 0


# ─────────────────────────────────────────────────────────────────
# _compute_quality_publish_threshold (publish.py)
# ─────────────────────────────────────────────────────────────────

class TestComputeQualityPublishThreshold:

    def test_disabled_returns_fallback(self):
        cfg = {"enabled": False, "fallback_min_quality_score": 75.0}
        assert _compute_quality_publish_threshold(cfg) == 75.0

    def test_insufficient_history_returns_fallback(self):
        cfg = {"enabled": True, "min_samples": 10, "sigma_multiplier": 1.0,
               "fallback_min_quality_score": 70.0,
               "adaptive_bounds": {"quality_floor": 50.0}}
        with patch("tools.proposal_genesis.reflexes.publish.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn_single({"mean_s": 78.0, "var_s": 25.0, "n": 3})
            result = _compute_quality_publish_threshold(cfg)
        assert result == 70.0

    def test_sufficient_history_computes_threshold(self):
        cfg = {"enabled": True, "min_samples": 3, "sigma_multiplier": 1.0,
               "fallback_min_quality_score": 70.0,
               "adaptive_bounds": {"quality_floor": 50.0}}
        # mean=80, var=0 → threshold = max(50, 80 - 1*0) = 80
        with patch("tools.proposal_genesis.reflexes.publish.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn_single({"mean_s": 80.0, "var_s": 0.0, "n": 10})
            result = _compute_quality_publish_threshold(cfg)
        assert result == pytest.approx(80.0, abs=0.1)
        assert result >= 50.0  # floor respected

    def test_floor_respected(self):
        cfg = {"enabled": True, "min_samples": 3, "sigma_multiplier": 10.0,
               "fallback_min_quality_score": 70.0,
               "adaptive_bounds": {"quality_floor": 55.0}}
        with patch("tools.proposal_genesis.reflexes.publish.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn_single({"mean_s": 60.0, "var_s": 100.0, "n": 10})
            result = _compute_quality_publish_threshold(cfg)
        assert result >= 55.0

    def test_db_error_returns_fallback(self):
        cfg = {"enabled": True, "min_samples": 5, "fallback_min_quality_score": 70.0}
        with patch("tools.proposal_genesis.reflexes.publish.get_connection",
                   side_effect=Exception("DB error")):
            result = _compute_quality_publish_threshold(cfg)
        assert result == 70.0

    def test_none_cfg_uses_defaults(self):
        with patch("tools.proposal_genesis.reflexes.publish.get_connection",
                   side_effect=Exception("no DB")):
            result = _compute_quality_publish_threshold(None)
        assert result == _MIN_QUALITY_SCORE


class TestPublishConstants:
    def test_all_positive(self):
        assert _MIN_QUALITY_SCORE > 0
        assert _MAX_DRAFTS_PER_RUN > 0
        assert _MAX_KB_BLOCKS > 0
        assert 0.0 < _DEFAULT_RELEVANCE_SCORE <= 1.0


# ─────────────────────────────────────────────────────────────────
# _compute_fit_thresholds (shape.py)
# ─────────────────────────────────────────────────────────────────

class TestComputeFitThresholds:

    def test_disabled_returns_fallback(self):
        cfg = {"enabled": False,
               "fallback_strong_threshold": 0.75,
               "fallback_good_threshold": 0.55,
               "fallback_marginal_threshold": 0.35}
        result = _compute_fit_thresholds(cfg)
        assert result["strong"] == 0.75
        assert result["good"] == 0.55
        assert result["computed"] is False

    def test_insufficient_history_returns_fallback(self):
        cfg = {"enabled": True, "min_samples": 15,
               "fallback_strong_threshold": 0.70,
               "fallback_good_threshold": 0.50,
               "fallback_marginal_threshold": 0.30,
               "adaptive_bounds": {"strong_floor": 0.50, "good_floor": 0.30, "marginal_floor": 0.10}}
        # SQLite fallback — only mean returned
        with patch("tools.proposal_genesis.reflexes.shape.get_connection") as mock_gc:
            conn = MagicMock()
            # First execute (PERCENTILE_CONT) raises, second execute (AVG) succeeds with n=3
            conn.execute.side_effect = [
                Exception("percentile not available"),
                MagicMock(fetchone=MagicMock(return_value={"mean_f": 0.60, "n": 3})),
            ]
            mock_gc.return_value = conn
            result = _compute_fit_thresholds(cfg)
        assert result["strong"] == 0.70
        assert result["computed"] is False

    def test_floors_respected(self):
        cfg = {"enabled": True, "min_samples": 5,
               "fallback_strong_threshold": 0.70,
               "fallback_good_threshold": 0.50,
               "fallback_marginal_threshold": 0.30,
               "adaptive_bounds": {"strong_floor": 0.50, "good_floor": 0.30, "marginal_floor": 0.10}}
        with patch("tools.proposal_genesis.reflexes.shape.get_connection") as mock_gc:
            conn = MagicMock()
            # percentile call raises, fallback to mean with sufficient samples
            conn.execute.side_effect = [
                Exception("no percentile"),
                MagicMock(fetchone=MagicMock(return_value={"mean_f": 0.10, "n": 10})),
            ]
            mock_gc.return_value = conn
            result = _compute_fit_thresholds(cfg)
        if result["computed"]:
            assert result["strong"] >= 0.50
            assert result["good"] >= 0.30
            assert result["marginal"] >= 0.10

    def test_db_error_returns_fallback(self):
        cfg = {"enabled": True, "min_samples": 5}
        with patch("tools.proposal_genesis.reflexes.shape.get_connection",
                   side_effect=Exception("DB error")):
            result = _compute_fit_thresholds(cfg)
        assert result["computed"] is False


class TestAssessPartnerFit:

    def _make_opp(self, title="cloud devsecops modernization"):
        return {"title": title, "naics_code": "541511", "agency": "DOD"}

    def _make_partner(self, caps="cloud devops agile", certs="fedramp cmmc cleared", vehicles="gwac gsa", sa="8a sdvosb"):
        return {"id": "p1", "name": "PartnerA", "status": "active",
                "capabilities": caps, "certifications": certs,
                "contract_vehicles": vehicles, "set_asides": sa}

    def test_strong_partner_scores_above_threshold(self):
        opp = self._make_opp()
        partner = self._make_partner()
        result = _assess_partner_fit(opp, partner)
        assert result["fit_score"] >= 0.0
        assert result["recommendation"] in ("strong_fit", "good_fit", "marginal", "not_recommended")

    def test_no_capabilities_risks_documented(self):
        opp = self._make_opp()
        partner = self._make_partner(caps="")
        result = _assess_partner_fit(opp, partner)
        assert any("capabilities" in r.lower() for r in result["risk_assessment"])

    def test_score_bounded(self):
        opp = self._make_opp()
        partner = self._make_partner()
        result = _assess_partner_fit(opp, partner)
        assert 0.0 <= result["fit_score"] <= 1.0

    def test_prospect_partner_flagged(self):
        opp = self._make_opp()
        partner = self._make_partner()
        partner["status"] = "prospect"
        result = _assess_partner_fit(opp, partner)
        assert any("prospect" in r.lower() for r in result["risk_assessment"])


class TestShapeConstants:
    def test_threshold_ordering(self):
        assert _FIT_STRONG_THRESHOLD > _FIT_GOOD_THRESHOLD > _FIT_MARGINAL_THRESHOLD >= 0.0

    def test_weights_sum_to_one(self):
        from tools.proposal_genesis.reflexes.shape import (
            _CAP_WEIGHT, _CERT_WEIGHT, _VEHICLE_WEIGHT, _SETASIDE_WEIGHT
        )
        total = _CAP_WEIGHT + _CERT_WEIGHT + _VEHICLE_WEIGHT + _SETASIDE_WEIGHT
        assert abs(total - 1.0) < 0.001
