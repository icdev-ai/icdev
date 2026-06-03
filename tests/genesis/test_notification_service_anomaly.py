# CUI // SP-CTI
"""Tests for anomaly-detection helpers and constants in the notification service."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.notification_service.alert_service import (
    _compute_poam_deadline_threshold,
    _NARRATIVE_MAX_TOKENS as ALERT_MAX_TOKENS,
    _NARRATIVE_TEMPERATURE as ALERT_TEMP,
    _CAT1_DIGEST_LIMIT,
    _STIG_DIGEST_LIMIT,
    _POAM_DAYS_THRESHOLD,
    _DIGEST_DAYS_WINDOW,
    _POAM_AUTO_DUE_DAYS,
)
from tools.notification_service.digest_service import (
    _NARRATIVE_MAX_TOKENS as DIGEST_MAX_TOKENS,
    _NARRATIVE_TEMPERATURE as DIGEST_TEMP,
    _KANBAN_DONE_LIMIT,
    _ORACLE_TOP_PREDS_LIMIT,
    _AIIFY_TOP_OPPS_LIMIT,
    _AGENT_ERRORS_LIMIT,
    _ZIG_GAPS_LIMIT,
    _POAM_DAYS_AHEAD,
    _AUDIT_SINCE_HOURS,
    _NARRATIVE_TOP_PREDS_SLICE,
    _NARRATIVE_TOP_OPPS_SLICE,
)
from tools.notification_service.handler_service import (
    _NARRATIVE_MAX_TOKENS as HANDLER_MAX_TOKENS,
    _NARRATIVE_TEMPERATURE as HANDLER_TEMP,
    _TASK_AUDIT_HISTORY_LIMIT,
    _SCAN_TOP_OPPS_LIMIT,
    _ROADMAP_TOP_OPPS_LIMIT,
    _CMMC_GAPS_LIMIT,
    _AGENT_ERRORS_LIMIT as HANDLER_ERR_LIMIT,
    _HANDLER_ERRORS_SLICE,
)
from tools.notification_service.event_service import (
    _NARRATIVE_MAX_TOKENS as EVENT_MAX_TOKENS,
    _NARRATIVE_TEMPERATURE as EVENT_TEMP,
    _TASK_AUDIT_LIMIT,
    _GENESIS_REFLEX_LIMIT,
    _AIIFY_TOP_OPPS_LIMIT as EVENT_AIIFY_LIMIT,
    _KANBAN_DIGEST_LIMIT,
    _ORACLE_HORIZON_FALLBACK,
)


def _mock_conn(row_dict):
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = row_dict
    return conn


# ─────────────────────────────────────────────────────────────────
# _compute_poam_deadline_threshold (alert_service)
# ─────────────────────────────────────────────────────────────────

class TestComputePoamDeadlineThreshold:

    def test_disabled_returns_fallback(self):
        cfg = {"enabled": False, "fallback_days_threshold": 20}
        assert _compute_poam_deadline_threshold(cfg) == 20

    def test_insufficient_history_returns_fallback(self):
        cfg = {"enabled": True, "min_samples": 15, "fallback_days_threshold": 14,
               "adaptive_bounds": {"threshold_floor": 3, "threshold_ceil": 60}}
        with patch("tools.notification_service.alert_service.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn({"avg_days": 30.0, "n": 5})
            result = _compute_poam_deadline_threshold(cfg)
        assert result == 14

    def test_short_remediation_time_tightens_threshold(self):
        cfg = {"enabled": True, "min_samples": 5, "fallback_days_threshold": 14,
               "adaptive_bounds": {"threshold_floor": 3, "threshold_ceil": 60}}
        # avg_days=4 → threshold = max(3, min(60, int(4*0.5))) = max(3,2) = 3
        with patch("tools.notification_service.alert_service.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn({"avg_days": 4.0, "n": 20})
            result = _compute_poam_deadline_threshold(cfg)
        assert result <= 14
        assert result >= 3

    def test_long_remediation_widens_threshold(self):
        cfg = {"enabled": True, "min_samples": 5, "fallback_days_threshold": 14,
               "adaptive_bounds": {"threshold_floor": 3, "threshold_ceil": 60}}
        # avg_days=90 → threshold = max(3, min(60, 45)) = 45
        with patch("tools.notification_service.alert_service.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn({"avg_days": 90.0, "n": 20})
            result = _compute_poam_deadline_threshold(cfg)
        assert result > 14

    def test_bounds_respected(self):
        cfg = {"enabled": True, "min_samples": 5, "fallback_days_threshold": 14,
               "adaptive_bounds": {"threshold_floor": 7, "threshold_ceil": 30}}
        with patch("tools.notification_service.alert_service.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn({"avg_days": 500.0, "n": 20})
            result = _compute_poam_deadline_threshold(cfg)
        assert result <= 30

    def test_db_error_returns_fallback(self):
        cfg = {"enabled": True, "min_samples": 5, "fallback_days_threshold": 14}
        with patch("tools.notification_service.alert_service.get_connection",
                   side_effect=Exception("DB error")):
            result = _compute_poam_deadline_threshold(cfg)
        assert result == 14


# ─────────────────────────────────────────────────────────────────
# Constants consistency across all 4 services
# ─────────────────────────────────────────────────────────────────

class TestAllServicesShareCommonConstants:
    """All 4 notification services should use the same LLM param defaults."""

    def test_narrative_max_tokens_consistent(self):
        assert ALERT_MAX_TOKENS == DIGEST_MAX_TOKENS == HANDLER_MAX_TOKENS == EVENT_MAX_TOKENS == 512

    def test_narrative_temperature_consistent(self):
        assert ALERT_TEMP == pytest.approx(0.3, abs=0.001)
        assert DIGEST_TEMP == pytest.approx(0.3, abs=0.001)
        assert HANDLER_TEMP == pytest.approx(0.3, abs=0.001)
        assert EVENT_TEMP == pytest.approx(0.3, abs=0.001)


class TestAlertServiceConstants:
    def test_all_positive(self):
        assert _CAT1_DIGEST_LIMIT > 0
        assert _STIG_DIGEST_LIMIT > 0
        assert _POAM_DAYS_THRESHOLD > 0
        assert _DIGEST_DAYS_WINDOW > 0
        assert _POAM_AUTO_DUE_DAYS > 0


class TestDigestServiceConstants:
    def test_all_positive(self):
        assert _KANBAN_DONE_LIMIT > 0
        assert _ORACLE_TOP_PREDS_LIMIT > 0
        assert _AIIFY_TOP_OPPS_LIMIT > 0
        assert _AGENT_ERRORS_LIMIT > 0
        assert _ZIG_GAPS_LIMIT > 0
        assert _POAM_DAYS_AHEAD > 0
        assert _AUDIT_SINCE_HOURS > 0

    def test_slice_limits_le_query_limits(self):
        assert _NARRATIVE_TOP_PREDS_SLICE <= _ORACLE_TOP_PREDS_LIMIT
        assert _NARRATIVE_TOP_OPPS_SLICE <= _AIIFY_TOP_OPPS_LIMIT


class TestHandlerServiceConstants:
    def test_all_positive(self):
        assert _TASK_AUDIT_HISTORY_LIMIT > 0
        assert _SCAN_TOP_OPPS_LIMIT > 0
        assert _ROADMAP_TOP_OPPS_LIMIT > 0
        assert _CMMC_GAPS_LIMIT > 0
        assert HANDLER_ERR_LIMIT > 0
        assert _HANDLER_ERRORS_SLICE > 0
        assert _HANDLER_ERRORS_SLICE <= HANDLER_ERR_LIMIT


class TestEventServiceConstants:
    def test_all_positive(self):
        assert _TASK_AUDIT_LIMIT > 0
        assert _GENESIS_REFLEX_LIMIT > 0
        assert EVENT_AIIFY_LIMIT > 0
        assert _KANBAN_DIGEST_LIMIT > 0
        assert _ORACLE_HORIZON_FALLBACK > 0
