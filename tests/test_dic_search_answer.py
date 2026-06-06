"""Tests for the grounded LLM answer synthesis in the DIC search engine.

aiify-opp-6046: fulltext_search_engine -> llm_generation. The DIC search engine
returns cited chunks with NO LLM by default (air-gap safe). ``DICSearchEngine.
answer`` is an OPT-IN layer that asks the LLM to compose an answer grounded
STRICTLY in the retrieved excerpts. These tests pin its load-bearing guarantees:

* it refuses (grounded=False) with "no_evidence" when search returns nothing,
  and never calls the LLM in that case;
* it grounds the model on the retrieved excerpts only, sends a bounded context,
  and keeps injection scanning ON (query is user-provided);
* it honors the model's INSUFFICIENT_EVIDENCE sentinel as a refusal;
* it degrades to grounded=False on any LLM failure or blank output — never
  fabricating an answer — while still surfacing the supporting citations.
"""
from __future__ import annotations

import importlib

import pytest

se = importlib.import_module("tools.document_intelligence.search_engine")
router_mod = importlib.import_module("tools.llm.router")


class _Resp:
    def __init__(self, content):
        self.content = content


class _Router:
    """Stand-in LLMRouter that records the request and returns a canned reply."""

    last_request = None
    _content = "The budget grew 12% in Q3 [1], driven by hiring [2]."

    def __init__(self, *a, **k):
        pass

    def invoke(self, function, request):
        _Router.last_request = request
        return _Resp(self._content)


@pytest.fixture(autouse=True)
def _reset_router():
    _Router.last_request = None
    _Router._content = "The budget grew 12% in Q3 [1], driven by hiring [2]."
    yield


def _patch_router(monkeypatch, content=None):
    if content is not None:
        _Router._content = content
    monkeypatch.setattr(router_mod, "LLMRouter", _Router)


def _result(i, content="Some grounded source text."):
    return se.DICSearchResult(
        chunk_id=f"chunk-{i}",
        doc_id=f"doc-{i}",
        doc_title=f"Doc {i}",
        content=content,
        score=1.0 - i * 0.1,
        citation=se.Citation(doc_id=f"doc-{i}", doc_title=f"Doc {i}", chunk_id=f"chunk-{i}"),
    )


def _patch_search(monkeypatch, results):
    monkeypatch.setattr(
        se.DICSearchEngine, "search", lambda self, *a, **k: list(results)
    )


# --------------------------------------------------------------------------- #
# No-evidence refusal
# --------------------------------------------------------------------------- #

def test_no_results_refuses_without_calling_llm(monkeypatch):
    _patch_search(monkeypatch, [])
    _patch_router(monkeypatch)
    ans = se.DICSearchEngine().answer("anything")
    assert ans.grounded is False
    assert ans.refusal_reason == "no_evidence"
    assert ans.answer == ""
    assert ans.result_count == 0
    # LLM must not be invoked when there is no evidence.
    assert _Router.last_request is None


# --------------------------------------------------------------------------- #
# Happy path: grounded answer
# --------------------------------------------------------------------------- #

def test_grounded_answer_returned(monkeypatch):
    _patch_search(monkeypatch, [_result(1), _result(2)])
    _patch_router(monkeypatch)
    ans = se.DICSearchEngine().answer("how did the budget change?")
    assert ans.grounded is True
    assert "budget grew" in ans.answer
    assert ans.result_count == 2
    assert len(ans.citations) == 2
    assert ans.origin == "ai_generated"


def test_to_dict_shape(monkeypatch):
    _patch_search(monkeypatch, [_result(1)])
    _patch_router(monkeypatch)
    d = se.DICSearchEngine().answer("q").to_dict()
    assert set(d) == {
        "answer",
        "grounded",
        "citations",
        "result_count",
        "refusal_reason",
        "origin",
        "grounding_quality",
        "weak_grounding",
    }
    assert isinstance(d["citations"], list)
    assert d["citations"][0]["doc_id"] == "doc-1"


# --------------------------------------------------------------------------- #
# Grounding guarantees on the request
# --------------------------------------------------------------------------- #

def test_context_contains_excerpts_and_markers(monkeypatch):
    _patch_search(monkeypatch, [_result(1, "alpha fact"), _result(2, "beta fact")])
    _patch_router(monkeypatch)
    se.DICSearchEngine().answer("q")
    content = _Router.last_request.messages[0]["content"]
    assert "[1]" in content and "[2]" in content
    assert "alpha fact" in content and "beta fact" in content
    assert "q" in content


def test_query_is_not_injection_skipped(monkeypatch):
    # The query is user-provided; injection scanning must stay enabled.
    _patch_search(monkeypatch, [_result(1)])
    _patch_router(monkeypatch)
    se.DICSearchEngine().answer("q")
    req = _Router.last_request
    assert getattr(req, "skip_injection_scan", False) is False


def test_per_result_excerpt_is_bounded(monkeypatch):
    long_text = "X" * 5000
    _patch_search(monkeypatch, [_result(1, long_text)])
    _patch_router(monkeypatch)
    se.DICSearchEngine().answer("q")
    content = _Router.last_request.messages[0]["content"]
    # Excerpt truncated to the per-result budget, not the full 5000 chars.
    assert content.count("X") <= se._ANSWER_CHARS_PER_RESULT


