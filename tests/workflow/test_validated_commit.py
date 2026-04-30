# CUI // SP-CTI
"""Unit tests for the cwd_exists guard in validate_working_tree.

Regression target: when a worktree path no longer exists on disk, the guard
introduced in commit a5f9bf36 must return a ``rebuild_worktree`` remediation
immediately — before any subprocess gates run — rather than letting swallowed
FileNotFoundErrors produce a false-pass or a coherence_broken loop.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.workflow.validated_commit import validate_working_tree  # noqa: E402


class TestCwdExistsGuard:
    """validate_working_tree must short-circuit when cwd is missing."""

    def test_missing_worktree_returns_false(self):
        with patch("tools.workflow.validated_commit.Path.exists", return_value=False):
            passed, _reason, _metrics = validate_working_tree("/nonexistent/worktree/path")
        assert passed is False

    def test_missing_worktree_reason_message(self):
        with patch("tools.workflow.validated_commit.Path.exists", return_value=False):
            _passed, reason, _metrics = validate_working_tree("/nonexistent/worktree/path")
        assert "worktree missing" in reason.lower()
        assert "rebuild" in reason.lower()

    def test_missing_worktree_rebuild_remediation(self):
        """Core acceptance criterion: metrics must carry rebuild_worktree, not None."""
        with patch("tools.workflow.validated_commit.Path.exists", return_value=False):
            _passed, _reason, metrics = validate_working_tree("/nonexistent/worktree/path")
        assert metrics.get("remediation") == "rebuild_worktree", (
            f"Expected 'rebuild_worktree', got {metrics.get('remediation')!r}"
        )

    def test_missing_worktree_no_subprocess_gates_run(self):
        """No CodeLens / coherence / E2E gates should execute when cwd is gone."""
        with patch("tools.workflow.validated_commit.Path.exists", return_value=False):
            with patch("tools.workflow.validated_commit._run_codelens") as mock_cl:
                with patch("tools.workflow.validated_commit._run_coherence") as mock_co:
                    validate_working_tree("/nonexistent/worktree/path")
        mock_cl.assert_not_called()
        mock_co.assert_not_called()

    def test_missing_worktree_metrics_shape(self):
        """Metrics dict must have all expected keys with safe zero/None defaults."""
        with patch("tools.workflow.validated_commit.Path.exists", return_value=False):
            _passed, _reason, metrics = validate_working_tree("/nonexistent/worktree/path")
        for key in (
            "codelens_passed",
            "coherence_passed",
            "e2e_ran",
            "e2e_passed",
            "companion_synced",
            "ruff_issues",
            "bandit_issues",
            "modified_files",
            "modified_py",
            "elapsed_sec",
            "budget_sec",
            "remediation",
        ):
            assert key in metrics, f"Missing key in metrics: {key!r}"
        assert metrics["e2e_ran"] is False
        assert metrics["elapsed_sec"] == 0

    def test_existing_worktree_does_not_short_circuit(self, tmp_path):
        """Sanity check: a real directory must not trigger the guard."""
        # We only verify the guard is bypassed (inner gates may fail for other reasons).
        with patch("tools.workflow.validated_commit._run_codelens", return_value=(True, "ok", {})):
            with patch("tools.workflow.validated_commit._run_coherence", return_value=(True, "ok")):
                with patch("tools.workflow.validated_commit._run_e2e", return_value=(True, "ok", {})):
                    with patch("tools.workflow.validated_commit._run_companion_sync", return_value=(True, "ok")):
                        passed, reason, metrics = validate_working_tree(str(tmp_path))
        assert metrics.get("remediation") != "rebuild_worktree"
        assert "worktree missing" not in reason.lower()
