# CUI // SP-CTI
"""Ctrl-C stops the turn, not the process (hgx-ctxw-03).

Three defects, one fix:

* ``AgentRuntime._stop`` was handed to the agent loop as its ``stop_event`` and
  then never set by anything — there was no ``AgentRuntime.stop()``.
* The REPL wrapped each turn in ``except Exception``, which does not catch
  ``KeyboardInterrupt`` (a ``BaseException``), so Ctrl-C during a turn took the
  whole process down instead of the turn.
* Nothing installed a SIGINT handler at all.

The signal tests use ``signal.signal`` + ``signal.raise_signal``, both of which
behave identically on Windows and POSIX — no ``SIGBREAK``, no process groups, no
``add_signal_handler``. Windows delivers the interrupt to the main thread only,
which is why the handler's job is to set a ``threading.Event`` rather than to
count on a worker seeing an exception. ``raise_signal`` sets a pending call that
CPython runs at the next bytecode boundary, so every assertion that expects the
escalation to raise spins briefly to give the interpreter that boundary.
"""
from __future__ import annotations

import signal
import threading
from dataclasses import dataclass, field
from typing import Any

import pytest

import tools.agent_runtime.sessions as sess_mod
from tools.agent_runtime.runtime import AgentRuntime, install_interrupt_handler


# ---------------------------------------------------------------------------
# Fakes (mirrors tests/agent_runtime/test_runtime.py — no DB)
# ---------------------------------------------------------------------------


class _FakeChatManager:
    def __init__(self) -> None:
        self._n = 0
        self.messages: dict[str, list[dict[str, Any]]] = {}

    def create_context(self, *, title="", **_kw) -> str:
        self._n += 1
        cid = f"ctx-{self._n}"
        self.messages[cid] = []
        return cid

    def add_message(self, context_id, *, role, content, **_kw) -> int:
        self.messages.setdefault(context_id, []).append({"role": role, "content": content})
        return len(self.messages[context_id])

    def get_messages(self, context_id, *, limit=200, offset=0):
        return list(self.messages.get(context_id, []))[offset : offset + limit]

    def update_title(self, context_id, title) -> None:
        pass


@dataclass
class _FakeResult:
    final_content: str = ""
    session_id: str = "sess-1"
    truncation_reason: str = "completed"
    total_input_tokens: int = 10
    total_output_tokens: int = 5
    total_cost_usd: float = 0.001
    messages: list = field(default_factory=list)


@pytest.fixture
def fake_manager(monkeypatch):
    mgr = _FakeChatManager()
    monkeypatch.setattr(sess_mod, "ChatManager", lambda *a, **k: mgr)
    return mgr


@pytest.fixture
def no_save(monkeypatch):
    import icdev.tools.llm.agent_loop_session as als

    monkeypatch.setattr(als, "save_session", lambda *a, **k: True)


@pytest.fixture(autouse=True)
def restore_sigint():
    """No test may leave this process's SIGINT disposition altered."""
    try:
        previous = signal.getsignal(signal.SIGINT)
    except (ValueError, OSError):  # pragma: no cover
        yield
        return
    yield
    signal.signal(signal.SIGINT, previous)


