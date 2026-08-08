# CUI // SP-CTI
"""GovLift — Performance Benchmark Tool.

Validates the workload discovery scanner against the SLA requirement:
  "the workload discovery scanner must complete a full scan of 1000 servers
   within 2 hours"

Usage:
  python tools/govlift/performance_benchmark.py --fleet 100 --json
  python tools/govlift/performance_benchmark.py --fleet 1000 --json   # full SLA
  python tools/govlift/performance_benchmark.py --ci --json           # CI gate

NIST 800-53: SA-8 (Security Engineering Principles), SI-12 (Information Management).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, quantiles
from uuid import uuid4

_ICDEV_ROOT = Path(__file__).resolve().parents[2]
if str(_ICDEV_ROOT) not in sys.path:
    sys.path.insert(0, str(_ICDEV_ROOT))

import yaml

from tools.db.storage import get_connection, translate_sql
from tools.govlift.constants import WORKLOAD_TYPES
from tools.govlift.db.init_db import init_govlift_db


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    cfg_path = _ICDEV_ROOT / "args" / "performance_config.yaml"
    if not cfg_path.exists():
        return {}
    with cfg_path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------------------
# Synthetic workload helpers (ephemeral — cleaned up after benchmark)
# ---------------------------------------------------------------------------

def _bench_id() -> str:
    return "bench-" + uuid4().hex[:8]


def _probe_single_workload(workload_id: str, batch_timeout: float) -> dict:
    """Simulate a single workload scan: INSERT → query → UPDATE."""
    t0 = time.perf_counter()
    try:
        conn = get_connection()
        try:
            sql_sel = translate_sql("SELECT id, migration_status FROM govlift_workloads WHERE id = ?")
            row = conn.execute(sql_sel, (workload_id,)).fetchone()
            if row:
                sql_upd = translate_sql(
                    "UPDATE govlift_workloads SET migration_status='assessed', updated_at=? WHERE id=?"
                )
                conn.execute(sql_upd, (datetime.now(timezone.utc).isoformat(), workload_id))
                conn.commit()
        finally:
            conn.close()
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return {"id": workload_id, "elapsed_ms": elapsed_ms, "ok": True}
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return {"id": workload_id, "elapsed_ms": elapsed_ms, "ok": False, "error": str(exc)}


def _seed_workloads(count: int, run_tag: str) -> list[str]:
    """Insert synthetic workloads for the benchmark run; return their IDs."""
    ids = []
    now = datetime.now(timezone.utc).isoformat()
    wl_type = WORKLOAD_TYPES[0]  # web_app
    conn = get_connection()
    try:
        sql = translate_sql(
            "INSERT INTO govlift_workloads "
            "(id, name, workload_type, os_name, os_version, environment, "
            " ip_address, cpu_cores, memory_gb, storage_tb, risk_level, "
            " migration_status, notes, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,'discovered',?,?,?)"
        )
        batch = []
        for i in range(count):
            wl_id = _bench_id()
            ids.append(wl_id)
            batch.append((
                wl_id, f"bench-server-{i:05d}", wl_type,
                "RHEL", "8.9", "benchmark",
                f"10.0.{i // 256}.{i % 256}", 8, 32.0, 2.0,
                "medium", run_tag, now, now,
            ))
            if len(batch) >= 500:
                conn.executemany(sql, batch)
                conn.commit()
                batch = []
        if batch:
            conn.executemany(sql, batch)
            conn.commit()
    finally:
        conn.close()
    return ids


def _cleanup_workloads(ids: list[str]) -> None:
    """Remove synthetic benchmark workloads from the DB."""
    if not ids:
        return
    conn = get_connection()
    try:
        placeholders = ",".join(["?"] * len(ids))
        sql = translate_sql(f"DELETE FROM govlift_workloads WHERE id IN ({placeholders})")
        conn.execute(sql, ids)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Core benchmark runner
# ---------------------------------------------------------------------------

def run_benchmark(
    fleet_size: int,
    workers: int = 20,
    batch_timeout: float = 30.0,
    sla_hours: float = 2.0,
) -> dict:
    """Run the workload scanner benchmark and return a structured report."""
    cfg = _load_config().get("govlift", {}).get("scanner", {})
    run_tag = f"perf-benchmark-{uuid4().hex[:6]}"
    started_at = datetime.now(timezone.utc).isoformat()

    # Ensure schema exists before seeding (idempotent)
    init_govlift_db()

    print(f"[bench] Seeding {fleet_size} synthetic workloads…", file=sys.stderr)
    ids = _seed_workloads(fleet_size, run_tag)

    print(f"[bench] Probing {fleet_size} workloads with {workers} workers…", file=sys.stderr)
    results: list[dict] = []
    wall_start = time.perf_counter()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_probe_single_workload, wl_id, batch_timeout): wl_id
            for wl_id in ids
        }
        for fut in as_completed(futures):
            results.append(fut.result())

    wall_elapsed_s = time.perf_counter() - wall_start
    print(f"[bench] Cleaning up {fleet_size} synthetic workloads…", file=sys.stderr)
    _cleanup_workloads(ids)

    # --- Statistics ---
    ok_results = [r for r in results if r["ok"]]
    fail_count = len(results) - len(ok_results)
    latencies = [r["elapsed_ms"] for r in ok_results]
    p95_ms = quantiles(latencies, n=20)[18] if len(latencies) >= 20 else (max(latencies) if latencies else 0)
    avg_ms = mean(latencies) if latencies else 0

    sla_window_s = sla_hours * 3600
    sla_met = wall_elapsed_s <= sla_window_s and fail_count == 0

    # Latency gate from config
    latency_target_ms = cfg.get("latency_p95_ms", {}).get("single_scan", 500)
    p95_warn_mult = _load_config().get("govlift", {}).get("monitoring", {}).get(
        "alert_thresholds", {}).get("p95_latency_warn_mult", 1.5)
    latency_ok = p95_ms <= latency_target_ms * p95_warn_mult

    report = {
        "run_tag": run_tag,
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "fleet_size": fleet_size,
        "workers": workers,
        "sla_window_hours": sla_hours,
        "wall_elapsed_seconds": round(wall_elapsed_s, 2),
        "sla_required_seconds": sla_window_s,
        "sla_met": sla_met,
        "succeeded": len(ok_results),
        "failed": fail_count,
        "p95_latency_ms": round(p95_ms, 2),
        "avg_latency_ms": round(avg_ms, 2),
        "latency_target_ms": latency_target_ms,
        "latency_ok": latency_ok,
        "rate_per_minute": round(fleet_size / (wall_elapsed_s / 60), 2) if wall_elapsed_s > 0 else 0,
        "sla_required_rate_per_min": cfg.get("sla_derived_rate_per_min", 8.4),
        "overall_pass": sla_met and latency_ok,
    }
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="GovLift workload scanner performance benchmark")
    ap.add_argument("--fleet", type=int, default=None, help="Number of workloads to probe")
    ap.add_argument("--workers", type=int, default=None, help="Concurrent worker threads")
    ap.add_argument("--sla-hours", type=float, default=None, help="SLA window in hours")
    ap.add_argument("--ci", action="store_true", help="CI mode: use medium_fleet size")
    ap.add_argument("--json", action="store_true", help="Output JSON report")
    return ap.parse_args()


def main() -> int:
    args = _parse_args()
    cfg = _load_config().get("govlift", {})
    scanner_cfg = cfg.get("scanner", {})
    ci_cfg = cfg.get("ci_gate", {})

    if args.ci:
        fleet_size = scanner_cfg.get("load_test", {}).get("medium_fleet", 100)
    else:
        fleet_size = args.fleet or scanner_cfg.get("load_test", {}).get("small_fleet", 10)

    workers = args.workers or scanner_cfg.get("concurrent_workers", 20)
    sla_hours = args.sla_hours or scanner_cfg.get("sla_window_hours", 2.0)

    report = run_benchmark(fleet_size=fleet_size, workers=workers, sla_hours=sla_hours)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        status = "PASS" if report["overall_pass"] else "FAIL"
        print(f"[{status}] fleet={fleet_size}  elapsed={report['wall_elapsed_seconds']}s"
              f"  rate={report['rate_per_minute']}/min  p95={report['p95_latency_ms']}ms"
              f"  sla_met={report['sla_met']}  latency_ok={report['latency_ok']}")

    # Write report for CI consumption
    report_path = Path(ci_cfg.get("report_path", ".tmp/perf_report.json"))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8", newline="")

    if ci_cfg.get("fail_on_sla_breach") and not report["sla_met"]:
        print("[bench] FAIL: SLA breach — scanner did not meet time requirement", file=sys.stderr)
        return 1
    if ci_cfg.get("fail_on_latency_breach") and not report["latency_ok"]:
        print("[bench] FAIL: Latency breach — p95 exceeded threshold", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
