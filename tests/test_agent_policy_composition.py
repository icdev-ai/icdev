# CUI // SP-CTI
"""Three-level policy composition and session state (exa-policy-02).

Four things have to be true, and each has a section here:

  1. **Precedence.** Policies resolve session, then agent, then server, in that
     order, and a DENY at any level short-circuits — including across levels, so
     a session DENY means the server level is never consulted.
  2. **A session cannot weaken anything.** The attempted-loosening cases get
     their own class, because "it cannot loosen" is the claim the whole
     session-first ordering rests on and an untested claim is a hope. Each
     attempt is asserted to have NO effect on the answer, and to be REPORTED
     rather than silently dropped.
  3. **Session state persists across tool calls within a session**, is scoped to
     the session id, and is visible to the next policy in the same composition.
  4. **It still fails closed** — the composition inherits policy_engine's
     posture and must not have introduced a way around it.

The state tests build the table from the migration's own DDL rather than a
hand-written schema, so a column added to one and not the other fails here
instead of at runtime inside a swallowed exception (CLAUDE.md: "every column in
an INSERT must exist in the LIVE schema").
"""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from tools.agent_runtime import policy_composition as pc
from tools.agent_runtime import policy_engine as pe

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _policy(effect: str, reason: str = "because", name: str = "p", **kw: Any):
    """A policy that records every event it was asked about."""
    seen: list[pe.PolicyEvent] = []

    def fn(event: pe.PolicyEvent) -> pe.PolicyDecision:
        seen.append(event)
        return pe.PolicyDecision(effect, reason, policy=name, **kw)

    fn.seen = seen  # type: ignore[attr-defined]
    return fn


def _level(name: str, *policies: tuple[str, Any], **kw: Any) -> pc.Level:
    """A Level with an explicit chain, bypassing config resolution."""
    return pc.Level(level=name, chain=tuple(policies), **kw)


def _event(target: str = "read_file", **kw: Any) -> pe.PolicyEvent:
    kw.setdefault("arguments", {"path": "a"})
    return pe.PolicyEvent(target=target, **kw)


def _state(session_id: str = "s1", values: dict[str, Any] | None = None) -> pc.SessionState:
    """State that never touches a database — persistence has its own section."""
    return pc.SessionState(session_id, values or {}, persist=False)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """No DB writes, no config from disk, no cache bleed between tests."""
    monkeypatch.setattr(pc, "_write_state", lambda *a, **k: True)
    monkeypatch.setattr(pc, "_read_state", lambda *a, **k: {})
    monkeypatch.setattr(pc, "_delete_state", lambda *a, **k: True)
    monkeypatch.setattr(pc.gate, "record_decision", lambda **kw: True)
    monkeypatch.setattr(pc.gate, "_hard_block", lambda *a, **k: (False, ""))
    pc._STATES.clear()
    yield
    pc._STATES.clear()


# ---------------------------------------------------------------------------
# 1. Precedence: session, then agent, then server
# ---------------------------------------------------------------------------
class TestPrecedenceOrder:
    def test_the_level_order_is_session_then_agent_then_server(self):
        assert pc.LEVELS == ("session", "agent", "server")

    def test_the_order_is_a_constant_not_a_config_key(self):
        """A config that could reorder the levels could put session last."""
        levels, _ = pc.load_levels(
            session={"levels": ["server", "agent", "session"], "chain": []},
            agent={},
            server={"chain": []},
        )
        assert tuple(lv.level for lv in levels) == pc.LEVELS

    def test_policies_run_in_level_order(self):
        calls: list[str] = []

        def recorder(tag: str):
            def fn(_event):
                calls.append(tag)
                return pe.PolicyDecision(pe.ALLOW, "ok", policy=tag)
            return fn

        levels = [
            _level("session", ("s", recorder("s"))),
            _level("agent", ("a", recorder("a"))),
            _level("server", ("v", recorder("v"))),
        ]
        pc.compose(_event(), levels, state=_state())
        assert calls == ["s", "a", "v"]

    def test_every_decision_says_which_level_it_came_from(self):
        levels = [
            _level("session", ("s", _policy(pe.ALLOW, name="s"))),
            _level("agent", ("a", _policy(pe.ASK, name="a"))),
            _level("server", ("v", _policy(pe.ALLOW, name="v"))),
        ]
        result = pc.compose(_event(), levels, state=_state())
        assert [lv for lv, _d in result.decisions] == ["session", "agent", "server"]
        assert result.levels_consulted == ("session", "agent", "server")

    def test_the_winning_level_is_named(self):
        levels = [
            _level("session", ("s", _policy(pe.ALLOW, name="s"))),
            _level("agent", ("a", _policy(pe.ASK, "author says ask", name="a"))),
            _level("server", ("v", _policy(pe.ALLOW, name="v"))),
        ]
        result = pc.compose(_event(), levels, state=_state())
        assert result.effect == pe.ASK
        assert result.level == "agent"
        assert result.policy == "a"

    def test_a_level_with_no_policies_is_skipped_not_an_error(self):
        levels = [
            _level("session"),
            _level("agent"),
            _level("server", ("v", _policy(pe.ALLOW, name="v"))),
        ]
        result = pc.compose(_event(), levels, state=_state())
        assert result.effect == pe.ALLOW
        assert result.levels_consulted == ("server",)


