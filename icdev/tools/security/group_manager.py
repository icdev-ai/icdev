#!/usr/bin/env python3
# CUI // SP-CTI
from __future__ import annotations

"""User Group Manager for ICDEV™.

Implements DoD/IC group-based access control (G-01).
Groups are tenant-scoped collections of users that share role assignments
and canvas access grants.

Public API:
    create_group(tenant_id, name, ...) -> str  (group_id)
    add_member(group_id, user_id, added_by) -> None
    remove_member(group_id, user_id) -> None
    assign_role(group_id, role, canvas_scope, granted_by) -> None
    get_user_groups(user_id, tenant_id) -> list[dict]
    get_group_roles(group_id) -> list[dict]
    resolve_effective_roles(user_id, tenant_id) -> set[str]
    check_group_permission(user_id, tenant_id, category, method) -> bool

NIST 800-53: AC-3, AC-6, AC-16.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("security.groups")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn():
    from tools.db.storage import get_connection
    return get_connection()


# ---------------------------------------------------------------------------
# Group CRUD
# ---------------------------------------------------------------------------

def create_group(
    tenant_id: str,
    name: str,
    description: str = "",
    classification: str = "CUI",
    created_by: Optional[str] = None,
) -> str:
    """Create a new user group. Returns the new group_id."""
    group_id = str(uuid.uuid4())
    now = _now()
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO groups
              (id, tenant_id, name, description, classification, created_at, updated_at, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (group_id, tenant_id, name, description, classification, now, now, created_by),
        )
        conn.commit()
    logger.info("Created group %s (%s) for tenant %s", name, group_id, tenant_id)
    return group_id


def get_group(group_id: str) -> Optional[dict]:
    """Return a group dict or None."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM groups WHERE id = %s", (group_id,)
        ).fetchone()
    if not row:
        return None
    return dict(row) if hasattr(row, "keys") else {
        "id": row[0], "tenant_id": row[1], "name": row[2], "description": row[3],
        "classification": row[4], "created_at": row[5], "updated_at": row[6],
        "created_by": row[7], "status": row[8],
    }


def list_groups(tenant_id: str) -> list[dict]:
    """Return all active groups for a tenant."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM groups WHERE tenant_id = %s AND status = 'active'",
            (tenant_id,),
        ).fetchall()
    return [dict(r) if hasattr(r, "keys") else {"id": r[0], "name": r[2]} for r in rows]


def disable_group(group_id: str) -> None:
    """Soft-delete a group (set status='disabled')."""
    now = _now()
    with _conn() as conn:
        conn.execute(
            "UPDATE groups SET status = 'disabled', updated_at = %s WHERE id = %s",
            (now, group_id),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Membership
# ---------------------------------------------------------------------------

def add_member(group_id: str, user_id: str, added_by: Optional[str] = None) -> None:
    """Add a user to a group (idempotent)."""
    with _conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO group_members (group_id, user_id, added_at, added_by)
            VALUES (%s, %s, %s, %s)
            """,
            (group_id, user_id, _now(), added_by),
        )
        conn.commit()


def remove_member(group_id: str, user_id: str) -> None:
    """Remove a user from a group."""
    with _conn() as conn:
        conn.execute(
            "DELETE FROM group_members WHERE group_id = %s AND user_id = %s",
            (group_id, user_id),
        )
        conn.commit()


def get_group_members(group_id: str) -> list[str]:
    """Return list of user_ids in a group."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT user_id FROM group_members WHERE group_id = %s", (group_id,)
        ).fetchall()
    return [r[0] if isinstance(r, (tuple, list)) else r["user_id"] for r in rows]


def get_user_groups(user_id: str, tenant_id: str) -> list[dict]:
    """Return all active groups a user belongs to within a tenant."""
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT g.id, g.name, g.classification
            FROM groups g
            JOIN group_members gm ON g.id = gm.group_id
            WHERE gm.user_id = %s AND g.tenant_id = %s AND g.status = 'active'
            """,
            (user_id, tenant_id),
        ).fetchall()
    result = []
    for r in rows:
        if hasattr(r, "keys"):
            d = dict(r)
        else:
            d = {"id": r[0], "name": r[1], "classification": r[2], "tenant_id": tenant_id}
        d.setdefault("tenant_id", tenant_id)
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# Role Assignment
# ---------------------------------------------------------------------------

def assign_role(
    group_id: str,
    role: str,
    canvas_scope: Optional[str] = None,
    granted_by: Optional[str] = None,
) -> None:
    """Assign a platform role to a group, optionally scoped to one canvas."""
    with _conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO group_roles
              (group_id, role, canvas_scope, granted_at, granted_by)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (group_id, role, canvas_scope, _now(), granted_by),
        )
        conn.commit()


