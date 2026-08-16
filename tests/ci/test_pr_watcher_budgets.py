# CUI // SP-CTI
"""Recovery budgets are per-PR, and the HITL alert is deduped.

A task that burned 5 resumes on an abandoned PR used to inherit 5/5 on its NEXT
one and could never be auto-recovered again — measured 2026-08-09, sbx-fld-05 sat
at 5/5 resumes and 2/2 rebases while holding a clean, green PR the watcher would
have refused to help. A new PR is a new attempt.
"""
from __future__ import annotations

import json

import tools.ci.pr_watcher as pw


class _Conn:
    def __init__(self, rows):
        self._rows = rows
        self.inserts = []

    def execute(self, sql, params=None):
        self._sql = sql
        if sql.strip().upper().startswith("SELECT"):
            return self
        self.inserts.append((sql, params))
        return self

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return None

    def commit(self):
        pass

    def close(self):
        pass


def _row(task_id, pr_url, action="escalate"):
    return {"d": json.dumps({"task_id": task_id, "pr_url": pr_url, "action": action})}


def _watcher(conn):
    return pw.PRWatcher(config={}, get_connection=lambda: conn)


def test_budget_counts_only_the_current_pr():
    conn = _Conn([_row("t-1", "https://x/pull/1"), _row("t-1", "https://x/pull/1"),
                  _row("t-1", "https://x/pull/2")])
    w = _watcher(conn)
    assert w._count_audit_actions("t-1", ("pr_watcher.resume",),
                                  pr_url="https://x/pull/2") == 1
    assert w._count_audit_actions("t-1", ("pr_watcher.resume",),
                                  pr_url="https://x/pull/1") == 2


def test_a_new_pr_starts_the_budget_fresh():
    """The whole point: a superseded PR's failures must not poison the next one."""
    conn = _Conn([_row("t-1", "https://x/pull/1") for _ in range(5)])
    w = _watcher(conn)
    assert w._count_audit_actions("t-1", ("pr_watcher.resume",),
                                  pr_url="https://x/pull/9") == 0


def test_another_tasks_rows_are_never_counted():
    """The payload embeds reasons naming other tasks' PRs, so a substring scan
    over the blob over-counts — it matched six tasks where one had escalated."""
    conn = _Conn([_row("t-2", "https://x/pull/1")])
    w = _watcher(conn)
    assert w._count_audit_actions("t-1", ("pr_watcher.resume",),
                                  pr_url="https://x/pull/1") == 0


def test_omitting_pr_url_keeps_the_old_lifetime_count():
    """Callers that have no PR in hand still get the task-wide number."""
    conn = _Conn([_row("t-1", "https://x/pull/1"), _row("t-2", "https://x/pull/2")])
    w = _watcher(conn)
    assert w._count_audit_actions("t-1", ("pr_watcher.resume",)) == 2


# ---------------------------------------------------------------------------
# A resume must be given time to work before the next one is spent (2026-08-16)
# ---------------------------------------------------------------------------
# max_resume_cycles_per_task is a budget of ATTEMPTS. Nothing stopped it being
# spent at POLL speed: the watcher injected context, the next poll ~45s later
# saw the same classification and injected again, and the whole budget was gone
# in about three minutes. Measured on the live board — #1742 burned 17:17:43 ->
# 17:20:56 and #1744 17:54:20 -> 17:57:29, both then escalating to "manual
# intervention required" while fully green and merging cleanly under
# `git merge-tree`. No agent reads a message and pushes a fix in 45 seconds, so
# those were not five attempts; they were one attempt and four wasted entries.

from datetime import datetime, timedelta, timezone  # noqa: E402


class _StampedConn(_Conn):
    """A connection whose rows carry created_at, as audit_trail's do."""


def _stamped_row(task_id, pr_url, age_seconds, action="pr_watcher.resume"):
    return {
        "d": json.dumps({"task_id": task_id, "pr_url": pr_url, "action": action}),
        "created_at": datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
    }


def test_age_of_the_last_resume_is_reported():
    conn = _StampedConn([_stamped_row("t1", "http://pr/1", 120)])
    age = _watcher(conn)._seconds_since_last_resume("t1", pr_url="http://pr/1")
    assert age is not None and 100 < age < 200, age