class TestDenyShortCircuitsAcrossLevels:
    def test_a_session_deny_never_consults_the_server_level(self):
        server = _policy(pe.ALLOW, name="v")
        levels = [
            _level("session", ("s", _policy(pe.DENY, "user forbade it", name="s"))),
            _level("agent", ("a", _policy(pe.ALLOW, name="a"))),
            _level("server", ("v", server)),
        ]
        result = pc.compose(_event(), levels, state=_state())
        assert result.effect == pe.DENY
        assert result.level == "session"
        assert result.short_circuited is True
        assert server.seen == []                      # never asked
        assert result.levels_consulted == ("session",)

    def test_a_server_deny_wins_over_an_earlier_session_allow(self):
        levels = [
            _level("session", ("s", _policy(pe.ALLOW, "user is fine with it", name="s"))),
            _level("agent", ("a", _policy(pe.ALLOW, name="a"))),
            _level("server", ("v", _policy(pe.DENY, "org baseline", name="v"))),
        ]
        result = pc.compose(_event(), levels, state=_state())
        assert result.effect == pe.DENY
        assert result.level == "server"
        assert result.reason == "org baseline"

    def test_a_server_deny_wins_over_an_earlier_agent_ask(self):
        """ASK must not short-circuit, or a later DENY could never be reached."""
        levels = [
            _level("agent", ("a", _policy(pe.ASK, name="a"))),
            _level("server", ("v", _policy(pe.DENY, "no", name="v"))),
        ]
        result = pc.compose(_event(), levels, state=_state())
        assert result.effect == pe.DENY

    def test_a_composed_deny_is_never_offered_to_the_approver(self):
        """The capability the reversibility gate does not have.

        An approver that says yes to everything would let an ASK through; the
        DENY must be blocked without it ever being consulted, or DENY is just a
        loudly-worded ASK.
        """
        asked: list[Any] = []
        pe.register_policy("_pc_hard_no", _policy(pe.DENY, "never"), replace=True)
        hook = pc.build_composed_policy_hook(
            session_id="s-noask",
            session_policy={"chain": [{"name": "_pc_hard_no"}]},
            agent_policy={"chain": []},
            server_policy={"chain": []},
            approver=lambda request: asked.append(request) or True,
            mode=pc.gate.MODE_ENFORCE,
        )
        blocked = hook("git_push", {"branch": "main"})
        assert blocked is not None and "BLOCKED" in blocked
        assert asked == [], "a DENY must never reach the approver"

    def test_an_ask_does_reach_the_approver(self):
        """The contrast case, so the test above is not passing for a dull reason."""
        asked: list[Any] = []
        pe.register_policy("_pc_maybe", _policy(pe.ASK, "check"), replace=True)
        hook = pc.build_composed_policy_hook(
            session_id="s-ask",
            session_policy={"chain": [{"name": "_pc_maybe"}]},
            agent_policy={"chain": []},
            server_policy={"chain": []},
            approver=lambda request: asked.append(request) or True,
            mode=pc.gate.MODE_ENFORCE,
        )
        assert hook("git_push", {"branch": "main"}) is None
        assert len(asked) == 1


