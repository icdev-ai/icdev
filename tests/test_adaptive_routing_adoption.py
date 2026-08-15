# CUI // SP-CTI
"""Adoption of AdaptiveRetriever at the Cortex search seam (trust-self-03).

``tools/rag/toggle_harness.py`` classified ``adaptive_routing`` as
WRAPPER-UNADOPTED: ``AdaptiveRetriever`` wraps ``RAGRetriever``, so it can never
appear in the retriever's own import closure and only a CALLER can adopt it.
Nothing had. These tests pin the three things that adoption has to be true for,
none of which a "the import exists" check would catch:

1. the wrapper is actually on the Cortex ``rag`` path, and the harness agrees;
2. the seam is a CITATION surface, so the ``none``/skip route cannot fire there
   even when the classifier insists on it — a wrongly-skipped retrieval would
   otherwise let Cortex answer with no evidence at all;
3. tenant scoping survives the wrapper (its own lazy retriever is built with NO
   ``tenant_id``, which would silently drop the vector-store tenant filter);

plus the ``complex_top_k`` regression the adoption exposed: the widened
single-pass fallback applied only when the caller passed no ``top_k``, and every
real caller passes one — so the decompose route was byte-identical to
single_pass and the config key did nothing.
"""
from __future__ import annotations

import importlib

import pytest

from tools.rag import adaptive_router
from tools.rag.adaptive_router import AdaptiveRetriever, load_labeled_mix, measure_savings


# ── fakes ─────────────────────────────────────────────────────────────────────


class _FakeSearchResult:
    """Minimal stand-in for tools/rag/retriever.SearchResult."""

    def __init__(self):
        self.content = "AC-2 requires account management."
        self.chunk_id = "c1"
        self.chunk_index = 0
        self.source_id = "s1"
        self.source_table = "rag_chunks"
        self.source_type = "compliance"
        self.classification = "CUI"
        self.tier = "hot"
        self.score = 0.7
        self.bm25_score = 0.3
        self.time_decay_score = 1.0
        self.rerank_score = 0.8
        self.final_score = 0.9


class _FakeRAGRetriever:
    """Records the tenant it was scoped to and every search kwarg it saw."""

    built: list = []

    def __init__(self, tenant_id: str = "", config=None):
        self.tenant_id = tenant_id
        self.searches: list = []
        _FakeRAGRetriever.built.append(self)

    def search(self, query, **kwargs):
        self.searches.append((query, kwargs))
        return [_FakeSearchResult()]


def _cfg(enabled: bool, **extra) -> dict:
    routing = {"enabled": enabled, "use_llm_classifier": False}
    routing.update(extra)
    return {"rag": {"adaptive_routing": routing}}


@pytest.fixture
def cortex_seam(monkeypatch):
    """search_rag wired to a fake retriever, with the rag config under test.

    Resolves the retriever module through the search service's OWN namespace
    root: under the compat shim ``tools.rag.retriever`` and
    ``icdev.tools.rag.retriever`` are DISTINCT module objects, and patching the
    wrong one installs a fake nothing ever calls.
    """
    from tools.cortex import search_service

    retriever_mod = importlib.import_module(f"{search_service._NS}.rag.retriever")

    def _install(config: dict):
        _FakeRAGRetriever.built = []
        monkeypatch.setattr(retriever_mod, "RAGRetriever", _FakeRAGRetriever)
        # AdaptiveRetriever resolves its config through this loader.
        monkeypatch.setattr(retriever_mod, "_load_rag_config", lambda *a, **k: config)
        return search_service

    return _install


# ── 1. the wrapper is on the Cortex rag path, and the harness agrees ──────────


def test_toggle_harness_no_longer_reports_adaptive_routing_unadopted():
    from tools.rag import toggle_harness

    toggle_harness._repo_importers.cache_clear()
    verdict = toggle_harness.probe_reachability("adaptive_routing")

    assert verdict.verdict != "WRAPPER-UNADOPTED"
    assert verdict.verdict == "WIRED"
    assert verdict.measurable is True
    assert "tools.cortex.search_service" in verdict.importers


def test_enabled_toggle_routes_cortex_rag_through_the_wrapper(cortex_seam):
    search_service = cortex_seam(_cfg(True))

    results = search_service.search_rag("What does AC-2 require?", top_k=5)

    assert results, "the fake retriever returns one hit"
    routing = results[0].metadata["adaptive_routing"]
    assert routing["route"] in ("single_pass", "decompose")
    assert routing["retrieved"] is True


def test_disabled_toggle_is_the_unchanged_single_pass(cortex_seam):
    search_service = cortex_seam(_cfg(False))

    results = search_service.search_rag("What does AC-2 require?", top_k=5)

    assert results
    # No routing record at all — the metadata contract is unchanged when off.
    assert "adaptive_routing" not in results[0].metadata
    assert len(_FakeRAGRetriever.built) == 1
    assert _FakeRAGRetriever.built[0].searches[0][1]["top_k"] == 5


# ── 2. citation safety: the skip route cannot fire on this surface ────────────


def test_skip_route_cannot_fire_at_the_cortex_seam(cortex_seam, monkeypatch):
    """Even a classifier that insists on 'none' must not skip retrieval here.

    Cortex results carry a Citation and the facade suppresses uncited content,
    so a skipped retrieval would let an answer be produced from no evidence.
    The clamp is enforced in the wrapper; this asserts the SEAM asks for it.
    """
    seen: dict = {}

    def _fake_classify(query, **kwargs):
        seen.update(kwargs)
        # Deliberately bypass the wrapper's own clamp so the only thing that can
        # save us is decide_route() honouring requires_citations.
        return {"complexity": "none", "source": "test", "vocabulary_version": "t"}

    monkeypatch.setattr(adaptive_router, "classify_complexity", _fake_classify)
    search_service = cortex_seam(_cfg(True))

    results = search_service.search_rag("hello", top_k=5)

    assert seen.get("requires_citations") is True
    assert results, "retrieval must still have run"
    assert results[0].metadata["adaptive_routing"]["route"] == "single_pass"
    assert results[0].metadata["adaptive_routing"]["retrieved"] is True


