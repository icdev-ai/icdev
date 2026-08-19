"""Tests for the DIC search governed evidence seam (cef-di-04).

``DICSearchEngine.search()`` reached exactly one rung: a bare
``RAGRetriever.search(...)`` inside ``_rag_search``. cef-di-04 routes that
candidate half through ONE call — ``cortex.resolve(query)`` — behind
``cortex.enabled`` in ``args/dic_search_config.yaml``.

The load-bearing guarantees pinned here:

* the seam is OFF by default and off means it is NEVER consulted, so the
  rollback is a flag flip rather than a merge revert;
* the CYCLE is cut. ``search()`` IS Cortex's ``dic`` rung, so a resolve fans
  back into it — and it fans back on a POOL WORKER THREAD, so the interlock
  must be process-wide. A thread-local guard passes a single-threaded test and
  recurses in production, and that is the case with its own test here;
* the clearance drop still runs strictly BEFORE the ``top_k`` cap on the
  governed path, so the cap fills with accessible results;
* the BM25 air-gap fallback is still the floor under both paths;
* every way the seam declines returns ``None`` (the legacy path) and is
  COUNTED, never silent;
* the ungoverned filesystem wiki cache is gone — symbols and all.
"""
from __future__ import annotations

import importlib
import threading

import pytest

se = importlib.import_module("tools.document_intelligence.search_engine")
ev = importlib.import_module("tools.document_intelligence.search_evidence")


# --------------------------------------------------------------------------- #
# Doubles
# --------------------------------------------------------------------------- #
class _Citation:
    """Shaped like ``tools.cortex.schemas.Citation``."""

    def __init__(self, source_id, snippet, source_type="rag_chunk",
                 source_table="rag_chunks", title="", clearance_required=""):
        self.source_id = source_id
        self.snippet = snippet
        self.source_type = source_type
        self.source_table = source_table
        self.title = title
        self.url = ""
        self.classification = "CUI"
        self.clearance_required = clearance_required
        self.provenance_id = "prov-1"


class _Resolution:
    def __init__(self, citations, verdict="unknown", backends=None, errors=None):
        self.citations = citations
        self.verdict = verdict
        self.backends_consulted = backends or ["currency", "rag", "dic"]
        self.backend_errors = errors or []


def _config(**overrides):
    """A config dict for the seam — never the shipped file, so ON is explicit."""
    block = {
        "enabled": True,
        "top_k": 5,
        "max_resolves_per_run": 50,
        "fallback_on_empty": True,
        "honour_collection_scope": False,
    }
    block.update(overrides)
    return {"cortex": block}


@pytest.fixture(autouse=True)
def _clean_state():
    """Per-test run state AND a zeroed process-wide interlock."""
    ev.reset_run_state()
    ev.reset_interlock()
    yield
    ev.reset_run_state()
    ev.reset_interlock()


def _patch_resolve(monkeypatch, fn):
    """Patch ``tools.cortex.api.resolve`` — the seam late-imports it by name."""
    api = importlib.import_module("tools.cortex.api")
    monkeypatch.setattr(api, "resolve", fn, raising=False)


# --------------------------------------------------------------------------- #
# The toggle — OFF is the shipped default and off means never consulted
# --------------------------------------------------------------------------- #
class TestToggle:
    def test_shipped_config_is_off(self):
        """The rollback is a flag flip, so the flag must ship off."""
        assert ev.cortex_enabled() is False
        assert ev.CONFIG_PATH.is_file(), "args/dic_search_config.yaml must exist"

    def test_off_never_calls_cortex(self, monkeypatch):
        calls = []
        _patch_resolve(monkeypatch, lambda *a, **k: calls.append(a))

        assert ev.resolve_evidence("anything", config={"cortex": {"enabled": False}}) is None
        assert calls == []

    def test_unreadable_config_reads_as_off(self, tmp_path):
        missing = tmp_path / "nope.yaml"
        assert ev.load_config(missing) == {}
        assert ev.cortex_enabled(ev.load_config(missing)) is False

    def test_blank_query_declines(self):
        assert ev.resolve_evidence("   ", config=_config()) is None


