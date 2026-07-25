#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for contextual re-index CLI + benchmark wiring (rce-ctx-02).

Fixture-driven — an injected fake embedding provider, fake store-update sink,
and in-memory chunk list are used, so no live corpus or DB is touched and the
suite runs in a fresh (empty-DB) worktree.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.rag.reindex_contextual import (  # noqa: E402
    ContextualReindexer,
    run_benchmark_compare,
)
from tools.rag.vector_store_provider import SearchResult, VectorChunk  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeProvider:
    def __init__(self) -> None:
        self.calls = []

    def embed(self, text: str):
        self.calls.append(text)
        # deterministic pseudo-embedding derived from length
        return [float(len(text) % 7), 1.0, 0.0]


def _chunk(cid, content, stype="compliance_artifacts", sid="1", idx=0, meta=None, tier="hot"):
    c = VectorChunk(
        chunk_id=cid, content=content, source_type=stype, source_id=sid,
        chunk_index=idx, metadata=meta or {}, tier=tier,
    )
    c.compute_content_hash()
    return c


# Contextualizer that always applies a fixed prefix (simulates enabled LLM/heuristic).
def _fake_ctx(chunk, document_text, config):
    prefix = f"CTX[{chunk.source_type}]"
    chunk.context_prefix = prefix
    chunk.embed_text = f"{prefix}\n\n{chunk.content}"
    chunk.metadata = dict(chunk.metadata or {})
    chunk.metadata["context_prefix"] = prefix
    chunk.metadata["context_provenance"] = {"method": "llm"}
    return True


_ENABLED_CFG = {"enabled": True}


# ---------------------------------------------------------------------------
# group_documents
# ---------------------------------------------------------------------------
class TestGroupDocuments:
    def test_groups_and_orders_by_chunk_index(self) -> None:
        chunks = [
            _chunk("c2", "second", sid="A", idx=1),
            _chunk("c1", "first", sid="A", idx=0),
            _chunk("c3", "other doc", sid="B", idx=0),
        ]
        docs = ContextualReindexer.group_documents(chunks)
        assert docs[("compliance_artifacts", "A")] == "first\nsecond"
        assert docs[("compliance_artifacts", "B")] == "other doc"


# ---------------------------------------------------------------------------
# dry-run
# ---------------------------------------------------------------------------
class TestDryRun:
    def test_dry_run_plans_without_embedding_or_writing(self) -> None:
        provider = FakeProvider()
        writes = []
        rx = ContextualReindexer(provider=provider, config=_ENABLED_CFG, contextualizer=_fake_ctx)
        chunks = [_chunk("c1", "body one"), _chunk("c2", "body two", sid="2")]
        stats = rx.reindex(chunks, store_update=writes.append, dry_run=True)
        assert stats["dry_run"] is True
        assert stats["planned"] == 2
        assert stats["reindexed"] == 0
        assert stats["embedded"] == 0
        assert provider.calls == []   # never embedded
        assert writes == []           # never wrote


