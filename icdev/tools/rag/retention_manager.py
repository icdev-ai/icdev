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
import math
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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

from tools.rag.vector_store_factory import VectorStoreFactory
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.rag.retention_manager")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
ICDEV_DB = BASE_DIR / "data" / "icdev.db"


# ---------------------------------------------------------------------------
# Module-level fallback constants — all overridable from args/rag_config.yaml
# under rag.retention (and rag.retention.anomaly_detection).  Change config,
# not code.
# ---------------------------------------------------------------------------
_HOT_DAYS = 30                       # default age before hot → warm migration
_WARM_DAYS = 365                     # default age before warm → cold migration
_REHYDRATE_EMBED_MODEL = "nomic-embed-text"   # model used to re-embed cold chunks

# Anomaly-detection knobs — a chunk that lingers in a tier far longer than its
# peers (age beyond mean + k·stddev of that tier's age distribution) is flagged
# as an overdue-migration outlier.  When enough history exists the overdue
# threshold is recomputed adaptively; otherwise the configured tier age is used.
_ANOMALY_STDDEV_K = 2.0              # flag chunks older than mean + k·stddev
_ANOMALY_MIN_SAMPLES = 20           # min chunks in a tier before adapting


def _load_retention_config() -> dict:
    """Load retention config from args/rag_config.yaml."""
    config_path = BASE_DIR / "args" / "rag_config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml

        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("rag", {}).get("retention", {})
    except Exception:
        return {}


def _load_retention_anomaly_config(config: "dict | None" = None) -> dict:
    """Load rag.retention.anomaly_detection settings from rag_config.yaml.

    A caller-supplied ``config`` may carry the block directly under an
    ``anomaly_detection`` key; otherwise fall back to the
    ``rag.retention.anomaly_detection`` block in args/rag_config.yaml.
    Returns an empty dict when nothing is configured.
    """
    cfg = config or {}
    if "anomaly_detection" in cfg:
        return cfg["anomaly_detection"] or {}
    return _load_retention_config().get("anomaly_detection", {}) or {}


def _chunk_age_days(created_at: Any, now: "datetime | None" = None) -> "float | None":
    """Return the age in days of a chunk given its ``created_at`` value.

    Accepts ISO-8601 strings (with or without timezone / trailing ``Z``) and
    SQLite ``CURRENT_TIMESTAMP`` strings (``YYYY-MM-DD HH:MM:SS``).  Returns
    ``None`` when the timestamp cannot be parsed.
    """
    if created_at is None:
        return None
    now = now or datetime.now(timezone.utc)
    ts = str(created_at).strip()
    if not ts:
        return None
    # Normalise common forms into something datetime.fromisoformat can read.
    normalised = ts.replace("Z", "+00:00").replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(normalised)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (now - dt).total_seconds() / 86400.0)


def _compute_retention_anomaly_thresholds(
    tenant_id: str = "",
    anomaly_cfg: "dict | None" = None,
) -> Dict[str, Any]:
    """Compute adaptive per-tier overdue-age thresholds from chunk history.

    For each managed tier (``hot``, ``warm``) the age distribution of resident
    chunks is summarised (mean / population stddev) and an overdue threshold is
    derived as ``mean + k·stddev`` — so "this chunk has been here too long"
    tracks the actual corpus instead of a frozen magic number.

    Falls back to the configured tier age (``hot_days`` / ``warm_days``) when a
    tier has fewer than ``min_samples`` chunks or anomaly detection is disabled.
    Pure statistics computed in Python (backend-agnostic, no SQLite-only SQL).
    """
    cfg = anomaly_cfg if anomaly_cfg is not None else _load_retention_anomaly_config()
    ret_cfg = _load_retention_config()
    hot_days = ret_cfg.get("hot_days", _HOT_DAYS)
    warm_days = ret_cfg.get("warm_days", _WARM_DAYS)

    fallback = {
        "classification": "CUI // SP-CTI",
        "computed": False,
        "thresholds": {"hot": float(hot_days), "warm": float(warm_days)},
    }
    if not cfg.get("enabled", True):
        return fallback

    min_samples = int(cfg.get("min_samples", _ANOMALY_MIN_SAMPLES))
    stddev_k = float(cfg.get("stddev_k", _ANOMALY_STDDEV_K))
    bounds = cfg.get("adaptive_bounds", {})

    now = datetime.now(timezone.utc)
    thresholds = {"hot": float(hot_days), "warm": float(warm_days)}
    stats: Dict[str, Any] = {}
    computed = False

    conn = None
    try:
        conn = get_connection()
        for tier, base_days in (("hot", hot_days), ("warm", warm_days)):
            rows = conn.execute(
                "SELECT created_at FROM rag_chunks WHERE tier = %s",
                (tier,),
            ).fetchall()
            ages = [
                age
                for age in (_chunk_age_days(r[0], now) for r in rows)
                if age is not None
            ]
            if len(ages) < min_samples:
                continue
            mean = sum(ages) / len(ages)
            var = sum(a * a for a in ages) / len(ages) - mean * mean
            std = math.sqrt(max(0.0, var))
            overdue = mean + stddev_k * std
            # Never flag earlier than the configured tier age; clamp to bounds.
            floor_min = float(bounds.get(f"{tier}_min", base_days))
            floor_max = float(bounds.get(f"{tier}_max", base_days * 4))
            overdue = max(floor_min, min(floor_max, overdue))
            thresholds[tier] = round(overdue, 2)
            stats[tier] = {
                "n_samples": len(ages),
                "mean_age_days": round(mean, 2),
                "stddev_days": round(std, 2),
            }
            computed = True
    except Exception:
        return fallback
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    return {
        "classification": "CUI // SP-CTI",
        "computed": computed,
        "thresholds": thresholds,
        "stats": stats,
        "stddev_k": stddev_k,
    }


