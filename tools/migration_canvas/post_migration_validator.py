# CUI // SP-CTI
"""Post-migration validation suite — server migration.

Runs 6 deterministic checks against target servers after cutover.
No mandatory LLM dependency.  Writes results to mc_srv_post_migration_tests.

Public functions:
    check_connectivity(target_ip, ports, timeout)  → dict
    check_ssl_cert(hostname, port, timeout)        → dict
    check_service_health(url, expected_status, timeout) → dict
    check_dns_resolution(fqdn, expected_ip)        → dict
    check_disk_space(host, mount, min_pct, creds)  → dict
    check_process_running(host, process_name, creds) → dict
    run_validation_suite(session_id, targets)      → dict
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import socket
import ssl
import subprocess
import time
import urllib.parse
import urllib.request
import urllib.error
import uuid
from datetime import datetime, timezone

logger = get_logger("icdev.migration_canvas.post_migration_validator")

_TIMEOUT = 10
_POST_MIGRATION_TESTS_DDL = """
CREATE TABLE IF NOT EXISTS mc_srv_post_migration_tests (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    run_at      TEXT NOT NULL,
    check_type  TEXT NOT NULL,
    target      TEXT NOT NULL,
    status      TEXT NOT NULL CHECK(status IN ('pass','fail','skip','error')),
    detail      TEXT,
    elapsed_ms  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_mc_srv_pmtest_session ON mc_srv_post_migration_tests(session_id);
CREATE INDEX IF NOT EXISTS idx_mc_srv_pmtest_status ON mc_srv_post_migration_tests(status);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn():
    from tools.migration_canvas.db.init_db import get_connection
    return get_connection()


def _ensure_table():
    with _conn() as db:
        db.executescript(_POST_MIGRATION_TESTS_DDL)
        db.commit()


def _result(check: str, target: str, status: str, detail: str, elapsed_ms: int) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "check_type": check,
        "target": target,
        "status": status,
        "detail": detail,
        "elapsed_ms": elapsed_ms,
        "run_at": _now(),
    }


# ── Individual checks ─────────────────────────────────────────────────────────

def check_connectivity(target_ip: str, ports: list[int], timeout: int = _TIMEOUT) -> dict:
    """TCP socket connect to each port. Pass if ALL ports respond."""
    start = time.monotonic()
    failed = []
    for port in ports:
        try:
            with socket.create_connection((target_ip, port), timeout=timeout):
                pass
        except OSError as exc:
            failed.append(f"port {port}: {exc}")
    elapsed = int((time.monotonic() - start) * 1000)
    if failed:
        return _result("connectivity", target_ip, "fail", "; ".join(failed), elapsed)
    return _result("connectivity", target_ip, "pass", f"All {len(ports)} ports reachable", elapsed)


