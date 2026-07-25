#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for the RAG retrieval-quality baseline harness (rce-eval-01).

Uses injected fixture retrievers — never touches the live corpus or DB, so the
suite is deterministic and runs in a fresh (empty-DB) worktree.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.rag.rag_benchmark import (  # noqa: E402
    DEFAULT_GOLDEN_SET,
    RAGBenchmark,
    compare_to_baseline,
    load_golden_set,
    score_query,
)
from tools.rag.vector_store_provider import SearchResult  # noqa: E402


def _res(chunk_id: str, content: str, source_id: str = "") -> SearchResult:
    return SearchResult(chunk_id=chunk_id, content=content, source_id=source_id)


# ---------------------------------------------------------------------------
# score_query
# ---------------------------------------------------------------------------


class TestScoreQuery:
    def test_substring_hit_full_recall(self) -> None:
        results = [
            _res("c1", "NIST AC-2 covers account management controls."),
            _res("c2", "Unrelated boundary protection text."),
        ]
        expect = {"substrings": ["account management", "AC-2"]}
        s = score_query(results, expect, top_k=5)
        assert s["recall_at_k"] == 1.0  # both substrings present in c1
        assert s["targets"] == 2
        assert s["targets_hit"] == 2
        assert s["hit"] == 1
        assert s["mrr"] == 1.0  # first result matched

    def test_partial_recall(self) -> None:
        results = [_res("c1", "Discusses AC-2 only, not the phrase.")]
        expect = {"substrings": ["AC-2", "account management"]}
        s = score_query(results, expect, top_k=5)
        assert s["targets"] == 2
        assert s["targets_hit"] == 1
        assert s["recall_at_k"] == 0.5
        assert s["hit"] == 1

    def test_no_hit_zero_metrics(self) -> None:
        results = [_res("c1", "Totally unrelated content.")]
        expect = {"substrings": ["AC-2"]}
        s = score_query(results, expect, top_k=5)
        assert s["recall_at_k"] == 0.0
        assert s["mrr"] == 0.0
        assert s["ndcg_at_k"] == 0.0
        assert s["hit"] == 0

    def test_mrr_reflects_rank_of_first_match(self) -> None:
        results = [
            _res("c1", "no match here"),
            _res("c2", "no match either"),
            _res("c3", "here is AC-2"),
        ]
        expect = {"substrings": ["AC-2"]}
        s = score_query(results, expect, top_k=5)
        assert s["mrr"] == pytest.approx(1.0 / 3.0, abs=1e-4)

    def test_top_k_cutoff_excludes_later_hits(self) -> None:
        results = [
            _res("c1", "no match"),
            _res("c2", "no match"),
            _res("c3", "AC-2 appears here but beyond k=2"),
        ]
        expect = {"substrings": ["AC-2"]}
        s = score_query(results, expect, top_k=2)
        assert s["hit"] == 0
        assert s["recall_at_k"] == 0.0

    def test_chunk_id_and_source_id_targets(self) -> None:
        results = [_res("chunk-42", "irrelevant text", source_id="src-9")]
        expect = {"chunk_ids": ["chunk-42"], "source_ids": ["src-9"]}
        s = score_query(results, expect, top_k=5)
        assert s["targets"] == 2
        assert s["targets_hit"] == 2
        assert s["recall_at_k"] == 1.0


# ---------------------------------------------------------------------------
# RAGBenchmark.run with an injected search_fn
# ---------------------------------------------------------------------------