# --------------------------------------------------------------------------- #
# The candidate lanes
# --------------------------------------------------------------------------- #
class TestCandidates:
    def test_candidates_are_index_aligned_with_citations(self, monkeypatch):
        _patch_resolve(monkeypatch, lambda *a, **k: _Resolution([
            _Citation("chunk-a", "alpha evidence text"),
            _Citation("chunk-b", "beta evidence text"),
        ]))

        bundle = ev.resolve_evidence("q", config=_config())

        assert bundle is not None and not bundle.is_empty
        assert len(bundle.candidates) == len(bundle.citations) == 2
        for cand, cite in zip(bundle.candidates, bundle.citations):
            assert cand.content == cite["detail"]
            assert cand.chunk_id == cite["source_id"]

    def test_candidate_shape_matches_the_legacy_retriever(self, monkeypatch):
        """Downstream reads .chunk_id/.content/.source_id/.final_score."""
        _patch_resolve(monkeypatch, lambda *a, **k: _Resolution([
            _Citation("chunk-a", "alpha"),
        ]))

        cand = ev.resolve_evidence("q", config=_config()).candidates[0]

        assert cand.chunk_id == "chunk-a"
        assert cand.source_id == "chunk-a"
        assert cand.content == "alpha"
        assert 0.0 < cand.final_score <= 1.0

    def test_rank_order_is_preserved_descending(self, monkeypatch):
        _patch_resolve(monkeypatch, lambda *a, **k: _Resolution([
            _Citation("c%d" % i, "text %d" % i) for i in range(4)
        ]))

        scores = [c.final_score for c in ev.resolve_evidence("q", config=_config()).candidates]

        assert scores == sorted(scores, reverse=True)

    def test_pack_evidence_is_never_a_candidate(self, monkeypatch):
        """A pack's own verdict rationale must not become search evidence."""
        _patch_resolve(monkeypatch, lambda *a, **k: _Resolution([
            _Citation("pack-1", "the pack concluded X", source_type="pack_evidence"),
            _Citation("chunk-a", "a real corpus chunk"),
        ]))

        bundle = ev.resolve_evidence("q", config=_config())

        assert [c.chunk_id for c in bundle.candidates] == ["chunk-a"]

    def test_citation_with_no_source_id_is_dropped_not_invented(self, monkeypatch):
        _patch_resolve(monkeypatch, lambda *a, **k: _Resolution([
            _Citation("", "orphan text"),
            _Citation("chunk-a", "  "),
            _Citation("chunk-b", "kept"),
        ]))

        bundle = ev.resolve_evidence("q", config=_config())

        assert [c.chunk_id for c in bundle.candidates] == ["chunk-b"]

    def test_top_k_bounds_the_lane(self, monkeypatch):
        _patch_resolve(monkeypatch, lambda *a, **k: _Resolution([
            _Citation("c%d" % i, "text %d" % i) for i in range(20)
        ]))

        bundle = ev.resolve_evidence("q", config=_config(), top_k=3)

        assert len(bundle.candidates) == 3


