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
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import get_connection
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.rag.chunker import chunk_fields
from tools.rag.source_registry import (
    SOURCE_REGISTRY,
    get_chunking_template,
    get_source_config,
)
from tools.rag.vector_store_factory import VectorStoreFactory
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
ICDEV_DB = BASE_DIR / "data" / "icdev.db"
MEMORY_DB = BASE_DIR / "data" / "memory.db"

# RAGKG_HOOK_ENABLED=false disables KG enrichment without touching RAG pipeline.
RAGKG_HOOK_ENABLED = os.environ.get("RAGKG_HOOK_ENABLED", "true").lower() not in ("false", "0")

# ---------------------------------------------------------------------------
# Module-level fallback constants — all overridable from args/rag_config.yaml
# under ingestion_manager.anomaly_detection.  Change config, not code.
# ---------------------------------------------------------------------------
_EMBED_BATCH_SIZE   = 20     # chunks embedded per provider batch
_DB_BUSY_TIMEOUT_MS = 5000   # SQLite busy_timeout (ms) before raising lock error
_SKIP_RATE_ANOMALY  = 0.95   # dedup skip-rate above this flags an anomalous ingestion run


# ---------------------------------------------------------------------------
# Ingestion-time PII/CUI masking (trust-mask-02)
# ---------------------------------------------------------------------------
# When args/redaction_config.yaml sets ``mask_at_ingestion: true``, content is
# anonymized BEFORE chunking/hashing/embedding, so raw sensitive text never
# lands in the vector store at rest. Default off (opt-in) — enabling it adds
# per-row detection cost (regex + optional local NER).

_ingestion_anonymizer = None
_ingestion_anonymizer_loaded = False


def _mask_at_ingestion_enabled() -> bool:
    """True when redaction.mask_at_ingestion is set (default False = off)."""
    try:
        import yaml

        cfg_path = BASE_DIR / "args" / "redaction_config.yaml"
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return bool(cfg.get("enabled", True)) and bool(cfg.get("mask_at_ingestion", False))
    except Exception:
        return False


def _get_ingestion_anonymizer():
    """Cached RedactionAnonymizer; None if the redaction module is unavailable."""
    global _ingestion_anonymizer, _ingestion_anonymizer_loaded
    if _ingestion_anonymizer_loaded:
        return _ingestion_anonymizer
    _ingestion_anonymizer_loaded = True
    try:
        from tools.redaction.anonymizer import RedactionAnonymizer

        _ingestion_anonymizer = RedactionAnonymizer()
    except Exception:
        _ingestion_anonymizer = None
    return _ingestion_anonymizer


def _mask_row_content(row_dict: dict, content_cols: list, impact_level: str = "IL4") -> int:
    """Anonymize the content columns of a source row in place (trust-mask-02).

    Returns the number of PII/CUI entities masked. No-op (returns 0) when
    masking is disabled or the redaction module is unavailable.
    """
    anonymizer = _get_ingestion_anonymizer()
    if anonymizer is None:
        return 0
    masked = 0
    for col in content_cols:
        val = row_dict.get(col)
        if not isinstance(val, str) or not val.strip():
            continue
        try:
            result = anonymizer.anonymize(val, impact_level=impact_level)
            if result.detections:
                row_dict[col] = result.anonymized_text
                masked += len(result.detections)
        except Exception:
            continue  # never block ingestion on a masking error
    return masked


def _load_anomaly_cfg() -> dict:
    """Load ingestion_manager.anomaly_detection config from args/rag_config.yaml."""
    config_path = BASE_DIR / "args" / "rag_config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml

        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("ingestion_manager", {}).get("anomaly_detection", {})
    except Exception:
        return {}


