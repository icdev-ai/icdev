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

# A plain import, not `importorskip`: `tools.ci.pr_watcher` is first-party and
# always present, so the guard could only ever convert a real breakage into a
# green skip — which is the whole reason this file was not gated before.
import tools.ci.pr_watcher as pr_watcher


class _Proc:
    def __init__(self, rc, stderr=""):
        self.returncode, self.stderr, self.stdout = rc, stderr, ""


def _watcher():
    return pr_watcher.PRWatcher(dry_run=True)


def _live_watcher():
    """dry_run off AND auto_merge on: both short-circuit before the merge runner,
    so a test that forgets either one passes while exercising nothing.

    THREE short-circuits now, since kpr-watch-05. The protected-path guard also
    precedes the merge runner, and it FAILS CLOSED when the open-PR index cannot
    name this PR's changed files — which is every test, because nothing here
    stubs the forge listing. So a clean index is part of "live" too, or these
    tests silently stop exercising the gh path and start exercising the guard.
    That is the same trap the docstring above already warns about, one rung
    later; the guard's own refusal is covered in
    tests/test_pr_watcher_protected_paths.py.
    """
    w = pr_watcher.PRWatcher(dry_run=True)
    w.dry_run = False
    w.config = {**getattr(w, "config", {}), "auto_merge_enabled": True}
    w._open_pr_index = lambda repo=None: {  # noqa: SLF001 — fixture, not production wiring
        "https://github.com/o/r/pull/1": {
            "files": {"README.md"}, "mergeable": "MERGEABLE", "draft": False},
    }
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
    head = src[src.index("classify_conflict(state)"):]
    block = head[:head.index("cycle = self._resume_cycle")]
    assert '"mergeable": "MERGEABLE"' not in block, (
        "reclassifying a stale conflict to MERGEABLE routes it to the merge the "
        "forge refuses, and skips the rebase that would fix it")
    assert "_refund_rebase_budget" in block
    assert "_maybe_rebase(task, state)" in src, "the rebase path must still exist"


def test_every_rebase_call_site_is_gated_on_a_named_condition():
    """If a gate ever widens, the assertion above stops meaning anything.

    There are TWO call sites, and neither may be reached unconditionally:

    * MERGE_CONFLICT -- the original, for a branch the forge reports DIRTY;
    * the staleness hold (kpr-stale-02) -- for a branch that is green and
      MERGEABLE and simply too far behind the default branch to merge safely.
      A rebase is the same repair for both, which is why it reuses this path
      rather than growing a second one.
    """
    import inspect
    src = inspect.getsource(pr_watcher.PRWatcher.poll_once)
    sites = [i for i in range(len(src))
             if src.startswith("_maybe_rebase(task, state)", i)]
    assert len(sites) == 2, "expected two rebase call sites, got %d" % len(sites)
    # 800 chars: enough to reach past each site's audit/log preamble to the
    # condition that guards it, and far too short to reach the OTHER site's.
    gates = [src[max(0, i - 800):i] for i in sites]
    assert any("KanbanState.MERGE_CONFLICT" in g for g in gates), (
        "the conflict-driven rebase lost its gate")
    assert any("if stale is not None" in g for g in gates), (
        "the staleness-driven rebase lost its gate")
    # ...and the two gates are on DIFFERENT sites, not both on one.
    assert not any("KanbanState.MERGE_CONFLICT" in g and "if stale is not None" in g
                   for g in gates), "the two rebase gates collapsed onto one site"


def test_the_refund_happens_at_most_once_per_pr(monkeypatch):
    """Unbounded refunds would hand a PR unlimited rebases: every poll proves the
    conflict stale again, so every poll would return the budget it just spent."""
    import inspect
    src = inspect.getsource(pr_watcher.PRWatcher.poll_once)
    block = src[src.index("classify_conflict(state)"):]
    block = block[:block.index("cycle = self._resume_cycle")]
    assert '"pr_watcher.rebase_refund"' in block and "== 0" in block, (
        "the refund must be guarded on there being no prior refund for this PR")


# ── the alert must clear on recovery, and ONLY on recovery ──────────────────
def _open_state(mergeable="MERGEABLE", checks=(("Test", "SUCCESS"),)):
    from datetime import datetime, timedelta, timezone
    created = datetime.now(timezone.utc) - timedelta(hours=2)
    return {
        "state": "OPEN", "isDraft": False, "mergeable": mergeable,
        "statusCheckRollup": [{"name": n, "conclusion": c, "status": "COMPLETED"}
                              for n, c in checks],
        "createdAt": created.isoformat().replace("+00:00", "Z"),
    }


