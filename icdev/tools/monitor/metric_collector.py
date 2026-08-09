#!/usr/bin/env python3
# CUI // SP-CTI
"""Prometheus metric collector. Queries Prometheus API for instant and range queries,
collects application metrics, checks SLA compliance, and stores metric snapshots."""

import argparse
import json
import sqlite3
import urllib.request
import urllib.parse
import urllib.error
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import get_connection
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

DEFAULT_PROMETHEUS_URL = "http://localhost:9090"


def _get_db(db_path: Path = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    conn = get_connection(db_path=str(path))
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
        with urllib.request.urlopen(req, timeout=15) as resp:  # nosec B310 -- URL scheme validated; internal/configured endpoints only
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

    now = datetime.now(timezone.utc)
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
        with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310 -- URL scheme validated; internal/configured endpoints only
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("status") == "success":
                results = data.get("data", {}).get("result", [])
                return {
                    "status": "success",
                    "query": promql,
                    "result_type": data.get("data", {}).get("resultType"),
                    "results": results,
                    "data_points": sum(len(r.get("values", [])) for r in results),
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
        "latency_p50": f'histogram_quantile(0.50, sum(rate(http_request_duration_seconds_bucket{{namespace="{ns}"}}[5m])) by (le))',  # noqa: E501
        "latency_p95": f'histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket{{namespace="{ns}"}}[5m])) by (le))',  # noqa: E501
        "latency_p99": f'histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket{{namespace="{ns}"}}[5m])) by (le))',  # noqa: E501
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
        "collected_at": datetime.now(timezone.utc).isoformat(),
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
        "checked_at": datetime.now(timezone.utc).isoformat(),
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
    now = datetime.now(timezone.utc).isoformat()
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
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (project_id, name, float_val, None, source, now),
                )
                count += 1

        conn.commit()
        return count

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Metric anomaly detection (anomaly_detection paradigm)
# ---------------------------------------------------------------------------
# The SLA check above compares each metric to a hardcoded threshold — it cannot
# catch a metric drifting badly while still inside its SLA, nor adapt to a
# service whose normal baseline differs from the global default. This section
# scores each current value against its own historical metric_snapshots using a
# configurable z-score or robust MAD (median absolute deviation) method. The
# tuning block lives in args/monitoring_config.yaml under ``metric_anomaly`` and
# degrades to these defaults whenever the file, PyYAML, or a key is missing.
_METRIC_ANOMALY_CONFIG_PATH = BASE_DIR / "args" / "monitoring_config.yaml"
_DEFAULT_METRIC_ANOMALY_CFG = {
    "method": "zscore",
    "z_threshold": 3.0,
    "mad_threshold": 3.5,
    "min_samples": 5,
    "direction": "both",
    "history_limit": 200,
}


def _median(values: list) -> float:
    """Return the median of a numeric list (0.0 for an empty list)."""
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return float(s[mid]) if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _load_metric_anomaly_cfg(config_path: Path = None) -> dict:
    """Load the ``metric_anomaly`` tuning block from monitoring_config.yaml.

    Returns defaults merged with any configured overrides. A missing file,
    absent PyYAML, or absent key always yields the documented defaults — anomaly
    detection must never break on configuration.
    """
    cfg = dict(_DEFAULT_METRIC_ANOMALY_CFG)
    path = config_path or _METRIC_ANOMALY_CONFIG_PATH
    try:
        import yaml

        with open(path, encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh) or {}
        section = loaded.get("metric_anomaly") or {}
        for key in cfg:
            if section.get(key) is not None:
                cfg[key] = section[key]
    except Exception:
        pass  # Any failure → defaults.
    return cfg


def _detect_value_anomaly(current: float, history: list, cfg: dict) -> dict:
    """Score a single current value against its historical baseline.

    Args:
        current: The newly observed metric value.
        history: Prior numeric values for the same metric (any order).
        cfg: A ``metric_anomaly`` config (see ``_load_metric_anomaly_cfg``).

    Methods (``cfg["method"]``):
        * ``zscore`` — classic mean/std-dev z-score: flag where
          |current - mean| / std exceeds ``z_threshold``.
        * ``mad`` — robust modified z-score (Iglewicz-Hoaglin):
          0.6745 * (current - median) / MAD, flagged at ``mad_threshold``. MAD
          is the median absolute deviation; resistant to outliers in history.

    ``direction`` restricts flagging to ``high`` (spikes only), ``low`` (drops
    only), or ``both``. Returns ``None`` when there is too little history
    (``min_samples``) or the value is within normal range.
    """
    samples = [float(v) for v in history if v is not None]
    min_samples = int(cfg.get("min_samples", 5) or 5)
    if len(samples) < max(2, min_samples):
        return None

    method = str(cfg.get("method", "zscore")).lower()
    direction = str(cfg.get("direction", "both")).lower()

    if method == "mad":
        med = _median(samples)
        mad = _median([abs(v - med) for v in samples])
        threshold = float(cfg.get("mad_threshold", 3.5))
        if mad > 0:
            score = 0.6745 * (current - med) / mad
        else:
            # Degenerate spread (≥ half the samples identical): any departure
            # from the median is anomalous; preserve sign for direction checks.
            if current == med:
                return None
            score = float("inf") if current > med else float("-inf")
        baseline, score_key = med, "mod_z_score"
    else:  # zscore (default)
        mean = sum(samples) / len(samples)
        variance = sum((v - mean) ** 2 for v in samples) / len(samples)
        std_dev = variance**0.5
        threshold = float(cfg.get("z_threshold", 3.0))
        if std_dev == 0:
            if current == mean:
                return None
            score = float("inf") if current > mean else float("-inf")
        else:
            score = (current - mean) / std_dev
        baseline, score_key = mean, "z_score"

    high = score > threshold
    low = score < -threshold
    if direction == "high" and not high:
        return None
    if direction == "low" and not low:
        return None
    if direction == "both" and not (high or low):
        return None

    finite = score not in (float("inf"), float("-inf"))
    return {
        "current": round(current, 4),
        "baseline": round(baseline, 4),
        score_key: round(score, 2) if finite else None,
        "direction": "high" if score > 0 else "low",
        "method": method if method == "mad" else "zscore",
        "samples": len(samples),
    }


def _metric_history(
    project_id: str,
    metric_name: str,
    exclude_value: float = None,
    limit: int = 200,
    db_path: Path = None,
) -> list:
    """Return recent historical values for a metric from metric_snapshots,
    most-recent first, excluding an optional just-collected value."""
    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            """SELECT metric_value FROM metric_snapshots
               WHERE project_id = %s AND metric_name = %s
               ORDER BY collected_at DESC, id DESC
               LIMIT %s""",
            (project_id, metric_name, int(limit)),
        ).fetchall()
    except Exception:
        return []
    finally:
        conn.close()

    values = []
    for r in rows:
        try:
            values.append(float(r[0]))
        except (ValueError, TypeError, IndexError):
            continue
    if exclude_value is not None and values and values[0] == exclude_value:
        values = values[1:]  # drop the snapshot we just stored, if present
    return values