def revoke_role(group_id: str, role: str, canvas_scope: Optional[str] = None) -> None:
    """Revoke a role from a group."""
    with _conn() as conn:
        conn.execute(
            "DELETE FROM group_roles WHERE group_id = %s AND role = %s AND canvas_scope IS %s",
            (group_id, role, canvas_scope),
        )
        conn.commit()


def get_group_roles(group_id: str) -> list[dict]:
    """Return all role assignments for a group."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT role, canvas_scope, granted_at FROM group_roles WHERE group_id = %s",
            (group_id,),
        ).fetchall()
    result = []
    for r in rows:
        if hasattr(r, "keys"):
            result.append(dict(r))
        else:
            result.append({"role": r[0], "canvas_scope": r[1], "granted_at": r[2]})
    return result


# ---------------------------------------------------------------------------
# Effective Role Resolution
# ---------------------------------------------------------------------------

def resolve_effective_roles(
    user_id: str,
    tenant_id: str,
    canvas_scope: Optional[str] = None,
) -> set[str]:
    """Return the union of all platform roles a user holds (direct + via groups).

    Direct role: read from users table field ``role``.
    Group roles: traverse group_members → group_roles for active groups.
    canvas_scope: if given, include only group roles whose scope is NULL or matches this canvas.
    """
    roles: set[str] = set()

    # Direct user role
    try:
        with _conn() as conn:
            row = conn.execute(
                "SELECT role FROM users WHERE id = %s AND tenant_id = %s",
                (user_id, tenant_id),
            ).fetchone()
        if row:
            direct = row[0] if isinstance(row, (tuple, list)) else row.get("role")
            if direct:
                roles.add(direct)
    except Exception:
        pass

    # Group-derived roles — optionally filtered by canvas_scope
    try:
        with _conn() as conn:
            if canvas_scope is not None:
                sql = """
                    SELECT gr.role
                    FROM group_roles gr
                    JOIN group_members gm ON gr.group_id = gm.group_id
                    JOIN groups g ON g.id = gm.group_id
                    WHERE gm.user_id = ? AND g.tenant_id = ? AND g.status = 'active'
                      AND (gr.canvas_scope IS NULL OR gr.canvas_scope = ?)
                """
                rows = conn.execute(sql, (user_id, tenant_id, canvas_scope)).fetchall()
            else:
                sql = """
                    SELECT gr.role
                    FROM group_roles gr
                    JOIN group_members gm ON gr.group_id = gm.group_id
                    JOIN groups g ON g.id = gm.group_id
                    WHERE gm.user_id = ? AND g.tenant_id = ? AND g.status = 'active'
                      AND gr.canvas_scope IS NULL
                """
                rows = conn.execute(sql, (user_id, tenant_id)).fetchall()
        for r in rows:
            roles.add(r[0] if isinstance(r, (tuple, list)) else r["role"])
    except Exception:
        pass

    return roles


def check_group_permission(
    user_id: str,
    tenant_id: str,
    category: str,
    method: str = "GET",
) -> bool:
    """Return True if any of the user's group-derived roles permit the action."""
    try:
        from tools.saas.auth.rbac import check_permission
        for role in resolve_effective_roles(user_id, tenant_id):
            if check_permission(role, category, method):
                return True
    except Exception as exc:
        logger.debug("Group permission check failed: %s", exc)
    return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="ICDEV™ Group Manager CLI")
    parser.add_argument("--list", metavar="TENANT_ID", help="List groups for tenant")
    parser.add_argument("--members", metavar="GROUP_ID", help="List members of a group")
    parser.add_argument("--roles", metavar="GROUP_ID", help="List roles for a group")
    parser.add_argument(
        "--effective-roles",
        nargs=2,
        metavar=("USER_ID", "TENANT_ID"),
        help="Resolve effective roles for user",
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    import json as _json

    if args.list:
        result = list_groups(args.list)
        print(_json.dumps(result, indent=2) if args.json else str(result))
    elif args.members:
        result = get_group_members(args.members)
        print(_json.dumps(result, indent=2) if args.json else str(result))
    elif args.roles:
        result = get_group_roles(args.roles)
        print(_json.dumps(result, indent=2) if args.json else str(result))
    elif args.effective_roles:
        result = sorted(resolve_effective_roles(*args.effective_roles))
        print(_json.dumps(result, indent=2) if args.json else str(result))


if __name__ == "__main__":
    main()
