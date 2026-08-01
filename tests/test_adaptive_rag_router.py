# CUI // SP-CTI
"""Tests for adaptive RAG complexity pre-routing (agx-rag-01).

The load-bearing safety property is that the 'none'/skip route is UNAVAILABLE
on citation surfaces — asserted directly, since a wrongly-skipped retrieval
would produce an uncited claim and violate the TRUST invariant.
"""
from __future__ import annotations

import json

from tools.rag.adaptive_router import (
    COMPLEXITY_VALUES,
    AdaptiveRetriever,
    classify_complexity,
    decide_route,
    measure_savings,
)


class _FakeResp:
    def __init__(self, content):
        self.content = content
        self.model_id = "fake"


class _FakeRouter:
    """Returns a fixed complexity label as JSON."""
    def __init__(self, label):
        self.label = label
        self.calls = 0

    def invoke(self, fn, req, **kw):
        self.calls += 1
        return _FakeResp(json.dumps({"complexity": self.label}))


class _FakeRetriever:
    def __init__(self):
        self.searches = 0

    def search(self, query, **kwargs):
        self.searches += 1
        return [{"query": query, "kwargs": kwargs}]


# ── decide_route truth table (pure composition) ─────────────────────────────

def test_decide_route_truth_table():
    assert decide_route("none") == "skip"
    assert decide_route("simple") == "single_pass"
    assert decide_route("complex") == "decompose"
    # unknown fails safe to retrieval, never skip
    assert decide_route("garbage") == "single_pass"
    assert decide_route("") == "single_pass"


def test_decide_route_never_skips_under_citations():
    for c in COMPLEXITY_VALUES + ("garbage",):
        assert decide_route(c, requires_citations=True) != "skip"


# ── classify citation-safety clamp ──────────────────────────────────────────

def test_none_clamped_to_simple_under_citations():
    router = _FakeRouter("none")
    out = classify_complexity("hi", router=router, requires_citations=True)
    assert out["complexity"] == "simple"
    assert "citation_clamp" in out["source"]


def test_none_allowed_without_citations():
    router = _FakeRouter("none")
    out = classify_complexity("hello there", router=router, requires_citations=False)
    assert out["complexity"] == "none"


def test_malformed_llm_output_falls_back_to_heuristic():
    class _BadRouter:
        def invoke(self, fn, req, **kw):
            return _FakeResp("not json")
    out = classify_complexity(
        "Compare AC-2 and AC-3 controls and explain the difference",
        router=_BadRouter(),
    )
    assert out["complexity"] in COMPLEXITY_VALUES
    assert out["source"] == "heuristic"


def test_heuristic_flags_complex_query():
    class _BadRouter:
        def invoke(self, fn, req, **kw):
            raise RuntimeError("offline")
    out = classify_complexity("Why does X compare to Y across Z?", router=_BadRouter())
    assert out["complexity"] == "complex"


# ── AdaptiveRetriever behavior ──────────────────────────────────────────────

def _cfg(enabled):
    return {"rag": {"adaptive_routing": {"enabled": enabled, "use_llm_classifier": True}}}


def test_disabled_is_current_behavior_single_pass():
    ret = _FakeRetriever()
    ar = AdaptiveRetriever(retriever=ret, config=_cfg(False))
    out = ar.retrieve("anything")
    assert out["route"] == "single_pass" and out["retrieved"] is True
    assert ret.searches == 1


def test_enabled_none_skips_retrieval():
    ret = _FakeRetriever()
    ar = AdaptiveRetriever(retriever=ret, router=_FakeRouter("none"), config=_cfg(True))
    out = ar.retrieve("hello there", requires_citations=False)
    assert out["route"] == "skip" and out["retrieved"] is False
    assert ret.searches == 0  # the whole point: a real call was avoided


def test_enabled_none_under_citations_still_retrieves():
    ret = _FakeRetriever()
    ar = AdaptiveRetriever(retriever=ret, router=_FakeRouter("none"), config=_cfg(True))
    out = ar.retrieve("what is AC-2", requires_citations=True)
    assert out["route"] == "single_pass" and out["retrieved"] is True
    assert ret.searches == 1


def test_enabled_complex_uses_decompose_fn_when_injected():
    ret = _FakeRetriever()
    calls = {"n": 0}

    def decompose(query, **kwargs):
        calls["n"] += 1
        return ["a", "b"]

    ar = AdaptiveRetriever(retriever=ret, router=_FakeRouter("complex"),
                           config=_cfg(True), decompose_fn=decompose)
    out = ar.retrieve("compare a and b")
    assert out["route"] == "decompose" and calls["n"] == 1
    assert ret.searches == 0


def test_enabled_complex_falls_back_to_widened_single_pass():
    ret = _FakeRetriever()
    cfg = {"rag": {"adaptive_routing": {"enabled": True, "complex_top_k": 12}}}
    ar = AdaptiveRetriever(retriever=ret, router=_FakeRouter("complex"), config=cfg)
    out = ar.retrieve("compare a and b")
    assert out["route"] == "decompose" and ret.searches == 1


# ── measurement harness ─────────────────────────────────────────────────────

def test_measure_savings_on_fixture():
    fixture = [
        {"query": "hi", "gold_route": "skip", "requires_citations": False},
        {"query": "what is AC-2", "gold_route": "single_pass", "requires_citations": True},
        {"query": "compare AC-2 versus AC-3 across baselines", "gold_route": "decompose",
         "requires_citations": False},
    ]
    # Heuristic-only (no live LLM) so the numbers are deterministic & reproducible.
    cfg = {"use_llm_classifier": False}
    m = measure_savings(fixture, config=cfg)
    assert m["n"] == 3
    assert 0.0 <= m["skip_rate"] <= 1.0
    assert 0.0 <= m["routing_accuracy"] <= 1.0
    assert isinstance(m["confusion"], dict)
