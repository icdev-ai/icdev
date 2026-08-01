# CUI // SP-CTI
"""Unit tests for the AGX architecture registry + uniform envelope (agx-core-01).

Covers: registry round-trip, envelope schema stability, wrapped-architecture
call mapping ChainResult -> ArchitectureResult without loss, built-in
registration, budget injection, and honest degradation. No real LLM calls.
"""
from unittest.mock import patch

import pytest

from tools.llm.architectures import (
    ENVELOPE_SCHEMA_VERSION,
    ArchitectureBudget,
    ArchitectureNotFound,
    ArchitectureResult,
    ArchitectureStep,
    get,
    is_registered,
    list_architectures,
    register,
    run,
    unregister,
)
from tools.llm.chain_orchestrator import BudgetExceededError, ChainResult


# ---------------------------------------------------------------------------
# Registry round-trip
# ---------------------------------------------------------------------------
def test_register_get_list_unregister_round_trip():
    def _dummy(task, *, router=None, budget=None, function="architecture_run", **kw):
        return ArchitectureResult(architecture="dummy", output=str(task))

    register("dummy_arch", _dummy)
    try:
        assert is_registered("dummy_arch")
        assert "dummy_arch" in list_architectures()
        assert get("dummy_arch") is _dummy
        res = run("dummy_arch", "hello")
        assert isinstance(res, ArchitectureResult)
        assert res.output == "hello"
    finally:
        unregister("dummy_arch")
    assert not is_registered("dummy_arch")


def test_get_unknown_raises():
    with pytest.raises(ArchitectureNotFound):
        get("does_not_exist_arch")


def test_double_register_without_overwrite_raises():
    def _a(task, **kw):
        return ArchitectureResult(architecture="a")

    register("dup_arch", _a)
    try:
        with pytest.raises(ValueError):
            register("dup_arch", _a)
        register("dup_arch", _a, overwrite=True)  # explicit overwrite is allowed
    finally:
        unregister("dup_arch")


def test_register_rejects_non_callable_and_empty_name():
    with pytest.raises(ValueError):
        register("bad", "not-callable")
    with pytest.raises(ValueError):
        register("", lambda task, **kw: None)


# ---------------------------------------------------------------------------
# Envelope schema stability
# ---------------------------------------------------------------------------
def test_envelope_schema_keys_are_stable():
    res = ArchitectureResult(architecture="x")
    d = res.to_dict()
    assert set(d) == {
        "architecture", "output", "steps", "model_ids_used",
        "input_tokens", "output_tokens", "cost_usd", "duration_ms",
        "method", "degraded", "stop_reason", "trace_id",
        "schema_version", "metadata",
    }
    assert d["schema_version"] == ENVELOPE_SCHEMA_VERSION
    assert d["degraded"] is False


def test_step_schema_keys_are_stable():
    step = ArchitectureStep(name="s", model_ids=["m1"])
    d = step.to_dict()
    assert set(d) == {
        "name", "model_ids", "input_tokens", "output_tokens",
        "cost_usd", "duration_ms", "detail",
    }


def test_budget_to_dict():
    b = ArchitectureBudget(max_cost_usd=0.1, max_tokens=1000, max_seconds=30)
    assert b.to_dict() == {"max_cost_usd": 0.1, "max_tokens": 1000, "max_seconds": 30}


# ---------------------------------------------------------------------------
# Built-in architectures self-register
# ---------------------------------------------------------------------------
def test_builtins_registered():
    names = list_architectures()
    for expected in ("chain_of_thought", "chain_of_debate", "council", "react"):
        assert expected in names, f"{expected} not registered"


# ---------------------------------------------------------------------------
# Wrapped-architecture call maps ChainResult without loss
# ---------------------------------------------------------------------------
def _fake_chain_result():
    return ChainResult(
        content="final synthesized answer",
        chain_mode="cot",
        models_used=["qwen3-local", "claude-sonnet"],
        rounds=[
            {"step_name": "reason", "model_id": "qwen3-local", "input_tokens": 10,
             "output_tokens": 20, "cost_usd": 0.001, "duration_ms": 100},
            {"step_name": "synthesize", "model_id": "claude-sonnet", "input_tokens": 30,
             "output_tokens": 40, "cost_usd": 0.002, "duration_ms": 200},
        ],
        total_input_tokens=40,
        total_output_tokens=60,
        total_cost_usd=0.003,
        total_duration_ms=300,
        stop_reason="completed",
        trace_id="trace-xyz",
        confidence=0.0,
    )


