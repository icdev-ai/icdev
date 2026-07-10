# CUI // SP-CTI
"""Tests for the CRAG corrective retrieval loop (ctx-search-04).

The evaluator branch is forced from both sides: low-confidence fixtures
(adapter scores below ``search.crag_threshold``) must trigger exactly one
corrective rewrite+re-retrieve round, high-confidence fixtures must never
trigger it. The rewrite path is faked at two layers — the module-level
``rewrite_query`` for loop-behavior tests, and ``tools.llm.get_router``
(shim-aware: importlib + setattr, matching tests/cortex/test_api_core.py)
for the routing-function contract.
"""
from __future__ import annotations

import importlib

from tools.cortex import search_service
from tools.cortex.schemas import Citation, CortexSearchResult
from tools.llm.provider import LLMResponse

# crag_threshold=0.5: hits below trigger the corrective loop, above never do.
TEST_CONFIG = {
    "search": {
        "router": {"factual_confidence": 0.75},
        "crag_threshold": 0.5,
        "timeouts": {"default": 5.0},
        "fan_out": {"backends": ["rag", "graph"], "max_workers": 4},
    }
}

LOW, HIGH = 0.2, 0.9


def _hit(backend, score, source_id="", content=""):
    return CortexSearchResult(
        content=content or f"{backend} hit",
        score=score,
        backend=backend,
        strategy="native",
        citation=Citation(source_id=source_id or f"{backend}-1"),
    )


def _patch_adapters(monkeypatch, score=LOW, calls=None, backends=("rag", "graph")):
    """Fake adapters returning one hit per call; records (backend, query)."""
    for name in backends:
        def fake(query, top_k=5, ctx=None, _name=name):
            if calls is not None:
                calls.append((_name, query))
            return [_hit(_name, score=score, source_id=f"{_name}-{query}")]

        monkeypatch.setitem(search_service.BACKEND_ADAPTERS, name, fake)


def _patch_taxonomy(monkeypatch, label="reasoning", confidence=0.8):
    mod = importlib.import_module("tools.rag.query_classifier")
    monkeypatch.setattr(
        mod,
        "classify_query",
        lambda query, context="": {
            "label": label,
            "confidence": confidence,
            "method": "heuristic",
        },
    )


def _patch_rewrite(monkeypatch, rewritten="entirely different phrasing", error=None):
    calls = []

    def fake(query, results, ctx=None):
        calls.append(query)
        if error is not None:
            raise error
        return rewritten

    monkeypatch.setattr(search_service, "rewrite_query", fake)
    return calls


# ---------------------------------------------------------------------------
# Trigger branch — exactly one corrective round
# ---------------------------------------------------------------------------


def test_low_confidence_triggers_exactly_one_corrective_round(monkeypatch):
    adapter_calls = []
    _patch_adapters(monkeypatch, score=LOW, calls=adapter_calls)
    _patch_taxonomy(monkeypatch)
    rewrite_calls = _patch_rewrite(monkeypatch, rewritten="second wording")

    results = search_service.search("vague original words", config=TEST_CONFIG)

    # One rewrite, each backend queried once per pass — never a third round,
    # even though the corrective hits are also below the threshold.
    assert rewrite_calls == ["vague original words"]
    assert sorted(adapter_calls) == [
        ("graph", "second wording"),
        ("graph", "vague original words"),
        ("rag", "second wording"),
        ("rag", "vague original words"),
    ]
    assert results
    for r in results:
        assert r.metadata["corrective_pass"] is True
        assert "corrective_skipped" not in r.metadata
        crag = r.metadata["crag"]
        assert crag["threshold"] == 0.5
        assert crag["original_top_score"] == LOW
        assert crag["original_query"] == "vague original words"
        assert crag["rewritten_query"] == "second wording"


def test_corrective_merge_dedupes_and_keeps_best_score(monkeypatch):
    passes = []

    def rag(query, top_k=5, ctx=None):
        passes.append(query)
        if len(passes) == 1:
            return [_hit("rag", 0.2, source_id="shared", content="same doc")]
        return [
            _hit("rag", 0.4, source_id="shared", content="same doc"),
            _hit("rag", 0.3, source_id="fresh", content="new doc"),
        ]

    monkeypatch.setitem(search_service.BACKEND_ADAPTERS, "rag", rag)
    _patch_rewrite(monkeypatch)

    results = search_service.search("q", strategy="rag", config=TEST_CONFIG)

    assert [(r.citation.source_id, r.score) for r in results] == [
        ("shared", 0.4),
        ("fresh", 0.3),
    ]


def test_empty_first_pass_returns_corrective_results(monkeypatch):
    passes = []

    def rag(query, top_k=5, ctx=None):
        passes.append(query)
        return [] if len(passes) == 1 else [_hit("rag", 0.7)]

    monkeypatch.setitem(search_service.BACKEND_ADAPTERS, "rag", rag)
    _patch_rewrite(monkeypatch)

    results = search_service.search("q", strategy="rag", config=TEST_CONFIG)

    assert len(passes) == 2
    assert [r.score for r in results] == [0.7]
    assert results[0].metadata["corrective_pass"] is True


