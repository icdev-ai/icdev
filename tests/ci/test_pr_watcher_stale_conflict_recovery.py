# CUI // SP-CTI
"""A PR the forge WRONGLY calls conflicting must not be locked out forever.

Measured on #1473 (2026-08-09). The PR was 18 commits behind main and merged
cleanly under both `git merge-tree` and a real `git rebase`, while the GitHub API
reported `mergeable=false mergeable_state=dirty` on every poll. Eleven AGOV PRs
sat in that state.

Two separate defects kept them there, and the first hid the second:

  1. `_auto_merge` returned False on a non-zero exit with NO log line, so a forge
     refusing every merge looked exactly like a board with nothing to merge.
  2. Detecting the verdict as stale only flipped a local flag. GitHub still
     refused. Close+reopen was tried against the live PR and did NOT clear it —
     mergeability is cached against the head sha, so only moving the ref helps.
     That is `_maybe_rebase`, whose 2-attempt budget had already been spent
     fighting the phantom, leaving the PR permanently unable to take the one
     action that would fix it.
"""
from __future__ import annotations

import pytest

pr_watcher = pytest.importorskip("tools.ci.pr_watcher")


class _Proc:
    def __init__(self, rc, stderr=""):
        self.returncode, self.stderr, self.stdout = rc, stderr, ""


def _watcher():
    return pr_watcher.PRWatcher(dry_run=True)


def _live_watcher():
    """dry_run off AND auto_merge on: both short-circuit before the merge runner,
    so a test that forgets either one passes while exercising nothing."""
    w = pr_watcher.PRWatcher(dry_run=True)
    w.dry_run = False
    w.config = {**getattr(w, "config", {}), "auto_merge_enabled": True}
    return w


def test_a_refused_merge_is_LOGGED_not_swallowed(caplog):
    """The silence is the bug: eleven PRs went unmerged for a day with no
    evidence in the log that a merge had ever been attempted, let alone refused."""
    w = _live_watcher()
    w._auto_merge_runner = lambda *a, **k: _Proc(1, "Pull request is not mergeable")
    with caplog.at_level("WARNING", logger=pr_watcher.logger.name):
        pr_watcher.logger.propagate = True
        assert w._auto_merge("https://github.com/o/r/pull/1") is False
    assert any("refused to merge" in r.message or "refused to merge" in r.getMessage()
               for r in caplog.records), "a refusal must leave a trace"
    assert any("not mergeable" in r.getMessage() for r in caplog.records), \
        "the forge's own reason is the only diagnostic there is — keep it"


def test_a_successful_merge_still_reports_success():
    w = _live_watcher()
    w._auto_merge_runner = lambda *a, **k: _Proc(0)
    assert w._auto_merge("https://github.com/o/r/pull/1") is True


def test_a_refund_restores_a_spent_rebase_attempt(monkeypatch):
    """Without this the budget is a one-way ratchet: the attempts burned on a
    conflict that never existed are exactly the attempts needed to clear it."""
    w = _watcher()
    counts = {("pr_watcher.rebase", "pr_watcher.rebase_failed"): 2,
              ("pr_watcher.rebase_refund",): 1}
    monkeypatch.setattr(w, "_count_audit_actions",
                        lambda tid, actions, pr_url=None: counts.get(tuple(actions), 0))
    assert w._rebase_attempts("t-1", "u") == 1, "2 spent - 1 refunded"


def test_refunds_can_restore_a_budget_but_never_GRANT_one(monkeypatch):
    """Floored at zero. A negative count would read as spare budget and let a
    genuinely conflicted PR rebase without limit."""
    w = _watcher()
    monkeypatch.setattr(w, "_count_audit_actions",
                        lambda tid, actions, pr_url=None:
                        0 if "pr_watcher.rebase" in actions else 5)
    assert w._rebase_attempts("t-1", "u") == 0


def test_the_refund_is_an_APPEND_not_an_edit(monkeypatch):
    """audit_trail is append-only (NIST AU). A refund that UPDATEd or DELETEd the
    rows it cancels would be a compliance defect, so it is a new row that the
    counter subtracts."""
    w = _watcher()
    w.dry_run = False
    seen = []
    monkeypatch.setattr(w, "_audit", lambda action: seen.append(action))
    w._refund_rebase_budget("t-1", "https://github.com/o/r/pull/1")
    assert len(seen) == 1
    assert seen[0].action == "rebase_refund"
    assert seen[0].pr_url.endswith("/1"), "must be scoped to THIS pr, not the task"


def test_the_refund_is_scoped_to_one_pr():
    """Budgets are per-PR: a refund on a reopened PR must not hand budget to a
    sibling PR for the same task."""
    import inspect
    src = inspect.getsource(pr_watcher.PRWatcher._rebase_attempts)
    assert "pr_url=pr_url" in src
    assert src.count("pr_url=pr_url") == 2, "both the spend AND the refund scope by pr"


# ── the defect that made the FIRST version of this fix inert ────────────────
def test_a_stale_conflict_routes_to_REBASE_not_to_merge():
    """The first version of this fix refunded the budget and then reclassified
    the PR to MERGEABLE. That sent it to _auto_merge — the one action GitHub is
    guaranteed to refuse — and away from _maybe_rebase, which is gated on
    MERGE_CONFLICT and is the only thing that moves the ref. The refund restored
    a budget nothing could ever spend.

    Asserted on source because the alternative is standing up a full poll against
    a live forge; the property is structural, not behavioural.
    """
    import inspect
    src = inspect.getsource(pr_watcher.PRWatcher.poll_once)
    head = src[src.index("_conflict_is_real(state)"):]
    block = head[:head.index("cycle = self._resume_cycle")]
    assert '"mergeable": "MERGEABLE"' not in block, (
        "reclassifying a stale conflict to MERGEABLE routes it to the merge the "
        "forge refuses, and skips the rebase that would fix it")
    assert "_refund_rebase_budget" in block
    assert "_maybe_rebase(task, state)" in src, "the rebase path must still exist"


def test_the_rebase_path_is_still_gated_on_merge_conflict():
    """If that gate ever widens, the assertion above stops meaning anything."""
    import inspect
    src = inspect.getsource(pr_watcher.PRWatcher.poll_once)
    i = src.index("_maybe_rebase(task, state)")
    assert "KanbanState.MERGE_CONFLICT" in src[max(0, i - 300):i]


def test_the_refund_happens_at_most_once_per_pr(monkeypatch):
    """Unbounded refunds would hand a PR unlimited rebases: every poll proves the
    conflict stale again, so every poll would return the budget it just spent."""
    import inspect
    src = inspect.getsource(pr_watcher.PRWatcher.poll_once)
    block = src[src.index("_conflict_is_real(state)"):]
    block = block[:block.index("cycle = self._resume_cycle")]
    assert '"pr_watcher.rebase_refund"' in block and "== 0" in block, (
        "the refund must be guarded on there being no prior refund for this PR")
