#!/usr/bin/env python3
# CUI // SP-CTI
"""Bayesian Autoresearch Engine — autonomous experiment loop (D-AR-1).

Adapts Karpathy's Autoresearch pattern with ICDEV™'s Bayesian Teaching
Intelligence for self-improving experiments across compliance, code quality,
security, RAG, Pulse, and skill domains.

The Karpathy Loop:
  HYPOTHESIZE → MODIFY → RUN (time-boxed) → EVALUATE → KEEP/DISCARD → REPEAT

Usage:
    python tools/autoresearch/experiment_engine.py --create --domain compliance --hypothesis "..." --json
    python tools/autoresearch/experiment_engine.py --run --experiment-id "exp-xxx" --json
    python tools/autoresearch/experiment_engine.py --evaluate --experiment-id "exp-xxx" --json
    python tools/autoresearch/experiment_engine.py --decide --experiment-id "exp-xxx" --json
    python tools/autoresearch/experiment_engine.py --loop --domain compliance --max-experiments 5 --json
    python tools/autoresearch/experiment_engine.py --status --json
    python tools/autoresearch/experiment_engine.py --health --json
"""

import argparse
import hashlib
import json
import subprocess
import sys
import uuid
from pathlib import Path
# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_ROOT = Path(__file__).resolve().parent.parent.parent

from tools.common.helpers import now_iso
from tools.db.storage import sql_placeholder
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.autoresearch.experiment_engine")

# Honesty flag: this engine does NOT apply real code modifications between the
# pre- and post-metric measurements (that requires the Genesis reflex or manual
# invocation). As a result pre/post metrics are measured against an *identity*
# baseline and the resulting deltas are placeholder/heuristic, not the product
# of a real experiment evaluation. Every payload that exposes these metrics
# carries these flags so downstream callers and the UI never mistake them for
# real experiment results.
_PLACEHOLDER_METRICS_NOTE = (
    "Metrics are measured against an identity baseline — the engine does not "
    "apply real code modifications between pre/post measurement, so deltas are "
    "placeholder/heuristic, not real experiment evaluations."
)


def _uuid() -> str:
    return f"exp-{uuid.uuid4().hex[:12]}"


def _result_uuid() -> str:
    return f"er-{uuid.uuid4().hex[:12]}"


def _score_uuid() -> str:
    return f"bes-{uuid.uuid4().hex[:12]}"


def _get_db():
    """Get database connection via storage abstraction."""
    from tools.db.storage import get_connection

    return get_connection()


def _load_config() -> dict:
    """Load autoresearch config."""
    config_path = _ROOT / "args" / "autoresearch_config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml

        with open(config_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        return {}


def _load_program(domain: str) -> dict:
    """Load experiment program config for a domain."""
    program_path = _ROOT / "args" / "experiment_programs" / f"{domain}.yaml"
    if not program_path.exists():
        return {}
    try:
        import yaml

        with open(program_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        return {"domain": domain}


def _audit(event_type: str, action: str, details: dict = None, project_id: str = "autoresearch"):
    """Append to audit trail."""
    try:
        with _get_db() as conn:
            ph = sql_placeholder(conn)
            conn.execute(
                "INSERT INTO audit_trail (id, event_type, actor, action, "
                "details, project_id, session_id, created_at) "
                f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})",
                (
                    f"at-{uuid.uuid4().hex[:12]}",
                    event_type,
                    "autoresearch-engine",
                    action,
                    json.dumps(details or {}, default=str),
                    project_id,
                    "autoresearch",
                    now_iso(),
                ),
            )
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        # Best-effort audit
        logger.warning("_audit: best-effort INSERT into audit_trail failed (non-blocking): %s", exc)


# ── Table Initialization ─────────────────────────────────────────────────────


def ensure_tables() -> None:
    """Create autoresearch tables if they do not exist."""
    try:
        with _get_db() as conn:
            # Quick check — if experiment_candidates exists, skip
            conn.execute("SELECT 1 FROM experiment_candidates LIMIT 1")
    except Exception:
        # Tables don't exist — init_icdev_db should have created them
        # Run init if needed
        try:
            subprocess.run(
                [sys.executable, str(_ROOT / "tools" / "db" / "init_icdev_db.py")],
                capture_output=True,
                timeout=30,
                stdin=subprocess.DEVNULL,
            )
        except Exception:
            pass


# ── CRUD ─────────────────────────────────────────────────────────────────────


