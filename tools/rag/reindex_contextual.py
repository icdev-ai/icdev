#!/usr/bin/env python3
# CUI // SP-CTI
"""Re-index existing RAG chunks with contextual prefixes + measure vs baseline (rce-ctx-02).

Follow-on to rce-ctx-01. Lets an existing corpus adopt contextual-retrieval
prefixes without re-ingesting from source: it walks stored chunks, reconstructs
each source "document" by grouping its chunks, generates a context prefix per
chunk, recomputes the embedding on the contextualized text, and updates the
store in place. The stored / cited ``content`` (and its ``content_hash``) stay
original, so citations and dedup are unaffected.

Design invariants:
  * Resumable / idempotent — chunks that already carry a ``context_prefix`` in
    metadata are skipped unless ``--force``. Re-running is safe. ``--limit`` /
    ``--offset`` bound a single run to one window of a stably-ordered chunk set,
    so a large corpus can be re-indexed across many short runs. Caveat: a window
    reconstructs document text from the chunks it loaded, so the one document a
    window may cut is contextualized against its partial text.
  * Retention-aware — cold-tier chunks (embedding-stripped) are skipped.
  * Testable without a live corpus — the reindexer takes an injected chunk
    iterable, embedding provider, and store-update callable; the live DB path is
    guarded behind ``--execute`` and only touched then.
  * Air-gap safe — no LLM/embedding provider ⇒ graceful no-op, never crashes.

Usage:
    # Preview (no writes): how many chunks would be re-indexed
    python tools/rag/reindex_contextual.py --reindex --source compliance_artifacts --dry-run --json

    # Execute live re-index (requires the corpus + embedding provider)
    python tools/rag/reindex_contextual.py --reindex --source compliance_artifacts --execute --json

    # Partial / resumable run: one 500-chunk window at a time. The response
    # carries next_offset + has_more, so a driver loops until has_more is false.
    python tools/rag/reindex_contextual.py --reindex --source compliance_reference \
        --limit 500 --offset 0 --execute --json

    # Measure retrieval quality vs the saved baseline
    python tools/rag/reindex_contextual.py --benchmark --baseline data/rag/rce_baseline.json --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent

from tools.rag import contextual_retrieval as cr  # noqa: E402
from tools.rag.vector_store_provider import VectorChunk  # noqa: E402


# ---------------------------------------------------------------------------
# Core reindexer (pure logic — fully injectable, no live DB required)
# ---------------------------------------------------------------------------
class ContextualReindexer:
    """Re-index chunks with contextual prefixes.

    Args:
        provider: embedding provider exposing ``.embed(text) -> list[float]``.
            Lazily resolved via the provider abstraction when omitted.
        config: contextual-retrieval config (loaded from rag_config when omitted).
        contextualizer: callable ``(chunk, document_text, config) -> bool`` that
            applies a prefix in place. Defaults to
            :func:`contextual_retrieval.contextualize_chunk` (injectable for tests).
    """

    def __init__(
        self,
        provider: Any = None,
        config: Optional[dict] = None,
        contextualizer: Optional[Callable[[VectorChunk, str, dict], bool]] = None,
    ):
        self._provider = provider
        self._provider_resolved = provider is not None
        self._config = config if config is not None else cr._load_config()
        self._contextualizer = contextualizer or cr.contextualize_chunk

    def _get_provider(self):
        if not self._provider_resolved:
            try:
                from tools.llm import get_embedding_provider

                self._provider = get_embedding_provider()
            except Exception:
                self._provider = None
            self._provider_resolved = True
        return self._provider

    @staticmethod
    def group_documents(chunks: Iterable[VectorChunk]) -> Dict[tuple, str]:
        """Reconstruct each source document by joining its chunks in order.

        Groups by ``(source_type, source_id)`` and concatenates chunk ``content``
        by ``chunk_index`` — an approximation of the original document that is
        exactly what contextual prefixing needs (situate a chunk within its doc).
        """
        groups: Dict[tuple, List[VectorChunk]] = {}
        for c in chunks:
            groups.setdefault((c.source_type, c.source_id), []).append(c)
        docs: Dict[tuple, str] = {}
        for key, members in groups.items():
            ordered = sorted(members, key=lambda c: c.chunk_index)
            docs[key] = "\n".join(m.content for m in ordered if m.content)
        return docs

    def reindex(
        self,
        chunks: Iterable[VectorChunk],
        store_update: Optional[Callable[[VectorChunk], None]] = None,
        dry_run: bool = False,
        force: bool = False,
    ) -> Dict[str, Any]:
        """Re-index ``chunks`` with contextual prefixes.

        Args:
            chunks: stored VectorChunks (with content + metadata) to re-index.
            store_update: callable persisting an updated chunk (embedding +
                metadata). Required when ``dry_run`` is False.
            dry_run: plan only — generate prefixes but never embed or persist.
            force: re-index even chunks that already carry a context_prefix.

        Returns:
            Stats dict (planned / reindexed / skipped_* / embedded / errors).
        """
        chunk_list = list(chunks)
        docs = self.group_documents(chunk_list)

        planned = reindexed = embedded = 0
        skipped_existing = skipped_cold = skipped_no_prefix = 0
        errors: List[str] = []

        provider = None if dry_run else self._get_provider()

        for chunk in chunk_list:
            # Retention-aware: cold-tier chunks have no embedding to refresh.
            if getattr(chunk, "tier", "hot") == "cold":
                skipped_cold += 1
                continue
            # Resumable / idempotent: skip already-contextualized chunks.
            existing_prefix = (chunk.metadata or {}).get("context_prefix")
            if existing_prefix and not force:
                skipped_existing += 1
                continue

            document_text = docs.get((chunk.source_type, chunk.source_id), chunk.content)
            try:
                applied = self._contextualizer(chunk, document_text, self._config)
            except Exception as exc:  # never abort the whole run on one chunk
                errors.append(f"{chunk.chunk_id}: contextualize: {exc}")
                continue
            if not applied:
                skipped_no_prefix += 1
                continue

            planned += 1
            if dry_run:
                continue

            # Recompute embedding on the contextualized text, then persist.
            if provider is None:
                errors.append(f"{chunk.chunk_id}: no embedding provider")
                continue
            try:
                chunk.embedding = provider.embed(chunk.text_for_embedding())
                embedded += 1
            except Exception as exc:
                errors.append(f"{chunk.chunk_id}: embed: {exc}")
                continue
            if store_update is None:
                errors.append(f"{chunk.chunk_id}: no store_update callable")
                continue
            try:
                store_update(chunk)
                reindexed += 1
            except Exception as exc:
                errors.append(f"{chunk.chunk_id}: store_update: {exc}")

        return {
            "classification": "CUI // SP-CTI",
            "dry_run": dry_run,
            "force": force,
            "total_chunks": len(chunk_list),
            "documents": len(docs),
            "planned": planned,
            "reindexed": reindexed,
            "embedded": embedded,
            "skipped_existing": skipped_existing,
            "skipped_cold": skipped_cold,
            "skipped_no_prefix": skipped_no_prefix,
            "errors": errors,
        }


# ---------------------------------------------------------------------------
# Live store adapter (default sqlite/pg backend) — only used with --execute
# ---------------------------------------------------------------------------
class LiveChunkStore:
    """Reads/updates rag_chunks directly for the default (sqlite/pg) backend.

    Used only on the live re-index path (``--execute``). Non-default vector
    backends (chroma/faiss) are not supported here and should re-ingest instead.
    """

    def __init__(self, tenant_id: str = ""):
        self._tenant_id = tenant_id

    @staticmethod
    def build_chunk_query(
        source_type: str = "",
        source_id: str = "",
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> tuple:
        """Build the (sql, params) pair used to read chunks.

        The ``ORDER BY`` is what makes ``limit``/``offset`` resumable: without a
        stable order, PostgreSQL is free to return rows in any order and two
        successive windows could overlap or skip chunks. Ordering by
        ``(source_type, source_id, chunk_index, id)`` also keeps a document's
        chunks contiguous, so a window cuts at most one document.

        Raises:
            ValueError: on a non-positive ``limit`` or negative ``offset``.
        """
        if limit is not None and limit <= 0:
            raise ValueError(f"--limit must be a positive integer, got {limit}")
        if offset < 0:
            raise ValueError(f"--offset must be >= 0, got {offset}")

        sql = (
            "SELECT id, content, content_hash, embedding, source_type, source_id, "
            "source_table, chunk_index, total_chunks, metadata, tier, "
            "tenant_id, project_id, classification FROM rag_chunks WHERE embedding IS NOT NULL"
        )
        params: list = []
        if source_type:
            sql += " AND source_type = %s"
            params.append(source_type)
        if source_id:
            sql += " AND source_id = %s"
            params.append(source_id)
        sql += " ORDER BY source_type, source_id, chunk_index, id"
        if limit is not None:
            sql += " LIMIT %s"
            params.append(int(limit))
        if offset:
            sql += " OFFSET %s"
            params.append(int(offset))
        return sql, params

    def iter_chunks(
        self,
        source_type: str = "",
        source_id: str = "",
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[VectorChunk]:
        """Read stored chunks, optionally windowed by ``limit``/``offset``."""
        from tools.db.storage import get_connection
        from tools.rag.sqlite_vector_store import _blob_to_embedding

        sql, params = self.build_chunk_query(source_type, source_id, limit, offset)
        conn = get_connection()
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        chunks: List[VectorChunk] = []
        for r in rows:
            d = dict(r)
            try:
                meta = json.loads(d.get("metadata") or "{}")
            except Exception:
                meta = {}
            emb = d.get("embedding")
            # psycopg hands back BYTEA as a memoryview, sqlite3 as bytes — decode
            # both, or callers get an opaque buffer instead of a float list.
            try:
                if isinstance(emb, (bytes, bytearray, memoryview)):
                    embedding = _blob_to_embedding(bytes(emb))
                else:
                    embedding = emb
            except Exception:
                embedding = None
            chunks.append(VectorChunk(
                chunk_id=d.get("id", ""),
                content=d.get("content", ""),
                content_hash=d.get("content_hash", ""),
                embedding=embedding,
                source_type=d.get("source_type", ""),
                source_id=str(d.get("source_id", "")),
                source_table=d.get("source_table", ""),
                chunk_index=d.get("chunk_index", 0) or 0,
                total_chunks=d.get("total_chunks", 1) or 1,
                metadata=meta,
                tier=d.get("tier", "hot") or "hot",
                tenant_id=d.get("tenant_id", "") or "",
                project_id=d.get("project_id", "") or "",
                classification=d.get("classification", "CUI") or "CUI",
            ))
        return chunks

    def update_chunk(self, chunk: VectorChunk) -> None:
        """Persist the re-embedded chunk.

        On PostgreSQL the refreshed embedding MUST also land in ``embedding_vec``:
        that pgvector column — not the legacy ``embedding`` BYTEA blob — is what
        ``pg_vector_store`` ranks on (``ORDER BY embedding_vec <=> %s::vector``).
        Writing only the blob leaves search on the pre-re-index vectors, so a
        full re-index measures a delta of exactly zero (observed on rce-eval-04).
        """
        from tools.db.storage import get_connection, is_pg
        from tools.rag.sqlite_vector_store import _embedding_to_blob

        conn = get_connection()
        blob = _embedding_to_blob(chunk.embedding) if chunk.embedding is not None else None
        meta_json = json.dumps(chunk.metadata) if chunk.metadata else "{}"
        if is_pg(conn) and chunk.embedding is not None:
            from tools.rag.pg_vector_store import _embedding_to_pg_vector

            conn.execute(
                "UPDATE rag_chunks SET embedding = %s, embedding_vec = %s::vector, "
                "metadata = %s WHERE id = %s",
                (blob, _embedding_to_pg_vector(chunk.embedding), meta_json, chunk.chunk_id),
            )
        else:
            conn.execute(
                "UPDATE rag_chunks SET embedding = %s, metadata = %s WHERE id = %s",
                (blob, meta_json, chunk.chunk_id),
            )
        conn.commit()
        conn.close()


# ---------------------------------------------------------------------------
# Benchmark-vs-baseline wiring (rce-eval-01 harness)
# ---------------------------------------------------------------------------
def run_benchmark_compare(
    baseline_path: str,
    golden_set_path: Optional[str] = None,
    top_k: Optional[int] = None,
    search_fn: Optional[Callable[[str, int], List[Any]]] = None,
) -> Dict[str, Any]:
    """Run the golden-set benchmark and compare to a saved baseline.

    Thin convenience over ``tools/rag/rag_benchmark.py``. ``search_fn`` is
    injectable for tests; otherwise the real RAGRetriever is used (and returns a
    zeroed run if the corpus is unavailable). Returns the run result with a
    ``comparison`` block of per-metric deltas.
    """
    from tools.rag.rag_benchmark import RAGBenchmark, compare_to_baseline

    bench = RAGBenchmark(golden_set_path=golden_set_path, top_k=top_k)
    result = bench.run(search_fn=search_fn)
    result["comparison"] = compare_to_baseline(result, baseline_path)
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def annotate_window(stats: Dict[str, Any], limit: Optional[int], offset: int,
                    loaded: int) -> Dict[str, Any]:
    """Add resume bookkeeping for a windowed (``--limit``/``--offset``) run.

    ``has_more`` is a full-window heuristic (no extra COUNT query): a window that
    came back full may have more behind it, so keep going from ``next_offset``
    until a short window is returned.
    """
    stats["limit"] = limit
    stats["offset"] = offset
    stats["next_offset"] = offset + loaded
    stats["has_more"] = bool(limit is not None and loaded == limit)
    return stats


def _reindex_cli(args) -> Dict[str, Any]:
    reindexer = ContextualReindexer()
    if not cr.is_enabled(reindexer._config) and not args.dry_run:
        return {
            "error": "contextual retrieval is disabled (rag.contextual_retrieval.enabled=false); "
                     "enable it before a live --execute re-index, or use --dry-run.",
            "reindexed": 0,
        }

    limit, offset = args.limit, args.offset
    # Load chunks: live only when --execute is given (guards the live DB path).
    if args.execute:
        store = LiveChunkStore(tenant_id=args.tenant_id)
        chunks = store.iter_chunks(source_type=args.source or "", limit=limit, offset=offset)
        stats = reindexer.reindex(
            chunks, store_update=store.update_chunk, dry_run=False, force=args.force
        )
        return annotate_window(stats, limit, offset, len(chunks))
    # Dry-run without touching the store: still needs the chunk list to plan.
    store = LiveChunkStore(tenant_id=args.tenant_id)
    try:
        chunks = store.iter_chunks(source_type=args.source or "", limit=limit, offset=offset)
    except Exception as exc:
        return {"error": f"could not read chunks for dry-run: {exc}", "planned": 0,
                "note": "fresh worktrees have an empty rag_chunks table"}
    stats = reindexer.reindex(chunks, store_update=None, dry_run=True, force=args.force)
    return annotate_window(stats, limit, offset, len(chunks))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Contextual re-index + measure vs baseline (rce-ctx-02)."
    )
    parser.add_argument("--reindex", action="store_true", help="Re-index stored chunks with prefixes.")
    parser.add_argument("--source", help="Restrict to a single source_type.")
    parser.add_argument("--dry-run", action="store_true", help="Plan only — no embed, no writes.")
    parser.add_argument("--execute", action="store_true", help="Perform the LIVE re-index (writes to the store).")
    parser.add_argument("--force", action="store_true", help="Re-index even already-contextualized chunks.")
    parser.add_argument("--limit", type=int, help="Re-index at most N chunks (partial/resumable run).")
    parser.add_argument("--offset", type=int, default=0, help="Skip the first N chunks (resume point).")
    parser.add_argument("--tenant-id", default="", help="Tenant ID.")
    parser.add_argument("--benchmark", action="store_true", help="Run the golden-set benchmark vs baseline.")
    parser.add_argument("--baseline", default="data/rag/rce_baseline.json", help="Baseline JSON to compare against.")
    parser.add_argument("--golden-set", help="Override golden query set YAML path.")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output.")
    args = parser.parse_args()

    if args.limit is not None and args.limit <= 0:
        parser.error(f"--limit must be a positive integer, got {args.limit}")
    if args.offset < 0:
        parser.error(f"--offset must be >= 0, got {args.offset}")

    result: Dict[str, Any] = {}
    if args.reindex:
        result["reindex"] = _reindex_cli(args)
    if args.benchmark:
        result["benchmark"] = run_benchmark_compare(
            baseline_path=args.baseline, golden_set_path=args.golden_set
        )
    if not result:
        parser.print_help()
        return

    if args.json_output:
        print(json.dumps(result, indent=2))
    else:
        if "reindex" in result:
            r = result["reindex"]
            if "error" in r:
                print(f"Re-index: {r['error']}")
            else:
                print(f"Re-index ({'dry-run' if r['dry_run'] else 'live'}): "
                      f"{r['total_chunks']} chunks, {r['planned']} planned, "
                      f"{r['reindexed']} written, "
                      f"{r['skipped_existing']} already-contextualized skipped")
                if r.get("limit") is not None:
                    print(f"  window: offset {r['offset']}, limit {r['limit']} — "
                          f"{'resume with --offset ' + str(r['next_offset']) if r['has_more'] else 'end of corpus'}")
        if "benchmark" in result:
            comp = result["benchmark"].get("comparison", {})
            if "error" in comp:
                print(f"Benchmark: {comp['error']}")
            else:
                print("Benchmark vs baseline:")
                for k, d in comp.get("deltas", {}).items():
                    print(f"  {k:20s}: {d['baseline']} -> {d['current']} (delta {d['delta']})")


if __name__ == "__main__":
    sys.exit(main())