# --------------------------------------------------------------------------- #
# THE CYCLE — and why the interlock cannot be thread-local
# --------------------------------------------------------------------------- #
class TestCycle:
    def test_reentrant_ask_declines_and_is_counted(self, monkeypatch):
        _patch_resolve(monkeypatch, lambda *a, **k: _Resolution([_Citation("c", "t")]))
        setattr(ev._shared(), ev._DEPTH_ATTR, 1)  # a resolution is in flight

        assert ev.resolve_evidence("q", config=_config()) is None
        assert ev.run_stats()["reentrant"] == 1
        assert ev.interlock_fires() == 1

    def test_the_fire_count_is_process_wide_not_per_run(self, monkeypatch):
        """A per-run counter tallies on the POOL WORKER thread and the caller
        reads 0 -- observed on the live canvas before this moved out of the
        thread-local run state (three fan-outs, ``reentrant: 0``).
        """
        _patch_resolve(monkeypatch, lambda *a, **k: _Resolution([_Citation("c", "t")]))
        setattr(ev._shared(), ev._DEPTH_ATTR, 1)

        def _worker():
            ev.reset_run_state()  # a fresh run, as a pool worker thread has
            ev.resolve_evidence("q", config=_config())

        thread = threading.Thread(target=_worker)
        thread.start()
        thread.join(10)

        assert ev.interlock_fires() == 1
        assert ev.run_stats()["reentrant"] == 1, "the caller must see the fire"

    def test_interlock_is_visible_from_another_thread(self, monkeypatch):
        """The whole point. Cortex's fan-out submits backends onto a shared
        ThreadPoolExecutor, so the `dic` rung's call back into DIC search
        arrives on a DIFFERENT thread. A thread-local guard would not see it.
        """
        _patch_resolve(monkeypatch, lambda *a, **k: _Resolution([_Citation("c", "t")]))
        setattr(ev._shared(), ev._DEPTH_ATTR, 1)

        seen = {}

        def _worker():
            seen["depth"] = ev.resolve_depth()
            seen["bundle"] = ev.resolve_evidence("q", config=_config())

        thread = threading.Thread(target=_worker)
        thread.start()
        thread.join(10)

        assert seen["depth"] == 1, "the interlock must be process-wide, not thread-local"
        assert seen["bundle"] is None

    def test_depth_is_raised_during_the_call_and_released_after(self, monkeypatch):
        observed = {}

        def _resolve(*a, **k):
            observed["during"] = ev.resolve_depth()
            return _Resolution([_Citation("c", "t")])

        _patch_resolve(monkeypatch, _resolve)

        ev.resolve_evidence("q", config=_config())

        assert observed["during"] == 1
        assert ev.resolve_depth() == 0

    def test_depth_is_released_when_resolve_raises(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("governance refused")

        _patch_resolve(monkeypatch, _boom)

        ev.resolve_evidence("q", config=_config())

        assert ev.resolve_depth() == 0, "a refusal must not leave the seam wedged shut"

    def test_a_recursive_resolve_terminates_at_depth_one(self, monkeypatch):
        """End to end: resolve fans out, the `dic` rung asks the seam again on
        another thread, and the recursion stops there instead of running away
        inside the bounded pool.
        """
        depths = []

        def _resolve(*a, **k):
            depths.append(ev.resolve_depth())
            box = {}

            def _rung():  # what search_service._run_backends does, in miniature
                box["inner"] = ev.resolve_evidence("q", config=_config())

            t = threading.Thread(target=_rung)
            t.start()
            t.join(10)
            assert box["inner"] is None
            return _Resolution([_Citation("c", "t")])

        _patch_resolve(monkeypatch, _resolve)

        bundle = ev.resolve_evidence("q", config=_config())

        assert depths == [1]
        assert bundle is not None and not bundle.is_empty


# --------------------------------------------------------------------------- #
# Every decline is reported, never silent
# --------------------------------------------------------------------------- #
class TestDeclines:
    def test_collection_scoped_ask_declines_and_is_counted(self, monkeypatch):
        calls = []
        _patch_resolve(monkeypatch, lambda *a, **k: calls.append(a))

        bundle = ev.resolve_evidence("q", collection_id="col-1", config=_config())

        assert bundle is None
        assert calls == [], "a scoped ask must not reach cortex at all"
        assert ev.run_stats()["declined_collection_scoped"] == 1

    def test_scoped_ask_is_honoured_when_the_flag_is_set(self, monkeypatch):
        _patch_resolve(monkeypatch, lambda *a, **k: _Resolution([_Citation("c", "t")]))

        bundle = ev.resolve_evidence(
            "q", collection_id="col-1", config=_config(honour_collection_scope=True)
        )

        assert bundle is not None and not bundle.is_empty

    def test_spent_budget_declines_and_is_counted(self, monkeypatch):
        _patch_resolve(monkeypatch, lambda *a, **k: _Resolution([_Citation("c", "t")]))
        cfg = _config(max_resolves_per_run=1)

        assert ev.resolve_evidence("first", config=cfg) is not None
        assert ev.resolve_evidence("second", config=cfg) is None
        assert ev.run_stats()["capped"] == 1
        assert ev.run_stats()["resolutions"] == 1

    def test_repeat_query_is_memoised_and_does_not_spend_budget(self, monkeypatch):
        calls = []

        def _resolve(*a, **k):
            calls.append(a)
            return _Resolution([_Citation("c", "t")])

        _patch_resolve(monkeypatch, _resolve)
        cfg = _config()

        ev.resolve_evidence("same", config=cfg)
        ev.resolve_evidence("SAME", config=cfg)

        assert len(calls) == 1
        assert ev.run_stats()["resolutions"] == 1

    def test_governance_refusal_returns_a_blocked_bundle_not_an_exception(self, monkeypatch):
        class _Blocked(Exception):
            reason = "citation_hallucinated"

        def _boom(*a, **k):
            raise _Blocked("blocked")

        _patch_resolve(monkeypatch, _boom)

        bundle = ev.resolve_evidence("q", config=_config())

        assert bundle is not None
        assert bundle.blocked == "citation_hallucinated"
        assert bundle.is_empty, "a refusal must fall through to the legacy path"

    def test_backend_errors_are_carried_not_merged_into_emptiness(self, monkeypatch):
        _patch_resolve(monkeypatch, lambda *a, **k: _Resolution(
            [], errors=[{"backend": "rag", "stage": "timeout", "message": "10.0s"}],
        ))

        bundle = ev.resolve_evidence("q", config=_config())

        assert bundle.is_empty
        assert bundle.errors and bundle.errors[0]["backend"] == "rag"


# --------------------------------------------------------------------------- #
# The engine — the criteria that must survive the migration
# --------------------------------------------------------------------------- #
class _Seam:
    """Stand-in for the search_evidence module, as the engine resolves it."""

    def __init__(self, bundle, empty_fallback=True):
        self._bundle = bundle
        self._empty_fallback = empty_fallback
        self.asked = []

    def resolve_evidence(self, query, **kwargs):
        self.asked.append((query, kwargs))
        return self._bundle

    def fallback_on_empty(self, config=None):
        return self._empty_fallback


class _Bundle:
    def __init__(self, candidates):
        self.candidates = candidates
        self.citations = []
        self.backends = ["rag"]
        self.errors = []
        self.blocked = ""

    @property
    def is_empty(self):
        return not self.candidates


class _Cand:
    def __init__(self, chunk_id, content, score=1.0):
        self.chunk_id = chunk_id
        self.content = content
        self.source_id = chunk_id
        self.score = score
        self.final_score = score
        self.classification = "CUI"


class _Conn:
    """Minimal connection double for _chunk_meta / _doc_meta.

    ``docs`` maps doc_id -> classification, which is the ONLY thing the
    clearance drop reads.
    """

    def __init__(self, docs):
        self._docs = docs
        self._pending = None

    def execute(self, sql, params=()):
        self._pending = (sql, params)
        return self

    def fetchone(self):
        sql, params = self._pending
        if "FROM rag_chunks" in sql:
            return None
        if "FROM dic_chunk_links" in sql:
            return (1, "S1", str(params[0]), "")
        if "FROM dic_documents" in sql:
            doc_id = str(params[0])
            if doc_id in self._docs:
                return (doc_id.upper(), self._docs[doc_id], "")
        return None

    def close(self):
        pass


class TestEngineIntegration:
    def _patch_engine(self, monkeypatch, seam, docs):
        monkeypatch.setattr(
            se.DICSearchEngine, "_search_evidence_module",
            staticmethod(lambda: seam),
        )
        storage = importlib.import_module("tools.db.storage")
        monkeypatch.setattr(storage, "get_connection", lambda *a, **k: _Conn(docs))

    def test_clearance_drop_still_runs_before_the_top_k_cap(self, monkeypatch):
        """The acceptance criterion, on the GOVERNED path.

        Six governed candidates come back, the three highest-ranked of which are
        above the caller's clearance. With ``top_k=3`` the cap must fill with the
        three ACCESSIBLE ones — if the cap ran first the caller would get zero.
        """
        candidates = [_Cand("hi-%d" % i, "secret text %d" % i, 1.0 - i * 0.01) for i in range(3)]
        candidates += [_Cand("ok-%d" % i, "open text %d" % i, 0.5 - i * 0.01) for i in range(3)]
        docs = {"hi-%d" % i: "SECRET" for i in range(3)}
        docs.update({"ok-%d" % i: "CUI" for i in range(3)})
        self._patch_engine(monkeypatch, _Seam(_Bundle(candidates)), docs)

        results = se.DICSearchEngine().search("q", top_k=3, clearance="CUI")

        assert len(results) == 3
        assert all(r.doc_id.startswith("ok-") for r in results)

    def test_a_rungs_own_marking_tightens_the_drop_it_never_loosens_it(self, monkeypatch):
        """A candidate from a non-DIC rung has no `dic_documents` row, so
        `_doc_meta` answers its default "CUI". Taking that default would hand the
        caller a marking the source never claimed -- observed live, where a `kb`
        entry surfaced as a DIC result. The effective marking is the MORE
        RESTRICTIVE of the two, so it can only tighten.
        """
        restricted = _Cand("kb-1", "kb text")
        restricted.classification = "SECRET"
        plain = _Cand("ok-1", "open text", 0.5)
        self._patch_engine(monkeypatch, _Seam(_Bundle([restricted, plain])), {"ok-1": "CUI"})

        results = se.DICSearchEngine().search("q", top_k=5, clearance="CUI")

        assert [r.doc_id for r in results] == ["ok-1"]

    def test_a_rung_reporting_nothing_leaves_the_marking_alone(self, monkeypatch):
        """The legacy path must be untouched: SearchResult.classification
        defaults to "CUI", the same rank as _doc_meta's default.
        """
        cand = _Cand("ok-1", "open text")
        cand.classification = "CUI"
        self._patch_engine(monkeypatch, _Seam(_Bundle([cand])), {"ok-1": "CUI"})

        results = se.DICSearchEngine().search("q", top_k=5, clearance="CUI")

        assert [r.doc_id for r in results] == ["ok-1"]
        assert results[0].citation.classification == "CUI"

    def test_clearance_is_threaded_into_the_seam(self, monkeypatch):
        seam = _Seam(_Bundle([_Cand("ok-1", "open text")]))
        self._patch_engine(monkeypatch, seam, {"ok-1": "CUI"})

        se.DICSearchEngine(tenant_id="t7").search("q", top_k=5, clearance="CUI")

        assert seam.asked, "the seam must be consulted"
        _query, kwargs = seam.asked[0]
        assert kwargs["clearance"] == "CUI"
        assert kwargs["tenant_id"] == "t7"

    def test_seam_declining_falls_through_to_the_direct_retriever(self, monkeypatch):
        seam = _Seam(None)
        self._patch_engine(monkeypatch, seam, {})
        retriever_mod = importlib.import_module("tools.rag.retriever")
        seen = {}

        class _R:
            def __init__(self, *a, **k):
                pass

            def search(self, query, **kwargs):
                seen["query"] = query
                return []

        monkeypatch.setattr(retriever_mod, "RAGRetriever", _R)

        se.DICSearchEngine().search("fall through", top_k=3)

        assert seen["query"] == "fall through"

    def test_bm25_fallback_is_still_the_floor_under_the_governed_path(self, monkeypatch):
        """Seam declines, retriever dies — BM25 must still run (air-gap)."""
        self._patch_engine(monkeypatch, _Seam(None), {})
        retriever_mod = importlib.import_module("tools.rag.retriever")

        class _Dead:
            def __init__(self, *a, **k):
                raise RuntimeError("no vector store")

        monkeypatch.setattr(retriever_mod, "RAGRetriever", _Dead)
        called = {}

        def _bm25(self, query, top_k):
            called["query"] = query
            return []

        monkeypatch.setattr(se.DICSearchEngine, "_bm25_fallback", _bm25)

        se.DICSearchEngine().search("air gap", top_k=3)

        assert called["query"] == "air gap"

    def test_empty_governed_bundle_falls_back_when_the_flag_is_on(self, monkeypatch):
        seam = _Seam(_Bundle([]), empty_fallback=True)
        self._patch_engine(monkeypatch, seam, {})
        retriever_mod = importlib.import_module("tools.rag.retriever")
        seen = {}

        class _R:
            def __init__(self, *a, **k):
                pass

            def search(self, query, **kwargs):
                seen["query"] = query
                return []

        monkeypatch.setattr(retriever_mod, "RAGRetriever", _R)

        se.DICSearchEngine().search("q", top_k=3)

        assert seen["query"] == "q"

    def test_empty_governed_bundle_is_final_when_the_flag_is_off(self, monkeypatch):
        seam = _Seam(_Bundle([]), empty_fallback=False)
        self._patch_engine(monkeypatch, seam, {})
        retriever_mod = importlib.import_module("tools.rag.retriever")
        seen = {}

        class _R:
            def __init__(self, *a, **k):
                pass

            def search(self, query, **kwargs):
                seen["query"] = query
                return []

        monkeypatch.setattr(retriever_mod, "RAGRetriever", _R)

        assert se.DICSearchEngine().search("q", top_k=3) == []
        assert "query" not in seen

    def test_a_seam_that_raises_cannot_fail_a_search(self, monkeypatch):
        class _Angry:
            def resolve_evidence(self, *a, **k):
                raise RuntimeError("seam exploded")

            def fallback_on_empty(self, config=None):
                return True

        self._patch_engine(monkeypatch, _Angry(), {})
        retriever_mod = importlib.import_module("tools.rag.retriever")
        seen = {}

        class _R:
            def __init__(self, *a, **k):
                pass

            def search(self, query, **kwargs):
                seen["query"] = query
                return []

        monkeypatch.setattr(retriever_mod, "RAGRetriever", _R)

        assert se.DICSearchEngine().search("q", top_k=3) == []
        assert seen["query"] == "q"


# --------------------------------------------------------------------------- #
# The wiki cache is gone
# --------------------------------------------------------------------------- #
class TestWikiCacheRemoved:
    @pytest.mark.parametrize(
        "symbol",
        ["_check_wiki_cache", "_file_qa_to_wiki", "_wiki_keyword_search", "_qa_slug",
         "_QA_WIKI_CONFIDENCE_THRESHOLD", "_QA_WIKI_SLUG_PREFIX",
         "_QA_WIKI_SEARCH_SCORE_FLOOR"],
    )
    def test_symbol_is_gone_from_both_trees(self, symbol):
        icdev_se = importlib.import_module("icdev.tools.document_intelligence.search_engine")
        assert not hasattr(se, symbol)
        assert not hasattr(icdev_se, symbol)

    def test_answer_never_reads_the_auto_memory_directory(self, monkeypatch):
        """The cache sat in front of a mandatory chokepoint. It must not be
        reachable from ``answer()`` by any route, including a lazy import.
        """
        memory_path = importlib.import_module("tools.memory.claude_memory_path")

        def _forbidden(*a, **k):
            raise AssertionError("answer() reached the auto-memory directory")

        monkeypatch.setattr(memory_path, "claude_memory_dir", _forbidden)
        monkeypatch.setattr(
            se.DICSearchEngine, "search", lambda self, *a, **k: [],
        )

        result = se.DICSearchEngine().answer("anything")

        assert result.grounded is False
        assert result.refusal_reason == "no_evidence"

    def test_answer_accepts_a_clearance(self, monkeypatch):
        seen = {}

        def _search(self, query, **kwargs):
            seen.update(kwargs)
            return []

        monkeypatch.setattr(se.DICSearchEngine, "search", _search)

        se.DICSearchEngine().answer("q", clearance="CUI")

        assert seen["clearance"] == "CUI"
