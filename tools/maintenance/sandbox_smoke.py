#!/usr/bin/env python3
# CUI // SP-CTI
"""OPT-57 — Daily sandbox liveness probe.

Runs a cheap end-to-end check against tools/security/sandbox_executor.py:
  1. Call SandboxExecutor.health_check() — confirm Docker + llm-sandbox reachable.
  2. If healthy, run a tiny smoke payload (`print('ok')`) through
     SandboxExecutor.execute() and verify the container returned exit_code=0.
  3. Write ONE audit_trail row per invocation (event_type='compliance_check',
     action='sandbox.liveness_probe', executor_type='liveness_probe' in details).
  4. Print a JSON summary suitable for cron / dashboard tiles.

Exit codes:
  0 — healthy (probe + smoke both OK)
  1 — degraded (health probe failed; Docker down or llm-sandbox missing)
  2 — smoke failed (sandbox reachable but execute() returned non-zero)

Usage:
    python tools/maintenance/sandbox_smoke.py --json
    python tools/maintenance/sandbox_smoke.py --json --timeout 20

Scheduling (Linux cron):
    0 3 * * * /usr/bin/python /opt/icdev/tools/maintenance/sandbox_smoke.py --json \
        >> /var/log/icdev/sandbox_smoke.log 2>&1

Scheduling (Windows Task Scheduler):
    schtasks /Create /TN ICDEVSandboxSmoke /TR \
        "python.exe C:/ICDev/tools/maintenance/sandbox_smoke.py --json" \
        /SC DAILY /ST 03:00
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

SMOKE_PAYLOAD = "print('ok')"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_audit(result: dict, db_path: Path | None = None) -> int | None:
    """Append one audit_trail row. Best-effort; silent on failure."""
    try:
        from tools.audit.audit_logger import log_event

        return log_event(
            event_type="compliance_check",
            actor="sandbox_liveness_probe",
            action="sandbox.liveness_probe",
            details={
                "executor_type": "liveness_probe",
                "health": result.get("health", {}),
                "smoke": result.get("smoke", {}),
                "exit_code": result.get("exit_code"),
                "probe_at": result.get("probe_at"),
                "duration_ms": result.get("duration_ms"),
            },
            classification="CUI",
            db_path=db_path,
        )
    except Exception as exc:
        # Best-effort logging — never crash the probe on audit failure
        result.setdefault("_audit_error", str(exc)[:200])
        return None


def run_probe(timeout_seconds: int = 30, *, write_audit: bool = True) -> dict[str, Any]:
    """Execute the full probe. Returns a serializable result dict."""
    started = time.time()
    result: dict[str, Any] = {
        "probe_at": _now_iso(),
        "health": {},
        "smoke": {},
        "healthy": False,
        "smoke_passed": False,
        "exit_code": 1,
        "status": "degraded",
    }

    # ---- Import executor lazily so --help works without Docker deps installed
    try:
        from tools.security.sandbox_executor import SandboxExecutor
    except Exception as exc:
        result["error"] = f"cannot import SandboxExecutor: {exc}"
        result["exit_code"] = 1
        result["duration_ms"] = int((time.time() - started) * 1000)
        if write_audit:
            _log_audit(result)
        return result

    # ---- Phase 1: health check
    executor = SandboxExecutor()
    try:
        health = executor.health_check()
        result["health"] = health
        result["healthy"] = bool(health.get("available") and health.get("docker_running")
                                 and health.get("llm_sandbox_installed"))
    except Exception as exc:
        result["error"] = f"health_check raised: {exc}"
        result["exit_code"] = 1
        result["duration_ms"] = int((time.time() - started) * 1000)
        if write_audit:
            _log_audit(result)
        return result

    if not result["healthy"]:
        # Health failed — skip smoke, report degraded
        result["exit_code"] = 1
        result["duration_ms"] = int((time.time() - started) * 1000)
        if write_audit:
            _log_audit(result)
        return result

    # ---- Phase 2: end-to-end smoke
    try:
        sandbox_result = executor.execute(
            code=SMOKE_PAYLOAD,
            language="python",
            timeout_seconds=timeout_seconds,
            executor_type="liveness_probe",
            actor="sandbox_smoke_probe",
        )
        smoke_dict = {
            "status": getattr(sandbox_result, "status", None),
            "exit_code": getattr(sandbox_result, "exit_code", None),
            "runtime_ms": getattr(sandbox_result, "runtime_ms", None),
            "stdout": (getattr(sandbox_result, "stdout", None) or "")[:400],
            "stderr": (getattr(sandbox_result, "stderr", None) or "")[:400],
            "error": getattr(sandbox_result, "error", None),
        }
        result["smoke"] = smoke_dict
        stdout_ok = "ok" in (smoke_dict.get("stdout") or "").lower()
        # Success status strings per SandboxResult — "completed" is the
        # primary value emitted by SandboxExecutor.execute() on a clean run
        result["smoke_passed"] = bool(
            smoke_dict.get("status") in ("success", "completed")
            and smoke_dict.get("exit_code") == 0
            and stdout_ok
        )
    except Exception as exc:
        result["smoke"] = {"error": str(exc)[:400]}
        result["smoke_passed"] = False

    if result["smoke_passed"]:
        result["status"] = "healthy"
        result["exit_code"] = 0
    else:
        result["status"] = "smoke_failed"
        result["exit_code"] = 2

    result["duration_ms"] = int((time.time() - started) * 1000)
    if write_audit:
        _log_audit(result)
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true", help="Emit JSON output")
    p.add_argument("--timeout", type=int, default=30,
                   help="Smoke-test timeout (seconds, default 30)")
    p.add_argument("--no-audit", action="store_true",
                   help="Skip writing an audit_trail row (useful for tests)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    result = run_probe(timeout_seconds=args.timeout, write_audit=not args.no_audit)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"status: {result['status']}")
        print(f"  healthy: {result['healthy']}")
        print(f"  smoke_passed: {result['smoke_passed']}")
        print(f"  runtime: {result.get('health', {}).get('runtime')}")
        print(f"  duration_ms: {result.get('duration_ms')}")
        if result.get("error"):
            print(f"  error: {result['error']}")
    return int(result["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
