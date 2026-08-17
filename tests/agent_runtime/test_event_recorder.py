# CUI // SP-CTI
"""The event log is wired to a real turn — hcx-evt-02.

hcx-evt-01 built ``agent_session_events`` and its writer, and nothing called it:
a registered, importable, catalogued capability with zero consumers, which is the
defect CLAUDE.md names as the one this platform ships most. These tests are the
consumption proof, and they are deliberately about the four things that could go
wrong in the wiring rather than about the writer, which
``tests/test_agent_event_log.py`` already covers:

  1. **A turn produces the events, in order, in the real table.** Written through
     the migration's own DDL, so a column that exists in one and not the other
     fails here rather than inside somebody's ``except``.
  2. **The gate's semantics are untouched.** The recorder's pre-tool hook returns
     ``None`` on every path — including the paths where the log is broken — and
     composed through the loop's own ``_compose_pre_tool_hooks`` it can neither
     deny a call nor rescue one the approval gate denied.
  3. **The recorder cannot kill a turn.** An unwritable log degrades to a warning
     and a failure count; the turn still returns its result.
  4. **The log has no holes where it matters.** ``on_post_tool_use`` fires for
     every tool call but ``on_pre_tool_use`` does not — it is skipped for an
     unregistered tool and short-circuited for a gate-blocked one, i.e. exactly
     the two an auditor cares about. Those calls must still appear as
     ``tool_call``, tagged for what they are.
"""
from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

import tools.agent_runtime.sessions as sess_mod
from tests._sql_compat import translating
from tools.agent_runtime.event_log import MIGRATION, read_session
from tools.agent_runtime.event_recorder import (
    OBSERVED_POST,
    OBSERVED_PRE,
    RECORDING_ENV,
    TurnRecorder,
    recording_enabled,
)
from tools.agent_runtime.runtime import AgentRuntime

REPO_ROOT = Path(__file__).resolve().parents[2]

CTX = "ctx-evt-02"


# ---------------------------------------------------------------------------
# The real table, from the migration itself
# ---------------------------------------------------------------------------
def _events_ddl() -> str:
    return (
        REPO_ROOT / "tools" / "db" / "migrations" / MIGRATION / "up.sql"
    ).read_text(encoding="utf-8")


def _storage_module():
    """The module ``event_log._connect`` actually resolves ``get_connection`` from.

    ``tools.db.storage`` and ``icdev.tools.db.storage`` are two distinct module
    objects; patching the wrong one installs a fake nothing calls, and the test
    then asserts against its own no-op while the code under test writes to the
    LIVE board.
    """
    return sys.modules["tools.db.storage"]


def _translating_conn(raw: sqlite3.Connection):
    """The connection handed to the code under test (``%s`` → ``?``, unclosable)."""
    return translating(raw, unclosable=True)


@pytest.fixture
def event_db(monkeypatch, tmp_path):
    raw = sqlite3.connect(str(tmp_path / "events.db"))
    raw.executescript(_events_ddl())
    conn = _translating_conn(raw)
    storage = _storage_module()
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: conn)
    monkeypatch.setattr(storage, "table_exists", lambda c, t: True)
    monkeypatch.delenv("ICDEV_AGENT_EVENT_PAYLOAD_RETENTION", raising=False)
    monkeypatch.delenv(RECORDING_ENV, raising=False)
    yield raw
    raw.close()


def _events(session_id: str = CTX) -> list[dict[str, Any]]:
    return [e.to_dict() for e in read_session(session_id, include_payload=True)]


def _types(session_id: str = CTX) -> list[str]:
    return [e["event_type"] for e in _events(session_id)]


# ---------------------------------------------------------------------------
# Fakes — no DB, no LLM (mirrors tests/agent_runtime/test_interrupt.py)
# ---------------------------------------------------------------------------
class _FakeChatManager:
    def __init__(self) -> None:
        self._n = 0
        self.messages: dict[str, list[dict[str, Any]]] = {}
        self.configs: dict[str, dict] = {}

    def create_context(self, *, title="", **_kw) -> str:
        self._n += 1
        cid = f"ctx-{self._n}"
        self.messages[cid] = []
        return cid

    def add_message(self, context_id, *, role, content, **_kw) -> int:
        self.messages.setdefault(context_id, []).append(
            {"role": role, "content": content}
        )
        return len(self.messages[context_id])

    def get_messages(self, context_id, *, limit=200, offset=0):
        return list(self.messages.get(context_id, []))[offset : offset + limit]

    def update_title(self, context_id, title) -> None:
        pass

    def update_config(self, context_id, cfg) -> None:
        self.configs.setdefault(context_id, {}).update(cfg)