def _compute_ingestion_thresholds(anomaly_cfg: "dict | None" = None) -> dict:
    """Compute adaptive ingestion thresholds from historical rag_ingestion_log data.

    Anomaly-detection calibration (replaces hardcoded constants):
    - embed_batch_size: scaled toward the observed average chunks-per-ingestion so
      small corpora use small batches and high-throughput sources use larger ones.
    - skip_rate_anomaly: the dedup skip-rate (chunks_skipped / total) above which an
      ingestion run is considered anomalous, derived from the historical norm.

    Falls back to module-level defaults when fewer than min_samples rows exist or
    anomaly detection is disabled.
    """
    cfg = anomaly_cfg or {}
    fallback_batch = int(cfg.get("fallback_embed_batch", _EMBED_BATCH_SIZE))
    fallback_skip  = float(cfg.get("fallback_skip_rate_anomaly", _SKIP_RATE_ANOMALY))

    if not cfg.get("enabled", True):
        return {"embed_batch_size": fallback_batch,
                "skip_rate_anomaly": fallback_skip,
                "computed": False}

    min_samples = cfg.get("min_samples", 30)
    bounds      = cfg.get("adaptive_bounds", {})
    batch_floor = int(bounds.get("batch_floor", 5))
    batch_ceil  = int(bounds.get("batch_ceil", 100))
    skip_floor  = float(bounds.get("skip_floor", 0.50))
    skip_ceil   = float(bounds.get("skip_ceil", 0.99))

    conn = None
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT AVG(chunks_created) AS avg_created, "
            "SUM(chunks_skipped) * 1.0 / NULLIF(SUM(chunks_created + chunks_skipped), 0) AS skip_rate, "
            "COUNT(*) AS n "
            "FROM rag_ingestion_log"
        ).fetchone()
        if row:
            n = row["n"] if isinstance(row, dict) else row[2]
            if n and n >= min_samples:
                avg_created = float(row["avg_created"] if isinstance(row, dict) else row[0]) or 0.0
                skip_rate   = float(row["skip_rate"] if isinstance(row, dict) else row[1]) or 0.0
                # Scale embed batch toward observed throughput; keep within safe bounds.
                new_batch = int(max(batch_floor, min(batch_ceil, round(avg_created) or fallback_batch)))
                # Flag runs whose skip-rate exceeds the historical norm by a small margin.
                new_skip  = max(skip_floor, min(skip_ceil, round(skip_rate + 0.05, 3)))
                return {
                    "embed_batch_size": new_batch,
                    "skip_rate_anomaly": new_skip,
                    "computed": True,
                    "n_samples": n,
                    "avg_chunks_created": round(avg_created, 2),
                    "historical_skip_rate": round(skip_rate, 3),
                }
    except Exception:
        pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    return {"embed_batch_size": fallback_batch,
            "skip_rate_anomaly": fallback_skip,
            "computed": False}


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


def _embed_chunks(chunks, provider, batch_size: "int | None" = None) -> int:
    """Embed chunks using embedding provider. Returns count embedded.

    batch_size defaults to the adaptive/configured value (see
    _compute_ingestion_thresholds); falls back to _EMBED_BATCH_SIZE.

    PER-CHUNK FAILURES ARE STILL SWALLOWED, DELIBERATELY. One malformed chunk
    must not abort a 10k-row ingest, so the ``continue`` stays.

    WHAT CHANGED (fli-ing-02) IS THAT A TOTAL FAILURE IS NO LONGER SILENT. The
    old loop discarded the exception entirely — not even its type — so an error
    hitting EVERY chunk was indistinguishable from an empty input. That is not
    hypothetical: rce-ctx-01 added the ``chunk.text_for_embedding()`` call below,
    the anomaly test's stub chunk never grew the method, and the resulting
    AttributeError was eaten on every chunk for weeks. The test asserted 0 == 5
    and no log line anywhere said why.

    The same shape reaches production, because the failure is systematic by
    nature: a renamed method, a provider outage, a wrong model name. Every chunk
    fails, ``embeddable`` comes out empty, the row is skipped, and the ingest
    reports success having stored nothing — invisible in retrieval too, since
    those chunks simply never come back.

    So: count the failures, keep the FIRST exception, and say so. Partial is a
    warning (some chunks are legitimately bad); total is an error, because
    "every single one failed" is a broken pipeline, not bad data.
    """
    if not provider or not chunks:
        return 0
    embedded = 0
    failed = 0
    first_error: "Exception | None" = None
    batch_size = batch_size or _EMBED_BATCH_SIZE
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        for chunk in batch:
            try:
                # Contextual retrieval (rce-ctx-01): embed the contextualized
                # text when a prefix was applied (embed_text set), otherwise the
                # original content — identical to prior behavior when unset.
                embed_input = chunk.text_for_embedding()
                if hasattr(provider, "embed"):
                    chunk.embedding = provider.embed(embed_input)
                else:
                    resp = provider.embeddings.create(input=embed_input, model="nomic-embed-text")
                    chunk.embedding = resp.data[0].embedding
                embedded += 1
            except Exception as exc:  # noqa: BLE001 — one bad chunk must not abort the ingest
                failed += 1
                if first_error is None:
                    first_error = exc
                continue

    if failed:
        detail = f"{type(first_error).__name__}: {first_error}"
        if embedded:
            logger.warning(
                "embedding: %d of %d chunk(s) failed and were dropped; first error was %s",
                failed, len(chunks), detail)
        else:
            logger.error(
                "embedding: ALL %d chunk(s) failed — nothing will be stored for this "
                "source and it will never appear in retrieval. First error was %s",
                failed, detail)
    return embedded