def check_ssl_cert(hostname: str, port: int = 443, timeout: int = _TIMEOUT) -> dict:
    """Verify SSL certificate is valid and not expiring within 30 days."""
    import datetime as _dt
    start = time.monotonic()
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((hostname, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert = ssock.getpeercert()
        elapsed = int((time.monotonic() - start) * 1000)
        # Parse expiry
        not_after = cert.get("notAfter", "")
        expiry = _dt.datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z") if not_after else None
        if expiry:
            days_left = (expiry - _dt.datetime.utcnow()).days
            if days_left < 0:
                return _result("ssl_cert", hostname, "fail", f"Certificate expired {-days_left}d ago", elapsed)
            if days_left < 30:
                return _result("ssl_cert", hostname, "fail", f"Certificate expires in {days_left}d", elapsed)
            return _result("ssl_cert", hostname, "pass", f"Valid cert, expires in {days_left}d", elapsed)
        return _result("ssl_cert", hostname, "pass", "Certificate valid (expiry not parsed)", elapsed)
    except ssl.SSLCertVerificationError as exc:
        elapsed = int((time.monotonic() - start) * 1000)
        return _result("ssl_cert", hostname, "fail", f"SSL verification failed: {exc}", elapsed)
    except Exception as exc:
        elapsed = int((time.monotonic() - start) * 1000)
        return _result("ssl_cert", hostname, "error", str(exc), elapsed)


def check_service_health(url: str, expected_status: int = 200, timeout: int = _TIMEOUT) -> dict:
    """HTTP GET to url; pass if response code matches expected_status."""
    start = time.monotonic()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return _result("service_health", url, "error", f"Unsupported URL scheme: {parsed.scheme!r}", 0)
    try:
        ctx = ssl.create_default_context() if parsed.scheme == "https" else None
        req = urllib.request.Request(url, headers={"User-Agent": "ICDEV-PostMigValidation/1.0"})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:  # nosec B310
            code = resp.getcode()
        elapsed = int((time.monotonic() - start) * 1000)
        if code == expected_status:
            return _result("service_health", url, "pass", f"HTTP {code}", elapsed)
        return _result("service_health", url, "fail", f"Expected {expected_status}, got {code}", elapsed)
    except urllib.error.HTTPError as exc:
        elapsed = int((time.monotonic() - start) * 1000)
        if exc.code == expected_status:
            return _result("service_health", url, "pass", f"HTTP {exc.code}", elapsed)
        return _result("service_health", url, "fail", f"HTTP {exc.code}: {exc.reason}", elapsed)
    except Exception as exc:
        elapsed = int((time.monotonic() - start) * 1000)
        return _result("service_health", url, "error", str(exc), elapsed)


def check_dns_resolution(fqdn: str, expected_ip: str = "") -> dict:
    """DNS lookup for fqdn; optionally compare to expected_ip."""
    start = time.monotonic()
    try:
        resolved = socket.gethostbyname(fqdn)
        elapsed = int((time.monotonic() - start) * 1000)
        if expected_ip and resolved != expected_ip:
            return _result(
                "dns_resolution", fqdn, "fail",
                f"Resolved to {resolved}, expected {expected_ip}", elapsed,
            )
        return _result("dns_resolution", fqdn, "pass", f"Resolved to {resolved}", elapsed)
    except OSError as exc:
        elapsed = int((time.monotonic() - start) * 1000)
        return _result("dns_resolution", fqdn, "fail", str(exc), elapsed)


def check_disk_space(
    host: str,
    mount: str = "/",
    min_pct_free: int = 10,
    ssh_user: str = "",
    ssh_key: str = "",
) -> dict:
    """SSH to host, run df -h, check free% on mount."""
    start = time.monotonic()
    is_local = host in ("localhost", "127.0.0.1", "::1")
    try:
        if is_local:
            result = subprocess.run(
                ["df", "-h", mount], capture_output=True, text=True, timeout=15
            )
        else:
            ssh_args = ["ssh", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=no"]
            if ssh_key:
                ssh_args += ["-i", ssh_key]
            if ssh_user:
                ssh_args += [f"{ssh_user}@{host}"]
            else:
                ssh_args += [host]
            ssh_args += [f"df -h {mount}"]
            result = subprocess.run(ssh_args, capture_output=True, text=True, timeout=20)

        elapsed = int((time.monotonic() - start) * 1000)
        if result.returncode != 0:
            return _result("disk_space", f"{host}:{mount}", "error", result.stderr[:300], elapsed)

        lines = result.stdout.strip().splitlines()
        if len(lines) < 2:
            return _result("disk_space", f"{host}:{mount}", "error", "No df output", elapsed)

        # Parse last line: Filesystem Size Used Avail Use% Mounted
        parts = lines[-1].split()
        if len(parts) >= 5:
            use_pct = int(parts[4].rstrip("%"))
            free_pct = 100 - use_pct
            if free_pct < min_pct_free:
                return _result(
                    "disk_space", f"{host}:{mount}", "fail",
                    f"Only {free_pct}% free on {mount} (min {min_pct_free}%)", elapsed,
                )
            return _result(
                "disk_space", f"{host}:{mount}", "pass",
                f"{free_pct}% free on {mount}", elapsed,
            )
        return _result("disk_space", f"{host}:{mount}", "error", "Could not parse df output", elapsed)
    except Exception as exc:
        elapsed = int((time.monotonic() - start) * 1000)
        return _result("disk_space", f"{host}:{mount}", "error", str(exc), elapsed)


def check_process_running(
    host: str,
    process_name: str,
    ssh_user: str = "",
    ssh_key: str = "",
) -> dict:
    """SSH to host and check if process_name is running via pgrep."""
    start = time.monotonic()
    is_local = host in ("localhost", "127.0.0.1", "::1")
    try:
        if is_local:
            result = subprocess.run(
                ["pgrep", "-x", process_name], capture_output=True, text=True, timeout=10
            )
        else:
            ssh_args = ["ssh", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=no"]
            if ssh_key:
                ssh_args += ["-i", ssh_key]
            if ssh_user:
                ssh_args += [f"{ssh_user}@{host}"]
            else:
                ssh_args += [host]
            ssh_args += [f"pgrep -x {process_name}"]
            result = subprocess.run(ssh_args, capture_output=True, text=True, timeout=20)

        elapsed = int((time.monotonic() - start) * 1000)
        running = result.returncode == 0
        status = "pass" if running else "fail"
        detail = f"Process '{process_name}' {'running' if running else 'not found'} on {host}"
        return _result("process_running", f"{host}:{process_name}", status, detail, elapsed)
    except Exception as exc:
        elapsed = int((time.monotonic() - start) * 1000)
        return _result("process_running", f"{host}:{process_name}", "error", str(exc), elapsed)


# ── Convenience aliases ───────────────────────────────────────────────────────

def http_smoke_test(url: str, expected_status: int = 200, timeout: int = _TIMEOUT) -> dict:
    """HTTP GET smoke test; alias for check_service_health with renamed keys."""
    result = check_service_health(url, expected_status=expected_status, timeout=timeout)
    result["response_time_ms"] = result.get("elapsed_ms", 0)
    return result


def db_connectivity_check(
    host: str,
    port: int,
    db_type: str = "postgresql",
    db_name: str = "",
    timeout: int = _TIMEOUT,
) -> dict:
    """TCP connectivity check to a database port."""
    result = check_connectivity(host, [port], timeout=timeout)
    result["ok"] = result.get("status") == "pass"
    result["db_type"] = db_type
    result["db_name"] = db_name
    return result


# ── Aggregate runner ──────────────────────────────────────────────────────────

def run_validation_suite(session_id: str, targets: list[dict]) -> dict:
    """Run all applicable checks against each target and persist results.

    target dict fields (all optional except 'host'):
        host, ports (list[int]), url, fqdn, expected_ip,
        mount, min_pct_free, ssh_user, ssh_key, processes (list[str]),
        checks (list[str]) — subset of check names to run (default: all)

    Returns:
        {
            "session_id": str,
            "run_at": str,
            "results": [check result dicts],
            "summary": {"pass": int, "fail": int, "error": int, "skip": int},
        }
    """
    _ensure_table()
    all_results: list[dict] = []
    run_at = _now()

    ALL_CHECKS = {"connectivity", "ssl_cert", "service_health", "dns_resolution",
                  "disk_space", "process_running"}

    for target in targets:
        host = target.get("host", "")
        enabled = set(target.get("checks", ALL_CHECKS)) & ALL_CHECKS

        if "connectivity" in enabled:
            ports = target.get("ports", [22])
            all_results.append(check_connectivity(host, ports, timeout=target.get("timeout", _TIMEOUT)))

        if "ssl_cert" in enabled:
            ssl_host = target.get("ssl_hostname", host)
            ssl_port = target.get("ssl_port", 443)
            all_results.append(check_ssl_cert(ssl_host, ssl_port))

        if "service_health" in enabled:
            url = target.get("url")
            if url:
                all_results.append(check_service_health(
                    url, target.get("expected_status", 200)
                ))
            else:
                all_results.append(_result("service_health", host, "skip", "No URL provided", 0))

        if "dns_resolution" in enabled:
            fqdn = target.get("fqdn", host)
            expected_ip = target.get("expected_ip", "")
            all_results.append(check_dns_resolution(fqdn, expected_ip))

        if "disk_space" in enabled:
            all_results.append(check_disk_space(
                host,
                target.get("mount", "/"),
                target.get("min_pct_free", 10),
                target.get("ssh_user", ""),
                target.get("ssh_key", ""),
            ))

        if "process_running" in enabled:
            for proc in target.get("processes", []):
                all_results.append(check_process_running(
                    host, proc,
                    target.get("ssh_user", ""),
                    target.get("ssh_key", ""),
                ))

    # Persist to DB
    try:
        with _conn() as db:
            for r in all_results:
                db.execute(
                    "INSERT OR IGNORE INTO mc_srv_post_migration_tests "
                    "(id, session_id, run_at, check_type, target, status, detail, elapsed_ms) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (r["id"], session_id, r.get("run_at", run_at),
                     r["check_type"], r["target"], r["status"],
                     r.get("detail", ""), r.get("elapsed_ms", 0)),
                )
            db.commit()
    except Exception as exc:
        logger.warning("Failed to persist validation results: %s", exc)

    summary: dict[str, int] = {"pass": 0, "fail": 0, "error": 0, "skip": 0}
    for r in all_results:
        summary[r["status"]] = summary.get(r["status"], 0) + 1

    return {
        "session_id": session_id,
        "run_at": run_at,
        "results": all_results,
        "summary": summary,
    }
