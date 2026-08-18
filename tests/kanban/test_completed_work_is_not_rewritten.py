# CUI // SP-CTI
"""Two background writers rewrite the record of a COMPLETED task.

Both end the same way: the task becomes dispatchable again, a second session
re-implements work that already landed, and the PR it opens can only merge as a
REVERT. #1651 was -38/+26 on rest_v1.py. #1784, measured 2026-08-17, was -10,615
lines across 73 files and would have deleted 30 files main currently has.

  1. `_detect_orphan_done_tasks` rolls a `done` task back to backlog when its
     dependency parent is not done. The intent is the E-gate incident of
     2026-04-15 — a row SET to done without the prerequisite work happening —
     and it cannot tell that apart from work that genuinely landed. MEASURED on
     kanban_status_transitions: 80 firings, 100% of them done->backlog, 61
     distinct tasks. `check_landed_bulk` over those 61 says 20 were ALREADY ON
     MAIN and 41 were not, so it is wrong 32.8% of the time.

  2. `pr_linker.link_open_prs` treats a stored link that is not OPEN as stale and
     repoints it at any open PR on the same branch. MERGED and CLOSED are not the
     same thing: a merged PR is the record of where the work landed, not a dead
     link to repair. That is how cef-fnd-05, done via #1777, came to point at
     #1784.

The fix NARROWS both; it must not disarm either. The 41 genuine orphans still
get caught, and a genuinely closed-unmerged link still gets repaired.
"""
from __future__ import annotations

import tools.kanban.pr_linker as pl


# ══ pr_linker: a MERGED link is a record, not a stale pointer ═══════════════
class _Conn:
    def __init__(self, rows):
        self._rows = rows
        self.writes = []
        self.closed = False

    def execute(self, sql, params=None):
        if sql.strip().upper().startswith("UPDATE"):
            self.writes.append((sql, params))
            return self
        return _Result(self._rows)

    def commit(self):
        pass

    def close(self):
        self.closed = True


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


def _pr(number, branch, created="2026-08-17T10:18:00Z"):
    return {
        "number": number,
        "url": f"https://github.com/o/r/pull/{number}",
        "headRefName": branch,
        "createdAt": created,
    }


def _run(rows, prs, states=None, **kw):
    # `_pr_number_of` yields a STRING, so the fixture keys must be strings too.
    # Getting this wrong made two of these tests pass for the wrong reason.
    st = {str(k): v for k, v in (states or {}).items()}
    conn = _Conn(rows)
    out = pl.link_open_prs(
        lambda: conn,
        runner=None,
        pr_lister=lambda **_: list(prs),
        pr_state=(lambda n: st.get(str(n), "CLOSED")),
        **kw,
    )
    return out, conn


def test_a_MERGED_link_is_never_overwritten():
    """The #1784 case, isolated to the merged guard: the task is still live, so
    only the state of the stored PR can save it. The work landed under #1777; an
    open PR on the same branch name is a duplicate, not a replacement."""
    rows = [{"id": "cef-fnd-05", "status": "pr_opened",
             "executor_url": "https://github.com/o/r/pull/1777"}]
    out, conn = _run(rows, [_pr(1784, "kanban/cef-fnd-05")],
                     states={1777: "MERGED"})
    assert conn.writes == [], "a merged link must never be overwritten"
    assert not out["relinked"]
    assert [e["task_id"] for e in out["settled"]] == ["cef-fnd-05"]
    assert out["settled"][0]["state"] == "MERGED"


def test_the_real_cef_fnd_05_shape_is_refused_by_BOTH_guards():
    """As it actually was on 2026-08-17: done AND merged. Either guard alone
    would have held it, which is why both are worth having — the window between
    a PR merging and the watcher marking the task done is real."""
    rows = [{"id": "cef-fnd-05", "status": "done",
             "executor_url": "https://github.com/o/r/pull/1777"}]
    out, conn = _run(rows, [_pr(1784, "kanban/cef-fnd-05")],
                     states={1777: "MERGED"})
    assert conn.writes == []
    assert not out["relinked"]
    assert [e["task_id"] for e in out["terminal"]] == ["cef-fnd-05"]


