# CUI // SP-CTI
"""Health, readiness, and liveness probes — Flask Blueprint (ECR-OBS-03).

GET /health        — structured JSON: {status, checks, version, uptime_seconds}
GET /health/ready  — Kubernetes readiness (503 when DB unreachable)
GET /health/live   — Kubernetes liveness (always 200)
"""
from __future__ import annotations

import time

from flask import Blueprint, jsonify

health_bp = Blueprint("health", __name__)

_START_TIME = time.monotonic()

try:
    import importlib.metadata as _imeta
    _VERSION = _imeta.version("icdev")
except Exception:
    _VERSION = "dev"


# ---------------------------------------------------------------------------
# Individual sub-checks
# ---------------------------------------------------------------------------

def _check_db() -> str:
    """Ping the database without going through the RLS/security-context layer.

    StorageConnection auto-attaches the Flask g.security_context inside a
    request context.  That causes _inject_rls to append WHERE classification
    IN (...) to bare "SELECT 1" (which has no FROM table), producing a PG
    "column does not exist" error.  Bypass by using the raw underlying
    connection directly so no predicate injection happens.
    """
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        raw = conn._conn
        cur = raw.cursor()
        cur.execute("SELECT 1")
        conn.close()
        return "ok"
    except Exception:
        return "fail"


def _check_llm() -> str:
    try:
        from tools.llm.router import LLMRouter  # noqa: F401
        return "ok"
    except Exception:
        return "degraded"


def _check_kanban() -> str:
    """Check kanban_tasks table is reachable, bypassing RLS for the probe."""
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        raw = conn._conn
        cur = raw.cursor()
        cur.execute("SELECT 1 FROM kanban_tasks LIMIT 1")
        conn.close()
        return "ok"
    except Exception:
        return "fail"


def _run_checks() -> dict:
    return {
        "db": _check_db(),
        "llm": _check_llm(),
        "kanban": _check_kanban(),
        "redis": "skip",
    }


def _aggregate_status(checks: dict) -> str:
    if checks.get("db") == "fail":
        return "unhealthy"
    non_critical_fail = any(
        v not in ("ok", "skip")
        for k, v in checks.items()
        if k != "db"
    )
    return "degraded" if non_critical_fail else "healthy"


def _emit_gauge(status: str) -> None:
    try:
        from tools.observability.metrics import health_check_status
        if health_check_status is not None:
            val = {"healthy": 1.0, "degraded": 0.5, "unhealthy": 0.0}.get(status, 0.0)
            health_check_status.set(val)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@health_bp.route("/health", methods=["GET"])
def health():
    checks = _run_checks()
    status = _aggregate_status(checks)
    _emit_gauge(status)
    http_code = 503 if status == "unhealthy" else 200
    return jsonify({
        "status": status,
        "checks": checks,
        "version": _VERSION,
        "uptime_seconds": round(time.monotonic() - _START_TIME, 2),
    }), http_code


@health_bp.route("/health/ready", methods=["GET"])
def health_ready():
    if _check_db() == "fail":
        return jsonify({"ready": False, "reason": "database unreachable"}), 503
    return jsonify({"ready": True}), 200


@health_bp.route("/health/live", methods=["GET"])
def health_live():
    return jsonify({"alive": True}), 200