def test_strategy_override_corrects_within_same_backend(monkeypatch):
    adapter_calls = []
    _patch_adapters(monkeypatch, score=LOW, calls=adapter_calls, backends=("kb",))
    _patch_rewrite(monkeypatch)

    results = search_service.search("q", strategy="kb", config=TEST_CONFIG)

    assert [c[0] for c in adapter_calls] == ["kb", "kb"]
    assert results[0].strategy == "kb:override[kb]"
    assert results[0].metadata["corrective_pass"] is True


# ---------------------------------------------------------------------------
# No-trigger branch — high confidence and disabled threshold
# ---------------------------------------------------------------------------


def test_high_confidence_never_triggers(monkeypatch):
    adapter_calls = []
    _patch_adapters(monkeypatch, score=HIGH, calls=adapter_calls)
    _patch_taxonomy(monkeypatch)
    rewrite_calls = _patch_rewrite(monkeypatch)

    results = search_service.search("confident query", config=TEST_CONFIG)

    assert rewrite_calls == []
    assert len(adapter_calls) == 2  # one pass over [rag, graph] only
    for r in results:
        assert "corrective_pass" not in r.metadata
        assert "corrective_skipped" not in r.metadata
        assert "crag" not in r.metadata


def test_missing_threshold_disables_loop(monkeypatch):
    _patch_adapters(monkeypatch, score=LOW)
    _patch_taxonomy(monkeypatch)
    rewrite_calls = _patch_rewrite(monkeypatch)

    cfg = {"search": {"fan_out": {"backends": ["rag", "graph"]}, "timeouts": {"default": 5.0}}}
    results = search_service.search("low scoring query", config=cfg)

    assert rewrite_calls == []
    assert all("corrective_pass" not in r.metadata for r in results)


# ---------------------------------------------------------------------------
# Skip branch — rewrite path unavailable or useless
# ---------------------------------------------------------------------------


def test_rewrite_unavailable_returns_originals_with_skip_reason(monkeypatch):
    adapter_calls = []
    _patch_adapters(monkeypatch, score=LOW, calls=adapter_calls)
    _patch_taxonomy(monkeypatch)
    _patch_rewrite(monkeypatch, error=RuntimeError("budget exceeded"))

    results = search_service.search("vague query", config=TEST_CONFIG)

    assert len(adapter_calls) == 2  # no second retrieval pass
    assert results
    for r in results:
        assert "budget exceeded" in r.metadata["corrective_skipped"]
        assert "corrective_pass" not in r.metadata
        assert r.metadata["crag"]["original_top_score"] == LOW


def test_unchanged_rewrite_skips(monkeypatch):
    adapter_calls = []
    _patch_adapters(monkeypatch, score=LOW, calls=adapter_calls)
    _patch_taxonomy(monkeypatch)
    _patch_rewrite(monkeypatch, rewritten="  Vague Query ")

    results = search_service.search("vague query", config=TEST_CONFIG)

    assert len(adapter_calls) == 2
    assert all(
        r.metadata["corrective_skipped"] == "rewrite produced no usable new query"
        for r in results
    )


def test_empty_rewrite_skips(monkeypatch):
    _patch_adapters(monkeypatch, score=LOW)
    _patch_taxonomy(monkeypatch)
    _patch_rewrite(monkeypatch, rewritten="")

    results = search_service.search("vague query", config=TEST_CONFIG)

    assert all("corrective_skipped" in r.metadata for r in results)


# ---------------------------------------------------------------------------
# rewrite_query() — routing-function contract through the LLM router
# ---------------------------------------------------------------------------


class FakeRouter:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def invoke(self, function, request, **kwargs):
        self.calls.append((function, request))
        if self.error is not None:
            raise self.error
        return self.response


def _install_router(monkeypatch, router):
    llm_pkg = importlib.import_module("tools.llm")
    monkeypatch.setattr(llm_pkg, "get_router", lambda config_path=None: router)
    return router


def test_rewrite_query_routes_through_cortex_search_rewrite(monkeypatch):
    router = _install_router(
        monkeypatch,
        FakeRouter(
            response=LLMResponse(
                content='"expanded query with synonyms"\nignored second line',
                provider="fake",
                model_id="fake-model",
            )
        ),
    )

    rewritten = search_service.rewrite_query(
        "orig", [_hit("rag", 0.1, content="ctx snippet")]
    )

    assert rewritten == "expanded query with synonyms"
    function, request = router.calls[0]
    assert function == "cortex_search_rewrite"
    assert "orig" in request.messages[0]["content"]
    assert "ctx snippet" in request.messages[0]["content"]


def test_router_failure_surfaces_as_corrective_skipped(monkeypatch):
    """End-to-end skip branch through the REAL rewrite_query."""
    _patch_adapters(monkeypatch, score=LOW)
    _patch_taxonomy(monkeypatch)
    _install_router(monkeypatch, FakeRouter(error=RuntimeError("no provider available")))

    results = search_service.search("vague query", config=TEST_CONFIG)

    assert results
    for r in results:
        assert "no provider available" in r.metadata["corrective_skipped"]


def test_crag_threshold_present_in_shipped_config():
    cfg = search_service.load_cortex_config(refresh=True)
    threshold = float((cfg.get("search") or {}).get("crag_threshold") or 0.0)
    assert 0.0 < threshold < 1.0


def test_loop_importable_via_canonical_namespace():
    from icdev.tools.cortex.search_service import rewrite_query  # noqa: F401
