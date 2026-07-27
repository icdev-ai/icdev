#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Reflex — FORGE Academy Oracle (academy_oracle_reflex).

Runs the in-app 7-lens ``AcademyOracleRunner`` on a cadence so Academy Oracle
predictions and convergence events are refreshed autonomously, instead of only
on-demand via ``POST /api/academy/oracle/run`` (apps/forge_academy/blueprint.py).

The runner persists to ``fa_oracle_predictions`` / ``fa_oracle_convergence_events``
(apps/forge_academy/oracle/db.py) — the tables the Academy Oracle actually reads.

Replaces the removed ``tools/genesis/reflexes/forge_academy_oracle.py`` (penta-aca-06),
which was doubly dead: it was never listed in ``REFLEX_NAMES`` (so the daemon
never dispatched it) AND it queried the *global* ``oracle_predictions`` table for
``lens_id='forge_academy'`` while the Academy Oracle writes only to
``fa_oracle_predictions`` — so it would have found 0 rows even if wired.

COOLDOWN_HOURS = 6 (enforced by the Genesis daemon via genesis_config.yaml).

Usage:
    python tools/genesis/reflexes/academy_oracle_reflex.py [--dry-run] [--json]
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

COOLDOWN_HOURS = 6


def run_oracle(dry_run: bool = False) -> Dict[str, Any]:
    """Execute all Academy Oracle lenses and persist their output.

    Returns a summary dict: ``success``, ``lenses_run``, ``predictions_generated``,
    ``persisted``, ``convergence_events``, ``errors``.
    """
    try:
        from apps.forge_academy.db import migrate
        from apps.forge_academy.oracle.runner import AcademyOracleRunner, _LENSES
    except Exception as exc:  # pragma: no cover - import guard for air-gap/partial installs
        return {
            "success": False,
            "error": f"import: {exc}",
            "lenses_run": 0,
            "predictions_generated": 0,
            "persisted": 0,
            "convergence_events": 0,
            "errors": [f"import: {exc}"],
        }

    lens_count = len(_LENSES)

    # Ensure the fa_* / fa_oracle_* tables exist before the lenses read/write.
    try:
        migrate()
    except Exception as exc:
        return {
            "success": False,
            "error": f"migrate: {exc}",
            "lenses_run": 0,
            "predictions_generated": 0,
            "persisted": 0,
            "convergence_events": 0,
            "errors": [f"migrate: {exc}"],
        }

    if dry_run:
        # Preview: the runner always persists, so a dry run reports the lens
        # count without executing/persisting.
        return {
            "success": True,
            "dry_run": True,
            "lenses_run": lens_count,
            "predictions_generated": 0,
            "persisted": 0,
            "convergence_events": 0,
            "errors": [],
        }

    try:
        result = AcademyOracleRunner().run()
    except Exception as exc:
        return {
            "success": False,
            "error": f"run: {exc}",
            "lenses_run": lens_count,
            "predictions_generated": 0,
            "persisted": 0,
            "convergence_events": 0,
            "errors": [f"run: {exc}"],
        }

    return {
        "success": True,
        "lenses_run": lens_count,
        "predictions_generated": len(result.get("predictions", [])),
        "persisted": int(result.get("persisted_count", 0)),
        "convergence_events": len(result.get("convergence", [])),
        "errors": [],
    }


# ---------------------------------------------------------------------------
# Reflex entry point (Genesis daemon contract: run(config, trust) -> dict)
# ---------------------------------------------------------------------------

def run(config: Dict[str, Any], trust: Any = None) -> Dict[str, Any]:
    """Genesis reflex entry point — dispatched every 6 h (COOLDOWN_HOURS)."""
    dry_run = bool((config or {}).get("dry_run", False))
    start = time.time()
    result = run_oracle(dry_run=dry_run)
    result["duration_ms"] = round((time.time() - start) * 1000)
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FORGE Academy Oracle Genesis Reflex")
    parser.add_argument("--dry-run", action="store_true", help="Preview without persisting")
    parser.add_argument("--json", action="store_true", dest="as_json", help="JSON output")
    args = parser.parse_args()

    out = run_oracle(dry_run=args.dry_run)
    if args.as_json:
        print(json.dumps(out, indent=2))
    else:
        print(f"Success              : {out['success']}")
        print(f"Lenses run           : {out['lenses_run']}")
        print(f"Predictions generated: {out['predictions_generated']}")
        print(f"Persisted            : {out['persisted']}")
        print(f"Convergence events   : {out['convergence_events']}")
        if out.get("errors"):
            print(f"Errors               : {out['errors']}")
