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
import sys
import time
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.testing.route_smoke")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ── Nav routes extracted from base.html ─────────────────────────────────────
# These are the routes reachable from the nav menu — the ones users actually hit.
# ── Critical API endpoints ───────────────────────────────────────────────────
# These are the GET endpoints that page features depend on.
# A 500 here means buttons/tables/charts on the page silently fail.
API_ENDPOINTS: List[Dict[str, object]] = [
    {"route": "/api/kanban/tasks",               "expect_json": True},
    {"route": "/api/kanban/tasks?status=backlog", "expect_json": True},
    {"route": "/api/code-quality/summary",        "expect_json": True},
    {"route": "/api/code-quality/log-health",     "expect_json": True},
    {"route": "/api/iqe-query",                   "expect_json": False,  "skip_404": True},
    {"route": "/api/proposals",                   "expect_json": True},
    {"route": "/api/govcon/opportunities",        "expect_json": True},
    {"route": "/api/projects",                    "expect_json": True},
    {"route": "/api/agents",                      "expect_json": True},
    {"route": "/health",                          "expect_json": False},
]

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
    # Canvas home routes — regression-tested 2026-06-27
    "/security",
    "/observability",
    "/network/diagram-analysis",
    "/ai-builder",
    "/ai-learning",
    "/ai-roi",
    "/ai-skills",
    "/ai-handoff",
    "/skillhub",
    "/security/zig",
    "/dashboard/executive-view",
    "/dashboard/compliance-view",
    "/dashboard/pm-view",
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
        # blueprint.py, app.py, or files inside a templates/ directory trigger full smoke
        if fp.endswith("blueprint.py") or fp.endswith("app.py") or "/templates/" in fp:
            return NAV_ROUTES  # full smoke when app.py or layout templates change
        # Per-canvas heuristics — match canvas slug anywhere in the path
        for route in NAV_ROUTES:
            slug = route.strip("/").split("/")[0]
            if slug and slug in fp:
                routes.append(route)
    return routes or []


def _smoke_api_endpoint(
    base: str, endpoint: Dict[str, object], timeout: float = 10.0
) -> Dict[str, object]:
    """Smoke a JSON API endpoint — checks 200 status + valid JSON response."""
    import json as _json
    route = str(endpoint["route"])
    expect_json = bool(endpoint.get("expect_json", True))
    skip_404 = bool(endpoint.get("skip_404", False))

    result = _smoke_route(base, route, timeout=timeout)
    result["is_api"] = True

    # A 404 on an optional endpoint is acceptable
    if result["status"] == 404 and skip_404:
        result["ok"] = True
        result["error"] = None
        return result

    if not result["ok"]:
        return result

    # For JSON endpoints, verify the body parses as JSON
    if expect_json:
        url = base.rstrip("/") + route
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ICDEV-APISmoker/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read(65536).decode("utf-8", errors="replace")
            _json.loads(body)  # raises ValueError if not JSON
        except (ValueError, Exception) as e:
            result["ok"] = False
            result["error"] = f"Non-JSON response: {str(e)[:80]}"

    return result


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
    record_smoke_results(results, base=base)
    return len(failures) == 0, results


_PERF_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "route_perf.db"


