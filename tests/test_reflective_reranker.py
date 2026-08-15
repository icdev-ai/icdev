# CUI // SP-CTI
"""Tests for Self-RAG per-document reflective reranking (agx-rag-02)."""
from __future__ import annotations

import itertools
import json

from tools.rag.reflective_reranker import (
    AXIS_VALUES,
    ab_compare,
    compose_reflection_score,
    map_axis,
    reflect_document,
    reflective_rerank,
)


class _Doc:
    def __init__(self, doc_id, content):
        self.id = doc_id
        self.content = content


class _FakeResp:
    def __init__(self, content):
        self.content = content
        self.model_id = "fake"


class _AxisRouter:
    """Returns fixed per-axis verdicts keyed by a substring of the document."""
    def __init__(self, mapping):
        self.mapping = mapping  # content-substring -> {axis: verdict}
        self.calls = 0

    def invoke(self, fn, req, **kw):
        self.calls += 1
        prompt = req.messages[0]["content"]
        verdicts = {"relevant": "no", "useful": "no"}
        for key, v in self.mapping.items():
            if key in prompt:
                verdicts = v
                break
        return _FakeResp(json.dumps(verdicts))


# ── composition truth table ─────────────────────────────────────────────────

def test_compose_no_claim_truth_table():
    for rel, use in itertools.product(AXIS_VALUES, AXIS_VALUES):
        got = compose_reflection_score(rel, use)
        expected = round(0.7 * map_axis(rel) + 0.3 * map_axis(use), 4)
        assert got == expected, (rel, use)


def test_compose_with_claim_truth_table():
    for rel, sup, use in itertools.product(AXIS_VALUES, AXIS_VALUES, AXIS_VALUES):
        got = compose_reflection_score(rel, use, supports=sup)
        expected = round(0.4 * map_axis(rel) + 0.4 * map_axis(sup) + 0.2 * map_axis(use), 4)
        assert got == expected, (rel, sup, use)


def test_compose_bounds():
    assert compose_reflection_score("yes", "yes", supports="yes") == 1.0
    assert compose_reflection_score("no", "no", supports="no") == 0.0


def test_map_axis_unknown_is_neutral():
    assert map_axis("banana") == 0.5
    assert map_axis("") == 0.5


# ── reflect_document ────────────────────────────────────────────────────────

def test_reflect_document_parses_axes():
    router = _AxisRouter({"ALPHA": {"relevant": "yes", "useful": "yes"}})
    out = reflect_document("q", "ALPHA doc", router=router)
    assert out["relevant"] == "yes" and out["useful"] == "yes"
    assert out["score"] == compose_reflection_score("yes", "yes")
    assert "supports" not in out  # no claim -> axis omitted


def test_reflect_document_with_claim_includes_supports():
    router = _AxisRouter({"BETA": {"relevant": "yes", "useful": "partial", "supports": "no"}})
    out = reflect_document("q", "BETA doc", claim="the sky is green", router=router)
    assert out["supports"] == "no"
    assert out["score"] == compose_reflection_score("yes", "partial", supports="no")


def test_reflect_document_malformed_falls_back_neutral():
    class _BadRouter:
        def invoke(self, fn, req, **kw):
            return _FakeResp("garbage")
    out = reflect_document("q", "doc", router=_BadRouter())
    assert out["relevant"] == "partial" and out["useful"] == "partial"
    assert out["score"] == 0.5


# ── reflective_rerank ordering ──────────────────────────────────────────────

def test_reflective_rerank_orders_by_composed_score():
    docs = [_Doc("d1", "ALPHA"), _Doc("d2", "BETA"), _Doc("d3", "GAMMA")]
    router = _AxisRouter({
        "GAMMA": {"relevant": "yes", "useful": "yes"},
        "ALPHA": {"relevant": "partial", "useful": "partial"},
        "BETA": {"relevant": "no", "useful": "no"},
    })
    ranked = reflective_rerank("q", docs, router=router, top_k=3)
    assert [d.id for d in ranked] == ["d3", "d1", "d2"]
    # supports signal attached for the citation layer
    assert hasattr(ranked[0], "reflection")


def test_reflective_rerank_bounds_candidates():
    docs = [_Doc(f"d{i}", f"DOC{i}") for i in range(5)]
    router = _AxisRouter({})  # everything -> no
    reflective_rerank("q", docs, router=router, top_k=5, max_candidates=2)
    # only the first 2 candidates incur an LLM call
    assert router.calls == 2


def test_reflective_rerank_empty():
    assert reflective_rerank("q", [], router=_AxisRouter({})) == []


# ── A/B harness ─────────────────────────────────────────────────────────────

def test_ab_compare_reports_quality_and_cost():
    docs = [_Doc("d1", "ALPHA"), _Doc("d2", "BETA")]

    def baseline(q, cands):
        return cands  # identity order

    def reflective(q, cands):
        return list(reversed(cands))

    fixture = [{
        "query": "q", "candidates": docs, "gold_relevant": ["d2"],
        "baseline_calls": 0, "reflective_calls": 2,
    }]
    out = ab_compare(fixture, baseline_rank=baseline, reflective_rank=reflective, k=1)
    assert out["n"] == 1
    assert out["baseline_precision_at_k"] == 0.0   # d1 first, not relevant
    assert out["reflective_precision_at_k"] == 1.0  # d2 first, relevant
    assert out["quality_delta"] == 1.0
    assert out["reflective_llm_calls"] == 2
    assert out["recommendation"] == "enable"