def detect_metric_anomalies(
    project_id: str,
    current_metrics: dict = None,
    prom_url: str = None,
    namespace: str = None,
    config_path: Path = None,
    db_path: Path = None,
    method: str = None,
) -> dict:
    """Flag current metrics that deviate anomalously from their own history.

    Complements ``check_sla``: rather than a fixed threshold, each metric is
    scored against its prior values in metric_snapshots. Collects current
    metrics from Prometheus when ``current_metrics`` is not supplied.

    Args:
        project_id: Project identifier.
        current_metrics: Pre-collected ``{name: value}`` map; collected via
            ``get_application_metrics`` when omitted.
        prom_url, namespace: Forwarded to collection when needed.
        config_path: Override path to monitoring_config.yaml.
        db_path: Override database path (history source).
        method: Optional ``"zscore"``/``"mad"`` override of the configured method.

    Returns:
        A result dict with the list of ``anomalies`` and the config used.
    """
    cfg = _load_metric_anomaly_cfg(config_path)
    if method:
        cfg["method"] = method

    if current_metrics is None:
        collected = get_application_metrics(project_id, prom_url, namespace)
        current_metrics = collected.get("metrics", {})

    limit = int(cfg.get("history_limit", 200) or 200)
    anomalies = []
    evaluated = 0
    for name, value in current_metrics.items():
        if value is None:
            continue
        try:
            current = float(value)
        except (ValueError, TypeError):
            continue
        history = _metric_history(project_id, name, exclude_value=current, limit=limit, db_path=db_path)
        evaluated += 1
        result = _detect_value_anomaly(current, history, cfg)
        if result:
            anomalies.append({"metric": name, **result})

    return {
        "project_id": project_id,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "method": cfg.get("method"),
        "metrics_evaluated": evaluated,
        "anomaly_count": len(anomalies),
        "anomalies": anomalies,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Prometheus metric collector")
    parser.add_argument("--project-id", "--project", required=True, help="Project ID", dest="project_id")
    parser.add_argument("--prom-url", default=DEFAULT_PROMETHEUS_URL, help="Prometheus URL")
    parser.add_argument("--namespace", help="Kubernetes namespace")
    parser.add_argument("--check-sla", action="store_true", help="Check metrics against SLA")
    parser.add_argument(
        "--detect-anomalies",
        action="store_true",
        help="Score current metrics against their historical baseline (anomaly_detection)",
    )
    parser.add_argument(
        "--anomaly-method",
        choices=["zscore", "mad"],
        help="Override anomaly detection method (zscore=mean/std-dev, mad=robust median-based)",
    )
    parser.add_argument("--query", help="Custom PromQL query")
    parser.add_argument("--range-query", help="Custom PromQL range query")
    parser.add_argument("--start", help="Range query start time")
    parser.add_argument("--end", help="Range query end time")
    parser.add_argument("--step", default="60s", help="Range query step")
    parser.add_argument("--store", action="store_true", help="Store snapshot to database")
    parser.add_argument("--format", choices=["json", "text"], default="text", help="Output format")
    parser.add_argument("--db-path", help="Database path override")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
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

    # Anomaly detection against historical baseline
    if args.detect_anomalies:
        collected = get_application_metrics(args.project_id, args.prom_url, args.namespace)
        result = detect_metric_anomalies(
            args.project_id,
            current_metrics=collected.get("metrics", {}),
            config_path=None,
            db_path=db_path,
            method=args.anomaly_method,
        )

        if args.format == "json" or args.json_output:
            print(json.dumps(result, indent=2))
        else:
            print(f"\n{'=' * 60}")
            print(f"  METRIC ANOMALY SCAN — {result['project_id']}")
            print(f"  Method: {result.get('method')}  |  Evaluated: {result['metrics_evaluated']}")
            print(f"{'=' * 60}")
            if not result["anomalies"]:
                print("  No anomalies detected.")
            for a in result["anomalies"]:
                score = a.get("z_score", a.get("mod_z_score"))
                print(
                    f"  [{a['direction'].upper():>4s}] {a['metric']}: "
                    f"current={a['current']} baseline={a['baseline']} "
                    f"score={score} (n={a['samples']})"
                )
            print(f"\n{'=' * 60}")
        return

    # SLA check
    if args.check_sla:
        result = check_sla(args.project_id, prom_url=args.prom_url, namespace=args.namespace)

        if args.format == "json":
            print(json.dumps(result, indent=2))
        else:
            print(f"\n{'=' * 60}")
            print(f"  SLA CHECK — {result['project_id']}")
            print(f"  Status: {'PASSED' if result['sla_met'] else 'VIOLATIONS DETECTED'}")
            print(f"{'=' * 60}")

            for check in result["checks"]:
                status = "PASS" if check["passed"] else "FAIL"
                print(
                    f"  [{status:>4s}] {check['metric']}: "
                    f"{check['current']} {check['unit']} "
                    f"(threshold: {check['threshold']} {check['unit']})"
                )

            if result["violations"]:
                print(f"\n  ** {len(result['violations'])} SLA VIOLATIONS **")

            print(f"\n{'=' * 60}")
        return

    # Standard metrics collection
    result = get_application_metrics(args.project_id, args.prom_url, args.namespace)

    if args.store:
        stored = store_snapshot(args.project_id, result.get("metrics", {}), db_path=db_path)
        result["stored_metrics"] = stored
        print(f"[metrics] Stored {stored} metric values to database")

    if args.format == "json":
        print(json.dumps(result, indent=2))
    else:
        print(f"\n{'=' * 60}")
        print(f"  APPLICATION METRICS — {result['project_id']}")
        print(f"  Collected: {result['collected_at']}")
        print(f"{'=' * 60}")

        metrics = result.get("metrics", {})
        for name, value in sorted(metrics.items()):
            if value is not None:
                # Format nicely
                if "bytes" in name:
                    formatted = f"{value / (1024**2):.1f} MB"
                elif "pct" in name or "rate" in name:
                    formatted = f"{value:.3f}"
                elif "latency" in name:
                    formatted = f"{value * 1000:.1f} ms"
                else:
                    formatted = f"{value:.2f}"
                print(f"  {name:>30s}: {formatted}")
            else:
                print(f"  {name:>30s}: N/A")

        if result.get("errors"):
            print("\n  Warnings:")
            for err in result["errors"][:5]:
                print(f"    - {err}")

        print(f"\n{'=' * 60}")


if __name__ == "__main__":
    main()
