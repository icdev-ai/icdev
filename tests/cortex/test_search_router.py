# CUI // SP-CTI
"""Tests for the Cortex search strategy router (ctx-search-02).

Backend adapters are monkeypatched via ``search_service.BACKEND_ADAPTERS``
(the router's call-time dispatch table); the RAG taxonomy classifier is
patched at the module attribute the router resolves at call time
(shim-aware: importlib + setattr).
"""
from __future__ import annotations

import importlib
import os
import time

import pytest

from tools.cortex import search_service
from tools.cortex.schemas import Citation, CortexContext, CortexSearchResult


# Test config keeps routing independent of args/cortex_config.yaml edits.
TEST_CONFIG = {
    "search": {
        "router": {"factual_confidence": 0.75},
        "timeouts": {"default": 5.0, "rag": 5.0, "graph": 5.0, "dic": 5.0, "kb": 5.0},
        "fan_out": {"backends": ["rag", "graph", "dic"], "max_workers": 4},
        "domains": {
            "security": {"backends": ["rag", "kb"], "collections": ["security"]},
        },
    }
}


def _hit(backend, score=0.5, strategy="native", sid="1"):
    return CortexSearchResult(
        content=f"{backend} hit",
        score=score,
        backend=backend,
        strategy=strategy,
        citation=Citation(source_id=f"{backend}-{sid}", source_type=f"{backend}_src"),
    )


def _patch_adapters(monkeypatch, backends=("rag", "graph", "dic", "kb"), calls=None):
    """Replace every adapter with a fake returning one hit per call."""
    for name in backends:
        def fake(query, top_k=5, ctx=None, _name=name):
            if calls is not None:
                calls.append(_name)
            return [_hit(_name)]

        monkeypatch.setitem(search_service.BACKEND_ADAPTERS, name, fake)


def _patch_taxonomy(monkeypatch, label="fact_single", confidence=0.8, calls=None):
    mod = importlib.import_module("tools.rag.query_classifier")

    def fake_classify(query, context=""):
        if calls is not None:
            calls.append(query)
        return {"label": label, "confidence": confidence, "method": "heuristic"}

    monkeypatch.setattr(mod, "classify_query", fake_classify)


# ---------------------------------------------------------------------------
# classify_route — deterministic labeled fixtures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query,label,backends",
    [
        # relational/entity -> graph
        (
            "What is the relationship between the firewall and the core router?",
            "relational",
            ["graph"],
        ),
        ("Which services depend on the auth gateway?", "relational", ["graph"]),
        ("Who owns the SIPR enclave?", "relational", ["graph"]),
        # document/clearance-scoped -> dic
        (
            "Which section of the Network Security SOP covers key rotation?",
            "document",
            ["dic"],
        ),
        ("Show me the clearance requirements memo", "document", ["dic"]),
        # exact-term/identifier -> kb
        ('How do I fix "connection pool exhausted"?', "exact_term", ["kb"]),
        ("db_connection_timeout keeps recurring", "exact_term", ["kb"]),
        ("Is CVE-2024-12345 patched?", "exact_term", ["kb"]),
        ("What raises tools.db.storage errors?", "exact_term", ["kb"]),
    ],
)
def test_pattern_routes_are_deterministic(query, label, backends):
    for _ in range(3):
        route = search_service.classify_route(query, config=TEST_CONFIG)
        assert route["label"] == label
        assert route["backends"] == backends
        assert route["method"] == "pattern"


def test_factual_query_routes_to_rag(monkeypatch):
    _patch_taxonomy(monkeypatch, label="fact_single", confidence=0.8)

    route = search_service.classify_route("What is AC-2?", config=TEST_CONFIG)

    assert route["label"] == "factual"
    assert route["backends"] == ["rag"]
    assert route["taxonomy"]["label"] == "fact_single"


def test_low_confidence_factual_is_ambiguous(monkeypatch):
    _patch_taxonomy(monkeypatch, label="fact_single", confidence=0.5)

    route = search_service.classify_route("thing about the stuff", config=TEST_CONFIG)

    assert route["label"] == "ambiguous"
    assert route["backends"] == ["rag", "graph", "dic"]


def test_ambiguous_query_fans_out(monkeypatch):
    _patch_taxonomy(monkeypatch, label="reasoning", confidence=0.8)

    route = search_service.classify_route(
        "How would zero trust change our posture?", config=TEST_CONFIG
    )

    assert route["label"] == "ambiguous"
    assert route["backends"] == ["rag", "graph", "dic"]


def test_taxonomy_failure_degrades_to_fan_out(monkeypatch):
    mod = importlib.import_module("tools.rag.query_classifier")

    def boom(query, context=""):
        raise RuntimeError("classifier down")

    monkeypatch.setattr(mod, "classify_query", boom)

    route = search_service.classify_route("some plain query", config=TEST_CONFIG)

    assert route["label"] == "ambiguous"
    assert route["backends"] == ["rag", "graph", "dic"]


