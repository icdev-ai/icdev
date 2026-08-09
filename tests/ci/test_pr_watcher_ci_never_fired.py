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