# ── 3. tenant scoping survives the wrapper ────────────────────────────────────


def test_adaptive_path_keeps_the_tenant_scope(cortex_seam):
    from tools.cortex.schemas import CortexContext

    search_service = cortex_seam(_cfg(True))
    search_service.search_rag("What does AC-2 require?", top_k=5,
                              ctx=CortexContext(tenant_id="acme"))

    scoped = [r for r in _FakeRAGRetriever.built if r.searches]
    assert scoped, "the wrapper must have used a retriever we built"
    assert all(r.tenant_id == "acme" for r in scoped), (
        "AdaptiveRetriever's own lazy _get_retriever() builds RAGRetriever() "
        "with no tenant_id — the seam must inject a scoped one"
    )


# ── 4. complex_top_k actually widens (the regression the adoption exposed) ────


def test_complex_top_k_widens_even_when_the_caller_passes_top_k():
    class _Router:
        def invoke(self, fn, req, **kw):
            class _R:
                content = '{"complexity": "complex"}'
            return _R()

    retriever = _FakeRAGRetriever()
    adaptive = AdaptiveRetriever(
        retriever=retriever,
        router=_Router(),
        config=_cfg(True, use_llm_classifier=True, complex_top_k=10),
    )

    out = adaptive.retrieve("compare AC-2 versus AC-3", top_k=5)

    assert out["route"] == "decompose"
    assert retriever.searches[0][1]["top_k"] == 10, (
        "a caller-supplied top_k used to suppress complex_top_k entirely"
    )


def test_complex_top_k_never_narrows_a_wider_caller():
    class _Router:
        def invoke(self, fn, req, **kw):
            class _R:
                content = '{"complexity": "complex"}'
            return _R()

    retriever = _FakeRAGRetriever()
    adaptive = AdaptiveRetriever(
        retriever=retriever,
        router=_Router(),
        config=_cfg(True, use_llm_classifier=True, complex_top_k=10),
    )

    adaptive.retrieve("compare AC-2 versus AC-3", top_k=25)

    assert retriever.searches[0][1]["top_k"] == 25


# ── 5. measure_savings on the real, committed query mix ───────────────────────


def test_labeled_mix_is_the_committed_golden_set():
    mix = load_labeled_mix()

    assert len(mix) >= 40, "the golden query set is the real mix, not a fixture"
    assert all(m["gold_route"] in ("single_pass", "decompose", "skip") for m in mix)

    golds = {m["gold_route"] for m in mix}
    assert golds == {"single_pass", "decompose"}, (
        "a mix whose gold labels are all one value makes accuracy meaningless"
    )

    by_id = {m["id"]: m for m in mix}
    # Explicit YAML overrides win over the pattern-derived default. Both are
    # 'complex' (pattern default single_pass) but their substring targets do not
    # co-occur in any one corpus chunk.
    assert by_id["q-cx-fedramp-vs-cmmc"]["gold_route"] == "decompose"
    assert by_id["q-cx-prove-logging-works"]["gold_route"] == "decompose"
    # ...and a 'complex' query whose targets DO co-occur keeps the default.
    assert by_id["q-cx-how-impact-decided"]["gold_route"] == "single_pass"


def test_measure_savings_reports_a_real_number_on_the_real_mix():
    mix = load_labeled_mix()
    result = measure_savings(mix, config={"use_llm_classifier": False})

    assert result["n"] == len(mix)
    assert 0.0 < result["routing_accuracy"] <= 1.0
    assert sum(result["confusion"].values()) == len(mix)
    # Cortex posture: every query is a citation surface query, so the skip route
    # is structurally unavailable and zero calls can be saved. That is the
    # correct number for this seam, not a missing measurement.
    assert result["retrieval_calls_saved"] == 0
    assert result["skip_rate"] == 0.0


# --------------------------------------------------------------------------- #
# Surface scoping must survive the adaptive path (trust-self-02 x trust-self-03)
# --------------------------------------------------------------------------- #

def test_both_retrieval_paths_declare_the_same_surface():
    """`_rag_retrieve` has two branches and BOTH must name the surface.

    trust-self-02 scoped reflective reranking per surface by passing
    `surface="chat_rag"` to the adapter's retrieval call. trust-self-03 then put
    AdaptiveRetriever in front of that call, creating a second path. If only the
    plain path carries the surface, enabling `rag.adaptive_routing` silently
    turns reflective reranking OFF for chat_rag — a config toggle disabling an
    unrelated feature, with nothing reporting the change and every test still
    green.

    Asserted at PARSE time and on LITERALS, matching
    tests/rag/test_reflective_surface_scoping.py: a name reference would degrade
    the guarantee to "some identifier we hope equals chat_rag".
    """
    import ast
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[1] / "tools/cortex/search_service.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))

    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "_rag_retrieve"),
        None,
    )
    assert fn is not None, "_rag_retrieve not found — did the adapter get renamed?"

    surfaces = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name not in ("run_rag_search", "retrieve"):
            continue
        surfaces.append(next(
            (kw.value.value for kw in node.keywords
             if kw.arg == "surface" and isinstance(kw.value, ast.Constant)),
            None,
        ))

    assert len(surfaces) == 2, f"expected both retrieval branches, found {surfaces}"
    assert all(s == "chat_rag" for s in surfaces), surfaces
