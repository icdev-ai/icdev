"""Tests for LLM-powered query intent classification in the DIC search engine.

aiify-opp-28: fulltext_search_engine -> llm_generation, analog of the
paperless src/documents/filters.py DocumentSearchFilter (combined fulltext +
metadata search). ``DICSearchEngine.classify_query_intent`` classifies a
search query's high-level intent and recommends the optimal DIC retrieval
strategy (mode, expansion, filtering, synthesis) as a structured
``DICQueryIntent`` decision object.

Guarantees tested:
* empty query refuses without calling the LLM;
* valid LLM output is parsed and validated: intent_type constrained to the
  four allowed values, recommended_mode to two values;
* boolean flags are coerced correctly from true/false/"true"/"false" strings;
* unknown / invented intent_type values fall back to "document_search";
* unknown mode falls back to "grounded";
* confidence is clamped to [0.0, 1.0];
* code fences in the response are stripped before JSON parsing;
* all degradation paths (blank response, None response, LLM exception, invalid
  JSON) return a usable DICQueryIntent with llm_used=False;
* ``to_dict()`` returns the expected key set and types.
"""
from __future__ import annotations

import importlib
import json

import pytest

se = importlib.import_module("tools.document_intelligence.search_engine")
router_mod = importlib.import_module("tools.llm.router")


class _Resp:
    def __init__(self, content):
        self.content = content


class _Router:
    """Stand-in LLMRouter that returns a canned intent JSON object."""

    last_request = None
    _content = json.dumps({
        "intent_type": "factual_qa",
        "recommended_mode": "grounded",
        "should_expand": False,
        "should_filter": False,
        "should_synthesize": True,
        "confidence": 0.9,
    })

    def __init__(self, *a, **k):
        pass

    def invoke(self, function, request):
        _Router.last_request = request
        return _Resp(self._content)


@pytest.fixture(autouse=True)
def _reset_router():
    _Router.last_request = None
    _Router._content = json.dumps({
        "intent_type": "factual_qa",
        "recommended_mode": "grounded",
        "should_expand": False,
        "should_filter": False,
        "should_synthesize": True,
        "confidence": 0.9,
    })
    yield


def _patch_router(monkeypatch, content=None):
    import sys as _sys
    if content is not None:
        _Router._content = content
    monkeypatch.setattr(router_mod, "LLMRouter", _Router)
    import icdev.tools.llm.router as _icdev_router_mod
    monkeypatch.setattr(_icdev_router_mod, "LLMRouter", _Router)
    for _key, _mod in list(_sys.modules.items()):
        if "llm.router" in _key and hasattr(_mod, "LLMRouter"):
            monkeypatch.setattr(_mod, "LLMRouter", _Router)


# --------------------------------------------------------------------------- #
# Empty query — refuse without calling the LLM
# --------------------------------------------------------------------------- #

def test_empty_query_refuses_without_llm(monkeypatch):
    _patch_router(monkeypatch)
    intent = se.DICSearchEngine().classify_query_intent("   ")
    assert intent.llm_used is False
    assert intent.refusal_reason == "empty_query"
    assert intent.intent_type == ""
    assert _Router.last_request is None


def test_empty_string_refuses(monkeypatch):
    _patch_router(monkeypatch)
    intent = se.DICSearchEngine().classify_query_intent("")
    assert intent.refusal_reason == "empty_query"
    assert intent.llm_used is False


# --------------------------------------------------------------------------- #
# Happy path: valid JSON with known intent types
# --------------------------------------------------------------------------- #

def test_factual_qa_intent(monkeypatch):
    _patch_router(monkeypatch)
    intent = se.DICSearchEngine().classify_query_intent("What is the TTX for system X?")
    assert intent.llm_used is True
    assert intent.refusal_reason == ""
    assert intent.intent_type == "factual_qa"
    assert intent.recommended_mode == "grounded"
    assert intent.should_synthesize is True
    assert intent.should_expand is False
    assert intent.should_filter is False
    assert intent.confidence == pytest.approx(0.9)
    assert intent.origin == "ai_generated"