def create_experiment(
    domain: str,
    hypothesis: str,
    modifications: dict = None,
    category: str = None,
    source: str = "manual",
    signal_id: str = None,
) -> dict:
    """Register a new experiment candidate."""
    ensure_tables()
    exp_id = _uuid()
    content_hash = hashlib.sha256(hypothesis.encode("utf-8")).hexdigest()[:16]
    now = now_iso()

    try:
        with _get_db() as conn:
            ph = sql_placeholder(conn)
            conn.execute(
                "INSERT INTO experiment_candidates "
                "(id, domain, hypothesis, category, modifications, source, "
                "signal_id, status, content_hash, created_at, updated_at) "
                f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})",
                (
                    exp_id,
                    domain,
                    hypothesis,
                    category,
                    json.dumps(modifications or {}, default=str),
                    source,
                    signal_id,
                    "created",
                    content_hash,
                    now,
                    now,
                ),
            )
    except Exception as exc:
        return {"success": False, "error": str(exc)}

    _audit(
        "experiment.created",
        f"Created experiment {exp_id}",
        {
            "experiment_id": exp_id,
            "domain": domain,
            "hypothesis": hypothesis[:200],
        },
    )

    return {
        "success": True,
        "experiment_id": exp_id,
        "domain": domain,
        "hypothesis": hypothesis,
        "category": category,
        "status": "created",
        "content_hash": content_hash,
        "created_at": now,
    }


def get_experiment(experiment_id: str) -> dict:
    """Get experiment by ID."""
    try:
        with _get_db() as conn:
            ph = sql_placeholder(conn)
            row = conn.execute(
                f"SELECT * FROM experiment_candidates WHERE id = {ph}",
                (experiment_id,),
            ).fetchone()
            if row:
                return dict(row)
    except Exception:
        pass
    return {}


# ── Execution ────────────────────────────────────────────────────────────────


def run_experiment(
    experiment_id: str,
    time_budget_seconds: int = None,
) -> dict:
    """Execute an experiment in a time-boxed sandbox.

    1. Load experiment + program config
    2. Measure pre-metric (baseline)
    3. Apply modifications (placeholder — in real usage, LLM applies code changes)
    4. Measure post-metric
    5. Record duration

    Note: Full git worktree + code modification requires the Genesis reflex
    or manual invocation. This engine handles the measurement + decision loop.
    """
    exp = get_experiment(experiment_id)
    if not exp:
        return {"success": False, "error": f"Experiment not found: {experiment_id}"}

    domain = exp["domain"]
    program = _load_program(domain)
    config = _load_config()

    if time_budget_seconds is None:
        time_budget_seconds = program.get(
            "time_budget_seconds",
            config.get("defaults", {}).get("time_budget_seconds", 300),
        )

    # Update status to running
    try:
        with _get_db() as conn:
            ph = sql_placeholder(conn)
            conn.execute(
                f"UPDATE experiment_candidates SET status = {ph}, updated_at = {ph} WHERE id = {ph}",
                ("running", now_iso(), experiment_id),
            )
    except Exception:
        pass

    # Measure pre-metric
    from tools.autoresearch.fitness_evaluator import evaluate

    pre_eval = evaluate(domain)
    pre_metric = pre_eval.get("metric_value", 0.0)

    _audit(
        "experiment.started",
        f"Running experiment {experiment_id}",
        {
            "experiment_id": experiment_id,
            "domain": domain,
            "pre_metric": pre_metric,
            "time_budget": time_budget_seconds,
        },
    )

    return {
        "success": True,
        "experiment_id": experiment_id,
        "domain": domain,
        "status": "running",
        "pre_metric": pre_metric,
        "time_budget_seconds": time_budget_seconds,
        "placeholder_metrics": True,
        "heuristic": True,
        "placeholder_note": _PLACEHOLDER_METRICS_NOTE,
    }