def test_a_CLOSED_unmerged_link_is_still_repaired():
    """Narrowed, not disarmed. sbx-fld-05 pointed at #1355 (CLOSED) while #1463
    was open on its branch, and stayed an unmergeable draft until a human looked."""
    rows = [{"id": "sbx-fld-05", "status": "pr_opened",
             "executor_url": "https://github.com/o/r/pull/1355"}]
    out, conn = _run(rows, [_pr(1463, "kanban/sbx-fld-05")],
                     states={1355: "CLOSED"})
    assert len(out["relinked"]) == 1
    assert out["relinked"][0]["url"].endswith("/1463")
    assert conn.writes, "the repair must actually write"


def test_a_terminal_task_is_never_relinked_even_from_a_closed_pr():
    """A done task's PR history is settled. Repointing it can only make already
    landed work look like it still has an open PR."""
    rows = [{"id": "t-done", "status": "done",
             "executor_url": "https://github.com/o/r/pull/900"}]
    out, conn = _run(rows, [_pr(901, "kanban/t-done")], states={900: "CLOSED"})
    assert conn.writes == []
    assert not out["relinked"]


def test_an_unknown_pr_state_is_treated_as_MERGED_not_as_closed():
    """Fail-safe direction: the cost of a wrong relink is a revert PR; the cost
    of a missed relink is one poll's delay for a human to notice."""
    rows = [{"id": "t-1", "status": "pr_opened",
             "executor_url": "https://github.com/o/r/pull/900"}]
    out, conn = _run(rows, [_pr(901, "kanban/t-1")], states={900: None})
    assert conn.writes == [], "unknown state must not authorise an overwrite"
    assert not out["relinked"]


def test_a_task_with_no_link_is_still_linked_normally():
    rows = [{"id": "t-2", "status": "pr_opened", "executor_url": ""}]
    out, conn = _run(rows, [_pr(902, "kanban/t-2")])
    assert len(out["linked"]) == 1
    assert conn.writes, "a first link is not a relink and is unaffected"


def test_a_terminal_task_with_no_link_is_not_linked_either():
    """Filling in a link on a done task points finished work at an open PR —
    the same defect the relink guard exists for, one branch over."""
    rows = [{"id": "t-3", "status": "done", "executor_url": ""}]
    out, conn = _run(rows, [_pr(903, "kanban/t-3")])
    assert conn.writes == []
    assert not out["linked"]


# ══ orphan sweep: landed work is not an orphan ═════════════════════════════
import tools.genesis.reflexes.kanban as kb  # noqa: E402


def test_a_done_task_whose_work_is_on_main_is_not_rolled_back(monkeypatch):
    """20 of the 61 tasks this swept had already landed. Rolling them back
    re-queues merged work for re-implementation."""
    moved = []
    monkeypatch.setattr(kb, "_move_task",
                        lambda tid, st, **kw: moved.append((tid, st)))
    monkeypatch.setattr(
        kb, "_orphan_rows",
        lambda: [{"id": "a", "parent_id": "p", "parent_status": "backlog"}])
    monkeypatch.setattr(
        kb, "_landed_reports",
        lambda ids: {"a": {"checked": True, "landed": True,
                           "confidence": "merge_ref"}})
    out = kb._detect_orphan_done_tasks()
    assert moved == [], "landed work must not be rolled back"
    assert out == [], "and it is not reported as an orphan"


