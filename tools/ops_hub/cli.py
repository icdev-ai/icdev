#!/usr/bin/env python3
# CUI // SP-CTI
"""OHC CLI — ops-hub health check and gate.

Usage:
  python tools/ops_hub/cli.py --health --json
  python tools/ops_hub/cli.py --gate
  python tools/ops_hub/cli.py --adapters --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _health(as_json: bool) -> int:
    from tools.ops_hub.ops_aggregator import get_ops_overview
    overview = get_ops_overview()
    score = overview.get("health_score", 0)
    status = overview.get("overall_status", "unknown")

    if as_json:
        print(json.dumps(overview, indent=2, default=str))
    else:
        print(f"[ohc] Health Score: {score}/100  Status: {status.upper()}")
        for alert in overview.get("top_alerts", []):
            print(f"  [{alert['severity'].upper()}] {alert['domain']}: {alert['message']}")

    return 0 if status == "healthy" else 1


def _adapters(as_json: bool) -> int:
    from tools.ops_hub.adapter_registry import probe_all
    results = probe_all(persist=True)
    available = [r for r in results if r.get("available")]

    if as_json:
        print(json.dumps(results, indent=2, default=str))
    else:
        print(f"[ohc] Adapters: {len(available)}/{len(results)} available")
        for r in results:
            mark = "✓" if r.get("available") else "✗"
            print(f"  {mark} {r['adapter_name']:<25} {r.get('domain', ''):<10} {r.get('error', '')}")

    return 0


def _gate() -> int:
    """Hard gate — exits 1 if overall status is critical."""
    from tools.ops_hub.ops_aggregator import get_ops_overview
    overview = get_ops_overview()
    status = overview.get("overall_status", "unknown")
    score = overview.get("health_score", 0)
    print(f"[ohc-gate] health_score={score}  status={status}")
    if status == "critical":
        print("[ohc-gate] FAIL — critical status blocks gate")
        return 1
    print("[ohc-gate] PASS")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="OHC — Ops Hub Canvas CLI")
    parser.add_argument("--health", action="store_true", help="Run health check")
    parser.add_argument("--adapters", action="store_true", help="Probe all adapters")
    parser.add_argument("--gate", action="store_true", help="Exit 1 if critical")
    parser.add_argument("--json", action="store_true", dest="as_json", help="JSON output")
    args = parser.parse_args()

    if args.gate:
        sys.exit(_gate())
    elif args.adapters:
        sys.exit(_adapters(args.as_json))
    else:
        sys.exit(_health(args.as_json))


if __name__ == "__main__":
    main()
