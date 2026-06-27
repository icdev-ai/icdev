# CUI // SP-CTI
"""GovLift RBAC — Role-Based Access Control (AC-2, AC-3, AC-6).

Roles and resource permissions (principle of least privilege):

  migration_engineer — read-write: waves, executor; read-only: all else
  isso               — read-write: stig/compliance posture; read-only: all else
  component_admin    — read-write: workloads; read-only: all else
  auditor            — read-only: all resources
  ciso               — read-only: overview (executive dashboard) only
  admin              — read-write: all resources

AC-2 IdAM helpers: provision_govlift_user / deprovision_govlift_user
"""
from __future__ import annotations

import functools
from typing import FrozenSet

from flask import abort, g, request

from tools.dashboard.auth import log_auth_event

# ---------------------------------------------------------------------------
# Canonical GovLift roles
# ---------------------------------------------------------------------------

GOVLIFT_ROLES: FrozenSet[str] = frozenset({
    "migration_engineer",
    "isso",
    "component_admin",
    "auditor",
    "ciso",
    "admin",
})

# ---------------------------------------------------------------------------
# Permissions matrix  { role: { resource: set_of_allowed_http_methods } }
#
# Resources map to GovLift blueprint route groups:
#   overview     — /govlift, /api/govlift/overview
#   workloads    — /govlift/workloads, /api/govlift/workloads[/*]
#   waves        — /govlift/waves, /api/govlift/waves[/*]
#   executor     — /govlift/executor, /api/govlift/migrations[/*]
#   stig         — /govlift/stig, /api/govlift/stig[/*]
#   audit        — /govlift/audit, /api/govlift/audit[/*]
#   integrations — /api/govlift/integrations
# ---------------------------------------------------------------------------

_RO = frozenset({"GET", "HEAD"})
_RW = frozenset({"GET", "HEAD", "POST", "PATCH", "PUT", "DELETE"})

PERMISSIONS: dict[str, dict[str, FrozenSet[str]]] = {
    "migration_engineer": {
        "overview":     _RO,
        "workloads":    _RO,
        "waves":        _RW,
        "executor":     _RW,
        "stig":         _RO,
        "audit":        _RO,
        "integrations": _RO,
    },
    "isso": {
        "overview":     _RO,
        "workloads":    _RO,
        "waves":        _RO,
        "executor":     _RO,
        "stig":         _RW,
        "audit":        _RO,
        "integrations": _RO,
    },
    "component_admin": {
        "overview":     _RO,
        "workloads":    _RW,
        "waves":        _RO,
        "executor":     _RO,
        "stig":         _RO,
        "audit":        _RO,
        "integrations": _RO,
    },
    "auditor": {
        "overview":     _RO,
        "workloads":    _RO,
        "waves":        _RO,
        "executor":     _RO,
        "stig":         _RO,
        "audit":        _RO,
        "integrations": _RO,
    },
    "ciso": {
        "overview":     _RO,
        # no access to other resources — principle of least privilege
    },
    "admin": {
        "overview":     _RW,
        "workloads":    _RW,
        "waves":        _RW,
        "executor":     _RW,
        "stig":         _RW,
        "audit":        _RW,
        "integrations": _RO,
    },
}


def can_access(role: str, resource: str, method: str = "GET") -> bool:
    """Return True if *role* may perform *method* on *resource*."""
    allowed = PERMISSIONS.get(role, {}).get(resource, frozenset())
    return method.upper() in allowed


# ---------------------------------------------------------------------------
# Flask decorator
# ---------------------------------------------------------------------------

def require_govlift_permission(resource: str):
    """Decorator that enforces GovLift resource-level RBAC.

    Usage::

        @bp.route("/govlift/waves", methods=["GET"])
        @require_govlift_permission("waves")
        def govlift_waves():
            ...
    """
    def decorator(f):
        @functools.wraps(f)
        def wrapped(*args, **kwargs):
            user = getattr(g, "current_user", None)
            if not user:
                abort(401)
            role = user["role"] if isinstance(user, dict) else user.get("role", "")
            method = request.method.upper()
            if not can_access(role, resource, method):
                log_auth_event(
                    user.get("id", "unknown") if isinstance(user, dict) else user["id"],
                    "permission_denied",
                    ip_address=request.remote_addr,
                    user_agent=request.headers.get("User-Agent", "")[:256],
                    details=f"govlift:{resource}:{method} denied for role={role}",
                )
                abort(403)
            return f(*args, **kwargs)
        return wrapped
    return decorator


# ---------------------------------------------------------------------------
# AC-2 IdAM automation — account lifecycle management
# ---------------------------------------------------------------------------

def provision_govlift_user(
    email: str,
    display_name: str,
    govlift_role: str,
    provisioned_by: str = "idam_auto",
    tenant_id: str | None = None,
) -> dict:
    """Provision a new user with a GovLift role (AC-2 automated account management).

    Returns the created user dict (same shape as tools.dashboard.auth.create_user).
    Raises ValueError for unknown govlift_role.
    """
    if govlift_role not in GOVLIFT_ROLES:
        raise ValueError(f"Unknown GovLift role: {govlift_role!r}. Valid: {sorted(GOVLIFT_ROLES)}")

    from tools.dashboard.auth import create_user
    user = create_user(
        email=email,
        display_name=display_name,
        role=govlift_role,
        created_by=provisioned_by,
        tenant_id=tenant_id,
    )
    log_auth_event(
        user["id"],
        "user_created",
        details=f"idam_provision: role={govlift_role}, by={provisioned_by}",
    )
    return user


def deprovision_govlift_user(user_id: str, deprovisioned_by: str = "idam_auto") -> None:
    """Deprovision (suspend) a GovLift user and revoke all active API keys (AC-2).

    Follows NIST AC-2(j): disable accounts no longer required.
    """
    from tools.dashboard.auth import suspend_user, list_api_keys_for_user, revoke_api_key
    # Revoke all active keys before suspension
    for key in list_api_keys_for_user(user_id):
        if key.get("status") == "active":
            revoke_api_key(key["id"], revoked_by=deprovisioned_by)
    suspend_user(user_id, suspended_by=deprovisioned_by)
    log_auth_event(
        user_id,
        "user_suspended",
        details=f"idam_deprovision: by={deprovisioned_by}",
    )


def rotate_govlift_user_role(
    user_id: str,
    new_role: str,
    changed_by: str = "idam_auto",
) -> None:
    """Update a user's GovLift role (AC-2 role change).

    Revokes existing API keys so they must re-authenticate with the new role.
    """
    if new_role not in GOVLIFT_ROLES:
        raise ValueError(f"Unknown GovLift role: {new_role!r}")

    from tools.db.storage import get_connection
    from tools.dashboard.config import DB_PATH
    from tools.dashboard.auth import list_api_keys_for_user, revoke_api_key
    from datetime import datetime, timezone

    conn = get_connection(db_path=str(DB_PATH))
    try:
        conn.execute(
            "UPDATE dashboard_users SET role = %s, updated_at = %s WHERE id = %s",
            (new_role, datetime.now(timezone.utc).isoformat(), user_id),
        )
        conn.commit()
    finally:
        conn.close()

    # Revoke keys so the new role takes effect on next login
    for key in list_api_keys_for_user(user_id):
        if key.get("status") == "active":
            revoke_api_key(key["id"], revoked_by=changed_by)

    log_auth_event(
        user_id,
        "user_reactivated",
        details=f"role_change: new_role={new_role}, by={changed_by}",
    )
