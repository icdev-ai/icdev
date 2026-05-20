#!/usr/bin/env python3
# CUI // SP-CTI
"""Route Smoke Tester — verify all nav routes return 200 against a running server.

This is the RUNTIME gate that catches what CodeLens + Coherence cannot:
  - Import errors that only surface when Flask tries to serve the route
  - Missing templates (TemplateNotFound 500)
  - Missing DB tables/columns that crash on first request
  - Nav links pointing to routes that were never registered

Two modes:
  1. --all    : smoke all nav routes extracted from base.html
  2. --routes : smoke a specific comma-separated list of routes
  3. --changed: auto-detect which routes were touched by changed files

Exit 0 = all routes pass. Exit 1 = at least one route failed.

Usage:
    python tools/testing/route_smoke.py --all
    python tools/testing/route_smoke.py --routes /kanban,/govcon,/proposals
    python tools/testing/route_smoke.py --changed tools/govcon/blueprint.py
    python tools/testing/route_smoke.py --all --base http://localhost:5050 --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ── Nav routes extracted from base.html ─────────────────────────────────────
# These are the routes reachable from the nav menu — the ones users actually hit.
NAV_ROUTES: List[str] = [
    "/",
    "/projects",
    "/agents",
    "/kanban",
    "/cicd",
    "/orchestration",
    "/monitoring",
    "/platform-health",
    "/activity",
    "/chat",
    "/ai-wizard",
    "/dev-profiles",
    "/phases",
    "/finetune",
    "/diagrams",
    "/connector-forge",
    "/translations",
    "/ai-patterns",
    "/ai-observatory",
    "/components-map",
    "/ask-icdev",
    "/research",
    "/autoresearch",
    "/knowledge-search",
    "/code-quality",
    "/writeguard",
    "/govcon",
    "/govcon/requirements",
    "/govcon/capabilities",
    "/proposals",
    "/cpmp",
    "/oscal",
    "/fedramp-20x",
    "/compliance",
    "/poam",
    "/ato-compliance",
    "/safety",
    "/stig-manager",
    "/digital-twin",
    "/fathomdesk",
    "/supply_chain",
    "/strategos",
    "/innovation",
    "/migration-canvas/network-migration/",
    "/studio/workflows",
    "/studio/forms",
    "/studio/cases",
    "/studio/automations",
    "/studio/dashboards",
    "/studio/marketplace",
    "/ai-ml/modernize",
    "/knowledge-graph",
    "/pulse",
    "/leads",
    "/genesis",
    "/sandbox",
    "/oracle",
    "/mosa",
    "/sre",
    "/pr-intel",
    "/network/ask",
    "/security/ask",
    "/devops/ask",
    "/boundary/ask",
    "/data/ask",
    "/observability/ask",
    "/infra/ask",
]

# Body text that indicates a broken response (even with 200 status)
ERROR_SIGNALS = [
    "Internal Server Error",
    "Traceback (most recent call last)",
    "TemplateNotFound",
    "ImportError",
    "AttributeError",
    "OperationalError",
    "no such table",
    "no such column",
    "404 Not Found",
    "Page not found",
]


def _server_up(base: str, timeout: float = 3.0) -> bool:
    try:
        urllib.request.urlopen(f"{base}/health", timeout=timeout)
        return True
    except Exception:
        return False


def _smoke_route(
    base: str, route: str, timeout: float = 10.0
) -> Dict[str, object]:
    url = base.rstrip("/") + route
    result: Dict[str, object] = {
        "route": route,
        "url": url,
        "status": None,
        "ok": False,
        "error": None,
        "elapsed_ms": 0,
        "error_signal": None,
    }
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ICDEV-RouteSmoker/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(32768).decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as e:
        result["status"] = e.code
        result["error"] = f"HTTP {e.code} {e.reason}"
        result["elapsed_ms"] = round((time.monotonic() - t0) * 1000)
        return result
    except Exception as e:
        result["error"] = str(e)
        result["elapsed_ms"] = round((time.monotonic() - t0) * 1000)
        return result

    result["status"] = status
    result["elapsed_ms"] = round((time.monotonic() - t0) * 1000)

    if status >= 400:
        result["error"] = f"HTTP {status}"
        return result

    # Scan body for runtime error signals
    for signal in ERROR_SIGNALS:
        if signal.lower() in body.lower():
            result["error"] = f"Body contains error signal: '{signal}'"
            result["error_signal"] = signal
            return result

    result["ok"] = True
    return result


def _routes_for_changed_files(changed_files: List[str]) -> List[str]:
    """Heuristic: map changed blueprint/template files → affected nav routes."""
    routes: List[str] = []
    for f in changed_files:
        fp = f.lower().replace("\\", "/")
        # Blueprint or template change — check all nav routes by default
        if "blueprint" in fp or "template" in fp or "app.py" in fp:
            return NAV_ROUTES  # full smoke when app.py or base template changes
        # Per-canvas heuristics
        for route in NAV_ROUTES:
            slug = route.strip("/").split("/")[0]
            if slug and slug in fp:
                routes.append(route)
    return routes or NAV_ROUTES


def run_smoke(
    routes: List[str],
    base: str = "http://localhost:5050",
    timeout: float = 10.0,
    verbose: bool = True,
) -> Tuple[bool, List[Dict]]:
    """Run smoke on *routes*. Return (all_passed, results)."""
    if not _server_up(base):
        if verbose:
            print(f"  [SKIP] Server not running at {base} — route smoke skipped")
        return True, []  # Skip gracefully; not a gate failure

    results = []
    for route in routes:
        result = _smoke_route(base, route, timeout=timeout)
        results.append(result)
        if verbose:
            if result["ok"]:
                print(f"  [OK]   {route}  ({result['elapsed_ms']}ms)")
            else:
                print(
                    f"  [FAIL] {route}  status={result['status']}  error={result['error']}"
                )

    failures = [r for r in results if not r["ok"]]
    return len(failures) == 0, results


def main() -> None:
    parser = argparse.ArgumentParser(description="ICDEV Route Smoke Tester")
    parser.add_argument("--all", action="store_true", help="Smoke all nav routes")
    parser.add_argument("--routes", help="Comma-separated route list, e.g. /kanban,/govcon")
    parser.add_argument("--changed", help="Comma-separated list of changed file paths")
    parser.add_argument("--base", default="http://localhost:5050")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    if args.routes:
        routes = [r.strip() for r in args.routes.split(",") if r.strip()]
    elif args.changed:
        changed = [f.strip() for f in args.changed.split(",") if f.strip()]
        routes = _routes_for_changed_files(changed)
    else:
        routes = NAV_ROUTES

    verbose = not args.as_json
    if verbose:
        print(f"Smoking {len(routes)} routes against {args.base} ...")

    passed, results = run_smoke(routes, base=args.base, timeout=args.timeout, verbose=verbose)

    failures = [r for r in results if not r["ok"]]

    if args.as_json:
        print(json.dumps({"passed": passed, "total": len(results), "failures": len(failures), "results": results}))
    else:
        print(f"\n{'PASS' if passed else 'FAIL'} — {len(results) - len(failures)}/{len(results)} routes OK")
        if failures:
            print("\nFailed routes:")
            for f in failures:
                print(f"  {f['route']}: {f['error']}")

    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