# ---------------------------------------------------------------------------
# 2. A session level cannot weaken an agent or server level
# ---------------------------------------------------------------------------
class TestASessionCannotWeaken:
    def test_a_session_allow_cannot_overturn_a_server_deny(self):
        levels = [
            _level("session", ("s", _policy(pe.ALLOW, "please let me", name="s"))),
            _level("server", ("v", _policy(pe.DENY, "forbidden", name="v"))),
        ]
        result = pc.compose(_event(), levels, state=_state())
        assert result.effect == pe.DENY
        assert result.level == "server"

    def test_a_session_allow_cannot_overturn_a_server_ask(self):
        levels = [
            _level("session", ("s", _policy(pe.ALLOW, name="s"))),
            _level("server", ("v", _policy(pe.ASK, "needs a human", name="v"))),
        ]
        assert pc.compose(_event(), levels, state=_state()).effect == pe.ASK

    def test_a_session_allow_is_indistinguishable_from_saying_nothing(self):
        """The structural claim, asserted directly."""
        server = [_level("server", ("v", _policy(pe.ASK, "human", name="v")))]
        with_session = pc.compose(
            _event(),
            [_level("session", ("s", _policy(pe.ALLOW, name="s")))] + server,
            state=_state(),
        )
        without_session = pc.compose(_event(), server, state=_state())
        assert with_session.effect == without_session.effect
        assert with_session.level == without_session.level == "server"

    @pytest.mark.parametrize(
        "session_floor,server_floor,expected,owner",
        [
            ("allow", "deny", "deny", "server"),   # session tries to lower: ignored
            ("ask", "deny", "deny", "server"),     # session tries to soften: ignored
            ("deny", "allow", "deny", "session"),  # session RAISES: honoured
            ("deny", "ask", "deny", "session"),
        ],
    )
    def test_a_floor_can_only_be_raised_by_a_session(
        self, session_floor, server_floor, expected, owner
    ):
        levels, _ = pc.load_levels(
            session={"chain": [], "floors": {"tool_call": session_floor}},
            agent={},
            server={"chain": [], "floors": {"tool_call": server_floor}},
        )
        assert pc.composed_floor(levels, "tool_call") == (expected, owner)

    def test_a_lowered_floor_is_reported_not_silently_dropped(self):
        _levels, relaxations = pc.load_levels(
            session={"chain": [], "floors": {"tool_call": "allow"}},
            agent={},
            server={"chain": [], "floors": {"tool_call": "deny"}},
        )
        hits = [r for r in relaxations if r.level == "session" and "floors" in r.key]
        assert hits, "a session floor below the server floor must be reported"
        assert hits[0].effective == "deny"
        assert "can only be raised" in hits[0].reason

    def test_a_session_cannot_disable_a_policy_the_server_enables(self):
        pe.register_policy("_pc_test_named", _policy(pe.DENY, "server rule"), replace=True)
        levels, relaxations = pc.load_levels(
            session={"chain": [{"name": "_pc_test_named", "enabled": False}]},
            agent={},
            server={"chain": [{"name": "_pc_test_named", "enabled": True}]},
        )
        # Still in the server chain, so still evaluated, so still a DENY.
        assert "_pc_test_named" in levels[2].policy_names
        result = pc.compose(_event(), levels, state=_state())
        assert result.effect == pe.DENY
        assert result.level == "server"
        # And the attempt is reported.
        hits = [r for r in relaxations if r.level == "session" and "enabled" in r.key]
        assert hits and hits[0].effective == "still evaluated"

    @pytest.mark.parametrize("level", ["session", "agent", "server"])
    def test_on_policy_error_allow_is_refused_at_every_level(self, level):
        """Refused at the server level too: a broken policy is not a yes."""
        relaxations: list[pc.Relaxation] = []
        built = pc.build_level(
            level, {"chain": [], "on_policy_error": "allow"}, relaxations=relaxations
        )
        assert built.on_policy_error == pe.DENY
        assert any(r.key == "on_policy_error" for r in relaxations)

    def test_a_session_ask_cannot_soften_a_server_deny_on_policy_error(self):
        levels, _ = pc.load_levels(
            session={"chain": [], "on_policy_error": "ask"},
            agent={"chain": [], "on_policy_error": "ask"},
            server={"chain": [], "on_policy_error": "deny"},
        )
        assert pc.composed_on_policy_error(levels) == pe.DENY

    def test_a_level_that_states_nothing_does_not_vote(self):
        """An empty session must not override an admin who chose `ask`."""
        levels, _ = pc.load_levels(
            session={}, agent={}, server={"chain": [], "on_policy_error": "ask"}
        )
        assert pc.composed_on_policy_error(levels) == pe.ASK

    def test_nobody_stating_one_still_means_deny(self):
        levels, _ = pc.load_levels(session={}, agent={}, server={"chain": []})
        assert pc.composed_on_policy_error(levels) == pe.DENY

    @pytest.mark.parametrize("level", ["session", "agent"])
    def test_only_the_server_level_can_turn_off_allow_logging(self, level):
        relaxations: list[pc.Relaxation] = []
        pc.build_level(
            level, {"chain": [], "audit": {"log_allow": False}}, relaxations=relaxations
        )
        assert any(r.key == "audit" and r.level == level for r in relaxations)

    def test_the_hook_reads_log_allow_from_the_server_level_only(self, monkeypatch):
        recorded: list[dict[str, Any]] = []
        monkeypatch.setattr(
            pc.gate, "record_decision", lambda **kw: recorded.append(kw) or True
        )
        pe.register_policy("_pc_ok", _policy(pe.ALLOW, "fine"), replace=True)
        hook = pc.build_composed_policy_hook(
            session_id="s-log",
            session_policy={"chain": [], "audit": {"log_allow": False}},
            agent_policy={"chain": []},
            server_policy={"chain": [{"name": "_pc_ok"}], "audit": {"log_allow": True}},
        )
        assert hook("read_file", {"path": "a"}) is None
        assert len(recorded) == 1, "the session must not suppress its own allow row"

    def test_a_session_cannot_introduce_a_policy_function(self):
        """No path from config to a callable — only names the registry holds."""
        levels, _ = pc.load_levels(
            session={"chain": [{"name": "_pc_not_registered_at_all"}]},
            agent={},
            server={"chain": []},
        )
        result = pc.compose(_event(), levels, state=_state())
        assert result.effect == pe.DENY
        assert "not registered" in result.reason


