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


#: The run-level verdicts. `unmeasurable` is its own verdict and never folds
#: into `ok` -- that conflation is the whole defect this card fixes.
STATUS_DISABLED = "disabled"
STATUS_UNMEASURABLE = "unmeasurable"
STATUS_OK = "ok"


def _rate(numerator: int, denominator: int):
    """Acceptance rate, or None over an empty denominator.

    ``kept / max(run, 1)`` returned a confident 0.0 for a run that executed no
    experiments at all, so a measured "we tried and nothing was accepted" and
    "nothing was tried" rendered identically. args/perfect_score_gate.yaml is
    ratcheted to 0 for exactly this shape at the other end of the scale.
    """
    if not denominator:
        return None
    return round(numerator / denominator, 4)


def run(config: dict, trust=None) -> dict:
    """Execute the Experiment Reflex.

    0. Honour the master switch (args/autoresearch_config.yaml -> ``enabled``,
       ``ICDEV_AUTORESEARCH_ENABLED``). Off means the loop is NOT run and no
       counts are reported.
    1. Pull high-scoring signals from innovation_signals (adaptive or static)
    2. Load experiment programs for eligible domains
    3. Run the Bayesian-guided experiment loop per domain
    4. Export results as a GKP for promotion -- ONLY from a measured run
    5. Return metric value and details

    WHAT THIS REFLEX REFUSES TO CLAIM (xrv-lab-01).
    ``experiment_engine.run_loop`` evaluates the domain, creates an experiment,
    runs it, evaluates AGAIN with nothing changed, and decides keep/discard on
    that delta -- which is why every result it returns carries
    ``placeholder_metrics: True`` and a note saying the baseline is an IDENTITY
    baseline. The reflex reported an ``acceptance_rate`` off those deltas
    anyway, and that number was indistinguishable on every surface from one
    measured against a real modification. A run built on placeholder metrics is
    now ``unmeasurable``: ``metric_value`` is None, ``total_kept`` /
    ``total_discarded`` are None, and NO GKP is exported -- promoting a
    knowledge packet derived from an identity baseline is how a measurement
    nobody made becomes a durable artifact.

    Args:
        config: Reflex configuration from genesis_config.yaml
        trust: TrustKernel instance (optional, for D-GEN risk tiers)

    Returns:
        Dict with success, metric_value, details
    """
    # -- Step 0: master switch ----------------------------------------------
    # Read through the engine's own gate so there is ONE reading of the switch.
    try:
        from tools.autoresearch.experiment_engine import autoresearch_enabled

        gate = autoresearch_enabled()
    except Exception as exc:  # noqa: BLE001 - an unreadable switch is not consent
        return {
            "success": True,
            "metric_value": None,
            "details": {
                "status": STATUS_DISABLED,
                "reason": "switch_unreadable",
                "error": str(exc)[:200],
                "total_experiments": None,
                "total_kept": None,
                "total_discarded": None,
                "acceptance_rate": None,
                "gkp_exported": False,
            },
        }

    if not gate.get("enabled"):
        # No loop run, no counts, no rate -- and success True, so an opt-in
        # feature being off never trips the daemon circuit breaker.
        return {
            "success": True,
            "metric_value": None,
            "details": {
                "status": STATUS_DISABLED,
                "reason": f"autoresearch disabled ({gate.get('basis')})",
                "switch": gate,
                "total_experiments": None,
                "total_kept": None,
                "total_discarded": None,
                "acceptance_rate": None,
                "gkp_exported": False,
            },
        }

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

    # Step 2: Run the experiment loop per domain
    from tools.autoresearch.experiment_engine import run_loop

    all_results = []
    measured_kept = 0
    measured_run = 0
    #: Domains split by whether their result is a MEASUREMENT. Never merged: a
    #: total summed across a measured and an unmeasured domain is not a total.
    measured_domains = []
    placeholder_domains = []
    failed_domains = []
    placeholder_note = None

    for domain in domains:
        try:
            loop_result = run_loop(
                domain=domain,
                max_experiments=max_experiments,
                seed=int(datetime.now(timezone.utc).timestamp()) % 10000,
            )
            if not loop_result.get("success"):
                failed_domains.append(domain)
                all_results.append(
                    {
                        "domain": domain,
                        "measured": False,
                        "reason": str(loop_result.get("error") or "loop reported failure")[:200],
                    }
                )
                continue

            if loop_result.get("placeholder_metrics"):
                placeholder_domains.append(domain)
                placeholder_note = placeholder_note or loop_result.get("placeholder_note")
                all_results.append(
                    {
                        "domain": domain,
                        "measured": False,
                        "reason": "placeholder_metrics",
                        "experiments_run": loop_result.get("experiments_run", 0),
                        # Deliberately NOT `kept`: the engine's keep/discard
                        # decision came off an identity baseline, so reporting it
                        # under the key a measured run uses is the conflation
                        # this reflex exists to refuse.
                        "kept_unmeasured": loop_result.get("kept", 0),
                        "acceptance_rate": None,
                    }
                )
                continue

            measured_domains.append(domain)
            measured_kept += loop_result.get("kept", 0)
            measured_run += loop_result.get("experiments_run", 0)
            all_results.append(
                {
                    "domain": domain,
                    "measured": True,
                    "experiments_run": loop_result.get("experiments_run", 0),
                    "kept": loop_result.get("kept", 0),
                    "acceptance_rate": loop_result.get("acceptance_rate"),
                    "total_improvement": loop_result.get("total_improvement", 0.0),
                }
            )
        except Exception as exc:
            failed_domains.append(domain)
            all_results.append(
                {
                    "domain": domain,
                    "measured": False,
                    "error": str(exc)[:200],
                }
            )

    # -- Verdict -------------------------------------------------------------
    # Three outcomes, and two of them report NO number. A run that mixed a
    # measured domain with a placeholder one is unmeasurable as a WHOLE: the
    # run-level rate would carry a denominator that is part fiction.
    unmeasurable_reason = None
    if placeholder_domains:
        unmeasurable_reason = "placeholder_metrics"
    elif not measured_domains:
        unmeasurable_reason = "no_measured_domain"
    elif measured_run == 0:
        unmeasurable_reason = "no_experiments_run"

    details = {
        "signals_fetched": len(signals),
        "domains_processed": len(domains),
        "measured_domains": measured_domains,
        "placeholder_domains": placeholder_domains,
        "failed_domains": failed_domains,
        "domain_results": all_results,
        "switch": gate,
    }
    if placeholder_note:
        details["placeholder_note"] = placeholder_note

    if unmeasurable_reason:
        details.update(
            {
                "status": STATUS_UNMEASURABLE,
                "reason": unmeasurable_reason,
                "total_experiments": None,
                "total_kept": None,
                "total_discarded": None,
                "acceptance_rate": None,
                "gkp_exported": False,
            }
        )
        return {"success": True, "metric_value": None, "details": details}

    acceptance_rate = _rate(measured_kept, measured_run)

    # Step 3: Export results as a GKP -- only from a MEASURED run with a keep.
    gkp_exported = False
    if measured_kept > 0 and config.get("export_gkp", True):
        try:
            gkp = {
                "type": "experiment_results",
                "version": "1.0",
                "source_reflex": "experiment",
                "created_at": _now(),
                "total_experiments": measured_run,
                "total_kept": measured_kept,
                "acceptance_rate": acceptance_rate,
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

    details.update(
        {
            "status": STATUS_OK,
            "total_experiments": measured_run,
            "total_kept": measured_kept,
            "total_discarded": measured_run - measured_kept,
            "acceptance_rate": acceptance_rate,
            "gkp_exported": gkp_exported,
        }
    )

    return {
        "success": True,
        "metric_value": acceptance_rate,
        "details": details,
    }
