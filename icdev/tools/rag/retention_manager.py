#!/usr/bin/env python3
# CUI // SP-CTI
"""RAG retention manager — tiered hot/warm/cold migration (D-RAG-6).

Manages chunk lifecycle: hot (full float32, 0-30 days) → warm (float16
compressed, 30-365 days) → cold (metadata only, 365+ days).

Usage:
    python tools/rag/retention_manager.py --migrate --json
    python tools/rag/retention_manager.py --status --json
    python tools/rag/retention_manager.py --rehydrate --chunk-ids "id1,id2" --json
"""

from __future__ import annotations

import argparse
import json
from tools.db.storage import get_connection
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

from tools.rag.vector_store_factory import VectorStoreFactory

BASE_DIR = Path(__file__).resolve().parent.parent.parent
ICDEV_DB = BASE_DIR / "data" / "icdev.db"


def _load_retention_config() -> dict:
    """Load retention config from args/rag_config.yaml."""
    config_path = BASE_DIR / "args" / "rag_config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml

        with open(config_path, "r") as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("rag", {}).get("retention", {})
    except Exception:
        return {}


def get_migration_candidates(
    tenant_id: str = "",
    hot_days: int = 0,
    warm_days: int = 0,
) -> Dict[str, Any]:
    """Find chunks eligible for tier migration.

    Args:
        tenant_id: Optional tenant ID.
        hot_days: Days before hot → warm (0 = use config default).
        warm_days: Days before warm → cold (0 = use config default).

    Returns:
        Dict with hot_to_warm and warm_to_cold chunk ID lists.
    """
    ret_cfg = _load_retention_config()
    hot_days = hot_days or ret_cfg.get("hot_days", 30)
    warm_days = warm_days or ret_cfg.get("warm_days", 365)

    store = VectorStoreFactory.create(tenant_id=tenant_id)
    if store.provider_name != "sqlite":
        return {
            "error": "Retention migration only supported for SQLite backend",
            "hot_to_warm": [],
            "warm_to_cold": [],
        }

    # Access DB via centralized connection
    conn = get_connection()

    now = datetime.now(timezone.utc)
    hot_cutoff = (now - timedelta(days=hot_days)).isoformat()
    warm_cutoff = (now - timedelta(days=warm_days)).isoformat()

    # Find hot chunks older than hot_days
    hot_to_warm = [
        row[0]
        for row in conn.execute(
            "SELECT id FROM rag_chunks WHERE tier = 'hot' AND created_at < ?",
            (hot_cutoff,),
        ).fetchall()
    ]

    # Find warm chunks older than warm_days
    warm_to_cold = [
        row[0]
        for row in conn.execute(
            "SELECT id FROM rag_chunks WHERE tier = 'warm' AND created_at < ?",
            (warm_cutoff,),
        ).fetchall()
    ]

    conn.close()

    return {
        "classification": "CUI // SP-CTI",
        "hot_to_warm": hot_to_warm,
        "warm_to_cold": warm_to_cold,
        "hot_to_warm_count": len(hot_to_warm),
        "warm_to_cold_count": len(warm_to_cold),
        "hot_cutoff_days": hot_days,
        "warm_cutoff_days": warm_days,
    }


