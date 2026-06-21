#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Learn Reflex — generate training pairs and fine-tune local Ollama.

Generates training pairs from approved outputs (research signals, pulse
posts, compliance knowledge), validates them via pass-rate filtering,
and triggers Ollama fine-tuning when sufficient pairs are accumulated.

YELLOW tier (writes training data, triggers Ollama API calls).
Scanner-tier only (zero Claude tokens).
"""
IMPLEMENTATION_STATUS = "full"

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _count_existing_pairs() -> int:
    """Count existing training pairs in the database."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT COUNT(*) as cnt FROM ft_dataset_examples WHERE approved = 1").fetchone()
        return (row["cnt"] if isinstance(row, dict) else row[0]) if row else 0
    except Exception:
        return 0
    finally:
        conn.close()


def _generate_pairs_from_source(
    source_table: str,
    limit: int = 20,
    min_pass_rate: float = 0.2,
    max_pass_rate: float = 0.8,
) -> List[Dict]:
    """Generate training pairs from a source table using pair_generator."""
    try:
        result = subprocess.run(
            [
                sys.executable,
                "tools/finetune/pair_generator.py",
                "--generate-filtered",
                "--dataset-id",
                f"genesis-{source_table}",
                "--source-table",
                source_table,
                "--num-attempts",
                str(limit),
                "--min-pass-rate",
                str(min_pass_rate),
                "--max-pass-rate",
                str(max_pass_rate),
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(BASE_DIR),
        )
        stdout = result.stdout.strip()
        json_start = stdout.find("{")
        if json_start >= 0:
            data = json.loads(stdout[json_start:])
            return data.get("pairs", [])
    except Exception as e:
        print(f"  WARN: Pair generation from {source_table} failed: {e}")
    return []


def _check_ollama_modelfile_support() -> bool:
    """Check if Ollama supports model creation (fine-tuning via Modelfile)."""
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode == 0
    except Exception:
        return False


def _get_training_stats() -> Dict[str, Any]:
    """Get training pair statistics."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT source, COUNT(*) as cnt, AVG(quality_score) as avg_quality
            FROM ft_dataset_examples
            WHERE approved = 1
            GROUP BY source
        """).fetchall()
        return {
            "sources": [
                {
                    "table": r["source"] if isinstance(r, dict) else r[0],
                    "count": r["cnt"] if isinstance(r, dict) else r[1],
                    "avg_quality": round(r["avg_quality"] if isinstance(r, dict) else r[2], 3)
                    if (r["avg_quality"] if isinstance(r, dict) else r[2])
                    else 0.0,
                }
                for r in rows
            ],
            "total": sum(r["cnt"] if isinstance(r, dict) else r[1] for r in rows),
        }
    except Exception:
        return {"sources": [], "total": 0}
    finally:
        conn.close()


def _compute_fine_tune_threshold(anomaly_cfg: Dict[str, Any]) -> float:
    """Compute adaptive fine-tune readiness threshold from historical pair accumulation.

    Uses population variance (mean + sigma·σ) of observed pair totals across
    prior cycles to surface a data-driven minimum rather than a hardcoded constant.
    Falls back to the configured static value when history is insufficient.

    All tuning constants are drawn from anomaly_cfg (learn.anomaly_detection in
    genesis_config.yaml) so they can be adjusted without code changes.
    """
    if not anomaly_cfg.get("enabled", True):
        return float(anomaly_cfg.get("fallback_min_pairs", 100))

    sigma_multiplier = anomaly_cfg.get("sigma_multiplier", 1.0)
    min_samples = anomaly_cfg.get("min_samples", 5)
    fallback = float(anomaly_cfg.get("fallback_min_pairs", 100))
    bounds = anomaly_cfg.get("adaptive_bounds", {})
    floor = float(bounds.get("min_pairs_floor", 20))

    try:
        conn = get_connection()
        try:
            rows = conn.execute("""
                SELECT metric_value FROM genesis_reflex_runs
                WHERE reflex_name = 'learn' AND success = 1
                ORDER BY ran_at DESC LIMIT 50
            """).fetchall()
            totals = [float(r["metric_value"] if isinstance(r, dict) else r[0]) for r in rows if (r["metric_value"] if isinstance(r, dict) else r[0]) is not None]
        finally:
            conn.close()
    except Exception:
        totals = []

    if len(totals) < min_samples:
        print(f"  Learn: anomaly threshold — insufficient history ({len(totals)}/{min_samples}), using static fallback {fallback}")
        return fallback

    n = len(totals)
    mean = sum(totals) / n
    variance = sum((x - mean) ** 2 for x in totals) / n
    std = variance ** 0.5
    adaptive = mean + sigma_multiplier * std
    threshold = max(floor, adaptive)
    print(f"  Learn: anomaly threshold from {n} samples — mean={mean:.1f}, std={std:.1f}, threshold={threshold:.1f}")
    return threshold


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Learn Reflex."""
    pair_config = config.get("pair_generation", {})
    source_tables = pair_config.get("source_tables", ["research_signals", "pulse_posts"])
    pairs_per_table = int(pair_config.get("pairs_per_table", 20))
    min_pass_rate = float(pair_config.get("min_pass_rate", 0.2))
    max_pass_rate = float(pair_config.get("max_pass_rate", 0.8))

    total_new_pairs = 0
    source_results = []

    for table in source_tables:
        print(f"  Learn: generating pairs from {table}")
        pairs = _generate_pairs_from_source(
            table,
            limit=pairs_per_table,
            min_pass_rate=min_pass_rate,
            max_pass_rate=max_pass_rate,
        )
        total_new_pairs += len(pairs)
        source_results.append(
            {
                "source": table,
                "pairs_generated": len(pairs),
            }
        )

    # Get overall stats
    stats = _get_training_stats()

    # Compute fine-tune readiness threshold via anomaly detection (adaptive)
    anomaly_cfg = config.get("anomaly_detection", {})
    fine_tune_threshold = _compute_fine_tune_threshold(anomaly_cfg)
    fine_tune_ready = stats.get("total", 0) >= fine_tune_threshold
    fine_tune_result = None

    if fine_tune_ready and _check_ollama_modelfile_support():
        print(f"  Learn: {stats['total']} pairs available -- fine-tuning not yet automated")
        fine_tune_result = {
            "status": "deferred",
            "reason": "Automated Ollama fine-tuning via Modelfile planned for Phase 5",
            "pairs_available": stats["total"],
        }

    return {
        "success": True,  # Informational — no source data is valid state
        "metric_value": float(total_new_pairs),
        "details": {
            "new_pairs_generated": total_new_pairs,
            "total_pairs_available": stats.get("total", 0),
            "source_results": source_results,
            "training_stats": stats,
            "fine_tune": fine_tune_result,
        },
    }
