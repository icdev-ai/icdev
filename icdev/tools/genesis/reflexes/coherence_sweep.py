# CUI // SP-CTI
"""Full-tier coherence sweep — the other half of the gate/sweep split.

The per-task coherence gate (``validated_commit._run_coherence``) runs the
FAST tier: the whole-app checks that dominate its cost — ``blueprint_imports``,
``openapi_parity``, ``llm_router_api`` — are skipped unless the task's diff
touches their trigger surface. On a cold kanban worktree the full tier measured
328s against a 120s subprocess budget, so the gate timed out on every task and
(worse) recorded the timeout as a pass.

Those three are global invariants rather than per-diff properties, so this
reflex runs the FULL tier against the main checkout on a slow cadence and
refreshes the cached baseline that the per-task gate diffs against. Anything
that regresses is surfaced here instead of being paid for on every task.

Reflex contract:
  - run(ctx, conn) → dict with success / metric_value / details
  - CADENCE_HOURS = 6
  - Idempotent, read-only (no --fix), never raises
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

IMPLEMENTATION_STATUS = "full"

import json
import time
from pathlib import Path
from typing import Any, Dict

logger = get_logger(__name__)

CADENCE_HOURS = 6

# Generous — this is the deliberately slow path. The per-task gate is the one
# with a tight budget; this one is allowed to take as long as it needs.
SWEEP_TIMEOUT_SEC = 1800


def run(ctx: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Run the full coherence tier on main and refresh the gate's baseline.

    ``ctx`` accepts ``dry_run`` (report without refreshing the cache) and
    ``cwd`` (override the checkout to sweep; defaults to the main worktree,
    which is what the daemon wants).

    Returns a summary with the failing check ids so a regression is visible on
    the board rather than only in a log line.
    """
    dry_run = ctx.get("dry_run", False)
    result: Dict[str, Any] = {
        "cadence_hours": CADENCE_HOURS,
        "errors": [],
        "status": "ok",
        "failing_checks": [],
        "failed_count": 0,
        "warned_count": 0,
        "total_checks": 0,
        "elapsed_sec": 0.0,
        "dry_run": dry_run,
    }

    started = time.monotonic()
    try:
        from tools.workflow.validated_commit import (
            BASE_DIR,
            _coherence_cmd,
            _extract_report_json,
            _failing_check_ids,
            _main_head_sha,
            _run_cancellable,
        )

        sweep_root = ctx.get("cwd") or str(BASE_DIR)
        result["cwd"] = sweep_root
        rc, stdout, status = _run_cancellable(
            _coherence_cmd("full", None), sweep_root, SWEEP_TIMEOUT_SEC
        )
        if status != "ok":
            result["status"] = "error"
            result["errors"].append(f"full-tier sweep did not complete: {status}")
            return _finalise(result, started)

        report = _extract_report_json(stdout)
        if report is None:
            # Most likely cause: the checkout at sweep_root predates --tier and
            # argparse rejected the flag (exit 2, empty stdout). Say so instead
            # of just "unparseable" — that reads like a checker bug.
            result["status"] = "error"
            result["errors"].append(
                f"unparseable checker output (exit={rc}, cwd={sweep_root}); "
                f"stdout tail: {stdout.strip()[-200:] or '<empty>'}"
            )
            return _finalise(result, started)

        failing = sorted(_failing_check_ids(report))
        result["failing_checks"] = failing
        result["failed_count"] = len(failing)
        result["warned_count"] = int(report.get("warned_checks") or 0)
        result["total_checks"] = int(report.get("total_checks") or 0)

        # Refresh the baseline the per-task gate diffs against, so the next task
        # to fail a check can tell "I broke this" from "main was already red".
        if not dry_run:
            sha = _main_head_sha()
            cache = BASE_DIR / ".tmp" / f"coherence_baseline_full_{sha[:12]}.json"
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(
                    json.dumps({"sha": sha, "tier": "full", "failing": failing}),
                    encoding="utf-8",
                )
                result["baseline_refreshed"] = str(cache.name)
            except OSError as exc:
                result["errors"].append(f"baseline cache not written: {exc}")

        if failing:
            logger.warning(
                "coherence_sweep: %d check(s) failing on main: %s",
                len(failing), ", ".join(failing),
            )
    except Exception as exc:
        logger.error("coherence_sweep reflex error: %s", exc)
        result["status"] = "error"
        result["errors"].append(str(exc))

    return _finalise(result, started)


def _finalise(result: Dict[str, Any], started: float) -> Dict[str, Any]:
    """Attach the dispatch contract keys tools/daemon/base.py::run_reflex reads.

    Without success/metric_value/details a healthy cycle scores as a FAILURE and,
    repeated, trips the reflex circuit breaker. metric = failing check count,
    so the threshold is 'lte 0' — a green main.
    """
    result["elapsed_sec"] = round(time.monotonic() - started, 1)
    result["success"] = result["status"] != "error"
    result["metric_value"] = float(result.get("failed_count", 0) or 0)
    result["details"] = {
        k: result.get(k)
        for k in (
            "failing_checks", "failed_count", "warned_count", "total_checks",
            "elapsed_sec", "status", "errors", "dry_run",
        )
    }
    return result


if __name__ == "__main__":
    # Load THIS repo's .env so a direct CLI run uses the same config as the
    # GenesisDaemon. override=True: a pip-installed ICDEV in site-packages may
    # have already loaded a different checkout's .env at import.
    try:
        from dotenv import load_dotenv as _load_dotenv
        _load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=True)
    except ImportError:
        pass
    print(json.dumps(run({}), indent=2))
