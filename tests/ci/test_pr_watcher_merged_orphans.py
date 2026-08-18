# CUI // SP-CTI
"""kpr-watch-09: a merged PR whose task is not done must not be invisible forever.

`pr_watcher.list_pr_tasks` polls BY STATUS. `backlog` is absent, and nothing
anywhere reconciled the other direction — "a merged PR whose task is not done".
So once a task carrying a live PR landed in a status outside the polled set, the
only component that closes the loop stopped looking at it and the board could
never self-correct.

MEASURED: kpr-watch-01 was dispatched 16:09 on 2026-08-16, opened PR #1744 at
16:27, and was reaped to `backlog` at 16:35 — eight minutes AFTER its PR
existed. The PR merged two days later with nothing watching. The task still read
`backlog` while its work was on main, and it blocked five siblings behind it.
One live case out of 424 PR-carrying tasks: not frequent, but PERMANENT and
SILENT when it happens, and it took a human noticing.
"""
from __future__ import annotations

import pytest

import tools.ci.pr_watcher as pw


# ── fakes ───────────────────────────────────────────────────────────────────
class _Row(dict):
    """A row that indexes by column name, as psycopg2's dict factory does."""


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Conn:
    """A kanban_tasks table just real enough for the two listers and the write."""

    def __init__(self, tasks):
        self.tasks = tasks
        self.updates = []
        self.closed = False

    def execute(self, sql, params=()):
        text = " ".join(sql.split())
        if text.startswith("UPDATE kanban_tasks SET status"):
            status, _updated, task_id = params
            self.updates.append((task_id, status))
            for t in self.tasks:
                if t["id"] == task_id:
                    t["status"] = status
            return _Cursor([])
        if text.startswith("SELECT title FROM kanban_tasks"):
            return _Cursor([_Row(title=t["title"])
                            for t in self.tasks if t["id"] == params[0]])
        if "FROM kanban_tasks" in text and "status NOT IN" in text:
            return _Cursor([_Row(t) for t in self.tasks
                            if t["status"] not in set(params)])
        if "FROM kanban_tasks" in text and "status IN" in text:
            return _Cursor([_Row(t) for t in self.tasks
                            if t["status"] in set(params)])
        if "FROM kanban_tasks" in text and "WHERE id" in text:
            return _Cursor([_Row(t) for t in self.tasks if t["id"] == params[0]])
        # INSERTs (status transitions, audit) are accepted and ignored.
        return _Cursor([])

    def commit(self):
        pass

    def close(self):
        self.closed = True


def _task(task_id="kpr-watch-01", *, status="backlog",
          url="https://github.com/o/r/pull/1744", title="A task", description=""):
    return {"id": task_id, "title": title, "description": description,
            "status": status, "executor_url": url}


def _pr(state="MERGED", base="main"):
    return {"state": state, "baseRefName": base, "url": "https://github.com/o/r/pull/1744"}


class _W(pw.PRWatcher):
    def __init__(self, tasks, pr, **config):
        cfg = {"reconcile_merged_orphans": True}
        cfg.update(config)
        self.conn = _Conn(tasks)
        super().__init__(config=cfg, get_connection=lambda: self.conn)
        self.reclaimed = []
        self._fetch_state = lambda url: pr
        self._default_branch = lambda: "main"
        self._audit = lambda action: None
        self.reclaim_worktree = lambda tid: (
            self.reclaimed.append(tid) or {"reclaimed": True})

    def _connection(self):
        return lambda: self.conn


def _sweep(w):
    report = pw.WatcherReport(started_at="", finished_at="", tasks_checked=0)
    count = w._sweep_merged_orphans(report)
    return count, report


# ── the defect ──────────────────────────────────────────────────────────────
def test_the_polled_query_cannot_see_a_backlog_task_with_a_pr():
    """The premise. `backlog` is not in the polled set, so nothing above the
    reconciler ever looks at kpr-watch-01 again."""
    conn = _Conn([_task(status="backlog")])
    assert pw.list_pr_tasks(lambda: conn) == []
    assert "backlog" not in pw.POLLED_STATUSES


def test_a_merged_pr_completes_its_backlog_task():
    """The measured case: PR #1744 merged, task still reading `backlog`."""
    w = _W([_task(status="backlog")], _pr())
    count, report = _sweep(w)
    assert count == 1
    assert w.conn.updates == [("kpr-watch-01", "done")]
    assert report.orphans_checked == 1
    assert [(a.task_id, a.action) for a in report.actions] == [
        ("kpr-watch-01", "merge")]
    assert "backlog" in report.actions[0].reason


@pytest.mark.parametrize("status", ["backlog", "token_exhausted", "suggested",
                                    "decomposed", "validating"])
def test_every_unpolled_non_terminal_status_is_reconciled(status):
    """The entry paths are many — stale reaper, PR-flow rollback, auto-revive,
    orphan sweep, a manual move — so the set is the COMPLEMENT of the polled
    one, not a list of the statuses anybody happened to think of."""
    w = _W([_task(status=status)], _pr())
    assert _sweep(w)[0] == 1
    assert w.conn.updates == [("kpr-watch-01", "done")]


