# CUI // SP-CTI
"""hgx-exec-02: tools/agents/adapters/local_agent.py — the owned build adapter.

Covers the card's three binding acceptance criteria:
  1. satisfies the AgentAdapter protocol and is returned by pick_default()
     under ICDEV_AGENT_ADAPTER=local_agent,
  2. degrades (does not raise) on AgentLoopUnsupported with a recorded reason,
  3. no model id appears in the module — every call routes by llm_function.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

import pytest

from tools.agents import registry
from tools.agents.adapter_base import AgentAdapter, AgentSession
from tools.agents.adapters import local_agent as la


@pytest.fixture(autouse=True)
def _clean_registry():
    registry.reset()
    yield
    registry.reset()


# ---------------------------------------------------------------------------
# 1. Protocol conformance + selection
# ---------------------------------------------------------------------------


def test_satisfies_agent_adapter_protocol():
    assert isinstance(la.ADAPTER, AgentAdapter)
    assert la.ADAPTER.name == "local_agent"


def test_registered_in_the_registry():
    assert "local_agent" in registry.list_adapters()
    assert registry.get_adapter("local_agent") is la.ADAPTER


def test_pick_default_returns_it_when_forced(monkeypatch):
    monkeypatch.setenv("ICDEV_AGENT_ADAPTER", "local_agent")
    assert registry.pick_default() is la.ADAPTER


def test_enabled_in_the_yaml_config():
    import yaml

    cfg_path = Path(registry._CONFIG_PATH)  # noqa: SLF001
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert "local_agent" in (cfg.get("enabled_adapters") or [])


# ---------------------------------------------------------------------------
# 2. Degradation on AgentLoopUnsupported
# ---------------------------------------------------------------------------


class _FakeLoopModule:
    """Stand-in for icdev.tools.llm.agent_loop."""

    def __init__(self, raise_unsupported: bool = False, rubric_result=None):
        from icdev.tools.llm.agent_loop import AgentLoopUnsupported

        self.AgentLoopUnsupported = AgentLoopUnsupported
        self._raise = raise_unsupported
        self._result = rubric_result
        self.kwargs = None

    def run_agent_loop_with_rubric(self, router, **kwargs):
        self.kwargs = kwargs
        if self._raise:
            raise self.AgentLoopUnsupported(
                "CLI bridge provider cannot serve native tool-use requests"
            )
        return self._result


def _arm(monkeypatch, tmp_path, loop_mod):
    """Make invoke() reach the loop without touching a router or the gates."""
    monkeypatch.setattr(la.LocalAgentAdapter, "available", lambda self: True)
    monkeypatch.setattr(la, "_import_agent_loop", lambda: loop_mod)
    monkeypatch.setattr(
        la.LocalAgentAdapter,
        "_build_session",
        lambda self, session, work_dir: ([{"name": "x"}], {"x": lambda i, s: ""},
                                         lambda result=None: None, object()),
    )
    return AgentSession(
        task_id="hgx-exec-02-test",
        prompt="Do the thing",
        working_dir=str(tmp_path),
    )


def test_degrades_without_raising_when_tool_use_unsupported(monkeypatch, tmp_path):
    loop_mod = _FakeLoopModule(raise_unsupported=True)
    session = _arm(monkeypatch, tmp_path, loop_mod)

    calls = {}

    class _Fallback:
        def invoke(self, sess):
            from tools.agents.adapter_base import AgentResult

            calls["invoked"] = sess.task_id
            return AgentResult(
                task_id=sess.task_id,
                adapter_name="local_llm_router",
                completed=True,
                output="a written plan",
                structured={"provider": "cli"},
            )

    monkeypatch.setattr(
        "tools.agents.adapters.local_llm_router.ADAPTER", _Fallback()
    )

    result = la.ADAPTER.invoke(session)  # must NOT raise

    assert result.structured["degraded"] is True
    assert "native tool-use" in result.structured["degraded_reason"]
    assert result.structured["degraded_to"] == "local_llm_router"
    assert calls["invoked"] == "hgx-exec-02-test"
    assert result.output == "a written plan"
    # Prose is advisory: nothing was edited and no gate passed.
    assert result.completed is False
    # ...but nothing errored either, so this is not a failure exit.
    assert result.exit_code == 0


def test_degrades_even_when_the_fallback_adapter_itself_raises(monkeypatch, tmp_path):
    loop_mod = _FakeLoopModule(raise_unsupported=True)
    session = _arm(monkeypatch, tmp_path, loop_mod)

    class _Broken:
        def invoke(self, sess):
            raise RuntimeError("no chain")

    monkeypatch.setattr("tools.agents.adapters.local_llm_router.ADAPTER", _Broken())

    result = la.ADAPTER.invoke(session)  # still must NOT raise

    assert result.structured["degraded"] is True
    assert result.structured["fallback_failed"] is True
    assert "no chain" in result.error
    assert result.completed is False


class _FakeProvider:
    def __init__(self, provider_name):
        self.provider_name = provider_name


class _FakeRouter:
    """Router whose resolved provider drives the REAL capability guard."""

    def __init__(self, provider_name, model_config=None):
        self._provider = _FakeProvider(provider_name)
        self._model_config = model_config or {}

    def get_provider_for_function(self, _llm_function):
        # No model id: the guard only inspects provider_name + supports_tools.
        return self._provider, "", self._model_config


def _arm_real_loop(monkeypatch, tmp_path, router):
    """Same as _arm but keeps the REAL agent_loop, so its own guard fires."""
    monkeypatch.setattr(la.LocalAgentAdapter, "available", lambda self: True)
    monkeypatch.setattr(
        la.LocalAgentAdapter,
        "_build_session",
        lambda self, session, work_dir: ([], {}, lambda result=None: None, router),
    )
    monkeypatch.setattr(
        "tools.agents.adapters.local_llm_router.ADAPTER", _NoopFallback()
    )
    return AgentSession(task_id="t-real", prompt="p", working_dir=str(tmp_path))


class _NoopFallback:
    def invoke(self, sess):
        from tools.agents.adapter_base import AgentResult

        return AgentResult(
            task_id=sess.task_id,
            adapter_name="local_llm_router",
            completed=True,
            output="prose",
        )


def test_cli_bridge_degrades_through_the_real_capability_guard(monkeypatch, tmp_path):
    """The CLI bridge flattens tools to text — degrade, don't fail the run."""
    session = _arm_real_loop(monkeypatch, tmp_path, _FakeRouter("cli"))

    result = la.ADAPTER.invoke(session)

    assert result.structured["degraded"] is True
    assert "CLI bridge" in result.structured["degraded_reason"]
    assert result.completed is False