@dataclass
class _FakeResult:
    final_content: str = "done"
    session_id: str = "loop-sess-1"
    trace_id: str = ""
    truncation_reason: str = "completed"
    result_subtype: str = "success"
    done: bool = True
    truncated: bool = False
    turns: int = 1
    total_input_tokens: int = 10
    total_output_tokens: int = 5
    total_cost_usd: float = 0.001
    elapsed_seconds: float = 0.5
    messages: list = field(default_factory=list)


@dataclass
class _FakeResponse:
    content: str = ""
    stop_reason: str = "end_turn"
    model_id: str = "fake-model"
    provider: str = "fake"
    input_tokens: int = 7
    output_tokens: int = 3
    tool_calls: list = field(default_factory=list)


@pytest.fixture
def fake_manager(monkeypatch):
    mgr = _FakeChatManager()
    monkeypatch.setattr(sess_mod, "ChatManager", lambda *a, **k: mgr)
    return mgr


@pytest.fixture
def no_save(monkeypatch):
    import icdev.tools.llm.agent_loop_session as als

    monkeypatch.setattr(als, "save_session", lambda *a, **k: True)


# ---------------------------------------------------------------------------
# 1. A turn produces the events, in order, in the real table
# ---------------------------------------------------------------------------
class TestATurnIsRecorded:
    def test_a_tool_using_turn_writes_the_whole_sequence_in_order(self, event_db):
        rec = TurnRecorder.for_turn(CTX)
        rec.turn_start("find the bug")
        rec.on_turn(0, _FakeResponse(tool_calls=[{"id": "t1", "name": "read_file",
                                                  "input": {"path": "a.py"}}]))
        rec.on_pre_tool_use("read_file", {"path": "a.py"})
        rec.on_post_tool_use("read_file", {"path": "a.py"}, "contents", False)
        rec.on_turn(1, _FakeResponse(content="here it is"))
        rec.on_stop(_FakeResult())

        assert _types() == [
            "turn_start",
            "assistant_message",
            "tool_call",
            "tool_result",
            "assistant_message",
            "turn_end",
        ]

    def test_seq_is_dense_and_monotonic_across_the_turn(self, event_db):
        rec = TurnRecorder.for_turn(CTX)
        rec.turn_start("hi")
        for i in range(4):
            rec.on_turn(i, _FakeResponse(content=str(i)))
        rec.on_stop(_FakeResult())
        assert [e["seq"] for e in _events()] == [1, 2, 3, 4, 5, 6]

    def test_every_event_of_a_turn_shares_one_correlation_id(self, event_db):
        rec = TurnRecorder.for_turn(CTX)
        rec.turn_start("hi")
        rec.on_pre_tool_use("health_check", {})
        rec.on_post_tool_use("health_check", {}, "ok", False)
        rec.on_stop(_FakeResult())
        ids = {e["correlation_id"] for e in _events()}
        assert ids == {rec.correlation_id}
        assert rec.correlation_id.startswith("turn-")

    def test_two_turns_of_one_session_share_the_session_and_not_the_correlation(
        self, event_db
    ):
        # The whole reason session_id is the chat context id and not
        # AgentLoopResult.session_id: a fork at a seq (hcx-evt-05) needs both
        # turns in ONE ordered log.
        first = TurnRecorder.for_turn(CTX)
        first.turn_start("one")
        first.turn_end(_FakeResult())
        second = TurnRecorder.for_turn(CTX)
        second.turn_start("two")
        second.turn_end(_FakeResult())

        events = _events()
        assert [e["seq"] for e in events] == [1, 2, 3, 4]
        assert len({e["correlation_id"] for e in events}) == 2

    def test_turn_start_carries_the_user_input(self, event_db):
        rec = TurnRecorder.for_turn(CTX)
        rec.turn_start("why is the build red?", llm_function="code_generation")
        payload = _events()[0]["payload"]
        assert payload["user_input"] == "why is the build red?"
        assert payload["llm_function"] == "code_generation"

    def test_turn_end_carries_the_truncation_reason(self, event_db):
        rec = TurnRecorder.for_turn(CTX)
        rec.on_stop(_FakeResult(truncation_reason="max_iterations", done=False,
                                truncated=True))
        payload = _events()[0]["payload"]
        assert payload["truncation_reason"] == "max_iterations"
        assert payload["truncated"] is True
        # The loop's own per-run id is not lost, it is recorded as data.
        assert payload["loop_session_id"] == "loop-sess-1"

    def test_assistant_message_records_the_response_not_the_history(self, event_db):
        """The blob is the thing this table exists to stop being the only record.

        Storing ``messages`` per iteration would make the append-only log a
        slower copy of ``messages_json`` instead of a decomposition of it — and
        would re-persist the entire history once per model call.
        """
        history = [{"role": "user", "content": "x"}] * 50
        rec = TurnRecorder.for_turn(CTX)
        rec.on_turn(0, _FakeResponse(content="answer"), history)
        payload = _events()[0]["payload"]
        assert payload["content"] == "answer"
        assert payload["model_id"] == "fake-model"
        assert "messages" not in payload
        assert "history" not in payload

    def test_the_recorder_reports_what_it_wrote(self, event_db):
        rec = TurnRecorder.for_turn(CTX)
        rec.turn_start("hi")
        rec.on_stop(_FakeResult())
        summary = rec.summary()
        assert summary["recorded"] == 2
        assert summary["failures"] == 0
        assert summary["ended"] is True
        assert summary["session_id"] == CTX