def test_the_url_may_come_from_the_description_like_the_poller():
    """Both listers share `_task_row`, so they cannot disagree about which
    tasks even have a PR."""
    t = _task(status="backlog", url="")
    t["description"] = "opened https://github.com/o/r/pull/1744 for review"
    w = _W([t], _pr())
    assert _sweep(w)[0] == 1


# ── the refusals ────────────────────────────────────────────────────────────
def test_a_polled_task_is_left_to_the_watch_loop():
    """It is being actively serviced there — resumes, rebases, the enforced
    done-gate. Completing it here would skip all of that."""
    for status in pw.POLLED_STATUSES:
        w = _W([_task(status=status)], _pr())
        count, report = _sweep(w)
        assert count == 0, status
        assert w.conn.updates == [], status
        assert report.orphans_checked == 0, status


def test_a_terminal_task_is_never_touched():
    """`done` is already the outcome; `failed` is a decision somebody made, and
    a merge is not new information about whether the work was acceptable."""
    for status in pw.TERMINAL_STATUSES:
        w = _W([_task(status=status)], _pr())
        assert _sweep(w)[0] == 0, status
        assert w.conn.updates == [], status


@pytest.mark.parametrize("state", ["OPEN", "CLOSED", "", None])
def test_only_a_merged_pr_completes_anything(state):
    """Not a reviver. An OPEN PR on an unpolled task is a different defect with
    a different owner; pulling it back here would fight that writer every poll."""
    w = _W([_task(status="backlog")], _pr(state=state))
    count, report = _sweep(w)
    assert count == 0
    assert w.conn.updates == []
    assert report.orphans_checked == 1  # looked at, and said so


def test_a_pr_merged_into_a_feature_branch_is_refused():
    """MERGED does not mean "on main". The auto-merge path already refuses a
    non-default base; reading a merge is the mirror of that."""
    w = _W([_task(status="backlog")], _pr(base="feat/other"))
    assert _sweep(w)[0] == 0
    assert w.conn.updates == []


def test_a_manual_gate_is_never_completed_by_a_merge():
    """A gate is a sentinel, not work. The refusal lives in `_set_task_status`
    and so covers this caller too — and no `merge` action is reported for a
    completion that did not happen."""
    w = _W([_task("hgx-gate-01", status="backlog", title="Gate: manual hold")],
           _pr())
    count, report = _sweep(w)
    assert count == 0
    assert w.conn.updates == []
    assert report.actions == []


def test_the_sweep_can_be_switched_off():
    w = _W([_task(status="backlog")], _pr(), reconcile_merged_orphans=False)
    count, report = _sweep(w)
    assert count == 0
    assert report.orphans_checked == 0


# ── observability ───────────────────────────────────────────────────────────
def test_dry_run_still_looks_and_still_reports():
    """`_sweep_unlinked_prs` returns immediately under --dry-run, which is why
    the pipeline could merge a PR but could not say why one was not merging. A
    reconciler that reports nothing in the mode an operator uses to ASK is not
    observable."""
    w = _W([_task(status="backlog")], _pr())
    w.dry_run = True
    count, report = _sweep(w)
    assert count == 1
    assert w.conn.updates == []           # wrote nothing
    assert [a.action for a in report.actions] == ["dry_run"]
    assert report.orphans_checked == 1


def test_a_forge_error_on_one_pr_does_not_stop_the_sweep():
    def _boom_then_merged(url):
        if url.endswith("/1"):
            raise RuntimeError("gh pr view failed")
        return _pr()

    w = _W([_task("a", status="backlog", url="https://github.com/o/r/pull/1"),
            _task("b", status="backlog", url="https://github.com/o/r/pull/2")],
           _pr())
    w._fetch_state = _boom_then_merged
    assert _sweep(w)[0] == 1
    assert w.conn.updates == [("b", "done")]


def test_an_unreadable_board_is_not_a_clean_sweep():
    """A listing failure must never read as "nothing to reconcile" — and must
    never stop the poll either."""
    w = _W([_task(status="backlog")], _pr())

    def _broken():
        raise RuntimeError("no database")

    w._connection = lambda: _broken
    count, report = _sweep(w)
    assert count == 0
    assert report.orphans_checked == 0


def test_the_report_counts_orphans_apart_from_polled_tasks():
    """Folding them into one number would make a healthy board (0 orphans) read
    the same as a board whose reconciler never ran."""
    assert "orphans_checked" in pw.WatcherReport(
        started_at="", finished_at="", tasks_checked=0).to_dict()


# ── the same trap, a second time ────────────────────────────────────────────
def test_the_done_branch_no_longer_carries_a_status_allow_list():
    """`list_pr_tasks(task_id=...)` has NO status filter, so `--task
    kpr-watch-01` did reach the DONE branch with a merged PR — and then declined
    to complete it, because 'backlog' was not on a hardcoded allow-list there.
    Two independent status gates, both of which had to name every live status."""
    import inspect

    src = inspect.getsource(pw.PRWatcher.poll_once)
    assert 'if merged and task.get("status") not in TERMINAL_STATUSES:' in src
    assert '"pr_opened", "in_progress", "ci_failed",' not in src