def test_non_tool_capable_model_degrades_through_the_real_guard(monkeypatch, tmp_path):
    """supports_tools=false resolves by CONFIG, never by matching a model id."""
    session = _arm_real_loop(
        monkeypatch, tmp_path, _FakeRouter("ollama", {"supports_tools": False})
    )

    result = la.ADAPTER.invoke(session)

    assert result.structured["degraded"] is True
    assert "supports_tools=false" in result.structured["degraded_reason"]
    assert result.completed is False
    assert result.output == "prose"


def test_loop_failure_is_returned_not_raised(monkeypatch, tmp_path):
    class _Boom:
        AgentLoopUnsupported = _FakeLoopModule().AgentLoopUnsupported

        def run_agent_loop_with_rubric(self, router, **kwargs):
            raise ValueError("provider exploded")

    session = _arm(monkeypatch, tmp_path, _Boom())
    result = la.ADAPTER.invoke(session)

    assert result.completed is False
    assert result.exit_code == 1
    assert "provider exploded" in result.error
    assert result.structured["degraded"] is False


def test_missing_working_dir_returns_an_error_result(monkeypatch, tmp_path):
    session = _arm(monkeypatch, tmp_path, _FakeLoopModule())
    session.working_dir = str(tmp_path / "does-not-exist")

    result = la.ADAPTER.invoke(session)

    assert result.completed is False
    assert result.exit_code == 2
    assert "not a directory" in result.error


# ---------------------------------------------------------------------------
# 3. LLM-agnostic: routes by function, never by model id
# ---------------------------------------------------------------------------


_MODEL_ID_PATTERNS = (
    r"claude-[a-z0-9]",
    r"\bgpt-[0-9a-z]",
    r"\bllama[-0-9]",
    r"\bmistral[-0-9]",
    r"\bgemini-[0-9a-z]",
    r"\bqwen[-0-9]",
    r"\bdeepseek-",
    r"anthropic\.",
    r"\bo[13]-(mini|preview)",
)


def test_module_contains_no_model_id():
    source = Path(la.__file__).read_text(encoding="utf-8")
    for pattern in _MODEL_ID_PATTERNS:
        assert not re.search(pattern, source, re.IGNORECASE), (
            f"model id matching {pattern!r} found in local_agent.py — route by "
            "llm_function through LLMRouter instead"
        )


