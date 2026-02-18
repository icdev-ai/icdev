#!/usr/bin/env python3
"""Application health checker. Performs HTTP health checks with retries,
checks Kubernetes pod status, and reports health status."""

import argparse
import json
import subprocess
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


# ---------------------------------------------------------------------------
# HTTP health check
# ---------------------------------------------------------------------------
def check_health(
    url: str,
    timeout: int = 5,
    expected_status: int = 200,
    expected_body: str = None,
) -> dict:
    """Perform a single HTTP GET health check.

    Args:
        url: Health endpoint URL
        timeout: Request timeout in seconds
        expected_status: Expected HTTP status code
        expected_body: Optional string that must be present in response body

    Returns:
        Dict with status, response_time_ms, status_code, etc.
    """
    result = {
        "url": url,
        "timestamp": datetime.utcnow().isoformat(),
        "healthy": False,
        "status_code": None,
        "response_time_ms": None,
        "error": None,
    }

    start = time.time()

    try:
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", "ICDev-HealthChecker/1.0")
        req.add_header("Accept", "application/json, text/plain, */*")

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            elapsed = (time.time() - start) * 1000
            body = resp.read().decode("utf-8", errors="replace")

            result["status_code"] = resp.status
            result["response_time_ms"] = round(elapsed, 1)
            result["response_size_bytes"] = len(body)

            # Check status code
            if resp.status != expected_status:
                result["error"] = f"Expected status {expected_status}, got {resp.status}"
                return result

            # Check response body if specified
            if expected_body and expected_body not in body:
                result["error"] = f"Expected body to contain '{expected_body}'"
                return result

            # Try to parse JSON health response
            try:
                health_data = json.loads(body)
                result["health_data"] = health_data

                # Common health endpoint formats
                status_field = (
                    health_data.get("status") or
                    health_data.get("health") or
                    health_data.get("state")
                )
                if isinstance(status_field, str):
                    healthy_values = ("ok", "healthy", "up", "pass", "alive", "green")
                    if status_field.lower() in healthy_values:
                        result["healthy"] = True
                    elif status_field.lower() in ("degraded", "warn", "warning"):
                        result["healthy"] = True
                        result["degraded"] = True
                    else:
                        result["error"] = f"Health status: {status_field}"
                else:
                    # If we got 200 and no explicit status field, assume healthy
                    result["healthy"] = True

            except json.JSONDecodeError:
                # Non-JSON response with 200 status = healthy
                result["healthy"] = True

            return result

    except urllib.error.HTTPError as e:
        elapsed = (time.time() - start) * 1000
        result["status_code"] = e.code
        result["response_time_ms"] = round(elapsed, 1)
        result["error"] = f"HTTP {e.code}: {e.reason}"
        return result

    except urllib.error.URLError as e:
        elapsed = (time.time() - start) * 1000
        result["response_time_ms"] = round(elapsed, 1)
        result["error"] = f"Connection failed: {e.reason}"
        return result

    except TimeoutError:
        elapsed = (time.time() - start) * 1000
        result["response_time_ms"] = round(elapsed, 1)
        result["error"] = f"Timeout after {timeout}s"
        return result

    except Exception as e:
        elapsed = (time.time() - start) * 1000
        result["response_time_ms"] = round(elapsed, 1)
        result["error"] = str(e)
        return result


# ---------------------------------------------------------------------------
# Health check with retries
# ---------------------------------------------------------------------------
def check_with_retries(
    url: str,
    retries: int = 3,
    interval: int = 5,
    timeout: int = 5,
    expected_status: int = 200,
    expected_body: str = None,
) -> dict:
    """Perform health check with retries on failure.

    Args:
        url: Health endpoint URL
        retries: Maximum number of attempts
        interval: Seconds between retries
        timeout: Per-request timeout in seconds
        expected_status: Expected HTTP status code
        expected_body: Optional string that must be present in response body

    Returns:
        Dict with final result and attempt history.
    """
    attempts = []
    last_result = None

    for attempt in range(1, retries + 1):
        result = check_health(url, timeout, expected_status, expected_body)
        result["attempt"] = attempt

        attempts.append({
            "attempt": attempt,
            "healthy": result["healthy"],
            "status_code": result["status_code"],
            "response_time_ms": result["response_time_ms"],
            "error": result.get("error"),
        })

        last_result = result

        if result["healthy"]:
            break

        if attempt < retries:
            print(f"[health-check] Attempt {attempt}/{retries} failed: {result.get('error')}. "
                  f"Retrying in {interval}s...")
            time.sleep(interval)

    # Compute summary
    successful_attempts = sum(1 for a in attempts if a["healthy"])
    response_times = [a["response_time_ms"] for a in attempts if a["response_time_ms"] is not None]

    summary = {
        "url": url,
        "timestamp": datetime.utcnow().isoformat(),
        "healthy": last_result["healthy"] if last_result else False,
        "total_attempts": len(attempts),
        "successful_attempts": successful_attempts,
        "final_status_code": last_result.get("status_code") if last_result else None,
        "final_response_time_ms": last_result.get("response_time_ms") if last_result else None,
        "avg_response_time_ms": round(sum(response_times) / len(response_times), 1) if response_times else None,
        "max_response_time_ms": max(response_times) if response_times else None,
        "error": last_result.get("error") if last_result and not last_result["healthy"] else None,
        "health_data": last_result.get("health_data") if last_result else None,
        "degraded": last_result.get("degraded", False) if last_result else False,
        "attempts": attempts,
    }

    return summary