def migrate_chunks(
    tenant_id: str = "",
    hot_days: int = 0,
    warm_days: int = 0,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Execute tier migration on eligible chunks.

    Args:
        tenant_id: Optional tenant ID.
        hot_days: Days before hot → warm.
        warm_days: Days before warm → cold.
        dry_run: If True, report candidates without migrating.

    Returns:
        Dict with migration results.
    """
    candidates = get_migration_candidates(
        tenant_id=tenant_id,
        hot_days=hot_days,
        warm_days=warm_days,
    )

    if "error" in candidates:
        return candidates

    if dry_run:
        return {
            "classification": "CUI // SP-CTI",
            "dry_run": True,
            "hot_to_warm_candidates": candidates["hot_to_warm_count"],
            "warm_to_cold_candidates": candidates["warm_to_cold_count"],
            "total_candidates": candidates["hot_to_warm_count"] + candidates["warm_to_cold_count"],
        }

    store = VectorStoreFactory.create(tenant_id=tenant_id)
    migrated_warm = 0
    migrated_cold = 0

    # Migrate hot → warm (float32 → float16 compression)
    if candidates["hot_to_warm"]:
        migrated_warm = store.migrate_tier(candidates["hot_to_warm"], "warm")

    # Migrate warm → cold (remove embeddings, keep metadata)
    if candidates["warm_to_cold"]:
        migrated_cold = store.migrate_tier(candidates["warm_to_cold"], "cold")

    # Log migration to ingestion log
    if (migrated_warm > 0 or migrated_cold > 0) and ICDEV_DB.exists():
        try:
            conn = get_connection()
            conn.execute(
                """INSERT INTO rag_ingestion_log
                   (source_type, source_id, source_table, chunks_created, chunks_skipped,
                    ingestion_mode, tenant_id, classification)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'CUI')""",
                (
                    "retention_migration",
                    f"warm:{migrated_warm},cold:{migrated_cold}",
                    "rag_chunks",
                    0,
                    migrated_warm + migrated_cold,
                    "batch",
                    tenant_id,
                ),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

    return {
        "classification": "CUI // SP-CTI",
        "migrated_hot_to_warm": migrated_warm,
        "migrated_warm_to_cold": migrated_cold,
        "total_migrated": migrated_warm + migrated_cold,
    }


def rehydrate_chunks(
    chunk_ids: List[str],
    tenant_id: str = "",
) -> Dict[str, Any]:
    """Re-embed cold chunks back to hot tier (on-demand).

    Cold chunks have no embeddings. This re-embeds them and moves
    them back to hot tier for active search.

    Args:
        chunk_ids: List of chunk IDs to rehydrate.
        tenant_id: Optional tenant ID.

    Returns:
        Dict with rehydration results.
    """
    if not chunk_ids:
        return {"rehydrated": 0, "reason": "no_chunk_ids"}

    store = VectorStoreFactory.create(tenant_id=tenant_id)
    if store.provider_name != "sqlite":
        return {"error": "Rehydration only supported for SQLite backend", "rehydrated": 0}

    # Get embedding provider
    try:
        from tools.llm import get_embedding_provider

        provider = get_embedding_provider()
    except Exception:
        return {"error": "No embedding provider available", "rehydrated": 0}

    conn = get_connection()
    rehydrated = 0

    import struct

    for cid in chunk_ids:
        row = conn.execute("SELECT content, tier FROM rag_chunks WHERE id = ?", (cid,)).fetchone()
        if not row:
            continue
        content, tier = row
        if tier != "cold":
            continue  # Only rehydrate cold chunks

        try:
            if hasattr(provider, "embed"):
                embedding = provider.embed(content)
            else:
                resp = provider.embeddings.create(input=content, model="nomic-embed-text")
                embedding = resp.data[0].embedding

            blob = struct.pack(f"{len(embedding)}f", *embedding)
            conn.execute(
                """UPDATE rag_chunks
                   SET tier = 'hot', embedding = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (blob, cid),
            )
            rehydrated += 1
        except Exception:
            continue

    conn.commit()
    conn.close()

    return {
        "classification": "CUI // SP-CTI",
        "rehydrated": rehydrated,
        "requested": len(chunk_ids),
    }


def get_retention_status(tenant_id: str = "") -> Dict[str, Any]:
    """Get retention status across tiers."""
    store = VectorStoreFactory.create(tenant_id=tenant_id)

    tier_counts = {}
    for tier in ("hot", "warm", "cold"):
        count = store.count(filters={"tier": tier})
        if count > 0:
            tier_counts[tier] = count

    ret_cfg = _load_retention_config()

    return {
        "classification": "CUI // SP-CTI",
        "total_chunks": store.count(),
        "by_tier": tier_counts,
        "config": {
            "hot_days": ret_cfg.get("hot_days", 30),
            "warm_days": ret_cfg.get("warm_days", 365),
            "warm_compression": ret_cfg.get("warm_compression", "float16"),
        },
        "backend": store.provider_name,
    }


def main():
    parser = argparse.ArgumentParser(description="RAG Retention Manager")
    parser.add_argument("--migrate", action="store_true", help="Run tier migration")
    parser.add_argument("--status", action="store_true", help="Show retention status")
    parser.add_argument("--rehydrate", action="store_true", help="Re-embed cold chunks")
    parser.add_argument("--chunk-ids", help="Comma-separated chunk IDs for rehydration")
    parser.add_argument("--dry-run", action="store_true", help="Report without migrating")
    parser.add_argument("--hot-days", type=int, default=0, help="Override hot→warm threshold")
    parser.add_argument("--warm-days", type=int, default=0, help="Override warm→cold threshold")
    parser.add_argument("--tenant-id", default="", help="Tenant ID")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    if args.status:
        result = get_retention_status(tenant_id=args.tenant_id)
    elif args.migrate:
        result = migrate_chunks(
            tenant_id=args.tenant_id,
            hot_days=args.hot_days,
            warm_days=args.warm_days,
            dry_run=args.dry_run,
        )
    elif args.rehydrate and args.chunk_ids:
        ids = [cid.strip() for cid in args.chunk_ids.split(",")]
        result = rehydrate_chunks(chunk_ids=ids, tenant_id=args.tenant_id)
    else:
        parser.print_help()
        return

    if args.json_output:
        print(json.dumps(result, indent=2))
    else:
        print(f"RAG Retention: {json.dumps(result, indent=2)}")


if __name__ == "__main__":
    main()
