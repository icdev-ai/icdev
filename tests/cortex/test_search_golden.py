# CUI // SP-CTI
"""Golden-query regression suite for unified Cortex search (ctx-search-05).

This is the acceptance gate for the search epic: it pins *observable*
behavior — which backend a labeled query routes to, and the *relative*
ordering of fused results across backends — so a future backend change
cannot silently reorder or drop results without a golden failure.

The corpus of labeled queries lives in ``fixtures/golden_queries.json``:
~12 queries across the five routing categories (factual, relational/entity,
document/clearance, exact-term, ambiguous fan-out). Each carries its expected
routing label + backend set and a per-backend score map. Assertions are on
*attribution and order* ("backend X ranks above backend Y for query Q"),
never on absolute scores — scores are fixture-controlled only to establish a
deterministic golden ordering the fusion layer must reproduce.

Backends are monkeypatched through ``search_service.BACKEND_ADAPTERS`` (the
router's call-time dispatch table) and the RAG taxonomy classifier through
its module attribute (shim-aware: importlib + setattr) — the same fixtures
used by tests/cortex/test_search_{adapters,router,crag}.py. Every strategy
branch is covered: auto-routing, ``all``/single-backend override, the CRAG
corrective loop, and the fan-out timeout path. One opt-in integration test
exercises the real RAGRetriever path against a seeded backend; the whole
suite is offline and runs green under ICDEV_AIRGAP=1.
"""
from __future__ import annotations

import importlib
import json
import os
import threading
from pathlib import Path

import pytest

from tools.cortex import CortexSearchResult, search
from tools.cortex import search_service
from tools.cortex.schemas import Citation

# ---------------------------------------------------------------------------
# Golden corpus + fixed routing config
# ---------------------------------------------------------------------------

GOLDEN_PATH = Path(__file__).parent / "fixtures" / "golden_queries.json"
GOLDEN_QUERIES = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))

# Golden routing config: independent of args/cortex_config.yaml edits, with
# the CRAG corrective loop OFF (crag_threshold absent) so result ordering is
# driven purely by the fused backend scores. fan_out order pins the ambiguous
# backend *set*; the golden ``expected_order`` pins the fused *ranking*.
GOLDEN_CONFIG = {
    "search": {
        "router": {"factual_confidence": 0.75},
        "timeouts": {"default": 5.0, "rag": 5.0, "graph": 5.0, "dic": 5.0, "kb": 5.0},
        "fan_out": {"backends": ["rag", "graph", "dic"], "max_workers": 4},
    }
}


def _hit(backend: str, score: float) -> CortexSearchResult:
    return CortexSearchResult(
        content=f"{backend} result",
        score=score,
        backend=backend,
        strategy="native",
        citation=Citation(source_id=f"{backend}-1", source_type=f"{backend}_src"),
    )


def _install_golden_backends(monkeypatch, scores: dict) -> None:
    """Replace every adapter with a fake returning one hit at its golden score.

    A backend absent from ``scores`` still gets a low-scored hit so that an
    unexpected route surfaces as an attribution mismatch rather than an empty
    result set.
    """
    for name in list(search_service.BACKEND_ADAPTERS):
        s = float(scores.get(name, 0.05))

        def fake(query, top_k=5, ctx=None, _n=name, _s=s):
            return [_hit(_n, _s)]

        monkeypatch.setitem(search_service.BACKEND_ADAPTERS, name, fake)


def _patch_taxonomy(monkeypatch, label: str, confidence: float) -> None:
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


# ---------------------------------------------------------------------------
# Golden parametrized suite — routing attribution + relative ordering
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case", GOLDEN_QUERIES, ids=[c["id"] for c in GOLDEN_QUERIES]
)
def test_golden_query_routing_and_ordering(monkeypatch, case):
    _install_golden_backends(monkeypatch, case["backend_scores"])
    if "taxonomy" in case:
        _patch_taxonomy(
            monkeypatch, case["taxonomy"]["label"], case["taxonomy"]["confidence"]
        )

    # 1. Routing decision matches the golden label + backend set.
    route = search_service.classify_route(case["query"], config=GOLDEN_CONFIG)
    assert route["label"] == case["expected_label"], case["id"]
    assert route["backends"] == case["expected_backends"], case["id"]

    # 2. End-to-end search() returns CortexSearchResult objects.
    results = search(case["query"], config=GOLDEN_CONFIG)
    assert results, case["id"]
    assert all(isinstance(r, CortexSearchResult) for r in results), case["id"]

    # 3. Backend attribution: only the routed backends produced results.
    assert {r.backend for r in results} == set(case["expected_backends"]), case["id"]

    # 4. Relative ordering is the golden order (asserted pairwise, not by score).
    order = [r.backend for r in results]
    assert order == case["expected_order"], (case["id"], order)
    for higher, lower in zip(case["expected_order"], case["expected_order"][1:]):
        assert order.index(higher) < order.index(lower), (
            f"{case['id']}: {higher!r} must rank above {lower!r}, got {order}"
        )