def evaluate_experiment(experiment_id: str) -> dict:
    """Measure metric delta and compute improvement score."""
    exp = get_experiment(experiment_id)
    if not exp:
        return {"success": False, "error": f"Experiment not found: {experiment_id}"}

    domain = exp["domain"]
    program = _load_program(domain)

    # Measure current metric
    from tools.autoresearch.fitness_evaluator import evaluate

    post_eval = evaluate(domain)
    post_metric = post_eval.get("metric_value", 0.0)

    # Get pre-metric from most recent run result or re-evaluate
    pre_metric = post_metric  # Placeholder — in real loop, pre-metric is cached
    try:
        with _get_db() as conn:
            ph = sql_placeholder(conn)
            row = conn.execute(
                f"SELECT pre_metric FROM experiment_results WHERE experiment_id = {ph} ORDER BY created_at DESC LIMIT 1",
                (experiment_id,),
            ).fetchone()
            if row:
                pre_metric = row["pre_metric"]
    except Exception:
        pass

    metric_delta = post_metric - pre_metric
    direction = program.get("objective", {}).get("direction", "maximize")
    if direction == "minimize":
        metric_delta = -metric_delta

    keep_threshold = program.get(
        "keep_threshold",
        _load_config().get("defaults", {}).get("keep_threshold", 0.005),
    )
    meets_threshold = metric_delta >= keep_threshold

    improvement_pct = (metric_delta / max(abs(pre_metric), 0.001)) * 100

    return {
        "success": True,
        "experiment_id": experiment_id,
        "domain": domain,
        "pre_metric": round(pre_metric, 6),
        "post_metric": round(post_metric, 6),
        "metric_delta": round(metric_delta, 6),
        "improvement_pct": round(improvement_pct, 2),
        "meets_threshold": meets_threshold,
        "keep_threshold": keep_threshold,
        "placeholder_metrics": True,
        "heuristic": True,
        "placeholder_note": _PLACEHOLDER_METRICS_NOTE,
    }


def decide(
    experiment_id: str,
    pre_metric: float = None,
    post_metric: float = None,
    tests_passed: bool = True,
    coherence_passed: bool = True,
) -> dict:
    """Binary keep/discard decision (Karpathy pattern).

    keep: record success, update landscape posterior
    discard: record failure, update landscape posterior
    """
    exp = get_experiment(experiment_id)
    if not exp:
        return {"success": False, "error": f"Experiment not found: {experiment_id}"}

    domain = exp["domain"]
    program = _load_program(domain)
    config = _load_config()

    keep_threshold = program.get(
        "keep_threshold",
        config.get("defaults", {}).get("keep_threshold", 0.005),
    )

    if pre_metric is None:
        pre_metric = 0.0
    if post_metric is None:
        post_metric = 0.0

    metric_delta = post_metric - pre_metric
    direction = program.get("objective", {}).get("direction", "maximize")
    if direction == "minimize":
        metric_delta = -metric_delta

    improvement_pct = (metric_delta / max(abs(pre_metric), 0.001)) * 100

    # Decision logic
    require_tests = config.get("defaults", {}).get("require_test_pass", True)
    require_coherence = config.get("defaults", {}).get("require_coherence_check", True)

    if require_tests and not tests_passed:
        decision = "discard"
        rationale = "Tests failed — discarding change"
    elif require_coherence and not coherence_passed:
        decision = "discard"
        rationale = "Coherence check failed — discarding change"
    elif metric_delta >= keep_threshold:
        decision = "keep"
        rationale = f"Metric improved by {improvement_pct:.2f}% (>= {keep_threshold * 100:.1f}% threshold)"
    else:
        decision = "discard"
        rationale = f"Metric delta {metric_delta:.6f} below threshold {keep_threshold}"

    now = now_iso()

    # Record result (append-only — D-AR-4)
    result_id = _result_uuid()
    try:
        with _get_db() as conn:
            ph = sql_placeholder(conn)
            conn.execute(
                "INSERT INTO experiment_results "
                "(id, experiment_id, domain, hypothesis, category, "
                "pre_metric, post_metric, metric_delta, improvement_pct, "
                "decision, decision_rationale, tests_passed, coherence_passed, "
                "classification, created_at) "
                f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})",
                (
                    result_id,
                    experiment_id,
                    domain,
                    exp.get("hypothesis", ""),
                    exp.get("category"),
                    pre_metric,
                    post_metric,
                    metric_delta,
                    improvement_pct,
                    decision,
                    rationale,
                    1 if tests_passed else 0,
                    1 if coherence_passed else 0,
                    "CUI",
                    now,
                ),
            )

            # Update candidate status
            new_status = "completed" if decision == "keep" else "discarded"
            conn.execute(
                f"UPDATE experiment_candidates SET status = {ph}, updated_at = {ph} WHERE id = {ph}",
                (new_status, now, experiment_id),
            )

            # Update landscape posterior
            category = exp.get("category", "general")
            _update_landscape(conn, domain, category, decision, metric_delta, now)

    except Exception as exc:
        return {"success": False, "error": str(exc)}

    # Update trust engine
    try:
        from tools.autonomy.trust_engine import observe

        observe(
            "run_experiment", success=(decision == "keep"), details={"experiment_id": experiment_id, "domain": domain}
        )
    except (ImportError, Exception):
        pass

    _audit(
        f"experiment.{decision}",
        f"Experiment {experiment_id}: {decision}",
        {
            "experiment_id": experiment_id,
            "result_id": result_id,
            "domain": domain,
            "decision": decision,
            "metric_delta": metric_delta,
            "improvement_pct": improvement_pct,
        },
    )

    return {
        "success": True,
        "experiment_id": experiment_id,
        "result_id": result_id,
        "domain": domain,
        "decision": decision,
        "rationale": rationale,
        "pre_metric": round(pre_metric, 6),
        "post_metric": round(post_metric, 6),
        "metric_delta": round(metric_delta, 6),
        "improvement_pct": round(improvement_pct, 2),
        "placeholder_metrics": True,
        "heuristic": True,
        "placeholder_note": _PLACEHOLDER_METRICS_NOTE,
    }