# ---------------------------------------------------------------------------
# 3. Session state
# ---------------------------------------------------------------------------
class TestSessionStateMechanics:
    @pytest.mark.parametrize(
        "action,value,start,expected",
        [
            ("increment", 1, None, 1),
            ("increment", 5, 2, 7),
            ("increment", None, 4, 5),      # value defaults to 1
            ("decrement", 2, 10, 8),
            ("set", "x", None, "x"),
            ("append", "b", ["a"], ["a", "b"]),
            ("append", "a", None, ["a"]),
        ],
    )
    def test_the_documented_actions(self, action, value, start, expected):
        state = _state(values={} if start is None else {"k": start})
        state.apply_updates([{"key": "k", "action": action, "value": value}])
        assert state.get("k") == expected

    def test_delete_removes_the_key(self):
        state = _state(values={"k": 3})
        state.apply_updates([{"key": "k", "action": "delete"}])
        assert "k" not in state

    def test_the_reference_example_from_the_card(self):
        state = _state()
        for _ in range(3):
            state.apply_updates(
                [{"key": "call_count", "action": "increment", "value": 1}]
            )
        assert state.get("call_count") == 3

    @pytest.mark.parametrize(
        "update",
        [
            {"key": "k", "action": "explode"},          # unknown action
            {"key": "", "action": "increment"},         # no key
            {"key": "k", "action": "increment", "value": "five"},  # not a number
        ],
    )
    def test_a_bad_update_raises_rather_than_being_dropped(self, update):
        """A counter that silently fails to increment is a limit that never fires."""
        with pytest.raises(ValueError):
            _state().apply_updates([update])

    def test_incrementing_a_non_number_raises(self):
        with pytest.raises(ValueError):
            _state(values={"k": "text"}).apply_updates(
                [{"key": "k", "action": "increment"}]
            )

    def test_appending_to_a_non_list_raises(self):
        with pytest.raises(ValueError):
            _state(values={"k": 1}).apply_updates([{"key": "k", "action": "append"}])

    def test_state_updates_survive_the_policy_engine_normalisation(self):
        decision = pe.PolicyDecision(
            pe.ALLOW, "ok", state_updates=({"key": "n", "action": "increment"},)
        )
        assert pe._normalize(decision, "p").state_updates == (
            {"key": "n", "action": "increment", "value": None},
        )

    def test_a_malformed_state_update_denies_through_the_chain(self):
        """policy_engine raises on structure; the chain resolves that to DENY."""
        def bad(_event):
            return pe.PolicyDecision(pe.ALLOW, "ok", state_updates=("not a mapping",))

        result = pc.compose(
            _event(), [_level("server", ("bad", bad))], state=_state()
        )
        assert result.effect == pe.DENY