def test_golden_corpus_shape():
    """The fixture stays well-formed: labeled, non-trivial, ordering-consistent."""
    assert len(GOLDEN_QUERIES) >= 12
    ids = [c["id"] for c in GOLDEN_QUERIES]
    assert len(ids) == len(set(ids)), "duplicate golden query ids"
    categories = {c["category"] for c in GOLDEN_QUERIES}
    assert categories == {"factual", "relational", "document", "exact_term", "ambiguous"}
    for c in GOLDEN_QUERIES:
        # expected_order must be a permutation of the routed backend set.
        assert set(c["expected_order"]) == set(c["expected_backends"]), c["id"]
        # ordering must be consistent with the fixture scores (descending).
        scores = [c["backend_scores"][b] for b in c["expected_order"]]
        assert scores == sorted(scores, reverse=True), c["id"]


# ---------------------------------------------------------------------------
# Strategy branch coverage — override, all, corrective, timeout
# ---------------------------------------------------------------------------


def test_strategy_all_returns_every_backend(monkeypatch):
    _install_golden_backends(
        monkeypatch, {"rag": 0.9, "graph": 0.7, "dic": 0.5, "kb": 0.3}
    )

    results = search("anything", strategy="all", config=GOLDEN_CONFIG)

    assert [r.backend for r in results] == ["rag", "graph", "dic", "kb"]
    assert all(r.strategy == "all:override[rag+graph+dic+kb]" for r in results)


def test_strategy_override_bypasses_routing(monkeypatch):
    calls = []
    mod = importlib.import_module("tools.rag.query_classifier")

    def boom(query, context=""):
        calls.append(query)
        raise AssertionError("taxonomy must not be consulted on override")

    monkeypatch.setattr(mod, "classify_query", boom)
    _install_golden_backends(monkeypatch, {"kb": 0.8})

    # A query whose auto-route would be graph, forced to kb.
    results = search(
        "What is the relationship between X and Y?", strategy="kb", config=GOLDEN_CONFIG
    )

    assert calls == []
    assert [r.backend for r in results] == ["kb"]
    assert results[0].strategy == "kb:override[kb]"


def test_unknown_strategy_raises():
    with pytest.raises(ValueError, match="Unknown Cortex search strategy"):
        search("q", strategy="not-a-backend", config=GOLDEN_CONFIG)


def test_corrective_branch_rewrites_and_re_retrieves(monkeypatch):
    """Low top score < crag_threshold triggers exactly one rewrite pass."""
    adapter_calls = []

    def rag(query, top_k=5, ctx=None):
        adapter_calls.append(query)
        # First pass low (below threshold), corrective pass better.
        score = 0.2 if len(adapter_calls) == 1 else 0.85
        return [_hit("rag", score)]

    monkeypatch.setitem(search_service.BACKEND_ADAPTERS, "rag", rag)
    monkeypatch.setattr(
        search_service, "rewrite_query", lambda q, results, ctx=None: "expanded query"
    )

    cfg = {"search": {"crag_threshold": 0.5, "timeouts": {"default": 5.0}}}
    results = search("vague", strategy="rag", config=cfg)

    assert adapter_calls == ["vague", "expanded query"]
    assert results[0].metadata["corrective_pass"] is True
    assert results[0].metadata["crag"]["rewritten_query"] == "expanded query"
    assert results[0].score == 0.85


def test_corrective_branch_disabled_by_high_confidence(monkeypatch):
    adapter_calls = []

    def rag(query, top_k=5, ctx=None):
        adapter_calls.append(query)
        return [_hit("rag", 0.9)]

    monkeypatch.setitem(search_service.BACKEND_ADAPTERS, "rag", rag)
    rewrite_calls = []
    monkeypatch.setattr(
        search_service,
        "rewrite_query",
        lambda q, results, ctx=None: rewrite_calls.append(q) or "unused",
    )

    cfg = {"search": {"crag_threshold": 0.5, "timeouts": {"default": 5.0}}}
    results = search("confident", strategy="rag", config=cfg)

    assert adapter_calls == ["confident"]
    assert rewrite_calls == []
    assert "corrective_pass" not in results[0].metadata


