#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for quality feedback loop (D-KARL-9)."""

import sys
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.rag.quality_feedback_loop import (
    run_feedback_cycle,
    get_feedback_status,
    _get_config,
    DEFAULT_CONFIG,
)


class TestConfig:
    """Config loading tests."""

    def test_default_config_keys(self):
        assert "auto_remediate" in DEFAULT_CONFIG
        assert "use_statement_extraction" in DEFAULT_CONFIG
        assert "max_auto_pairs_per_cycle" in DEFAULT_CONFIG

    def test_get_config_returns_defaults(self):
        cfg = _get_config()
        assert cfg["auto_remediate"] is True
        assert cfg["max_auto_pairs_per_cycle"] == 50


class TestFeedbackCycle:
    """Feedback cycle orchestration tests."""

    @patch("tools.finetune.quality_monitor.check_quality")
    def test_no_action_when_quality_ok(self, mock_check):
        mock_check.return_value = {
            "status": "ok",
            "metrics": {"ndcg": 0.8, "mrr": 0.7},
            "alerts": [],
            "retrain_recommended": False,
        }
        result = run_feedback_cycle(dry_run=False)
        assert "no_action_needed" in result["actions"]
        assert result["retrain_recommended"] is False
        assert result["pairs_generated"] == 0

    @patch("tools.finetune.quality_monitor.check_quality")
    def test_dry_run_skips_generation(self, mock_check):
        mock_check.return_value = {
            "status": "ok",
            "metrics": {"ndcg": 0.3},
            "alerts": [{"metric": "ndcg", "retrain_recommended": True}],
            "retrain_recommended": True,
        }
        result = run_feedback_cycle(dry_run=True)
        assert "dry_run_skip_generation" in result["actions"]
        assert result["pairs_generated"] == 0

    @patch("tools.rag.quality_feedback_loop._try_trigger_retrain")
    @patch("tools.rag.quality_feedback_loop._generate_with_statement_extraction")
    @patch("tools.finetune.quality_monitor.check_quality")
    def test_generates_pairs_on_degradation(self, mock_check, mock_gen, mock_retrain):
        mock_check.return_value = {
            "status": "ok",
            "metrics": {"ndcg": 0.2},
            "alerts": [{"metric": "ndcg", "retrain_recommended": True}],
            "retrain_recommended": True,
        }
        mock_gen.return_value = 15
        mock_retrain.return_value = False

        result = run_feedback_cycle(dry_run=False)
        assert result["pairs_generated"] == 15
        assert "statement_extraction_generation" in result["actions"]
        mock_gen.assert_called_once()

    @patch("tools.rag.quality_feedback_loop._try_trigger_retrain")
    @patch("tools.rag.quality_feedback_loop._generate_with_statement_extraction")
    @patch("tools.finetune.quality_monitor.check_quality")
    def test_retrain_triggered(self, mock_check, mock_gen, mock_retrain):
        mock_check.return_value = {
            "status": "ok",
            "metrics": {},
            "alerts": [{"metric": "ndcg", "retrain_recommended": True}],
            "retrain_recommended": True,
        }
        mock_gen.return_value = 10
        mock_retrain.return_value = True

        result = run_feedback_cycle(dry_run=False)
        assert result["retrain_triggered"] is True
        assert "retrain_triggered" in result["actions"]

    def test_cycle_result_has_expected_keys(self):
        with patch("tools.finetune.quality_monitor.check_quality") as mock_check:
            mock_check.return_value = {
                "status": "ok",
                "metrics": {},
                "alerts": [],
                "retrain_recommended": False,
            }
            result = run_feedback_cycle()
            assert "cycle_id" in result
            assert "started_at" in result
            assert "actions" in result
            assert "pairs_generated" in result

    @patch("tools.finetune.quality_monitor.check_quality")
    def test_auto_remediate_disabled(self, mock_check):
        mock_check.return_value = {
            "status": "ok",
            "metrics": {},
            "alerts": [{"metric": "ndcg", "retrain_recommended": True}],
            "retrain_recommended": True,
        }
        with patch("tools.rag.quality_feedback_loop._get_config") as mock_cfg:
            mock_cfg.return_value = {**DEFAULT_CONFIG, "auto_remediate": False}
            result = run_feedback_cycle()
            assert "auto_remediate_disabled" in result["actions"]


class TestFeedbackStatus:
    """Status endpoint tests."""

    @patch("tools.finetune.quality_monitor.get_quality_status")
    @patch("tools.finetune.quality_monitor.check_quality")
    def test_status_returns_ok(self, mock_check, mock_history):
        mock_check.return_value = {"status": "ok", "metrics": {}, "alerts": []}
        mock_history.return_value = {"total_snapshots": 5, "below_threshold_count": 1}
        result = get_feedback_status()
        assert result["status"] == "ok"
        assert "auto_remediate" in result
        assert "use_statement_extraction" in result