# ---------------------------------------------------------------------------
# Contextual retrieval (rce-ctx-01) — opt-in, default OFF, air-gap safe.
# ---------------------------------------------------------------------------
def _load_contextual_cfg() -> dict:
    """Load rag.contextual_retrieval config from args/rag_config.yaml (once per run)."""
    config_path = BASE_DIR / "args" / "rag_config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml

        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("rag", {}).get("contextual_retrieval", {}) or {}
    except Exception:
        return {}


def _build_document_text(fields: dict, content_cols: list) -> str:
    """Rebuild the row's combined 'document' text (same shape chunk_fields uses)."""
    parts = []
    for name in content_cols:
        val = fields.get(name, "")
        if val and str(val).strip():
            parts.append(f"{name}: {str(val).strip()}")
    return "\n".join(parts)


def _apply_contextual_prefixes(new_chunks, document_text: str, ctx_cfg: dict) -> int:
    """Apply context prefixes to new chunks in place. Returns count applied.

    No-op (returns 0) when contextual retrieval is disabled or the module is
    unavailable — never blocks ingestion. content_hash is left untouched (it was
    computed on original content at chunk time), so dedup stays stable.
    """
    if not new_chunks or not ctx_cfg.get("enabled", False):
        return 0
    try:
        from tools.rag.contextual_retrieval import contextualize_chunk
    except Exception:
        return 0
    applied = 0
    for chunk in new_chunks:
        try:
            if contextualize_chunk(chunk, document_text, config=ctx_cfg):
                applied += 1
        except Exception:
            continue
    return applied


