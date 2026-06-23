# CUI // SP-CTI
"""Public v1 REST API — Flask Blueprint.

All routes require a valid Bearer API key (Authorization: Bearer ick_...).
Rate limits enforced via gateway_rate_limits table:
  - 1 000 req / hr  per API key prefix
  - 10 000 req / hr per tenant

NIST 800-53: AC-3 (Access Enforcement), AC-4, AU-2 (Audit Events),
             SC-5 (Denial-of-Service Protection), SI-10 (Input Validation)
"""
from __future__ import annotations

import time
from pathlib import Path

from flask import Blueprint, current_app, g, jsonify, request

from tools.auth.api_key import require_api_key
from tools.db.storage import get_connection

v1_api = Blueprint("v1_api", __name__, url_prefix="/v1")

# ---------------------------------------------------------------------------
# Rate limiting constants
# ---------------------------------------------------------------------------

_WINDOW = 3600        # 1-hour window in seconds
_KEY_LIMIT = 1000     # per API-key prefix
_TENANT_LIMIT = 10000  # per tenant


# ---------------------------------------------------------------------------
# DB-backed rate limiter
# ---------------------------------------------------------------------------

def _check_rate_limit(bucket_key: str, limit: int) -> tuple[bool, int, int]:
    """Increment counter for *bucket_key* and return (allowed, remaining, reset_ts).

    Uses the gateway_rate_limits table so counters survive across workers.
    On any DB error the request is allowed (fail-open) to avoid outages.
    """
    now_ts = int(time.time())
    window_start = (now_ts // _WINDOW) * _WINDOW
    reset_ts = window_start + _WINDOW

    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT request_count FROM gateway_rate_limits "
                "WHERE key = ? AND window_start = ?",
                (bucket_key, window_start),
            ).fetchone()

            if row is None:
                conn.execute(
                    "INSERT INTO gateway_rate_limits (key, window_start, request_count) "
                    "VALUES (?, ?, 1)",
                    (bucket_key, window_start),
                )
                conn.commit()
                return True, limit - 1, reset_ts

            count = int(row[0])
            if count >= limit:
                return False, 0, reset_ts

            conn.execute(
                "UPDATE gateway_rate_limits SET request_count = request_count + 1 "
                "WHERE key = ? AND window_start = ?",
                (bucket_key, window_start),
            )
            conn.commit()
            return True, limit - count - 1, reset_ts

    except Exception:  # noqa: BLE001
        return True, limit, reset_ts


def _apply_rate_limits():
    """Check per-key and per-tenant limits; return (error_body, status) or (None, None)."""
    auth_header = request.headers.get("Authorization", "")
    key_prefix = auth_header[len("Bearer "):].strip()[:12]  # display prefix only
    tenant_id = getattr(g, "api_tenant_id", "unknown")

    key_ok, key_rem, key_reset = _check_rate_limit(f"key:{key_prefix}", _KEY_LIMIT)
    if not key_ok:
        return {"error": "Rate limit exceeded (per-key)", "retry_after": key_reset}, 429

    ten_ok, ten_rem, ten_reset = _check_rate_limit(f"tenant:{tenant_id}", _TENANT_LIMIT)
    if not ten_ok:
        return {"error": "Rate limit exceeded (per-tenant)", "retry_after": ten_reset}, 429

    g.rl_remaining = min(key_rem, ten_rem)
    g.rl_limit = _KEY_LIMIT
    g.rl_reset = min(key_reset, ten_reset)
    return None, None


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------

@v1_api.after_request
def _add_rate_headers(response):
    """Attach X-RateLimit-* headers from g if available."""
    if hasattr(g, "rl_limit"):
        response.headers["X-RateLimit-Limit"] = str(g.rl_limit)
        response.headers["X-RateLimit-Remaining"] = str(g.rl_remaining)
        response.headers["X-RateLimit-Reset"] = str(g.rl_reset)
    return response


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@v1_api.route("/health", methods=["GET"])
@require_api_key
def health():
    """Return API liveness status."""
    err, status = _apply_rate_limits()
    if err is not None:
        return jsonify(err), status
    return jsonify({"status": "ok"})


