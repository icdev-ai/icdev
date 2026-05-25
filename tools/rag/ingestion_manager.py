#!/usr/bin/env python3
# CUI // SP-CTI
"""RAG ingestion manager — real-time + batch pipeline (D-RAG-9).

Reads from ICDEV™ source tables, chunks content, embeds via Ollama
nomic-embed-text (D-RAG-10), and stores in vector store with dedup.

Usage:
    python tools/rag/ingestion_manager.py --ingest --source innovation_signals --json
    python tools/rag/ingestion_manager.py --sweep --json
    python tools/rag/ingestion_manager.py --status --json
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from tools.db.storage import get_connection
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.rag.chunker import chunk_fields
from tools.rag.source_registry import (
    SOURCE_REGISTRY,
    get_source_config,
)
from tools.rag.vector_store_factory import VectorStoreFactory

BASE_DIR = Path(__file__).resolve().parent.parent.parent
ICDEV_DB = BASE_DIR / "data" / "icdev.db"
MEMORY_DB = BASE_DIR / "data" / "memory.db"

# RAGKG_HOOK_ENABLED=false disables KG enrichment without touching RAG pipeline.
RAGKG_HOOK_ENABLED = os.environ.get("RAGKG_HOOK_ENABLED", "true").lower() not in ("false", "0")


def _kg_enrich_chunks(embeddable: list) -> None:
    """Call KG bridge for each newly-inserted chunk. KG failures are silenced."""
    if not RAGKG_HOOK_ENABLED or not embeddable:
        return
    try:
        import tools.rag.rag_to_kg_ingester as _rki
    except ImportError:
        return
    kg_conn = get_connection()
    try:
        for chunk in embeddable:
            try:
                _rki.ingest_chunk(kg_conn, chunk.chunk_id)
            except Exception:
                pass
    finally:
        kg_conn.close()


def _get_db_path(db_name: str) -> Path:
    """Resolve database path from name."""
    if db_name == "memory":
        return MEMORY_DB
    # Canvas databases live as separate SQLite files in data/
    _CANVAS_DB_MAP = {
        "network_canvas": "network_canvas.db",
        "infra_canvas": "infra_canvas.db",
        "security_canvas": "security_canvas.db",
        "boundary_canvas": "boundary_canvas.db",
        "data_canvas": "data_canvas.db",
        "pipeline_canvas": "pipeline_canvas.db",
        "observability_canvas": "observability_canvas.db",
        "migration_canvas": "migration_canvas.db",
        "qdc_canvas": "qdc_canvas.db",
    }
    if db_name in _CANVAS_DB_MAP:
        return BASE_DIR / "data" / _CANVAS_DB_MAP[db_name]
    return ICDEV_DB


def _get_embedding_provider():
    """Get embedding provider via LLM router (D-RAG-10)."""
    try:
        from tools.llm import get_embedding_provider

        return get_embedding_provider()
    except Exception:
        return None


def _embed_chunks(chunks, provider) -> int:
    """Embed chunks using embedding provider. Returns count embedded."""
    if not provider or not chunks:
        return 0
    embedded = 0
    batch_size = 20
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        for chunk in batch:
            try:
                if hasattr(provider, "embed"):
                    chunk.embedding = provider.embed(chunk.content)
                else:
                    resp = provider.embeddings.create(input=chunk.content, model="nomic-embed-text")
                    chunk.embedding = resp.data[0].embedding
                embedded += 1
            except Exception:
                continue
    return embedded


def _log_ingestion(
    db_path: Path,
    source_type: str,
    source_id: str,
    source_table: str,
    chunks_created: int,
    chunks_skipped: int,
    content_hash: str = "",
    mode: str = "batch",
    tenant_id: str = "",
    project_id: str = "",
    agent_id: str = "",
    correlation_id: str = "",
):
    """Log ingestion event to rag_ingestion_log (append-only, D-RAG-11, D-RAG-18)."""
    conn = get_connection()
    conn.execute(
        """INSERT INTO rag_ingestion_log
           (source_type, source_id, source_table, chunks_created, chunks_skipped,
            content_hash, ingestion_mode, tenant_id, project_id, agent_id,
            correlation_id, classification)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'CUI')""",
        (
            source_type,
            source_id,
            source_table,
            chunks_created,
            chunks_skipped,
            content_hash,
            mode,
            tenant_id,
            project_id,
            agent_id,
            correlation_id,
        ),
    )
    conn.commit()
    conn.close()


def ingest_source(
    source_type: str,
    tenant_id: str = "",
    project_id: str = "",
    limit: int = 0,
    mode: str = "",
) -> Dict[str, Any]:
    """Ingest a single source type into the RAG vector store.

    Args:
        source_type: Source type key from SOURCE_REGISTRY.
        tenant_id: Optional tenant ID for multi-tenant.
        project_id: Optional project filter.
        limit: Max rows to process (0 = all).
        mode: Override ingestion mode.

    Returns:
        Dict with ingestion stats.
    """
    cfg = get_source_config(source_type)
    if not cfg:
        return {"error": f"Unknown source_type: {source_type}", "ingested": 0}

    db_path = _get_db_path(cfg["db"])
    # Only check file existence for canvas/separate SQLite DBs.
    # "icdev" and "memory" use get_connection() (Postgres) when the .db file is absent.
    _is_separate_db = cfg["db"] not in ("icdev", "memory")
    if _is_separate_db and not db_path.exists():
        return {"error": f"Database not found: {db_path}", "ingested": 0}

    # Get embedding provider
    provider = _get_embedding_provider()
    if not provider:
        return {"error": "No embedding provider available", "ingested": 0}

    # Get vector store
    store = VectorStoreFactory.create(tenant_id=tenant_id)

    # Query source table — validate identifiers against registry whitelist
    table = cfg["table"]
    pk = cfg["pk"]
    content_cols = cfg["content_cols"]
    metadata_cols = cfg.get("metadata_cols", [])
    all_cols = [pk] + content_cols + metadata_cols

    # SEC: Whitelist validation — only allow identifiers from SOURCE_REGISTRY
    import re

    _IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")
    for ident in [table, pk] + content_cols + metadata_cols:
        if not _IDENT_RE.match(ident):
            return {"error": f"Invalid identifier in source registry: {ident!r}", "ingested": 0}

    col_str = ", ".join(all_cols)

    # Canvas databases are separate SQLite files; main DB may be Postgres
    if cfg["db"] != "icdev" and db_path.suffix == ".db" and db_path.exists():
        conn = get_connection(str(db_path))
        _canvas_conn = True
    else:
        conn = get_connection()
        try:
            conn.execute("PRAGMA busy_timeout=5000")
        except Exception:
            pass  # Postgres doesn't support PRAGMA
        _canvas_conn = False
    sql = f"SELECT {col_str} FROM {table}"  # nosec B608 -- table/column names are internal constants, not user input
    conditions = []
    params: list = []

    # Apply source-specific filter — validate against safe patterns
    if "filter" in cfg:
        raw_filter = cfg["filter"]
        # SEC: Only allow simple column op value patterns in filters
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*\s*(=|!=|<|>|<=|>=|IN|LIKE|IS)\s*", raw_filter, re.IGNORECASE):
            return {"error": f"Unsafe filter pattern in source registry: {raw_filter!r}", "ingested": 0}
        conditions.append(f"({raw_filter})")

    if project_id and "project_id" in [c for c in all_cols]:
        conditions.append("project_id = ?")
        params.append(project_id)

    if conditions:
        sql += " WHERE " + " AND ".join(conditions)

    sql += f" ORDER BY {pk} DESC"
    if limit > 0:
        sql += " LIMIT ?"
        params.append(limit)

    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as e:
        conn.close()
        return {"error": f"Query failed: {e}", "ingested": 0}
    conn.close()

    total_chunks = 0
    total_skipped = 0
    total_embedded = 0
    ingestion_mode = mode or cfg.get("mode", "batch")

    for row in rows:
        row_dict = dict(row)
        row_id = str(row_dict[pk])

        # Build metadata
        meta = {}
        for mc in metadata_cols:
            val = row_dict.get(mc)
            if val is not None:
                meta[mc] = val

        # Chunk the content
        chunks = chunk_fields(
            fields=row_dict,
            field_names=content_cols,
            source_type=source_type,
            source_id=row_id,
            source_table=table,
            metadata=meta,
            tenant_id=tenant_id,
            project_id=project_id,
        )

        if not chunks:
            continue

        # Check dedup before embedding (save API calls)
        new_chunks = []
        for chunk in chunks:
            existing = store.get_by_content_hash(chunk.content_hash)
            if existing:
                total_skipped += 1
            else:
                new_chunks.append(chunk)

        if not new_chunks:
            continue

        # Embed new chunks
        embedded = _embed_chunks(new_chunks, provider)
        total_embedded += embedded

        # Filter to only chunks that got embeddings
        embeddable = [c for c in new_chunks if c.embedding is not None]
        if embeddable:
            inserted = store.upsert(embeddable)
            total_chunks += inserted

            _kg_enrich_chunks(embeddable)

            # Log ingestion
            _log_ingestion(
                db_path=ICDEV_DB,
                source_type=source_type,
                source_id=row_id,
                source_table=table,
                chunks_created=inserted,
                chunks_skipped=len(chunks) - inserted,
                mode=ingestion_mode,
                tenant_id=tenant_id,
                project_id=project_id,
            )

    return {
        "classification": "CUI // SP-CTI",
        "source_type": source_type,
        "table": table,
        "rows_processed": len(rows),
        "chunks_created": total_chunks,
        "chunks_skipped": total_skipped,
        "chunks_embedded": total_embedded,
        "mode": ingestion_mode,
    }


def sweep_all(
    tenant_id: str = "",
    project_id: str = "",
    sources: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Batch sweep all (or specified) source types.

    Args:
        tenant_id: Optional tenant ID.
        project_id: Optional project filter.
        sources: Optional list of source types to sweep. Defaults to all.

    Returns:
        Dict with aggregated stats.
    """
    source_list = sources or list(SOURCE_REGISTRY.keys())
    results = []
    total_chunks = 0
    total_skipped = 0
    errors = []

    for src in source_list:
        result = ingest_source(
            source_type=src,
            tenant_id=tenant_id,
            project_id=project_id,
        )
        results.append(result)
        if "error" in result:
            errors.append(result["error"])
        else:
            total_chunks += result.get("chunks_created", 0)
            total_skipped += result.get("chunks_skipped", 0)

    return {
        "classification": "CUI // SP-CTI",
        "sources_processed": len(source_list),
        "total_chunks_created": total_chunks,
        "total_chunks_skipped": total_skipped,
        "errors": errors,
        "per_source": results,
    }


