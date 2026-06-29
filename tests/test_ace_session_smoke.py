# CUI // SP-CTI
"""Pytest wrapper for the ACE session smoke test.

Marked with @pytest.mark.live so it is skipped in normal CI runs
(requires a running server at localhost:5050).
"""
from __future__ import annotations

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.mark.live
def test_ace_session_smoke_list_page():
    """Sessions list page renders without errors."""
    from tools.testing.ace_session_smoke import check_sessions_list_page
    result = check_sessions_list_page("http://localhost:5050")
    assert result["passed"], f"sessions list page check failed: {result['detail']}"


@pytest.mark.live
def test_ace_session_smoke_api():
    """Sessions API returns valid JSON."""
    from tools.testing.ace_session_smoke import check_sessions_api
    result, _ = check_sessions_api("http://localhost:5050")
    assert result["passed"], f"sessions API check failed: {result['detail']}"


class TestSmokeHelpers:
    """Unit tests that don't require a running server."""

    def test_check_helper_passed(self):
        from tools.testing.ace_session_smoke import _check
        r = _check("test", True, "detail here")
        assert r["passed"] is True
        assert r["check"] == "test"
        assert r["detail"] == "detail here"

    def test_check_helper_failed(self):
        from tools.testing.ace_session_smoke import _check
        r = _check("test", False, "bad thing")
        assert r["passed"] is False

    def test_run_smoke_structure_on_connection_error(self):
        """run_smoke returns list of check dicts even on connection failure."""
        from tools.testing.ace_session_smoke import run_smoke
        # Port 1 should always fail to connect
        results = run_smoke("http://localhost:1", fast=True)
        assert isinstance(results, list)
        assert len(results) >= 2
        for r in results:
            assert "check" in r
            assert "passed" in r
            assert isinstance(r["passed"], bool)