# ---------------------------------------------------------------------------
# 2. Gate semantics are untouched
# ---------------------------------------------------------------------------
class TestTheGateStillWins:
    def test_the_pre_hook_returns_none_on_the_happy_path(self, event_db):
        rec = TurnRecorder.for_turn(CTX)
        assert rec.on_pre_tool_use("read_file", {"path": "a.py"}) is None

    def test_the_pre_hook_returns_none_when_the_log_is_broken(self, monkeypatch):
        """A logging hook must not become a denial because the DB fell over.

        Returning any string here would block the tool call — an audit outage
        would silently turn into a refusal, which is a far worse failure than a
        missing row.
        """
        def boom(*a, **k):
            raise RuntimeError("disk is full")

        rec = TurnRecorder.for_turn(CTX, appender=boom)
        assert rec.on_pre_tool_use("read_file", {"path": "a.py"}) is None
        assert rec.failures == 1

    def test_a_blocking_gate_still_blocks_when_composed_with_the_recorder(
        self, event_db
    ):
        """Through the loop's REAL composer, in the order the loop uses."""
        from icdev.tools.llm.agent_loop import _compose_pre_tool_hooks

        def gate(name, tool_input):
            return "BLOCKED: needs approval"

        rec = TurnRecorder.for_turn(CTX)
        composed = _compose_pre_tool_hooks(gate, rec.on_pre_tool_use)
        assert composed("rm", {"path": "/"}) == "BLOCKED: needs approval"

    def test_the_recorder_cannot_rescue_a_blocked_call_in_either_position(
        self, event_db
    ):
        """Composed FIRST it still cannot allow — the gate's message survives.

        The loop composes the caller's hook second (agent_loop.py:1395) so this
        is not the live ordering; it is asserted because "guards can only deny,
        never force-allow" has to hold on the property, not on the seating plan.
        """
        from icdev.tools.llm.agent_loop import _compose_pre_tool_hooks

        def gate(name, tool_input):
            return "BLOCKED: needs approval"

        rec = TurnRecorder.for_turn(CTX)
        composed = _compose_pre_tool_hooks(rec.on_pre_tool_use, gate)
        assert composed("rm", {"path": "/"}) == "BLOCKED: needs approval"

    def test_a_permissive_gate_is_not_turned_into_a_block(self, event_db):
        from icdev.tools.llm.agent_loop import _compose_pre_tool_hooks

        rec = TurnRecorder.for_turn(CTX)
        composed = _compose_pre_tool_hooks(lambda n, i: None, rec.on_pre_tool_use)
        assert composed("read_file", {"path": "a.py"}) is None

    def test_the_runtime_hands_the_recorder_to_on_pre_tool_use_not_approval_gate(
        self, event_db, fake_manager, no_save, monkeypatch
    ):
        """Passing it as ``approval_gate`` would seat it AHEAD of the real gate.

        ``_compose_pre_tool_hooks(_resolve_approval_gate(approval_gate), caller)``
        — whatever arrives as ``approval_gate`` is composed first and is the hook
        whose answer is taken as authoritative. The recorder must never be that
        argument.
        """
        captured: dict[str, Any] = {}

        def fake_loop(router, **kw):
            captured.update(kw)
            return _FakeResult()

        monkeypatch.setattr("icdev.tools.llm.agent_loop.run_agent_loop", fake_loop)
        rt = AgentRuntime(router=object())
        rt.run_turn("hi")
        assert "approval_gate" not in captured
        assert captured["on_pre_tool_use"].__self__.__class__ is TurnRecorder


