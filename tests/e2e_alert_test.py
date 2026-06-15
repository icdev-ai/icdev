# CUI // SP-CTI
"""E2E integration test: alert pipeline under load.

Integrates two optimizations:
  1. Async network writer (forward_alert_async) — DB write is non-blocking.
  2. DB optimization (AsyncDBWriter) — WAL mode, batched commits, background queue.

Generates ALERT_RATE alerts/sec, measuring end-to-end latency
(serialization + HTTP forward + queued DB write).
Acceptance criterion: p99 latency < 5 000 ms.

Runnable as pytest or as a standalone script.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import statistics
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer   # handles concurrent connections
from pathlib import Path
from typing import List

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Force SQLite — never route to PostgreSQL during load test.
os.environ["ICDEV_STORAGE_BACKEND"] = "sqlite"

# ---------------------------------------------------------------------------
# Test parameters
# ---------------------------------------------------------------------------
ALERT_RATE = 500           # target alerts/sec (task requirement)
TEST_BURST_SEC = 2         # sustain for N seconds → ALERT_RATE × N total
TOTAL_ALERTS = ALERT_RATE * TEST_BURST_SEC   # 1 000 alerts
P99_LIMIT_MS = 5_000.0     # 5-second SLA (NIST AU-6, SI-4)
MAX_WORKERS = 128          # thread-pool ceiling — allows true concurrent bursts


# ---------------------------------------------------------------------------
# Threaded mock SIEM — handles concurrent connections without queueing
# ---------------------------------------------------------------------------

class _FastHandler(BaseHTTPRequestHandler):
    """Zero-latency mock SIEM: reads body, returns 200."""

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


class _HighCapacitySIEM(ThreadingHTTPServer):
    # Default request_queue_size=5 causes connection refusals under 128-thread
    # load on Windows; raise it to match MAX_WORKERS.
    request_queue_size = 256


def _start_mock_siem() -> tuple[ThreadingHTTPServer, str]:
    """Start a multi-threaded mock SIEM that handles concurrent POSTs."""
    server = _HighCapacitySIEM(("127.0.0.1", 0), _FastHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{port}/siem"


# ---------------------------------------------------------------------------
# Alert generator
# ---------------------------------------------------------------------------

def _make_alert(seq: int) -> dict:
    sevs = ["low", "warning", "high", "critical"]
    return {
        "id": str(uuid.uuid4()),
        "title": f"LoadTest alert #{seq}",
        "severity": sevs[seq % len(sevs)],
        "source": "e2e_load_test",
        "service": f"svc-{seq % 10}",
        "description": (
            f"Synthetic load-test alert #{seq} "
            f"at {datetime.now(timezone.utc).isoformat()}"
        ),
    }


# ---------------------------------------------------------------------------
# Per-alert runner — uses async writer so DB write is off critical path
# ---------------------------------------------------------------------------

def _run_single_async(seq: int, siem_url: str, db_path: str) -> dict:
    """Forward one alert using the async optimised path."""
    from tools.monitor.async_alert_writer import forward_alert_async

    alert = _make_alert(seq)
    t0 = time.perf_counter()
    try:
        result = forward_alert_async(
            alert_payload=alert,
            siem_endpoint=siem_url,
            db_path=db_path,
        )
        e2e_ms = (time.perf_counter() - t0) * 1000
        return {
            "seq": seq,
            "e2e_ms": e2e_ms,
            "sla_met": result.get("sla_met", False),
            "delivered": result.get("delivered", False),
            "error": result.get("error"),
        }
    except Exception as exc:
        e2e_ms = (time.perf_counter() - t0) * 1000
        return {
            "seq": seq,
            "e2e_ms": e2e_ms,
            "sla_met": False,
            "delivered": False,
            "error": str(exc),
        }


# ---------------------------------------------------------------------------
# Core load runner
# ---------------------------------------------------------------------------

def run_load_test(
    total_alerts: int = TOTAL_ALERTS,
    siem_url: str = "",
    db_path: str = "",
    max_workers: int = MAX_WORKERS,
) -> dict:
    """Submit total_alerts concurrently; return latency statistics."""
    latencies: List[float] = []
    failures = 0
    sla_violations = 0

    wall_start = time.perf_counter()

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = [
            pool.submit(_run_single_async, i, siem_url, db_path)
            for i in range(total_alerts)
        ]
        for fut in concurrent.futures.as_completed(futs):
            r = fut.result()
            latencies.append(r["e2e_ms"])
            if not r["delivered"]:
                failures += 1
            if not r["sla_met"]:
                sla_violations += 1

    # Flush background DB writes before measuring (non-blocking path)
    if db_path:
        try:
            from tools.monitor.async_alert_writer import get_writer
            get_writer(db_path).flush(timeout=10.0)
        except Exception:
            pass

    wall_elapsed = time.perf_counter() - wall_start
    effective_rate = total_alerts / wall_elapsed if wall_elapsed > 0 else 0.0

    latencies.sort()
    n = len(latencies)
    p95_idx = max(0, int(0.95 * n) - 1)
    p99_idx = max(0, int(0.99 * n) - 1)
    p999_idx = max(0, int(0.999 * n) - 1)

    return {
        "total_alerts": total_alerts,
        "failures": failures,
        "sla_violations": sla_violations,
        "wall_elapsed_sec": round(wall_elapsed, 3),
        "effective_rate_per_sec": round(effective_rate, 1),
        "latency_ms": {
            "min": round(min(latencies), 2),
            "mean": round(statistics.mean(latencies), 2),
            "p50": round(statistics.median(latencies), 2),
            "p95": round(latencies[p95_idx], 2),
            "p99": round(latencies[p99_idx], 2),
            "p99_9": round(latencies[p999_idx], 2),
            "max": round(max(latencies), 2),
        },
        "p99_limit_ms": P99_LIMIT_MS,
        "p99_passed": latencies[p99_idx] < P99_LIMIT_MS,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# pytest test functions
# ---------------------------------------------------------------------------

def test_alert_pipeline_p99_latency_under_load():
    """
    E2E: 500 alerts/sec burst — p99 end-to-end latency must be < 5 000 ms.

    Uses async network writer (network hop off DB write critical path) and
    AsyncDBWriter (WAL mode + batched SQLite commits).
    """
    from tools.monitor.async_alert_writer import shutdown_all

    server, siem_url = _start_mock_siem()

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        results = run_load_test(
            total_alerts=TOTAL_ALERTS,
            siem_url=siem_url,
            db_path=db_path,
            max_workers=MAX_WORKERS,
        )
    finally:
        server.shutdown()
        shutdown_all(wait=False)
        try:
            Path(db_path).unlink(missing_ok=True)
        except Exception:
            pass

    lm = results["latency_ms"]

    assert results["total_alerts"] == TOTAL_ALERTS, (
        f"Expected {TOTAL_ALERTS} alerts, got {results['total_alerts']}"
    )

    # Allow ≤ 0.1 % delivery failures (transient connect errors on busy CI)
    max_failures = max(1, int(TOTAL_ALERTS * 0.001))
    assert results["failures"] <= max_failures, (
        f"Delivery failures {results['failures']}/{TOTAL_ALERTS} exceed {max_failures}"
    )

    # Core acceptance criterion
    assert results["p99_passed"], (
        f"p99 latency {lm['p99']:.1f}ms EXCEEDS {P99_LIMIT_MS:.0f}ms SLA\n"
        f"  min={lm['min']:.1f}  p50={lm['p50']:.1f}  "
        f"p95={lm['p95']:.1f}  p99={lm['p99']:.1f}  max={lm['max']:.1f}"
    )


def test_alert_pipeline_effective_throughput():
    """
    E2E: effective throughput must be ≥ 250 alerts/sec (50 % of 500/sec target).

    Uses the same async writer stack; 250/sec floor accounts for CI variability.
    """
    from tools.monitor.async_alert_writer import shutdown_all

    server, siem_url = _start_mock_siem()

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        results = run_load_test(
            total_alerts=500,
            siem_url=siem_url,
            db_path=db_path,
            max_workers=MAX_WORKERS,
        )
    finally:
        server.shutdown()
        shutdown_all(wait=False)
        try:
            Path(db_path).unlink(missing_ok=True)
        except Exception:
            pass

    assert results["effective_rate_per_sec"] >= 250, (
        f"Throughput {results['effective_rate_per_sec']:.1f} alerts/sec "
        f"is below the 250 alerts/sec minimum"
    )


def test_async_db_writer_persists_records():
    """
    Unit-level E2E: verify AsyncDBWriter actually writes records to SQLite.
    """
    import sqlite3
    from tools.monitor.async_alert_writer import AsyncDBWriter

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    writer = AsyncDBWriter(db_path)
    for i in range(10):
        writer.enqueue({
            "id": str(uuid.uuid4()),
            "alert_title": f"test-alert-{i}",
            "severity": "info",
            "siem_endpoint": "http://mock",
            "status_code": 200,
            "duration_ms": float(i),
            "sla_met": True,
            "error": None,
            "delivered_at": datetime.now(timezone.utc).isoformat(),
        })

    writer.flush(timeout=5.0)
    writer.shutdown(wait=True)

    try:
        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM siem_delivery_log").fetchone()[0]
        conn.close()
        assert count == 10, f"Expected 10 persisted records, got {count}"
    finally:
        Path(db_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

def main() -> int:
    from tools.monitor.async_alert_writer import shutdown_all

    server, siem_url = _start_mock_siem()

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    print(f"[e2e-load-test] Mock SIEM : {siem_url}")
    print(f"[e2e-load-test] DB        : {db_path}")
    print(f"[e2e-load-test] Load      : {TOTAL_ALERTS} alerts "
          f"({ALERT_RATE}/sec target × {TEST_BURST_SEC}s burst)")
    print(f"[e2e-load-test] Workers   : {MAX_WORKERS}")
    print("[e2e-load-test] Writer    : async (DB writes off critical path)")
    print()

    results = run_load_test(
        total_alerts=TOTAL_ALERTS,
        siem_url=siem_url,
        db_path=db_path,
    )

    server.shutdown()
    shutdown_all(wait=False)
    try:
        Path(db_path).unlink(missing_ok=True)
    except Exception:
        pass

    print(json.dumps(results, indent=2))
    print()

    lm = results["latency_ms"]
    print(
        f"  min={lm['min']:.1f}ms  p50={lm['p50']:.1f}ms  "
        f"p95={lm['p95']:.1f}ms  p99={lm['p99']:.1f}ms  max={lm['max']:.1f}ms"
    )
    print(
        f"  rate={results['effective_rate_per_sec']:.0f} alerts/sec  "
        f"failures={results['failures']}  sla_violations={results['sla_violations']}"
    )
    print()

    if results["p99_passed"]:
        print(f"[PASS] p99 latency {lm['p99']:.1f}ms < {P99_LIMIT_MS:.0f}ms limit")
        return 0

    print(f"[FAIL] p99 latency {lm['p99']:.1f}ms EXCEEDS {P99_LIMIT_MS:.0f}ms limit")
    return 1


if __name__ == "__main__":
    sys.exit(main())