def test_filtered_search_intent(monkeypatch):
    payload = json.dumps({
        "intent_type": "filtered_search",
        "recommended_mode": "grounded",
        "should_expand": False,
        "should_filter": True,
        "should_synthesize": False,
        "confidence": 0.85,
    })
    _patch_router(monkeypatch, content=payload)
    intent = se.DICSearchEngine().classify_query_intent("recent CUI PDFs from last 30 days")
    assert intent.intent_type == "filtered_search"
    assert intent.should_filter is True
    assert intent.should_expand is False
    assert intent.should_synthesize is False
    assert intent.confidence == pytest.approx(0.85)


def test_broad_exploration_intent(monkeypatch):
    payload = json.dumps({
        "intent_type": "broad_exploration",
        "recommended_mode": "hybrid",
        "should_expand": True,
        "should_filter": False,
        "should_synthesize": False,
        "confidence": 0.75,
    })
    _patch_router(monkeypatch, content=payload)
    intent = se.DICSearchEngine().classify_query_intent("tell me about zero trust architecture")
    assert intent.intent_type == "broad_exploration"
    assert intent.recommended_mode == "hybrid"
    assert intent.should_expand is True
    assert intent.should_filter is False


def test_document_search_intent(monkeypatch):
    payload = json.dumps({
        "intent_type": "document_search",
        "recommended_mode": "grounded",
        "should_expand": False,
        "should_filter": False,
        "should_synthesize": False,
        "confidence": 0.92,
    })
    _patch_router(monkeypatch, content=payload)
    intent = se.DICSearchEngine().classify_query_intent("show me threat model documents")
    assert intent.intent_type == "document_search"
    assert intent.should_synthesize is False


# --------------------------------------------------------------------------- #
# to_dict() shape
# --------------------------------------------------------------------------- #

def test_to_dict_shape(monkeypatch):
    _patch_router(monkeypatch)
    d = se.DICSearchEngine().classify_query_intent("What is X?").to_dict()
    expected_keys = {
        "query", "intent_type", "recommended_mode", "should_expand",
        "should_filter", "should_synthesize", "confidence", "llm_used",
        "refusal_reason", "origin",
    }
    assert set(d) == expected_keys
    assert isinstance(d["should_expand"], bool)
    assert isinstance(d["should_filter"], bool)
    assert isinstance(d["should_synthesize"], bool)
    assert isinstance(d["llm_used"], bool)
    assert isinstance(d["confidence"], float)


# --------------------------------------------------------------------------- #
# Schema validation: unknown/invented values fall back to defaults
# --------------------------------------------------------------------------- #

def test_unknown_intent_type_falls_back_to_document_search(monkeypatch):
    payload = json.dumps({
        "intent_type": "invented_type",
        "recommended_mode": "grounded",
        "should_expand": False,
        "should_filter": False,
        "should_synthesize": False,
        "confidence": 0.5,
    })
    _patch_router(monkeypatch, content=payload)
    intent = se.DICSearchEngine().classify_query_intent("something unusual")
    assert intent.intent_type == "document_search"
    assert intent.llm_used is True


def test_unknown_mode_falls_back_to_grounded(monkeypatch):
    payload = json.dumps({
        "intent_type": "factual_qa",
        "recommended_mode": "unknown_mode",
        "should_expand": False,
        "should_filter": False,
        "should_synthesize": True,
        "confidence": 0.7,
    })
    _patch_router(monkeypatch, content=payload)
    intent = se.DICSearchEngine().classify_query_intent("What is X?")
    assert intent.recommended_mode == "grounded"


def test_confidence_clamped_above_one(monkeypatch):
    payload = json.dumps({
        "intent_type": "factual_qa",
        "recommended_mode": "grounded",
        "should_expand": False,
        "should_filter": False,
        "should_synthesize": True,
        "confidence": 99.9,
    })
    _patch_router(monkeypatch, content=payload)
    intent = se.DICSearchEngine().classify_query_intent("test query")
    assert intent.confidence <= 1.0


def test_confidence_clamped_below_zero(monkeypatch):
    payload = json.dumps({
        "intent_type": "document_search",
        "recommended_mode": "grounded",
        "should_expand": False,
        "should_filter": False,
        "should_synthesize": False,
        "confidence": -5.0,
    })
    _patch_router(monkeypatch, content=payload)
    intent = se.DICSearchEngine().classify_query_intent("test query")
    assert intent.confidence >= 0.0


# --------------------------------------------------------------------------- #
# Boolean coercion: string "true"/"false" from LLM
# --------------------------------------------------------------------------- #

