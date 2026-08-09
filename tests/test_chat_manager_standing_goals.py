# CUI // SP-CTI
"""Standing goals in the chat context status payload (hgx-goal-03).

``ChatManager.get_context_status`` is ``get_context`` plus a ``standing_goals``
block. These tests pin both halves of its contract: the block appears for a
conversation that has open goals, and it is *absent* — not empty, not an error —
whenever the goal store is unavailable, disabled or simply empty, with the rest
of the chat payload untouched.

DB-independent: ``tools.db.storage.get_connection`` is monkeypatched (shim-aware,
via ``importlib.import_module``) with a ``tests/_sql_compat`` connection, so the
``%s`` placeholders ``standing_goals`` authors for PostgreSQL are rewritten the
way the real storage layer rewrites them. A bare sqlite3 handle would make every
statement raise inside the module's own ``except`` and the test would assert
against a no-op it caused itself.

Contexts are registered directly in ``ChatManager._contexts`` rather than through
``create_context``, which would start a daemon agent-loop thread per test with
nothing to read from its queue.
"""
from __future__ import annotations

import importlib

import pytest

from tests._sql_compat import connect as translating_connect

import tools.agent_runtime.goal_context as goal_context
import tools.agent_runtime.standing_goals as sg
from tools.dashboard.chat_manager import ChatContext, ChatManager

CONTEXT_ID = "ctx-goal-test"
USER = "op"
TENANT = "acme"

#: One translating connection per test, created lazily so the patched factory —
#: not a fixture-local lambda — is what the runtime is handed.
_DB: dict = {}


def _get_connection(*_args, **_kwargs):
    if "conn" not in _DB:
        _DB["conn"] = translating_connect(":memory:")
    return _DB["conn"]


def _patch_storage(monkeypatch):
    _DB.clear()
    storage = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(storage, "get_connection", _get_connection)


@pytest.fixture()
def manager(monkeypatch):
    """A ChatManager holding one context, backed by an in-memory goal store."""
    _patch_storage(monkeypatch)
    # The kill-switch is env-driven and this suite must not inherit an operator's
    # setting from the ambient environment.
    monkeypatch.delenv(goal_context.ENV_DISABLE, raising=False)
    monkeypatch.delenv(goal_context.ENV_LIMIT, raising=False)

    mgr = ChatManager()
    # get_context falls back to the chat_contexts table for contexts not in
    # memory. That path binds get_connection at import time, so it would reach
    # the real backend rather than the in-memory store patched above.
    mgr._db_get_context = lambda context_id: None
    mgr._contexts[CONTEXT_ID] = ChatContext(
        context_id=CONTEXT_ID, user_id=USER, tenant_id=TENANT, title="Goals"
    )
    return mgr


@pytest.fixture()
def goals():
    """GoalManager on the same scope as the fixture context."""
    return sg.GoalManager(user_id=USER, tenant_id=TENANT)


# ---------------------------------------------------------------------------
# The block is present when there is something to show
# ---------------------------------------------------------------------------
def test_status_reports_goals_for_this_context(manager, goals):
    goals.create(
        "Keep the migration backlog at zero",
        detail="Scaffold every migration with migrate.py --create.",
        status=sg.GoalStatus.ACTIVE,
        context_id=CONTEXT_ID,
    )

    status = manager.get_context_status(CONTEXT_ID)

    assert status["standing_goals"]["total"] == 1
    goal = status["standing_goals"]["goals"][0]
    assert goal["title"] == "Keep the migration backlog at zero"
    assert goal["status"] == "active"
    assert goal["scope"] == "context"


def test_status_still_carries_the_base_context_payload(manager, goals):
    goals.create("Ship it", status=sg.GoalStatus.ACTIVE, context_id=CONTEXT_ID)

    status = manager.get_context_status(CONTEXT_ID)

    assert status["context_id"] == CONTEXT_ID
    assert status["user_id"] == USER
    assert status["title"] == "Goals"


def test_operator_wide_goals_are_in_play_here_too(manager, goals):
    """A goal with no context_id is standing across every conversation."""
    goals.create("Always propose a test first", status=sg.GoalStatus.ACTIVE)

    block = manager.get_context_status(CONTEXT_ID)["standing_goals"]

    assert [g["scope"] for g in block["goals"]] == ["global"]


