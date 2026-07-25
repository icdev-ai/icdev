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

import pytest  # noqa: E402

from tools.rag.reindex_contextual import (  # noqa: E402
    ContextualReindexer,
    LiveChunkStore,
    annotate_window,
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
# --limit / --offset windowing (rce-eval-04)
# ---------------------------------------------------------------------------
class TestChunkQueryWindow:
    def test_unwindowed_query_has_stable_order_and_no_limit(self) -> None:
        sql, params = LiveChunkStore.build_chunk_query()
        assert "ORDER BY source_type, source_id, chunk_index, id" in sql
        assert "LIMIT" not in sql and "OFFSET" not in sql
        assert params == []

    def test_limit_and_offset_are_parameterized_after_order_by(self) -> None:
        sql, params = LiveChunkStore.build_chunk_query(
            source_type="compliance_reference", limit=10, offset=20
        )
        assert sql.index("ORDER BY") < sql.index("LIMIT") < sql.index("OFFSET")
        assert sql.endswith("LIMIT %s OFFSET %s")
        assert params == ["compliance_reference", 10, 20]

    def test_zero_offset_omits_offset_clause(self) -> None:
        sql, params = LiveChunkStore.build_chunk_query(limit=10, offset=0)
        assert "OFFSET" not in sql
        assert params == [10]

    @pytest.mark.parametrize("limit,offset", [(0, 0), (-1, 0), (5, -1)])
    def test_invalid_window_rejected(self, limit, offset) -> None:
        with pytest.raises(ValueError):
            LiveChunkStore.build_chunk_query(limit=limit, offset=offset)


class TestWindowedReindex:
    def _rx(self):
        return ContextualReindexer(
            provider=FakeProvider(), config=_ENABLED_CFG, contextualizer=_fake_ctx
        )

    def test_window_reindexes_only_its_slice(self) -> None:
        # Simulates what iter_chunks(limit=2, offset=1) hands back.
        corpus = [_chunk(f"c{i}", f"body {i}", sid=str(i)) for i in range(5)]
        writes = []
        stats = self._rx().reindex(corpus[1:3], store_update=writes.append)
        assert stats["total_chunks"] == 2
        assert stats["reindexed"] == 2
        assert [w.chunk_id for w in writes] == ["c1", "c2"]

    def test_successive_windows_cover_the_corpus_exactly_once(self) -> None:
        corpus = [_chunk(f"c{i}", f"body {i}", sid=str(i)) for i in range(5)]
        writes = []
        for offset in (0, 2, 4):
            self._rx().reindex(corpus[offset:offset + 2], store_update=writes.append)
        assert [w.chunk_id for w in writes] == ["c0", "c1", "c2", "c3", "c4"]


# ---------------------------------------------------------------------------
# update_chunk must refresh the column search actually ranks on (rce-eval-04-d3)
# ---------------------------------------------------------------------------
class FakeConn:
    def __init__(self, backend: str) -> None:
        self._backend = backend
        self.statements = []
        self.committed = False
        self.closed = False

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        return self

    def commit(self) -> None:
        self.committed = True

    def close(self) -> None:
        self.closed = True


class TestUpdateChunkVectorColumn:
    """pg_vector_store ranks on ``embedding_vec``; the blob alone is invisible."""

    def _run(self, monkeypatch, backend):
        import importlib

        storage = importlib.import_module("tools.db.storage")
        conn = FakeConn(backend)
        monkeypatch.setattr(storage, "get_connection", lambda *a, **k: conn)
        monkeypatch.setattr(storage, "is_pg", lambda c=None: backend == "postgresql")
        chunk = _chunk("c1", "body", meta={"context_prefix": "CTX"})
        chunk.embedding = [0.5, 0.25, 0.125]
        LiveChunkStore().update_chunk(chunk)
        assert conn.committed and conn.closed
        return conn.statements[0]

    def test_postgres_update_writes_embedding_vec(self, monkeypatch) -> None:
        sql, params = self._run(monkeypatch, "postgresql")
        assert "embedding_vec = %s::vector" in sql
        assert params[1] == "[0.500000,0.250000,0.125000]"

    def test_sqlite_update_leaves_vector_column_alone(self, monkeypatch) -> None:
        sql, _ = self._run(monkeypatch, "sqlite")
        assert "embedding_vec" not in sql


class TestIterChunksDecodesBlob:
    """psycopg returns BYTEA as memoryview; sqlite3 returns bytes."""

    def _rows(self, blob):
        return [{
            "id": "c1", "content": "body", "content_hash": "h", "embedding": blob,
            "source_type": "compliance_reference", "source_id": "nist53:ac-2",
            "source_table": "", "chunk_index": 0, "total_chunks": 1,
            "metadata": "{}", "tier": "hot", "tenant_id": "", "project_id": "",
            "classification": "CUI",
        }]

    def _iter(self, monkeypatch, blob):
        import importlib

        storage = importlib.import_module("tools.db.storage")
        rows = self._rows(blob)

        class Conn:
            def execute(self, sql, params=None):
                return self

            def fetchall(self):
                return rows

            def close(self):
                pass

        monkeypatch.setattr(storage, "get_connection", lambda *a, **k: Conn())
        return LiveChunkStore().iter_chunks(source_type="compliance_reference")

    @pytest.mark.parametrize("wrap", [bytes, memoryview])
    def test_blob_decoded_to_float_list(self, monkeypatch, wrap) -> None:
        from tools.rag.sqlite_vector_store import _embedding_to_blob

        blob = wrap(_embedding_to_blob([0.5, 0.25, 0.125]))
        chunks = self._iter(monkeypatch, blob)
        assert chunks[0].embedding == pytest.approx([0.5, 0.25, 0.125])


class TestAnnotateWindow:
    def test_full_window_reports_more_and_next_offset(self) -> None:
        stats = annotate_window({}, limit=10, offset=20, loaded=10)
        assert stats["has_more"] is True
        assert stats["next_offset"] == 30

    def test_short_window_is_end_of_corpus(self) -> None:
        stats = annotate_window({}, limit=10, offset=20, loaded=4)
        assert stats["has_more"] is False
        assert stats["next_offset"] == 24

    def test_no_limit_never_reports_more(self) -> None:
        stats = annotate_window({}, limit=None, offset=0, loaded=99)
        assert stats["has_more"] is False
        assert stats["limit"] is None


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