def get_status(tenant_id: str = "") -> Dict[str, Any]:
    """Get ingestion status across all sources."""
    store = VectorStoreFactory.create(tenant_id=tenant_id)
    available = store.check_availability()

    # Count by source type
    source_counts = {}
    for src in SOURCE_REGISTRY:
        count = store.count(filters={"source_type": src})
        if count > 0:
            source_counts[src] = count

    # Count by tier
    tier_counts = {}
    for tier in ("hot", "warm", "cold"):
        count = store.count(filters={"tier": tier})
        if count > 0:
            tier_counts[tier] = count

    # Last ingestion from log
    last_ingestion = None
    if ICDEV_DB.exists():
        try:
            conn = get_connection()
            row = conn.execute("SELECT MAX(created_at) FROM rag_ingestion_log").fetchone()
            if row and row[0]:
                last_ingestion = row[0]
            conn.close()
        except Exception:
            pass

    return {
        "classification": "CUI // SP-CTI",
        "vector_store_available": available,
        "vector_store_backend": store.provider_name,
        "total_chunks": store.count(),
        "by_source_type": source_counts,
        "by_tier": tier_counts,
        "last_ingestion": last_ingestion,
        "registered_sources": len(SOURCE_REGISTRY),
    }


def get_realtime_sources() -> List[str]:
    """Return list of source types configured for real-time ingestion."""
    return [name for name, cfg in SOURCE_REGISTRY.items() if cfg.get("mode") == "realtime"]