def test_a_resume_for_another_PR_does_not_count_as_this_one():
    """The budget is per-PR; so is the cooldown, or a busy task starves."""
    conn = _StampedConn([_stamped_row("t1", "http://pr/OTHER", 5)])
    assert _watcher(conn)._seconds_since_last_resume("t1", pr_url="http://pr/1") is None


def test_no_prior_resume_means_no_wait():
    """A first attempt must never be delayed."""
    conn = _StampedConn([])
    assert _watcher(conn)._seconds_since_last_resume("t1", pr_url="http://pr/1") is None


def test_an_unreadable_clock_does_not_block_recovery():
    """Fail toward acting. A watcher that cannot read a timestamp must still work."""
    class _Broken(_Conn):
        def execute(self, sql, params=None):
            raise RuntimeError("audit_trail unavailable")

    assert _watcher(_Broken([]))._seconds_since_last_resume("t1", pr_url="p") is None


def test_the_cooldown_default_is_long_enough_to_be_an_attempt():
    """45 seconds is a poll interval; it is not an opportunity to fix a PR.

    Pinned as a floor rather than an exact value: the number may be tuned, but
    dropping it back to poll speed reintroduces the defect — five cycles gone in
    three minutes.
    """
    assert pw.RESUME_COOLDOWN_SECONDS >= 300, (
        "a resume budget spent faster than an agent can respond is not a budget"
    )


def test_the_call_site_CONSULTS_the_cooldown_before_injecting():
    """A helper nothing calls is the defect this codebase ships most.

    An earlier version of this test only proved _seconds_since_last_resume
    EXISTS — and passed with the call site deleted. Assert the ordering that
    makes it load-bearing: the cooldown is consulted before the resume context
    is prepared, and a hit takes the `continue` instead of injecting.
    """
    import inspect

    src = inspect.getsource(pw.PRWatcher.poll_once)
    consult = src.find("_seconds_since_last_resume")
    inject = src.find("prepare_resume_context")
    assert consult != -1, "poll_once never consults the resume cooldown"
    assert inject != -1
    assert consult < inject, (
        "the cooldown must be checked BEFORE the resume is prepared, or the "
        "budget is spent at poll speed again"
    )
    window = src[consult:inject]
    assert "continue" in window, (
        "a cooldown hit must skip the injection, not merely be measured"
    )
    assert "resume_cooldown_seconds" in src, "the interval must be configurable"


def test_the_escalate_branch_CONSULTS_prior_escalations_before_re_alerting():
    """Escalate once per PR, not once per poll.

    The branch re-fired every cycle: #1742 and #1744 were re-escalated every
    ~42s for hours, and pr_watcher.escalate stood at 42,902 rows — nearly all of
    it a handful of PRs re-announcing, each one re-sending the HITL alert, which
    is how a "manual intervention required" notification stops being read.

    Asserted as ordering, not presence: the count must be taken BEFORE the alert
    fires, and guard the path to it.
    """
    import inspect

    src = inspect.getsource(pw.PRWatcher.poll_once)
    cap = src.index("resume cap reached")
    head = src.rindex("if cycle >= max_cycles", 0, cap)
    block = src[head:cap]
    assert "_count_audit_actions" in block, (
        "the escalate branch must consult prior escalations for this PR"
    )
    assert "continue" in block, (
        "an already-escalated PR must return quietly rather than re-alerting"
    )
    alert = src.index("_hitl_alert", cap)
    assert src.index("_count_audit_actions", head) < alert, (
        "the prior-escalation check must run BEFORE the HITL alert is re-sent"
    )


# ---------------------------------------------------------------------------
# The resume budget is refunded when a phantom conflict spent it (2026-08-16)
# ---------------------------------------------------------------------------
# The rebase budget already had this protection; the resume budget did not. A
# stale CONFLICTING verdict keeps the PR classified MERGE_CONFLICT, so it takes
# the resume path too — and once the cap is reached it escalates to "manual
# intervention required" PERMANENTLY, because nothing ever gave a resume back.
# #1742 and #1744 sat there fully green, 0 of 10 checks failing, merging cleanly
# under `git merge-tree`.