def detect_retention_anomalies(tenant_id: str = "") -> Dict[str, Any]:
    """Flag chunks that have lingered in a tier far beyond their peers.

    Uses the adaptive overdue-age thresholds from
    :func:`_compute_retention_anomaly_thresholds` to surface chunks whose age
    exceeds ``mean + k·stddev`` for their tier — overdue-migration outliers that
    a frozen 30/365-day rule would let sit silently.  Migration behaviour is
    unchanged; this is a separate, read-only diagnostic surface.

    Returns per-tier overdue chunk IDs, counts, and the thresholds used.
    """
    thr = _compute_retention_anomaly_thresholds(tenant_id=tenant_id)
    thresholds = thr["thresholds"]

    now = datetime.now(timezone.utc)
    overdue: Dict[str, List[str]] = {"hot": [], "warm": []}

    conn = None
    try:
        conn = get_connection()
        for tier in ("hot", "warm"):
            limit_days = thresholds.get(tier, _HOT_DAYS if tier == "hot" else _WARM_DAYS)
            for cid, created_at in conn.execute(
                "SELECT id, created_at FROM rag_chunks WHERE tier = %s",
                (tier,),
            ).fetchall():
                age = _chunk_age_days(created_at, now)
                if age is not None and age > limit_days:
                    overdue[tier].append(cid)
    except Exception:
        pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    return {
        "classification": "CUI // SP-CTI",
        "overdue_hot": overdue["hot"],
        "overdue_warm": overdue["warm"],
        "overdue_hot_count": len(overdue["hot"]),
        "overdue_warm_count": len(overdue["warm"]),
        "total_anomalies": len(overdue["hot"]) + len(overdue["warm"]),
        "thresholds": thresholds,
        "adaptive": thr.get("computed", False),
        "stats": thr.get("stats", {}),
    }


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
    hot_days = hot_days or ret_cfg.get("hot_days", _HOT_DAYS)
    warm_days = warm_days or ret_cfg.get("warm_days", _WARM_DAYS)

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
            "SELECT id FROM rag_chunks WHERE tier = 'hot' AND created_at < %s",
            (hot_cutoff,),
        ).fetchall()
    ]

    # Find warm chunks older than warm_days
    warm_to_cold = [
        row[0]
        for row in conn.execute(
            "SELECT id FROM rag_chunks WHERE tier = 'warm' AND created_at < %s",
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
                   VALUES (%s, %s, %s, %s, %s, %s, %s, 'CUI')""",
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
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            logger.warning("migrate_chunks: best-effort INSERT into rag_ingestion_log failed (non-blocking): %s", exc)

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
        row = conn.execute("SELECT content, tier FROM rag_chunks WHERE id = %s", (cid,)).fetchone()
        if not row:
            continue
        content, tier = row
        if tier != "cold":
            continue  # Only rehydrate cold chunks

        try:
            if hasattr(provider, "embed"):
                embedding = provider.embed(content)
            else:
                resp = provider.embeddings.create(input=content, model=_REHYDRATE_EMBED_MODEL)
                embedding = resp.data[0].embedding

            blob = struct.pack(f"{len(embedding)}f", *embedding)
            conn.execute(
                """UPDATE rag_chunks
                   SET tier = 'hot', embedding = %s, updated_at = CURRENT_TIMESTAMP
                   WHERE id = %s""",
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
            "hot_days": ret_cfg.get("hot_days", _HOT_DAYS),
            "warm_days": ret_cfg.get("warm_days", _WARM_DAYS),
            "warm_compression": ret_cfg.get("warm_compression", "float16"),
        },
        "backend": store.provider_name,
    }


def main():
    parser = argparse.ArgumentParser(description="RAG Retention Manager")
    parser.add_argument("--migrate", action="store_true", help="Run tier migration")
    parser.add_argument("--status", action="store_true", help="Show retention status")
    parser.add_argument("--rehydrate", action="store_true", help="Re-embed cold chunks")
    parser.add_argument(
        "--detect-anomalies",
        action="store_true",
        help="Flag chunks overdue for migration (adaptive anomaly detection)",
    )
    parser.add_argument("--chunk-ids", help="Comma-separated chunk IDs for rehydration")
    parser.add_argument("--dry-run", action="store_true", help="Report without migrating")
    parser.add_argument("--hot-days", type=int, default=0, help="Override hot→warm threshold")
    parser.add_argument("--warm-days", type=int, default=0, help="Override warm→cold threshold")
    parser.add_argument("--tenant-id", default="", help="Tenant ID")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    if args.status:
        result = get_retention_status(tenant_id=args.tenant_id)
    elif args.detect_anomalies:
        result = detect_retention_anomalies(tenant_id=args.tenant_id)
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
