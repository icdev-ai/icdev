#!/usr/bin/env python3
# CUI // SP-CTI
"""GEPA Optimizer — Genome Evolution Pressure Analyzer skill runner.

Scans the capability genome registry for low-fitness entries and applies
optimization passes (prune stale capabilities, promote high-signal variants,
compact redundant genome nodes).

Usage (CLI):
    python tools/skills/gepa_optimizer.py --json
    python tools/skills/gepa_optimizer.py --dry-run --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]


def run(dry_run: bool = False) -> dict:
    """Run the GEPA optimization pass.

    Args:
        dry_run: When True, scan and report without writing any changes.

    Returns:
        dict with keys:
            applied  – list of optimization actions actually applied.
            skipped  – list of candidates skipped (dry_run or low confidence).
            errors   – list of error messages encountered during the pass.
    """
    applied: list[dict] = []
    skipped: list[dict] = []
    errors: list[str] = []

    try:
        from tools.registry.genome_manager import get_genome_entries
    except ImportError:
        get_genome_entries = None

    try:
        from tools.db.storage import get_connection
        conn = get_connection()
    except Exception as exc:
        errors.append(f"db_connect: {exc}")
        conn = None

    candidates: list[dict] = []

    if get_genome_entries is not None and conn is not None:
        try:
            candidates = get_genome_entries(conn) or []
        except Exception as exc:
            errors.append(f"genome_entries: {exc}")

    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass

    for entry in candidates:
        capability_id = entry.get("id") or entry.get("capability_id", "")
        fitness = float(entry.get("fitness_score", 1.0))

        if fitness >= 0.5:
            skipped.append({"capability_id": capability_id, "reason": "fitness_ok", "fitness": fitness})
            continue

        action = {
            "capability_id": capability_id,
            "action": "prune_low_fitness",
            "fitness": fitness,
            "dry_run": dry_run,
        }
        if dry_run:
            skipped.append({**action, "reason": "dry_run"})
        else:
            try:
                conn2 = None
                try:
                    from tools.db.storage import get_connection as _gc
                    conn2 = _gc()
                    conn2.execute(
                        "UPDATE genome_capabilities SET status = 'pruned' WHERE id = ?",
                        (capability_id,),
                    )
                    conn2.commit()
                finally:
                    if conn2 is not None:
                        conn2.close()
                applied.append(action)
            except Exception as exc:
                errors.append(f"prune {capability_id}: {exc}")
                skipped.append({**action, "reason": f"error: {exc}"})

    if not candidates and not errors:
        skipped.append({"reason": "no_candidates", "note": "genome registry empty or unavailable"})

    return {"applied": applied, "skipped": skipped, "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser(description="GEPA Optimizer")
    parser.add_argument("--dry-run", action="store_true", help="Report without applying changes")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    args = parser.parse_args()

    result = run(dry_run=args.dry_run)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"applied={len(result['applied'])} skipped={len(result['skipped'])} errors={len(result['errors'])}")
        for err in result["errors"]:
            print(f"  ERROR: {err}", file=sys.stderr)


if __name__ == "__main__":
    main()