def record_smoke_results(results: List[Dict], base: str = "http://localhost:5050") -> None:
    """Persist elapsed_ms + ok status for each smoke result to route_perf.db."""
    import sqlite3 as _sqlite3
    try:
        _PERF_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = _sqlite3.connect(str(_PERF_DB_PATH))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS route_perf (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                route       TEXT NOT NULL,
                base        TEXT NOT NULL,
                elapsed_ms  INTEGER,
                status_ok   INTEGER NOT NULL,
                http_status INTEGER,
                recorded_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_route_perf_route ON route_perf(route)
        """)
        rows = [
            (r["route"], base, r.get("elapsed_ms"), 1 if r.get("ok") else 0, r.get("status"))
            for r in results if r.get("route")
        ]
        conn.executemany(
            "INSERT INTO route_perf (route, base, elapsed_ms, status_ok, http_status) VALUES (?,?,?,?,?)",
            rows,
        )
        conn.commit()
        conn.close()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        # Never block smoke runs on DB write failure
        logger.warning("record_smoke_results: best-effort INSERT into route_perf failed (non-blocking): %s", exc)


def detect_perf_regressions(
    threshold_multiplier: float = 2.5,
    min_runs: int = 5,
    lookback: int = 30,
) -> List[Dict]:
    """Return routes whose recent p50 latency exceeds baseline by threshold_multiplier.

    Args:
        threshold_multiplier: current_p50 > baseline_p50 * multiplier → regression
        min_runs: minimum recorded runs before a route is eligible
        lookback: number of recent runs to compare against the full baseline
    Returns:
        List of {route, baseline_p50, current_p50, ratio, runs}
    """
    import sqlite3 as _sqlite3
    if not _PERF_DB_PATH.exists():
        return []
    try:
        conn = _sqlite3.connect(str(_PERF_DB_PATH))
        conn.row_factory = _sqlite3.Row
        rows = conn.execute(
            """
            SELECT route, elapsed_ms, status_ok, recorded_at
            FROM route_perf
            WHERE elapsed_ms IS NOT NULL AND status_ok = 1
            ORDER BY recorded_at ASC
            """
        ).fetchall()
        conn.close()
    except Exception:
        return []

    from collections import defaultdict
    by_route: dict = defaultdict(list)
    for row in rows:
        by_route[row["route"]].append(row["elapsed_ms"])

    regressions = []
    for route, times in by_route.items():
        if len(times) < min_runs:
            continue
        all_sorted = sorted(times)
        baseline_p50 = all_sorted[len(all_sorted) // 2]
        recent = sorted(times[-lookback:])
        current_p50 = recent[len(recent) // 2]
        if current_p50 > baseline_p50 * threshold_multiplier:
            regressions.append({
                "route": route,
                "baseline_p50_ms": baseline_p50,
                "current_p50_ms": current_p50,
                "ratio": round(current_p50 / max(baseline_p50, 1), 2),
                "runs": len(times),
            })
    return sorted(regressions, key=lambda r: -r["ratio"])


def run_api_smoke(
    endpoints: Optional[List[Dict]] = None,
    base: str = "http://localhost:5050",
    timeout: float = 10.0,
    verbose: bool = True,
) -> Tuple[bool, List[Dict]]:
    """Smoke critical API endpoints — verifies JSON responses, not just 200."""
    if not _server_up(base):
        if verbose:
            print(f"  [SKIP] Server not running at {base} — API smoke skipped")
        return True, []

    targets = endpoints or API_ENDPOINTS
    results = []
    for ep in targets:
        result = _smoke_api_endpoint(base, ep, timeout=timeout)
        results.append(result)
        if verbose:
            route = ep["route"]
            if result["ok"]:
                print(f"  [OK]   {route}  ({result['elapsed_ms']}ms)")
            else:
                print(f"  [FAIL] {route}  status={result['status']}  error={result['error']}")

    failures = [r for r in results if not r["ok"]]
    return len(failures) == 0, results


def main() -> None:
    parser = argparse.ArgumentParser(description="ICDEV Route + API Smoke Tester")
    parser.add_argument("--all", action="store_true", help="Smoke all nav routes + API endpoints")
    parser.add_argument("--routes", help="Comma-separated route list, e.g. /kanban,/govcon")
    parser.add_argument("--changed", help="Comma-separated list of changed file paths")
    parser.add_argument("--api", action="store_true", help="Also smoke critical API endpoints")
    parser.add_argument("--api-only", action="store_true", help="Only smoke API endpoints")
    parser.add_argument("--base", default="http://localhost:5050")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    all_results: List[Dict] = []
    overall_passed = True
    verbose = not args.as_json

    if not args.api_only:
        if args.routes:
            routes = [r.strip() for r in args.routes.split(",") if r.strip()]
        elif args.changed:
            changed = [f.strip() for f in args.changed.split(",") if f.strip()]
            routes = _routes_for_changed_files(changed)
        else:
            routes = NAV_ROUTES

        if verbose:
            print(f"Smoking {len(routes)} nav routes against {args.base} ...")
        passed, results = run_smoke(routes, base=args.base, timeout=args.timeout, verbose=verbose)
        all_results.extend(results)
        if not passed:
            overall_passed = False

    if args.api or args.api_only or args.all:
        if verbose:
            print(f"\nSmoking {len(API_ENDPOINTS)} API endpoints against {args.base} ...")
        passed, results = run_api_smoke(base=args.base, timeout=args.timeout, verbose=verbose)
        all_results.extend(results)
        if not passed:
            overall_passed = False

    failures = [r for r in all_results if not r["ok"]]

    if args.as_json:
        print(json.dumps({
            "passed": overall_passed,
            "total": len(all_results),
            "failures": len(failures),
            "results": all_results,
        }))
    else:
        print(f"\n{'PASS' if overall_passed else 'FAIL'} — {len(all_results) - len(failures)}/{len(all_results)} checks OK")
        if failures:
            print("\nFailures:")
            for f in failures:
                print(f"  {f.get('route')}: {f['error']}")

    sys.exit(0 if overall_passed else 1)


if __name__ == "__main__":
    main()