def test_timeout_branch_returns_partial_results(monkeypatch):
    """Same property, same instrument change as
    tests/cortex/test_search_router.py::test_fan_out_timeout_returns_partial_results
    — see that docstring. A stopwatch here measured the runner; an Event the
    test never sets measures the router (tsg-iso-02)."""
    started, release, finished = (
        threading.Event(), threading.Event(), threading.Event(),
    )

    def slow_rag(query, top_k=5, ctx=None):
        started.set()
        release.wait(timeout=30)   # hang guard, not a timing assertion
        finished.set()
        return [_hit("rag", 0.9)]

    monkeypatch.setitem(search_service.BACKEND_ADAPTERS, "rag", slow_rag)
    monkeypatch.setitem(
        search_service.BACKEND_ADAPTERS, "graph", lambda q, top_k=5, ctx=None: [_hit("graph", 0.6)]
    )
    monkeypatch.setitem(
        search_service.BACKEND_ADAPTERS, "dic", lambda q, top_k=5, ctx=None: [_hit("dic", 0.4)]
    )
    _patch_taxonomy(monkeypatch, "reasoning", 0.8)

    cfg = {
        "search": {
            "fan_out": {"backends": ["rag", "graph", "dic"]},
            "timeouts": {"default": 5.0, "rag": 0.2},
        }
    }
    try:
        results = search("something slow", config=cfg)
        assert started.is_set(), "precondition: the slow backend was never invoked"
        assert not finished.is_set(), (
            "the router returned only after rag completed — awaited-then-discarded, "
            "not abandoned"
        )
    finally:
        release.set()

    assert {r.backend for r in results} == {"graph", "dic"}
    for r in results:
        assert r.metadata["router"]["timed_out"] == ["rag"]


# ---------------------------------------------------------------------------
# Public API contract + air-gap
# ---------------------------------------------------------------------------


def test_search_and_result_type_exported_by_package():
    # Both the entry point and the result type ship on the public facade.
    # Post-governance (ctx-govern-04) the public `search` entry point is the
    # GOVERNED facade (tools/cortex/api.py, run through GovernancePipeline); the
    # raw adapter stays available as search_service.search.
    import tools.cortex as cortex

    assert callable(cortex.search)
    assert cortex.search.__module__.endswith("cortex.api")
    assert callable(search_service.search)
    assert cortex.CortexSearchResult is CortexSearchResult
    assert "search" in cortex.__all__
    assert "CortexSearchResult" in cortex.__all__


def test_public_api_returns_cortex_search_results(monkeypatch):
    _install_golden_backends(monkeypatch, {"graph": 0.7})

    # Routed entirely through the tools.cortex.search public symbol.
    results = search(
        "which services depend on the gateway?", config=GOLDEN_CONFIG
    )

    assert results
    for r in results:
        assert isinstance(r, CortexSearchResult)
        assert isinstance(r.citation, Citation)


def test_suite_runs_green_when_airgapped(monkeypatch):
    """The offline suite must pass with the air-gap invariant forced on."""
    monkeypatch.setenv("ICDEV_AIRGAP", "1")
    _install_golden_backends(monkeypatch, {"rag": 0.8})
    _patch_taxonomy(monkeypatch, "fact_single", 0.85)

    results = search("What is AC-2?", config=GOLDEN_CONFIG)

    assert [r.backend for r in results] == ["rag"]
    assert all(isinstance(r, CortexSearchResult) for r in results)


def test_golden_importable_via_canonical_namespace():
    from icdev.tools.cortex import CortexSearchResult as CanonResult  # noqa: F401
    from icdev.tools.cortex import search as canon_search  # noqa: F401


# ---------------------------------------------------------------------------
# Integration — real RAGRetriever path against a seeded backend (opt-in)
# ---------------------------------------------------------------------------

# tests/conftest.py force-sets ICDEV_STORAGE_BACKEND=sqlite for every test, so
# the backend var alone can't gate this — it is always present. Gate on an
# explicit opt-in instead so the default `pytest tests/cortex/ -q` run skips
# cleanly, and only exercises the live embedding/DB path when a seeded dev
# corpus is available (ICDEV_CORTEX_INTEGRATION=1).
_RUN_INTEGRATION = os.environ.get("ICDEV_CORTEX_INTEGRATION", "").lower() in (
    "1",
    "true",
    "yes",
)


@pytest.mark.integration
@pytest.mark.skipif(
    not _RUN_INTEGRATION,
    reason="opt-in: set ICDEV_CORTEX_INTEGRATION=1 (with a seeded "
    "ICDEV_STORAGE_BACKEND) to exercise the real RAGRetriever path",
)
def test_real_rag_retriever_path_returns_cortex_results():
    assert os.environ.get("ICDEV_STORAGE_BACKEND"), (
        "integration run requires ICDEV_STORAGE_BACKEND to be configured"
    )
    # No adapter monkeypatch: this drives the real search_rag -> RAGRetriever
    # pipeline against whatever the configured backend has seeded. GOLDEN_CONFIG
    # omits crag_threshold, so an empty/low-scoring corpus cannot pull the live
    # LLM rewrite path into this retrieval-focused integration check.
    results = search(
        "account management controls", strategy="rag", top_k=3, config=GOLDEN_CONFIG
    )

    assert isinstance(results, list)
    for r in results:
        assert isinstance(r, CortexSearchResult)
        assert r.backend == "rag"
        assert isinstance(r.citation, Citation)
        assert 0.0 <= r.score <= 1.0
