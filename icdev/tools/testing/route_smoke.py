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


BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402
logger = get_logger("icdev.testing.route_smoke")

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
    # /api/iqe-query is POST-only, so a GET probe answers 405 Method Not Allowed.
    # A 405 still proves the route is registered and the blueprint imported.
    {"route": "/api/iqe-query",                   "expect_json": False,  "skip_404": True, "skip_405": True},
    # Collection routes live under their blueprint's url_prefix — there is no
    # bare GET /api/proposals or GET /api/govcon/opportunities.
    {"route": "/api/proposals/opportunities",     "expect_json": True},
    {"route": "/api/govcon/sam/opportunities",    "expect_json": True},
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

# Body text that indicates a broken response (even with 200 status).
#
# These are signals for a *rendered page* that leaked a Python error. They are
# applied to HTML/text responses only. JSON responses are checked structurally
# by _json_error() instead, because an API payload legitimately carries
# user-authored prose — kanban task descriptions, findings, log excerpts — that
# routinely mentions "ImportError", "no such table", "404 Not Found" and the
# rest. Substring-scanning those bodies fails the gate on data, not on defects.
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


# JSON bodies must be read whole to parse. HTML only needs a prefix, since a
# leaked traceback appears near the top of the rendered page.
HTML_READ_LIMIT = 32768
JSON_READ_LIMIT = 8 * 1024 * 1024


