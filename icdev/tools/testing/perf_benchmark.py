# CUI // SP-CTI
"""Performance baseline harness — load + latency regression detection.

Two complementary capabilities, both pure-Python and air-gap safe:

1. **Latency benchmark** (built-in, no third-party deps) — hits ~10 *hot*
   dashboard/API endpoints against a **live** local dashboard, records the
   per-endpoint p50 / p95 / p99 response latency, and (optionally) compares the
   result against a committed baseline with a configurable tolerance. Uses only
   ``urllib`` from the stdlib, so it runs anywhere ``route_smoke.py`` runs.

2. **Load profile** (``--load``) — drives the same hot endpoints under
   concurrent load via `locust <https://locust.io>`_ (pure-Python,
   ``pip install locust`` — no npm/Node). ``locust`` is an **optional** dep: the
   import is guarded, and ``--load`` degrades to a clear message (never an error)
   when locust is not installed. The load profile lives in
   ``tools/testing/perf/locustfile.py``.

Warn-only by design
-------------------
The regression gate is **advisory**. ``--compare`` reports endpoints that
exceeded the baseline * (1 + tolerance) but exits 0 unless ``--enforce`` is
explicitly passed. This tool is **NOT** wired into the required CI checks
(Lint / Test / Security / Helm) — do not add it without user sign-off.

Backend caveat
--------------
Perf numbers are only comparable **like-for-like**. The tests run under a
SQLite-forced env by default; a PostgreSQL deployment has different latency
characteristics. The active storage backend is therefore recorded in every
result and in the committed baseline, and ``--compare`` refuses to compare
across mismatched backends unless ``--allow-backend-mismatch`` is given.

Never benchmark through LocalStack / mocks — point ``--base`` at a real, running
dashboard instance only.

CLI
---
    # Benchmark a live dashboard, print JSON (no baseline write)
    python tools/testing/perf_benchmark.py --json

    # Capture / refresh the committed baseline from the current live state
    python tools/testing/perf_benchmark.py --update-baseline

    # Compare current latencies against the baseline (warn-only)
    python tools/testing/perf_benchmark.py --compare --tolerance 0.5 --json

    # Enforce: exit 1 on any regression beyond tolerance
    python tools/testing/perf_benchmark.py --compare --enforce

    # Run the locust load profile (requires: pip install locust)
    python tools/testing/perf_benchmark.py --load --users 20 --run-time 30s
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

BASELINE_PATH: Path = PROJECT_ROOT / "tests" / "perf" / "perf_baseline.json"
LOCUSTFILE_PATH: Path = PROJECT_ROOT / "tools" / "testing" / "perf" / "locustfile.py"

DEFAULT_BASE_URL: str = os.environ.get(
    "ICDEV_PERF_BASE_URL", "http://127.0.0.1:5050"
)

# ── Hot endpoints ────────────────────────────────────────────────────────────
# A stable, representative cross-section of the highest-traffic GET surfaces:
# the home board, a handful of hot canvas pages, and the JSON APIs that back the
# widgets users hit constantly. Kept in sync (subset) with route_smoke.py.
HOT_ENDPOINTS: List[str] = [
    "/",                          # home / task board
    "/kanban",                    # kanban board page
    "/govcon",                    # govcon canvas home
    "/proposals",                 # proposals page
    "/compliance",                # compliance page
    "/health",                    # liveness probe
    "/api/kanban/tasks",          # board data API
    "/api/projects",              # projects API
    "/api/agents",                # agents API
    "/api/govcon/opportunities",  # govcon opportunities API
]


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested offline — no server / no locust needed)
# ---------------------------------------------------------------------------

def percentile(samples: List[float], pct: float) -> float:
    """Nearest-rank percentile of *samples* (0 < pct <= 100).

    Robust for small sample counts; returns 0.0 for an empty list. The nearest-
    rank method avoids interpolation surprises and is deterministic.
    """
    if not samples:
        return 0.0
    if pct <= 0:
        pct = 0.0001
    if pct > 100:
        pct = 100.0
    ordered = sorted(samples)
    # nearest-rank: rank = ceil(pct/100 * N), 1-indexed
    import math
    rank = max(1, math.ceil((pct / 100.0) * len(ordered)))
    return float(ordered[rank - 1])


def summarize(samples: List[float]) -> Dict[str, float]:
    """p50/p95/p99 + min/max/mean/count summary for a list of latencies (ms)."""
    if not samples:
        return {
            "count": 0, "p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0,
            "min_ms": 0.0, "max_ms": 0.0, "mean_ms": 0.0,
        }
    return {
        "count": len(samples),
        "p50_ms": round(percentile(samples, 50), 2),
        "p95_ms": round(percentile(samples, 95), 2),
        "p99_ms": round(percentile(samples, 99), 2),
        "min_ms": round(min(samples), 2),
        "max_ms": round(max(samples), 2),
        "mean_ms": round(sum(samples) / len(samples), 2),
    }


def storage_backend() -> str:
    """Active storage backend — perf is only comparable like-for-like."""
    return os.environ.get("ICDEV_STORAGE_BACKEND", "sqlite").strip().lower() or "sqlite"


def compare_to_baseline(
    current: Dict,
    baseline: Dict,
    tolerance: float = 0.5,
    metric: str = "p95_ms",
    allow_backend_mismatch: bool = False,
) -> Dict:
    """Compare *current* run against *baseline*; flag regressions (warn-only).

    A regression is an endpoint whose current ``metric`` exceeds the baseline
    ``metric`` * (1 + tolerance). Endpoints absent from the baseline (or with a
    zeroed placeholder baseline) are reported as ``unbaselined`` — never
    regressions — so a placeholder baseline never produces false alarms.

    Returns a dict: {backend_match, regressions[], improvements[], unbaselined[]}.
    """
    cur_backend = current.get("backend")
    base_backend = baseline.get("backend")
    backend_match = (cur_backend == base_backend)

    result: Dict = {
        "tolerance": tolerance,
        "metric": metric,
        "current_backend": cur_backend,
        "baseline_backend": base_backend,
        "backend_match": backend_match,
        "comparable": backend_match or allow_backend_mismatch,
        "regressions": [],
        "improvements": [],
        "unbaselined": [],
    }
    if not result["comparable"]:
        result["note"] = (
            f"backend mismatch ({cur_backend} vs {base_backend}); numbers are "
            "not comparable like-for-like. Pass --allow-backend-mismatch to force."
        )
        return result

    base_eps = {e["endpoint"]: e for e in baseline.get("endpoints", [])}
    for ep in current.get("endpoints", []):
        name = ep["endpoint"]
        cur_val = float(ep.get(metric, 0.0) or 0.0)
        base_ep = base_eps.get(name)
        base_val = float(base_ep.get(metric, 0.0) or 0.0) if base_ep else 0.0
        # Placeholder / zeroed baseline → nothing to compare against.
        if base_ep is None or base_val <= 0.0:
            result["unbaselined"].append({"endpoint": name, metric: cur_val})
            continue
        threshold = base_val * (1.0 + tolerance)
        entry = {
            "endpoint": name,
            "baseline_" + metric: round(base_val, 2),
            "current_" + metric: round(cur_val, 2),
            "ratio": round(cur_val / base_val, 2) if base_val else None,
            "threshold_" + metric: round(threshold, 2),
        }
        if cur_val > threshold:
            result["regressions"].append(entry)
        elif cur_val < base_val:
            result["improvements"].append(entry)
    result["regressions"].sort(key=lambda e: -(e.get("ratio") or 0))
    return result


# ---------------------------------------------------------------------------
# Live-server helpers
# ---------------------------------------------------------------------------

def is_server_reachable(base: str = DEFAULT_BASE_URL, timeout: float = 3.0) -> bool:
    """True when a dashboard answers ``/health`` at *base*."""
    try:
        urllib.request.urlopen(f"{base.rstrip('/')}/health", timeout=timeout)
        return True
    except Exception:
        return False


def _sample_once(url: str, timeout: float) -> Optional[float]:
    """Return elapsed ms for a single GET, or None on failure."""
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "ICDEV-PerfBench/1.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read(65536)
            if resp.status >= 400:
                return None
    except urllib.error.HTTPError as exc:
        # A 404 on an optional endpoint still returned quickly; but treat
        # >=400 as a non-sample so latency stats aren't polluted by error paths.
        return None if exc.code >= 400 else round((time.monotonic() - t0) * 1000, 2)
    except Exception:
        return None
    return round((time.monotonic() - t0) * 1000, 2)


def benchmark_endpoint(
    base: str, path: str, samples: int = 20, warmup: int = 2, timeout: float = 15.0
) -> Dict:
    """Sample *path* *samples* times (after *warmup* discarded requests)."""
    url = base.rstrip("/") + path
    for _ in range(max(0, warmup)):
        _sample_once(url, timeout)
    latencies: List[float] = []
    failures = 0
    for _ in range(max(1, samples)):
        val = _sample_once(url, timeout)
        if val is None:
            failures += 1
        else:
            latencies.append(val)
    stats = summarize(latencies)
    stats.update({"endpoint": path, "url": url, "failures": failures})
    return stats


def run_benchmark(
    base: str = DEFAULT_BASE_URL,
    endpoints: Optional[List[str]] = None,
    samples: int = 20,
    timeout: float = 15.0,
) -> Dict:
    """Benchmark all *endpoints*; return a full result document.

    Raises RuntimeError when no server is reachable — callers (CLI / pytest)
    are expected to check ``is_server_reachable`` first and skip gracefully.
    """
    if not is_server_reachable(base):
        raise RuntimeError(f"no live dashboard reachable at {base}")
    targets = endpoints or HOT_ENDPOINTS
    results = [
        benchmark_endpoint(base, path, samples=samples, timeout=timeout)
        for path in targets
    ]
    return {
        "schema": "icdev.perf_baseline/1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "backend": storage_backend(),
        "base_url": base,
        "samples_per_endpoint": samples,
        "endpoints": results,
    }


# ---------------------------------------------------------------------------
# Baseline persistence
# ---------------------------------------------------------------------------

def load_baseline(path: Path = BASELINE_PATH) -> Optional[Dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_baseline(doc: Dict, path: Path = BASELINE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Locust load profile (optional dep)
# ---------------------------------------------------------------------------

def locust_available() -> bool:
    try:
        import locust  # noqa: F401
    except Exception:
        return False
    return True


def run_load(
    base: str = DEFAULT_BASE_URL,
    users: int = 20,
    spawn_rate: int = 5,
    run_time: str = "30s",
) -> Dict:
    """Run the locust load profile headlessly. Warn-only, never raises.

    Returns a dict describing the outcome. When locust is not installed or no
    server is reachable, returns ``{"skipped": True, "reason": ...}`` so callers
    never break on an optional capability.
    """
    if not locust_available():
        return {
            "skipped": True,
            "reason": "locust not installed (pip install locust) — load profile skipped",
        }
    if not LOCUSTFILE_PATH.exists():
        return {"skipped": True, "reason": f"locustfile missing at {LOCUSTFILE_PATH}"}
    if not is_server_reachable(base):
        return {"skipped": True, "reason": f"no live dashboard at {base} — load skipped"}

    cmd = [
        sys.executable, "-m", "locust",
        "-f", str(LOCUSTFILE_PATH),
        "--headless",
        "-u", str(users),
        "-r", str(spawn_rate),
        "-t", run_time,
        "--host", base.rstrip("/"),
        "--only-summary",
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600, check=False
        )
        return {
            "skipped": False,
            "exit_code": proc.returncode,
            "users": users,
            "spawn_rate": spawn_rate,
            "run_time": run_time,
            "backend": storage_backend(),
            "stdout_tail": proc.stdout[-4000:],
            "stderr_tail": proc.stderr[-2000:],
        }
    except Exception as exc:  # never let an optional capability break the caller
        return {"skipped": True, "reason": f"locust run failed: {type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="ICDEV performance baseline harness")
    parser.add_argument("--base", default=DEFAULT_BASE_URL, help="dashboard base URL")
    parser.add_argument("--samples", type=int, default=20, help="samples per endpoint")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--update-baseline", action="store_true",
                        help="write the current live result to the committed baseline")
    parser.add_argument("--compare", action="store_true",
                        help="compare current latencies vs baseline (warn-only)")
    parser.add_argument("--tolerance", type=float, default=0.5,
                        help="regression tolerance fraction (0.5 = +50%%)")
    parser.add_argument("--metric", default="p95_ms",
                        choices=["p50_ms", "p95_ms", "p99_ms"])
    parser.add_argument("--allow-backend-mismatch", action="store_true")
    parser.add_argument("--enforce", action="store_true",
                        help="exit 1 on regressions (default: warn-only, exit 0)")
    # load profile
    parser.add_argument("--load", action="store_true", help="run the locust load profile")
    parser.add_argument("--users", type=int, default=20)
    parser.add_argument("--spawn-rate", type=int, default=5)
    parser.add_argument("--run-time", default="30s")
    args = parser.parse_args()

    verbose = not args.as_json

    # ── Load profile mode ────────────────────────────────────────────────────
    if args.load:
        outcome = run_load(
            base=args.base, users=args.users,
            spawn_rate=args.spawn_rate, run_time=args.run_time,
        )
        if args.as_json:
            print(json.dumps(outcome, indent=2))
        elif outcome.get("skipped"):
            print(f"[SKIP] load profile: {outcome.get('reason')}")
        else:
            print(f"locust finished (exit {outcome.get('exit_code')})\n{outcome.get('stdout_tail','')}")
        return 0  # load is always advisory

    # ── Benchmark modes (need a live server) ────────────────────────────────
    if not is_server_reachable(args.base):
        msg = f"no live dashboard reachable at {args.base} — benchmark skipped (not a failure)"
        if args.as_json:
            print(json.dumps({"skipped": True, "reason": msg}))
        else:
            print(f"[SKIP] {msg}")
        return 0  # self-skip: never break required checks

    current = run_benchmark(
        base=args.base, samples=args.samples, timeout=args.timeout
    )

    if args.update_baseline:
        save_baseline(current)
        if verbose:
            print(f"Baseline written to {BASELINE_PATH} (backend={current['backend']})")

    exit_code = 0
    comparison = None
    if args.compare:
        baseline = load_baseline()
        if baseline is None:
            if verbose:
                print(f"[WARN] no baseline at {BASELINE_PATH} — run --update-baseline first")
        else:
            comparison = compare_to_baseline(
                current, baseline, tolerance=args.tolerance,
                metric=args.metric,
                allow_backend_mismatch=args.allow_backend_mismatch,
            )
            regressions = comparison.get("regressions", [])
            if regressions and args.enforce:
                exit_code = 1

    if args.as_json:
        out = {"result": current}
        if comparison is not None:
            out["comparison"] = comparison
        print(json.dumps(out, indent=2))
    else:
        print(f"\nPerf benchmark — backend={current['backend']} base={current['base_url']}")
        for ep in current["endpoints"]:
            print(f"  {ep['endpoint']:<32} p50={ep['p50_ms']:>7}ms  "
                  f"p95={ep['p95_ms']:>7}ms  p99={ep['p99_ms']:>7}ms  "
                  f"(n={ep['count']}, fail={ep['failures']})")
        if comparison is not None:
            regs = comparison.get("regressions", [])
            if not comparison.get("comparable"):
                print(f"\n[WARN] {comparison.get('note')}")
            elif regs:
                print(f"\n[WARN] {len(regs)} regression(s) beyond +{int(args.tolerance*100)}%:")
                for r in regs:
                    print(f"  {r['endpoint']}: {r} ")
            else:
                print("\n[OK] no regressions beyond tolerance")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