def test_results_capped_at_max(monkeypatch):
    many = [_result(i) for i in range(1, 20)]
    _patch_search(monkeypatch, many)
    _patch_router(monkeypatch)
    ans = se.DICSearchEngine().answer("q")
    # Only the top _ANSWER_MAX_RESULTS feed synthesis / citations...
    assert len(ans.citations) == se._ANSWER_MAX_RESULTS
    # ...but result_count reflects everything that matched.
    assert ans.result_count == 19
    content = _Router.last_request.messages[0]["content"]
    assert f"[{se._ANSWER_MAX_RESULTS}]" in content
    assert f"[{se._ANSWER_MAX_RESULTS + 1}]" not in content


# --------------------------------------------------------------------------- #
# Model-driven refusal
# --------------------------------------------------------------------------- #

def test_insufficient_evidence_sentinel_is_refusal(monkeypatch):
    _patch_search(monkeypatch, [_result(1)])
    _patch_router(monkeypatch, content="INSUFFICIENT_EVIDENCE")
    ans = se.DICSearchEngine().answer("q")
    assert ans.grounded is False
    assert ans.refusal_reason == "insufficient_evidence"
    assert ans.answer == ""
    # Citations still surfaced so the caller can show what was searched.
    assert len(ans.citations) == 1


# --------------------------------------------------------------------------- #
# Graceful degradation
# --------------------------------------------------------------------------- #

def test_llm_exception_degrades_gracefully(monkeypatch):
    _patch_search(monkeypatch, [_result(1)])

    class _Boom:
        def __init__(self, *a, **k):
            pass

        def invoke(self, *a, **k):
            raise RuntimeError("provider down")

    monkeypatch.setattr(router_mod, "LLMRouter", _Boom)
    ans = se.DICSearchEngine().answer("q")
    assert ans.grounded is False
    assert ans.refusal_reason == "llm_unavailable"
    assert ans.answer == ""
    assert ans.result_count == 1


def test_blank_llm_output_degrades(monkeypatch):
    _patch_search(monkeypatch, [_result(1)])
    _patch_router(monkeypatch, content="   ")
    ans = se.DICSearchEngine().answer("q")
    assert ans.grounded is False
    assert ans.refusal_reason == "llm_unavailable"


def test_none_llm_response_degrades(monkeypatch):
    _patch_search(monkeypatch, [_result(1)])

    class _NoneRouter:
        def __init__(self, *a, **k):
            pass

        def invoke(self, *a, **k):
            return None

    monkeypatch.setattr(router_mod, "LLMRouter", _NoneRouter)
    ans = se.DICSearchEngine().answer("q")
    assert ans.grounded is False
    assert ans.refusal_reason == "llm_unavailable"


# --------------------------------------------------------------------------- #
# Grounding-quality anomaly assessment (aiify-opp-6123:
# hardcoded_threshold -> anomaly_detection). The chat path no longer trusts the
# top-k blindly — it scores the actual excerpts with the deterministic
# relevance-anomaly detector and surfaces a weak-grounding flag.
# --------------------------------------------------------------------------- #

def _weak_result(i):
    # Best hit is itself weak (score below _RELEVANCE_WEAK = 0.30).
    return se.DICSearchResult(
        chunk_id=f"chunk-{i}",
        doc_id=f"doc-{i}",
        doc_title=f"Doc {i}",
        content="Marginally related text.",
        score=0.10 - i * 0.01,
        citation=se.Citation(doc_id=f"doc-{i}", doc_title=f"Doc {i}", chunk_id=f"chunk-{i}"),
    )


def test_strong_grounding_not_flagged(monkeypatch):
    # _result(1)=0.9, _result(2)=0.8 — a strong best hit, no relevance cliff.
    _patch_search(monkeypatch, [_result(1), _result(2)])
    _patch_router(monkeypatch)
    ans = se.DICSearchEngine().answer("q")
    assert ans.weak_grounding is False
    assert ans.grounding_quality["severity"] == "low"
    assert ans.grounding_quality["low_confidence"] is False
    assert ans.grounding_quality["top_score"] >= se._RELEVANCE_STRONG


def test_weak_grounding_flagged(monkeypatch):
    # All hits below the weak band -> low_confidence -> weak_grounding True.
    _patch_search(monkeypatch, [_weak_result(i) for i in range(5)])
    _patch_router(monkeypatch)
    ans = se.DICSearchEngine().answer("q")
    assert ans.weak_grounding is True
    assert ans.grounding_quality["low_confidence"] is True
    assert ans.grounding_quality["top_score"] < se._RELEVANCE_WEAK
    # Grounding is assessed on the same evidence that is cited / synthesized.
    assert ans.grounded is True  # the LLM still answered; the flag is advisory


def test_grounding_assessed_on_refusal_with_evidence(monkeypatch):
    # Even when the model declines, the grounding signal is still reported.
    _patch_search(monkeypatch, [_weak_result(i) for i in range(5)])
    _patch_router(monkeypatch, content="INSUFFICIENT_EVIDENCE")
    ans = se.DICSearchEngine().answer("q")
    assert ans.refusal_reason == "insufficient_evidence"
    assert ans.weak_grounding is True


def test_no_evidence_has_empty_grounding(monkeypatch):
    _patch_search(monkeypatch, [])
    _patch_router(monkeypatch)
    ans = se.DICSearchEngine().answer("q")
    assert ans.refusal_reason == "no_evidence"
    assert ans.grounding_quality == {}
    assert ans.weak_grounding is False
