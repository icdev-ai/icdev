# CUI // SP-CTI
"""OPT-51: tests for run_pre_tool_check git destructive-command blocklist.

Adapted from mattpocock/skills/git-guardrails-claude-code (MIT). Verifies
that hook_compat.run_pre_tool_check blocks the same dangerous git
commands that .claude/hooks/pre_tool_use.py blocks for the Claude path.
"""
from __future__ import annotations

import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.airgap import hook_compat  # noqa: E402


def _bash(cmd):
    return hook_compat.run_pre_tool_check("Bash", {"command": cmd})


def test_force_push_blocked():
    r = _bash("git push origin main --force")
    assert r["allowed"] is False
    assert "force" in r["reason"].lower()


def test_force_push_short_flag_blocked():
    r = _bash("git push -f origin feature")
    assert r["allowed"] is False
    assert "force" in r["reason"].lower()


def test_force_push_force_with_lease_allowed():
    """``--force-with-lease`` is DELIBERATELY exempt (exa-bench-05).

    It refuses the push when the remote moved underneath it, which is the
    collision this repo's concurrent sessions have to survive, and it is what
    they actually run: 127 of the 30-day corpus's 389 git_danger fires were
    this form on the session's own ``kanban/*`` branch. See
    ``_GIT_FORCE_WITH_LEASE_RE`` in tools/hooks/shared_checks.py. This test
    pinned the pre-exa-bench-05 verdict and was red from 2026-08-12 until
    task-det-ea73602df5 (born_red_survey finding ea73602df5798228).
    """
    r = _bash("git push --force-with-lease origin main")
    assert r["allowed"] is True


def test_force_with_lease_exemption_is_per_segment():
    """The exemption covers ONE shell segment; a bare ``--force`` chained
    after a lease push is still the destructive form and still refused."""
    r = _bash("git push --force-with-lease origin main && git push --force origin main")
    assert r["allowed"] is False
    assert "force" in r["reason"].lower()


def test_normal_push_allowed():
    r = _bash("git push origin main")
    assert r["allowed"] is True


def test_reset_hard_blocked():
    r = _bash("git reset --hard HEAD~1")
    assert r["allowed"] is False
    assert "reset" in r["reason"].lower()


def test_soft_reset_allowed():
    r = _bash("git reset --soft HEAD~1")
    assert r["allowed"] is True


def test_branch_force_delete_blocked():
    r = _bash("git branch -D feature-xyz")
    assert r["allowed"] is False


def test_branch_delete_force_flag_blocked():
    r = _bash("git branch --delete --force feature-xyz")
    assert r["allowed"] is False


def test_safe_branch_delete_allowed():
    r = _bash("git branch -d already-merged")
    assert r["allowed"] is True


def test_clean_force_blocked():
    r = _bash("git clean -f")
    assert r["allowed"] is False


def test_clean_fd_blocked():
    r = _bash("git clean -fd")
    assert r["allowed"] is False


def test_clean_dry_run_allowed():
    r = _bash("git clean -n")
    assert r["allowed"] is True


def test_checkout_dot_blocked():
    r = _bash("git checkout .")
    assert r["allowed"] is False


def test_checkout_branch_allowed():
    r = _bash("git checkout main")
    assert r["allowed"] is True


def test_restore_dot_blocked():
    r = _bash("git restore .")
    assert r["allowed"] is False


def test_restore_specific_file_allowed():
    r = _bash("git restore src/main.py")
    assert r["allowed"] is True


def test_rebase_interactive_blocked():
    r = _bash("git rebase -i HEAD~5")
    assert r["allowed"] is False


def test_case_insensitive_match():
    r = _bash("GIT PUSH --FORCE origin main")
    assert r["allowed"] is False


def test_git_status_not_blocked():
    r = _bash("git status")
    assert r["allowed"] is True


def test_non_git_bash_not_blocked_by_git_rules():
    r = _bash("ls -la")
    assert r["allowed"] is True
