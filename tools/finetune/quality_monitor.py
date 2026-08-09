#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Quality Monitor — RAG evaluation feedback loop with retrain triggers (D-KARL-8).

Monitors RAG evaluation metrics (NDCG, MRR, faithfulness) and triggers
fine-tuning retraining when quality degrades below configured thresholds.

Pipeline:
    1. Read recent RAG evaluation results
    2. Compare against baseline thresholds
    3. If N consecutive failures, generate targeted training pairs
    4. Trigger retrain if threshold met

Usage:
    python tools/finetune/quality_monitor.py --check --json
    python tools/finetune/quality_monitor.py --status --json
    python tools/finetune/quality_monitor.py --snapshot --json
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

logger = get_logger("icdev.finetune.quality_monitor")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _load_config() -> Dict[str, Any]:
    """Load quality_feedback config from finetune_config.yaml."""
    config_path = BASE_DIR / "args" / "finetune_config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml

        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("quality_feedback", {})
    except Exception:
        return {}


DEFAULT_CONFIG = {
    "enabled": True,
    "rag_eval_thresholds": {
        "min_ndcg": 0.5,
        "min_mrr": 0.4,
        "min_faithfulness": 0.6,
    },
    "consecutive_failures_before_retrain": 3,
    "target_pairs_per_failing_query": 5,
}


def _get_config() -> Dict[str, Any]:
    cfg = DEFAULT_CONFIG.copy()
    file_cfg = _load_config()
    if file_cfg:
        cfg.update(file_cfg)
    return cfg


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