# ---------------------------------------------------------------------------
# 3. The recorder cannot kill a turn
# ---------------------------------------------------------------------------
class TestAnUnwritableLogNeverEndsATurn:
    def test_every_hook_survives_an_appender_that_raises(self):
        def boom(*a, **k):
            raise RuntimeError("agent_session_events is missing")

        rec = TurnRecorder.for_turn(CTX, appender=boom)
        rec.turn_start("hi")
        rec.on_turn(0, _FakeResponse())
        rec.on_pre_tool_use("read_file", {"path": "a.py"})
        rec.on_post_tool_use("read_file", {"path": "a.py"}, "x", False)
        rec.on_stop(_FakeResult())
        assert rec.recorded == 0
        # turn_start, assistant_message, tool_call, tool_result, turn_end.
        assert rec.failures == 5

    def test_a_failing_recorder_does_not_stop_the_turn_returning_its_result(
        self, fake_manager, no_save, monkeypatch
    ):
        import tools.agent_runtime.event_recorder as er

        def boom(*a, **k):
            raise RuntimeError("disk is full")

        monkeypatch.setattr(er, "append", boom)
        monkeypatch.setattr(
            "icdev.tools.llm.agent_loop.run_agent_loop",
            lambda router, **kw: _FakeResult(final_content="the answer"),
        )
        rt = AgentRuntime(router=object())
        assert rt.run_turn("hi").final_content == "the answer"

    def test_keyboardinterrupt_is_not_swallowed_by_the_recorder(self):
        """The turn is interruptible by design; the audit log must not be why
        an operator cannot stop a run."""
        def interrupt(*a, **k):
            raise KeyboardInterrupt

        rec = TurnRecorder.for_turn(CTX, appender=interrupt)
        with pytest.raises(KeyboardInterrupt):
            rec.turn_start("hi")

    def test_a_transient_failure_does_not_disable_the_recorder_for_good(
        self, event_db
    ):
        """A recorder that latched off after one error would go on reporting a
        clean turn while writing nothing for the rest of the session."""
        calls = {"n": 0}
        real = TurnRecorder.for_turn(CTX)._append

        def flaky(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient")
            return real(*a, **k)

        rec = TurnRecorder.for_turn(CTX, appender=flaky)
        rec.turn_start("hi")
        rec.on_stop(_FakeResult())
        assert rec.failures == 1
        assert rec.recorded == 1
        assert _types() == ["turn_end"]

    def test_recording_can_be_stood_down_by_the_environment(
        self, event_db, monkeypatch
    ):
        monkeypatch.setenv(RECORDING_ENV, "0")
        assert recording_enabled() is False
        rec = TurnRecorder.for_turn(CTX)
        rec.turn_start("hi")
        rec.on_stop(_FakeResult())
        assert _events() == []
        assert rec.recorded == 0
        assert rec.failures == 0  # off is not broken

    def test_recording_is_on_by_default(self, monkeypatch):
        """The defect this card closes is a capability nobody consumes. Shipping
        it default-off would be that defect with an extra step."""
        monkeypatch.delenv(RECORDING_ENV, raising=False)
        assert recording_enabled() is True

    def test_a_session_with_no_id_records_nothing_rather_than_a_junk_row(self):
        rec = TurnRecorder("", appender=lambda *a, **k: pytest.fail("wrote"))
        rec.turn_start("hi")
        assert rec.recorded == 0
        assert rec.failures == 0


# ---------------------------------------------------------------------------
# 4. No holes where they matter
# ---------------------------------------------------------------------------
class TestBlockedAndUnregisteredCallsStillAppear:
    def test_a_call_whose_pre_hook_never_ran_gets_a_reconstructed_tool_call(
        self, event_db
    ):
        """The gate blocked it, so the composed chain returned before reaching
        the recorder — but ``on_post_tool_use`` still fires (agent_loop.py:1903).
        Without this the DENIED calls would be the ones with no ``tool_call``."""
        rec = TurnRecorder.for_turn(CTX)
        rec.on_post_tool_use(
            "run_command", {"cmd": "rm -rf /"}, "BLOCKED: needs approval", True
        )
        events = _events()
        assert [e["event_type"] for e in events] == ["tool_call", "tool_result"]
        assert events[0]["payload"]["observed"] == OBSERVED_POST
        assert events[0]["payload"]["name"] == "run_command"
        assert events[1]["payload"]["is_error"] is True

    def test_a_normal_call_is_tagged_as_seen_by_the_pre_hook(self, event_db):
        rec = TurnRecorder.for_turn(CTX)
        rec.on_pre_tool_use("read_file", {"path": "a.py"})
        rec.on_post_tool_use("read_file", {"path": "a.py"}, "contents", False)
        events = _events()
        assert [e["event_type"] for e in events] == ["tool_call", "tool_result"]
        assert events[0]["payload"]["observed"] == OBSERVED_PRE

    def test_two_identical_calls_pair_one_for_one(self, event_db):
        """Pairing by presence alone would treat the second post-hook as
        unpaired and synthesise a duplicate — a turn may legitimately call one
        tool twice with identical inputs."""
        rec = TurnRecorder.for_turn(CTX)
        rec.on_pre_tool_use("read_file", {"path": "a.py"})
        rec.on_pre_tool_use("read_file", {"path": "a.py"})
        rec.on_post_tool_use("read_file", {"path": "a.py"}, "x", False)
        rec.on_post_tool_use("read_file", {"path": "a.py"}, "x", False)
        events = _events()
        assert [e["event_type"] for e in events] == [
            "tool_call", "tool_call", "tool_result", "tool_result"
        ]
        assert {e["payload"]["observed"] for e in events[:2]} == {OBSERVED_PRE}

    def test_parallel_read_only_calls_keep_their_dispatch_order(self, event_db):
        """The loop fires every read-only pre-hook, THEN collects the results —
        the log records dispatch and collection as they happened rather than
        pretending each call completed before the next began."""
        rec = TurnRecorder.for_turn(CTX)
        rec.on_pre_tool_use("read_file", {"path": "a.py"})
        rec.on_pre_tool_use("search_files", {"q": "bug"})
        rec.on_post_tool_use("read_file", {"path": "a.py"}, "A", False)
        rec.on_post_tool_use("search_files", {"q": "bug"}, "B", False)
        assert _types() == ["tool_call", "tool_call", "tool_result", "tool_result"]

    def test_an_input_whose_key_order_differs_still_pairs(self, event_db):
        """Dict order is a property of how the input was built, never of what it
        means — the same argument ``compute_payload_hash`` makes one layer down."""
        rec = TurnRecorder.for_turn(CTX)
        rec.on_pre_tool_use("write", {"a": 1, "b": 2})
        rec.on_post_tool_use("write", {"b": 2, "a": 1}, "ok", False)
        assert [e["payload"].get("observed") for e in _events()][0] == OBSERVED_PRE
        assert _types() == ["tool_call", "tool_result"]


def _assert_ordered_subset(actual, expected):
    """`expected` occurs in `actual`, in order, ignoring events in between.

    These tests are about WHICH events a path records and in what order — never
    about the absence of others. Pinning the exact list made every one of them a
    tripwire for any new event type, and hcx-evt-03 is that: `request_context`
    now lands on every context injection, so four assertions broke while the
    properties they exist to protect were never in question.

    A subsequence check still fails on a missing event, on a reordered one, and
    on a duplicate that breaks the order — it only stops asserting that nothing
    ELSE was recorded, which was never the claim.
    """
    it = iter(actual)
    missing = [e for e in expected if not any(a == e for a in it)]
    assert not missing, (
        f"{missing} missing from {actual!r} (or out of order)"
    )


# ---------------------------------------------------------------------------
# 5. turn_end happens exactly once, on every exit path
# ---------------------------------------------------------------------------
class TestTurnEndIsExactlyOnce:
    def test_on_stop_and_the_finally_backstop_do_not_both_write(self, event_db):
        rec = TurnRecorder.for_turn(CTX)
        rec.on_stop(_FakeResult())
        rec.turn_end(_FakeResult())  # the runtime's belt-and-braces call
        assert _types() == ["turn_end"]

    def test_a_loop_that_raises_still_closes_the_turn(
        self, event_db, fake_manager, no_save, monkeypatch
    ):
        """``on_stop`` fires on every exit path the LOOP controls; an exception
        out of the loop is not one of them, and a turn with no ``turn_end`` reads
        to a fork (hcx-evt-05) as a turn still open."""
        def boom(router, **kw):
            raise RuntimeError("provider exploded")

        monkeypatch.setattr("icdev.tools.llm.agent_loop.run_agent_loop", boom)
        rt = AgentRuntime(router=object())
        monkeypatch.setattr(rt.session, "context_id", CTX)
        with pytest.raises(RuntimeError, match="provider exploded"):
            rt.run_turn("hi")
        types = _types()
        # BRACKETS, not the whole list. What this test is about is that the turn
        # is CLOSED on a path the loop does not control — so it asserts the first
        # and last events and that turn_end happens once. Pinning the exact
        # sequence made it a tripwire for any new event type recorded between
        # them, and hcx-evt-03 is exactly that: `request_context` now lands on
        # every context injection, which broke this assertion while the property
        # it exists to protect was never in question.
        assert types[0] == "turn_start"
        assert types[-1] == "turn_end"
        assert types.count("turn_end") == 1, "exactly once, on every exit path"
        assert "turn_start" not in types[1:]
        assert _events()[-1]["payload"]["truncation_reason"] == "loop_raised:RuntimeError"

    def test_a_stopped_turn_records_the_stop_reason(self, event_db):
        rec = TurnRecorder.for_turn(CTX)
        rec.on_stop(_FakeResult(truncation_reason="stop_event", done=False))
        assert _events()[0]["payload"]["truncation_reason"] == "stop_event"


# ---------------------------------------------------------------------------
# 6. run_turn wires all four hooks, and keeps its old contract
# ---------------------------------------------------------------------------
class TestRunTurnWiring:
    def _capture(self, monkeypatch) -> dict[str, Any]:
        captured: dict[str, Any] = {}

        def fake_loop(router, **kw):
            captured.update(kw)
            return _FakeResult()

        monkeypatch.setattr("icdev.tools.llm.agent_loop.run_agent_loop", fake_loop)
        return captured

    def test_all_four_hooks_reach_the_loop(
        self, event_db, fake_manager, no_save, monkeypatch
    ):
        captured = self._capture(monkeypatch)
        AgentRuntime(router=object()).run_turn("hi")
        for hook in ("on_turn", "on_pre_tool_use", "on_post_tool_use", "on_stop"):
            assert captured[hook].__self__.__class__ is TurnRecorder, hook
        # One recorder for the turn — not four.
        assert len({id(captured[h].__self__) for h in
                    ("on_turn", "on_pre_tool_use", "on_post_tool_use", "on_stop")}) == 1

    def test_the_turn_correlation_id_is_handed_to_the_loop(
        self, event_db, fake_manager, no_save, monkeypatch
    ):
        """So an event row joins the loop's OTel span and ``result.trace_id``."""
        captured = self._capture(monkeypatch)
        AgentRuntime(router=object()).run_turn("hi")
        recorder = captured["on_stop"].__self__
        assert captured["correlation_id"] == recorder.correlation_id

    def test_each_turn_gets_its_own_recorder(
        self, event_db, fake_manager, no_save, monkeypatch
    ):
        """Pre/post pairing state must not leak across turns."""
        seen: list[Any] = []

        def fake_loop(router, **kw):
            seen.append(kw["on_stop"].__self__)
            return _FakeResult()

        monkeypatch.setattr("icdev.tools.llm.agent_loop.run_agent_loop", fake_loop)
        rt = AgentRuntime(router=object())
        rt.run_turn("one")
        rt.run_turn("two")
        assert seen[0] is not seen[1]
        assert seen[0].correlation_id != seen[1].correlation_id

    def test_the_events_land_under_the_chat_context_id(
        self, event_db, fake_manager, no_save, monkeypatch
    ):
        """NOT ``AgentLoopResult.session_id``, which is a fresh UUID per call and
        would make every user message its own one-turn 'session'."""
        self._capture(monkeypatch)
        rt = AgentRuntime(router=object())
        ctx_id = rt.session.context_id
        rt.run_turn("hi")
        _assert_ordered_subset(_types(ctx_id), ["turn_start", "turn_end"])
        assert read_session("loop-sess-1") == []

    def test_the_existing_persistence_contract_is_unchanged(
        self, event_db, fake_manager, no_save, monkeypatch
    ):
        """ADDITIVE: ``messages_json`` stays the resume path."""
        self._capture(monkeypatch)
        rt = AgentRuntime(router=object())
        rt.run_turn("hi")
        assert rt.session.resume_session_id == "loop-sess-1"
        assert rt.session.turn_count == 1
        assert fake_manager.messages[rt.session.context_id][-1] == {
            "role": "assistant", "content": "done"
        }

    def test_turn_active_is_still_cleared_when_the_loop_raises(
        self, event_db, fake_manager, no_save, monkeypatch
    ):
        def boom(router, **kw):
            raise RuntimeError("nope")

        monkeypatch.setattr("icdev.tools.llm.agent_loop.run_agent_loop", boom)
        rt = AgentRuntime(router=object())
        with pytest.raises(RuntimeError):
            rt.run_turn("hi")
        assert rt.turn_active is False

    def test_an_unimportable_recorder_degrades_to_a_null_one(
        self, fake_manager, no_save, monkeypatch
    ):
        """A runtime that refused to answer because its audit log is missing
        would be a worse outcome than one that answers and says so."""
        def explode(*a, **k):
            raise ImportError("event_recorder is not installed")

        monkeypatch.setattr(TurnRecorder, "for_turn", explode)
        captured = self._capture(monkeypatch)
        rt = AgentRuntime(router=object())
        assert rt.run_turn("hi").final_content == "done"
        assert captured["on_pre_tool_use"]("anything", {}) is None


# ---------------------------------------------------------------------------
# 7. The consumption proof itself
# ---------------------------------------------------------------------------
class TestTheCapabilityIsActuallyConsumed:
    def test_a_turn_through_the_real_loop_writes_rows(
        self, event_db, fake_manager, no_save
    ):
        """End to end: the REAL ``run_agent_loop`` with a scripted provider.

        This is the assertion hcx-evt-01 could not make and hcx-evt-06 verifies
        against the live database — ``agent_session_events`` holds rows because
        an agent turn put them there, not because a test called ``append``.
        """
        from icdev.tools.llm.provider import LLMResponse

        class _Router:
            def get_provider_for_function(self, function: str):
                return (
                    type("P", (), {"provider_name": "fake"})(),
                    "m",
                    {"supports_tools": True},
                )

            def invoke(self, function, request):
                return LLMResponse(
                    content="the answer", stop_reason="end_turn", provider="fake"
                )

        rt = AgentRuntime(router=_Router())
        ctx_id = rt.session.context_id
        result = rt.run_turn("what is the answer?")

        assert result.done is True
        _assert_ordered_subset(
            _types(ctx_id), ["turn_start", "assistant_message", "turn_end"])
        # THE COMPLEMENT OF LOOSENING THE ASSERTIONS ABOVE.
        #
        # Four assertions in this file used to pin the exact event sequence, and
        # hcx-evt-03 relaxed them to ordered-subset checks so a new event type
        # would stop being a tripwire. That trade is only honest if something
        # still asserts the new event IS recorded — otherwise the relaxation is
        # a coverage cut wearing a refactor's clothes, and `request_context`
        # could stop being emitted entirely without this file noticing.
        #
        # `tests/agent_runtime/test_context_events.py` covers the event's own
        # contract; this covers its place in a REAL turn's lifecycle, which is
        # what this file is for.
        _assert_ordered_subset(
            _types(ctx_id),
            ["turn_start", "request_context", "assistant_message", "turn_end"],
        )
        end = _events(ctx_id)[-1]["payload"]
        assert end["truncation_reason"] == "completed"
        assert end["loop_session_id"] == result.session_id
        # The join the correlation_id column exists for.
        assert _events(ctx_id)[0]["correlation_id"] == result.trace_id

    def test_the_stop_event_path_is_recorded_too(self, event_db, fake_manager, no_save):
        class _Router:
            def get_provider_for_function(self, function: str):
                return (
                    type("P", (), {"provider_name": "fake"})(),
                    "m",
                    {"supports_tools": True},
                )

            def invoke(self, function, request):  # pragma: no cover — never reached
                raise AssertionError("the loop should have stopped first")

        rt = AgentRuntime(router=_Router())
        ctx_id = rt.session.context_id
        rt.stop()
        rt.run_turn("do the thing")
        _assert_ordered_subset(_types(ctx_id), ["turn_start", "turn_end"])
        assert _events(ctx_id)[-1]["payload"]["truncation_reason"] == "stop_event"