def _update_landscape(conn, domain: str, category: str, decision: str, metric_delta: float, now: str):
    """Update experiment landscape posterior (Thompson Sampling)."""
    try:
        ph = sql_placeholder(conn)
        row = conn.execute(
            f"SELECT * FROM experiment_landscapes WHERE domain = {ph} AND category = {ph}",
            (domain, category),
        ).fetchone()

        if row:
            alpha = row["alpha"]
            beta_val = row["beta_val"]
            total = row["total_experiments"]
            kept = row["total_kept"]
            discarded = row["total_discarded"]
            best = row["best_improvement"]
            cumulative = row["cumulative_improvement"]

            if decision == "keep":
                alpha += 1.0
                kept += 1
                best = max(best, metric_delta)
                cumulative += metric_delta
            else:
                beta_val += 1.0
                discarded += 1

            conn.execute(
                f"UPDATE experiment_landscapes SET alpha = {ph}, beta_val = {ph}, "
                f"total_experiments = {ph}, total_kept = {ph}, total_discarded = {ph}, "
                f"best_improvement = {ph}, cumulative_improvement = {ph}, "
                f"last_experiment_at = {ph}, updated_at = {ph} "
                f"WHERE domain = {ph} AND category = {ph}",
                (
                    alpha,
                    beta_val,
                    total + 1,
                    kept,
                    discarded,
                    best,
                    cumulative,
                    now,
                    now,
                    domain,
                    category,
                ),
            )
        else:
            lid = f"el-{uuid.uuid4().hex[:12]}"
            alpha = 3.0 if decision == "keep" else 2.0
            beta_val = 8.0 if decision == "discard" else 7.0
            conn.execute(
                "INSERT INTO experiment_landscapes "
                "(id, domain, category, alpha, beta_val, total_experiments, "
                "total_kept, total_discarded, best_improvement, "
                "cumulative_improvement, last_experiment_at, updated_at) "
                f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})",
                (
                    lid,
                    domain,
                    category,
                    alpha,
                    beta_val,
                    1,
                    1 if decision == "keep" else 0,
                    1 if decision == "discard" else 0,
                    metric_delta if decision == "keep" else 0.0,
                    metric_delta if decision == "keep" else 0.0,
                    now,
                    now,
                ),
            )
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning(
            "_update_landscape: best-effort INSERT into experiment_landscapes failed (non-blocking): %s",
            exc,
        )


# ── Autonomous Loop ──────────────────────────────────────────────────────────


