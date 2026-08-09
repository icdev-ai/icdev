#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Automated RAG-to-FT Pipeline — detect new chunks, generate pairs, trigger retrain (D-KARL-5).

Orchestrates the automated flow:
    1. Detect new RAG chunks since last pipeline run
    2. Generate Q&A training pairs from chunks via pair_generator
    3. Quality-filter and auto-approve high-confidence pairs
    4. Check retrain trigger thresholds
    5. Trigger retrain if threshold met

Usage:
    python tools/finetune/rag_ft_pipeline.py --run --json
    python tools/finetune/rag_ft_pipeline.py --dry-run --json
    python tools/finetune/rag_ft_pipeline.py --status --json
    python tools/finetune/rag_ft_pipeline.py --run --source-type innovation_signals --json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

from tools.common.helpers import now_iso  # noqa: E402

logger = get_logger("icdev.finetune.rag_ft_pipeline")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _load_config() -> Dict[str, Any]:
    """Load rag_ft_pipeline config from finetune_config.yaml."""
    config_path = BASE_DIR / "args" / "finetune_config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml

        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("rag_ft_pipeline", {})
    except Exception:
        return {}


DEFAULT_CONFIG = {
    "enabled": True,
    "auto_approve_threshold": 0.8,
    "source_types": ["innovation_signals", "research_dossiers", "compliance_artifacts"],
    "schedule_minutes": 360,
    "max_pairs_per_run": 100,
    "default_dataset_name": "rag-auto-pipeline",
    "default_purpose": "general",
}


def _get_config() -> Dict[str, Any]:
    """Merge file config with defaults."""
    cfg = DEFAULT_CONFIG.copy()
    cfg.update(_load_config())
    return cfg


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


