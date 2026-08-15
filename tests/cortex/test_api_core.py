# CUI // SP-CTI
"""Tests for the Cortex complete/classify/extract facades (ctx-core-02).

The router is monkeypatched shim-aware: ``tools.llm`` and ``icdev.tools.llm``
are distinct module objects for from-imports, so patches go through
``importlib.import_module('tools.llm')`` + ``setattr`` — the module the
facade resolves ``get_router`` from at call time.
"""
import importlib
import json
import re
from pathlib import Path

import pytest

from tools.cortex import CortexContext, CortexResult, classify, complete, extract
# Reached through the module, not as a from-import: test_airgap_assertion.py
# reloads tools.cortex.api and mints a new exception class, which a bound name
# would not follow (the raise would stop matching pytest.raises).
from tools.cortex import api as _cortex_api
from tools.llm.provider import LLMResponse


class FakeRouter:
    """Captures invoke() calls; returns a canned response or raises."""

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def invoke(self, function, request, **kwargs):
        self.calls.append((function, request))
        if self.error is not None:
            raise self.error
        return self.response


@pytest.fixture
def install_router(monkeypatch):
    def _install(router):
        llm_pkg = importlib.import_module("tools.llm")
        monkeypatch.setattr(llm_pkg, "get_router", lambda config_path=None: router)
        return router

    return _install


@pytest.fixture
def patch_classify_query(monkeypatch):
    def _patch(result):
        qc = importlib.import_module("tools.rag.query_classifier")
        monkeypatch.setattr(qc, "classify_query", lambda text, context="": result)

    return _patch


def _response(**overrides):
    defaults = dict(
        content="hello from the fake provider",
        provider="fake-provider",
        model_id="logical-model-name",
        cost_usd=0.0123,
        duration_ms=42,
        input_tokens=10,
        output_tokens=5,
    )
    defaults.update(overrides)
    return LLMResponse(**defaults)


# ---------------------------------------------------------------------------
# complete()
# ---------------------------------------------------------------------------
def test_complete_returns_cortex_result_with_accounting(install_router):
    install_router(FakeRouter(response=_response()))
    result = complete("say hello")
    assert isinstance(result, CortexResult)
    assert result.text == "hello from the fake provider"
    assert result.provider == "fake-provider"
    assert result.model == "logical-model-name"
    assert result.cost == pytest.approx(0.0123)
    assert result.latency_ms == 42
    assert result.grounded is False
    assert result.citations == []


def test_complete_routes_via_default_function_name(install_router):
    router = install_router(FakeRouter(response=_response()))
    complete("say hello")
    function, _ = router.calls[0]
    assert function == "cortex_complete"


def test_complete_honors_function_override(install_router):
    router = install_router(FakeRouter(response=_response()))
    complete("say hello", function="proposal_drafting")
    function, _ = router.calls[0]
    assert function == "proposal_drafting"


def test_complete_threads_context_into_request(install_router):
    router = install_router(FakeRouter(response=_response()))
    ctx = CortexContext(tenant_id="tenant-a", classification="SECRET")
    complete("say hello", ctx=ctx)
    _, request = router.calls[0]
    assert request.tenant_id == "tenant-a"
    assert request.classification == "SECRET"


def test_complete_defaults_context_when_none(install_router):
    router = install_router(FakeRouter(response=_response()))
    complete("say hello")
    _, request = router.calls[0]
    assert request.tenant_id == ""
    assert request.classification == "CUI"


def test_complete_accepts_context_dict(install_router):
    router = install_router(FakeRouter(response=_response()))
    complete("say hello", ctx={"tenant_id": "tenant-b", "classification": "CUI"})
    _, request = router.calls[0]
    assert request.tenant_id == "tenant-b"


def test_complete_measures_latency_when_provider_omits_duration(install_router):
    install_router(FakeRouter(response=_response(duration_ms=0)))
    result = complete("say hello")
    assert isinstance(result.latency_ms, int)
    assert result.latency_ms >= 0


def test_complete_propagates_router_errors(install_router):
    install_router(FakeRouter(error=RuntimeError("chain exhausted")))
    with pytest.raises(RuntimeError):
        complete("say hello")


# ---------------------------------------------------------------------------
# classify()
# ---------------------------------------------------------------------------
def test_classify_returns_matched_label(install_router):
    router = install_router(FakeRouter(response=_response(content="Positive.")))
    result = classify("great product", ["positive", "negative"])
    assert isinstance(result, CortexResult)
    assert result.text == "positive"
    function, request = router.calls[0]
    assert function == "cortex_classify"
    assert request.classification == "CUI"


def test_classify_threads_context(install_router):
    router = install_router(FakeRouter(response=_response(content="negative")))
    classify("bad", ["positive", "negative"], ctx=CortexContext(tenant_id="t-9"))
    _, request = router.calls[0]
    assert request.tenant_id == "t-9"


