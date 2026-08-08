# CUI // SP-CTI
"""Unit tests for /goal and standing-goal prompt injection (hgx-goal-02).

DB-independent in the same way ``test_standing_goals.py`` is: ``get_connection``
is monkeypatched (shim-aware, via ``importlib.import_module``) with a
``tests/_sql_compat`` connection so the ``%s`` placeholders the goal store
authors for PostgreSQL are rewritten exactly as the real storage layer rewrites
them. A bare ``sqlite3`` handle would make every statement raise inside the
module's own ``except`` and each assertion would be checking a no-op the test
itself caused.

The four acceptance criteria are covered by, in order:
``test_create_then_next_turn_shows_goal_in_system_prompt``,
``test_cap_holds_with_more_goals_than_the_limit``,
``test_mutation_invalidates_the_cache_within_a_session``, and
``test_help_lists_goal_commands`` / ``test_docstring_matches_registry``.
"""
from __future__ import annotations

import importlib
from typing import Any

import pytest

from tests._sql_compat import connect as translating_connect

import tools.agent_runtime.commands as cmds
import tools.agent_runtime.goal_context as gc
import tools.agent_runtime.standing_goals as sg
from tools.agent_runtime.commands import dispatch

_DB: dict = {}


def _get_connection(*_args, **_kwargs):
    if "conn" not in _DB:
        _DB["conn"] = translating_connect(":memory:")
    return _DB["conn"]


@pytest.fixture(autouse=True)
def _storage(monkeypatch):
    """Point the goal store at a fresh, placeholder-translating in-memory DB."""
    _DB.clear()
    storage = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(storage, "get_connection", _get_connection)
    # Keep the env-driven caps deterministic regardless of the host .env.
    monkeypatch.delenv(gc.ENV_LIMIT, raising=False)
    monkeypatch.delenv(gc.ENV_DISABLE, raising=False)


class _FakeSession:
    def __init__(self) -> None:
        self.context_id = "ctx-1"
        self.title = "Untitled session"


class _FakeRuntime:
    """The runtime surface ``/goal`` actually touches, plus the real cache seam."""

    def __init__(self) -> None:
        self.session = _FakeSession()
        self.user_id = "op"
        self.tenant_id = "acme"
        self.llm_function = "code_generation"
        self.system_prompt = "You are the ICDEV standalone agent."
        self._goals_preamble: str | None = None
        self.invalidated = 0

    def invalidate_goals(self) -> None:
        self.invalidated += 1
        self._goals_preamble = None

    def goal_block(self) -> str:
        """Mimic ``AgentRuntime._goals_context`` — build once, then reuse."""
        if self._goals_preamble is None:
            self._goals_preamble = gc.build_for_runtime(
                self.llm_function,
                self.system_prompt,
                user_id=self.user_id,
                tenant_id=self.tenant_id,
                context_id=self.session.context_id,
            )
        return self._goals_preamble


def _manager() -> Any:
    return sg.GoalManager(user_id="op", tenant_id="acme")


# ---------------------------------------------------------------------------
# Acceptance: create -> visible in the system prompt on the next turn
# ---------------------------------------------------------------------------
def test_create_then_next_turn_shows_goal_in_system_prompt():
    rt = _FakeRuntime()
    assert rt.goal_block() == ""  # nothing to inject yet

    _h, resp, _e = dispatch(rt, "/goal create Keep the migration backlog at zero")
    assert "created and active" in resp

    block = rt.goal_block()
    assert "Keep the migration backlog at zero" in block
    assert gc._HEADER in block


def test_create_records_detail_and_priority():
    rt = _FakeRuntime()
    dispatch(rt, "/goal create Ship PGP | drain the SQLite fallbacks --priority=90")
    goal = _manager().list_active()[0]
    assert goal.title == "Ship PGP"
    assert goal.detail == "drain the SQLite fallbacks"
    assert goal.priority == 90
    assert "drain the SQLite fallbacks" in rt.goal_block()


def test_create_requires_a_title():
    _h, resp, _e = dispatch(_FakeRuntime(), "/goal create   ")
    assert "Usage:" in resp


# ---------------------------------------------------------------------------
# Acceptance: the cap holds with more goals than the limit
# ---------------------------------------------------------------------------
def test_cap_holds_with_more_goals_than_the_limit():
    rt = _FakeRuntime()
    for i in range(9):
        # Descending priority so the ordering under the cap is deterministic.
        dispatch(rt, f"/goal create Goal number {i} --priority={90 - i}")

    report = gc.describe(
        llm_function="code_generation",
        user_id="op",
        tenant_id="acme",
        context_id="ctx-1",
    )
    assert report["total_active"] == 9
    assert report["limit"] == gc.DEFAULT_LIMIT
    assert report["shown"] == gc.DEFAULT_LIMIT
    assert report["withheld"] == 9 - gc.DEFAULT_LIMIT

    block = report["text"]
    assert "Goal number 0" in block          # highest priority survives
    assert "Goal number 8" not in block      # lowest is withheld
    assert "4 further active goal(s) not shown" in block  # never silent


