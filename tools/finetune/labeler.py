#!/usr/bin/env python3
# CUI // SP-CTI
"""Fine-tuning labeling backend — multi-dimensional scoring (D-FT-12).

Provides quality/compliance/relevance scoring (1-5 scale) for training
examples. Supports batch approve/reject operations.

Usage:
    python tools/finetune/labeler.py --label --example-id 42 --quality 4 --compliance 5 --relevance 3 --json
    python tools/finetune/labeler.py --batch-approve --dataset-id "ds-xxx" --min-quality 3 --json
    python tools/finetune/labeler.py --batch-reject --dataset-id "ds-xxx" --max-quality 2 --json
    python tools/finetune/labeler.py --unlabeled --dataset-id "ds-xxx" --json
    python tools/finetune/labeler.py --summary --dataset-id "ds-xxx" --json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from tools.db.storage import get_connection
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


def _get_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    conn = get_connection(db_path=str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Label Single Example ─────────────────────────────────────────────


def label_example(
    example_id: int,
    quality_score: float = 0.0,
    compliance_score: float = 0.0,
    relevance_score: float = 0.0,
    approved: bool = False,
    labeled_by: str = "",
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Label a single training example with multi-dimensional scores.

    Scores are 0-5 scale:
        quality:    How well-formed is the Q&A pair?
        compliance: Does it meet CUI/classification standards?
        relevance:  How relevant to the dataset purpose?
    """
    for name, score in [("quality", quality_score), ("compliance", compliance_score), ("relevance", relevance_score)]:
        if not (0.0 <= score <= 5.0):
            return {"success": False, "error": f"{name}_score must be 0-5, got {score}"}

    conn = _get_db(db_path)
    try:
        existing = conn.execute(
            "SELECT id, dataset_id FROM ft_dataset_examples WHERE id = ?",
            (example_id,),
        ).fetchone()
        if not existing:
            return {"success": False, "error": f"Example not found: {example_id}"}

        # Note: We UPDATE scores on the example row (scores are mutable labels,
        # the example content itself is append-only per D6)
        conn.execute(
            """UPDATE ft_dataset_examples
               SET quality_score = ?, compliance_score = ?, relevance_score = ?,
                   approved = ?, labeled_by = ?, labeled_at = ?
               WHERE id = ?""",
            (quality_score, compliance_score, relevance_score, 1 if approved else 0, labeled_by, _now(), example_id),
        )
        conn.commit()
        return {
            "success": True,
            "example_id": example_id,
            "quality_score": quality_score,
            "compliance_score": compliance_score,
            "relevance_score": relevance_score,
            "approved": approved,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


# ── Batch Operations ─────────────────────────────────────────────────


def batch_approve(
    dataset_id: str,
    min_quality: float = 3.0,
    min_compliance: float = 3.0,
    min_relevance: float = 3.0,
    labeled_by: str = "batch_auto",
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Approve all examples meeting minimum score thresholds."""
    conn = _get_db(db_path)
    try:
        cursor = conn.execute(
            """UPDATE ft_dataset_examples
               SET approved = 1, labeled_by = COALESCE(NULLIF(labeled_by, ''), ?), labeled_at = ?
               WHERE dataset_id = ? AND approved = 0
                 AND quality_score >= ? AND compliance_score >= ? AND relevance_score >= ?
                 AND quality_score > 0""",
            (labeled_by, _now(), dataset_id, min_quality, min_compliance, min_relevance),
        )
        count = cursor.rowcount
        conn.commit()
        return {"success": True, "approved_count": count, "dataset_id": dataset_id}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def batch_reject(
    dataset_id: str,
    max_quality: float = 2.0,
    labeled_by: str = "batch_auto",
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Reject (set approved=0) all examples below quality threshold.

    Note: We don't DELETE — examples are append-only. We just mark approved=0.
    """
    conn = _get_db(db_path)
    try:
        cursor = conn.execute(
            """UPDATE ft_dataset_examples
               SET approved = 0, labeled_by = ?, labeled_at = ?
               WHERE dataset_id = ? AND quality_score > 0 AND quality_score <= ?""",
            (labeled_by, _now(), dataset_id, max_quality),
        )
        count = cursor.rowcount
        conn.commit()
        return {"success": True, "rejected_count": count, "dataset_id": dataset_id}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


# ── Queries ──────────────────────────────────────────────────────────


def get_unlabeled(
    dataset_id: str,
    limit: int = 50,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Get examples that haven't been labeled yet."""
    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            """SELECT * FROM ft_dataset_examples
               WHERE dataset_id = ? AND quality_score = 0 AND compliance_score = 0 AND relevance_score = 0
               ORDER BY created_at ASC LIMIT ?""",
            (dataset_id, limit),
        ).fetchall()
        examples = [dict(r) for r in rows]

        total_unlabeled = conn.execute(
            """SELECT COUNT(*) FROM ft_dataset_examples
               WHERE dataset_id = ? AND quality_score = 0 AND compliance_score = 0 AND relevance_score = 0""",
            (dataset_id,),
        ).fetchone()[0]

        return {
            "success": True,
            "examples": examples,
            "count": len(examples),
            "total_unlabeled": total_unlabeled,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def get_labeling_summary(
    dataset_id: str,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Get labeling progress summary for a dataset."""
    conn = _get_db(db_path)
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM ft_dataset_examples WHERE dataset_id = ?",
            (dataset_id,),
        ).fetchone()[0]

        labeled = conn.execute(
            """SELECT COUNT(*) FROM ft_dataset_examples
               WHERE dataset_id = ? AND (quality_score > 0 OR compliance_score > 0 OR relevance_score > 0)""",
            (dataset_id,),
        ).fetchone()[0]

        approved = conn.execute(
            "SELECT COUNT(*) FROM ft_dataset_examples WHERE dataset_id = ? AND approved = 1",
            (dataset_id,),
        ).fetchone()[0]

        scores = conn.execute(
            """SELECT
                 AVG(quality_score) as avg_quality,
                 AVG(compliance_score) as avg_compliance,
                 AVG(relevance_score) as avg_relevance,
                 MIN(quality_score) as min_quality,
                 MAX(quality_score) as max_quality
               FROM ft_dataset_examples
               WHERE dataset_id = ? AND quality_score > 0""",
            (dataset_id,),
        ).fetchone()

        # Score distribution
        dist = {}
        for score_val in range(1, 6):
            cnt = conn.execute(
                "SELECT COUNT(*) FROM ft_dataset_examples WHERE dataset_id = ? AND CAST(quality_score AS INTEGER) = ?",
                (dataset_id, score_val),
            ).fetchone()[0]
            dist[str(score_val)] = cnt

        return {
            "success": True,
            "dataset_id": dataset_id,
            "total_examples": total,
            "labeled": labeled,
            "unlabeled": total - labeled,
            "approved": approved,
            "rejected": labeled - approved,
            "label_progress_pct": round(labeled / total * 100, 1) if total > 0 else 0.0,
            "approval_rate_pct": round(approved / labeled * 100, 1) if labeled > 0 else 0.0,
            "avg_quality": round(scores["avg_quality"] or 0, 2),
            "avg_compliance": round(scores["avg_compliance"] or 0, 2),
            "avg_relevance": round(scores["avg_relevance"] or 0, 2),
            "quality_distribution": dist,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


# ── CLI ───────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Fine-tuning labeling backend")
    parser.add_argument("--label", action="store_true", help="Label a single example")
    parser.add_argument("--batch-approve", action="store_true", help="Batch approve by thresholds")
    parser.add_argument("--batch-reject", action="store_true", help="Batch reject by threshold")
    parser.add_argument("--unlabeled", action="store_true", help="Get unlabeled examples")
    parser.add_argument("--summary", action="store_true", help="Labeling progress summary")

    parser.add_argument("--example-id", type=int, default=0)
    parser.add_argument("--dataset-id", type=str, default="")
    parser.add_argument("--quality", type=float, default=0.0)
    parser.add_argument("--compliance", type=float, default=0.0)
    parser.add_argument("--relevance", type=float, default=0.0)
    parser.add_argument("--approve", action="store_true")
    parser.add_argument("--labeled-by", type=str, default="")
    parser.add_argument("--min-quality", type=float, default=3.0)
    parser.add_argument("--min-compliance", type=float, default=3.0)
    parser.add_argument("--min-relevance", type=float, default=3.0)
    parser.add_argument("--max-quality", type=float, default=2.0)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--json", action="store_true")

    args = parser.parse_args()

    result: Dict[str, Any] = {"success": False, "error": "No action specified"}

    if args.label:
        result = label_example(
            example_id=args.example_id,
            quality_score=args.quality,
            compliance_score=args.compliance,
            relevance_score=args.relevance,
            approved=args.approve,
            labeled_by=args.labeled_by,
        )
    elif args.batch_approve:
        result = batch_approve(
            dataset_id=args.dataset_id,
            min_quality=args.min_quality,
            min_compliance=args.min_compliance,
            min_relevance=args.min_relevance,
            labeled_by=args.labeled_by or "batch_auto",
        )
    elif args.batch_reject:
        result = batch_reject(
            dataset_id=args.dataset_id,
            max_quality=args.max_quality,
            labeled_by=args.labeled_by or "batch_auto",
        )
    elif args.unlabeled:
        result = get_unlabeled(dataset_id=args.dataset_id, limit=args.limit)
    elif args.summary:
        result = get_labeling_summary(dataset_id=args.dataset_id)

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