def _json_error(body: str) -> Optional[str]:
    """Structurally check a JSON response for a server-side *crash*.

    Only the top-level "error"/"traceback" field is examined, and only for the
    ERROR_SIGNALS this gate exists to catch (import errors, missing templates,
    missing tables). Two things are deliberately NOT failures:

      - Nested payload text. An API legitimately returns prose that mentions
        these words; scanning it fails the gate on data, not on defects.
      - A semantic empty-state such as {"error": "No scan data found"}. The
        route served fine, it just has nothing to report.

    A truncated or unparseable body yields None: the status code already
    passed, so there is nothing to justify inventing a failure.
    """
    try:
        payload = json.loads(body)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    for key in ("error", "traceback"):
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        for signal in ERROR_SIGNALS:
            if signal.lower() in value.lower():
                return value.strip()[:200]
    return None


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
            is_json = "json" in (resp.headers.get("Content-Type") or "").lower()
            limit = JSON_READ_LIMIT if is_json else HTML_READ_LIMIT
            body = resp.read(limit).decode("utf-8", errors="replace")
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

    if is_json:
        # Structural check — never substring-scan an API payload (see
        # ERROR_SIGNALS note).
        err = _json_error(body)
        if err:
            result["error"] = f"JSON error payload: {err}"
            result["error_signal"] = "json_error"
            return result
    else:
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
    skip_405 = bool(endpoint.get("skip_405", False))

    result = _smoke_route(base, route, timeout=timeout)
    result["is_api"] = True

    # A 404 on an optional endpoint is acceptable
    if result["status"] == 404 and skip_404:
        result["ok"] = True
        result["error"] = None
        return result

    # A 405 on a POST-only endpoint still proves the route is registered
    if result["status"] == 405 and skip_405:
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
                # Read the FULL body — a truncated read splits a large but valid
                # JSON document mid-string and json.loads reports a bogus
                # "Unterminated string" failure (/api/kanban/tasks is ~580KB).
                body = resp.read().decode("utf-8", errors="replace")
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
    parser.add_argument(
        "--max-routes", type=int, default=0,
        help=("Check at most N routes and NAME the ones skipped. 0 = no bound. "
              "For the pre-commit gate, which cannot afford a full sweep: "
              "measured 2026-08-15, all 79 nav routes take ~212s against a warm "
              "dashboard (2-3s each), so a hook budget of 120s could never "
              "finish one and every run died on the timeout instead."))
    parser.add_argument(
        "--exclude", default="",
        help=("Comma-separated routes to skip, each NAMED in the output. For "
              "routes a given environment genuinely cannot serve — CI disables "
              "the network canvas (ICDEV_NETWORK_ENABLED=false), so /network/* "
              "returns 404 there and always will. An exclusion is a statement "
              "that the route is out of scope HERE, never that a failure is "
              "acceptable; it is enumerated so a reader can see the coverage "
              "they are actually getting."))
    parser.add_argument(
        "--expect-fail", default="",
        help=("Comma-separated routes allowed to fail WITHOUT failing the run. "
              "Unlike --exclude these are still CHECKED, which is the point: a "
              "route that starts working is reported as a STALE entry to remove, "
              "so the list cleans itself. Use this where the run must still be "
              "able to go red (CI); use --exclude only where not checking saves "
              "something that matters, such as the pre-commit hook's time "
              "budget. An entry here is a statement about the ENVIRONMENT — a "
              "canvas this deployment does not enable — never about a route "
              "being allowed to be broken."))
    parser.add_argument("--base", default="http://localhost:5050")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    all_results: List[Dict] = []
    # Defined here, not inside the nav-route branch: --api-only skips that
    # branch entirely and the reporting below still reads it.
    skipped_by_cap: List[str] = []
    excluded: List[str] = []
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

        # Environment exclusions come off FIRST, so --max-routes bounds the
        # routes actually worth checking rather than spending its budget on
        # routes this environment was never going to serve.
        if args.exclude:
            drop = {r.strip() for r in args.exclude.split(",") if r.strip()}
            excluded = [r for r in routes if r in drop]
            routes = [r for r in routes if r not in drop]
            missing = sorted(drop - set(excluded))
            if missing and verbose:
                # A stale exclusion is how coverage shrinks without anyone
                # deciding to shrink it — the same reason red_first_gate.yaml
                # reports an exemption matching nothing.
                print(f"  STALE --exclude entries (no such route): {', '.join(missing)}")

        # A cap you cannot see reads as "covered everything". CLAUDE.md forbids
        # a silent one, so the dropped routes are NAMED, not counted.
        if args.max_routes and len(routes) > args.max_routes:
            skipped_by_cap = routes[args.max_routes:]
            routes = routes[: args.max_routes]

        if verbose:
            print(f"Smoking {len(routes)} nav routes against {args.base} ...")
            if excluded:
                print(f"  EXCLUDED for this environment ({len(excluded)}): "
                      f"{', '.join(excluded)}")
            if skipped_by_cap:
                print(f"  BOUNDED by --max-routes={args.max_routes}: "
                      f"{len(skipped_by_cap)} route(s) NOT checked: "
                      f"{', '.join(skipped_by_cap)}")
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

    # --expect-fail: a route this ENVIRONMENT cannot serve. Still checked, so
    # the list can clean itself — an entry that starts passing is reported as
    # STALE rather than quietly costing coverage forever, which is how a gate
    # shrinks without anyone deciding to shrink it.
    expected_fail = {r.strip() for r in (args.expect_fail or "").split(",") if r.strip()}
    # A route is "/something". Anything else is almost always MSYS path
    # conversion: Git Bash rewrites a leading-slash argument into a Windows
    # path, so `--expect-fail "/network/ask,..."` arrives as
    # "C:/Program Files/Git/network/ask,..." and that route is silently NOT
    # tolerated. Observed while verifying this very command. It would still be
    # reported below as an entry matching no route, but only among the
    # legitimately-stale ones, so say plainly what happened.
    mangled = sorted(r for r in expected_fail if not r.startswith("/"))
    if mangled and verbose:
        print(f"  --expect-fail entries that are not route paths: {', '.join(mangled)}"
              f"\n    (on Git Bash / MSYS, prefix the command with MSYS_NO_PATHCONV=1)")
    tolerated: List[str] = []
    stale_expect_fail: List[str] = []
    if expected_fail:
        checked = {str(r["route"]) for r in all_results}
        tolerated = sorted(str(r["route"]) for r in failures
                           if str(r["route"]) in expected_fail)
        failures = [r for r in failures if str(r["route"]) not in expected_fail]
        # Passed, or never ran at all — either way the entry no longer earns
        # its place and someone should say so out loud.
        stale_expect_fail = sorted(
            (expected_fail & checked) - set(tolerated)) + sorted(expected_fail - checked)
        overall_passed = not failures

    if args.as_json:
        print(json.dumps({
            "passed": overall_passed,
            "total": len(all_results),
            "failures": len(failures),
            # Named, not counted: a consumer must be able to tell "79/79 checked"
            # from "20 checked and the other 59 never looked at".
            "skipped_by_cap": skipped_by_cap,
            "excluded": excluded,
            "tolerated": tolerated,
            "stale_expect_fail": stale_expect_fail,
            "results": all_results,
        }))
    else:
        print(f"\n{'PASS' if overall_passed else 'FAIL'} — {len(all_results) - len(failures)}/{len(all_results)} checks OK")
        if tolerated:
            print(f"  TOLERATED for this environment ({len(tolerated)}): "
                  f"{', '.join(tolerated)}")
        if stale_expect_fail:
            print(f"  STALE --expect-fail ({len(stale_expect_fail)}): "
                  f"{', '.join(stale_expect_fail)} — these no longer fail here; "
                  f"remove them so the gate covers them again")
        if skipped_by_cap:
            print(f"  ({len(skipped_by_cap)} route(s) were NOT checked — see the "
                  f"--max-routes line above; this is not full coverage)")
        if failures:
            print("\nFailures:")
            for f in failures:
                print(f"  {f.get('route')}: {f['error']}")

    sys.exit(0 if overall_passed else 1)


if __name__ == "__main__":
    main()