def test_routes_by_llm_function_and_defaults_to_code_generation(monkeypatch, tmp_path):
    loop_mod = _FakeLoopModule(rubric_result=_rubric(satisfied=True))
    session = _arm(monkeypatch, tmp_path, loop_mod)

    la.ADAPTER.invoke(session)
    assert loop_mod.kwargs["llm_function"] == "code_generation"

    session.metadata = {"llm_function": "reasoning"}
    la.ADAPTER.invoke(session)
    assert loop_mod.kwargs["llm_function"] == "reasoning"


# ---------------------------------------------------------------------------
# Result mapping — the gates decide "done", not the model
# ---------------------------------------------------------------------------


def _rubric(satisfied: bool, done: bool = True):
    from icdev.tools.llm.agent_loop import AgentLoopResult, RubricLoopResult

    return RubricLoopResult(
        result=AgentLoopResult(
            done=done, final_content="built it", turns=4, session_id="s1"
        ),
        satisfied=satisfied,
        grading_attempts=1,
    )


def test_completed_tracks_the_grader_not_the_loop(monkeypatch, tmp_path):
    # Loop ended cleanly (done=True) but the pipeline said no.
    loop_mod = _FakeLoopModule(rubric_result=_rubric(satisfied=False, done=True))
    session = _arm(monkeypatch, tmp_path, loop_mod)

    result = la.ADAPTER.invoke(session)

    assert result.structured["loop_done"] is True
    assert result.completed is False
    assert result.exit_code == 1
    assert "not satisfied" in result.error


def test_completed_when_the_pipeline_is_satisfied(monkeypatch, tmp_path):
    loop_mod = _FakeLoopModule(rubric_result=_rubric(satisfied=True))
    session = _arm(monkeypatch, tmp_path, loop_mod)

    result = la.ADAPTER.invoke(session)

    assert result.completed is True
    assert result.exit_code == 0
    assert result.error == ""
    assert result.output == "built it"
    assert result.adapter_name == "local_agent"
    assert result.structured["degraded"] is False


def test_stop_event_from_metadata_is_forwarded(monkeypatch, tmp_path):
    loop_mod = _FakeLoopModule(rubric_result=_rubric(satisfied=True))
    session = _arm(monkeypatch, tmp_path, loop_mod)
    ev = threading.Event()
    session.metadata = {"stop_event": ev}

    la.ADAPTER.invoke(session)

    assert loop_mod.kwargs["stop_event"] is ev


def test_budgets_derive_from_the_session_timeout(monkeypatch, tmp_path):
    loop_mod = _FakeLoopModule(rubric_result=_rubric(satisfied=True))
    session = _arm(monkeypatch, tmp_path, loop_mod)
    session.timeout_seconds = 1000

    la.ADAPTER.invoke(session)

    # Held under the caller's ceiling so the loop stops itself.
    assert loop_mod.kwargs["max_wall_clock_seconds"] == pytest.approx(900.0)
    assert loop_mod.kwargs["max_wall_clock_seconds"] < session.timeout_seconds
    # Floors protect a tiny timeout from producing a zero budget.
    assert la._budget(0, la._GATE_BUDGET_SHARE) == la._MIN_BUDGET_SECONDS


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_detect_completion_is_a_pure_string_heuristic():
    assert la.ADAPTER.detect_completion("All delivery-pipeline gates passed.") is True
    assert la.ADAPTER.detect_completion("[DONE]") is True
    assert la.ADAPTER.detect_completion("") is False
    assert la.ADAPTER.detect_completion("I will start by reading the file") is False


def test_prepare_prompt_carries_the_completion_contract():
    session = AgentSession(task_id="t", prompt="Fix the bug", working_dir=".")
    prompt = la.ADAPTER.prepare_prompt(session)
    assert "Fix the bug" in prompt
    assert "call done" in prompt


def test_parse_response_shape():
    parsed = la.ADAPTER.parse_response("hello")
    assert parsed == {"content": "hello", "tool_calls": [], "diff": ""}


def test_changed_files_thunk_never_raises(tmp_path):
    thunk = la._changed_files_thunk(str(tmp_path / "nope"))
    assert thunk() == []


def test_available_never_raises(monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("boom")

    # Shim-aware: `import tools.llm.router` yields the icdev module object,
    # while sys.modules['tools.llm.router'] is the physical shim — patch both
    # so whichever one available()'s import binds is the patched one.
    import importlib
    import sys

    for mod in {
        importlib.import_module("tools.llm.router"),
        sys.modules.get("tools.llm.router"),
    }:
        if mod is not None:
            monkeypatch.setattr(mod, "LLMRouter", _boom)

    assert la.ADAPTER.available() is False
