#!/usr/bin/env python3
# CUI // SP-CTI
"""Quality Feedback Loop — closed-loop RAG quality → retrain pipeline (D-KARL-9).

Orchestrates: quality_monitor.check_quality() → statement_extractor → pair_generator
→ retrain_trigger. When RAG quality degrades, automatically generates targeted
training pairs using statement extraction (grounded, low hallucination) and
triggers retraining if thresholds are met.

Usage:
    python tools/rag/quality_feedback_loop.py --run --json
    python tools/rag/quality_feedback_loop.py --dry-run --json
    python tools/rag/quality_feedback_loop.py --status --json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.rag.quality_feedback_loop")


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


# ---------------------------------------------------------------------------
# Module-level fallback constants — all overridable from
# args/finetune_config.yaml under quality_feedback (+ .anomaly_detection).
# Change config, not code.
# ---------------------------------------------------------------------------
_MAX_AUTO_PAIRS_PER_CYCLE = 50   # cap on generated pairs per remediation cycle
_MIN_PAIRS_PER_SOURCE     = 5    # floor on per-source generation budget
_ID_HEX_LEN               = 12   # hex chars in generated cycle/object ids

# Anomaly-detection quality floors — a cycle whose RAG metric scores below
# these floors is flagged as a regression.  When enough history exists they
# are recomputed adaptively as (mean − k·stddev) of past snapshots; otherwise
# these module floors are used.  Keys map to ft_quality_snapshots.metric_name.
_METRIC_ANOMALY_FLOORS = {
    "ndcg": 0.30,
    "mrr": 0.30,
    "avg_retrieval_score": 0.30,
}
_ANOMALY_STDDEV_K = 2.0          # flag metrics below mean − k·stddev of history


DEFAULT_CONFIG = {
    "auto_remediate": True,
    "use_statement_extraction": True,
    "taxonomy_aware_generation": True,
    "max_auto_pairs_per_cycle": _MAX_AUTO_PAIRS_PER_CYCLE,
    "target_dataset_name": "rag-quality-remediation",
    "target_purpose": "general",
}


def _get_config() -> Dict[str, Any]:
    cfg = DEFAULT_CONFIG.copy()
    file_cfg = _load_config()
    if file_cfg:
        cfg.update(file_cfg)
    return cfg


def _get_anomaly_config() -> Dict[str, Any]:
    """Load quality_feedback.anomaly_detection settings (config over code)."""
    return _get_config().get("anomaly_detection", {}) or {}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gen_id(prefix: str = "qfl") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:_ID_HEX_LEN]}"


# ---------------------------------------------------------------------------
# Adaptive anomaly detection
# ---------------------------------------------------------------------------


def _compute_feedback_anomaly_thresholds(anomaly_cfg: "dict | None" = None) -> Dict[str, Any]:
    """Compute adaptive RAG quality floors from historical snapshots.

    Reads metric_value history from ft_quality_snapshots and derives a
    statistical lower bound (mean − k·stddev) per metric, so the definition of
    "degraded" tracks observed quality instead of a frozen magic number.

    Population stddev is computed via E[x²] − E[x]² because SQLite has no
    STDDEV().  Floors are bounded by ``adaptive_bounds`` and fall back to the
    module floors when fewer than ``min_samples`` rows exist for a metric or
    detection is disabled.
    """
    cfg = anomaly_cfg or {}
    fallback_floors = cfg.get("fallback_floors", {}) or {}
    floors: Dict[str, float] = {
        m: float(fallback_floors.get(m, default)) for m, default in _METRIC_ANOMALY_FLOORS.items()
    }
    result: Dict[str, Any] = {"floors": floors, "computed": False}
    if not cfg.get("enabled", True):
        return result

    min_samples = int(cfg.get("min_samples", 30))
    stddev_k    = float(cfg.get("stddev_k", _ANOMALY_STDDEV_K))
    bounds      = cfg.get("adaptive_bounds", {}) or {}
    floor_min   = float(bounds.get("floor_min", 0.05))
    floor_max   = float(bounds.get("floor_max", 0.80))

    conn = None
    try:
        from tools.db.storage import get_connection

        conn = get_connection()
        computed: Dict[str, float] = {}
        for metric in _METRIC_ANOMALY_FLOORS:
            # Population mean / stddev via E[x²] − E[x]² (SQLite has no STDDEV()).
            row = conn.execute(
                "SELECT AVG(metric_value) AS mean, "
                "AVG(metric_value * metric_value) AS sq, COUNT(*) AS n "
                "FROM ft_quality_snapshots "
                "WHERE metric_name = %s AND metric_value IS NOT NULL",
                (metric,),
            ).fetchone()
            if not row:
                continue
            n = row["n"] if isinstance(row, dict) else row[2]
            if not n or n < min_samples:
                continue
            mean = float((row["mean"] if isinstance(row, dict) else row[0]) or 0.0)
            sq   = float((row["sq"] if isinstance(row, dict) else row[1]) or 0.0)
            std  = math.sqrt(max(0.0, sq - mean * mean))
            computed[metric] = round(max(floor_min, min(floor_max, mean - stddev_k * std)), 3)
        if computed:
            floors.update(computed)
            result["computed"] = True
            result["computed_metrics"] = sorted(computed)
    except Exception:
        pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    result["floors"] = floors
    return result


def flag_quality_anomaly(
    metrics: Dict[str, Any],
    thresholds: "dict | None" = None,
) -> Dict[str, Any]:
    """Flag a quality-metrics snapshot as anomalous against adaptive floors.

    Returns ``{"anomalous": bool, "reasons": [...], "floors": {...}}`` — clean
    when every present metric meets or exceeds its (adaptive) floor.
    """
    th = thresholds or _compute_feedback_anomaly_thresholds(_get_anomaly_config())
    floors = th.get("floors", {})
    reasons: List[str] = []
    for metric, floor in floors.items():
        value = metrics.get(metric)
        if value is None:
            continue
        try:
            if float(value) < float(floor):
                reasons.append(f"{metric} {round(float(value), 4)} < floor {floor}")
        except (TypeError, ValueError):
            continue
    return {"anomalous": bool(reasons), "reasons": reasons, "floors": floors}


# ---------------------------------------------------------------------------
# Feedback Loop
# ---------------------------------------------------------------------------


def run_feedback_cycle(
    dry_run: bool = False,
    conn: Any = None,
) -> Dict[str, Any]:
    """Run one feedback cycle: check quality → generate pairs → trigger retrain.

    Args:
        dry_run: If True, check quality and report but do not generate pairs.
        conn: Optional DB connection.

    Returns:
        Dict with cycle results, actions taken, and pair generation stats.
    """
    config = _get_config()
    cycle_id = _gen_id("cycle")
    result: Dict[str, Any] = {
        "cycle_id": cycle_id,
        "started_at": _now(),
        "dry_run": dry_run,
        "actions": [],
        "pairs_generated": 0,
        "retrain_triggered": False,
    }

    # Step 1: Check current quality
    try:
        from tools.finetune.quality_monitor import check_quality

        quality = check_quality(conn=conn)
    except ImportError:
        result["error"] = "quality_monitor not available"
        return result
    except Exception as exc:
        result["error"] = f"quality check failed: {exc}"
        return result

    result["quality_status"] = quality.get("status", "unknown")
    result["metrics"] = quality.get("metrics", {})
    result["alerts"] = quality.get("alerts", [])
    result["retrain_recommended"] = quality.get("retrain_recommended", False)

    # Adaptive anomaly detection: flag metric regressions vs history-derived floors
    anomaly = flag_quality_anomaly(quality.get("metrics", {}))
    result["anomaly_detection"] = anomaly
    if anomaly["anomalous"]:
        result["actions"].append("quality_anomaly_detected")

    # Step 2: If no issues, return early
    if not quality.get("retrain_recommended", False):
        result["actions"].append("no_action_needed")
        result["completed_at"] = _now()
        return result

    if dry_run:
        result["actions"].append("dry_run_skip_generation")
        result["completed_at"] = _now()
        return result

    if not config.get("auto_remediate", True):
        result["actions"].append("auto_remediate_disabled")
        result["completed_at"] = _now()
        return result

    # Step 3: Identify failing areas from alerts
    failing_metrics = [a["metric"] for a in quality.get("alerts", []) if a.get("retrain_recommended", False)]
    result["failing_metrics"] = failing_metrics

    # Step 4: Generate targeted training pairs
    max_pairs = config.get("max_auto_pairs_per_cycle", 50)
    use_statement = config.get("use_statement_extraction", True)
    dataset_name = config.get("target_dataset_name", "rag-quality-remediation")
    purpose = config.get("target_purpose", "general")

    total_generated = 0

    if use_statement:
        total_generated = _generate_with_statement_extraction(
            dataset_name=dataset_name,
            purpose=purpose,
            max_pairs=max_pairs,
        )
        result["actions"].append("statement_extraction_generation")
    else:
        total_generated = _generate_with_pair_generator(
            dataset_name=dataset_name,
            purpose=purpose,
            max_pairs=max_pairs,
        )
        result["actions"].append("pair_generator_generation")

    result["pairs_generated"] = total_generated

    # Step 5: Check if retrain threshold met
    if total_generated > 0:
        retrain_triggered = _try_trigger_retrain(dataset_name)
        result["retrain_triggered"] = retrain_triggered
        if retrain_triggered:
            result["actions"].append("retrain_triggered")
        else:
            result["actions"].append("pairs_generated_retrain_pending")
    else:
        result["actions"].append("no_pairs_generated")

    result["completed_at"] = _now()
    return result


def _generate_with_statement_extraction(
    dataset_name: str,
    purpose: str,
    max_pairs: int,
) -> int:
    """Generate pairs using statement extraction (Know Your RAG method)."""
    try:
        from tools.finetune.statement_extractor import generate_from_rag_source
        from tools.finetune.dataset_manager import ensure_dataset

        dataset_id = ensure_dataset(name=dataset_name, purpose=purpose).get("dataset_id", "")
        if not dataset_id:
            return 0

        # Generate from recent RAG sources
        sources = ["compliance_artifacts", "innovation_signals", "research_dossiers"]
        total = 0
        min_per_source = int(_get_config().get("min_pairs_per_source", _MIN_PAIRS_PER_SOURCE))
        per_source = max(max_pairs // len(sources), min_per_source)

        for source in sources:
            try:
                gen_result = generate_from_rag_source(
                    dataset_id=dataset_id,
                    source_table=source,
                    limit=per_source,
                )
                total += gen_result.get("pairs_added", 0)
            except Exception:
                continue

            if total >= max_pairs:
                break

        return total
    except ImportError:
        logger.debug("statement_extractor not available, falling back")
        return 0
    except Exception as exc:
        logger.warning("Statement extraction failed: %s", exc)
        return 0


def _generate_with_pair_generator(
    dataset_name: str,
    purpose: str,
    max_pairs: int,
) -> int:
    """Generate pairs using standard pair generator."""
    try:
        from tools.finetune.pair_generator import generate_from_rag_source
        from tools.finetune.dataset_manager import ensure_dataset

        dataset_id = ensure_dataset(name=dataset_name, purpose=purpose).get("dataset_id", "")
        if not dataset_id:
            return 0

        result = generate_from_rag_source(
            dataset_id=dataset_id,
            source_table="compliance_artifacts",
            purpose=purpose,
            limit=max_pairs,
        )
        return result.get("pairs_added", 0)
    except ImportError:
        return 0
    except Exception as exc:
        logger.warning("Pair generation failed: %s", exc)
        return 0


def _try_trigger_retrain(dataset_name: str) -> bool:
    """Attempt to trigger retrain via retrain_trigger.py."""
    try:
        from tools.finetune.retrain_trigger import check_and_trigger

        result = check_and_trigger()
        return result.get("triggered", False)
    except ImportError:
        return False
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def get_feedback_status() -> Dict[str, Any]:
    """Get current feedback loop status."""
    try:
        from tools.finetune.quality_monitor import check_quality, get_quality_status

        quality = check_quality()
        history = get_quality_status()
        config = _get_config()
        anomaly = flag_quality_anomaly(quality.get("metrics", {}))

        return {
            "status": "ok",
            "auto_remediate": config.get("auto_remediate", True),
            "use_statement_extraction": config.get("use_statement_extraction", True),
            "current_quality": quality,
            "anomaly_detection": anomaly,
            "history_snapshots": history.get("total_snapshots", 0),
            "below_threshold_count": history.get("below_threshold_count", 0),
            "checked_at": _now(),
        }
    except ImportError:
        return {"status": "unavailable", "error": "quality_monitor not available"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Quality Feedback Loop — closed-loop RAG quality (D-KARL-9)",
    )
    parser.add_argument("--run", action="store_true", help="Run feedback cycle")
    parser.add_argument("--dry-run", action="store_true", help="Check quality without generating pairs")
    parser.add_argument("--status", action="store_true", help="Show feedback loop status")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    if args.run:
        result = run_feedback_cycle(dry_run=False)
    elif args.dry_run:
        result = run_feedback_cycle(dry_run=True)
    elif args.status:
        result = get_feedback_status()
    else:
        parser.print_help()
        return

    if args.json_output:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