def test_empty_query_fans_out():
    route = search_service.classify_route("   ", config=TEST_CONFIG)

    assert route["label"] == "ambiguous"
    assert route["backends"] == ["rag", "graph", "dic"]


# ---------------------------------------------------------------------------
# search() — routing, override, domain scoping, strategy recording
# ---------------------------------------------------------------------------


def test_auto_routes_relational_to_graph_only(monkeypatch):
    calls = []
    _patch_adapters(monkeypatch, calls=calls)

    results = search_service.search(
        "What is connected to the core router?", config=TEST_CONFIG
    )

    assert calls == ["graph"]
    assert [r.backend for r in results] == ["graph"]
    assert results[0].strategy == "auto:relational[graph]"
    # Native adapter strategy is preserved, routing decision is recorded
    assert results[0].metadata["backend_strategy"] == "native"
    assert results[0].metadata["router"]["label"] == "relational"
    assert results[0].metadata["router"]["backends"] == ["graph"]
    assert results[0].metadata["router"]["timed_out"] == []


def test_auto_ambiguous_fans_out_to_configured_backends(monkeypatch):
    calls = []
    _patch_adapters(monkeypatch, calls=calls)
    _patch_taxonomy(monkeypatch, label="summary", confidence=0.8)

    results = search_service.search("overview of everything", config=TEST_CONFIG)

    assert sorted(calls) == ["dic", "graph", "rag"]
    assert {r.backend for r in results} == {"rag", "graph", "dic"}
    assert all(r.strategy == "auto:ambiguous[rag+graph+dic]" for r in results)


def test_strategy_override_bypasses_classification(monkeypatch):
    calls = []
    taxonomy_calls = []
    _patch_adapters(monkeypatch, calls=calls)
    _patch_taxonomy(monkeypatch, calls=taxonomy_calls)

    # A query whose auto-route would be graph, forced to kb
    results = search_service.search(
        "What is the relationship between X and Y?",
        strategy="kb",
        config=TEST_CONFIG,
    )

    assert taxonomy_calls == []
    assert calls == ["kb"]
    assert [r.backend for r in results] == ["kb"]
    assert results[0].strategy == "kb:override[kb]"


def test_strategy_all_runs_every_backend(monkeypatch):
    calls = []
    _patch_adapters(monkeypatch, calls=calls)

    results = search_service.search("anything", strategy="all", config=TEST_CONFIG)

    assert sorted(calls) == ["dic", "graph", "kb", "rag"]
    assert {r.backend for r in results} == {"rag", "graph", "dic", "kb"}
    assert all(r.strategy == "all:override[rag+graph+dic+kb]" for r in results)


def test_unknown_strategy_raises():
    with pytest.raises(ValueError, match="Unknown Cortex search strategy"):
        search_service.search("q", strategy="bogus", config=TEST_CONFIG)


def test_domain_restricts_backend_scope(monkeypatch):
    calls = []
    _patch_adapters(monkeypatch, calls=calls)
    _patch_taxonomy(monkeypatch, label="reasoning", confidence=0.8)

    # Ambiguous fan-out [rag, graph, dic] intersected with security's [rag, kb]
    results = search_service.search(
        "how should we harden things",
        ctx=CortexContext(domain="security"),
        config=TEST_CONFIG,
    )

    assert calls == ["rag"]
    assert [r.backend for r in results] == ["rag"]
    scope = results[0].metadata["router"]["domain_scope"]
    assert scope["domain"] == "security"
    assert scope["collections"] == ["security"]


def test_domain_with_no_overlap_falls_back_to_domain_backends(monkeypatch):
    calls = []
    _patch_adapters(monkeypatch, calls=calls)

    # Relational -> [graph], security allows [rag, kb] -> domain wins
    results = search_service.search(
        "what is connected to the enclave",
        ctx=CortexContext(domain="security"),
        config=TEST_CONFIG,
    )

    assert sorted(calls) == ["kb", "rag"]
    assert {r.backend for r in results} == {"rag", "kb"}


def test_results_sorted_by_score(monkeypatch):
    def rag(query, top_k=5, ctx=None):
        # Distinct sources (sid a/b) — two different rag documents, not a dup.
        return [_hit("rag", score=0.3, sid="a"), _hit("rag", score=0.9, sid="b")]

    def graph(query, top_k=5, ctx=None):
        return [_hit("graph", score=0.6)]

    monkeypatch.setitem(search_service.BACKEND_ADAPTERS, "rag", rag)
    monkeypatch.setitem(search_service.BACKEND_ADAPTERS, "graph", graph)
    _patch_taxonomy(monkeypatch, label="reasoning", confidence=0.8)

    cfg = {
        "search": {
            "fan_out": {"backends": ["rag", "graph"]},
            "timeouts": {"default": 5.0},
        }
    }
    results = search_service.search("compare things", config=cfg)

    # RRF ranks each backend's list, then orders by fused score with ties broken
    # by raw score: rag@0.9 (rank1) and graph@0.6 (rank1) tie on 1/(60+1) -> raw
    # score wins, then rag@0.3 (rank2, 1/(60+2)). Same intuitive order here.
    assert [r.score for r in results] == [0.9, 0.6, 0.3]
    assert all("rrf" in (r.raw_scores or {}) for r in results)


