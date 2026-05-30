#!/usr/bin/env python3
# CUI // SP-CTI
from __future__ import annotations

"""Canvas / Child-App Explicit Access Control for ICDEV™.

Implements DoD/IC per-canvas access control (G-02).
Every canvas and child app requires an explicit grant in ``canvas_access_grants``.
Default posture is DENY-ALL — no ambient access by authenticated users.

Grant resolution order (first permit wins):
  1. Direct user grant (principal_type='user', principal_id=user_id)
  2. Group grant      (principal_type='group', principal_id=group_id ∈ user's groups)
  3. Role grant       (principal_type='role',  principal_id=user's platform role)

Public API:
    @require_canvas_access(canvas_name, min_level='read')   # Flask route decorator
    grant_access(tenant_id, principal_type, principal_id, canvas_name, level, granted_by) -> str
    revoke_access(grant_id) -> None
    check_access(user_id, tenant_id, canvas_name, required_level='read') -> bool
    list_grants(tenant_id, canvas_name) -> list[dict]
    seed_tenant_defaults(tenant_id, granted_by) -> None

NIST 800-53: AC-3, AC-6, ZTA Pillar 5.
"""

import functools
import uuid
from datetime import datetime, timezone
from typing import Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("security.canvas_access")

# Access level ordering (higher index = more permissive)
_LEVEL_ORDER = {"read": 0, "write": 1, "admin": 2}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn():
    from tools.db.storage import get_connection
    return get_connection()


# ---------------------------------------------------------------------------
# Grant management
# ---------------------------------------------------------------------------