def _get_db():
    from tools.db.storage import get_connection

    conn = get_connection()
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _gen_id(prefix: str = "qsnap") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _ensure_tables(conn: sqlite3.Connection) -> None:
    """Ensure quality snapshot table exists."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ft_quality_snapshots (
            id TEXT PRIMARY KEY,
            snapshot_type TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            metric_value REAL NOT NULL,
            baseline_value REAL,
            below_threshold INTEGER DEFAULT 0,
            details TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Metric collection
# ---------------------------------------------------------------------------


def _collect_rag_metrics(conn: sqlite3.Connection) -> Dict[str, float]:
    """Collect recent RAG evaluation metrics."""
    metrics: Dict[str, float] = {}

    # Try to get metrics from rag_evaluations or recent retrieval log
    try:
        # Check for evaluation results
        row = conn.execute(
            """SELECT AVG(top_score) as avg_score, COUNT(*) as query_count
               FROM rag_retrieval_log
               WHERE created_at > datetime('now', '-7 days')"""
        ).fetchone()
        if row and row["query_count"] > 0:
            metrics["avg_retrieval_score"] = row["avg_score"] or 0.0
            metrics["query_count"] = row["query_count"]
    except Exception:
        pass

    # Try RAG evaluator results
    try:
        row = conn.execute(
            """SELECT AVG(ndcg_score) as ndcg, AVG(mrr_score) as mrr
               FROM rag_evaluations
               WHERE created_at > datetime('now', '-7 days')"""
        ).fetchone()
        if row:
            if row["ndcg"] is not None:
                metrics["ndcg"] = row["ndcg"]
            if row["mrr"] is not None:
                metrics["mrr"] = row["mrr"]
    except Exception:
        # Table might not exist
        pass

    return metrics


def _record_snapshot(
    conn: sqlite3.Connection,
    metric_name: str,
    metric_value: float,
    baseline: float,
    below: bool,
) -> None:
    """Record a quality snapshot (append-only)."""
    conn.execute(
        """INSERT INTO ft_quality_snapshots
           (id, snapshot_type, metric_name, metric_value, baseline_value, below_threshold, created_at)
           VALUES (%s, 'rag_eval', %s, %s, %s, %s, %s)""",
        (_gen_id(), metric_name, metric_value, baseline, 1 if below else 0, now_iso()),
    )
    conn.commit()


def _count_consecutive_failures(
    conn: sqlite3.Connection,
    metric_name: str,
) -> int:
    """Count consecutive recent snapshots that are below threshold."""
    rows = conn.execute(
        """SELECT below_threshold FROM ft_quality_snapshots
           WHERE metric_name = %s AND snapshot_type = 'rag_eval'
           ORDER BY created_at DESC LIMIT 10""",
        (metric_name,),
    ).fetchall()

    count = 0
    for row in rows:
        if row["below_threshold"]:
            count += 1
        else:
            break
    return count


# ---------------------------------------------------------------------------
# Quality check
# ---------------------------------------------------------------------------


def check_quality(conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """Check current RAG quality against thresholds (D-KARL-8).

    Returns:
        Dict with metric status, alerts, and retrain recommendations.
    """
    config = _get_config()
    if not config.get("enabled", True):
        return {"status": "disabled"}

    thresholds = config.get("rag_eval_thresholds", DEFAULT_CONFIG["rag_eval_thresholds"])
    failure_threshold = config.get("consecutive_failures_before_retrain", 3)

    close_conn = conn is None
    if conn is None:
        conn = _get_db()
    _ensure_tables(conn)

    metrics = _collect_rag_metrics(conn)
    alerts: List[Dict[str, Any]] = []
    retrain_recommended = False

    # Check each metric against thresholds
    metric_checks = {
        "ndcg": thresholds.get("min_ndcg", 0.5),
        "mrr": thresholds.get("min_mrr", 0.4),
        "avg_retrieval_score": thresholds.get("min_faithfulness", 0.6),
    }

    for metric_name, min_value in metric_checks.items():
        current = metrics.get(metric_name)
        if current is None:
            continue

        below = current < min_value
        _record_snapshot(conn, metric_name, current, min_value, below)

        consecutive = _count_consecutive_failures(conn, metric_name)

        if below:
            alert = {
                "metric": metric_name,
                "current": round(current, 4),
                "threshold": min_value,
                "consecutive_failures": consecutive,
                "retrain_recommended": consecutive >= failure_threshold,
            }
            alerts.append(alert)
            if consecutive >= failure_threshold:
                retrain_recommended = True

    if close_conn:
        conn.close()

    return {
        "status": "ok",
        "metrics": metrics,
        "alerts": alerts,
        "retrain_recommended": retrain_recommended,
        "checked_at": now_iso(),
    }


def get_quality_status(conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """Get quality monitoring history and trends."""
    close_conn = conn is None
    if conn is None:
        conn = _get_db()
    _ensure_tables(conn)

    try:
        recent = conn.execute(
            """SELECT id, metric_name, metric_value, baseline_value,
                      below_threshold, created_at
               FROM ft_quality_snapshots
               ORDER BY created_at DESC LIMIT 30"""
        ).fetchall()

        total = conn.execute("SELECT COUNT(*) FROM ft_quality_snapshots").fetchone()[0]
        below_count = conn.execute("SELECT COUNT(*) FROM ft_quality_snapshots WHERE below_threshold = 1").fetchone()[0]

        return {
            "status": "ok",
            "total_snapshots": total,
            "below_threshold_count": below_count,
            "recent_snapshots": [dict(r) for r in recent],
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
    finally:
        if close_conn:
            conn.close()


# ---------------------------------------------------------------------------
# Regression Detection
# ---------------------------------------------------------------------------

# Metrics where higher values are better
_HIGHER_IS_BETTER = {"bleu", "rouge_l", "accuracy", "f1", "pass_rate", "ndcg", "mrr", "faithfulness"}
# Metrics where lower values are better
_LOWER_IS_BETTER = {"perplexity", "loss"}


def detect_regression(
    current_metrics: dict,
    baseline_metrics: dict,
    *,
    threshold_pct: float = 0.05,
) -> dict:
    """Compare current model metrics to baseline and flag regressions.

    Returns dict with has_regression, regressions, improvements, and summary.
    """
    regressions = []
    improvements = []

    for metric, baseline_val in baseline_metrics.items():
        if metric not in current_metrics:
            continue
        current_val = current_metrics[metric]
        if baseline_val == 0:
            continue  # avoid division by zero
        delta_pct = (current_val - baseline_val) / abs(baseline_val)

        if metric in _LOWER_IS_BETTER:
            # Higher delta_pct = worse (value went up)
            if delta_pct > threshold_pct:
                regressions.append({"metric": metric, "delta_pct": round(delta_pct, 6), "direction": "worse"})
            elif delta_pct < -threshold_pct:
                improvements.append({"metric": metric, "delta_pct": round(delta_pct, 6), "direction": "better"})
        else:
            # Default: higher is better (value went down = worse)
            if delta_pct < -threshold_pct:
                regressions.append({"metric": metric, "delta_pct": round(delta_pct, 6), "direction": "worse"})
            elif delta_pct > threshold_pct:
                improvements.append({"metric": metric, "delta_pct": round(delta_pct, 6), "direction": "better"})

    has_regression = bool(regressions)
    if has_regression:
        reg_names = ", ".join(r["metric"] for r in regressions)
        summary = f"Regression detected in: {reg_names}"
    elif improvements:
        imp_names = ", ".join(i["metric"] for i in improvements)
        summary = f"Improvements detected in: {imp_names}"
    else:
        summary = "No significant changes detected"

    return {
        "has_regression": has_regression,
        "regressions": regressions,
        "improvements": improvements,
        "summary": summary,
    }


def compare_jobs(job_id_a: str, job_id_b: str, *, conn=None) -> dict:
    """Load final metrics for two training jobs and return regression report (B vs A).

    job_id_a is the baseline; job_id_b is the candidate being evaluated.
    """
    close_conn = conn is None
    if conn is None:
        conn = _get_db()

    try:
        def _load_metrics(job_id: str) -> dict:
            """Load final metrics JSON from ft_training_jobs."""
            try:
                row = conn.execute(
                    "SELECT final_metrics FROM ft_training_jobs WHERE id = %s",
                    (job_id,),
                ).fetchone()
                if row is None:
                    return {}
                raw = row["final_metrics"] if hasattr(row, "keys") else row[0]
                if not raw:
                    return {}
                return json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                return {}

        metrics_a = _load_metrics(job_id_a)
        metrics_b = _load_metrics(job_id_b)

        report = detect_regression(metrics_b, metrics_a)
        report["job_id_baseline"] = job_id_a
        report["job_id_candidate"] = job_id_b
        report["baseline_metrics"] = metrics_a
        report["candidate_metrics"] = metrics_b
        return report
    finally:
        if close_conn:
            conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Quality Monitor — RAG eval feedback loop (D-KARL-8)",
    )
    parser.add_argument("--check", action="store_true", help="Check current quality")
    parser.add_argument("--status", action="store_true", help="Show quality history")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    if args.check:
        result = check_quality()
    elif args.status:
        result = get_quality_status()
    else:
        parser.print_help()
        return

    if args.json_output:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
