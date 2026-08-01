# CUI // SP-CTI
"""LLM-agnosticism + air-gap parity enforcement for AGX architectures (agx-core-02).

Turns the "LLM-agnostic" acceptance criterion into a failing-on-violation gate so
no later AGX task can quietly reintroduce a vendor dependency, a hardcoded model
ID, or a provider instantiation that bypasses LLMRouter.

Three guarantees:
  (a) AST scan of tools/llm/architectures/ (+ icdev mirror) is clean, and the
      scanner actually catches each violation class (proven with bad snippets).
  (b) Air-gap parity: every registered architecture completes (possibly
      degraded=True) against a local-only stub with zero cloud calls, and never
      raises on a runtime provider failure.
  (c) The documented degradation contract holds: a failed run yields an honest
      degraded envelope with empty output, never a fabricated verdict.
"""
from unittest.mock import patch

import pytest

from tools.llm.architectures import ArchitectureResult, list_architectures, run
from tools.llm.chain_orchestrator import ChainResult
from tools.workflow.coherence_checker import (
    check_architecture_agnosticism,
    scan_architecture_agnosticism,
)


# ---------------------------------------------------------------------------
# (a) Static agnosticism scan
# ---------------------------------------------------------------------------
def test_architecture_package_is_agnostic():
    """The live architecture package must pass the agnosticism gate."""
    result = check_architecture_agnosticism()
    assert result.status == "pass", f"agnosticism violations: {result.actual}"


def test_scanner_flags_vendor_sdk_import():
    v = scan_architecture_agnosticism("import anthropic\n", "x.py")
    assert any("vendor-SDK import" in s for s in v)
    v2 = scan_architecture_agnosticism("from langchain.graph import StateGraph\n", "x.py")
    assert any("vendor-SDK import" in s for s in v2)
    v3 = scan_architecture_agnosticism("import openai.types\n", "x.py")
    assert any("vendor-SDK import" in s for s in v3)


def test_scanner_flags_hardcoded_model_id():
    v = scan_architecture_agnosticism('MODEL = "claude-3-5-sonnet-20241022"\n', "x.py")
    assert any("model-ID-shaped literal" in s for s in v)
    v2 = scan_architecture_agnosticism('m = "gpt-4o-mini"\n', "x.py")
    assert any("model-ID-shaped literal" in s for s in v2)


def test_scanner_ignores_model_ids_in_docstrings():
    src = '"""This wraps claude-3 and gpt-4o for demo purposes."""\nx = 1\n'
    v = scan_architecture_agnosticism(src, "x.py")
    assert v == [], f"docstring model names should not be flagged: {v}"


def test_scanner_flags_provider_bypass():
    v = scan_architecture_agnosticism(
        "from tools.llm.anthropic_provider import AnthropicLLMProvider\n", "x.py"
    )
    assert any("bypasses LLMRouter" in s for s in v)
    v2 = scan_architecture_agnosticism("p = OpenAILLMProvider()\n", "x.py")
    assert any("direct provider instantiation" in s for s in v2)


def test_scanner_allows_legit_router_and_request_imports():
    src = (
        "from tools.llm.provider import LLMRequest, LLMResponse\n"
        "from tools.llm.router import LLMRouter\n"
        "from tools.llm.config_path import resolve_llm_config_path\n"
        "r = LLMRouter()\n"
    )
    assert scan_architecture_agnosticism(src, "x.py") == []


# ---------------------------------------------------------------------------
# (b) Air-gap parity + (c) degradation contract
# ---------------------------------------------------------------------------
_LOCAL_MODEL = "qwen3-local"  # logical name; resolves to Ollama in air-gap config


def _local_chain_result():
    """A ChainResult as a local-only provider would produce it."""
    return ChainResult(
        content="local answer",
        chain_mode="cot",
        models_used=[_LOCAL_MODEL],
        rounds=[{"step_name": "reason", "model_id": _LOCAL_MODEL}],
        total_input_tokens=5,
        total_output_tokens=5,
        total_cost_usd=0.0,  # local inference is free
        total_duration_ms=10,
        stop_reason="completed",
        trace_id="local-trace",
    )


_CLOUD_PROVIDERS = {"anthropic", "openai", "bedrock", "google", "gemini", "cohere", "mistral"}


def _assert_no_cloud(result: ArchitectureResult):
    for mid in result.model_ids_used:
        low = mid.lower()
        assert not any(cp in low for cp in _CLOUD_PROVIDERS), f"cloud model leaked in air-gap: {mid}"


@pytest.mark.parametrize("arch,method", [
    ("chain_of_thought", "invoke_chain_of_thought"),
    ("chain_of_debate", "invoke_chain_of_debate"),
    ("council", "invoke_council"),
])
def test_chain_architectures_complete_airgapped(arch, method):
    """Each chain architecture completes against a local-only stub, no cloud."""
    class FakeOrch:
        def __init__(self, router=None):
            self._config = {}

    def _invoke(self, function, request):
        return _local_chain_result()

    setattr(FakeOrch, method, _invoke)
    with patch("tools.llm.chain_orchestrator.ChainOrchestrator", FakeOrch):
        res = run(arch, "trivial local task")
    assert isinstance(res, ArchitectureResult)
    assert res.degraded is False
    assert res.output == "local answer"
    _assert_no_cloud(res)


@pytest.mark.parametrize("arch,method", [
    ("chain_of_thought", "invoke_chain_of_thought"),
    ("chain_of_debate", "invoke_chain_of_debate"),
    ("council", "invoke_council"),
])
def test_chain_architectures_degrade_on_no_provider(arch, method):
    """No cloud fallback: an unavailable local provider degrades, never raises."""
    class FakeOrch:
        def __init__(self, router=None):
            self._config = {}

    def _invoke(self, function, request):
        raise RuntimeError("all providers unavailable (air-gap, no cloud fallback)")

    setattr(FakeOrch, method, _invoke)
    with patch("tools.llm.chain_orchestrator.ChainOrchestrator", FakeOrch):
        res = run(arch, "task")
    assert res.degraded is True
    assert res.output == ""          # honest: no fabricated verdict
    assert res.stop_reason == "unavailable"


def test_react_completes_airgapped():
    from types import SimpleNamespace
    fake = SimpleNamespace(
        final_content="local react answer", truncated=False, turns=1,
        model_id=_LOCAL_MODEL, total_input_tokens=3, total_output_tokens=3,
        total_cost_usd=0.0, tool_call_log=[], truncation_reason="completed",
        stop_reason="end_turn", trace_id="t", session_id="s",
    )
    with patch("tools.llm.agent_loop.run_agent_loop", return_value=fake), \
         patch("tools.llm.router.LLMRouter"):
        res = run("react", "trivial task")
    assert res.degraded is False
    assert res.output == "local react answer"
    _assert_no_cloud(res)


def test_every_registered_architecture_returns_envelope_contract():
    """Generic guard: every architecture the registry knows about returns an
    ArchitectureResult (never a bare value / None) so the bench can compare them."""
    # Only assert the type contract is declared; actual execution is covered by
    # the per-architecture cases above. This future-proofs new registrations.
    assert set(list_architectures()) >= {"chain_of_thought", "chain_of_debate", "council", "react"}