def get_batch_sources() -> List[str]:
    """Return list of source types configured for batch ingestion."""
    return [name for name, cfg in SOURCE_REGISTRY.items() if cfg.get("mode") == "batch"]


def ingest_single_record(
    source_type: str,
    record: dict,
    tenant_id: str = "",
    project_id: str = "",
    correlation_id: str = "",
) -> Dict[str, Any]:
    """Ingest a single record (for real-time hook use).

    Args:
        source_type: Source type key.
        record: Dict with at least pk and content columns.
        tenant_id: Optional tenant ID.
        project_id: Optional project ID.
        correlation_id: Optional correlation ID from triggering tool call (D-RAG-18).

    Returns:
        Dict with ingestion result.
    """
    cfg = get_source_config(source_type)
    if not cfg:
        return {"error": f"Unknown source_type: {source_type}", "ingested": 0}

    provider = _get_embedding_provider()
    if not provider:
        return {"error": "No embedding provider available", "ingested": 0}

    store = VectorStoreFactory.create(tenant_id=tenant_id)
    pk = cfg["pk"]
    content_cols = cfg["content_cols"]
    metadata_cols = cfg.get("metadata_cols", [])

    row_id = str(record.get(pk, ""))
    meta = {mc: record.get(mc) for mc in metadata_cols if record.get(mc) is not None}

    chunks = chunk_fields(
        fields=record,
        field_names=content_cols,
        source_type=source_type,
        source_id=row_id,
        source_table=cfg["table"],
        metadata=meta,
        tenant_id=tenant_id,
        project_id=project_id,
    )

    if not chunks:
        return {"ingested": 0, "reason": "no_content"}

    # Dedup + embed
    new_chunks = [c for c in chunks if not store.get_by_content_hash(c.content_hash)]
    if not new_chunks:
        return {"ingested": 0, "skipped": len(chunks), "reason": "dedup"}

    _embed_chunks(new_chunks, provider)
    embeddable = [c for c in new_chunks if c.embedding is not None]
    inserted = store.upsert(embeddable) if embeddable else 0

    _kg_enrich_chunks(embeddable)

    if inserted > 0:
        _log_ingestion(
            db_path=ICDEV_DB,
            source_type=source_type,
            source_id=row_id,
            source_table=cfg["table"],
            chunks_created=inserted,
            chunks_skipped=len(chunks) - inserted,
            mode="realtime",
            tenant_id=tenant_id,
            project_id=project_id,
            correlation_id=correlation_id,
        )

    return {"ingested": inserted, "embedded": len(embeddable), "skipped": len(chunks) - inserted}