# ---------------------------------------------------------------------------
# Fan-out timeouts — slow backend abandoned, partial results returned
# ---------------------------------------------------------------------------


def test_fan_out_timeout_returns_partial_results(monkeypatch, caplog):
    def slow_rag(query, top_k=5, ctx=None):
        time.sleep(2.0)
        return [_hit("rag")]

    monkeypatch.setitem(search_service.BACKEND_ADAPTERS, "rag", slow_rag)
    _patch_adapters(monkeypatch, backends=("graph", "dic"))
    _patch_taxonomy(monkeypatch, label="reasoning", confidence=0.8)

    cfg = {
        "search": {
            "fan_out": {"backends": ["rag", "graph", "dic"]},
            "timeouts": {"default": 5.0, "rag": 0.2},
        }
    }
    start = time.monotonic()
    results = search_service.search("something slow", config=cfg)
    elapsed = time.monotonic() - start

    # Slow backend abandoned well before its 2s sleep completes
    assert elapsed < 1.5
    assert {r.backend for r in results} == {"graph", "dic"}
    for r in results:
        assert r.metadata["router"]["timed_out"] == ["rag"]
        assert r.strategy == "auto:ambiguous[rag+graph+dic]"


def test_single_backend_timeout_returns_empty(monkeypatch, caplog):
    def slow_kb(query, top_k=5, ctx=None):
        time.sleep(2.0)
        return [_hit("kb")]

    monkeypatch.setitem(search_service.BACKEND_ADAPTERS, "kb", slow_kb)

    cfg = {"search": {"timeouts": {"default": 5.0, "kb": 0.2}}}
    with caplog.at_level("WARNING"):
        results = search_service.search("q", strategy="kb", config=cfg)

    assert results == []
    assert "kb backend timed out" in caplog.text


def test_unknown_backend_in_fan_out_skipped(monkeypatch, caplog):
    calls = []
    _patch_adapters(monkeypatch, backends=("kb",), calls=calls)
    _patch_taxonomy(monkeypatch, label="reasoning", confidence=0.8)

    cfg = {"search": {"fan_out": {"backends": ["kb", "nope"]}}}
    with caplog.at_level("WARNING"):
        results = search_service.search("q", config=cfg)

    assert calls == ["kb"]
    assert [r.backend for r in results] == ["kb"]
    assert "unknown backend 'nope' skipped" in caplog.text


# ---------------------------------------------------------------------------
# Config + namespace
# ---------------------------------------------------------------------------


def test_cortex_config_loads_from_args():
    cfg = search_service.load_cortex_config(refresh=True)

    search_cfg = cfg.get("search") or {}
    assert "timeouts" in search_cfg
    assert float(search_cfg["timeouts"]["default"]) > 0
    assert search_cfg["fan_out"]["backends"]


def test_router_uses_the_canonical_config_loader():
    """cxo-perf-01: search_service must NOT define its own loader.

    The shadow copy skipped the CORTEX_CONFIG_DEFAULTS merge, ignored
    $ICDEV_CORTEX_CONFIG, and cached forever — so cortex.search and
    cortex.govern read the same YAML with different semantics than every
    other caller.
    """
    from tools.cortex import config as cortex_config

    assert search_service.load_cortex_config is cortex_config.load_cortex_config
    assert not hasattr(search_service, "_find_repo_root")
    assert not hasattr(search_service, "_config_cache")


def test_router_config_merges_defaults_and_reloads_on_edit(tmp_path, monkeypatch):
    """Defaults are merged in, and an edit lands without a process restart."""
    path = tmp_path / "cortex_config.yaml"
    path.write_text("search:\n  crag_threshold: 0.11\n", encoding="utf-8")
    monkeypatch.setenv("ICDEV_CORTEX_CONFIG", str(path))

    cfg = search_service.load_cortex_config()
    assert cfg["search"]["crag_threshold"] == 0.11
    # Keys absent from the file above come from CORTEX_CONFIG_DEFAULTS.
    assert cfg["search"]["rrf_k"] == 60
    assert cfg["search"]["strategy_weights"]["rag"] == 1.0

    path.write_text("search:\n  crag_threshold: 0.42\n  rrf_k: 7\n", encoding="utf-8")
    st = path.stat()
    os.utime(path, (st.st_atime, st.st_mtime + 5))

    # No refresh=True, no restart — the mtime-keyed cache invalidates itself.
    reloaded = search_service.load_cortex_config()
    assert reloaded["search"]["crag_threshold"] == 0.42
    assert reloaded["search"]["rrf_k"] == 7


def test_router_importable_via_canonical_namespace():
    from icdev.tools.cortex.search_service import (  # noqa: F401
        CORTEX_STRATEGIES,
        classify_route,
        search,
    )

    assert set(CORTEX_STRATEGIES) == {"auto", "all", "rag", "graph", "dic", "kb", "sme"}