def run_loop(
    domain: str,
    max_experiments: int = 5,
    overnight: bool = False,
    seed: int = 42,
) -> dict:
    """Autonomous Karpathy Loop.

    1. Load experiment program for domain
    2. Generate hypothesis pool (scanner-tier LLM / templates)
    3. Score candidates via Bayesian selector
    4. For each: create → run → evaluate → decide
    5. Update Thompson Sampling posterior
    6. Report results
    """
    config = _load_config()
    program = _load_program(domain)
    if not program:
        return {"success": False, "error": f"No program config for domain: {domain}"}

    if overnight:
        max_experiments = config.get("defaults", {}).get("overnight_max_experiments", 20)

    # Check circuit breaker
    consecutive_failures = 0
    max_failures = config.get("circuit_breaker", {}).get("max_consecutive_failures", 3)

    # Generate hypotheses
    from tools.autoresearch.hypothesis_generator import generate_hypotheses

    hyp_result = generate_hypotheses(domain, max_hypotheses=max_experiments * 2, seed=seed)
    candidates = hyp_result.get("hypotheses", [])

    if not candidates:
        return {
            "success": True,
            "domain": domain,
            "experiments_run": 0,
            "kept": 0,
            "discarded": 0,
            "reason": "no_hypotheses_generated",
        }

    # Get recent experiments for dedup
    recent = []
    try:
        with _get_db() as conn:
            ph = sql_placeholder(conn)
            rows = conn.execute(
                "SELECT hypothesis, content_hash FROM experiment_candidates "
                f"WHERE domain = {ph} ORDER BY created_at DESC LIMIT 50",
                (domain,),
            ).fetchall()
            recent = [dict(r) for r in rows]
    except Exception:
        pass

    # Measure baseline
    from tools.autoresearch.fitness_evaluator import evaluate

    baseline = evaluate(domain)
    baseline_metric = baseline.get("metric_value", 0.0)

    results = []
    kept_count = 0
    discarded_count = 0
    total_improvement = 0.0

    for i in range(min(max_experiments, len(candidates))):
        # Circuit breaker check
        if consecutive_failures >= max_failures:
            _audit(
                "experiment.circuit_breaker",
                "Circuit breaker tripped",
                {
                    "domain": domain,
                    "consecutive_failures": consecutive_failures,
                },
            )
            break

        # Select next experiment via Bayesian selector
        from tools.autoresearch.bayesian_selector import select_next_experiment

        selection = select_next_experiment(
            candidates[i:],
            domain,
            recent_experiments=recent,
            seed=seed + i,
        )
        selected = selection.get("selected")
        if not selected:
            break

        # Create experiment
        exp_result = create_experiment(
            domain=domain,
            hypothesis=selected["hypothesis"],
            category=selected.get("category"),
            source=selected.get("source", "llm_generated"),
            signal_id=selected.get("signal_id"),
        )
        if not exp_result.get("success"):
            continue

        exp_id = exp_result["experiment_id"]

        # Store Bayesian score (append-only — D-AR-4)
        try:
            with _get_db() as conn:
                ph = sql_placeholder(conn)
                conn.execute(
                    "INSERT INTO bayesian_experiment_scores "
                    "(id, candidate_id, domain, info_gain_score, dimensions, "
                    "threshold_band, thompson_sample, scored_at) "
                    f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})",
                    (
                        _score_uuid(),
                        exp_id,
                        domain,
                        selection.get("info_gain_score", 0.5),
                        json.dumps(selection.get("dimensions", {}), default=str),
                        selection.get("threshold_band", "suggest"),
                        1 if selection.get("thompson_explored") else 0,
                        now_iso(),
                    ),
                )
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            logger.warning(
                "run_loop: best-effort INSERT into bayesian_experiment_scores failed (non-blocking): %s",
                exc,
            )

        # Run experiment (measure current metric)
        run_result = run_experiment(exp_id)
        pre_metric = run_result.get("pre_metric", baseline_metric)

        # Re-evaluate after experiment (in real usage, code is modified between)
        post_eval = evaluate(domain)
        post_metric = post_eval.get("metric_value", pre_metric)

        # Decide
        decision_result = decide(
            exp_id,
            pre_metric=pre_metric,
            post_metric=post_metric,
        )

        decision = decision_result.get("decision", "discard")
        if decision == "keep":
            kept_count += 1
            consecutive_failures = 0
            improvement = decision_result.get("metric_delta", 0.0)
            total_improvement += improvement
            # Update baseline for next iteration
            baseline_metric = post_metric
        else:
            discarded_count += 1
            consecutive_failures += 1

        results.append(
            {
                "experiment_id": exp_id,
                "hypothesis": selected["hypothesis"][:200],
                "decision": decision,
                "metric_delta": decision_result.get("metric_delta", 0.0),
                "info_gain_score": selection.get("info_gain_score", 0.0),
                "thompson_explored": selection.get("thompson_explored", False),
            }
        )

        # Add to recent for dedup
        recent.append(
            {
                "hypothesis": selected["hypothesis"],
                "content_hash": selected.get("content_hash", ""),
            }
        )

    acceptance_rate = kept_count / max(kept_count + discarded_count, 1)

    _audit(
        "experiment.loop_complete",
        f"Loop completed for {domain}",
        {
            "domain": domain,
            "experiments_run": len(results),
            "kept": kept_count,
            "discarded": discarded_count,
            "acceptance_rate": round(acceptance_rate, 4),
            "total_improvement": round(total_improvement, 6),
        },
    )

    return {
        "success": True,
        "domain": domain,
        "experiments_run": len(results),
        "kept": kept_count,
        "discarded": discarded_count,
        "acceptance_rate": round(acceptance_rate, 4),
        "total_improvement": round(total_improvement, 6),
        "baseline_metric": round(baseline_metric, 6),
        "results": results,
        "circuit_breaker_tripped": consecutive_failures >= max_failures,
        "placeholder_metrics": True,
        "heuristic": True,
        "placeholder_note": _PLACEHOLDER_METRICS_NOTE,
        "timestamp": now_iso(),
    }


