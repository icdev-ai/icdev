# CUI // SP-CTI
"""A PR whose CI never fired can never go green, and never be recovered.

Every other repair path in the watch loop assumes there is a CI result to react
to: a failure to resume from, a conflict to rebase. With an EMPTY check rollup
there is nothing to act on, so the PR waits in PR_OPENED forever. #1462 sat that
way — zero checks, not failing, not running, simply absent.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import tools.ci.pr_watcher as pw


def _state(*, checks=None, created_minutes_ago=60):
    created = datetime.now(timezone.utc) - timedelta(minutes=created_minutes_ago)
    return {
        "state": "OPEN", "isDraft": False, "mergeable": "MERGEABLE",
        "statusCheckRollup": checks or [],
        "createdAt": created.isoformat().replace("+00:00", "Z"),
        "url": "https://x/pull/1", "number": 1,
    }


def _watcher(**config):
    cfg = {"ci_missing_grace_minutes": 15, "max_ci_retriggers_per_pr": 1}
    cfg.update(config)
    return pw.PRWatcher(config=cfg, get_connection=lambda: None)


# ── detection ───────────────────────────────────────────────────────────────
def test_an_old_pr_with_no_checks_is_flagged():
    assert _watcher()._ci_never_fired(_state()) is True


def test_a_pr_with_checks_is_never_flagged():
    """Running or failing CI is a different problem with its own repair path."""
    w = _watcher()
    assert w._ci_never_fired(_state(checks=[{"conclusion": "FAILURE"}])) is False
    assert w._ci_never_fired(_state(checks=[{"status": "IN_PROGRESS"}])) is False


def test_a_brand_new_pr_is_given_time():
    """An empty rollup seconds after opening is GitHub queueing, not a miss."""
    assert _watcher()._ci_never_fired(_state(created_minutes_ago=2)) is False


def test_age_comes_from_createdAt_so_a_comment_cannot_reset_it():
    """updatedAt moves on every comment and label — a chatty PR would never look
    old enough to have missed its run."""
    st = _state(created_minutes_ago=60)
    st["updatedAt"] = datetime.now(timezone.utc).isoformat()
    assert _watcher()._ci_never_fired(st) is True


def test_an_unparseable_or_missing_timestamp_does_not_act():
    """Cannot age it, so do not act on it — the recovery closes a real PR."""
    w = _watcher()
    st = _state(); st["createdAt"] = "not-a-date"
    assert w._ci_never_fired(st) is False
    st2 = _state(); st2["createdAt"] = ""
    assert w._ci_never_fired(st2) is False


# ── recovery ────────────────────────────────────────────────────────────────
class _Runner:
    def __init__(self, close_rc=0, reopen_rc=0):
        self.calls = []
        self._rcs = [close_rc, reopen_rc]

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        rc = self._rcs[min(len(self.calls) - 1, 1)]
        return type("P", (), {"returncode": rc, "stdout": "", "stderr": "boom"})()


def test_the_recovery_is_close_then_reopen():
    """Chosen over an empty commit: a commit lands in history forever to work
    around an infrastructure hiccup."""
    w = _watcher()
    w._ci_retrigger_attempts = lambda t, p: 0
    runner = _Runner()
    w._auto_merge_runner = runner
    v = w._retrigger_ci("t-1", "https://x/pull/1")
    assert v["attempted"] and v["ok"]
    assert [c[1] for c in runner.calls] == ["pr", "pr"]
    assert runner.calls[0][2] == "close" and runner.calls[1][2] == "reopen"


def test_a_failed_reopen_is_reported_not_swallowed():
    """The one outcome worse than doing nothing is leaving the PR closed."""
    w = _watcher()
    w._ci_retrigger_attempts = lambda t, p: 0
    w._auto_merge_runner = _Runner(reopen_rc=1)
    v = w._retrigger_ci("t-1", "https://x/pull/1")
    assert v["attempted"] and not v["ok"]


def test_the_cap_is_one_because_a_second_try_only_delays_the_human():
    w = _watcher()
    w._ci_retrigger_attempts = lambda t, p: 1
    runner = _Runner()
    w._auto_merge_runner = runner
    v = w._retrigger_ci("t-1", "https://x/pull/1")
    assert not v["attempted"] and "exhausted" in v["reason"]
    assert runner.calls == [], "no gh call may be made once exhausted"


def test_dry_run_never_closes_a_real_pr():
    w = _watcher()
    w.dry_run = True
    runner = _Runner()
    w._auto_merge_runner = runner
    assert w._retrigger_ci("t-1", "https://x/pull/1")["attempted"] is False
    assert runner.calls == []


# ── an empty rollup is not a workflow that never fired (kpr-watch-12) ───────
#
# MEASURED 2026-09-05 (docs/audits/kpr-watch-12-ci-never-fired-narrowing-survey.md).
# On this deployment a workflow run exists for 32-84 seconds before its first
# check run appears in the rollup, against a 30s poll interval -- so a queued run
# is indistinguishable from an absent one. The shipped grace could not cover it
# because it was aged from the PR's `createdAt`, which is spent forever after a
# PR's first 15 minutes: every push after that had ZERO grace.
#
# Replaying both narrowings over every recorded firing of this predicate: they
# withhold 2 of 30 escalations and 19 of 23 close/reopen re-triggers, and ZERO of
# the 31 firings on a branch where no workflow run had EVER fired -- the class
# #1462/#1646/#1651 are, and the class this rung exists for.
def _commit_state(*, head="deadbeef", committed_minutes_ago=1,
                  created_minutes_ago=600, checks=None):
    """A PR whose head sha is YOUNG while the PR itself is old."""
    st = _state(checks=checks, created_minutes_ago=created_minutes_ago)
    st["headRefOid"] = head
    st["commits"] = [
        {"oid": "0" * 40, "committedDate":
            (datetime.now(timezone.utc)
             - timedelta(minutes=created_minutes_ago)).isoformat()},
        {"oid": head, "committedDate":
            (datetime.now(timezone.utc)
             - timedelta(minutes=committed_minutes_ago)).isoformat()},
    ]
    return st


class _GhRunner:
    """Stands in for `gh api`. `runs` is the JSON body it hands back."""

    def __init__(self, runs="", rc=0, boom=False):
        self.calls = []
        self._runs, self._rc, self._boom = runs, rc, boom

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        if self._boom:
            raise OSError("gh is not installed")
        return type("P", (), {"returncode": self._rc,
                              "stdout": self._runs, "stderr": "boom"})()


def _run_payload(minutes_ago):
    when = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return '{"created_at": "%s"}\n' % when.isoformat().replace("+00:00", "Z")


def test_grace_is_anchored_to_the_head_sha_not_the_pr():
    """Every push gets the grace the docstring describes, not just the first.

    #2088 was 28.5 minutes old at its escalation and its head sha was 1.2
    minutes old; the shipped predicate saw only the first number.
    """
    w = _watcher()
    w._gh_runner = _GhRunner()  # must not be reached: the cheap rung answers
    st = _commit_state(committed_minutes_ago=1, created_minutes_ago=600)
    assert w._ci_never_fired(st) is False
    assert w._gh_runner.calls == [], "the forge is asked only as a last resort"


def test_a_head_sha_past_the_grace_still_escalates():
    """The rung is narrowed, never removed: #1646 and #1651 sat 63.5 and 28.6
    minutes past their own head shas with no workflow run ever created."""
    w = _watcher()
    w._gh_runner = _GhRunner(runs="")   # forge answers: no runs for this sha
    assert w._ci_never_fired(_commit_state(committed_minutes_ago=60)) is True


def test_a_queued_run_for_this_head_sha_is_not_a_workflow_that_never_fired():
    """The predicate's whole defect: a run that EXISTS but has materialised no
    check run yet. #2088's escalation landed 9.5s before its first check."""
    w = _watcher()
    w._gh_runner = _GhRunner(runs=_run_payload(2))
    assert w._ci_never_fired(_commit_state(committed_minutes_ago=60)) is False