def invalidate_cortex_cache(source_type: str, chunks: int) -> None:
    """Purge the Cortex response cache after the RAG corpus actually changed.

    ``cortex.search`` answers are derived from the vector store this module
    writes, so new chunks make cached search results stale before their TTL
    runs out. This is the in-process choke point for that corpus, which is why
    ``cortex.search`` is cacheable and ``cortex.ask`` (whose invalidating writes
    land in the operational DB from other processes) is not — see
    tools/cortex/cache.py.

    Best-effort: ingestion must never fail because a cache purge did. Skipped
    when nothing was written, so a no-op sweep does not throw away warm entries.
    """
    if chunks <= 0:
        return
    try:
        from tools.cortex import cache as _cortex_cache
        if _cortex_cache.is_enabled():
            _cortex_cache.invalidate(f"rag ingest: {source_type} (+{chunks} chunks)")
    except Exception as exc:  # noqa: BLE001
        logger.debug("cortex cache invalidation skipped: %s", exc)


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
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'CUI')""",
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

    # Adaptive thresholds (anomaly_detection) — replaces hardcoded batch size.
    thresholds = _compute_ingestion_thresholds(_load_anomaly_cfg())
    embed_batch_size = thresholds["embed_batch_size"]

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
            conn.execute(f"PRAGMA busy_timeout={_DB_BUSY_TIMEOUT_MS}")
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
    rows_embedding_failed = 0
    total_masked = 0
    total_contextualized = 0
    ingestion_mode = mode or cfg.get("mode", "batch")
    # oss-chunk-01: the registry's `chunking` key names a template in
    # args/chunking_templates.yaml. "" = configured default (sliding window).
    chunking_template = get_chunking_template(source_type)
    # trust-mask-02: decide once per run whether to mask content at ingestion.
    _mask_ingest = _mask_at_ingestion_enabled()
    # rce-ctx-01: decide once per run whether to apply contextual prefixes.
    _ctx_cfg = _load_contextual_cfg()

    for row in rows:
        row_dict = dict(row)
        row_id = str(row_dict[pk])

        # trust-mask-02: anonymize sensitive content BEFORE chunking/hashing so
        # raw PII/CUI never lands in the vector store at rest.
        if _mask_ingest:
            total_masked += _mask_row_content(row_dict, content_cols)

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
            template=chunking_template or None,
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

        # rce-ctx-01: prepend contextual prefixes so the embedding is computed
        # on contextualized text while stored content stays original.
        total_contextualized += _apply_contextual_prefixes(
            new_chunks, _build_document_text(row_dict, content_cols), _ctx_cfg
        )

        # Embed new chunks
        embedded = _embed_chunks(new_chunks, provider, batch_size=embed_batch_size)
        total_embedded += embedded
        # fli-ing-02: this loop skips the row when nothing embedded, and the
        # summary below reported only what SUCCEEDED — so a sweep in which every
        # row failed to embed returned the same shape as a sweep with nothing to
        # do. Count the rows dropped that way and report them.
        if new_chunks and not embedded:
            rows_embedding_failed += 1

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

    # The corpus cortex.search answers from just changed -> drop stale answers.
    invalidate_cortex_cache(source_type, total_chunks)

    # Anomaly check: flag runs whose dedup skip-rate exceeds the adaptive norm.
    _processed = total_chunks + total_skipped
    skip_rate = (total_skipped / _processed) if _processed else 0.0
    skip_anomaly = skip_rate > thresholds["skip_rate_anomaly"]

    return {
        "classification": "CUI // SP-CTI",
        "source_type": source_type,
        "table": table,
        "rows_processed": len(rows),
        "chunks_created": total_chunks,
        "chunks_skipped": total_skipped,
        "chunks_embedded": total_embedded,
        "rows_embedding_failed": rows_embedding_failed,
        "mode": ingestion_mode,
        "chunking_template": chunking_template or "default",
        "embed_batch_size": embed_batch_size,
        "skip_rate": round(skip_rate, 3),
        "skip_rate_anomaly": skip_anomaly,
        "masked_at_ingestion": total_masked,
        "contextualized_chunks": total_contextualized,
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
        # oss-chunk-01: honour the registry's `chunking` key on realtime ingest.
        template=get_chunking_template(source_type) or None,
    )

    if not chunks:
        return {"ingested": 0, "reason": "no_content"}

    # Dedup + embed
    new_chunks = [c for c in chunks if not store.get_by_content_hash(c.content_hash)]
    if not new_chunks:
        return {"ingested": 0, "skipped": len(chunks), "reason": "dedup"}

    # rce-ctx-01: contextual prefixing (opt-in, default off, air-gap safe).
    _apply_contextual_prefixes(
        new_chunks, _build_document_text(record, content_cols), _load_contextual_cfg()
    )

    embed_batch_size = _compute_ingestion_thresholds(_load_anomaly_cfg())["embed_batch_size"]
    _embed_chunks(new_chunks, provider, batch_size=embed_batch_size)
    embeddable = [c for c in new_chunks if c.embedding is not None]

    # fli-ing-02: chunks existed and NONE embedded. Returning the bare
    # {"ingested": 0, ...} below made that indistinguishable from the dedup
    # early-return above — both say "nothing was stored" and only one of them is
    # a working system. Name it, so a caller and a log reader can tell them apart.
    if new_chunks and not embeddable:
        return {"ingested": 0, "embedded": 0, "skipped": len(chunks),
                "reason": "embedding_failed"}

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

    invalidate_cortex_cache(source_type, inserted)

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
