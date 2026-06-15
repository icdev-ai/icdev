"""Tests for LLM-powered query intent classification in the DIC search engine.

aiify-opp-28: fulltext_search_engine -> llm_generation, analog of paperless
src/documents/filters.py DocumentSearchFilter. ``DICSearchEngine.
classify_query_intent`` is an OPT-IN layer that asks the LLM to assess a
search query's intent and recommend the optimal DIC retrieval strategy as a
structured decision object. These tests pin its load-bearing guarantees:

* it returns all intent fields (type, mode, boolean flags, confidence);
* it degrades gracefully on empty query, LLM failure, blank/None output, and
  unparseable JSON — all flags False, llm_used False, refusal_reason set;
* the LLM is fed only a user-turn message (injection scanning stays on);
* unknown intent_type / recommended_mode values fall back to safe defaults;
* confidence is clamped to [0.0, 1.0];
* to_dict() shape is stable.
"""
from __future__ import annotations

import importlib
import json

import pytest

se = importlib.import_module("tools.document_intelligence.search_engine")
router_mod = importlib.import_module("tools.llm.router")

_VALID_RESPONSE = json.dumps({
    "intent_type": "factual_qa",
    "recommended_mode": "grounded",
    "should_expand": False,
    "should_filter": False,
    "should_synthesize": True,
    "confidence": 0.92,
})

_EXPLORATION_RESPONSE = json.dumps({
    "intent_type": "broad_exploration",
    "recommended_mode": "hybrid",
    "should_expand": True,
    "should_filter": False,
    "should_synthesize": False,
    "confidence": 0.75,
})


class _Resp:
    def __init__(self, content):
        self.content = content


class _Router:
    """Stand-in LLMRouter that records the request and returns canned JSON."""

    last_request = None
    _content = _VALID_RESPONSE

    def __init__(self, *a, **k):
        pass

    def invoke(self, function, request):
        _Router.last_request = request
        return _Resp(self._content)


@pytest.fixture(autouse=True)
def _reset_router():
    _Router.last_request = None
    _Router._content = _VALID_RESPONSE
    yield


def _patch(monkeypatch, content=None):
    if content is not None:
        _Router._content = content
    monkeypatch.setattr(router_mod, "LLMRouter", _Router)


# --------------------------------------------------------------------------- #
# Empty query — refuse without calling the LLM
# --------------------------------------------------------------------------- #

def test_empty_query_refuses_without_llm(monkeypatch):
    _patch(monkeypatch)
    intent = se.DICSearchEngine().classify_query_intent("   ")
    assert intent.llm_used is False
    assert intent.refusal_reason == "empty_query"
    assert intent.intent_type == ""
    assert intent.should_expand is False
    assert intent.should_synthesize is False
    assert _Router.last_request is None


# --------------------------------------------------------------------------- #
# Happy path — factual QA
# --------------------------------------------------------------------------- #

def test_factual_qa_intent_parsed(monkeypatch):
    _patch(monkeypatch)
    intent = se.DICSearchEngine().classify_query_intent("What is the TTX for project Alpha?")
    assert intent.llm_used is True
    assert intent.refusal_reason == ""
    assert intent.intent_type == "factual_qa"
    assert intent.recommended_mode == "grounded"
    assert intent.should_synthesize is True
    assert intent.should_expand is False
    assert 0.0 <= intent.confidence <= 1.0
    assert intent.origin == "ai_generated"


# --------------------------------------------------------------------------- #
# Happy path — broad exploration recommends hybrid mode
# --------------------------------------------------------------------------- #

def test_broad_exploration_uses_hybrid_mode(monkeypatch):
    _patch(monkeypatch, content=_EXPLORATION_RESPONSE)
    intent = se.DICSearchEngine().classify_query_intent("Tell me everything about supply chain")
    assert intent.intent_type == "broad_exploration"
    assert intent.recommended_mode == "hybrid"
    assert intent.should_expand is True
    assert intent.should_synthesize is False


# --------------------------------------------------------------------------- #
# to_dict shape is stable
# --------------------------------------------------------------------------- #

def test_to_dict_shape(monkeypatch):
    _patch(monkeypatch)
    d = se.DICSearchEngine().classify_query_intent("budget report 2025").to_dict()
    assert set(d) == {
        "query",
        "intent_type",
        "recommended_mode",
        "should_expand",
        "should_filter",
        "should_synthesize",
        "confidence",
        "llm_used",
        "refusal_reason",
        "origin",
    }
    assert isinstance(d["should_expand"], bool)
    assert isinstance(d["confidence"], float)


# --------------------------------------------------------------------------- #
# Unknown intent_type falls back to default
# --------------------------------------------------------------------------- #

def test_unknown_intent_type_falls_back_to_default(monkeypatch):
    bad = json.dumps({
        "intent_type": "magic_search",
        "recommended_mode": "grounded",
        "should_expand": False,
        "should_filter": False,
        "should_synthesize": False,
        "confidence": 0.5,
    })
    _patch(monkeypatch, content=bad)
    intent = se.DICSearchEngine().classify_query_intent("find budget docs")
    assert intent.intent_type == se._INTENT_DEFAULT_TYPE
    assert intent.llm_used is True