@v1_api.route("/components", methods=["GET"])
@require_api_key
def list_components():
    """List enabled ICDEV™ components for the authenticated tenant."""
    err, status = _apply_rate_limits()
    if err is not None:
        return jsonify(err), status

    try:
        from tools.config.component_registry import ComponentRegistry
        registry = ComponentRegistry()
        components = [
            {
                "key": c.key,
                "name": c.display_name,
                "kind": c.kind,
                "url_prefix": c.url_prefix,
            }
            for c in registry.iter_enabled()
        ]
    except Exception:  # noqa: BLE001
        components = []

    return jsonify({"ok": True, "components": components, "count": len(components)})


@v1_api.route("/iqe/query", methods=["POST"])
@require_api_key
def iqe_query():
    """Execute an IQE query file; body: {query_path: str}. Returns JSON rows."""
    err, status = _apply_rate_limits()
    if err is not None:
        return jsonify(err), status

    body = request.get_json(silent=True) or {}
    query_path: str = body.get("query_path", "")
    if not query_path:
        return jsonify({"ok": False, "error": "query_path is required"}), 400

    # Path-traversal guard: resolve and confirm the target stays inside queries_dir
    queries_dir = (Path(__file__).resolve().parents[3] / "context" / "iqe" / "queries")
    target = (queries_dir / query_path).resolve()
    try:
        target.relative_to(queries_dir.resolve())
    except ValueError:
        return jsonify({"ok": False, "error": "invalid query_path"}), 400

    if not target.exists() or target.suffix != ".iqe":
        return jsonify({"ok": False, "error": f"query not found: {query_path}"}), 404

    try:
        from tools.iqe import parse
        from tools.iqe.executor import execute_query

        ast = parse(target.read_text(encoding="utf-8"))
        with get_connection() as conn:
            rows = execute_query(ast, conn)
        return jsonify({"ok": True, "query": query_path, "rows": rows, "count": len(rows)})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400


@v1_api.route("/audit/events", methods=["GET"])
@require_api_key
def audit_events():
    """Return paginated audit log events. Params: since (ISO-8601), limit (default 50, max 500)."""
    err, status = _apply_rate_limits()
    if err is not None:
        return jsonify(err), status

    since = request.args.get("since")
    try:
        limit = min(int(request.args.get("limit", "50")), 500)
    except ValueError:
        limit = 50

    sql = "SELECT * FROM audit_trail WHERE 1=1"
    params: list = []
    if since:
        sql += " AND recorded_at >= ?"
        params.append(since)
    sql += " ORDER BY recorded_at DESC LIMIT ?"
    params.append(limit)

    try:
        with get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        events = [dict(r) for r in rows]
    except Exception:  # noqa: BLE001
        events = []

    return jsonify({"ok": True, "events": events, "count": len(events)})


@v1_api.route("/usage/summary", methods=["GET"])
@require_api_key
def usage_summary():
    """Return metering totals by event_type for the authenticated tenant. Param: since (ISO-8601)."""
    err, status = _apply_rate_limits()
    if err is not None:
        return jsonify(err), status

    tenant_id = g.api_tenant_id
    since = request.args.get("since")

    try:
        from tools.billing.metering import get_usage_summary
        summary = get_usage_summary(tenant_id, since=since)
    except Exception:  # noqa: BLE001
        summary = {}

    return jsonify({"ok": True, "tenant_id": tenant_id, "summary": summary})


@v1_api.route("/openapi.json", methods=["GET"])
@require_api_key
def openapi_spec():
    """Return the OpenAPI 3.0.3 specification for this API."""
    err, status = _apply_rate_limits()
    if err is not None:
        return jsonify(err), status

    from tools.api.openapi_builder import build_openapi_spec
    spec = build_openapi_spec(current_app._get_current_object())
    return jsonify(spec)