class TestSessionStatePersistsAcrossToolCalls:
    def _counter(self, limit: int):
        """A stateful policy: deny once the session has made `limit` calls."""

        def fn(event: pe.PolicyEvent) -> pe.PolicyDecision:
            seen = event.session_state.get("call_count", 0)
            effect = pe.DENY if seen >= limit else pe.ALLOW
            return pe.PolicyDecision(
                effect,
                f"call_count={seen} against a limit of {limit}",
                policy="max_calls",
                state_updates=({"key": "call_count", "action": "increment", "value": 1},),
            )

        return fn

    def test_a_counter_survives_across_tool_calls_in_one_session(self):
        pe.register_policy("_pc_max_calls", self._counter(3), replace=True)
        hook = pc.build_composed_policy_hook(
            session_id="s-count",
            session_policy={"chain": [{"name": "_pc_max_calls"}]},
            agent_policy={"chain": []},
            server_policy={"chain": []},
        )
        assert hook("read_file", {"path": "a"}) is None   # 0 -> 1
        assert hook("read_file", {"path": "b"}) is None   # 1 -> 2
        assert hook("read_file", {"path": "c"}) is None   # 2 -> 3
        blocked = hook("read_file", {"path": "d"})        # 3 >= 3 -> DENY
        assert blocked is not None and "BLOCKED" in blocked
        assert pc.get_session_state("s-count").get("call_count") == 4

    def test_a_rebuilt_hook_counts_against_the_same_session(self):
        """The runtime rebuilds the hook each turn; the limit must not reset."""
        pe.register_policy("_pc_max_calls", self._counter(2), replace=True)
        policy = {"chain": [{"name": "_pc_max_calls"}]}
        first = pc.build_composed_policy_hook(
            session_id="s-turns", session_policy=policy,
            agent_policy={"chain": []}, server_policy={"chain": []},
        )
        assert first("read_file", {"path": "a"}) is None
        assert first("read_file", {"path": "b"}) is None
        second = pc.build_composed_policy_hook(
            session_id="s-turns", session_policy=policy,
            agent_policy={"chain": []}, server_policy={"chain": []},
        )
        assert second("read_file", {"path": "c"}) is not None

    def test_state_is_scoped_to_the_session_id(self):
        pe.register_policy("_pc_max_calls", self._counter(1), replace=True)
        policy = {"chain": [{"name": "_pc_max_calls"}]}
        kw = {"agent_policy": {"chain": []}, "server_policy": {"chain": []}}
        a = pc.build_composed_policy_hook(session_id="s-a", session_policy=policy, **kw)
        b = pc.build_composed_policy_hook(session_id="s-b", session_policy=policy, **kw)
        assert a("read_file", {"path": "x"}) is None
        assert a("read_file", {"path": "y"}) is not None   # s-a is used up
        assert b("read_file", {"path": "x"}) is None       # s-b is untouched

    def test_a_later_policy_sees_what_an_earlier_one_wrote(self):
        """Updates apply as the chain runs, not after it finishes."""
        def writer(_event):
            return pe.PolicyDecision(
                pe.ALLOW, "wrote", policy="w",
                state_updates=({"key": "n", "action": "set", "value": 7},),
            )

        seen: list[Any] = []

        def reader(event):
            seen.append(event.session_state.get("n"))
            return pe.PolicyDecision(pe.ALLOW, "read", policy="r")

        pc.compose(
            _event(),
            [_level("session", ("w", writer)), _level("server", ("r", reader))],
            state=_state(),
        )
        assert seen == [7]

    def test_a_policy_that_never_ran_never_wrote(self):
        def writer(_event):
            return pe.PolicyDecision(
                pe.ALLOW, "wrote", policy="w",
                state_updates=({"key": "n", "action": "increment"},),
            )

        state = _state()
        pc.compose(
            _event(),
            [
                _level("session", ("d", _policy(pe.DENY, "no", name="d"))),
                _level("server", ("w", writer)),
            ],
            state=state,
        )
        assert "n" not in state

    def test_a_policy_cannot_mutate_state_by_writing_to_the_event(self):
        """The event carries a snapshot; state_updates is the only writer."""
        def sneaky(event):
            event.session_state["n"] = 999
            return pe.PolicyDecision(pe.ALLOW, "ok", policy="s")

        state = _state(values={"n": 1})
        pc.compose(_event(), [_level("server", ("s", sneaky))], state=state)
        assert state.get("n") == 1

    def test_the_changed_keys_are_reported(self):
        def writer(_event):
            return pe.PolicyDecision(
                pe.ALLOW, "ok", policy="w",
                state_updates=({"key": "n", "action": "increment"},),
            )

        result = pc.compose(
            _event(), [_level("server", ("w", writer))], state=_state()
        )
        assert result.state_changed == ("n",)

    def test_no_session_id_means_no_shared_state(self):
        """Honest rather than convenient: without a session there is no session."""
        state = pc.get_session_state("")
        assert state.persist is False
        assert pc.get_session_state("") is not state   # a throwaway each time

    def test_the_registry_returns_one_state_per_session(self):
        assert pc.get_session_state("s-same") is pc.get_session_state("s-same")
        assert pc.get_session_state("s-same") is not pc.get_session_state("s-other")


