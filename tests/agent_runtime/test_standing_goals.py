# CUI // SP-CTI
"""Unit tests for standing goals (hgx-goal-01).

DB-independent: ``tools.db.storage.get_connection`` is monkeypatched (shim-aware,
via ``importlib.import_module``) with a ``tests/_sql_compat`` connection, so the
``%s`` placeholders the module authors for PostgreSQL are rewritten exactly the
way the real storage layer rewrites them. A bare sqlite3 handle here would make
every statement raise inside the module's own ``except`` and the tests would
assert against a no-op they caused themselves.
"""
from __future__ import annotations

import importlib
import sqlite3

import pytest

from tests._sql_compat import connect as translating_connect

import tools.agent_runtime.standing_goals as sg

#: One translating connection per test, created lazily so the patched factory —
#: not a fixture-local lambda — is what the runtime is handed.
_DB: dict = {}


def _get_connection(*_args, **_kwargs):
    """Stand-in for ``tools.db.storage.get_connection`` (shared, translating)."""
    if "conn" not in _DB:
        _DB["conn"] = translating_connect(":memory:")
    return _DB["conn"]


def _patch_storage(monkeypatch):
    """Point ``get_connection`` at a fresh, placeholder-translating in-memory DB."""
    _DB.clear()
    storage = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(storage, "get_connection", _get_connection)


@pytest.fixture()
def manager(monkeypatch):
    """A GoalManager backed by a shared in-memory SQLite DB."""
    _patch_storage(monkeypatch)
    return sg.GoalManager(user_id="op", tenant_id="acme")


@pytest.fixture()
def dropped(monkeypatch):
    """A GoalManager whose table does not exist and cannot be self-created.

    This is the "migration not run / table dropped" shape: the DDL is suppressed
    so ``_ensure_schema`` cannot paper over it.
    """
    _patch_storage(monkeypatch)
    monkeypatch.setattr(sg.GoalManager, "_ensure_schema", staticmethod(lambda c: None))
    return sg.GoalManager(user_id="op", tenant_id="acme")


# ---------------------------------------------------------------------------
# status vocabulary + transition table
# ---------------------------------------------------------------------------
def test_status_vocabulary_is_closed():
    assert {s.value for s in sg.GoalStatus} == {
        "pending", "active", "paused", "blocked", "completed", "cancelled",
    }


def test_coerce_rejects_unknown_status():
    assert sg.GoalStatus.coerce("ACTIVE") is sg.GoalStatus.ACTIVE
    assert sg.GoalStatus.coerce(sg.GoalStatus.PAUSED) is sg.GoalStatus.PAUSED
    with pytest.raises(ValueError):
        sg.GoalStatus.coerce("almost-done")


@pytest.mark.parametrize(
    "current,requested,allowed",
    [
        ("pending", "active", True),
        ("pending", "completed", False),   # must be activated first
        ("pending", "paused", False),
        ("active", "paused", True),
        ("active", "blocked", True),
        ("active", "completed", True),
        ("paused", "active", True),
        ("paused", "completed", False),
        ("blocked", "active", True),
        ("completed", "active", False),    # terminal
        ("cancelled", "active", False),    # terminal
        ("completed", "cancelled", False),
        ("active", "active", True),        # idempotent no-op
    ],
)
def test_transition_table(current, requested, allowed):
    assert sg.can_transition(current, requested) is allowed


def test_can_transition_is_false_for_garbage():
    assert sg.can_transition("nonsense", "active") is False


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
def test_create_and_get_roundtrip(manager):
    goal = manager.create(
        "Keep the migration backlog at zero",
        detail="Check pending migrations each session",
        tags=["ops", "db"],
        metadata={"origin": "operator"},
        context_id="ctx-1",
        priority=80,
    )
    assert goal is not None
    assert goal.status is sg.GoalStatus.PENDING
    assert goal.goal_id.startswith("goal-")

    fetched = manager.get(goal.goal_id)
    assert fetched is not None
    assert fetched.title == "Keep the migration backlog at zero"
    assert fetched.tags == ["ops", "db"]
    assert fetched.metadata == {"origin": "operator"}
    assert fetched.context_id == "ctx-1"
    assert fetched.priority == 80
    assert fetched.classification == "CUI"
    assert fetched.to_dict()["status"] == "pending"