def test_a_run_older_than_the_grace_is_STUCK_and_still_escalates():
    """A run that has produced no check in 40 minutes is not queueing. Withholding
    there would disable the rung for exactly the PR that needs it."""
    w = _watcher()
    w._gh_runner = _GhRunner(runs=_run_payload(40))
    assert w._ci_never_fired(_commit_state(committed_minutes_ago=60)) is True


def test_the_forge_probe_fails_open_so_an_outage_cannot_disable_the_rung():
    """Unmeasurable is not 'a run exists'. #1462 waited for a person precisely
    because nothing escalated; a silent probe failure must not restore that."""
    for runner in (_GhRunner(boom=True), _GhRunner(rc=1),
                   _GhRunner(runs="not json")):
        w = _watcher()
        w._gh_runner = runner
        assert w._ci_never_fired(_commit_state(committed_minutes_ago=60)) is True


def test_a_positive_probe_is_cached_and_a_negative_one_is_not():
    """A run cannot un-exist, so True is cached. False must NEVER be, or the
    32-84s materialisation gap gets frozen in for the life of the watcher."""
    w = _watcher()
    w._gh_runner = _GhRunner(runs="")
    st = _commit_state(committed_minutes_ago=60)
    assert w._ci_never_fired(st) is True
    assert w._ci_never_fired(st) is True
    assert len(w._gh_runner.calls) == 2, "a negative answer must be re-asked"

    w2 = _watcher()
    w2._gh_runner = _GhRunner(runs=_run_payload(2))
    assert w2._ci_never_fired(st) is False
    assert w2._ci_never_fired(st) is False
    assert len(w2._gh_runner.calls) == 1, "a run that exists cannot un-exist"


def test_the_probe_can_be_switched_off_without_touching_the_grace():
    w = _watcher(ci_probe_workflow_runs=False)
    w._gh_runner = _GhRunner(runs=_run_payload(2))
    assert w._ci_never_fired(_commit_state(committed_minutes_ago=60)) is True
    assert w._gh_runner.calls == []


def test_a_head_sha_absent_from_the_commits_list_falls_back_to_createdAt():
    """An unmatched head means the commits list is stale or truncated. Guessing
    the last commit would anchor the grace to a sha that is not the head."""
    w = _watcher()
    w._gh_runner = _GhRunner(runs="")
    st = _commit_state(head="deadbeef", committed_minutes_ago=1,
                       created_minutes_ago=600)
    st["headRefOid"] = "somethingelse"
    assert w._ci_never_fired(st) is True
