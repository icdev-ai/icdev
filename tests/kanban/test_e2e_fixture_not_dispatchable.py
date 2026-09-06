# CUI // SP-CTI
"""An E2E fixture card must never be dispatched to an agent.

`tests/e2e/kanban_pipeline.spec.ts` and `kanban_api.spec.ts` POST a task to the
REAL board, drive it, and delete it in `afterAll`. The cleanup works. It is not
enough, because the card is VISIBLE to the scheduler for the whole time the
spec is running, and the scheduler polls faster than the spec finishes.

MEASURED on the live board 2026-09-06, from `kanban_status_transitions`:

    11:37:41  the nightly [AUTO-RUN] Playwright suite starts
    11:59:22  kanban_pipeline.spec POSTs `task-444e9c3f6c` as `backlog`
    11:59:24  the scheduler has ALREADY promoted it: `scheduled -> in_progress`
    11:59:38  scheduler: "dispatched: agent subprocess launched"
    (later)   the spec's afterAll DELETEs the card, successfully

The promotion window was ~2 seconds. A sibling fixture (`task-e69f3b0e42`) was
dispatched 12 seconds earlier in the same run and the scheduler recorded its
outcome as "No git commits found on task branch - agent produced no committed
file-level output": a whole worker session spent on a card that no longer
existed. `task-7054abe88d` (2026-08-28) went one worse and was parked
`token_exhausted, retry 2/60` against a deleted row.

Lifetime cost, re-derivable: of 619 distinct dispatched task ids, 4 went to a
card that no longer exists (0.65%), and 2 of the 4 are from this one run.

THE FIX IS ON THE SCHEDULER SIDE, not the spec side. The spec cannot close the
window: the card has to be a real backlog row or the test proves nothing about
the real pipeline. So the board must decline to DISPATCH it, exactly the way it
already declines to dispatch a manual gate.

SURVEYED BEFORE ARMING, as CLAUDE.md requires: over all 3,950 lifetime board
rows, ZERO carry the `[E2E ` title prefix. The predicate refuses nothing that
has ever been real work. Cards that merely MENTION e2e or Playwright are real
work and MUST stay dispatchable -- they are pinned below, by their real titles.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tools.kanban.fixtures import is_test_fixture

_REPO = Path(__file__).resolve().parents[2]


# --- the predicate ---------------------------------------------------------

# The three titles the e2e specs actually POST to the board.
@pytest.mark.parametrize("title", [
    "[E2E Pipeline] Full-lifecycle proof task",
    "[E2E Test] Playwright smoke task",
    "[E2E Test] Child task (dependent)",
])
def test_spec_created_fixture_titles_are_recognised(title):
    assert is_test_fixture("task-444e9c3f6c", title) is True


# Real cards, taken verbatim from the live board. Every one is genuine work and
# every one mentions e2e or Playwright -- which is why the marker is a PREFIX
# and not a substring search.
@pytest.mark.parametrize("title", [
    "[AUTO-RUN] Playwright E2E Suite - full smoke",
    "Playwright E2E spec .claude/commands/e2e/network_canvas.md",
    "Survey whether E2E (Playwright) can become a required check",
    "Explain the ~55 local Playwright E2E failures (not the backend)",
    "Playwright V&V suite for /boundary (full ecosystem)",
    "ODC E2E: .claude/commands/e2e/observability.md skill + Playwright",
])
def test_real_work_mentioning_e2e_stays_dispatchable(title):
    assert is_test_fixture("task-e2e-e9731aca", title) is False


def test_absent_title_is_not_a_fixture():
    """An unreadable title must never be guessed into a refusal."""
    assert is_test_fixture("task-444e9c3f6c", None) is False
    assert is_test_fixture("task-444e9c3f6c", "") is False


# --- door 1: promotion -----------------------------------------------------

class _Row(dict):
    pass


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Conn:
    """Minimal stand-in for what `promote()` asks of a connection."""

    def __init__(self, rows):
        self._rows = rows
        self.updated: list[str] = []
        self.committed = False

    def execute(self, sql, params=()):
        if sql.strip().upper().startswith("UPDATE"):
            self.updated.append(params[2])
            return _Result([])
        # The promote() SELECT must be matched FIRST: it also contains the
        # substring "depends_on_task_id FROM kanban_tasks".
        if "SELECT id, title, priority" in sql:
            return _Result(self._rows)
        if "kanban_task_deps" in sql:
            return _Result([])
        return _Result([{"dep_id": None, "dep_title": None, "depends_on_task_id": None}])

    def commit(self):
        self.committed = True

    def close(self):
        pass


def _promote_with(monkeypatch, rows):
    from tools.kanban import promote_backlog_to_scheduled as mod

    conn = _Conn(rows)
    monkeypatch.setattr(mod, "get_connection", lambda *a, **k: conn)
    monkeypatch.setattr(mod, "_deps_satisfied", lambda tid, c: True)
    return mod.promote(), conn


def test_promotion_skips_an_e2e_fixture(monkeypatch):
    rows = [_Row({
        "id": "task-444e9c3f6c",
        "title": "[E2E Pipeline] Full-lifecycle proof task",
        "priority": "low", "project_id": None, "depends_on_task_id": None,
    })]
    promoted, conn = _promote_with(monkeypatch, rows)
    assert promoted == []
    assert conn.updated == []


def test_promotion_still_promotes_ordinary_work(monkeypatch):
    """The guard must not cost the board a real promotion."""
    rows = [_Row({
        "id": "task-e2e-e9731aca",
        "title": "[AUTO-RUN] Playwright E2E Suite - full smoke",
        "priority": "high", "project_id": None, "depends_on_task_id": None,
    })]
    promoted, conn = _promote_with(monkeypatch, rows)
    assert promoted == ["task-e2e-e9731aca"]
    assert conn.updated == ["task-e2e-e9731aca"]


# --- door 2: dispatch ------------------------------------------------------

def test_due_task_selection_filters_fixtures():
    """`_get_due_tasks` must apply the predicate beside the manual-gate one.

    Read from the AST rather than by booting the dispatcher: the failure mode is
    a future edit dropping the filter, and the whole point of a second door is
    that the first one (promotion) already stopped every case we can reproduce,
    so a behavioural test here would pass with the filter removed.
    """
    src = (_REPO / "tools" / "genesis" / "reflexes" / "kanban.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "_get_due_tasks"),
        None,
    )
    assert fn is not None, "_get_due_tasks not found"
    called = {
        n.func.id for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "_is_test_fixture" in called, (
        "_get_due_tasks no longer filters E2E fixture cards - the scheduler can "
        "dispatch an agent against a spec's throwaway board row again"
    )