class TestRAGBenchmarkRun:
    def _golden(self) -> dict:
        return {
            "version": 1,
            "description": "test set",
            "top_k": 3,
            "queries": [
                {"id": "q1", "query": "AC-2?", "expect": {"substrings": ["AC-2"]}},
                {"id": "q2", "query": "AU-2?", "expect": {"substrings": ["AU-2"]}},
                {"id": "q3", "query": "empty targets", "expect": {}},  # skipped
            ],
        }

    def test_all_hits(self) -> None:
        def search_fn(query, k):
            marker = query.replace("?", "")
            return [_res("c1", f"content mentions {marker} control")]

        bench = RAGBenchmark(golden_set=self._golden())
        out = bench.run(search_fn=search_fn)
        assert out["queries_scored"] == 2  # q3 skipped (no targets)
        assert out["aggregate"]["citation_hit_rate"] == 1.0
        assert out["aggregate"]["recall_at_3"] == 1.0
        assert out["top_k"] == 3

    def test_mixed_hits(self) -> None:
        def search_fn(query, k):
            if "AC-2" in query:
                return [_res("c1", "AC-2 account management")]
            return [_res("c2", "irrelevant")]

        bench = RAGBenchmark(golden_set=self._golden())
        out = bench.run(search_fn=search_fn)
        assert out["queries_scored"] == 2
        assert out["aggregate"]["citation_hit_rate"] == 0.5

    def test_empty_corpus_yields_zeroed_baseline(self) -> None:
        bench = RAGBenchmark(golden_set=self._golden())
        out = bench.run(search_fn=lambda q, k: [])
        assert out["queries_scored"] == 2
        assert out["aggregate"]["citation_hit_rate"] == 0.0
        assert out["aggregate"]["recall_at_3"] == 0.0

    def test_broken_retriever_does_not_abort(self) -> None:
        def search_fn(query, k):
            raise RuntimeError("backend down")

        bench = RAGBenchmark(golden_set=self._golden())
        out = bench.run(search_fn=search_fn)
        # Both queries error out; scored=0, aggregate citation_hit_rate is None.
        assert out["queries_scored"] == 0
        assert any("error" in r for r in out["results"])

    def test_retriever_object_interface(self) -> None:
        class FakeRetriever:
            def search(self, query, top_k):
                return [_res("c1", "AC-2 and AU-2 both here")]

        bench = RAGBenchmark(golden_set=self._golden())
        out = bench.run(retriever=FakeRetriever())
        assert out["aggregate"]["citation_hit_rate"] == 1.0


# ---------------------------------------------------------------------------
# compare_to_baseline
# ---------------------------------------------------------------------------


class TestCompareBaseline:
    def test_deltas(self, tmp_path) -> None:
        baseline = {
            "generated_at": "2026-01-01T00:00:00+00:00",
            "aggregate": {"mrr": 0.5, "recall_at_3": 0.4, "citation_hit_rate": 0.6},
        }
        bpath = tmp_path / "baseline.json"
        bpath.write_text(json.dumps(baseline), encoding="utf-8")
        current = {
            "generated_at": "2026-02-01T00:00:00+00:00",
            "aggregate": {"mrr": 0.7, "recall_at_3": 0.4, "citation_hit_rate": 0.5},
        }
        cmp = compare_to_baseline(current, bpath)
        assert cmp["deltas"]["mrr"]["delta"] == 0.2
        assert cmp["deltas"]["recall_at_3"]["delta"] == 0.0
        assert cmp["deltas"]["citation_hit_rate"]["delta"] == -0.1

    def test_missing_baseline(self, tmp_path) -> None:
        out = compare_to_baseline({"aggregate": {}}, tmp_path / "nope.json")
        assert "error" in out


# ---------------------------------------------------------------------------
# Shipped golden query set integrity
# ---------------------------------------------------------------------------


class TestGoldenSetIntegrity:
    def test_default_golden_set_loads(self) -> None:
        data = load_golden_set(DEFAULT_GOLDEN_SET)
        queries = data["queries"]
        assert len(queries) >= 30  # ~30-50 per spec
        ids = [q["id"] for q in queries]
        assert len(ids) == len(set(ids)), "query ids must be unique"
        for q in queries:
            assert q.get("query"), f"{q.get('id')} missing query text"
            expect = q.get("expect", {})
            targets = (
                (expect.get("substrings") or [])
                + (expect.get("chunk_ids") or [])
                + (expect.get("source_ids") or [])
            )
            assert targets, f"{q['id']} has no expected targets"

    def test_default_golden_set_runs_against_fixture(self) -> None:
        # Every query's first substring is echoed back -> full recall path works.
        data = load_golden_set(DEFAULT_GOLDEN_SET)

        def search_fn(query, k):
            # Find the matching query to echo its expected substrings.
            for q in data["queries"]:
                if q["query"] == query:
                    subs = q["expect"].get("substrings", [])
                    return [_res("c1", " ".join(subs))]
            return []

        bench = RAGBenchmark(golden_set=data)
        out = bench.run(search_fn=search_fn)
        assert out["aggregate"]["citation_hit_rate"] == 1.0