def test_a_hitl_alert_clears_when_the_pr_is_mergeable_again():
    """It used to clear only on DONE — but a branch whose conflict is resolved
    returns to MERGEABLE without ever passing through DONE, so its alert stayed
    firing forever. Measured 2026-08-10: 14 firing HITL alerts, at least 2 of
    them naming branches already fixed and sitting green. An alert list that can
    only grow is one people stop reading."""
    assert _watcher()._hitl_recovered(_open_state(), cycle=0, max_cycles=5) is True


def test_a_still_conflicting_pr_is_not_recovered():
    assert _watcher()._hitl_recovered(
        _open_state(mergeable="CONFLICTING"), cycle=0, max_cycles=5) is False


def test_mergeable_with_the_resume_budget_SPENT_is_not_recovery():
    """The flap. The resolve runs EARLIER in the same pass than the raise, so
    clearing on `mergeable` alone resolved an alert that the very same pass then
    re-raised — for a PR that is mergeable with red CI and nothing left to try.
    Measured 2026-08-10, hours after the resolve shipped: agov-det-02 and
    sbx-sig-02 cycled about once a minute, ~180 rows in a day. An alert list
    that refills as fast as it drains is the same list nobody reads."""
    w = _watcher()
    state = _open_state(checks=(("Test", "FAILURE"),))
    assert w._hitl_recovered(state, cycle=5, max_cycles=5) is False
    assert w._hitl_recovered(state, cycle=6, max_cycles=5) is False, "cap is >="
    assert w._hitl_recovered(state, cycle=4, max_cycles=5) is True, (
        "budget left means the loop can still act — that is genuine recovery")


def test_a_pr_whose_CI_NEVER_FIRED_is_not_recovered():
    """The other raise site. An empty rollup looks mergeable and un-failed, but
    it is precisely the PR that can never go green on its own."""
    assert _watcher()._hitl_recovered(
        _open_state(checks=()), cycle=0, max_cycles=5) is False


def test_an_already_landed_pr_is_not_recovered():
    """The third raise site, behaviourally.

    An already-landed PR is green and MERGEABLE — that is precisely what makes
    it dangerous — so every other clause in _hitl_recovered votes "recovered".
    Without its own negation the alert resolves and re-raises on every pass,
    which is the flap the two clauses above it were written to stop.
    """
    w = _watcher()
    landed = {"checked": True, "landed": True, "confidence": "subject"}
    assert w._hitl_recovered(_open_state(), cycle=0, max_cycles=5,
                             landed=landed) is False
    # ...and the negation must key on the FINDING, not merely on being passed a
    # report: an unchecked or clean one is not a reason to hold an alert open.
    assert w._hitl_recovered(_open_state(), cycle=0, max_cycles=5,
                             landed={"checked": False, "landed": True}) is True
    assert w._hitl_recovered(_open_state(), cycle=0, max_cycles=5,
                             landed={"checked": True, "landed": False}) is True
    assert w._hitl_recovered(_open_state(), cycle=0, max_cycles=5,
                             landed=None) is True


def test_every_raise_site_is_negated_by_the_recovery_check():
    """The invariant that keeps the flap from coming back: a raise condition
    with no matching clause in _hitl_recovered resolves and re-raises on every
    pass, forever."""
    import inspect
    poll = inspect.getsource(pr_watcher.PRWatcher.poll_once)
    assert poll.count("self._hitl_alert(") == 4, (
        "a new raise site must also be negated in _hitl_recovered")
    rec = inspect.getsource(pr_watcher.PRWatcher._hitl_recovered)
    assert "max_cycles" in rec and "_ci_never_fired" in rec
    # Third raise site (trust-disc-05): a task already on the default branch.
    # It is the most flap-prone of the three, because such a PR is green AND
    # MERGEABLE by construction — every other condition here says "recovered".
    assert 'landed.get("landed")' in rec, (
        "the already-landed raise site is not negated in _hitl_recovered — it "
        "will resolve and re-raise on every pass")
    # Fourth raise site (kpr-stale-02): a branch too far BEHIND the default
    # branch to merge safely, whose automatic rebase was declined. Same flap
    # shape as the third — such a PR is green AND MERGEABLE by construction.
    assert "_stale_verdict" in rec, (
        "the behind-main raise site is not negated in _hitl_recovered — it "
        "will resolve and re-raise on every pass")
    # the DONE path must still clear it
    assert poll.count("_resolve_hitl_alert") >= 2