# ---------------------------------------------------------------------------
# live re-index via injected fake store
# ---------------------------------------------------------------------------
class TestReindexExecute:
    def test_updates_embed_text_and_embedding(self) -> None:
        provider = FakeProvider()
        writes = []
        rx = ContextualReindexer(provider=provider, config=_ENABLED_CFG, contextualizer=_fake_ctx)
        chunks = [_chunk("c1", "body one")]
        stats = rx.reindex(chunks, store_update=writes.append, dry_run=False)
        assert stats["reindexed"] == 1
        assert stats["embedded"] == 1
        # the embedded text was the contextualized text, not the raw content
        assert provider.calls == ["CTX[compliance_artifacts]\n\nbody one"]
        updated = writes[0]
        assert updated.embed_text == "CTX[compliance_artifacts]\n\nbody one"
        assert updated.embedding is not None
        # stored content + hash unchanged
        assert updated.content == "body one"
        assert updated.metadata["context_prefix"] == "CTX[compliance_artifacts]"

    def test_idempotent_skips_already_contextualized(self) -> None:
        provider = FakeProvider()
        writes = []
        rx = ContextualReindexer(provider=provider, config=_ENABLED_CFG, contextualizer=_fake_ctx)
        already = _chunk("c1", "done", meta={"context_prefix": "CTX[x]"})
        fresh = _chunk("c2", "todo", sid="2")
        stats = rx.reindex([already, fresh], store_update=writes.append)
        assert stats["skipped_existing"] == 1
        assert stats["reindexed"] == 1
        assert [w.chunk_id for w in writes] == ["c2"]

    def test_force_reindexes_existing(self) -> None:
        provider = FakeProvider()
        writes = []
        rx = ContextualReindexer(provider=provider, config=_ENABLED_CFG, contextualizer=_fake_ctx)
        already = _chunk("c1", "done", meta={"context_prefix": "OLD"})
        stats = rx.reindex([already], store_update=writes.append, force=True)
        assert stats["skipped_existing"] == 0
        assert stats["reindexed"] == 1

    def test_cold_tier_skipped(self) -> None:
        provider = FakeProvider()
        writes = []
        rx = ContextualReindexer(provider=provider, config=_ENABLED_CFG, contextualizer=_fake_ctx)
        cold = _chunk("c1", "archived", tier="cold")
        stats = rx.reindex([cold], store_update=writes.append)
        assert stats["skipped_cold"] == 1
        assert stats["reindexed"] == 0
        assert writes == []

    def test_no_provider_records_error_not_crash(self) -> None:
        # provider explicitly None and resolution yields None
        rx = ContextualReindexer(provider=None, config=_ENABLED_CFG, contextualizer=_fake_ctx)
        rx._provider_resolved = True  # simulate "resolved to unavailable"
        rx._provider = None
        writes = []
        stats = rx.reindex([_chunk("c1", "body")], store_update=writes.append)
        assert stats["reindexed"] == 0
        assert any("no embedding provider" in e for e in stats["errors"])


# ---------------------------------------------------------------------------
# benchmark-vs-baseline wiring
# ---------------------------------------------------------------------------
class TestBenchmarkCompare:
    def test_compare_returns_deltas(self, tmp_path) -> None:
        # Build a tiny baseline artifact.
        baseline = tmp_path / "baseline.json"
        baseline.write_text(
            '{"generated_at": "2026-01-01T00:00:00Z", '
            '"aggregate": {"recall_at_5": 0.5, "mrr": 0.4}}',
            encoding="utf-8",
        )
        # Minimal golden set so RAGBenchmark has queries to score.
        golden = tmp_path / "golden.yaml"
        golden.write_text(
            "version: t\ntop_k: 5\nqueries:\n"
            "  - id: q1\n    query: account management\n"
            "    expect:\n      substrings: [account management]\n",
            encoding="utf-8",
        )

        # Injected retriever that always returns a hitting chunk → current > baseline.
        def search_fn(query, k):
            return [SearchResult(chunk_id="c1", content="account management controls")]

        result = run_benchmark_compare(
            baseline_path=str(baseline),
            golden_set_path=str(golden),
            search_fn=search_fn,
        )
        comp = result["comparison"]
        assert "deltas" in comp
        assert comp["deltas"]["recall_at_5"]["baseline"] == 0.5
        assert comp["deltas"]["recall_at_5"]["current"] == 1.0
        assert comp["deltas"]["recall_at_5"]["delta"] == 0.5

    def test_missing_baseline_reports_error(self, tmp_path) -> None:
        golden = tmp_path / "golden.yaml"
        golden.write_text(
            "version: t\ntop_k: 5\nqueries:\n"
            "  - id: q1\n    query: foo\n    expect:\n      substrings: [foo]\n",
            encoding="utf-8",
        )
        result = run_benchmark_compare(
            baseline_path=str(tmp_path / "nope.json"),
            golden_set_path=str(golden),
            search_fn=lambda q, k: [],
        )
        assert "error" in result["comparison"]