def test_a_done_task_NOT_on_main_is_still_rolled_back(monkeypatch):
    """The 41. This is the E-gate case the sweep exists for."""
    moved = []
    monkeypatch.setattr(kb, "_move_task",
                        lambda tid, st, **kw: moved.append((tid, st)))
    monkeypatch.setattr(
        kb, "_orphan_rows",
        lambda: [{"id": "b", "parent_id": "p", "parent_status": "backlog"}])
    monkeypatch.setattr(
        kb, "_landed_reports",
        lambda ids: {"b": {"checked": True, "landed": False}})
    out = kb._detect_orphan_done_tasks()
    assert moved == [("b", "backlog")]
    assert [o["id"] for o in out] == ["b"]


def test_a_landed_check_that_could_not_RUN_does_not_roll_back(monkeypatch):
    """A sweep that could not verify is not a sweep that found an orphan — the
    same rule red_first_gate encodes as exit 2. The rollback is the destructive
    direction, so `checked: False` holds rather than proceeds."""
    moved = []
    monkeypatch.setattr(kb, "_move_task",
                        lambda tid, st, **kw: moved.append((tid, st)))
    monkeypatch.setattr(
        kb, "_orphan_rows",
        lambda: [{"id": "c", "parent_id": "p", "parent_status": "backlog"}])
    monkeypatch.setattr(
        kb, "_landed_reports",
        lambda ids: {"c": {"checked": False, "landed": False}})
    out = kb._detect_orphan_done_tasks()
    assert moved == []
    assert out == []


def test_an_unverifiable_rollback_is_REPORTED_not_silent(monkeypatch, caplog):
    """Holding silently would turn the sweep off without anyone deciding to."""
    monkeypatch.setattr(kb, "_move_task", lambda *a, **k: None)
    monkeypatch.setattr(
        kb, "_orphan_rows",
        lambda: [{"id": "c", "parent_id": "p", "parent_status": "backlog"}])
    monkeypatch.setattr(
        kb, "_landed_reports", lambda ids: {"c": {"checked": False}})
    kb.logger.propagate = True  # icdev_logger detaches from root by default
    with caplog.at_level("WARNING", logger=kb.logger.name):
        kb._detect_orphan_done_tasks()
    assert any("could not verify" in r.getMessage().lower()
               or "unverified" in r.getMessage().lower()
               for r in caplog.records)


def test_a_landed_orphan_reports_the_DEPENDENCY_as_the_anomaly(monkeypatch,
                                                               caplog):
    """A done task with an unfinished parent and merged work means the graph is
    mis-declared. That is worth saying; it is not worth un-completing the task."""
    monkeypatch.setattr(kb, "_move_task", lambda *a, **k: None)
    monkeypatch.setattr(
        kb, "_orphan_rows",
        lambda: [{"id": "a", "parent_id": "p", "parent_status": "backlog"}])
    monkeypatch.setattr(
        kb, "_landed_reports",
        lambda ids: {"a": {"checked": True, "landed": True,
                           "confidence": "merge_ref"}})
    kb.logger.propagate = True
    with caplog.at_level("WARNING", logger=kb.logger.name):
        kb._detect_orphan_done_tasks()
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "a" in msgs and "p" in msgs
    assert "landed" in msgs.lower() or "on main" in msgs.lower()


def test_the_sweep_asks_ONCE_for_the_whole_batch(monkeypatch):
    """One `git log --grep` per sweep, not one per task — the same batching
    pr_watcher._landed_map already uses."""
    calls = []
    monkeypatch.setattr(kb, "_move_task", lambda *a, **k: None)
    monkeypatch.setattr(
        kb, "_orphan_rows",
        lambda: [{"id": x, "parent_id": "p", "parent_status": "backlog"}
                 for x in ("a", "b", "c")])

    def _batch(ids):
        calls.append(list(ids))
        return {i: {"checked": True, "landed": False} for i in ids}

    monkeypatch.setattr(kb, "_landed_reports", _batch)
    kb._detect_orphan_done_tasks()
    assert len(calls) == 1
    assert sorted(calls[0]) == ["a", "b", "c"]
