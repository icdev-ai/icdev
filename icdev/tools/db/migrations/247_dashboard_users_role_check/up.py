#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 247: Extend dashboard_users.role CHECK constraint.

Two Python-side role lists drifted ahead of this CHECK constraint and were
never reconciled:
  - prop-fix-08 added bd/capture_mgr/contract_mgr/reviewer to
    tools/dashboard/auth.py::RBAC_MATRIX (govcon/proposals/cpmp page
    gating), explicitly scoped as "No route wiring yet" -- it never
    touched the DB schema.
  - Migration 139 added migration_engineer/component_admin/auditor/ciso
    for tools/govlift/rbac.py::GOVLIFT_ROLES via a SQLite-only table
    rebuild (CREATE ... AS new / INSERT OR IGNORE / DROP / RENAME) that
    was never mirrored to PostgreSQL.

Result: create_user(role=<any of those 8>) always failed with a CHECK
constraint violation on a from-scratch or PG-backed install, so no user
could ever actually be assigned one of these roles even though both
RBAC_MATRIX and GOVLIFT_ROLES, plus numerous @require_role(...)/GovLift
permission-matrix call sites, reference them extensively.

This migration is the union of both gaps (idempotent regardless of
whether migration 139 ever successfully applied against this database).

PG only: SQLite doesn't support ALTER TABLE ... DROP/ADD CONSTRAINT; fresh
SQLite databases get the corrected constraint directly from
tools/db/init_icdev_db.py's CREATE TABLE statement instead.
"""

MIGRATION_ID = "247"
MIGRATION_NAME = "dashboard_users_role_check"
DESCRIPTION = "Extend dashboard_users.role CHECK to the full VALID_DASHBOARD_ROLES set"

# Keep in sync with tools/dashboard/auth.py::VALID_DASHBOARD_ROLES.
_ROLES = (
    "admin", "pm", "developer", "isso", "co", "cor",
    "migration_engineer", "component_admin", "auditor", "ciso",
    "bd", "capture_mgr", "contract_mgr", "reviewer",
)


def _is_pg(conn) -> bool:
    return getattr(conn, "_backend", "sqlite") == "postgresql"


def _table_exists(conn, table: str) -> bool:
    if _is_pg(conn):
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name=?",
            (table,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
    return row is not None


def up(conn) -> dict:
    actions = []

    if not _table_exists(conn, "dashboard_users"):
        return {"status": "skipped", "reason": "dashboard_users absent"}

    if not _is_pg(conn):
        return {"status": "skipped", "reason": "SQLite gets the corrected constraint from CREATE TABLE directly"}

    try:
        cur = conn.execute(
            "SELECT pg_get_constraintdef(c.oid) as def "
            "FROM pg_constraint c JOIN pg_class t ON c.conrelid = t.oid "
            "WHERE t.relname = ? AND c.conname = ?",
            ("dashboard_users", "dashboard_users_role_check"),
        ).fetchone()
        current = dict(cur).get("def", "") if cur else ""
        if all(f"'{role}'" in current for role in _ROLES):
            actions.append("already_current")
        else:
            conn.execute(
                "ALTER TABLE dashboard_users DROP CONSTRAINT IF EXISTS dashboard_users_role_check"
            )
            role_list = ", ".join(f"'{r}'::text" for r in _ROLES)
            conn.execute(
                "ALTER TABLE dashboard_users ADD CONSTRAINT dashboard_users_role_check "
                f"CHECK (role = ANY (ARRAY[{role_list}]))"
            )
            actions.append("role_check_expanded")
    except Exception as exc:
        actions.append(f"role_check_skipped: {exc}")

    conn.commit()
    return {"status": "applied", "actions": actions}