# --------------------------------------------------------------------------- #
# Unknown recommended_mode falls back to "grounded"
# --------------------------------------------------------------------------- #

def test_unknown_mode_falls_back_to_grounded(monkeypatch):
    bad = json.dumps({
        "intent_type": "document_search",
        "recommended_mode": "turbo_mode",
        "should_expand": False,
        "should_filter": False,
        "should_synthesize": False,
        "confidence": 0.5,
    })
    _patch(monkeypatch, content=bad)
    intent = se.DICSearchEngine().classify_query_intent("find contract files")
    assert intent.recommended_mode == "grounded"


# --------------------------------------------------------------------------- #
# Confidence is clamped to [0.0, 1.0]
# --------------------------------------------------------------------------- #

def test_confidence_clamped_above(monkeypatch):
    over = json.dumps({
        "intent_type": "document_search",
        "recommended_mode": "grounded",
        "should_expand": False,
        "should_filter": False,
        "should_synthesize": False,
        "confidence": 9.9,
    })
    _patch(monkeypatch, content=over)
    intent = se.DICSearchEngine().classify_query_intent("q")
    assert intent.confidence == 1.0


def test_confidence_clamped_below(monkeypatch):
    under = json.dumps({
        "intent_type": "document_search",
        "recommended_mode": "grounded",
        "should_expand": False,
        "should_filter": False,
        "should_synthesize": False,
        "confidence": -0.5,
    })
    _patch(monkeypatch, content=under)
    intent = se.DICSearchEngine().classify_query_intent("q")
    assert intent.confidence == 0.0


# --------------------------------------------------------------------------- #
# JSON wrapped in code fences is stripped
# --------------------------------------------------------------------------- #

def test_code_fence_stripped(monkeypatch):
    fenced = f"```json\n{_VALID_RESPONSE}\n```"
    _patch(monkeypatch, content=fenced)
    intent = se.DICSearchEngine().classify_query_intent("what is TTX?")
    assert intent.llm_used is True
    assert intent.intent_type == "factual_qa"


# --------------------------------------------------------------------------- #
# Injection scanning stays on (query is in user turn)
# --------------------------------------------------------------------------- #

def test_query_in_user_turn_not_system(monkeypatch):
    _patch(monkeypatch)
    se.DICSearchEngine().classify_query_intent("budget 2025")
    req = _Router.last_request
    assert getattr(req, "skip_injection_scan", False) is False
    assert any("budget 2025" in m.get("content", "") for m in req.messages)


# --------------------------------------------------------------------------- #
# Degradation — LLM exception
# --------------------------------------------------------------------------- #

def test_llm_exception_degrades(monkeypatch):
    class _Boom:
        def __init__(self, *a, **k):
            pass

        def invoke(self, *a, **k):
            raise RuntimeError("provider down")

    monkeypatch.setattr(router_mod, "LLMRouter", _Boom)
    intent = se.DICSearchEngine().classify_query_intent("find docs")
    assert intent.llm_used is False
    assert intent.refusal_reason == "llm_unavailable"
    assert intent.should_expand is False


# --------------------------------------------------------------------------- #
# Degradation — blank / None response
# --------------------------------------------------------------------------- #

def test_blank_output_degrades(monkeypatch):
    _patch(monkeypatch, content="   ")
    intent = se.DICSearchEngine().classify_query_intent("find docs")
    assert intent.llm_used is False
    assert intent.refusal_reason == "llm_unavailable"


def test_none_response_degrades(monkeypatch):
    class _NoneRouter:
        def __init__(self, *a, **k):
            pass

        def invoke(self, *a, **k):
            return None

    monkeypatch.setattr(router_mod, "LLMRouter", _NoneRouter)
    intent = se.DICSearchEngine().classify_query_intent("find docs")
    assert intent.llm_used is False
    assert intent.refusal_reason == "llm_unavailable"


# --------------------------------------------------------------------------- #
# Degradation — unparseable JSON
# --------------------------------------------------------------------------- #

def test_unparseable_json_degrades(monkeypatch):
    _patch(monkeypatch, content="not json at all !@#$")
    intent = se.DICSearchEngine().classify_query_intent("find docs")
    assert intent.llm_used is True
    assert intent.refusal_reason == "llm_unavailable"
    assert intent.intent_type == ""


def test_empty_json_object_degrades(monkeypatch):
    _patch(monkeypatch, content="{}")
    intent = se.DICSearchEngine().classify_query_intent("find docs")
    assert intent.llm_used is True
    assert intent.refusal_reason == "llm_unavailable"


# --------------------------------------------------------------------------- #
# String boolean values are accepted
# --------------------------------------------------------------------------- #

def test_string_booleans_parsed(monkeypatch):
    resp = json.dumps({
        "intent_type": "filtered_search",
        "recommended_mode": "grounded",
        "should_expand": "true",
        "should_filter": "true",
        "should_synthesize": "false",
        "confidence": 0.8,
    })
    _patch(monkeypatch, content=resp)
    intent = se.DICSearchEngine().classify_query_intent("classified docs from 2024")
    assert intent.should_expand is True
    assert intent.should_filter is True
    assert intent.should_synthesize is False
