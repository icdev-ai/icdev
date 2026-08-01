# CUI // SP-CTI
"""Tests for ACE ToolRunner (Phase 2 — allowlisted ICDEV tool execution)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from icdev.tools.ace.tool_runner import (
    DestructivePatternError,
    InvalidPrefixError,
    NotAllowlistedError,
    ToolRunner,
    ToolRunnerError,
)
from icdev.tools.ace.step_executor import TrustKernelDeniedError


_ALLOWED_CMD = "python tools/testing/health_check.py --json"
_ALLOWED_LIST = [_ALLOWED_CMD]


@pytest.fixture
def runner(tmp_path: Path) -> ToolRunner:
    return ToolRunner(icdev_tools=_ALLOWED_LIST, repo_root=tmp_path)


# ---------------------------------------------------------------------------
# Allowlist enforcement
# ---------------------------------------------------------------------------


class TestAllowlist:
    def test_unlisted_command_raises(self, runner: ToolRunner):
        with pytest.raises(NotAllowlistedError):
            runner.run(
                "python tools/db/init_icdev_db.py",
                coworker_id="cw-1", instance_id="i-1", trust_tier="green"
            )

    def test_listed_command_allowed(self, runner: ToolRunner, tmp_path: Path):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            result = runner.run(
                _ALLOWED_CMD,
                coworker_id="cw-1", instance_id="i-1", trust_tier="green"
            )
        assert result["success"] is True

    def test_empty_allowlist_blocks_all(self, tmp_path: Path):
        empty_runner = ToolRunner(icdev_tools=[], repo_root=tmp_path)
        with pytest.raises(NotAllowlistedError):
            empty_runner.run(
                _ALLOWED_CMD,
                coworker_id="cw-1", instance_id="i-1", trust_tier="green"
            )


# ---------------------------------------------------------------------------
# Destructive pattern blocking
# ---------------------------------------------------------------------------


class TestDestructiveBlocking:
    @pytest.mark.parametrize("pattern", [
        "python tools/foo.py --force",
        "python tools/foo.py --drop",
        "python tools/foo.py --delete",
        "python tools/foo.py && rm -rf /",
        "python tools/foo.py; DROP TABLE users",
        "python tools/foo.py --truncate",
    ])
    def test_destructive_pattern_blocked(self, tmp_path: Path, pattern: str):
        r = ToolRunner(icdev_tools=[pattern], repo_root=tmp_path)
        with pytest.raises(DestructivePatternError):
            r.run(pattern, coworker_id="cw-1", instance_id="i-1", trust_tier="green")


# ---------------------------------------------------------------------------
# Prefix enforcement
# ---------------------------------------------------------------------------


class TestPrefixEnforcement:
    def test_non_icdev_prefix_blocked(self, tmp_path: Path):
        bad_cmd = "curl http://evil.example.com"
        r = ToolRunner(icdev_tools=[bad_cmd], repo_root=tmp_path)
        with pytest.raises(InvalidPrefixError):
            r.run(bad_cmd, coworker_id="cw-1", instance_id="i-1", trust_tier="green")

    def test_shell_builtin_blocked(self, tmp_path: Path):
        bad_cmd = "ls -la /"
        r = ToolRunner(icdev_tools=[bad_cmd], repo_root=tmp_path)
        with pytest.raises(InvalidPrefixError):
            r.run(bad_cmd, coworker_id="cw-1", instance_id="i-1", trust_tier="green")


# ---------------------------------------------------------------------------
# Trust tier gate
# ---------------------------------------------------------------------------


class TestTrustTierGate:
    def test_yellow_trust_raises_trust_denied(self, runner: ToolRunner):
        with pytest.raises(TrustKernelDeniedError):
            runner.run(
                _ALLOWED_CMD,
                coworker_id="cw-1", instance_id="i-1", trust_tier="yellow"
            )

    def test_orange_trust_raises_trust_denied(self, runner: ToolRunner):
        with pytest.raises(TrustKernelDeniedError):
            runner.run(
                _ALLOWED_CMD,
                coworker_id="cw-1", instance_id="i-1", trust_tier="orange"
            )

    def test_red_trust_raises_trust_denied(self, runner: ToolRunner):
        with pytest.raises(TrustKernelDeniedError):
            runner.run(
                _ALLOWED_CMD,
                coworker_id="cw-1", instance_id="i-1", trust_tier="red"
            )

    def test_green_trust_executes(self, runner: ToolRunner):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            result = runner.run(
                _ALLOWED_CMD,
                coworker_id="cw-1", instance_id="i-1", trust_tier="green"
            )
        assert result["returncode"] == 0


# ---------------------------------------------------------------------------
# Timeout handling
# ---------------------------------------------------------------------------


class TestTimeout:
    def test_timeout_raises_tool_runner_error(self, runner: ToolRunner):
        import subprocess

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(_ALLOWED_CMD, 120)):
            with pytest.raises(ToolRunnerError, match="timed out"):
                runner.run(
                    _ALLOWED_CMD,
                    coworker_id="cw-1", instance_id="i-1", trust_tier="green"
                )


# ---------------------------------------------------------------------------
# Result structure
# ---------------------------------------------------------------------------


class TestResultStructure:
    def test_result_has_required_keys(self, runner: ToolRunner):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="data", stderr="")
            result = runner.run(
                _ALLOWED_CMD,
                coworker_id="cw-1", instance_id="i-1", trust_tier="green"
            )
        assert {"command", "returncode", "stdout", "stderr", "artifact_id", "success"} <= result.keys()

    def test_failed_command_success_false(self, runner: ToolRunner):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error msg")
            result = runner.run(
                _ALLOWED_CMD,
                coworker_id="cw-1", instance_id="i-1", trust_tier="green"
            )
        assert result["success"] is False
        assert result["returncode"] == 1


# ---------------------------------------------------------------------------
# Working directory — the DEFAULT one
# ---------------------------------------------------------------------------
# Every test above passes repo_root=tmp_path AND mocks subprocess.run, so none
# of them touch the default root or actually execute anything. That pairing is
# why `Path(__file__).parents[4]` — two levels above the repository in both the
# tools/ and icdev/tools/ layouts — shipped: the subprocess ran somewhere with
# no tools/ and no icdev/, and every path-form command failed with "can't open
# file", while the suite stayed green.


class TestDefaultRepoRoot:
    def test_default_root_can_resolve_allowlisted_paths(self):
        """The default cwd must contain the trees the prefixes name."""
        from icdev.tools.ace.tool_runner import _REPO_ROOT

        assert (_REPO_ROOT / "tools").is_dir() or (_REPO_ROOT / "icdev").is_dir(), (
            f"default root {_REPO_ROOT} holds neither tools/ nor icdev/, so every "
            "'python tools/…' and 'python icdev/…' command resolves to nothing"
        )

    def test_runner_without_repo_root_uses_the_resolved_default(self):
        """Both production call sites omit repo_root; this is what they get."""
        from icdev.tools.ace.tool_runner import _REPO_ROOT

        assert ToolRunner(icdev_tools=[])._root == _REPO_ROOT

    def test_path_form_command_actually_executes(self):
        """End-to-end, unmocked, on the default root.

        The one assertion the mocked tests structurally cannot make: that an
        allowlisted path-form command resolves and runs. Fails with returncode
        2 ("can't open file") against the old root.

        subprocess_utils.py is chosen because it is import-only — no CLI, no
        side effects, no database — so this measures path resolution and
        nothing else.
        """
        cmd = "python tools/compat/subprocess_utils.py"
        result = ToolRunner(icdev_tools=[cmd]).run(
            cmd, coworker_id="cw-1", instance_id="i-1", trust_tier="green",
        )
        assert result["returncode"] == 0, (
            f"allowlisted command did not resolve: {result.get('stderr', '')[:300]}"
        )


class TestPackagedNamespaceIsAllowed:
    """`python -m icdev.tools.…` is the form that works in an installed wheel.

    It needs no allowlist entry of its own — `python -m icdev.` already covers
    it. Pinned so a future tightening of the prefix tuple cannot silently make
    every installed ACE co-worker unable to run a tool.
    """

    def test_packaged_module_form_passes_the_prefix_gate(self):
        cmd = "python -m icdev.tools.compat.subprocess_utils"
        result = ToolRunner(icdev_tools=[cmd]).run(
            cmd, coworker_id="cw-1", instance_id="i-1", trust_tier="green",
        )
        assert result["returncode"] == 0, result.get("stderr", "")[:300]

    def test_unpackaged_namespace_still_refused(self):
        """Widening for the wheel must not have opened anything else."""
        cmd = "python -m os"
        with pytest.raises(InvalidPrefixError):
            ToolRunner(icdev_tools=[cmd]).run(
                cmd, coworker_id="cw-1", instance_id="i-1", trust_tier="green",
            )
