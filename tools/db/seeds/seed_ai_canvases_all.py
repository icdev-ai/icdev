# CUI // SP-CTI
"""Combined seed orchestrator for all AI canvas DoD/IC demo data.

Runs T1–T6 in order:
  T1 — AADC 8 designs (seed_ai_canvases_aadc.py)
  T2 — AIMC 8 designs (seed_ai_canvases_aimc.py)
  T3 — AI-ify 5 scans (seed_ai_canvases_aac.py)
  T4 — Observatory 200 decisions (seed_ai_canvases_observatory.py)
  T5 — KG 38 nodes + 30+ edges (seed_ai_canvases_kg.py)
  T6 — SDC demo before/after state (seed_sdc_demo.py)

Flags:
  --reset-all         DELETE existing records before reseeding
  --json              Machine-readable output
  --skip-observatory  Skip T4
  --skip-kg           Skip T5
  --skip-sdc          Skip T6

Run:
    python tools/db/seeds/seed_ai_canvases_all.py
    python tools/db/seeds/seed_ai_canvases_all.py --reset-all --json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def _run_step(label: str, fn, reset: bool) -> dict:
    t0 = time.monotonic()
    try:
        fn(reset=reset)
        elapsed = round((time.monotonic() - t0) * 1000)
        return {"step": label, "status": "ok", "elapsed_ms": elapsed}
    except Exception as exc:
        elapsed = round((time.monotonic() - t0) * 1000)
        return {"step": label, "status": "error", "error": str(exc), "elapsed_ms": elapsed}


def main(
    reset_all: bool = False,
    skip_observatory: bool = False,
    skip_kg: bool = False,
    skip_sdc: bool = False,
    verbose: bool = True,
) -> list[dict]:
    from tools.db.seeds.seed_ai_canvases_aadc import main as seed_aadc
    from tools.db.seeds.seed_ai_canvases_aimc import main as seed_aimc
    from tools.db.seeds.seed_ai_canvases_aac import main as seed_aac
    from tools.db.seeds.seed_ai_canvases_observatory import main as seed_obs
    from tools.db.seeds.seed_ai_canvases_kg import main as seed_kg
    from tools.db.seeds.seed_sdc_demo import main as seed_sdc

    steps = [
        ("T1 — AADC designs", seed_aadc),
        ("T2 — AIMC designs", seed_aimc),
        ("T3 — AI-ify scans", seed_aac),
    ]
    if not skip_observatory:
        steps.append(("T4 — Observatory decisions", seed_obs))
    if not skip_kg:
        steps.append(("T5 — KG nodes+edges", seed_kg))
    if not skip_sdc:
        steps.append(("T6 — SDC demo state", seed_sdc))

    results = []
    for label, fn in steps:
        if verbose:
            print(f"  [{label}] running ...")
        result = _run_step(label, fn, reset=reset_all)
        results.append(result)
        if verbose:
            status = result["status"]
            elapsed = result["elapsed_ms"]
            if status == "ok":
                print(f"  [{label}] OK ({elapsed}ms)")
            else:
                print(f"  [{label}] ERROR: {result.get('error')} ({elapsed}ms)")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed all AI canvas DoD/IC demo data")
    parser.add_argument("--reset-all", action="store_true", help="Delete and reseed all records")
    parser.add_argument("--skip-observatory", action="store_true")
    parser.add_argument("--skip-kg", action="store_true")
    parser.add_argument("--skip-sdc", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    if not args.as_json:
        print("Seeding AI canvas DoD/IC demo data ...")

    results = main(
        reset_all=args.reset_all,
        skip_observatory=args.skip_observatory,
        skip_kg=args.skip_kg,
        skip_sdc=args.skip_sdc,
        verbose=not args.as_json,
    )

    failures = [r for r in results if r["status"] != "ok"]

    if args.as_json:
        print(json.dumps({
            "passed": len(failures) == 0,
            "total_steps": len(results),
            "failures": len(failures),
            "results": results,
        }))
    else:
        print(f"\n{'PASS' if not failures else 'FAIL'} — {len(results) - len(failures)}/{len(results)} steps OK")
        if failures:
            print("\nFailed steps:")
            for r in failures:
                print(f"  {r['step']}: {r.get('error')}")
        sys.exit(0 if not failures else 1)