def test_cap_is_configurable_and_clamped(monkeypatch):
    monkeypatch.setenv(gc.ENV_LIMIT, "2")
    assert gc.goal_limit() == 2
    monkeypatch.setenv(gc.ENV_LIMIT, "9999")
    assert gc.goal_limit() == gc.MAX_LIMIT
    # A typo must not silently strip the agent's objectives.
    monkeypatch.setenv(gc.ENV_LIMIT, "not-a-number")
    assert gc.goal_limit() == gc.DEFAULT_LIMIT
    monkeypatch.setenv(gc.ENV_LIMIT, "-3")
    assert gc.goal_limit() == gc.DEFAULT_LIMIT


def test_token_budget_caps_independently_of_the_count():
    """Five goals are under the count cap; long ones must still be shaved."""
    mgr = _manager()
    for i in range(5):
        mgr.create(
            f"Objective {i} " + "with a deliberately verbose restatement " * 4,
            # Under _MAX_DETAIL_CHARS, so a generous budget renders it whole.
            detail="drain the sqlite fallbacks first " * 4,
            status=sg.GoalStatus.ACTIVE,
            priority=90 - i,
        )

    generous = gc.describe(llm_function="code_generation", user_id="op",
                           tenant_id="acme", budget=4000)
    tight = gc.describe(llm_function="code_generation", user_id="op",
                        tenant_id="acme", budget=150)
    squeezed = gc.describe(llm_function="code_generation", user_id="op",
                           tenant_id="acme", budget=130)

    assert generous["shown"] == 5          # count cap not reached
    assert "drain the sqlite fallbacks" in generous["text"]
    assert generous["shortened"] is False

    # Under pressure the block shaves before it drops: every goal is still
    # named, but the pasted details are gone and the cut is announced.
    assert tight["shortened"] is True, "the token cap never engaged"
    assert tight["tokens"] <= tight["budget"], "the block overran its budget"
    assert tight["tokens"] < generous["tokens"]
    assert "drain the sqlite fallbacks" not in tight["text"]
    assert "shortened to fit the context budget" in tight["text"]
    assert "Objective 0" in tight["text"]  # the highest-priority goal survives

    # Squeezed further, goals do get dropped — and that is reported too.
    assert squeezed["shown"] < 5
    assert squeezed["truncated"] is True
    assert squeezed["tokens"] <= squeezed["budget"]
    assert "not shown" in squeezed["text"]


def test_block_is_dropped_when_the_budget_is_unusable():
    _manager().create("A goal", status=sg.GoalStatus.ACTIVE)
    report = gc.describe(llm_function="code_generation", user_id="op",
                         tenant_id="acme", budget=gc.MIN_BLOCK_TOKENS - 1)
    assert report["text"] == ""
    assert report["withheld"] == 1


def test_injection_can_be_disabled(monkeypatch):
    _manager().create("A goal", status=sg.GoalStatus.ACTIVE)
    monkeypatch.setenv(gc.ENV_DISABLE, "0")
    assert gc.build_for_runtime("code_generation", user_id="op", tenant_id="acme") == ""