def test_create_rejects_empty_title(manager):
    with pytest.raises(ValueError):
        manager.create("   ")


def test_create_rejects_terminal_initial_status(manager):
    with pytest.raises(ValueError):
        manager.create("already done", status="completed")


def test_get_is_scoped_to_owner(manager, monkeypatch):
    goal = manager.create("mine")
    other = sg.GoalManager(user_id="someone-else", tenant_id="acme")
    assert other.get(goal.goal_id) is None
    assert other.list_goals() == []


def test_delete_removes_the_row(manager):
    goal = manager.create("temporary")
    assert manager.delete(goal.goal_id) is True
    assert manager.get(goal.goal_id) is None
    assert manager.delete(goal.goal_id) is False


# ---------------------------------------------------------------------------
# listing
# ---------------------------------------------------------------------------
def test_list_active_and_ordering(manager):
    low = manager.create("low", priority=10)
    high = manager.create("high", priority=90)
    manager.create("still pending")
    manager.activate(low.goal_id)
    manager.activate(high.goal_id)

    active = manager.list_active()
    assert [g.title for g in active] == ["high", "low"]
    assert manager.list_active(limit=1)[0].title == "high"


def test_priority_zero_survives_the_roundtrip(manager):
    """`or _DEFAULT_PRIORITY` in the row mapper would turn 0 into 50."""
    goal = manager.create("lowest", priority=0)
    assert manager.get(goal.goal_id).priority == 0


def test_list_for_context_includes_global_goals(manager):
    scoped = manager.create("scoped", context_id="ctx-1")
    glob = manager.create("global")
    elsewhere = manager.create("elsewhere", context_id="ctx-2")
    for g in (scoped, glob, elsewhere):
        manager.activate(g.goal_id)

    titles = {g.title for g in manager.list_for_context("ctx-1")}
    assert titles == {"scoped", "global"}

    strict = manager.list_for_context("ctx-1", include_global=False)
    assert [g.title for g in strict] == ["scoped"]


def test_list_for_context_skips_paused_by_default(manager):
    paused = manager.create("paused one", context_id="ctx-1")
    manager.activate(paused.goal_id)
    manager.pause(paused.goal_id)
    assert manager.list_for_context("ctx-1") == []
    assert len(manager.list_for_context("ctx-1", statuses=None)) == 1


def test_render_active_is_capped(manager):
    for i in range(8):
        g = manager.create(f"goal {i}", priority=i)
        manager.activate(g.goal_id)
    rendered = manager.render_active(limit=3)
    assert rendered.count("  - goal ") == 3
    assert "goal 7" in rendered and "goal 0" not in rendered


def test_render_active_empty_when_nothing_active(manager):
    manager.create("pending only")
    assert manager.render_active() == ""


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------
def test_full_lifecycle(manager):
    goal = manager.create("ship it")

    activated = manager.activate(goal.goal_id)
    assert activated.status is sg.GoalStatus.ACTIVE
    assert activated.activated_at

    blocked = manager.block(goal.goal_id, reason="waiting on CI")
    assert blocked.status is sg.GoalStatus.BLOCKED
    assert blocked.blocked_reason == "waiting on CI"

    unblocked = manager.activate(goal.goal_id)
    assert unblocked.blocked_reason == ""

    paused = manager.pause(goal.goal_id)
    assert paused.status is sg.GoalStatus.PAUSED

    manager.activate(goal.goal_id)
    done = manager.complete(goal.goal_id)
    assert done.status is sg.GoalStatus.COMPLETED
    assert done.progress == 100
    assert done.completed_at
    assert manager.get(goal.goal_id).status is sg.GoalStatus.COMPLETED