# ── Status ───────────────────────────────────────────────────────────────────


def get_status() -> dict:
    """Current autoresearch status across all domains."""
    try:
        with _get_db() as conn:
            # Total experiments
            total = conn.execute("SELECT COUNT(*) as cnt FROM experiment_results").fetchone()
            total_count = total["cnt"] if total else 0

            # By decision
            kept = conn.execute("SELECT COUNT(*) as cnt FROM experiment_results WHERE decision = 'keep'").fetchone()
            kept_count = kept["cnt"] if kept else 0

            # By domain
            domains = conn.execute(
                "SELECT domain, COUNT(*) as cnt, "
                "SUM(CASE WHEN decision = 'keep' THEN 1 ELSE 0 END) as kept, "
                "MAX(improvement_pct) as best_pct "
                "FROM experiment_results GROUP BY domain"
            ).fetchall()

            # Landscapes
            landscapes = conn.execute("SELECT * FROM experiment_landscapes ORDER BY domain, category").fetchall()

            return {
                "total_experiments": total_count,
                "total_kept": kept_count,
                "total_discarded": total_count - kept_count,
                "acceptance_rate": round(kept_count / max(total_count, 1), 4),
                "domains": [dict(d) for d in domains],
                "landscapes": [dict(row) for row in landscapes],
                "placeholder_metrics": True,
                "heuristic": True,
                "placeholder_note": _PLACEHOLDER_METRICS_NOTE,
                "timestamp": now_iso(),
            }
    except Exception as exc:
        return {
            "total_experiments": 0,
            "total_kept": 0,
            "acceptance_rate": 0.0,
            "error": str(exc),
            "timestamp": now_iso(),
        }


def health_check() -> dict:
    """Health check for autoresearch engine."""
    config = _load_config()
    enabled = config.get("enabled", False)

    programs_dir = _ROOT / "args" / "experiment_programs"
    programs = list(programs_dir.glob("*.yaml")) if programs_dir.exists() else []

    db_ok = False
    try:
        with _get_db() as conn:
            conn.execute("SELECT 1 FROM experiment_candidates LIMIT 1")
            db_ok = True
    except Exception:
        pass

    return {
        "status": "healthy" if db_ok else "degraded",
        "enabled": enabled,
        "db_available": db_ok,
        "programs_found": len(programs),
        "program_domains": [p.stem for p in programs],
        "config_loaded": bool(config),
        "timestamp": now_iso(),
    }


# ── CLI ──────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Bayesian Autoresearch Engine — autonomous experiment loop")
    parser.add_argument("--create", action="store_true", help="Create experiment")
    parser.add_argument("--run", action="store_true", help="Run experiment")
    parser.add_argument("--evaluate", action="store_true", help="Evaluate experiment")
    parser.add_argument("--decide", action="store_true", help="Keep/discard decision")
    parser.add_argument("--loop", action="store_true", help="Run autonomous loop")
    parser.add_argument("--status", action="store_true", help="Get status")
    parser.add_argument("--health", action="store_true", help="Health check")
    parser.add_argument("--domain", type=str, default="compliance")
    parser.add_argument("--hypothesis", type=str, default="")
    parser.add_argument("--experiment-id", type=str, default="")
    parser.add_argument("--max-experiments", type=int, default=5)
    parser.add_argument("--overnight", action="store_true")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.health:
        result = health_check()
    elif args.status:
        result = get_status()
    elif args.create:
        result = create_experiment(
            domain=args.domain,
            hypothesis=args.hypothesis or "Test hypothesis",
        )
    elif args.run:
        result = run_experiment(args.experiment_id)
    elif args.evaluate:
        result = evaluate_experiment(args.experiment_id)
    elif args.decide:
        result = decide(args.experiment_id)
    elif args.loop:
        result = run_loop(
            domain=args.domain,
            max_experiments=args.max_experiments,
            overnight=args.overnight,
        )
    else:
        parser.print_help()
        return

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