def _get_db():
    """Get connection to icdev.db."""
    from tools.db.storage import get_connection

    conn = get_connection()
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _gen_id(prefix: str = "ftpipe") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _ensure_tables(conn: sqlite3.Connection) -> None:
    """Create pipeline tracking table if not exists."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ft_pipeline_runs (
            id TEXT PRIMARY KEY,
            run_type TEXT NOT NULL DEFAULT 'rag_to_ft',
            source_type TEXT,
            chunks_processed INTEGER DEFAULT 0,
            pairs_generated INTEGER DEFAULT 0,
            pairs_approved INTEGER DEFAULT 0,
            retrain_triggered INTEGER DEFAULT 0,
            status TEXT DEFAULT 'running',
            error TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        )
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------


def _detect_new_chunks(
    conn: sqlite3.Connection,
    source_type: str,
    since: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Find RAG chunks created since last pipeline run for a source type."""
    # Find last pipeline run for this source type
    if since is None:
        row = conn.execute(
            """SELECT MAX(completed_at) as last_run FROM ft_pipeline_runs
               WHERE source_type = %s AND status = 'completed'""",
            (source_type,),
        ).fetchone()
        since = row["last_run"] if row and row["last_run"] else "2000-01-01T00:00:00Z"

    # Query rag_chunks for new content
    try:
        rows = conn.execute(
            """SELECT id, content, source_id, source_table, created_at
               FROM rag_chunks
               WHERE source_table = %s AND created_at > %s AND tier = 'hot'
               ORDER BY created_at DESC LIMIT %s""",
            (source_type, since, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        # Table might not exist or be empty
        return []


def _generate_pairs(
    chunks: List[Dict[str, Any]],
    dataset_id: str,
    purpose: str = "general",
    questions_per_chunk: int = 3,
) -> Dict[str, Any]:
    """Generate Q&A pairs from chunks using pair_generator."""
    try:
        from tools.finetune.pair_generator import generate_from_document

        chunk_data = [
            {"content": c.get("content", ""), "chunk_id": c.get("id", "")} for c in chunks if c.get("content")
        ]
        if not chunk_data:
            return {"success": False, "pairs_generated": 0, "error": "No chunks with content"}

        return generate_from_document(
            dataset_id=dataset_id,
            chunks=chunk_data,
            purpose=purpose,
            questions_per_chunk=questions_per_chunk,
        )
    except ImportError:
        return {"success": False, "pairs_generated": 0, "error": "pair_generator not available"}
    except Exception as exc:
        return {"success": False, "pairs_generated": 0, "error": str(exc)}


def _auto_approve_pairs(
    conn: sqlite3.Connection,
    dataset_id: str,
    threshold: float = 0.8,
) -> int:
    """Auto-approve unapproved pairs with quality_score >= threshold."""
    try:
        cursor = conn.execute(
            """UPDATE ft_dataset_examples
               SET approved = 1, labeled_by = 'rag_ft_pipeline', labeled_at = %s
               WHERE dataset_id = %s AND approved = 0 AND quality_score >= %s""",
            (now_iso(), dataset_id, threshold),
        )
        conn.commit()
        return cursor.rowcount
    except Exception:
        return 0


def _check_and_trigger_retrain(dataset_id: str) -> Dict[str, Any]:
    """Check retrain trigger and initiate if threshold met."""
    try:
        from tools.finetune.retrain_trigger import check_retrain_needed

        result = check_retrain_needed(dataset_id=dataset_id)
        if result.get("datasets_needing_retrain"):
            return {"retrain_needed": True, "details": result}
        return {"retrain_needed": False, "details": result}
    except ImportError:
        return {"retrain_needed": False, "error": "retrain_trigger not available"}
    except Exception as exc:
        return {"retrain_needed": False, "error": str(exc)}


def _ensure_dataset(conn: sqlite3.Connection, name: str, purpose: str) -> str:
    """Find or create the auto-pipeline dataset."""
    row = conn.execute(
        "SELECT id FROM ft_datasets WHERE name = %s AND status IN ('draft', 'labeling', 'ready')",
        (name,),
    ).fetchone()
    if row:
        return row["id"]

    # Create new dataset
    try:
        from tools.finetune.dataset_manager import create_dataset

        result = create_dataset(name=name, purpose=purpose, description="Auto-generated by RAG-FT pipeline")
        return result.get("dataset_id", "")
    except Exception:
        # Fallback: create directly
        ds_id = f"ds-{uuid.uuid4().hex[:12]}"
        conn.execute(
            "INSERT INTO ft_datasets (id, name, purpose, description, status, created_at) "
            "VALUES (%s, %s, %s, 'Auto-generated by RAG-FT pipeline', 'labeling', %s)",
            (ds_id, name, purpose, now_iso()),
        )
        conn.commit()
        return ds_id


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def run_pipeline(
    source_type: Optional[str] = None,
    dry_run: bool = False,
    run_type: str = "rag_to_ft",
) -> Dict[str, Any]:
    """Execute the full RAG-to-FT pipeline.

    Args:
        source_type: Specific source to process (None = all configured sources).
        dry_run: If True, detect chunks but don't generate/approve/retrain.
        run_type: Pipeline type ('rag_to_ft' or 'kg_to_ft').

    Returns:
        Pipeline execution summary.
    """
    config = _get_config()
    if not config.get("enabled", True):
        return {"status": "disabled", "message": "rag_ft_pipeline is disabled in config"}

    conn = _get_db()
    _ensure_tables(conn)

    source_types = [source_type] if source_type else config.get("source_types", [])
    max_pairs = config.get("max_pairs_per_run", 100)
    approve_threshold = config.get("auto_approve_threshold", 0.8)
    dataset_name = config.get("default_dataset_name", "rag-auto-pipeline")
    purpose = config.get("default_purpose", "general")

    results = []
    total_chunks = 0
    total_pairs = 0
    total_approved = 0
    retrain_triggered = False

    for src in source_types:
        run_id = _gen_id()
        conn.execute(
            "INSERT INTO ft_pipeline_runs (id, run_type, source_type, status, created_at) "
            "VALUES (%s, %s, %s, 'running', %s)",
            (run_id, run_type, src, now_iso()),
        )
        conn.commit()

        try:
            # Stage 1: Detect new chunks
            chunks = _detect_new_chunks(conn, src, limit=max_pairs)
            total_chunks += len(chunks)

            if dry_run:
                conn.execute(
                    "UPDATE ft_pipeline_runs SET chunks_processed=%s, status='dry_run', completed_at=%s WHERE id=%s",
                    (len(chunks), now_iso(), run_id),
                )
                conn.commit()
                results.append(
                    {
                        "source_type": src,
                        "run_id": run_id,
                        "chunks_found": len(chunks),
                        "dry_run": True,
                    }
                )
                continue

            if not chunks:
                conn.execute(
                    "UPDATE ft_pipeline_runs SET status='completed', completed_at=%s WHERE id=%s",
                    (now_iso(), run_id),
                )
                conn.commit()
                results.append(
                    {
                        "source_type": src,
                        "run_id": run_id,
                        "chunks_found": 0,
                        "message": "No new chunks",
                    }
                )
                continue

            # Stage 2: Ensure dataset exists
            dataset_id = _ensure_dataset(conn, f"{dataset_name}-{src}", purpose)

            # Stage 3: Generate pairs
            pair_result = _generate_pairs(chunks, dataset_id, purpose)
            pairs_generated = pair_result.get("pairs_generated", 0)
            total_pairs += pairs_generated

            # Stage 4: Auto-approve high-quality pairs
            approved = _auto_approve_pairs(conn, dataset_id, approve_threshold)
            total_approved += approved

            # Stage 5: Check retrain trigger
            retrain_result = _check_and_trigger_retrain(dataset_id)
            if retrain_result.get("retrain_needed"):
                retrain_triggered = True

            # Record completion
            conn.execute(
                """UPDATE ft_pipeline_runs
                   SET chunks_processed=%s, pairs_generated=%s, pairs_approved=%s,
                       retrain_triggered=%s, status='completed', completed_at=%s
                   WHERE id=%s""",
                (
                    len(chunks),
                    pairs_generated,
                    approved,
                    1 if retrain_result.get("retrain_needed") else 0,
                    now_iso(),
                    run_id,
                ),
            )
            conn.commit()

            results.append(
                {
                    "source_type": src,
                    "run_id": run_id,
                    "chunks_found": len(chunks),
                    "pairs_generated": pairs_generated,
                    "pairs_approved": approved,
                    "retrain_needed": retrain_result.get("retrain_needed", False),
                }
            )

        except Exception as exc:
            conn.execute(
                "UPDATE ft_pipeline_runs SET status='failed', error=%s, completed_at=%s WHERE id=%s",
                (str(exc), now_iso(), run_id),
            )
            conn.commit()
            results.append(
                {
                    "source_type": src,
                    "run_id": run_id,
                    "error": str(exc),
                }
            )

    conn.close()

    return {
        "status": "ok",
        "run_type": run_type,
        "dry_run": dry_run,
        "source_types_processed": source_types,
        "total_chunks": total_chunks,
        "total_pairs_generated": total_pairs,
        "total_pairs_approved": total_approved,
        "retrain_triggered": retrain_triggered,
        "source_results": results,
        "completed_at": now_iso(),
    }


def get_pipeline_status() -> Dict[str, Any]:
    """Get recent pipeline run history and statistics."""
    conn = _get_db()
    _ensure_tables(conn)

    try:
        recent = conn.execute(
            """SELECT id, run_type, source_type, chunks_processed, pairs_generated,
                      pairs_approved, retrain_triggered, status, error, created_at, completed_at
               FROM ft_pipeline_runs ORDER BY created_at DESC LIMIT 20""",
        ).fetchall()

        total_runs = conn.execute("SELECT COUNT(*) FROM ft_pipeline_runs").fetchone()[0]
        successful = conn.execute("SELECT COUNT(*) FROM ft_pipeline_runs WHERE status='completed'").fetchone()[0]

        return {
            "status": "ok",
            "total_runs": total_runs,
            "successful_runs": successful,
            "recent_runs": [dict(r) for r in recent],
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="RAG-to-FT automated pipeline (D-KARL-5)",
    )
    parser.add_argument("--run", action="store_true", help="Run the pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Detect chunks but don't generate/approve")
    parser.add_argument("--status", action="store_true", help="Show pipeline status")
    parser.add_argument("--source-type", help="Specific source type to process")
    parser.add_argument("--run-type", default="rag_to_ft", help="Pipeline type")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    if args.status:
        result = get_pipeline_status()
    elif args.run or args.dry_run:
        result = run_pipeline(
            source_type=args.source_type,
            dry_run=args.dry_run,
            run_type=args.run_type,
        )
    else:
        parser.print_help()
        return

    if args.json_output:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