def grant_access(
    tenant_id: str,
    principal_type: str,
    principal_id: str,
    canvas_name: str,
    access_level: str = "read",
    granted_by: str = "system",
    expires_at: Optional[str] = None,
) -> str:
    """Create or update an access grant. Returns the grant id."""
    if principal_type not in ("user", "group", "role"):
        raise ValueError(f"Invalid principal_type: {principal_type}")
    if access_level not in _LEVEL_ORDER:
        raise ValueError(f"Invalid access_level: {access_level}")

    grant_id = str(uuid.uuid4())
    now = _now()
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO canvas_access_grants
              (id, tenant_id, principal_type, principal_id, canvas_name,
               access_level, granted_by, granted_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (tenant_id, principal_type, principal_id, canvas_name)
            DO UPDATE SET access_level = excluded.access_level,
                          granted_by   = excluded.granted_by,
                          granted_at   = excluded.granted_at,
                          expires_at   = excluded.expires_at,
                          revoked_at   = NULL
            """,
            (grant_id, tenant_id, principal_type, principal_id, canvas_name,
             access_level, granted_by, now, expires_at),
        )
        conn.commit()

    _audit("grant", tenant_id, principal_type, principal_id, canvas_name, access_level, granted_by)
    logger.info("Canvas grant: %s %s → %s:%s", principal_type, principal_id, canvas_name, access_level)
    return grant_id


def revoke_access(
    tenant_id: str,
    principal_type: str,
    principal_id: str,
    canvas_name: str,
    revoked_by: str = "system",
) -> None:
    """Soft-revoke an access grant."""
    now = _now()
    with _conn() as conn:
        conn.execute(
            """
            UPDATE canvas_access_grants
            SET revoked_at = ?
            WHERE tenant_id = ? AND principal_type = ? AND principal_id = ?
              AND canvas_name = ? AND revoked_at IS NULL
            """,
            (now, tenant_id, principal_type, principal_id, canvas_name),
        )
        conn.commit()
    _audit("revoke", tenant_id, principal_type, principal_id, canvas_name, "", revoked_by)


def list_grants(tenant_id: str, canvas_name: Optional[str] = None) -> list[dict]:
    """Return active grants for a tenant, optionally filtered by canvas."""
    now = _now()
    if canvas_name:
        sql = (
            "SELECT * FROM canvas_access_grants "
            "WHERE tenant_id = ? AND canvas_name = ? AND revoked_at IS NULL "
            "AND (expires_at IS NULL OR expires_at > ?)"
        )
        params = (tenant_id, canvas_name, now)
    else:
        sql = (
            "SELECT * FROM canvas_access_grants "
            "WHERE tenant_id = ? AND revoked_at IS NULL "
            "AND (expires_at IS NULL OR expires_at > ?)"
        )
        params = (tenant_id, now)
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) if hasattr(r, "keys") else _row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Access check
# ---------------------------------------------------------------------------

def check_access(
    user_id: str,
    tenant_id: str,
    canvas_name: str,
    required_level: str = "read",
    user_role: Optional[str] = None,
) -> bool:
    """Return True if the user has at least *required_level* access to *canvas_name*.

    Resolution order:
    1. Direct user grant
    2. Group grants (user's group membership in tenant)
    3. Role grant (user's platform role — resolved from users table or user_role param)
    """
    if required_level not in _LEVEL_ORDER:
        required_level = "read"
    required_order = _LEVEL_ORDER[required_level]
    now = _now()

    try:
        with _conn() as conn:
            # 1 — Direct user grant
            row = conn.execute(
                """
                SELECT access_level FROM canvas_access_grants
                WHERE tenant_id = ? AND canvas_name = ?
                  AND principal_type = 'user' AND principal_id = ?
                  AND revoked_at IS NULL
                  AND (expires_at IS NULL OR expires_at > ?)
                LIMIT 1
                """,
                (tenant_id, canvas_name, user_id, now),
            ).fetchone()
            if row:
                level = row[0] if isinstance(row, (tuple, list)) else row["access_level"]
                if _LEVEL_ORDER.get(level, -1) >= required_order:
                    return _pdp_second_opinion(user_id, tenant_id, canvas_name, required_level)

            # 2 — Group grants
            group_rows = conn.execute(
                """
                SELECT cag.access_level
                FROM canvas_access_grants cag
                JOIN group_members gm ON cag.principal_id = gm.group_id
                JOIN groups g ON g.id = gm.group_id
                WHERE cag.tenant_id = ? AND cag.canvas_name = ?
                  AND cag.principal_type = 'group' AND gm.user_id = ?
                  AND g.tenant_id = ? AND g.status = 'active'
                  AND cag.revoked_at IS NULL
                  AND (cag.expires_at IS NULL OR cag.expires_at > ?)
                """,
                (tenant_id, canvas_name, user_id, tenant_id, now),
            ).fetchall()
            for r in group_rows:
                level = r[0] if isinstance(r, (tuple, list)) else r["access_level"]
                if _LEVEL_ORDER.get(level, -1) >= required_order:
                    return _pdp_second_opinion(user_id, tenant_id, canvas_name, required_level)

            # 3 — Role grant (resolve user's direct platform role)
            # Use caller-supplied role first; fall back to users table lookup.
            resolved_role = user_role
            if not resolved_role:
                user_row = conn.execute(
                    "SELECT role FROM users WHERE id = ? AND tenant_id = ?",
                    (user_id, tenant_id),
                ).fetchone()
                if user_row:
                    resolved_role = user_row[0] if isinstance(user_row, (tuple, list)) else user_row.get("role", "")
            if resolved_role:
                role_row = conn.execute(
                    """
                    SELECT access_level FROM canvas_access_grants
                    WHERE tenant_id = ? AND canvas_name = ?
                      AND principal_type = 'role' AND principal_id = ?
                      AND revoked_at IS NULL
                      AND (expires_at IS NULL OR expires_at > ?)
                    LIMIT 1
                    """,
                    (tenant_id, canvas_name, resolved_role, now),
                ).fetchone()
                if role_row:
                    level = role_row[0] if isinstance(role_row, (tuple, list)) else role_row["access_level"]
                    if _LEVEL_ORDER.get(level, -1) >= required_order:
                        return _pdp_second_opinion(user_id, tenant_id, canvas_name, required_level)

    except Exception as exc:
        logger.warning("Canvas access check error for %s/%s: %s", canvas_name, user_id, exc)
        return False

    return False


def _pdp_second_opinion(user_id: str, tenant_id: str, canvas_name: str, required_level: str) -> bool:
    """G-12: Optional PDP second-opinion gate (gated by ICDEV_PDP_ENABLED env var).

    Returns True to permit, False to deny. Defaults to True when PDP is disabled or errors out.
    """
    import os
    if os.environ.get("ICDEV_PDP_ENABLED", "false").lower() != "true":
        return True
    try:
        from tools.security.pdp_client import evaluate_access
        user_ctx = {"user_id": user_id, "tenant_id": tenant_id}
        decision = evaluate_access(user_ctx, f"canvas:{canvas_name}", required_level)
        if not decision.permit:
            logger.warning(
                "PDP denied canvas access: user=%s canvas=%s level=%s reason=%s",
                user_id, canvas_name, required_level, decision.reason,
            )
            return False
    except Exception as exc:
        logger.debug("PDP second-opinion error: %s — defaulting to permit", exc)
    return True


# ---------------------------------------------------------------------------
# Tenant default seeding
# ---------------------------------------------------------------------------

def seed_tenant_defaults(tenant_id: str, granted_by: str = "system") -> None:
    """Auto-grant default_roles defined in canvas_registry.yaml for a new tenant.

    Called from tenant_manager.py after tenant creation.
    """
    try:
        from pathlib import Path
        import yaml  # type: ignore[import-untyped]
        registry_path = Path(__file__).resolve().parent.parent.parent / "args" / "canvas_registry.yaml"
        with open(registry_path, encoding="utf-8") as fh:
            registry = yaml.safe_load(fh) or {}
    except Exception as exc:
        logger.warning("Cannot load canvas_registry.yaml: %s", exc)
        return

    for canvas in registry.get("canvases", []):
        canvas_name = canvas.get("name")
        for role in canvas.get("default_roles", []):
            try:
                grant_access(
                    tenant_id=tenant_id,
                    principal_type="role",
                    principal_id=role,
                    canvas_name=canvas_name,
                    access_level="read",
                    granted_by=granted_by,
                )
            except Exception as exc:
                logger.debug("Seed grant failed for %s/%s: %s", canvas_name, role, exc)

    logger.info("Seeded canvas default grants for tenant %s", tenant_id)


# ---------------------------------------------------------------------------
# Flask decorator
# ---------------------------------------------------------------------------

def require_canvas_access(canvas_name: str, min_level: str = "read"):
    """Flask route decorator: deny request with 403 if user lacks canvas access.

    Usage::

        @bp.route("/proposals")
        @require_canvas_access("proposals", min_level="read")
        def proposals_page():
            ...

    The decorator reads ``g.current_user`` and ``g.tenant_id`` set by auth middleware.
    Returns JSON 403 for API requests; HTML 403 for browser requests.
    """
    def decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            try:
                from flask import g, request  # noqa: F401
            except ImportError:
                return f(*args, **kwargs)

            user = getattr(g, "current_user", None) or {}
            user_id = str(user.get("id", "") or user.get("user_id", "") or "")
            tenant_id = str(getattr(g, "tenant_id", None) or user.get("tenant_id", "") or "")

            if not user_id or not tenant_id:
                logger.warning(
                    "Canvas access denied (unauthenticated): canvas=%s path=%s",
                    canvas_name, request.path,
                )
                _deny_response(canvas_name)

            permitted = check_access(user_id, tenant_id, canvas_name, required_level=min_level)
            if not permitted:
                logger.warning(
                    "Canvas access denied: user=%s tenant=%s canvas=%s level=%s path=%s",
                    user_id, tenant_id, canvas_name, min_level, request.path,
                )
                _deny_response(canvas_name)

            return f(*args, **kwargs)
        return wrapper
    return decorator


def _deny_response(canvas_name: str) -> None:
    """Abort with 403 in the appropriate format (JSON or HTML)."""
    try:
        from flask import request, jsonify, make_response, abort
        accept = request.headers.get("Accept", "")
        if "application/json" in accept or request.is_json:
            from flask import abort as _abort
            _abort(
                make_response(
                    jsonify({
                        "error": "Access denied",
                        "code": "CANVAS_ACCESS_DENIED",
                        "canvas": canvas_name,
                        "message": "You do not have permission to access this canvas. "
                                   "Contact your tenant administrator.",
                    }),
                    403,
                )
            )
        abort(403)
    except Exception:
        raise


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _audit(action: str, tenant_id: str, principal_type: str, principal_id: str,
           canvas_name: str, access_level: str, actor: str) -> None:
    """Write to audit_trail (best-effort, never raises)."""
    try:
        from tools.db.storage import get_connection
        from datetime import datetime, timezone
        import json
        now = datetime.now(timezone.utc).isoformat()
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO audit_trail (event_type, actor, details, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    f"canvas_access_{action}",
                    actor,
                    json.dumps({
                        "tenant_id": tenant_id,
                        "principal_type": principal_type,
                        "principal_id": principal_id,
                        "canvas_name": canvas_name,
                        "access_level": access_level,
                    }),
                    now,
                ),
            )
            conn.commit()
    except Exception as exc:
        logger.debug("Canvas access audit write failed: %s", exc)


def _row_to_dict(row) -> dict:
    cols = ["id", "tenant_id", "principal_type", "principal_id", "canvas_name",
            "access_level", "granted_by", "granted_at", "expires_at", "revoked_at"]
    return dict(zip(cols, row))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="ICDEV™ Canvas Access CLI")
    parser.add_argument("--check", nargs=3, metavar=("USER_ID", "TENANT_ID", "CANVAS"),
                        help="Check access for user to canvas")
    parser.add_argument("--list", nargs=2, metavar=("TENANT_ID", "CANVAS"),
                        help="List grants for tenant/canvas")
    parser.add_argument("--seed", metavar="TENANT_ID",
                        help="Seed default grants for a tenant")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.check:
        result = check_access(*args.check)
        print(json.dumps({"permitted": result}, indent=2) if args.json else str(result))
    elif args.list:
        result = list_grants(*args.list)
        print(json.dumps(result, indent=2) if args.json else str(result))
    elif args.seed:
        seed_tenant_defaults(args.seed)
        print(json.dumps({"seeded": args.seed}, indent=2) if args.json else f"Seeded {args.seed}")


if __name__ == "__main__":
    main()