# ---------------------------------------------------------------------------
# Kubernetes pod health check
# ---------------------------------------------------------------------------
def check_k8s_pods(
    namespace: str,
    deployment: str = None,
    label_selector: str = None,
) -> dict:
    """Check Kubernetes pod status using kubectl.

    Args:
        namespace: Kubernetes namespace
        deployment: Deployment name (will be used as label selector if no label_selector)
        label_selector: Custom label selector (e.g., 'app=myapp')

    Returns:
        Dict with pod status, readiness, and health summary.
    """
    result = {
        "namespace": namespace,
        "deployment": deployment,
        "timestamp": datetime.utcnow().isoformat(),
        "healthy": False,
        "pods": [],
        "summary": {},
    }

    # Build kubectl command
    cmd = ["kubectl", "get", "pods", "-n", namespace, "-o", "json"]
    if label_selector:
        cmd.extend(["-l", label_selector])
    elif deployment:
        cmd.extend(["-l", f"app.kubernetes.io/name={deployment}"])

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)

        if proc.returncode != 0:
            result["error"] = f"kubectl failed: {proc.stderr.strip()}"
            return result

        pods_json = json.loads(proc.stdout)
        items = pods_json.get("items", [])

        total = len(items)
        ready = 0
        pending = 0
        failed = 0
        total_restarts = 0
        pods = []

        for item in items:
            pod_name = item.get("metadata", {}).get("name", "unknown")
            phase = item.get("status", {}).get("phase", "Unknown")

            # Check readiness
            conditions = item.get("status", {}).get("conditions", [])
            is_ready = any(
                c.get("type") == "Ready" and c.get("status") == "True"
                for c in conditions
            )

            # Container statuses
            container_statuses = item.get("status", {}).get("containerStatuses", [])
            restarts = sum(cs.get("restartCount", 0) for cs in container_statuses)
            total_restarts += restarts

            # Check for crash loops
            crash_loop = any(
                cs.get("state", {}).get("waiting", {}).get("reason") == "CrashLoopBackOff"
                for cs in container_statuses
            )

            # Check container ready state
            containers_ready = sum(1 for cs in container_statuses if cs.get("ready"))
            containers_total = len(container_statuses)

            pod_info = {
                "name": pod_name,
                "phase": phase,
                "ready": is_ready,
                "containers": f"{containers_ready}/{containers_total}",
                "restarts": restarts,
                "crash_loop": crash_loop,
                "age": item.get("metadata", {}).get("creationTimestamp", ""),
            }

            pods.append(pod_info)

            if is_ready:
                ready += 1
            elif phase == "Pending":
                pending += 1
            elif phase == "Failed" or crash_loop:
                failed += 1

        result["pods"] = pods
        result["summary"] = {
            "total": total,
            "ready": ready,
            "pending": pending,
            "failed": failed,
            "total_restarts": total_restarts,
            "crash_loops": sum(1 for p in pods if p["crash_loop"]),
        }

        # Overall health
        if total == 0:
            result["healthy"] = False
            result["status"] = "no_pods"
        elif ready == total:
            result["healthy"] = True
            result["status"] = "healthy"
        elif ready > 0:
            result["healthy"] = True
            result["status"] = "degraded"
            result["degraded"] = True
        else:
            result["healthy"] = False
            result["status"] = "unhealthy"

        # Warnings
        warnings = []
        if total_restarts > 10:
            warnings.append(f"High restart count: {total_restarts}")
        if any(p["crash_loop"] for p in pods):
            warnings.append("CrashLoopBackOff detected")
        if pending > 0:
            warnings.append(f"{pending} pods pending")
        result["warnings"] = warnings

    except FileNotFoundError:
        result["error"] = "kubectl not found. Ensure kubectl is installed and in PATH."
    except subprocess.TimeoutExpired:
        result["error"] = "kubectl timed out after 15s"
    except json.JSONDecodeError as e:
        result["error"] = f"Failed to parse kubectl output: {e}"

    return result