def test_invalid_transition_is_rejected(manager):
    goal = manager.create("no skipping")
    with pytest.raises(sg.InvalidGoalTransition):
        manager.complete(goal.goal_id)          # pending -> completed
    assert manager.get(goal.goal_id).status is sg.GoalStatus.PENDING

    manager.activate(goal.goal_id)
    manager.cancel(goal.goal_id)
    with pytest.raises(sg.InvalidGoalTransition):
        manager.activate(goal.goal_id)          # cancelled is terminal
    assert manager.get(goal.goal_id).status is sg.GoalStatus.CANCELLED


def test_transition_on_missing_goal_returns_none(manager):
    assert manager.activate("goal-does-not-exist") is None


def test_transition_rejects_unknown_status(manager):
    goal = manager.create("x")
    with pytest.raises(ValueError):
        manager.transition(goal.goal_id, "almost")


# ---------------------------------------------------------------------------
# progress
# ---------------------------------------------------------------------------
def test_update_progress_clamps_and_notes(manager):
    goal = manager.create("measure me")
    manager.activate(goal.goal_id)

    assert manager.update_progress(goal.goal_id, 42, note="half way").progress == 42
    assert manager.update_progress(goal.goal_id, 500).progress == 100
    assert manager.update_progress(goal.goal_id, -5).progress == 0
    assert manager.update_progress(goal.goal_id, "junk").progress == 0

    stored = manager.get(goal.goal_id)
    assert stored.progress == 0
    assert stored.metadata["progress_notes"][0]["note"] == "half way"
    # progress alone never changes status
    assert stored.status is sg.GoalStatus.ACTIVE


def test_progress_on_terminal_goal_is_rejected(manager):
    goal = manager.create("finished")
    manager.activate(goal.goal_id)
    manager.complete(goal.goal_id)
    with pytest.raises(ValueError):
        manager.update_progress(goal.goal_id, 50)


def test_update_progress_on_missing_goal_returns_none(manager):
    assert manager.update_progress("goal-nope", 10) is None


# ---------------------------------------------------------------------------
# degradation — the table is absent, or the DB is unreachable
# ---------------------------------------------------------------------------
def test_degrades_when_table_is_missing(dropped):
    assert dropped.create("no table here") is None
    assert dropped.get("goal-1") is None
    assert dropped.list_goals() == []
    assert dropped.list_active() == []
    assert dropped.list_for_context("ctx-1") == []
    assert dropped.activate("goal-1") is None
    assert dropped.update_progress("goal-1", 10) is None
    assert dropped.delete("goal-1") is False
    assert dropped.render_active() == ""


def test_degrades_when_db_is_unreachable(monkeypatch):
    storage = importlib.import_module("tools.db.storage")

    def _boom(*a, **k):
        raise RuntimeError("no database")

    monkeypatch.setattr(storage, "get_connection", _boom)
    mgr = sg.GoalManager()
    assert mgr.create("offline") is None
    assert mgr.get("goal-1") is None
    assert mgr.list_goals() == []
    assert mgr.list_active() == []
    assert mgr.delete("goal-1") is False
    assert mgr.render_active() == ""


def test_ensure_schema_survives_a_read_only_connection():
    class _ReadOnly:
        def execute(self, *a, **k):
            raise sqlite3.OperationalError("attempt to write a readonly database")

        def commit(self):
            raise AssertionError("should not be reached")

    sg.GoalManager._ensure_schema(_ReadOnly())  # must not raise


# ---------------------------------------------------------------------------
# row mapping
# ---------------------------------------------------------------------------
def test_from_row_rejects_a_short_row():
    assert sg.StandingGoal.from_row(["goal-1", "op"]) is None


def test_from_row_tolerates_an_unknown_status():
    row = ["goal-1", "op", "acme", "CUI", "t", "", "from-the-future", 50, 0,
           "", "", "[]", "{}", "", "", "", "", ""]
    goal = sg.StandingGoal.from_row(row)
    assert goal is not None
    assert goal.status is sg.GoalStatus.PENDING


def test_from_row_tolerates_corrupt_json():
    row = ["goal-1", "op", "acme", "CUI", "t", "", "active", 50, 0,
           "", "", "not json", "also not json", "", "", "", "", ""]
    goal = sg.StandingGoal.from_row(row)
    assert goal.tags == []
    assert goal.metadata == {}