def test_classify_matches_label_inside_sentence(install_router):
    install_router(FakeRouter(response=_response(content="The label is NEGATIVE, clearly.")))
    result = classify("bad", ["positive", "negative"])
    assert result.text == "negative"


def test_classify_requires_labels(install_router):
    install_router(FakeRouter(response=_response()))
    with pytest.raises(ValueError):
        classify("text", [])


def test_classify_degrades_to_query_classifier_when_router_raises(
    install_router, patch_classify_query
):
    install_router(FakeRouter(error=RuntimeError("no provider available")))
    patch_classify_query(
        {"label": "reasoning", "confidence": 0.8, "method": "heuristic", "reasoning": "x"}
    )
    result = classify("why does zero trust matter?", ["fact_single", "reasoning"])
    assert result.text == "reasoning"
    assert result.provider == "deterministic"
    assert result.model == "query_classifier"
    assert result.cost == 0.0


def test_classify_fallback_uses_first_label_when_taxonomy_disjoint(
    install_router, patch_classify_query
):
    install_router(FakeRouter(error=RuntimeError("no provider available")))
    patch_classify_query(
        {"label": "summary", "confidence": 0.8, "method": "heuristic", "reasoning": "x"}
    )
    result = classify("some text", ["alpha", "beta"])
    assert result.text == "alpha"
    assert result.provider == "deterministic"


def test_classify_degrades_when_llm_answer_matches_no_label(
    install_router, patch_classify_query
):
    install_router(FakeRouter(response=_response(content="I cannot decide.")))
    patch_classify_query(
        {"label": "fact_single", "confidence": 0.8, "method": "heuristic", "reasoning": "x"}
    )
    result = classify("what is AC-2?", ["fact_single", "summary"])
    assert result.text == "fact_single"
    assert result.provider == "deterministic"


# ---------------------------------------------------------------------------
# extract()
# ---------------------------------------------------------------------------
def test_extract_prefers_structured_output(install_router):
    router = install_router(
        FakeRouter(response=_response(structured_output={"name": "ICDEV", "year": 2026}))
    )
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    result = extract("ICDEV shipped in 2026", schema)
    assert json.loads(result.text) == {"name": "ICDEV", "year": 2026}
    function, request = router.calls[0]
    assert function == "cortex_extract"
    assert request.output_schema == schema


def test_extract_parses_fenced_json_content(install_router):
    content = 'Here you go:\n```json\n{"name": "ICDEV"}\n```'
    install_router(FakeRouter(response=_response(content=content)))
    result = extract("text", {"type": "object"})
    assert json.loads(result.text) == {"name": "ICDEV"}


def test_extract_refuses_rather_than_falling_back_to_raw_content(install_router):
    """It used to hand the raw completion back as ``result.text`` (trust-struct-03).

    ``{"type": "object"}`` is inside the contract subset, so the payload is held
    to it: prose is a refusal, not a fallback. The old best-effort return is
    still reachable, but only by asking for it at the call site.
    """
    install_router(FakeRouter(response=_response(content="not json at all")))
    with pytest.raises(_cortex_api.CortexSchemaError):
        extract("text", {"type": "object"})

    degraded = extract("text", {"type": "object"}, on_invalid="return")
    assert degraded.text == "not json at all"
    assert degraded.metadata["schema_valid"] is False


def test_extract_threads_context_and_accounting(install_router):
    router = install_router(FakeRouter(response=_response(structured_output={"a": 1})))
    ctx = CortexContext(tenant_id="t-1", classification="SECRET")
    result = extract("text", {"type": "object"}, ctx=ctx)
    _, request = router.calls[0]
    assert request.tenant_id == "t-1"
    assert request.classification == "SECRET"
    assert result.provider == "fake-provider"
    assert result.latency_ms == 42


def test_extract_propagates_router_errors(install_router):
    install_router(FakeRouter(error=RuntimeError("chain exhausted")))
    with pytest.raises(RuntimeError):
        extract("text", {"type": "object"})


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------
_MODEL_ID_FRAGMENTS = re.compile(
    r"claude|gpt|gemini|qwen|llama|kimi|mistral|codestral|deepseek|minimax|haiku|sonnet|opus",
    re.IGNORECASE,
)


@pytest.mark.parametrize("namespace", ["tools", "icdev.tools"])
def test_no_model_id_literals_in_module(namespace):
    module = importlib.import_module(f"{namespace}.cortex.api")
    source = Path(module.__file__).read_text(encoding="utf-8")
    hits = _MODEL_ID_FRAGMENTS.findall(source)
    assert not hits, f"model-ID literals leaked into {module.__file__}: {hits}"


def test_facades_exported_from_both_namespaces():
    from icdev.tools.cortex import classify as c2, complete as c1, extract as c3  # noqa: F401
    from tools.cortex import classify, complete, extract  # noqa: F401