def test_goal_context_degrades_when_the_store_is_unavailable(monkeypatch):
    monkeypatch.setattr(sg.GoalManager, "list_for_context",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no db")))
    assert gc.active_goals(user_id="op", tenant_id="acme") == []
    assert gc.build_for_runtime("code_generation", user_id="op", tenant_id="acme") == ""


# ---------------------------------------------------------------------------
# Acceptance: a mutation invalidates the cache within the same session
# ---------------------------------------------------------------------------
def test_mutation_invalidates_the_cache_within_a_session():
    rt = _FakeRuntime()
    dispatch(rt, "/goal create Keep the backlog at zero")
    before = rt.goal_block()
    assert "Keep the backlog at zero" in before

    # Pause it: same session, no restart — the next block must not show it.
    _h, resp, _e = dispatch(rt, "/goal pause 1")
    assert "now paused" in resp
    assert rt._goals_preamble is None, "the mutation did not invalidate the cache"
    assert "Keep the backlog at zero" not in rt.goal_block()

    # And resuming brings it back, still within the one session.
    dispatch(rt, "/goal resume 1")
    assert "Keep the backlog at zero" in rt.goal_block()


def test_invalidation_falls_back_to_the_private_attribute():
    """A runtime without the public hook (older build / fake) is still refreshed."""

    class _NoHook(_FakeRuntime):
        invalidate_goals = None  # type: ignore[assignment]

    rt = _NoHook()
    dispatch(rt, "/goal create Something durable")
    rt.goal_block()
    dispatch(rt, "/goal complete 1")
    assert rt._goals_preamble is None


def test_real_runtime_exposes_the_cache_seam():
    """The AgentRuntime attribute/method /goal relies on must actually exist."""
    from tools.agent_runtime.runtime import AgentRuntime

    assert callable(getattr(AgentRuntime, "invalidate_goals", None))
    assert callable(getattr(AgentRuntime, "_goals_context", None))
    src = AgentRuntime._effective_system_prompt.__code__.co_names
    assert "_goals_context" in src, "goals are not wired into the system prompt"


# ---------------------------------------------------------------------------
# Lifecycle subcommands
# ---------------------------------------------------------------------------
def test_list_numbers_goals_and_numbers_resolve():
    rt = _FakeRuntime()
    dispatch(rt, "/goal create First --priority=90")
    dispatch(rt, "/goal create Second --priority=10")
    _h, resp, _e = dispatch(rt, "/goal list")
    assert "2 goal(s):" in resp
    assert "1. [active] First" in resp
    _h, resp, _e = dispatch(rt, "/goal complete 2")
    assert "'Second' is now completed" in resp


def test_list_reports_empty_and_filters_by_status():
    rt = _FakeRuntime()
    _h, resp, _e = dispatch(rt, "/goal list")
    assert "No live goals" in resp
    dispatch(rt, "/goal create Alpha")
    dispatch(rt, "/goal cancel 1")
    _h, resp, _e = dispatch(rt, "/goal list")
    assert "No live goals" in resp          # cancelled is not live
    _h, resp, _e = dispatch(rt, "/goal list all")
    assert "Alpha" in resp


def test_block_records_the_reason_and_status_shows_it():
    rt = _FakeRuntime()
    dispatch(rt, "/goal create Land the migration")
    _h, resp, _e = dispatch(rt, "/goal block 1 waiting on PG snapshot")
    assert "now blocked" in resp and "waiting on PG snapshot" in resp
    _h, resp, _e = dispatch(rt, "/goal status 1")
    assert "blocked: waiting on PG snapshot" in resp
    # Blocked is not active, so it stops being injected.
    assert "Land the migration" not in rt.goal_block()


def test_illegal_transition_is_reported_not_swallowed():
    rt = _FakeRuntime()
    dispatch(rt, "/goal create Done deal")
    dispatch(rt, "/goal complete 1")
    _h, resp, _e = dispatch(rt, "/goal resume Done")  # no live goal matches now
    assert "No goal matches" in resp
    goal = _manager().list_goals()[0]
    _h, resp, _e = dispatch(rt, f"/goal resume {goal.goal_id}")
    assert "Cannot resume that goal" in resp and "terminal" in resp


def test_unknown_reference_and_missing_argument():
    rt = _FakeRuntime()
    _h, resp, _e = dispatch(rt, "/goal pause")
    assert "Usage: /goal pause" in resp
    _h, resp, _e = dispatch(rt, "/goal pause 7")
    assert "No goal matches" in resp


def test_unknown_subcommand_shows_usage():
    _h, resp, _e = dispatch(_FakeRuntime(), "/goal frobnicate")
    assert "Usage: /goal create" in resp


def test_status_reports_injected_versus_active():
    rt = _FakeRuntime()
    for i in range(7):
        dispatch(rt, f"/goal create Goal {i} --priority={90 - i}")
    _h, resp, _e = dispatch(rt, "/goal status")
    assert "7 active" in resp
    assert f"Injected into the system prompt: {gc.DEFAULT_LIMIT} of 7 active" in resp
    assert "2 active goal(s) withheld" in resp


def test_clear_previews_before_it_cancels():
    rt = _FakeRuntime()
    dispatch(rt, "/goal create One")
    dispatch(rt, "/goal create Two")
    _h, resp, _e = dispatch(rt, "/goal clear")
    assert "would cancel 2 goal(s)" in resp
    assert len(_manager().list_active()) == 2, "preview must not mutate"

    _h, resp, _e = dispatch(rt, "/goal clear --yes")
    assert "Cancelled 2 of 2" in resp
    assert _manager().list_active() == []
    assert rt.goal_block() == ""


def test_store_unavailable_is_reported_not_crashed(monkeypatch):
    monkeypatch.setattr(sg.GoalManager, "create", lambda *a, **k: None)
    _h, resp, _e = dispatch(_FakeRuntime(), "/goal create Anything")
    assert "goal store unavailable" in resp


# ---------------------------------------------------------------------------
# Acceptance: /help lists the commands and the docstring matches the registry
# ---------------------------------------------------------------------------
def test_help_lists_goal_commands():
    _h, resp, _e = dispatch(_FakeRuntime(), "/help")
    assert "/goal" in resp
    for verb in ("create", "list", "status", "pause", "resume", "complete",
                 "block", "cancel", "clear"):
        assert verb in resp, f"/help does not mention /goal {verb}"


def test_docstring_matches_registry():
    """Every registered command is documented in the module docstring.

    A command set that ships without its docstring entry is invisible to anyone
    reading the module — this is the check that keeps the two in step.
    """
    doc = cmds.__doc__ or ""
    for name in cmds.REGISTRY:
        # Documented either bare (``/goal``) or with its argument spec
        # (``/new [title]``) — but never merely as a prefix of another command.
        documented = f"``{name}``" in doc or f"``{name} " in doc
        assert documented, f"{name} is registered but undocumented"
    # And the reverse for the stale claims this task removed.
    assert "stub until" not in doc