def test_chain_of_thought_wrapper_maps_envelope_without_loss():
    fake = _fake_chain_result()
    with patch("tools.llm.chain_orchestrator.ChainOrchestrator") as MockOrch:
        inst = MockOrch.return_value
        inst._config = {}
        inst.invoke_chain_of_thought.return_value = fake
        res = run("chain_of_thought", "some task", function="unit_test_fn")

    assert res.architecture == "chain_of_thought"
    assert res.output == "final synthesized answer"
    assert res.method == "wrapped:cot"
    assert res.degraded is False
    assert res.model_ids_used == ["qwen3-local", "claude-sonnet"]
    assert res.input_tokens == 40
    assert res.output_tokens == 60
    assert res.cost_usd == pytest.approx(0.003)
    assert res.duration_ms == 300
    assert res.trace_id == "trace-xyz"
    assert [s.name for s in res.steps] == ["reason", "synthesize"]
    assert res.steps[0].model_ids == ["qwen3-local"]
    assert res.metadata["chain_mode"] == "cot"


def test_empty_content_chain_result_marks_degraded():
    fake = _fake_chain_result()
    fake.content = ""
    fake.stop_reason = "all_advisors_failed"
    with patch("tools.llm.chain_orchestrator.ChainOrchestrator") as MockOrch:
        inst = MockOrch.return_value
        inst._config = {}
        inst.invoke_council.return_value = fake
        res = run("council", "decide something")
    assert res.degraded is True
    assert res.output == ""
    assert res.stop_reason == "all_advisors_failed"


# ---------------------------------------------------------------------------
# Budget injection + honest degradation
# ---------------------------------------------------------------------------
def test_budget_injected_into_orchestrator_config():
    captured = {}

    class FakeOrch:
        def __init__(self, router=None):
            self._config = {}

        def invoke_chain_of_thought(self, function, request):
            captured["cost_cap"] = self._config.get("cost_cap_usd")
            captured["token_cap"] = self._config.get("token_cap")
            return _fake_chain_result()

    with patch("tools.llm.chain_orchestrator.ChainOrchestrator", FakeOrch):
        run("chain_of_thought", "t", budget=ArchitectureBudget(max_cost_usd=0.05, max_tokens=500))

    assert captured["cost_cap"] == 0.05
    assert captured["token_cap"] == 500


def test_budget_exceeded_returns_degraded_not_raises():
    class FakeOrch:
        def __init__(self, router=None):
            self._config = {}

        def invoke_chain_of_debate(self, function, request):
            raise BudgetExceededError("over budget")

    with patch("tools.llm.chain_orchestrator.ChainOrchestrator", FakeOrch):
        res = run("chain_of_debate", "t")
    assert res.degraded is True
    assert res.stop_reason == "budget_exceeded"
    assert res.output == ""


def test_unavailable_provider_degrades_for_airgap_parity():
    class FakeOrch:
        def __init__(self, router=None):
            self._config = {}

        def invoke_chain_of_thought(self, function, request):
            raise RuntimeError("no provider available")

    with patch("tools.llm.chain_orchestrator.ChainOrchestrator", FakeOrch):
        res = run("chain_of_thought", "t")
    assert res.degraded is True
    assert res.stop_reason == "unavailable"


def test_programming_errors_still_raise():
    class FakeOrch:
        def __init__(self, router=None):
            self._config = {}

        def invoke_chain_of_thought(self, function, request):
            raise ValueError("bug, not a runtime degrade")

    with patch("tools.llm.chain_orchestrator.ChainOrchestrator", FakeOrch):
        with pytest.raises(ValueError):
            run("chain_of_thought", "t")


def test_coerce_request_rejects_bad_type():
    with pytest.raises(TypeError):
        run("chain_of_thought", 12345)