class _StubRuntime:
    """The two properties + one method the handler actually touches."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._active = False
        self.messages: list[str] = []

    @property
    def turn_active(self) -> bool:
        return self._active

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def stop(self) -> None:
        self._stop.set()


def _spin() -> None:
    """Give CPython a bytecode boundary to run the pending signal handler."""
    for _ in range(10_000):
        pass


# ---------------------------------------------------------------------------
# AgentRuntime.stop()
# ---------------------------------------------------------------------------


class TestStopApi:
    def test_stop_sets_the_token_the_loop_is_already_watching(self, fake_manager):
        rt = AgentRuntime(router=object())
        assert rt.stopping is False
        rt.stop()
        assert rt.stopping is True
        assert rt.stop_event.is_set()
        rt.clear_stop()
        assert rt.stopping is False

    def test_the_token_handed_to_the_loop_is_the_one_stop_sets(
        self, fake_manager, no_save, monkeypatch
    ):
        """The pre-fix bug in one assertion: the objects must be identical."""
        captured: dict[str, Any] = {}

        def fake_loop(router, **kw):
            captured.update(kw)
            return _FakeResult(final_content="x")

        monkeypatch.setattr("icdev.tools.llm.agent_loop.run_agent_loop", fake_loop)
        rt = AgentRuntime(router=object())
        rt.stop()
        rt.run_turn("hi")
        assert captured["stop_event"] is rt.stop_event
        assert captured["stop_event"].is_set()

    def test_run_turn_exits_at_the_next_boundary_with_stop_event(
        self, fake_manager, no_save, monkeypatch
    ):
        """AC 2, end to end through the real loop with a scripted provider."""
        from icdev.tools.llm.provider import LLMResponse

        class _Router:
            def get_provider_for_function(self, function: str):
                return type("P", (), {"provider_name": "anthropic"})(), "m", {
                    "supports_tools": True
                }

            def invoke(self, function, request):  # pragma: no cover — never reached
                return LLMResponse(content="nope", stop_reason="end_turn", provider="f")

        rt = AgentRuntime(router=_Router())
        rt.stop()
        result = rt.run_turn("do the thing")
        assert result.truncation_reason == "stop_event"
        assert result.truncated is False

    def test_turn_active_is_only_set_during_a_turn(
        self, fake_manager, no_save, monkeypatch
    ):
        seen: list[bool] = []

        def fake_loop(router, **kw):
            seen.append(rt.turn_active)
            return _FakeResult()

        monkeypatch.setattr("icdev.tools.llm.agent_loop.run_agent_loop", fake_loop)
        rt = AgentRuntime(router=object())
        assert rt.turn_active is False
        rt.run_turn("hi")
        assert seen == [True]
        assert rt.turn_active is False

    def test_turn_active_is_cleared_even_when_the_turn_raises(
        self, fake_manager, monkeypatch
    ):
        def boom(router, **kw):
            raise RuntimeError("provider down")

        monkeypatch.setattr("icdev.tools.llm.agent_loop.run_agent_loop", boom)
        rt = AgentRuntime(router=object())
        with pytest.raises(RuntimeError):
            rt.run_turn("hi")
        assert rt.turn_active is False


# ---------------------------------------------------------------------------
# The SIGINT handler
# ---------------------------------------------------------------------------


class TestInterruptHandler:
    def test_it_installs_and_restores_the_previous_disposition(self):
        before = signal.getsignal(signal.SIGINT)
        stub = _StubRuntime()
        with install_interrupt_handler(stub, stub.messages.append) as installed:
            assert installed is True
            assert signal.getsignal(signal.SIGINT) is not before
        assert signal.getsignal(signal.SIGINT) is before

    def test_ctrl_c_during_a_turn_sets_the_token_instead_of_raising(self):
        """The core of AC 1: the process is not interrupted, the turn is."""
        stub = _StubRuntime()
        stub._active = True
        with install_interrupt_handler(stub, stub.messages.append):
            signal.raise_signal(signal.SIGINT)
            _spin()
            assert stub.stopping is True
        assert any("stopping" in m for m in stub.messages)

    def test_ctrl_c_at_the_prompt_still_raises(self):
        """No turn running → Ctrl-C keeps meaning 'leave the REPL'."""
        stub = _StubRuntime()
        stub._active = False
        with install_interrupt_handler(stub, stub.messages.append):
            with pytest.raises(KeyboardInterrupt):
                signal.raise_signal(signal.SIGINT)
                _spin()
        assert stub.stopping is False

    def test_a_second_ctrl_c_escalates(self):
        """A handler ignoring the token must still be escapable."""
        stub = _StubRuntime()
        stub._active = True
        with install_interrupt_handler(stub, stub.messages.append):
            signal.raise_signal(signal.SIGINT)
            _spin()
            assert stub.stopping is True
            with pytest.raises(KeyboardInterrupt):
                signal.raise_signal(signal.SIGINT)
                _spin()

    def test_it_degrades_to_a_no_op_off_the_main_thread(self):
        """``signal.signal`` raises off the main thread — that must not kill the REPL."""
        stub = _StubRuntime()
        outcome: dict[str, Any] = {}

        def _worker() -> None:
            try:
                with install_interrupt_handler(stub, stub.messages.append) as installed:
                    outcome["installed"] = installed
                outcome["ok"] = True
            except BaseException as exc:  # noqa: BLE001 — the point of the test
                outcome["error"] = exc

        t = threading.Thread(target=_worker)
        t.start()
        t.join(timeout=10)
        assert outcome.get("ok") is True, outcome
        assert outcome["installed"] is False

    def test_a_failing_output_fn_cannot_break_the_handler(self):
        """A signal handler that raises corrupts an unrelated stack frame."""
        stub = _StubRuntime()
        stub._active = True

        def _bad(_msg: str) -> None:
            raise OSError("stdout is gone")

        with install_interrupt_handler(stub, _bad):
            signal.raise_signal(signal.SIGINT)
            _spin()
            assert stub.stopping is True


# ---------------------------------------------------------------------------
# The REPL
# ---------------------------------------------------------------------------


class TestReplSurvivesCtrlC:
    def test_ctrl_c_mid_turn_returns_to_the_prompt(
        self, fake_manager, no_save, monkeypatch
    ):
        """AC 1 through the real REPL: signal → stop → prompt → next turn.

        ``run_turn`` here stands in for a real turn: it marks itself active,
        takes the interrupt, observes the token, and reports the same
        ``truncation_reason`` the loop would.
        """
        turns: list[str] = []

        def interrupted_turn(self_rt, text):
            turns.append(text)
            self_rt._turn_active.set()
            try:
                signal.raise_signal(signal.SIGINT)
                _spin()
                assert self_rt.stopping, "the handler did not set the token"
                return _FakeResult(final_content="", truncation_reason="stop_event")
            finally:
                self_rt._turn_active.clear()

        rt = AgentRuntime(router=object())
        monkeypatch.setattr(
            AgentRuntime, "run_turn", lambda s, t: interrupted_turn(s, t)
        )

        inputs = iter(["long running thing", "/exit"])
        outputs: list[str] = []
        rt.loop(input_fn=lambda _p: next(inputs), output_fn=outputs.append, banner=False)

        # The process survived, the turn was reported as stopped, and the REPL
        # went on to read (and act on) the next line.
        assert turns == ["long running thing"]
        assert any("Turn stopped" in o for o in outputs)
        assert any("Goodbye" in o for o in outputs)

    def test_the_token_is_cleared_so_the_next_turn_runs(
        self, fake_manager, no_save, monkeypatch
    ):
        """A stop must not be sticky — turn 2 would exit instantly at boundary 0."""
        seen_stopping: list[bool] = []

        def turn(self_rt, text):
            seen_stopping.append(self_rt.stopping)
            if text == "first":
                self_rt.stop()
                return _FakeResult(truncation_reason="stop_event")
            return _FakeResult(final_content="second answer")

        monkeypatch.setattr(AgentRuntime, "run_turn", lambda s, t: turn(s, t))
        rt = AgentRuntime(router=object())
        inputs = iter(["first", "second", "/exit"])
        outputs: list[str] = []
        rt.loop(input_fn=lambda _p: next(inputs), output_fn=outputs.append, banner=False)

        assert seen_stopping == [False, False]
        assert any("second answer" in o for o in outputs)

    def test_an_escalated_keyboard_interrupt_does_not_kill_the_repl(
        self, fake_manager, monkeypatch
    ):
        """``except Exception`` never caught this — that is why Ctrl-C killed the process."""
        def boom(self_rt, text):
            raise KeyboardInterrupt

        monkeypatch.setattr(AgentRuntime, "run_turn", lambda s, t: boom(s, t))
        rt = AgentRuntime(router=object())
        inputs = iter(["wedged", "/exit"])
        outputs: list[str] = []
        rt.loop(input_fn=lambda _p: next(inputs), output_fn=outputs.append, banner=False)

        assert any("Turn interrupted" in o for o in outputs)
        assert any("Goodbye" in o for o in outputs)

    def test_a_stopped_turn_still_shows_its_partial_answer(
        self, fake_manager, no_save, monkeypatch
    ):
        monkeypatch.setattr(
            AgentRuntime,
            "run_turn",
            lambda s, t: _FakeResult(
                final_content="I got this far", truncation_reason="stop_event"
            ),
        )
        rt = AgentRuntime(router=object())
        inputs = iter(["go", "/exit"])
        outputs: list[str] = []
        rt.loop(input_fn=lambda _p: next(inputs), output_fn=outputs.append, banner=False)

        assert "I got this far" in outputs
        assert any("Turn stopped" in o for o in outputs)


class TestAcceptanceCriterionOne:
    """The whole path, with nothing about the interrupt faked.

    A real ``AgentRuntime.loop``, a real ``run_agent_loop`` turn, a provider
    that blocks, and a genuine SIGINT delivered *asynchronously* from another
    thread — the same shape as a console Ctrl-C. ``signal.raise_signal`` from a
    worker still runs the Python-level handler on the **main** thread (CPython
    schedules it there on every platform), which is precisely the Windows
    behaviour the handler is written for.
    """

    def test_ctrl_c_during_a_real_turn_returns_to_the_prompt(
        self, fake_manager, no_save
    ):
        from icdev.tools.llm.provider import LLMResponse

        entered = threading.Event()
        release = threading.Event()

        class BlockingRouter:
            def get_provider_for_function(self, function: str):
                provider = type("P", (), {"provider_name": "anthropic"})()
                return provider, "fake-model", {"supports_tools": True}

            def invoke(self, function, request):
                entered.set()
                release.wait(timeout=30)  # a slow provider, ignoring the token
                return LLMResponse(
                    content="too late", stop_reason="end_turn", provider="fake"
                )

        rt = AgentRuntime(router=BlockingRouter(), llm_function="code_generation")

        def _press_ctrl_c() -> None:
            entered.wait(timeout=20)
            signal.raise_signal(signal.SIGINT)

        presser = threading.Thread(target=_press_ctrl_c, daemon=True)

        prompts: list[str] = []
        outputs: list[str] = []

        def _input(prompt: str) -> str:
            prompts.append(prompt)
            if len(prompts) == 1:
                presser.start()
                return "something long running"
            return "/exit"

        try:
            rt.loop(input_fn=_input, output_fn=outputs.append, banner=False)
        finally:
            release.set()
            presser.join(timeout=5)

        assert entered.is_set(), "the turn never reached the provider"
        # Back at the prompt (asked for input a second time), turn reported as
        # stopped, process intact.
        assert len(prompts) == 2, f"the REPL did not prompt again: {outputs}"
        assert any("Turn stopped" in o for o in outputs), outputs
        assert any("Goodbye" in o for o in outputs)
        # And the token was re-armed for the next turn.
        assert rt.stopping is False


# ---------------------------------------------------------------------------
# The token reaches the handlers (BUILD item 3)
# ---------------------------------------------------------------------------


class TestTheTokenReachesHandlers:
    """A cancellation is only cooperative if the token actually arrives.

    ``dispatch.make_handler`` injects it into any handler whose signature
    declares ``stop_event``; the mutating built-ins declare it and are expected
    to poll it. Both halves are asserted here — an injected token that the
    handler discards is the bug this pair exists to catch.
    """

    def test_dispatch_injects_the_token_into_a_declaring_handler(self):
        from tools.agent_runtime.discovery import ToolSpec
        from tools.agent_runtime.dispatch import make_handler

        spec = ToolSpec(
            name="run_command",
            schema={},
            source="decorated",
            read_only=False,
            module="tools.agent_runtime.mutating_tools",
            handler="run_command",
        )
        handler = make_handler(spec, gate=lambda _n, _i, _ro: (True, ""))
        stop = threading.Event()
        stop.set()

        # A stopped run: the tool must decline rather than launch a child.
        out = handler({"command": "python -c \"print(1)\""}, stop)
        assert "cancelled" in out

    def test_run_command_does_not_launch_a_child_after_a_stop(self, monkeypatch):
        import tools.agent_runtime.mutating_tools as mt
        import tools.skills.invoke as invoke_mod

        launched: list[str] = []
        monkeypatch.setattr(
            invoke_mod,
            "run_command",
            lambda cmd, args, **kw: launched.append(cmd) or {"returncode": 0},
        )
        stop = threading.Event()
        stop.set()

        out = mt.run_command("python -c \"print(1)\"", stop_event=stop)
        assert launched == [], "a stopped run started another subprocess"
        assert out == mt._CANCELLED

    def test_write_file_does_not_mutate_the_tree_after_a_stop(self, tmp_path, monkeypatch):
        import tools.agent_runtime.mutating_tools as mt

        monkeypatch.setattr(mt, "_REPO_ROOT", tmp_path.resolve(), raising=False)
        stop = threading.Event()
        stop.set()

        out = mt.write_file("scratch/out.txt", "payload", stop_event=stop)
        assert out == mt._CANCELLED
        assert not (tmp_path / "scratch" / "out.txt").exists()

    def test_the_tools_still_work_when_nothing_is_stopped(self, tmp_path, monkeypatch):
        """The guard must not fire on the ordinary path."""
        import tools.agent_runtime.mutating_tools as mt

        monkeypatch.setattr(mt, "_REPO_ROOT", tmp_path.resolve(), raising=False)
        out = mt.write_file("scratch/out.txt", "payload", stop_event=threading.Event())
        assert "wrote" in out
        assert (tmp_path / "scratch" / "out.txt").read_text(encoding="utf-8") == "payload"