def main():
    parser = argparse.ArgumentParser(description="RAG Ingestion Manager")
    parser.add_argument("--ingest", action="store_true", help="Ingest a source")
    parser.add_argument("--source", help="Source type to ingest")
    parser.add_argument("--sweep", action="store_true", help="Sweep all sources")
    parser.add_argument("--status", action="store_true", help="Show ingestion status")
    parser.add_argument("--backfill-kg", action="store_true", help="Backfill KG from all RAG chunks")
    parser.add_argument("--tenant-id", default="", help="Tenant ID")
    parser.add_argument("--project-id", default="", help="Project ID")
    parser.add_argument("--limit", type=int, default=0, help="Max rows per source")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    if args.backfill_kg:
        import tools.rag.rag_to_kg_ingester as _rki
        result = _rki.run_backfill(as_json=args.json_output)
        if args.json_output:
            print(json.dumps(result, indent=2))
        else:
            print(
                f"KG backfill complete: {result.get('chunks_processed', 0)} chunks, "
                f"{result.get('nodes_written', 0)} nodes, "
                f"{result.get('edges_written', 0)} edges"
            )
    elif args.status:
        result = get_status(tenant_id=args.tenant_id)
        if args.json_output:
            print(json.dumps(result, indent=2))
        else:
            print("RAG Ingestion Status")
            print(f"  Backend: {result['vector_store_backend']}")
            print(f"  Available: {result['vector_store_available']}")
            print(f"  Total chunks: {result['total_chunks']}")
            print(f"  Last ingestion: {result['last_ingestion'] or 'never'}")
            print(f"  By source: {json.dumps(result['by_source_type'], indent=4)}")
            print(f"  By tier: {json.dumps(result['by_tier'], indent=4)}")
    elif args.ingest and args.source:
        result = ingest_source(
            source_type=args.source,
            tenant_id=args.tenant_id,
            project_id=args.project_id,
            limit=args.limit,
        )
        if args.json_output:
            print(json.dumps(result, indent=2))
        else:
            print(f"Ingested {result.get('chunks_created', 0)} chunks from {args.source}")
            if "error" in result:
                print(f"  Error: {result['error']}")
    elif args.sweep:
        result = sweep_all(tenant_id=args.tenant_id, project_id=args.project_id)
        if args.json_output:
            print(json.dumps(result, indent=2))
        else:
            print(f"Sweep complete: {result['total_chunks_created']} chunks created")
            if result["errors"]:
                print(f"  Errors: {result['errors']}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
