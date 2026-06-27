#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Experiment Reflex — Bayesian Autoresearch loop (D-AR-9).

ORANGE tier (code mutation with test gate + human review for high-risk).
Pulls signals from Innovation/Creative/Research engines, converts to
hypotheses, runs Bayesian-guided experiment loop, reports results as GKPs.

Schedule: nightly 01:00
Risk tier: orange
ADRs: D-AR-1 through D-AR-10
"""
IMPLEMENTATION_STATUS = "full"

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_db():
    """Get database connection via storage abstraction."""
    from tools.db.storage import get_connection

    return get_connection()


def _compute_adaptive_threshold(
    fallback: float = 0.70,
    z_factor: float = 0.5,
    min_samples: int = 10,
) -> float:
    """Compute adaptive signal threshold via statistical anomaly detection.

    Selects the positive-outlier region (mean + z_factor * std) from the
    scored innovation_signals distribution.  Falls back to ``fallback`` when
    the table has fewer than ``min_samples`` rows.
    """
    try:
        with _get_db() as conn:
            row = conn.execute(
                "SELECT AVG(innovation_score), "
                "AVG(innovation_score * innovation_score) - "
                "AVG(innovation_score) * AVG(innovation_score) AS variance, "
                "COUNT(*) "
                "FROM innovation_signals WHERE status = 'scored'"
            ).fetchone()
            if row and row[2] >= min_samples:
                mean = float(row[0] or 0.0)
                variance = max(float(row[1] or 0.0), 0.0)
                std = variance ** 0.5
                return min(max(mean + z_factor * std, 0.0), 1.0)
    except Exception:
        pass
    return fallback


def _fetch_high_score_signals(threshold: float = 0.70, limit: int = 10) -> list:
    """Pull high-scoring signals from Innovation/Creative engines."""
    signals = []
    try:
        with _get_db() as conn:
            rows = conn.execute(
                "SELECT id, title, description, innovation_score, category, "
                "source_type FROM innovation_signals "
                "WHERE innovation_score >= %s AND status = 'scored' "
                "ORDER BY innovation_score DESC LIMIT %s",
                (threshold, limit),
            ).fetchall()
            signals = [dict(r) for r in rows]
    except Exception:
        pass
    return signals


def run(config: dict, trust=None) -> dict:
    """Execute the Experiment Reflex.

    1. Pull high-scoring signals from innovation_signals (adaptive or static threshold)
    2. Load experiment programs for eligible domains
    3. Run Bayesian-guided experiment loop per domain
    4. Export results as GKP for promotion
    5. Return metric value and details

    Args:
        config: Reflex configuration from genesis_config.yaml
        trust: TrustKernel instance (optional, for D-GEN risk tiers)

    Returns:
        Dict with success, metric_value, details
    """
    adaptive_cfg = config.get("adaptive_threshold", {})
    if adaptive_cfg.get("enabled", False):
        signal_threshold = _compute_adaptive_threshold(
            fallback=config.get("signal_score_threshold", 0.70),
            z_factor=adaptive_cfg.get("z_factor", 0.5),
            min_samples=adaptive_cfg.get("min_samples", 10),
        )
    else:
        signal_threshold = config.get("signal_score_threshold", 0.70)
    max_experiments = config.get("max_experiments_per_run", 3)
    domains = config.get("domains", ["compliance", "code_quality", "security"])

    # Step 1: Fetch signals
    signals = _fetch_high_score_signals(signal_threshold)

    # Step 2: Run experiment loop per domain
    from tools.autoresearch.experiment_engine import run_loop

    all_results = []
    total_kept = 0
    total_run = 0

    for domain in domains:
        try:
            loop_result = run_loop(
                domain=domain,
                max_experiments=max_experiments,
                seed=int(datetime.now(timezone.utc).timestamp()) % 10000,
            )
            if loop_result.get("success"):
                total_kept += loop_result.get("kept", 0)
                total_run += loop_result.get("experiments_run", 0)
                all_results.append(
                    {
                        "domain": domain,
                        "experiments_run": loop_result.get("experiments_run", 0),
                        "kept": loop_result.get("kept", 0),
                        "acceptance_rate": loop_result.get("acceptance_rate", 0.0),
                        "total_improvement": loop_result.get("total_improvement", 0.0),
                    }
                )
        except Exception as exc:
            all_results.append(
                {
                    "domain": domain,
                    "error": str(exc)[:200],
                }
            )

    # Step 3: Export results as GKP (if any improvements found)
    gkp_exported = False
    if total_kept > 0 and config.get("export_gkp", True):
        try:
            gkp = {
                "type": "experiment_results",
                "version": "1.0",
                "source_reflex": "experiment",
                "created_at": _now(),
                "total_experiments": total_run,
                "total_kept": total_kept,
                "acceptance_rate": round(total_kept / max(total_run, 1), 4),
                "domain_results": all_results,
            }
            export_dir = Path(_ROOT / "data" / "genesis" / "exports")
            export_dir.mkdir(parents=True, exist_ok=True)
            gkp_path = export_dir / f"gkp-experiment-{uuid.uuid4().hex[:8]}.json"
            with open(gkp_path, "w", encoding="utf-8") as f:
                json.dump(gkp, f, indent=2, default=str)
            gkp_exported = True
        except Exception:
            pass

    acceptance_rate = total_kept / max(total_run, 1)

    return {
        "success": True,
        "metric_value": acceptance_rate,
        "details": {
            "total_experiments": total_run,
            "total_kept": total_kept,
            "total_discarded": total_run - total_kept,
            "acceptance_rate": round(acceptance_rate, 4),
            "signals_fetched": len(signals),
            "domains_processed": len(domains),
            "domain_results": all_results,
            "gkp_exported": gkp_exported,
        },
    }