def test_ab_compare_negative_result_is_valid():
    docs = [_Doc("d1", "ALPHA"), _Doc("d2", "BETA")]
    fixture = [{"query": "q", "candidates": docs, "gold_relevant": ["d1"],
                "baseline_calls": 0, "reflective_calls": 2}]
    out = ab_compare(fixture, baseline_rank=lambda q, c: c,
                     reflective_rank=lambda q, c: list(reversed(c)), k=1)
    assert out["quality_delta"] < 0
    assert out["recommendation"] == "leave_disabled_negative_result"


# ── degraded is not neutral (trust-self-02) ─────────────────────────────────


class _DeadRouter:
    """Every call raises — the shape of an unreachable or budget-blocked model."""

    def __init__(self, exc=RuntimeError("provider down")):
        self.exc = exc
        self.calls = 0

    def invoke(self, fn, req, **kw):
        self.calls += 1
        raise self.exc


def test_degraded_marks_the_verdict_it_could_not_reach():
    out = reflect_document("q", "doc", router=_DeadRouter())
    assert out["degraded"] is True
    assert out["relevant"] == "partial" and out["useful"] == "partial"
    assert "provider down" in out["reason"]


def test_a_genuine_partial_is_not_degraded():
    """The distinction the flag exists for: same score, different fact.

    A model that answers "partial" on every axis and a model that never
    answered produce the identical 0.5 — and only one of them measured
    anything.
    """
    router = _AxisRouter({"ALPHA": {"relevant": "partial", "useful": "partial"}})
    out = reflect_document("q", "ALPHA doc", router=router)
    assert out["degraded"] is False
    assert out["score"] == 0.5
    assert "reason" not in out


def test_unparseable_response_is_degraded_not_silently_neutral():
    class _Garbage:
        def invoke(self, fn, req, **kw):
            return _FakeResp("I'm sorry, I can't answer that.")

    out = reflect_document("q", "doc", router=_Garbage())
    assert out["degraded"] is True


def test_rerank_gives_up_after_two_consecutive_failures():
    """An unreachable model does not become reachable on document three."""
    docs = [_Doc(f"d{i}", f"doc {i}") for i in range(6)]
    router = _DeadRouter()
    out = reflective_rerank("q", docs, router=router, top_k=6)
    assert router.calls == 2, "bail-out did not bound the spend"
    # ...and the incoming order is handed back untouched, not an ordering
    # derived from six identical fallbacks.
    assert [d.id for d in out] == [d.id for d in docs]


def test_rerank_reports_what_actually_happened():
    docs = [_Doc("d1", "a"), _Doc("d2", "b"), _Doc("d3", "c")]
    report: dict = {}
    reflective_rerank("q", docs, router=_DeadRouter(), top_k=3, report=report)
    assert report["effective"] is False
    assert report["degraded"] == 2 and report["reflected"] == 2
    assert report["bounded_out"] == 1          # skipped by the bail-out
    assert "provider down" in report["reason"]


def test_a_working_model_reports_effective():
    docs = [_Doc("d1", "ALPHA"), _Doc("d2", "BETA")]
    router = _AxisRouter({
        "ALPHA": {"relevant": "no", "useful": "no"},
        "BETA": {"relevant": "yes", "useful": "yes"},
    })
    report: dict = {}
    out = reflective_rerank("q", docs, router=router, top_k=2, report=report)
    assert report["effective"] is True and report["degraded"] == 0
    assert [d.id for d in out] == ["d2", "d1"]   # reordering actually happened


def test_ab_compare_refuses_to_call_an_unreached_model_a_negative_result():
    """A 0.0 delta from a model that never answered is not evidence of no benefit.

    This is the whole reason the flag exists: without it, the run that produced
    this card's own measurement would have been recorded as
    ``leave_disabled_negative_result``.
    """
    docs = [_Doc("d1", "ALPHA"), _Doc("d2", "BETA")]
    router = _DeadRouter()
    fixture = [{"query": "q", "candidates": docs, "gold_relevant": ["d2"],
                "baseline_calls": 0, "reflective_calls": 2}]
    out = ab_compare(
        fixture,
        baseline_rank=lambda q, c: list(c),
        reflective_rank=lambda q, c: reflective_rerank(q, c, router=router, top_k=2),
        k=2,
    )
    assert out["quality_delta"] == 0.0
    assert out["documents_judged"] == 0 and out["documents_degraded"] == 2
    assert out["recommendation"] == "unmeasurable_reflection_degraded"
    assert "provider down" in out["degraded_reason"]


def test_ab_compare_still_grades_a_run_that_was_actually_judged():
    docs = [_Doc("d1", "ALPHA"), _Doc("d2", "BETA")]
    router = _AxisRouter({
        "ALPHA": {"relevant": "no", "useful": "no"},
        "BETA": {"relevant": "yes", "useful": "yes"},
    })
    fixture = [{"query": "q", "candidates": docs, "gold_relevant": ["d2"],
                "baseline_calls": 0, "reflective_calls": 2}]
    out = ab_compare(
        fixture,
        baseline_rank=lambda q, c: list(c),
        reflective_rank=lambda q, c: reflective_rerank(q, c, router=router, top_k=1),
        k=1,
    )
    assert out["documents_judged"] == 1 and out["documents_degraded"] == 0
    assert out["recommendation"] == "enable"
