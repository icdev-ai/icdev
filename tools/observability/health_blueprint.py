# CUI // SP-CTI
"""Health, readiness, and liveness probes — Flask Blueprint (ECR-OBS-03).

GET /health        — structured JSON: {status, checks, version, uptime_seconds,
                     checkout_id}
GET /health/ready  — Kubernetes readiness (503 when DB unreachable)
GET /health/live   — Kubernetes liveness (always 200)
"""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

from flask import Blueprint, jsonify

health_bp = Blueprint("health", __name__)

_START_TIME = time.monotonic()

try:
    import importlib.metadata as _imeta
    _VERSION = _imeta.version("icdev")
except Exception:
    _VERSION = "dev"


# ---------------------------------------------------------------------------
# Checkout identity (rem-e2e-01)
# ---------------------------------------------------------------------------

def repo_root_for(path: str | Path) -> Path | None:
    """Return the repo root containing *path*, or None if there is no git marker.

    Walks up from *path* to the first directory holding a ``.git`` entry. A git
    worktree carries a ``.git`` *file* and the main checkout a ``.git``
    *directory*, so both resolve, and stopping at the FIRST marker is what makes
    a worktree nested inside another checkout resolve to itself rather than to
    its parent. Walking to the marker (rather than counting ``parents``) also
    collapses the canonical ``tools/`` package and the ``icdev/tools/`` mirror
    onto the same root — the two import paths are the same working copy and must
    not report different identities.
    """
    try:
        here = Path(path).resolve()
    except Exception:
        return None
    for candidate in here.parents:
        if (candidate / ".git").exists():
            return candidate
    return None


def checkout_id_for(path: str | Path) -> str:
    """Stable identifier for the working copy containing *path*.

    An E2E harness needs to know *which* checkout answered, not merely that a
    port answered: a suite launched from a git worktree will otherwise connect
    to a dashboard started from the main checkout and measure the wrong tree —
    a green run that means nothing and a red run that is pure noise.

    The value is a hash of the resolved repo root, not the root itself, so an
    unauthenticated endpoint gains an exact-match identity signal without
    disclosing the server's filesystem layout. Empty string when the tree has
    no git marker (installed package, container image), which callers must
    treat as "cannot verify" rather than as a match.

    It takes a *path* rather than reading ``__file__`` so a client can identify
    the checkout it is itself running from, instead of the one this module
    happened to be imported from. Those differ exactly when it matters: under
    pytest, ``import tools`` can resolve to the shared checkout (an earlier
    ``sys.modules`` entry, or a stray ``.pth``), and an identity probe that
    trusted it would compute the SHARED tree's id, match the running dashboard,
    and cheerfully run the suite against the wrong tree — the very defect this
    function exists to prevent.
    """
    root = repo_root_for(path)
    if root is None:
        return ""
    return hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]


def _checkout_id() -> str:
    """``checkout_id`` for the tree this module was imported from."""
    return checkout_id_for(__file__)


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


def _llm_ping_enabled() -> bool:
    """Whether the optional deeper LLM reachability probe is enabled.

    Off by default so the probe stays air-gap safe (no network I/O unless the
    operator explicitly opts in via ICDEV_HEALTH_LLM_PING).
    """
    return os.environ.get("ICDEV_HEALTH_LLM_PING", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _check_llm() -> str:
    """Truthful LLM availability check (key: ``llm_import``).

    Default behaviour verifies only that the LLMRouter module *imports*. A
    successful import proves the code is installed — it does NOT prove any
    provider is reachable — hence the key name ``llm_import``.

    Optional deeper probe (env ``ICDEV_HEALTH_LLM_PING=true``, default off):
    asks the router whether at least one configured provider+model is actually
    reachable via ``LLMRouter.has_any_llm()`` — a reachability check that does
    NOT invoke a completion. Any failure degrades gracefully (returns
    ``"degraded"``) rather than raising, so the probe never breaks the
    endpoint. When disabled, no network I/O is attempted.
    """
    try:
        from tools.llm.router import LLMRouter  # noqa: F401
    except Exception:
        return "degraded"

    if not _llm_ping_enabled():
        return "ok"

    # Deep probe: provider reachability (no completion invoked).
    try:
        return "ok" if LLMRouter().has_any_llm() else "degraded"
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
        "llm_import": _check_llm(),
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
        "checkout_id": _checkout_id(),
    }), http_code


@health_bp.route("/health/ready", methods=["GET"])
def health_ready():
    if _check_db() == "fail":
        return jsonify({"ready": False, "reason": "database unreachable"}), 503
    return jsonify({"ready": True}), 200


@health_bp.route("/health/live", methods=["GET"])
def health_live():
    return jsonify({"alive": True}), 200
