# CUI // SP-CTI
"""kpr-dup-09: nothing protected the transition FROM done, so last writer won.

`tests/kanban/test_completed_work_is_not_rewritten.py` covers two writers that
rewrite a completed task — the orphan sweep and pr_linker. This is the THIRD,
and it is the one that showed the rule belongs at the seam rather than in each
caller.

MEASURED 2026-08-19. cef-ui-03 was dispatched while `backlog`; its PR merged
mid-flight and pr_watcher marked it `done`; the agent then hit the verification
budget and the scheduler's outcome handler wrote `backlog` over the completion:

    23:32:54  cef-ui-03  done -> backlog          by scheduler
    23:32:56  cef-ui-02  done -> token_exhausted  by scheduler

Neither writer was buggy. pr_watcher was right that the PR merged; the scheduler
was right that its run ran out of budget. They were answering different
questions about the same row, and the row kept whichever answer arrived last.
The pair flipped done<->backlog 95 times in 5.5 hours, cascading a
failure_count reset onto cef-ci-01 each time.

kpr-dup-04 already stated the rule — "a background sweep must not un-complete
work that is already on main" — and fixed exactly one caller. `_move_task` is
the seam every writer goes through, which is where a rule about a row belongs.
"""
from __future__ import annotations

import pytest

import tools.genesis.reflexes.kanban as k

TASK = "cef-ui-03"


class _Conn:
    """Minimal stand-in: one SELECT for the prior status, then any writes."""

    def __init__(self, status="done"):
        self.status = status
        self.writes: list = []
        self.closed = False

    def execute(self, sql, params=None):
        if sql.strip().upper().startswith(("UPDATE", "INSERT", "DELETE")):
            self.writes.append((sql, params))
        return self

    def fetchone(self):
        return {"status": self.status}

    def fetchall(self):
        return []

    def commit(self):
        pass

    def close(self):
        self.closed = True


@pytest.fixture
def seam(monkeypatch):
    """Wire _move_task's collaborators and capture what it decided."""
    conn = _Conn()
    recorded: list = []
    monkeypatch.setattr(k, "get_connection", lambda *a, **kw: conn)
    monkeypatch.setattr(
        k, "_record_status_transition",
        lambda tid, frm, to, actor="", reason="": recorded.append(
            {"task": tid, "from": frm, "to": to, "actor": actor, "reason": reason}))
    return conn, recorded


def _landed(monkeypatch, verdict, detail="detail"):
    monkeypatch.setattr(k, "_work_already_landed", lambda t: (verdict, detail))


# ── the defect ─────────────────────────────────────────────────────────────
def test_a_done_task_whose_work_landed_is_not_demoted(seam, monkeypatch):
    """The exact live case: the completion must survive a losing race."""
    conn, recorded = seam
    _landed(monkeypatch, True, "its work is already on the default branch")
    k._move_task(TASK, "backlog", actor="scheduler", reason="budget exhausted")
    assert conn.writes == [], "a landed task must not be written back to backlog"
    assert recorded and recorded[0]["to"] == "REFUSED_uncomplete_backlog"


def test_the_refusal_is_recorded_not_silent(seam, monkeypatch):
    """A guard that refuses silently is indistinguishable from one that never
    ran — and this one fires inside a loop nobody was watching."""
    _, recorded = seam
    _landed(monkeypatch, True, "already on the default branch (subject evidence)")
    k._move_task(TASK, "token_exhausted", actor="scheduler")
    assert recorded[0]["to"] == "REFUSED_uncomplete_token_exhausted"
    assert "guard:" in recorded[0]["reason"]
    assert "default branch" in recorded[0]["reason"]


# ── it NARROWS, it does not disarm ─────────────────────────────────────────
def test_a_genuine_rollback_still_happens(seam, monkeypatch):
    """kpr-dup-04's 41 genuine orphans must still be caught. A task whose work
    is NOT on the branch is legitimately un-completed."""
    conn, _ = seam
    _landed(monkeypatch, False, "work is not on the default branch")
    k._move_task(TASK, "backlog", actor="scheduler")
    assert conn.writes, "a task that never landed must still roll back"


def test_cannot_answer_REFUSES_the_rollback(seam, monkeypatch):
    """kpr-dup-04's rule, at the seam: between the two ways of being wrong,
    rolling back is the one that destroys a record. An unanswerable check is
    not a licence to un-complete."""
    conn, recorded = seam
    _landed(monkeypatch, None, "landed check could not verify")
    k._move_task(TASK, "backlog", actor="scheduler")
    assert conn.writes == []
    assert recorded[0]["to"] == "REFUSED_uncomplete_backlog"


def test_a_raising_landed_check_is_not_a_licence_either(monkeypatch, seam):
    """The predicate must swallow its own errors into `None`, never propagate
    them into a status write."""
    conn, _ = seam

    def _boom(_t):
        raise RuntimeError("git unavailable")

    monkeypatch.setattr(k, "_landed_reports", lambda ids: (_ for _ in ()).throw(_boom))
    verdict, detail = k._work_already_landed(TASK)
    assert verdict is None and "refusing to un-complete" in detail


# ── the boundaries ─────────────────────────────────────────────────────────
def test_a_human_is_exempt(seam, monkeypatch):
    """An operator re-opening a task is making a decision, not losing a race."""
    conn, _ = seam
    _landed(monkeypatch, True, "landed")
    k._move_task(TASK, "backlog", actor="manual")
    assert conn.writes, "a manual re-open must not be blocked"


@pytest.mark.parametrize("target", ["cancelled", "archived"])
def test_terminal_targets_are_not_guarded(seam, monkeypatch, target):
    """done -> cancelled/archived is an operator decision about a finished task,
    not an un-completion, and this guard has no business overriding it."""
    conn, _ = seam
    _landed(monkeypatch, True, "landed")
    k._move_task(TASK, target, actor="scheduler")
    assert conn.writes, f"done -> {target} must not be guarded"


def test_a_task_that_was_not_done_is_unaffected(monkeypatch):
    """The guard keys on the PRIOR status. A backlog task moving to backlog
    must not pay for a landed check."""
    conn = _Conn(status="in_progress")
    asked = []
    monkeypatch.setattr(k, "get_connection", lambda *a, **kw: conn)
    monkeypatch.setattr(k, "_record_status_transition", lambda *a, **kw: None)
    monkeypatch.setattr(k, "_work_already_landed",
                        lambda t: asked.append(t) or (True, "landed"))
    k._move_task(TASK, "backlog", actor="scheduler")
    assert asked == [], "the guard must not consult git for a non-done task"
    assert conn.writes


# ── the placement is the point ─────────────────────────────────────────────
def test_the_guard_lives_at_the_SEAM_not_in_a_caller():
    """kpr-dup-04 fixed one caller and the same defect reappeared through
    another. Every writer reaches the row through `_move_task`; asserting the
    guard is there is asserting that the next writer inherits it."""
    import inspect

    src = inspect.getsource(k._move_task)
    assert "_work_already_landed" in src
    assert "REFUSED_uncomplete" in src


def test_the_guarded_targets_are_the_working_statuses():
    """Named explicitly so adding a status to the state machine is a decision
    about this guard too, rather than a silent hole in it."""
    assert {"backlog", "scheduled", "in_progress", "token_exhausted"} <= \
        k._UNCOMPLETE_GUARDED_TARGETS
    assert "cancelled" not in k._UNCOMPLETE_GUARDED_TARGETS
    assert "done" not in k._UNCOMPLETE_GUARDED_TARGETS