def test_boolean_string_true_coerced(monkeypatch):
    payload = json.dumps({
        "intent_type": "broad_exploration",
        "recommended_mode": "hybrid",
        "should_expand": "true",
        "should_filter": "false",
        "should_synthesize": "false",
        "confidence": 0.8,
    })
    _patch_router(monkeypatch, content=payload)
    intent = se.DICSearchEngine().classify_query_intent("explore threat intelligence")
    assert intent.should_expand is True
    assert intent.should_filter is False


def test_boolean_integer_one_coerced(monkeypatch):
    payload = json.dumps({
        "intent_type": "filtered_search",
        "recommended_mode": "grounded",
        "should_expand": 0,
        "should_filter": 1,
        "should_synthesize": 0,
        "confidence": 0.8,
    })
    _patch_router(monkeypatch, content=payload)
    intent = se.DICSearchEngine().classify_query_intent("CUI contracts 2024")
    assert intent.should_filter is True
    assert intent.should_expand is False


# --------------------------------------------------------------------------- #
# Code fence stripping
# --------------------------------------------------------------------------- #

def test_code_fence_stripped_before_parse(monkeypatch):
    inner = json.dumps({
        "intent_type": "factual_qa",
        "recommended_mode": "grounded",
        "should_expand": False,
        "should_filter": False,
        "should_synthesize": True,
        "confidence": 0.88,
    })
    payload = f"```json\n{inner}\n```"
    _patch_router(monkeypatch, content=payload)
    intent = se.DICSearchEngine().classify_query_intent("What is the policy on X?")
    assert intent.intent_type == "factual_qa"
    assert intent.llm_used is True


# --------------------------------------------------------------------------- #
# Degradation — never throws, always returns a usable DICQueryIntent
# --------------------------------------------------------------------------- #

def test_blank_response_returns_llm_unavailable(monkeypatch):
    _patch_router(monkeypatch, content="   ")
    intent = se.DICSearchEngine().classify_query_intent("test")
    assert intent.llm_used is False
    assert intent.refusal_reason == "llm_unavailable"
    assert intent.intent_type == ""


def test_none_response_returns_llm_unavailable(monkeypatch):
    class _NoneRouter:
        def __init__(self, *a, **k):
            pass
        def invoke(self, *a, **k):
            return None

    import sys as _sys
    monkeypatch.setattr(router_mod, "LLMRouter", _NoneRouter)
    import icdev.tools.llm.router as _icdev_router_mod
    monkeypatch.setattr(_icdev_router_mod, "LLMRouter", _NoneRouter)
    for _key, _mod in list(_sys.modules.items()):
        if "llm.router" in _key and hasattr(_mod, "LLMRouter"):
            monkeypatch.setattr(_mod, "LLMRouter", _NoneRouter)
    intent = se.DICSearchEngine().classify_query_intent("find documents")
    assert intent.llm_used is False
    assert intent.refusal_reason == "llm_unavailable"


def test_llm_exception_returns_llm_unavailable(monkeypatch):
    class _Boom:
        def __init__(self, *a, **k):
            pass
        def invoke(self, *a, **k):
            raise RuntimeError("provider down")

    import sys as _sys
    monkeypatch.setattr(router_mod, "LLMRouter", _Boom)
    import icdev.tools.llm.router as _icdev_router_mod
    monkeypatch.setattr(_icdev_router_mod, "LLMRouter", _Boom)
    for _key, _mod in list(_sys.modules.items()):
        if "llm.router" in _key and hasattr(_mod, "LLMRouter"):
            monkeypatch.setattr(_mod, "LLMRouter", _Boom)
    intent = se.DICSearchEngine().classify_query_intent("recent threat reports")
    assert intent.llm_used is False
    assert intent.refusal_reason == "llm_unavailable"


def test_invalid_json_returns_llm_unavailable(monkeypatch):
    _patch_router(monkeypatch, content="not json at all")
    intent = se.DICSearchEngine().classify_query_intent("find something")
    assert intent.llm_used is True
    assert intent.refusal_reason == "llm_unavailable"


def test_non_dict_json_returns_llm_unavailable(monkeypatch):
    _patch_router(monkeypatch, content='["factual_qa", "grounded"]')
    intent = se.DICSearchEngine().classify_query_intent("what is policy X")
    assert intent.llm_used is True
    assert intent.refusal_reason == "llm_unavailable"
