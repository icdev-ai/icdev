# CUI // SP-CTI
"""GitHub's `mergeable` is a cache, and git is the authority.

Mergeability is computed asynchronously and cached. When the base moves quickly
the cached answer goes stale and STAYS stale, because nothing about the PR
changed to invalidate it. On 2026-08-09 thirteen PRs were reported CONFLICTING
while `git merge-tree` merged every one cleanly — same base sha, same head sha.

Believing it was expensive: each burned two rebase attempts and five resume
cycles fighting a conflict that did not exist, then raised a HITL alert. A rebase
cannot clear it either — a branch that is already current has nothing to rebase,
so it succeeds, changes nothing, and the stale verdict survives.
"""
from __future__ import annotations

import subprocess

import tools.ci.pr_watcher as pw


def _state(head="kanban/x", base="main"):
    return {"headRefName": head, "baseRefName": base, "mergeable": "CONFLICTING"}


class _Runner:
    """git stub: (fetch_rc, mergetree_rc), or an exception to raise."""

    def __init__(self, fetch_rc=0, merge_rc=0, raises=None):
        self.calls = []
        self._rcs = [fetch_rc, merge_rc]
        self._raises = raises

    def __call__(self, argv, **kw):
        if self._raises:
            raise self._raises
        self.calls.append(argv)
        rc = self._rcs[min(len(self.calls) - 1, 1)]
        return type("P", (), {"returncode": rc, "stdout": "tree", "stderr": ""})()


def _w():
    w = pw.PRWatcher(config={}, get_connection=lambda: None)
    w._default_branch = lambda: "main"
    return w


def test_a_clean_merge_means_the_forge_verdict_is_stale():
    assert _w()._conflict_is_real(_state(), runner=_Runner(merge_rc=0)) is False


def test_a_real_conflict_is_still_reported():
    """It must discriminate, not blanket-override the forge."""
    assert _w()._conflict_is_real(_state(), runner=_Runner(merge_rc=1)) is True


def test_it_verifies_against_the_prs_own_base_not_always_main():
    r = _Runner(merge_rc=0)
    _w()._conflict_is_real(_state(base="release/2026"), runner=r)
    assert any("origin/release/2026" in a for call in r.calls for a in call)


# ── every failure trusts the forge ──────────────────────────────────────────
def test_an_unfetchable_ref_trusts_the_forge():
    """Erring the other way would declare a real conflict resolved."""
    assert _w()._conflict_is_real(_state(), runner=_Runner(fetch_rc=1)) is True


def test_git_missing_trusts_the_forge():
    assert _w()._conflict_is_real(
        _state(), runner=_Runner(raises=OSError("no git"))) is True


def test_a_timeout_trusts_the_forge():
    assert _w()._conflict_is_real(
        _state(), runner=_Runner(raises=subprocess.TimeoutExpired("git", 1))) is True


def test_a_pr_with_no_head_ref_trusts_the_forge():
    assert _w()._conflict_is_real({"baseRefName": "main"}, runner=_Runner()) is True
