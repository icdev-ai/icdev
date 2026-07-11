"""Phase 3b dispatch wiring: flag-gated rubric loop vs unchanged air-gap path.

Uses the shim-aware pattern (patch the module object the executor actually
imports) and fakes only — no real LLM, no real gates, no threads.
"""
import importlib

import pytest

kmod = importlib.import_module("tools.genesis.reflexes.kanban")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("KANBAN_RUBRIC_LOOP", raising=False)
    yield


class _FakeHandle:
    """Stands in for _LLMTaskHandle: records start(), spawns no thread."""

    started = False

    def __init__(self, task_id, log_path):
        self.task_id = task_id
        self.log_path = log_path

    def start(self, target, args=()):
        type(self).started = True  # old air-gap path was taken


def test_flag_default_off():
    assert kmod._rubric_loop_enabled() is False


@pytest.mark.parametrize("val,expected", [("1", True), ("true", True), ("on", True), ("0", False), ("", False)])
def test_flag_parsing(monkeypatch, val, expected):
    monkeypatch.setenv("KANBAN_RUBRIC_LOOP", val)
    assert kmod._rubric_loop_enabled() is expected


def test_flag_off_uses_unchanged_path(monkeypatch, tmp_path):
    _FakeHandle.started = False
    calls = []
    monkeypatch.setattr(kmod, "_dispatch_via_rubric_loop", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(kmod, "_LLMTaskHandle", _FakeHandle)
    monkeypatch.setattr(kmod, "_running", {})
    monkeypatch.setattr(kmod, "_dispatch_times", {})
    kmod._dispatch_via_llm_router(
        {"id": "t1"}, str(tmp_path / "p"), "do it", str(tmp_path), tmp_path / "log"
    )
    assert calls == []              # rubric loop NOT used
    assert _FakeHandle.started is True  # old path taken


def test_flag_on_routes_to_rubric_loop(monkeypatch, tmp_path):
    _FakeHandle.started = False
    monkeypatch.setenv("KANBAN_RUBRIC_LOOP", "1")
    calls = []
    monkeypatch.setattr(kmod, "_dispatch_via_rubric_loop", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(kmod, "_LLMTaskHandle", _FakeHandle)
    kmod._dispatch_via_llm_router(
        {"id": "t2"}, "p", "do it", str(tmp_path), tmp_path / "log"
    )
    assert len(calls) == 1
    assert calls[0] == ({"id": "t2"}, "p", "do it", str(tmp_path), tmp_path / "log")
    assert _FakeHandle.started is False  # old path NOT taken


def test_rubric_loop_invokes_primitive_with_grader_and_tools(monkeypatch, tmp_path):
    """flag-on real _dispatch_via_rubric_loop wires grader + toolset into the primitive."""
    captured = {}

    class _FakeAR:
        done = True
        total_cost_usd = 0.0

    class _FakeResult:
        satisfied = True
        grading_attempts = 1
        result = _FakeAR()

    def _fake_rubric(router, **kwargs):
        captured.update(kwargs)
        captured["router"] = router
        return _FakeResult()

    # Patch the module objects the executor imports inside _runner. The rubric
    # primitive lives in the CANONICAL icdev copy (root is a stale shim).
    agent_loop = importlib.import_module("icdev.tools.llm.agent_loop")
    router_mod = importlib.import_module("tools.llm.router")
    monkeypatch.setattr(agent_loop, "run_agent_loop_with_rubric", _fake_rubric)
    monkeypatch.setattr(router_mod, "LLMRouter", lambda *a, **k: object())

    # Run _runner synchronously via a fake handle.
    class _SyncHandle(_FakeHandle):
        def start(self, target, args=()):
            target(*args)

    monkeypatch.setattr(kmod, "_LLMTaskHandle", _SyncHandle)
    monkeypatch.setattr(kmod, "_running", {})
    monkeypatch.setattr(kmod, "_dispatch_times", {})

    kmod._dispatch_via_rubric_loop(
        {"id": "t3"}, "p", "implement X", str(tmp_path), tmp_path / "log"
    )

    assert callable(captured["grader"])                    # pipeline grader wired
    assert captured["tool_handlers"].get("write_file")     # file-editing toolset wired
    assert len(captured["tools"]) >= 4
    assert captured["llm_function"] == "code_generation"   # routes by function name (LLM-agnostic)
    assert captured["user_prompt"] == "implement X"
