#!/usr/bin/env python3
"""Prometheus metric collector. Queries Prometheus API for instant and range queries,
collects application metrics, checks SLA compliance, and stores metric snapshots."""

import argparse
import json
import sqlite3
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

DEFAULT_PROMETHEUS_URL = "http://localhost:9090"


def _get_db(db_path: Path = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Prometheus API queries
# ---------------------------------------------------------------------------
def query_instant(promql: str, prom_url: str = None, time: str = None) -> dict:
    """Execute an instant query against Prometheus.
    GET /api/v1/query?query=PROMQL&time=TIME

    Args:
        promql: PromQL expression
        prom_url: Prometheus base URL
        time: Evaluation timestamp (RFC3339 or Unix). None = current time.
    """
    url = prom_url or DEFAULT_PROMETHEUS_URL
    params = {"query": promql}
    if time:
        params["time"] = time

    endpoint = f"{url}/api/v1/query?{urllib.parse.urlencode(params)}"

    try:
        req = urllib.request.Request(endpoint, method="GET")
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("status") == "success":
                return {
                    "status": "success",
                    "query": promql,
                    "result_type": data.get("data", {}).get("resultType"),
                    "results": data.get("data", {}).get("result", []),
                }
            else:
                return {
                    "status": "error",
                    "query": promql,
                    "error": data.get("error", "Unknown error"),
                    "error_type": data.get("errorType"),
                }
    except urllib.error.URLError as e:
        return {
            "status": "error",
            "query": promql,
            "error": f"Connection failed: {e}",
        }
    except Exception as e:
        return {
            "status": "error",
            "query": promql,
            "error": str(e),
        }


def query_range(
    promql: str,
    start: str = None,
    end: str = None,
    step: str = "60s",
    prom_url: str = None,
) -> dict:
    """Execute a range query against Prometheus.
    GET /api/v1/query_range?query=PROMQL&start=START&end=END&step=STEP

    Args:
        promql: PromQL expression
        start: Start timestamp (RFC3339 or Unix). Default: 1h ago.
        end: End timestamp. Default: now.
        step: Query resolution step (e.g., '15s', '60s', '5m')
        prom_url: Prometheus base URL
    """
    url = prom_url or DEFAULT_PROMETHEUS_URL

    now = datetime.utcnow()
    if not start:
        start = (now - timedelta(hours=1)).isoformat() + "Z"
    if not end:
        end = now.isoformat() + "Z"

    params = {
        "query": promql,
        "start": start,
        "end": end,
        "step": step,
    }

    endpoint = f"{url}/api/v1/query_range?{urllib.parse.urlencode(params)}"

    try:
        req = urllib.request.Request(endpoint, method="GET")
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("status") == "success":
                results = data.get("data", {}).get("result", [])
                return {
                    "status": "success",
                    "query": promql,
                    "result_type": data.get("data", {}).get("resultType"),
                    "results": results,
                    "data_points": sum(
                        len(r.get("values", [])) for r in results
                    ),
                }
            else:
                return {
                    "status": "error",
                    "query": promql,
                    "error": data.get("error", "Unknown error"),
                }
    except urllib.error.URLError as e:
        return {
            "status": "error",
            "query": promql,
            "error": f"Connection failed: {e}",
        }
    except Exception as e:
        return {
            "status": "error",
            "query": promql,
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# Application metrics collection
# ---------------------------------------------------------------------------
def get_application_metrics(
    project_id: str,
    prom_url: str = None,
    namespace: str = None,
) -> dict:
    """Collect standard application metrics: request_rate, error_rate,
    latency percentiles, CPU, memory.

    Args:
        project_id: Project identifier (used as job/service label)
        prom_url: Prometheus base URL
        namespace: Kubernetes namespace filter
    """
    url = prom_url or DEFAULT_PROMETHEUS_URL
    ns = namespace or project_id
    metrics = {}
    errors = []

    # Define queries for standard metrics
    queries = {
        "request_rate": f'sum(rate(http_requests_total{{namespace="{ns}"}}[5m]))',
        "error_rate": (
            f'sum(rate(http_requests_total{{namespace="{ns}",status=~"5.."}}[5m])) / '
            f'sum(rate(http_requests_total{{namespace="{ns}"}}[5m]))'
        ),
        "latency_p50": f'histogram_quantile(0.50, sum(rate(http_request_duration_seconds_bucket{{namespace="{ns}"}}[5m])) by (le))',
        "latency_p95": f'histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket{{namespace="{ns}"}}[5m])) by (le))',
        "latency_p99": f'histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket{{namespace="{ns}"}}[5m])) by (le))',
        "cpu_usage": f'sum(rate(container_cpu_usage_seconds_total{{namespace="{ns}"}}[5m]))',
        "memory_usage_bytes": f'sum(container_memory_working_set_bytes{{namespace="{ns}"}})',
        "memory_limit_bytes": f'sum(kube_pod_container_resource_limits{{namespace="{ns}",resource="memory"}})',
        "pod_restarts": f'sum(increase(kube_pod_container_status_restarts_total{{namespace="{ns}"}}[1h]))',
        "active_connections": f'sum(http_connections_active{{namespace="{ns}"}})',
    }

    for metric_name, promql in queries.items():
        result = query_instant(promql, url)
        if result["status"] == "success" and result.get("results"):
            # Extract scalar value from result
            for r in result["results"]:
                value = r.get("value", [None, None])
                if len(value) >= 2 and value[1] != "NaN":
                    try:
                        metrics[metric_name] = float(value[1])
                    except (ValueError, TypeError):
                        metrics[metric_name] = None
                else:
                    metrics[metric_name] = None
        else:
            metrics[metric_name] = None
            if result.get("error"):
                errors.append(f"{metric_name}: {result['error']}")

    # Compute derived metrics
    mem_usage = metrics.get("memory_usage_bytes")
    mem_limit = metrics.get("memory_limit_bytes")
    if mem_usage is not None and mem_limit is not None and mem_limit > 0:
        metrics["memory_utilization_pct"] = round((mem_usage / mem_limit) * 100, 2)

    return {
        "project_id": project_id,
        "collected_at": datetime.utcnow().isoformat(),
        "metrics": metrics,
        "errors": errors,
        "prometheus_url": url,
    }


# ---------------------------------------------------------------------------
# SLA check
# ---------------------------------------------------------------------------
DEFAULT_SLA = {
    "availability_pct": 99.9,
    "latency_p95_ms": 500,
    "latency_p99_ms": 2000,
    "error_rate_pct": 1.0,
    "memory_utilization_pct": 85,
}


def check_sla(
    project_id: str,
    sla_config: dict = None,
    prom_url: str = None,
    namespace: str = None,
) -> dict:
    """Check application metrics against SLA thresholds.

    Args:
        project_id: Project identifier
        sla_config: Dict of SLA thresholds. Uses defaults if not provided.
        prom_url: Prometheus URL
        namespace: K8s namespace
    """
    sla = sla_config or DEFAULT_SLA
    app_metrics = get_application_metrics(project_id, prom_url, namespace)
    metrics = app_metrics.get("metrics", {})

    violations = []
    checks = []

    # Error rate check
    error_rate = metrics.get("error_rate")
    if error_rate is not None:
        error_rate_pct = error_rate * 100
        threshold = sla.get("error_rate_pct", 1.0)
        passed = error_rate_pct <= threshold
        check = {
            "metric": "error_rate",
            "current": round(error_rate_pct, 3),
            "threshold": threshold,
            "unit": "%",
            "passed": passed,
        }
        checks.append(check)
        if not passed:
            violations.append(check)

    # Latency P95 check
    p95 = metrics.get("latency_p95")
    if p95 is not None:
        p95_ms = p95 * 1000  # Convert seconds to ms
        threshold = sla.get("latency_p95_ms", 500)
        passed = p95_ms <= threshold
        check = {
            "metric": "latency_p95",
            "current": round(p95_ms, 1),
            "threshold": threshold,
            "unit": "ms",
            "passed": passed,
        }
        checks.append(check)
        if not passed:
            violations.append(check)

    # Latency P99 check
    p99 = metrics.get("latency_p99")
    if p99 is not None:
        p99_ms = p99 * 1000
        threshold = sla.get("latency_p99_ms", 2000)
        passed = p99_ms <= threshold
        check = {
            "metric": "latency_p99",
            "current": round(p99_ms, 1),
            "threshold": threshold,
            "unit": "ms",
            "passed": passed,
        }
        checks.append(check)
        if not passed:
            violations.append(check)

    # Memory utilization check
    mem_pct = metrics.get("memory_utilization_pct")
    if mem_pct is not None:
        threshold = sla.get("memory_utilization_pct", 85)
        passed = mem_pct <= threshold
        check = {
            "metric": "memory_utilization",
            "current": round(mem_pct, 1),
            "threshold": threshold,
            "unit": "%",
            "passed": passed,
        }
        checks.append(check)
        if not passed:
            violations.append(check)

    # Pod restarts check (any restarts in the last hour is a concern)
    restarts = metrics.get("pod_restarts")
    if restarts is not None:
        threshold = sla.get("max_pod_restarts_1h", 3)
        passed = restarts <= threshold
        check = {
            "metric": "pod_restarts_1h",
            "current": int(restarts),
            "threshold": threshold,
            "unit": "restarts",
            "passed": passed,
        }
        checks.append(check)
        if not passed:
            violations.append(check)

    sla_met = len(violations) == 0

    return {
        "project_id": project_id,
        "checked_at": datetime.utcnow().isoformat(),
        "sla_met": sla_met,
        "total_checks": len(checks),
        "passed_checks": len(checks) - len(violations),
        "violations": violations,
        "checks": checks,
        "metrics": metrics,
    }


# ---------------------------------------------------------------------------
# Store metric snapshot
# ---------------------------------------------------------------------------
def store_snapshot(
    project_id: str,
    metrics: dict,
    source: str = "prometheus",
    db_path: Path = None,
) -> int:
    """Store a metrics snapshot in the metric_snapshots table.

    Args:
        project_id: Project identifier
        metrics: Dict of metric_name -> metric_value
        source: Metric source identifier
        db_path: Override database path

    Returns:
        Number of rows inserted
    """
    conn = _get_db(db_path)
    now = datetime.utcnow().isoformat()
    count = 0

    try:
        for name, value in metrics.items():
            if value is not None:
                try:
                    float_val = float(value)
                except (ValueError, TypeError):
                    continue

                conn.execute(
                    """INSERT INTO metric_snapshots
                       (project_id, metric_name, metric_value, labels, source, collected_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (project_id, name, float_val, None, source, now),
                )
                count += 1

        conn.commit()
        return count

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Prometheus metric collector")
    parser.add_argument("--project", required=True, help="Project ID")
    parser.add_argument("--prom-url", default=DEFAULT_PROMETHEUS_URL, help="Prometheus URL")
    parser.add_argument("--namespace", help="Kubernetes namespace")
    parser.add_argument("--check-sla", action="store_true", help="Check metrics against SLA")
    parser.add_argument("--query", help="Custom PromQL query")
    parser.add_argument("--range-query", help="Custom PromQL range query")
    parser.add_argument("--start", help="Range query start time")
    parser.add_argument("--end", help="Range query end time")
    parser.add_argument("--step", default="60s", help="Range query step")
    parser.add_argument("--store", action="store_true", help="Store snapshot to database")
    parser.add_argument("--format", choices=["json", "text"], default="text", help="Output format")
    parser.add_argument("--db-path", help="Database path override")
    args = parser.parse_args()

    db_path = Path(args.db_path) if args.db_path else None

    # Custom instant query
    if args.query:
        result = query_instant(args.query, args.prom_url)
        print(json.dumps(result, indent=2))
        return

    # Custom range query
    if args.range_query:
        result = query_range(args.range_query, args.start, args.end, args.step, args.prom_url)
        print(json.dumps(result, indent=2))
        return

    # SLA check
    if args.check_sla:
        result = check_sla(args.project, prom_url=args.prom_url, namespace=args.namespace)

        if args.format == "json":
            print(json.dumps(result, indent=2))
        else:
            print(f"\n{'='*60}")
            print(f"  SLA CHECK — {result['project_id']}")
            print(f"  Status: {'PASSED' if result['sla_met'] else 'VIOLATIONS DETECTED'}")
            print(f"{'='*60}")

            for check in result["checks"]:
                status = "PASS" if check["passed"] else "FAIL"
                print(f"  [{status:>4s}] {check['metric']}: "
                      f"{check['current']} {check['unit']} "
                      f"(threshold: {check['threshold']} {check['unit']})")

            if result["violations"]:
                print(f"\n  ** {len(result['violations'])} SLA VIOLATIONS **")

            print(f"\n{'='*60}")
        return

    # Standard metrics collection
    result = get_application_metrics(args.project, args.prom_url, args.namespace)

    if args.store:
        stored = store_snapshot(args.project, result.get("metrics", {}), db_path=db_path)
        result["stored_metrics"] = stored
        print(f"[metrics] Stored {stored} metric values to database")

    if args.format == "json":
        print(json.dumps(result, indent=2))
    else:
        print(f"\n{'='*60}")
        print(f"  APPLICATION METRICS — {result['project_id']}")
        print(f"  Collected: {result['collected_at']}")
        print(f"{'='*60}")

        metrics = result.get("metrics", {})
        for name, value in sorted(metrics.items()):
            if value is not None:
                # Format nicely
                if "bytes" in name:
                    formatted = f"{value / (1024**2):.1f} MB"
                elif "pct" in name or "rate" in name:
                    formatted = f"{value:.3f}"
                elif "latency" in name:
                    formatted = f"{value*1000:.1f} ms"
                else:
                    formatted = f"{value:.2f}"
                print(f"  {name:>30s}: {formatted}")
            else:
                print(f"  {name:>30s}: N/A")

        if result.get("errors"):
            print("\n  Warnings:")
            for err in result["errors"][:5]:
                print(f"    - {err}")

        print(f"\n{'='*60}")


if __name__ == "__main__":
    main()