def test_progress_is_reported_per_goal_and_rolled_up(manager, goals):
    first = goals.create("Half done", status=sg.GoalStatus.ACTIVE, context_id=CONTEXT_ID)
    second = goals.create("Just started", status=sg.GoalStatus.ACTIVE, context_id=CONTEXT_ID)
    goals.update_progress(first.goal_id, 60)
    goals.update_progress(second.goal_id, 20)

    block = manager.get_context_status(CONTEXT_ID)["standing_goals"]

    assert block["progress"] == 40
    assert sorted(g["progress"] for g in block["goals"]) == [20, 60]


def test_another_contexts_goals_are_not_shown(manager, goals):
    goals.create("Someone else's goal", status=sg.GoalStatus.ACTIVE, context_id="ctx-other")

    assert "standing_goals" not in manager.get_context_status(CONTEXT_ID)


def test_terminal_goals_are_history_not_status(manager, goals):
    goal = goals.create("Done and dusted", status=sg.GoalStatus.ACTIVE, context_id=CONTEXT_ID)
    goals.complete(goal.goal_id)

    assert "standing_goals" not in manager.get_context_status(CONTEXT_ID)


def test_blocked_goal_carries_its_reason(manager, goals):
    goal = goals.create("Blocked on review", status=sg.GoalStatus.ACTIVE, context_id=CONTEXT_ID)
    goals.block(goal.goal_id, "waiting on CI")

    block = manager.get_context_status(CONTEXT_ID)["standing_goals"]

    assert block["by_status"] == {"blocked": 1}
    assert block["goals"][0]["blocked_reason"] == "waiting on CI"


# ---------------------------------------------------------------------------
# Capping — the display cap must not hide the goals actually in play
# ---------------------------------------------------------------------------
def test_cap_holds_and_reports_what_it_withheld(manager, goals, monkeypatch):
    monkeypatch.setenv(goal_context.ENV_LIMIT, "2")
    for i in range(5):
        goals.create(f"Goal {i}", status=sg.GoalStatus.ACTIVE, context_id=CONTEXT_ID)

    block = manager.get_context_status(CONTEXT_ID)["standing_goals"]

    assert (block["total"], block["shown"], block["withheld"]) == (5, 2, 3)
    assert len(block["goals"]) == 2


def test_live_goals_sort_ahead_of_queued_ones(manager, goals, monkeypatch):
    monkeypatch.setenv(goal_context.ENV_LIMIT, "1")
    # Higher priority, but only pending — it must not displace the active goal.
    goals.create("Queued", priority=90, context_id=CONTEXT_ID)
    goals.create("In flight", priority=10, status=sg.GoalStatus.ACTIVE, context_id=CONTEXT_ID)

    block = manager.get_context_status(CONTEXT_ID)["standing_goals"]

    assert [g["title"] for g in block["goals"]] == ["In flight"]
    assert block["by_status"] == {"pending": 1, "active": 1}


# ---------------------------------------------------------------------------
# Degradation — chat is never broken by the optional subsystem
# ---------------------------------------------------------------------------
def test_key_omitted_when_the_table_is_absent(manager, goals, monkeypatch):
    """Migration not run / table dropped: no key, and the payload still works."""
    goals.create("Never stored", status=sg.GoalStatus.ACTIVE, context_id=CONTEXT_ID)
    # Suppress the DDL so _ensure_schema cannot paper over the missing table,
    # then drop it out from under the reader.
    _get_connection().execute(f"DROP TABLE IF EXISTS {sg._TABLE}")
    monkeypatch.setattr(sg.GoalManager, "_ensure_schema", staticmethod(lambda c: None))

    status = manager.get_context_status(CONTEXT_ID)

    assert "standing_goals" not in status
    assert status["context_id"] == CONTEXT_ID


def test_key_omitted_when_the_context_has_no_goals(manager):
    assert "standing_goals" not in manager.get_context_status(CONTEXT_ID)


def test_kill_switch_omits_the_block(manager, goals, monkeypatch):
    goals.create("Hidden by the switch", status=sg.GoalStatus.ACTIVE, context_id=CONTEXT_ID)
    monkeypatch.setenv(goal_context.ENV_DISABLE, "0")

    assert "standing_goals" not in manager.get_context_status(CONTEXT_ID)


def test_a_raising_goal_store_does_not_break_chat(manager, goals, monkeypatch):
    goals.create("Unreachable", status=sg.GoalStatus.ACTIVE, context_id=CONTEXT_ID)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("goal store is on fire")

    monkeypatch.setattr(sg.GoalManager, "list_for_context", _boom)

    status = manager.get_context_status(CONTEXT_ID)

    assert "standing_goals" not in status
    assert status["status"] == "active"


def test_unknown_context_is_still_none(manager):
    assert manager.get_context_status("ctx-does-not-exist") is None