# ---------------------------------------------------------------------------
# 3b. Persistence against the migration's own DDL
# ---------------------------------------------------------------------------
def _migration_ddl() -> str:
    path = (
        REPO_ROOT / "tools" / "db" / "migrations"
        / "20260812054330_agent_session_policy_state" / "up.py"
    )
    spec = importlib.util.spec_from_file_location("_m_session_policy_state", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module._DDL


def _translating_conn(raw: sqlite3.Connection):
    """Wrap ``raw`` in a %s -> ? translating connection, as production has.

    The UPSERT under test is authored for PostgreSQL. Handing production code a
    bare ``sqlite3`` connection makes it raise ``near "%": syntax error`` inside
    ``_write_state``'s ``except``, and the test then asserts against a no-op it
    caused itself. ``tests/_sql_compat`` delegates to the same ``translate_sql``
    the runtime uses, so this fixture cannot drift from it.
    """
    from tests._sql_compat import translating

    conn = translating(raw)
    conn.close = lambda: None      # the fixture owns the lifetime
    return conn


def _storage_module():
    """The module ``_write_state`` actually resolves ``get_connection`` from.

    ``tools.db.storage`` in ``sys.modules`` is the compat shim, and
    ``import tools.db.storage`` binds the canonical ``icdev.tools.db.storage``
    instead — two different objects. Patching the wrong one asserts a no-op.
    """
    import sys

    return sys.modules["tools.db.storage"]


@pytest.fixture
def state_db(monkeypatch, tmp_path):
    """A real table built from the migration's own DDL, with %s translation."""
    raw = sqlite3.connect(str(tmp_path / "state.db"))
    raw.executescript(_migration_ddl())
    storage = _storage_module()
    monkeypatch.setattr(
        storage, "get_connection", lambda *a, **k: _translating_conn(raw)
    )
    monkeypatch.setattr(storage, "table_exists", lambda c, t: True)
    # Undo the autouse no-DB stubs — this section is the one that wants the real
    # writers. _REAL_* were bound at import, before any fixture could replace
    # them, so these are the genuine functions and not a stub reinstalling itself.
    monkeypatch.setattr(pc, "_write_state", _REAL_WRITE)
    monkeypatch.setattr(pc, "_read_state", _REAL_READ)
    monkeypatch.setattr(pc, "_delete_state", _REAL_DELETE)
    yield raw
    raw.close()


_REAL_WRITE = pc._write_state
_REAL_READ = pc._read_state
_REAL_DELETE = pc._delete_state


class TestStatePersistence:
    def test_a_counter_round_trips_through_the_table(self, state_db):
        state = pc.SessionState("s-db", {}, persist=True)
        state.apply_updates([{"key": "call_count", "action": "increment", "value": 2}])
        rows = state_db.execute(
            "SELECT session_id, state_key, state_value FROM "
            "agent_session_policy_state"
        ).fetchall()
        assert rows == [("s-db", "call_count", "2")]

    def test_state_survives_a_process_restart(self, state_db):
        """The reason this is a table and not a dict."""
        pc.SessionState("s-restart", {}, persist=True).apply_updates(
            [{"key": "call_count", "action": "increment", "value": 4}]
        )
        pc._STATES.clear()                                     # "restart"
        reloaded = pc.get_session_state("s-restart", refresh=True)
        assert reloaded.get("call_count") == 4

    def test_an_update_overwrites_rather_than_appending_a_row(self, state_db):
        state = pc.SessionState("s-upsert", {}, persist=True)
        for _ in range(3):
            state.apply_updates([{"key": "n", "action": "increment"}])
        rows = state_db.execute(
            "SELECT state_value FROM agent_session_policy_state WHERE state_key = 'n'"
        ).fetchall()
        assert rows == [("3",)], "one row per key per session, not an audit log"

    def test_delete_removes_the_row(self, state_db):
        state = pc.SessionState("s-del", {}, persist=True)
        state.apply_updates([{"key": "n", "action": "set", "value": 1}])
        state.apply_updates([{"key": "n", "action": "delete"}])
        assert state_db.execute(
            "SELECT COUNT(*) FROM agent_session_policy_state"
        ).fetchone()[0] == 0

    def test_reset_clears_the_session(self, state_db):
        state = pc.SessionState("s-reset", {}, persist=True)
        state.apply_updates([{"key": "a", "action": "set", "value": 1}])
        state.apply_updates([{"key": "b", "action": "set", "value": 2}])
        state.reset()
        assert state_db.execute(
            "SELECT COUNT(*) FROM agent_session_policy_state"
        ).fetchone()[0] == 0

    def test_two_sessions_do_not_share_rows(self, state_db):
        for sid in ("s-1", "s-2"):
            pc.SessionState(sid, {}, persist=True).apply_updates(
                [{"key": "n", "action": "set", "value": sid}]
            )
        rows = dict(state_db.execute(
            "SELECT session_id, state_value FROM agent_session_policy_state"
        ).fetchall())
        assert rows == {"s-1": '"s-1"', "s-2": '"s-2"'}

    def test_the_table_is_not_registered_append_only(self):
        """A counter is the current value; the evidence row is elsewhere."""
        hook = (REPO_ROOT / ".claude" / "hooks" / "pre_tool_use.py").read_text(
            encoding="utf-8"
        )
        assert '"agent_approval_log"' in hook, "the evidence table is append-only"
        assert f'"{pc.STATE_TABLE}"' not in hook


@pytest.fixture
def empty_db(monkeypatch, tmp_path):
    """A database where the table does NOT exist, probed for real.

    ``table_exists`` is deliberately left unpatched here — the branch under test
    is "what happens when the migration has not run", and stubbing the probe that
    detects that would test the stub.
    """
    raw = sqlite3.connect(str(tmp_path / "empty.db"))
    storage = _storage_module()
    monkeypatch.setattr(
        storage, "get_connection", lambda *a, **k: _translating_conn(raw)
    )
    monkeypatch.setattr(pc, "_write_state", _REAL_WRITE)
    monkeypatch.setattr(pc, "_read_state", _REAL_READ)
    monkeypatch.setattr(pc, "_STATE_TABLE_WARNED", False)
    yield raw
    raw.close()


class TestPersistenceFailsSafely:
    def test_a_missing_table_leaves_in_process_state_working(self, empty_db):
        """Losing persistence must not lose the limit for this process."""
        state = pc.SessionState("s-notable", {}, persist=True)
        state.apply_updates([{"key": "n", "action": "increment"}])
        assert state.get("n") == 1

    def test_a_missing_table_reads_as_empty_rather_than_raising(self, empty_db):
        assert pc._read_state("s-notable") == {}

    def test_a_missing_table_warns_naming_the_migration(self, empty_db, monkeypatch):
        """A silently absent limit reported as a satisfied one is the bug.

        Asserted against ``pc.logger`` directly rather than ``caplog``:
        ``icdev_logger`` does not propagate to the root logger, so caplog sees
        nothing and the test would pass for the wrong reason forever.
        """
        warnings: list[str] = []
        monkeypatch.setattr(
            pc.logger, "warning",
            lambda msg, *a, **k: warnings.append(str(msg) % a if a else str(msg)),
        )
        pc._read_state("s-notable")
        assert any(pc.STATE_MIGRATION in w for w in warnings), warnings
        assert any(pc.STATE_TABLE in w for w in warnings)

    def test_the_missing_table_warning_fires_once_not_per_call(self, empty_db, monkeypatch):
        """A per-call warning on a hot path is a log nobody reads."""
        warnings: list[str] = []
        monkeypatch.setattr(
            pc.logger, "warning",
            lambda msg, *a, **k: warnings.append(str(msg) % a if a else str(msg)),
        )
        for _ in range(3):
            pc._read_state("s-notable")
        assert len([w for w in warnings if pc.STATE_TABLE in w]) == 1

    def test_an_unreadable_row_is_skipped_not_guessed(self, state_db):
        """A real row this module did not write — not a stubbed cursor."""
        state_db.execute(
            "INSERT INTO agent_session_policy_state "
            "(session_id, state_key, state_value) VALUES (?, ?, ?)",
            ("s-mixed", "good", "1"),
        )
        state_db.execute(
            "INSERT INTO agent_session_policy_state "
            "(session_id, state_key, state_value) VALUES (?, ?, ?)",
            ("s-mixed", "bad", "{not json"),
        )
        state_db.commit()
        assert pc._read_state("s-mixed") == {"good": 1}

    def test_a_db_error_does_not_propagate(self, empty_db):
        """A genuinely broken connection, on the real path.

        Closing the underlying connection makes the read raise where production
        would — inside ``_read_state`` — rather than at a stubbed seam. State
        that cannot be loaded must read as empty, never as an exception thrown
        through a policy evaluation.
        """
        empty_db.close()
        assert pc._read_state("s") == {}
        assert pc._write_state("s", {"n": 1}) is False


# ---------------------------------------------------------------------------
# 4. Still fails closed
# ---------------------------------------------------------------------------
class TestFailsClosed:
    def test_a_raising_policy_denies_and_keeps_its_level(self):
        def boom(_event):
            raise RuntimeError("kaboom")

        result = pc.compose(
            _event(),
            [_level("agent", ("boom", boom), on_policy_error=pe.DENY)],
            state=_state(),
        )
        assert result.effect == pe.DENY
        assert result.level == "agent"
        assert "kaboom" in result.reason

    def test_no_policy_anywhere_authorises_nothing(self):
        result = pc.compose(
            _event(), [_level("session"), _level("agent"), _level("server")],
            state=_state(),
        )
        assert result.effect == pe.ASK
        assert result.levels_consulted == ()

    def test_an_unregistered_name_denies_at_its_own_level(self):
        levels, _ = pc.load_levels(
            session={},
            agent={"chain": [{"name": "_pc_nope"}]},
            server={"chain": []},
        )
        result = pc.compose(_event(), levels, state=_state())
        assert result.effect == pe.DENY
        assert result.level == "agent"

    def test_an_unreadable_level_file_is_an_empty_level_not_a_crash(
        self, monkeypatch, tmp_path
    ):
        bad = tmp_path / "broken.yaml"
        bad.write_text("chain: [ unclosed", encoding="utf-8")
        monkeypatch.setenv(pc.AGENT_CONFIG_ENV, str(bad))
        levels, _ = pc.load_levels(session={}, server={"chain": []})
        assert levels[1].policy_names == ()

    def test_a_nonsense_return_value_denies(self):
        result = pc.compose(
            _event(), [_level("server", ("weird", lambda _e: 42))], state=_state()
        )
        assert result.effect == pe.DENY

    def test_the_composed_floor_can_only_raise_the_answer(self):
        levels = [
            _level("server", ("v", _policy(pe.ALLOW, name="v")), floors={"tool_call": "ask"})
        ]
        result = pc.compose(_event(), levels, state=_state())
        assert result.effect == pe.ASK
        assert result.floor_applied == pe.ASK
        assert result.floor_level == "server"

    def test_an_unparseable_floor_is_no_floor_not_a_crash(self):
        levels, relaxations = pc.load_levels(
            session={"chain": [], "floors": {"tool_call": "maybe"}},
            agent={}, server={"chain": []},
        )
        assert pc.composed_floor(levels, "tool_call") == (pe.ALLOW, "")
        assert any(r.attempted == "maybe" for r in relaxations)

    def test_a_hard_block_wins_before_any_policy(self, monkeypatch):
        monkeypatch.setattr(
            pc.gate, "_hard_block", lambda *a, **k: (True, "hook says no")
        )
        pe.register_policy("_pc_ok", _policy(pe.ALLOW, "fine"), replace=True)
        hook = pc.build_composed_policy_hook(
            session_id="s-hard",
            session_policy={"chain": [{"name": "_pc_ok"}]},
            agent_policy={"chain": []},
            server_policy={"chain": []},
        )
        blocked = hook("run_command", {"command": "rm -rf /"})
        assert blocked is not None and "hard block" in blocked


# ---------------------------------------------------------------------------
# 5. Audit and the shipped config
# ---------------------------------------------------------------------------
class TestAuditAndConfig:
    def test_the_audit_rule_names_the_deciding_level(self, monkeypatch):
        recorded: list[dict[str, Any]] = []
        monkeypatch.setattr(
            pc.gate, "record_decision", lambda **kw: recorded.append(kw) or True
        )
        pe.register_policy("_pc_deny", _policy(pe.DENY, "user said no"), replace=True)
        hook = pc.build_composed_policy_hook(
            session_id="s-audit",
            session_policy={"chain": [{"name": "_pc_deny"}]},
            agent_policy={"chain": []},
            server_policy={"chain": []},
        )
        hook("git_push", {"branch": "main"})
        assert len(recorded) == 1
        rule = recorded[0]["classification"].rule
        assert rule.startswith("policy_composition:session:")
        assert rule.endswith(":deny")

    def test_argument_values_never_reach_the_composition_record(self, monkeypatch):
        recorded: list[dict[str, Any]] = []
        monkeypatch.setattr(
            pc.gate, "record_decision", lambda **kw: recorded.append(kw) or True
        )
        pe.register_policy("_pc_deny", _policy(pe.DENY, "no"), replace=True)
        hook = pc.build_composed_policy_hook(
            session_id="s-cui",
            session_policy={"chain": [{"name": "_pc_deny"}]},
            agent_policy={"chain": []}, server_policy={"chain": []},
        )
        hook("write_file", {"path": "x", "content": "SECRET-CANARY"})
        assert recorded, "the deny must have been recorded"
        blob = " ".join(
            f"{kw['classification'].detail} {kw['classification'].rule} "
            f"{kw['decision'].reason}"
            for kw in recorded
        )
        assert "SECRET-CANARY" not in blob
        # tool_input IS handed to record_decision — that is the function whose
        # job is to reduce it to key names and a digest, and reusing it rather
        # than reimplementing it is the point (see record_composed_decision).
        assert set(recorded[0]["tool_input"]) == {"path", "content"}

    def test_the_shipped_config_declares_all_three_levels(self):
        """The prose in the config must match what the code reads."""
        import yaml

        raw = yaml.safe_load(
            (REPO_ROOT / "args" / "agent_policy_chain.yaml").read_text(encoding="utf-8")
        )
        assert "chain" in raw, "the top level is the server chain"
        assert pc.LEVEL_AGENT in raw, "the in-repo agent-level default must exist"
        levels, relaxations = pc.load_levels(server=raw)
        assert [lv.level for lv in levels] == list(pc.LEVELS)
        assert "reversibility" in levels[2].policy_names
        assert relaxations == [], f"the shipped config relaxes nothing: {relaxations}"

    def test_the_composition_result_is_json_safe(self):
        result = pc.compose(
            _event(), [_level("server", ("v", _policy(pe.ASK, name="v")))],
            state=_state(),
        )
        import json

        assert json.loads(json.dumps(result.as_dict()))["level"] == "server"