class _ActionAwareConn(_Conn):
    """A fake that honours the `action IN (...)` filter the real query applies.

    The simpler fake returns every row for every query, so a test using it
    cannot tell a `resume` row from a `resume_refund` one — it would pass
    whatever the arithmetic did. That is the vacuous-test trap, so this one
    filters the way audit_trail does.
    """

    def execute(self, sql, params=None):
        self._sql = sql
        if not sql.strip().upper().startswith("SELECT"):
            self.inserts.append((sql, params))
            return self
        wanted = {p for p in (params or ()) if isinstance(p, str) and p.startswith("pr_watcher.")}
        self._filtered = [
            r for r in self._rows
            if not wanted or json.loads(r["d"]).get("action") in {w.split(".", 1)[1] for w in wanted}
            or json.loads(r["d"]).get("action") in wanted
        ]
        return self

    def fetchall(self):
        return getattr(self, "_filtered", self._rows)


def _act_row(task_id, pr_url, action):
    return {"d": json.dumps({"task_id": task_id, "pr_url": pr_url, "action": action})}


def test_one_refund_restores_one_full_budget_not_one_cycle():
    """A single extra poll against a stale verdict achieves nothing."""
    rows = [_act_row("t1", "p1", "pr_watcher.resume") for _ in range(5)]
    rows.append(_act_row("t1", "p1", "pr_watcher.resume_refund"))
    w = pw.PRWatcher(config={"max_resume_cycles_per_task": 5},
                     get_connection=lambda: _ActionAwareConn(rows))
    assert w._resume_cycle("t1", pr_url="p1") == 0, (
        "5 spent minus a 5-cycle refund must be 0 — a full second run, not one poll"
    )


def test_the_refund_is_floored_at_zero():
    """A refund restores a budget; it never grants one."""
    rows = [_act_row("t1", "p1", "pr_watcher.resume"),
            _act_row("t1", "p1", "pr_watcher.resume_refund")]
    w = pw.PRWatcher(config={"max_resume_cycles_per_task": 5},
                     get_connection=lambda: _ActionAwareConn(rows))
    assert w._resume_cycle("t1", pr_url="p1") == 0


def test_without_a_refund_the_count_is_unchanged():
    """Guards the arithmetic against over-crediting the common case."""
    rows = [_act_row("t1", "p1", "pr_watcher.resume") for _ in range(3)]
    w = pw.PRWatcher(config={"max_resume_cycles_per_task": 5},
                     get_connection=lambda: _ActionAwareConn(rows))
    assert w._resume_cycle("t1", pr_url="p1") == 3


def test_the_call_site_refunds_ONLY_when_the_budget_is_exhausted():
    """The one-shot must not be spent on a PR that still has cycles left.

    Asserted on the call site, not the helper: a refund method nothing calls at
    the right moment is the defect this codebase ships most.
    """
    import inspect

    src = inspect.getsource(pw.PRWatcher.poll_once)
    call = src.find("_refund_resume_budget")
    assert call != -1, "poll_once never refunds the resume budget"

    guard = src[max(0, call - 700):call]
    assert "max_resume_cycles_per_task" in guard, (
        "the refund must be gated on the budget actually being exhausted"
    )
    assert "pr_watcher.resume_refund" in guard, (
        "the refund must be once per PR — bounded, so a forge that keeps lying "
        "cannot buy unlimited attempts"
    )
    # It has to happen before the cycle count that decides escalate-vs-resume.
    decide = src.index("cycle = self._resume_cycle(")
    assert call < decide, (
        "the refund must be issued BEFORE the cycle is recomputed, or it cannot "
        "take effect until the next poll"
    )


def test_the_refund_only_fires_for_a_conflict_proved_phantom():
    """Never refund on a REAL conflict — that would loop forever on real work."""
    import inspect

    src = inspect.getsource(pw.PRWatcher.poll_once)
    phantom = src.index("_conflict_is_real")
    call = src.index("_refund_resume_budget")
    assert phantom < call, (
        "the refund must sit inside the branch guarded by _conflict_is_real"
    )
