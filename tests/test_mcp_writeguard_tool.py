#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for the writeguard_analyze MCP tool adapter
(tools.pulse.writeguard.handle_writeguard_analyze)."""
from __future__ import annotations

from unittest.mock import patch

from tools.pulse.writeguard import handle_writeguard_analyze


class TestHandleWriteguardAnalyze:
    def test_missing_text_returns_error_dict(self):
        assert "error" in handle_writeguard_analyze({})
        assert "error" in handle_writeguard_analyze({"text": "   "})

    def test_success_delegates_to_run_full_quality_check(self):
        fake_result = {"passed": True, "overall_score": 87.5, "recommendations": []}
        with patch("tools.pulse.writeguard.run_full_quality_check", return_value=fake_result) as mock_check:
            result = handle_writeguard_analyze({"text": "Some markdown report body."})

        mock_check.assert_called_once_with("Some markdown report body.")
        assert result == fake_result

    def test_exception_is_caught_and_returns_error_dict(self):
        with patch("tools.pulse.writeguard.run_full_quality_check", side_effect=RuntimeError("boom")):
            result = handle_writeguard_analyze({"text": "some text"})
        assert "error" in result
        assert "boom" in result["error"]

    def test_no_opportunity_id_needed_plagiarism_check_noops(self):
        """run_full_quality_check's plagiarism check safely no-ops without an
        opportunity_id (see check_plagiarism) -- the MCP tool never passes
        one, so a cross-repo caller (e.g. idea_lab) must never hit the DB."""
        result = handle_writeguard_analyze({"text": "A short piece of sample text for analysis purposes."})
        assert "error" not in result
        assert "overall_score" in result
