#!/usr/bin/env python3
# CUI // SP-CTI
"""RAG-KG Bridge Ingester — reads rag_chunks, extracts entities/relationships,
writes kg_nodes + kg_edges with source_chunk_id and tenant_id back-refs.

Requires migration 046 (rag_kg_bridge) to have been applied.

Environment:
    RAGKG_INCLUDE_COLD=true   — include tier='cold' chunks (default: skip)
    RAGKG_PASSTHROUGH=true    — read content from rag_chunks.content directly,
                                skip source_registry lookups (default behaviour
                                anyway; this flag documents the intent explicitly)

CLI:
    python tools/rag/rag_to_kg_ingester.py --backfill [--tier warm] [--json]
    python tools/rag/rag_to_kg_ingester.py --chunk-id <id> [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection, is_pg, sql_now, sql_placeholder
from tools.knowledge_graph.text_network import (
    _ensure_tables,
    _extract_entities,
    _extract_relationships,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
# Module-level fallback constants — all overridable from args/rag_config.yaml
# under rag_to_kg_ingester.anomaly_detection.  Change config, not code.
PAGE_SIZE = 500            # rag_chunks fetched per backfill page (fallback)
_EMPTY_RATE_ANOMALY = 0.85  # zero-entity extraction rate above this flags a run

RAGKG_INCLUDE_COLD = os.environ.get("RAGKG_INCLUDE_COLD", "").lower() in ("true", "1")
RAGKG_PASSTHROUGH = os.environ.get("RAGKG_PASSTHROUGH", "").lower() in ("true", "1")


def _load_anomaly_cfg() -> dict:
    """Load rag_to_kg_ingester.anomaly_detection config from args/rag_config.yaml."""
    config_path = BASE_DIR / "args" / "rag_config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml

        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("rag_to_kg_ingester", {}).get("anomaly_detection", {})
    except Exception:
        return {}


def _compute_kg_thresholds(anomaly_cfg: "dict | None" = None) -> dict:
    """Compute adaptive KG-ingestion thresholds from the rag_chunks corpus.

    Anomaly-detection calibration (replaces hardcoded constants):
    - page_size: backfill page sized so ~target_pages cover the corpus, so small
      corpora use small pages and large corpora use larger ones (bounded).
    - empty_rate_anomaly: the zero-entity extraction rate (chunks yielding no KG
      nodes / processed chunks) above which a backfill run is considered anomalous,
      derived from the historical norm.

    Falls back to module-level defaults when fewer than min_samples chunks have
    been processed or anomaly detection is disabled.
    """
    cfg = anomaly_cfg or {}
    fallback_page  = int(cfg.get("fallback_page_size", PAGE_SIZE))
    fallback_empty = float(cfg.get("fallback_empty_rate_anomaly", _EMPTY_RATE_ANOMALY))

    if not cfg.get("enabled", True):
        return {"page_size": fallback_page,
                "empty_rate_anomaly": fallback_empty,
                "computed": False}

    min_samples  = cfg.get("min_samples", 50)
    target_pages = int(cfg.get("target_pages", 20))
    bounds       = cfg.get("adaptive_bounds", {})
    page_floor   = int(bounds.get("page_floor", 50))
    page_ceil    = int(bounds.get("page_ceil", 2000))
    empty_floor  = float(bounds.get("empty_floor", 0.30))
    empty_ceil   = float(bounds.get("empty_ceil", 0.99))

    conn = None
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT COUNT(*) AS n_total, "
            "SUM(CASE WHEN kg_node_ids IS NOT NULL AND kg_node_ids NOT IN ('', '[]') "
            "    THEN 1 ELSE 0 END) AS n_with_nodes, "
            "SUM(CASE WHEN kg_node_ids = '[]' THEN 1 ELSE 0 END) AS n_empty "
            "FROM rag_chunks"
        ).fetchone()
        if row:
            n_total      = int((row["n_total"] if isinstance(row, dict) else row[0]) or 0)
            n_with_nodes = int((row["n_with_nodes"] if isinstance(row, dict) else row[1]) or 0)
            n_empty      = int((row["n_empty"] if isinstance(row, dict) else row[2]) or 0)
            processed    = n_with_nodes + n_empty
            if processed >= min_samples:
                empty_rate = n_empty / processed if processed else 0.0
                # Size pages so ~target_pages cover the corpus; keep within bounds.
                raw_page = round(n_total / target_pages) if target_pages else fallback_page
                new_page = int(max(page_floor, min(page_ceil, raw_page or fallback_page)))
                # Flag runs whose empty-extraction rate exceeds the norm by a small margin.
                new_empty = max(empty_floor, min(empty_ceil, round(empty_rate + 0.10, 3)))
                return {
                    "page_size": new_page,
                    "empty_rate_anomaly": new_empty,
                    "computed": True,
                    "n_samples": processed,
                    "corpus_size": n_total,
                    "historical_empty_rate": round(empty_rate, 3),
                }
    except Exception:
        pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    return {"page_size": fallback_page,
            "empty_rate_anomaly": fallback_empty,
            "computed": False}

# Supplemental title-case noun pattern used in the no-LLM fallback
_RE_TITLE_NOUN = re.compile(r"\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,}){0,2})\b")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _kg_id() -> str:
    return f"kg-{uuid.uuid4().hex[:10]}"


def _has_llm() -> bool:
    try:
        from tools.llm.router import LLMRouter
        return LLMRouter().has_any_llm()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Entity extraction
# ---------------------------------------------------------------------------

def _extract_no_llm(text: str) -> list[tuple[str, str]]:
    """NIST control regex + title-case noun extraction (no-LLM fallback).

    Returns deduplicated (label, entity_type) list.
    """
    entities = _extract_entities(text)
    seen = {(lbl.lower(), et) for lbl, et in entities}

    for m in _RE_TITLE_NOUN.finditer(text):
        label = m.group(1).strip()
        if len(label) < 3:
            continue
        key = (label.lower(), "component")
        if key not in seen:
            seen.add(key)
            entities.append((label, "component"))

    return entities


# ---------------------------------------------------------------------------
# Cursor management
# ---------------------------------------------------------------------------

def _seed_cursor(conn) -> None:
    if is_pg(conn):
        conn.execute(
            "INSERT INTO rag_kg_backfill_cursor(last_chunk_id) VALUES(0) "
            "ON CONFLICT DO NOTHING"
        )
    else:
        conn.execute(
            "INSERT OR IGNORE INTO rag_kg_backfill_cursor(id, last_chunk_id) VALUES(1, 0)"
        )
    conn.commit()


def _get_cursor(conn):
    """Return last_chunk_id (0 for fresh start, TEXT id after first SQLite page,
    INT offset after first PostgreSQL page)."""
    ph = sql_placeholder(conn)
    row = conn.execute(
        f"SELECT last_chunk_id FROM rag_kg_backfill_cursor WHERE id = {ph}",  # nosec B608 — ph is ? or %s, not user data
        (1,),
    ).fetchone()
    if row is None:
        _seed_cursor(conn)
        return 0
    val = row[0]
    # SQLite may return an integer 0 or a stored text id
    try:
        return int(val)
    except (TypeError, ValueError):
        return val  # already a string id


def _update_cursor(conn, cursor_val) -> None:
    ph = sql_placeholder(conn)
    now_expr = sql_now(conn)
    conn.execute(
        f"UPDATE rag_kg_backfill_cursor "  # nosec B608 — ph/sql_now() only, no user input
        f"SET last_chunk_id = {ph}, updated_at = {now_expr} "
        f"WHERE id = {ph}",
        (cursor_val, 1),
    )


# ---------------------------------------------------------------------------
# Page fetching
# ---------------------------------------------------------------------------

def _fetch_page(conn, cursor, tier: Optional[str], page_size: int) -> list:
    """Return next page of rag_chunks for KG ingestion.

    SQLite: cursor-by-id (TEXT or 0 for start).
    PostgreSQL: cursor-by-offset (INT).
    """
    ph = sql_placeholder(conn)
    cols = (
        "id, content, metadata, tier, tenant_id, classification, "
        "COALESCE(kg_node_ids, '[]') AS kg_node_ids"
    )

    tier_cond: list[str] = []
    tier_params: list = []
    if not RAGKG_INCLUDE_COLD:
        if tier:
            tier_cond.append(f"tier = {ph}")
            tier_params.append(tier)
        else:
            tier_cond.append("tier != 'cold'")

    if is_pg(conn):
        offset = int(cursor) if isinstance(cursor, int) else 0
        where = f"WHERE {' AND '.join(tier_cond)}" if tier_cond else ""
        return conn.execute(
            f"SELECT {cols} FROM rag_chunks {where} ORDER BY id "  # nosec B608 — cols/where are constants; ph is placeholder
            f"LIMIT {ph} OFFSET {ph}",
            tier_params + [page_size, offset],
        ).fetchall()

    # SQLite: id > cursor (0 starts from the beginning; TEXT id gives true range)
    if cursor == 0:
        id_cond = []
        id_params: list = []
    else:
        id_cond = [f"id > {ph}"]
        id_params = [cursor]

    all_cond = id_cond + tier_cond
    where = f"WHERE {' AND '.join(all_cond)}" if all_cond else ""
    return conn.execute(
        f"SELECT {cols} FROM rag_chunks {where} ORDER BY id LIMIT {ph}",  # nosec B608 — cols/where are constants; ph is placeholder
        id_params + tier_params + [page_size],
    ).fetchall()


# ---------------------------------------------------------------------------
# Stale invalidation
# ---------------------------------------------------------------------------

def _delete_stale_nodes(conn, node_ids: list[str]) -> None:
    """Delete kg_nodes (and their edges) that belong to a previously-ingested chunk."""
    ph = sql_placeholder(conn)
    for node_id in node_ids:
        conn.execute(
            f"DELETE FROM kg_edges WHERE source_id = {ph} OR target_id = {ph}",  # nosec B608 — ph only; node_id bound
            (node_id, node_id),
        )
        conn.execute(
            f"DELETE FROM kg_nodes WHERE id = {ph}",  # nosec B608 — ph only; node_id bound
            (node_id,),
        )


# ---------------------------------------------------------------------------
# Graph creation
# ---------------------------------------------------------------------------

def _create_graph(conn, chunk_id: str, tenant_id: str, classification: str) -> str:
    ph = sql_placeholder(conn)
    graph_id = _kg_id()
    now = _now()
    conn.execute(
        f"INSERT INTO kg_graphs "  # nosec B608 — only ph placeholders; all values bound
        f"(id, project_id, name, description, metadata, created_at, updated_at) "
        f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})",
        (
            graph_id,
            tenant_id or "rag-bridge",
            f"rag-chunk-{chunk_id}",
            f"Auto-extracted from RAG chunk {chunk_id}",
            json.dumps({
                "source": "rag_kg_bridge",
                "source_chunk_id": chunk_id,
                "tenant_id": tenant_id,
                "classification": classification,
            }),
            now,
            now,
        ),
    )
    return graph_id


# ---------------------------------------------------------------------------
# Single-chunk ingestion
# ---------------------------------------------------------------------------

def _ingest_chunk(conn, chunk: dict, use_llm: bool) -> dict:
    """Extract entities from one chunk and write to KG tables.

    Returns {"nodes_written": int, "edges_written": int, "skipped": bool}.
    """
    chunk_id: str = chunk["id"]
    content: str = chunk.get("content") or ""

    if not content.strip():
        return {"nodes_written": 0, "edges_written": 0, "skipped": True}

    tenant_id: str = chunk.get("tenant_id") or ""

    try:
        raw_meta = json.loads(chunk.get("metadata") or "{}")
    except (json.JSONDecodeError, TypeError):
        raw_meta = {}

    # Classification: chunk metadata field takes precedence over column value
    classification: str = (
        raw_meta.get("classification") or chunk.get("classification") or "CUI"
    )

    # Stale invalidation: delete existing KG nodes for this chunk
    try:
        existing_ids: list = json.loads(chunk.get("kg_node_ids") or "[]")
    except (json.JSONDecodeError, TypeError):
        existing_ids = []

    if existing_ids:
        _delete_stale_nodes(conn, [str(nid) for nid in existing_ids])

    # Entity extraction — passthrough: content always read from rag_chunks.content
    if use_llm:
        entities = _extract_entities(content)
        relationships = _extract_relationships(content, entities)
    else:
        entities = _extract_no_llm(content)
        relationships = []

    ph = sql_placeholder(conn)
    now = _now()

    if not entities:
        conn.execute(
            f"UPDATE rag_chunks SET kg_node_ids = {ph} WHERE id = {ph}",  # nosec B608 — ph only; values bound
            ("[]", chunk_id),
        )
        return {"nodes_written": 0, "edges_written": 0, "skipped": False}

    graph_id = _create_graph(conn, chunk_id, tenant_id, classification)

    # Insert nodes
    node_ids: list[str] = []
    label_to_id: dict[str, str] = {}

    for label, entity_type in entities:
        node_id = _kg_id()
        props = json.dumps({
            "tenant_id": tenant_id,
            "classification": classification,
            "source": "rag_kg_bridge",
        })
        conn.execute(
            f"INSERT INTO kg_nodes "  # nosec B608 — only ph placeholders; all values bound
            f"(id, graph_id, label, entity_type, properties, source_chunk_id, created_at) "
            f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})",
            (node_id, graph_id, label, entity_type, props, chunk_id, now),
        )
        node_ids.append(node_id)
        label_to_id[label] = node_id

    # Insert edges — enforce tenant isolation (all nodes share chunk's tenant_id)
    edges_written = 0
    for src_label, tgt_label, rel_type in relationships:
        src_id = label_to_id.get(src_label)
        tgt_id = label_to_id.get(tgt_label)
        if not src_id or not tgt_id:
            continue
        edge_id = _kg_id()
        conn.execute(
            f"INSERT INTO kg_edges "  # nosec B608 — only ph placeholders; all values bound
            f"(id, graph_id, source_id, target_id, relationship, weight, properties, created_at) "
            f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})",
            (edge_id, graph_id, src_id, tgt_id, rel_type, 1.0, "{}", now),
        )
        edges_written += 1

    # Update graph entity/edge counts
    conn.execute(
        f"UPDATE kg_graphs SET entity_count = {ph}, edge_count = {ph}, updated_at = {ph} "  # nosec B608 — only ph placeholders; all values bound
        f"WHERE id = {ph}",
        (len(node_ids), edges_written, now, graph_id),
    )

    # Write back kg_node_ids to rag_chunks
    conn.execute(
        f"UPDATE rag_chunks SET kg_node_ids = {ph} WHERE id = {ph}",  # nosec B608 — ph only; values bound
        (json.dumps(node_ids), chunk_id),
    )

    return {"nodes_written": len(node_ids), "edges_written": edges_written, "skipped": False}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_backfill(tier: Optional[str] = None, as_json: bool = False) -> dict:
    """Cursor-based backfill: process all rag_chunks in pages of PAGE_SIZE."""
    use_llm = _has_llm()

    # Adaptive thresholds (anomaly_detection) — replaces hardcoded PAGE_SIZE.
    thresholds = _compute_kg_thresholds(_load_anomaly_cfg())
    page_size = thresholds["page_size"]

    conn = get_connection()
    try:
        _ensure_tables(conn)  # ensure kg_graphs/kg_nodes/kg_edges exist

        cursor = _get_cursor(conn)
        total_nodes = total_edges = total_chunks = total_empty = pages = 0

        while True:
            page_start = time.monotonic()
            rows = _fetch_page(conn, cursor, tier, page_size)
            if not rows:
                break

            pages += 1
            page_nodes = page_edges = 0

            for row in rows:
                stats = _ingest_chunk(conn, dict(row), use_llm)
                page_nodes += stats["nodes_written"]
                page_edges += stats["edges_written"]
                if stats["nodes_written"] == 0:
                    total_empty += 1
                total_chunks += 1

            # Advance cursor
            if is_pg(conn):
                cursor = (int(cursor) if isinstance(cursor, int) else 0) + len(rows)
            else:
                cursor = rows[-1]["id"]

            _update_cursor(conn, cursor)
            conn.commit()

            elapsed = time.monotonic() - page_start
            total_nodes += page_nodes
            total_edges += page_edges

            if not as_json:
                print(
                    f"[page {pages}] chunks={len(rows)} nodes={page_nodes} "
                    f"edges={page_edges} elapsed={elapsed:.2f}s cursor={cursor!r}"
                )

        empty_rate = (total_empty / total_chunks) if total_chunks else 0.0
        empty_anomaly = empty_rate > thresholds["empty_rate_anomaly"]

        return {
            "status": "ok",
            "pages": pages,
            "chunks_processed": total_chunks,
            "nodes_written": total_nodes,
            "edges_written": total_edges,
            "use_llm": use_llm,
            "page_size": page_size,
            "empty_extraction_rate": round(empty_rate, 3),
            "empty_rate_anomaly_threshold": thresholds["empty_rate_anomaly"],
            "empty_extraction_anomaly": empty_anomaly,
            "thresholds_computed": thresholds["computed"],
        }
    finally:
        conn.close()


def ingest_chunk(conn, chunk_id: str) -> dict:
    """Post-ingest hook: extract KG entities from a single newly-written chunk.

    Accepts an existing connection so the caller can reuse a long-lived conn.
    Commits internally. Raises on hard DB errors — caller's try/except isolates
    KG failures from the RAG ingest transaction.
    """
    use_llm = _has_llm()
    _ensure_tables(conn)

    ph = sql_placeholder(conn)
    row = conn.execute(
        f"SELECT id, content, metadata, tier, tenant_id, classification, "  # nosec B608 — ph only; chunk_id bound
        f"COALESCE(kg_node_ids, '[]') AS kg_node_ids "
        f"FROM rag_chunks WHERE id = {ph}",
        (chunk_id,),
    ).fetchone()

    if row is None:
        return {"status": "not_found", "chunk_id": chunk_id}

    stats = _ingest_chunk(conn, dict(row), use_llm)
    conn.commit()
    return {"status": "ok", "chunk_id": chunk_id, **stats}


def run_single(chunk_id: str, as_json: bool = False) -> dict:
    """Process a single chunk by id."""
    use_llm = _has_llm()

    conn = get_connection()
    try:
        _ensure_tables(conn)

        ph = sql_placeholder(conn)
        row = conn.execute(
            f"SELECT id, content, metadata, tier, tenant_id, classification, "  # nosec B608 — ph only; chunk_id bound
            f"COALESCE(kg_node_ids, '[]') AS kg_node_ids "
            f"FROM rag_chunks WHERE id = {ph}",
            (chunk_id,),
        ).fetchone()

        if row is None:
            return {"status": "error", "message": f"chunk {chunk_id!r} not found"}

        stats = _ingest_chunk(conn, dict(row), use_llm)
        conn.commit()

        return {
            "status": "ok",
            "chunk_id": chunk_id,
            "use_llm": use_llm,
            **stats,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="RAG-KG Bridge Ingester",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python tools/rag/rag_to_kg_ingester.py --backfill --tier warm --json\n"
            "  python tools/rag/rag_to_kg_ingester.py --chunk-id chunk-abc123def456 --json"
        ),
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--backfill", action="store_true",
        help="Cursor-based backfill of all unprocessed chunks",
    )
    mode.add_argument(
        "--chunk-id", metavar="ID",
        help="Process a single chunk by id",
    )
    p.add_argument(
        "--tier", choices=["hot", "warm", "cold"],
        help="Restrict backfill to chunks of this tier",
    )
    p.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Emit JSON output",
    )
    return p.parse_args(argv)


def main(argv=None) -> None:
    args = _parse_args(argv)
    t0 = time.monotonic()

    if args.backfill:
        result = run_backfill(tier=args.tier, as_json=args.as_json)
    else:
        result = run_single(chunk_id=args.chunk_id, as_json=args.as_json)

    result["elapsed_seconds"] = round(time.monotonic() - t0, 3)

    if args.as_json:
        print(json.dumps(result, indent=2))
    elif not args.backfill:
        print(result)


if __name__ == "__main__":
    main()
