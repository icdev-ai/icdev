#!/usr/bin/env python3
# CUI // SP-CTI
"""Canvas Quartet Orchestrator -- seeds all four canvases (NOCC, PMC, CCC, DSOC) in sequence.

Usage:
    python tools/db/seeds/seed_canvas_quartet.py [--reset] [--json]
    python tools/db/seeds/seed_canvas_quartet.py --verify --json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS = [
    ("NOCC", "tools/db/seeds/seed_nocc_demo.py"),
    ("PMC",  "tools/db/seeds/seed_pmc_demo.py"),
    ("CCC",  "tools/db/seeds/seed_ccc_demo.py"),
    ("DSOC", "tools/db/seeds/seed_dsoc_demo.py"),
]


def _run(name: str, script: str, args: list[str]) -> dict:
    cmd = [sys.executable, str(_ROOT / script)] + args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_ROOT))
        if result.returncode == 0:
            try:
                data = json.loads(result.stdout)
                return {"name": name, "status": "ok", **data}
            except json.JSONDecodeError:
                return {"name": name, "status": "ok", "stdout": result.stdout.strip()}
        return {"name": name, "status": "error", "stdout": result.stdout.strip(), "stderr": result.stderr.strip()}
    except Exception as exc:
        return {"name": name, "status": "exception", "error": str(exc)}


def main():
    parser = argparse.ArgumentParser(description="Canvas Quartet Orchestrator")
    parser.add_argument("--reset", action="store_true", help="Clear existing demo data before seeding")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--verify", action="store_true", help="Only verify counts")
    args = parser.parse_args()

    flags = []
    if args.reset:
        flags.append("--reset")
    if args.json or args.verify:
        flags.append("--json")
    if args.verify:
        flags.append("--verify")

    results = []
    for name, script in _SCRIPTS:
        results.append(_run(name, script, flags))

    summary = {
        "success": all(r.get("status") == "ok" for r in results),
        "results": results,
    }

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        for r in results:
            status = r["status"]
            name = r["name"]
            if status == "ok":
                seeded = r.get("seeded", {})
                verify = r.get("verify", {})
                print(f"[{name}] OK -- seeded: {seeded} | verify: {verify}")
            else:
                print(f"[{name}] {status.upper()} -- {r.get('stderr', r.get('error', ''))}")
        print(f"\n[orchestrator] All ok: {summary['success']}")

    sys.exit(0 if summary["success"] else 1)


if __name__ == "__main__":
    main()