# ---------------------------------------------------------------------------
# Comprehensive health check
# ---------------------------------------------------------------------------
def check_all(
    url: str = None,
    namespace: str = None,
    deployment: str = None,
    retries: int = 3,
    interval: int = 5,
    timeout: int = 5,
) -> dict:
    """Run both HTTP and K8s health checks and produce a combined report."""
    result = {
        "timestamp": datetime.utcnow().isoformat(),
        "overall_healthy": True,
        "checks": {},
    }

    if url:
        http_result = check_with_retries(url, retries, interval, timeout)
        result["checks"]["http"] = http_result
        if not http_result["healthy"]:
            result["overall_healthy"] = False

    if namespace:
        k8s_result = check_k8s_pods(namespace, deployment)
        result["checks"]["k8s"] = k8s_result
        if not k8s_result.get("healthy"):
            result["overall_healthy"] = False

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Application health checker")
    parser.add_argument("--url", help="Health endpoint URL")
    parser.add_argument("--retries", type=int, default=3, help="Number of retry attempts")
    parser.add_argument("--interval", type=int, default=5, help="Seconds between retries")
    parser.add_argument("--timeout", type=int, default=5, help="Request timeout in seconds")
    parser.add_argument("--expected-status", type=int, default=200, help="Expected HTTP status code")
    parser.add_argument("--expected-body", help="String that must appear in response body")
    parser.add_argument("--namespace", help="Kubernetes namespace for pod check")
    parser.add_argument("--deployment", help="Kubernetes deployment name")
    parser.add_argument("--label-selector", help="Kubernetes label selector")
    parser.add_argument("--format", choices=["json", "text"], default="text", help="Output format")
    args = parser.parse_args()

    if not args.url and not args.namespace:
        parser.error("At least one of --url or --namespace is required")

    results = {}

    # HTTP health check
    if args.url:
        print(f"[health-check] Checking {args.url} (retries={args.retries}, "
              f"timeout={args.timeout}s, interval={args.interval}s)...")

        http_result = check_with_retries(
            url=args.url,
            retries=args.retries,
            interval=args.interval,
            timeout=args.timeout,
            expected_status=args.expected_status,
            expected_body=args.expected_body,
        )
        results["http"] = http_result

    # K8s pod check
    if args.namespace:
        print(f"[health-check] Checking K8s pods in namespace={args.namespace}...")

        k8s_result = check_k8s_pods(
            namespace=args.namespace,
            deployment=args.deployment,
            label_selector=args.label_selector,
        )
        results["k8s"] = k8s_result

    # Output
    if args.format == "json":
        print(json.dumps(results, indent=2))
    else:
        print(f"\n{'='*60}")
        print(f"  HEALTH CHECK REPORT")
        print(f"  Timestamp: {datetime.utcnow().isoformat()}")
        print(f"{'='*60}")

        if "http" in results:
            r = results["http"]
            status = "HEALTHY" if r["healthy"] else "UNHEALTHY"
            if r.get("degraded"):
                status = "DEGRADED"

            print(f"\n  HTTP Health Check:")
            print(f"    URL:      {r['url']}")
            print(f"    Status:   {status}")
            print(f"    Code:     {r.get('final_status_code', 'N/A')}")
            print(f"    Response: {r.get('final_response_time_ms', 'N/A')} ms")
            print(f"    Attempts: {r.get('successful_attempts', 0)}/{r.get('total_attempts', 0)} successful")

            if r.get("avg_response_time_ms"):
                print(f"    Avg time: {r['avg_response_time_ms']} ms")
            if r.get("max_response_time_ms"):
                print(f"    Max time: {r['max_response_time_ms']} ms")
            if r.get("error"):
                print(f"    Error:    {r['error']}")

            # Show health data if available
            if r.get("health_data") and isinstance(r["health_data"], dict):
                print(f"    Health data:")
                for k, v in r["health_data"].items():
                    print(f"      {k}: {v}")

        if "k8s" in results:
            r = results["k8s"]
            status = r.get("status", "unknown").upper()
            summary = r.get("summary", {})

            print(f"\n  Kubernetes Pod Check:")
            print(f"    Namespace:  {r.get('namespace', 'N/A')}")
            print(f"    Deployment: {r.get('deployment', 'N/A')}")
            print(f"    Status:     {status}")
            print(f"    Pods:       {summary.get('ready', 0)}/{summary.get('total', 0)} ready")

            if summary.get("pending"):
                print(f"    Pending:    {summary['pending']}")
            if summary.get("failed"):
                print(f"    Failed:     {summary['failed']}")
            if summary.get("total_restarts"):
                print(f"    Restarts:   {summary['total_restarts']}")
            if summary.get("crash_loops"):
                print(f"    CrashLoops: {summary['crash_loops']}")

            if r.get("warnings"):
                print(f"    Warnings:")
                for w in r["warnings"]:
                    print(f"      - {w}")

            if r.get("error"):
                print(f"    Error: {r['error']}")

            if r.get("pods"):
                print(f"\n    Pods:")
                for pod in r["pods"]:
                    status_icon = "ready" if pod["ready"] else "NOT ready"
                    crash = " [CrashLoop!]" if pod["crash_loop"] else ""
                    print(f"      {pod['name']}: {pod['phase']} ({status_icon}) "
                          f"containers={pod['containers']} restarts={pod['restarts']}{crash}")

        # Overall
        all_healthy = all(
            r.get("healthy", False) for r in results.values()
        )
        print(f"\n  Overall: {'HEALTHY' if all_healthy else 'UNHEALTHY'}")
        print(f"{'='*60}")

        # Exit code
        if not all_healthy:
            exit(1)


if __name__ == "__main__":
    main()
