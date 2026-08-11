# CUI // SP-CTI
"""A rebase must not silently discard a fix that lives inside a merge commit.

Measured 2026-08-10 on `kanban/sbx-sig-02`. An operator pressed **Rebase** on the
monitoring firing-alerts panel. That branch's commit `46facae13` was a MERGE, and
its subject says where the fix was:

    fix(sbom): validate_sbom takes a document, and merge main

`git rebase` drops merge commits unless given `--rebase-merges`, so a change made
INSIDE the merge — an "evil merge", resolved by editing rather than by taking a
side — goes with it. The rebase restored the very `validate_sbom = validate_file`
alias that commit existed to remove, and took 87 lines across 6 files with it.
Nothing errored. The force-push published the shortened history, and it surfaced
only because an unrelated push was rejected and somebody read the conflict
instead of forcing past it.

The button was not even wrong to be offered: that alert's cause was `ci_failed`,
not `merge_conflict`, so the cause-based gate had no opinion. The real
precondition is the SHAPE of the branch.
"""
from __future__ import annotations

import pytest

rr = pytest.importorskip("tools.kanban.rebase_recovery")


class _Proc:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


def _runner(merge_output, *, rev_list_rc=0):
    """Answer the calls rebase_and_push makes before it would touch a worktree."""
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        if argv[:2] == ["git", "fetch"]:
            return _Proc()
        if argv[:2] == ["git", "rev-parse"]:
            return _Proc("a" * 40)
        if argv[:3] == ["git", "rev-list", "--merges"]:
            return _Proc(merge_output, returncode=rev_list_rc)
        return _Proc()

    run.calls = calls
    return run


def _rebase(runner):
    return rr.rebase_and_push("t-1", "kanban/t-1", base="main", runner=runner)


# ── the refusal ─────────────────────────────────────────────────────────────
def test_a_branch_with_a_merge_commit_is_REFUSED():
    runner = _runner("1234567890abcdef1234567890abcdef12345678\n")
    verdict = _rebase(runner)

    assert verdict["pushed"] is False
    assert verdict["attempted"] is False, "nothing may be attempted on this branch"
    assert "merge commit" in verdict["reason"]


def test_the_refusal_says_WHY_so_the_panel_can_show_it():
    """The dashboard renders this string. 'failed' teaches nothing; the operator
    has to learn that a rebase would DROP something."""
    verdict = _rebase(_runner("1234567890abcdef1234567890abcdef12345678\n"))

    assert "DROPS merge commits" in verdict["reason"]
    assert "by hand" in verdict["reason"], "say what to do instead"


def test_the_refusal_happens_BEFORE_any_worktree_or_push():
    """The whole point is that nothing is rewritten. If the guard ran after the
    scratch worktree, a crash mid-way could still leave the branch rewritten."""
    runner = _runner("1234567890abcdef1234567890abcdef12345678\n")
    _rebase(runner)

    ran = [" ".join(c) for c in runner.calls]
    assert not any("worktree add" in c for c in ran), "no worktree may be created"
    assert not any("rebase" in c and "rev-list" not in c for c in ran)
    assert not any("push" in c for c in ran), "nothing may be pushed"


def test_several_merges_are_counted_and_named():
    runner = _runner("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
                     "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n")
    reason = _rebase(runner)["reason"]

    assert "2 merge commit(s)" in reason
    assert "aaaaaaaaa" in reason and "bbbbbbbbb" in reason


# ── and it must not block the ordinary case ─────────────────────────────────
def test_a_LINEAR_branch_is_not_refused():
    """This module exists to recover stale branches; most carry no merge at all,
    and over-refusing would take the cheap recovery away from all of them."""
    runner = _runner("")
    verdict = _rebase(runner)

    assert "merge commit" not in verdict["reason"]
    ran = [" ".join(c) for c in runner.calls]
    assert any("worktree add" in c for c in ran), "the normal path must proceed"


def test_an_unanswerable_probe_does_not_take_the_recovery_away():
    """Best-effort: if git cannot answer, proceed rather than refusing. A probe
    that fails must not make the recovery unavailable — the rebase itself is
    still clean-only and still force-with-lease."""
    runner = _runner("", rev_list_rc=128)
    verdict = _rebase(runner)

    assert "merge commit" not in verdict["reason"]


# ── the probe itself ────────────────────────────────────────────────────────
def test_the_probe_asks_only_for_merges_on_the_branch_side():
    """`origin/<base>..<head>` — merges already on the base are not this
    branch's problem and would refuse every branch on a repo that merges PRs."""
    runner = _runner("")
    rr._merge_commits("/repo", "main", "deadbeef", runner)

    argv = [c for c in runner.calls if c[:3] == ["git", "rev-list", "--merges"]][0]
    assert argv[-1] == "origin/main..deadbeef"
