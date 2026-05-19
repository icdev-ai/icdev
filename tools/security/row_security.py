#!/usr/bin/env python3
# CUI // SP-CTI
from __future__ import annotations
"""Row-Level Security (RLS) for ICDEV™.

Dual-backend:
- PostgreSQL: emit ``CREATE POLICY`` DDL and set session variables.
- SQLite: inject ``AND tenant_id = ?`` / ``AND classification IN (...)``
  predicates into SELECT/UPDATE/DELETE queries via regex (no ``sqlparse``
  dependency).

Integration:
    tools.db.storage.StorageConnection.set_security_context(ctx)
    tools.db.storage.StorageCursor.set_security_context(ctx)

Public API:
    inject_row_predicate(sql, tenant_id, classification) -> (sql, params)
    generate_rls_policy(table, predicate_expr, roles) -> DDL string
    apply_tenant_rls(conn, table) -> None
"""

import json
import logging
import re
from typing import Any, Optional, Set, Tuple

logger = logging.getLogger("security.rls")

# ---------------------------------------------------------------------------
# SQLite predicate injection (regex-based)
# ---------------------------------------------------------------------------

_RE_WHERE = re.compile(r"\bWHERE\b", re.IGNORECASE)
_RE_ORDER_BY = re.compile(r"\bORDER\b\s+\bBY\b", re.IGNORECASE)
_RE_GROUP_BY = re.compile(r"\bGROUP\b\s+\bBY\b", re.IGNORECASE)
_RE_LIMIT = re.compile(r"\bLIMIT\b", re.IGNORECASE)
_RE_SELECT = re.compile(r"^\s*SELECT\b", re.IGNORECASE)
_RE_UPDATE = re.compile(r"^\s*UPDATE\b", re.IGNORECASE)
_RE_DELETE = re.compile(r"^\s*DELETE\b", re.IGNORECASE)
_RE_INSERT = re.compile(r"^\s*INSERT\b", re.IGNORECASE)


def inject_row_predicate(
    sql: str,
    tenant_id: Optional[str],
    classification: Optional[str] = None,
    classifications: Optional[Set[str]] = None,
    tenant_column: str = "tenant_id",
    classification_column: str = "classification",
) -> Tuple[str, Tuple[Any, ...]]:
    """Inject tenant and classification predicates into a SQLite SQL string.

    Returns:
        (modified_sql, extra_params) where extra_params should be prepended
        to the caller's existing parameter tuple.
    """
    extra_clauses: list[str] = []
    extra_params: list[Any] = []

    # Tenant predicate
    if tenant_id is not None:
        extra_clauses.append(f"{tenant_column} = ?")
        extra_params.append(tenant_id)

    # Classification predicate — rows with NULL/empty classification are always
    # visible (unclassified data is world-readable within the tenant).
    # Only rows that have a non-empty classification must match the caller's level.
    if classifications:
        placeholders = ", ".join(["?"] * len(classifications))
        sorted_classes = sorted(classifications)
        extra_clauses.append(
            f"({classification_column} IS NULL OR {classification_column} = '' "
            f"OR {classification_column} IN ({placeholders}))"
        )
        extra_params.extend(sorted_classes)
    elif classification:
        extra_clauses.append(
            f"({classification_column} IS NULL OR {classification_column} = '' "
            f"OR {classification_column} = ?)"
        )
        extra_params.append(classification)

    if not extra_clauses:
        return sql, ()

    # Determine injection point
    predicate = " AND ".join(extra_clauses)

    # INSERT, DDL, PRAGMA: never modify — these have no WHERE clause and
    # injecting anything would corrupt the statement structure.
    if (
        _RE_INSERT.match(sql)
        or sql.strip().upper().startswith("PRAGMA")
        or sql.strip().upper().startswith("CREATE")
    ):
        return sql, ()

    # UPDATE/DELETE: predicate appended to the END of the WHERE clause so that
    # SQLite parameter binding stays correct — SET-slot params come first in
    # the param tuple, WHERE-slot params (including the injected predicate)
    # come after.  Callers (storage._inject_rls) must APPEND these extra_params
    # rather than prepend them.  PostgreSQL additionally enforces this via its
    # native policy engine.
    if _RE_UPDATE.match(sql) or _RE_DELETE.match(sql):
        where_match = _RE_WHERE.search(sql)
        if where_match:
            # Append after all existing WHERE conditions
            new_sql = sql.rstrip().rstrip(";") + " AND " + predicate
        else:
            new_sql = sql.rstrip().rstrip(";") + " WHERE " + predicate
        return new_sql, tuple(extra_params)

    # SELECT (and anything else): predicate injected at the START of the WHERE
    # clause so callers prepend extra_params before existing params.
    where_match = _RE_WHERE.search(sql)
    if where_match:
        pos = where_match.end()
        new_sql = sql[:pos] + " " + predicate + " AND" + sql[pos:]
        return new_sql, tuple(extra_params)

    # No WHERE — inject before the first SQL clause that follows the FROM/JOIN.
    # Must check GROUP BY before ORDER BY: WHERE is only valid before GROUP BY
    # in standard SQL (SELECT … FROM … WHERE … GROUP BY … ORDER BY …).
    for pattern in (_RE_GROUP_BY, _RE_ORDER_BY, _RE_LIMIT):
        m = pattern.search(sql)
        if m:
            pos = m.start()
            new_sql = sql[:pos] + " WHERE " + predicate + " " + sql[pos:]
            return new_sql, tuple(extra_params)

    # No WHERE, no ORDER/GROUP/LIMIT — append at end
    new_sql = sql.rstrip().rstrip(";") + " WHERE " + predicate
    return new_sql, tuple(extra_params)


# ---------------------------------------------------------------------------
# PostgreSQL RLS helpers
# ---------------------------------------------------------------------------

def generate_rls_policy(
    table: str,
    predicate_expr: str,
    roles: list[str],
    policy_name: Optional[str] = None,
    command: str = "ALL",
) -> str:
    """Emit a ``CREATE POLICY`` DDL statement for PostgreSQL.

    Args:
        table: table name
        predicate_expr: raw SQL predicate (e.g. "tenant_id = current_setting('app.tenant_id')")
        roles: list of role names to apply the policy to
        policy_name: optional policy name (defaults to rls_{table}_{command})
        command: ALL, SELECT, INSERT, UPDATE, DELETE
    """
    name = policy_name or f"rls_{table}_{command.lower()}"
    roles_str = ", ".join(roles)
    return (
        f"CREATE POLICY {name}\n"
        f"  ON {table}\n"
        f"  FOR {command}\n"
        f"  TO {roles_str}\n"
        f"  USING ({predicate_expr});"
    )


def apply_tenant_rls(
    conn,
    table: str,
    roles: Optional[list[str]] = None,
    tenant_setting: str = "app.tenant_id",
) -> None:
    """Auto-generate and execute a tenant-scoped RLS policy on PostgreSQL.

    Requires a PostgreSQL connection (not StorageConnection wrapper).
    """
    if roles is None:
        roles = ["tenant_admin", "developer", "compliance_officer", "auditor", "viewer"]
    predicate = f"tenant_id = current_setting('{tenant_setting}')"
    ddl = generate_rls_policy(table, predicate, roles)
    try:
        conn.execute(ddl)
        conn.commit()
        logger.info("Applied tenant RLS on %s", table)
    except Exception as exc:
        logger.warning("Could not apply tenant RLS on %s: %s", table, exc)


def set_pg_session_vars(conn, tenant_id: Optional[str], classification: Optional[str] = None) -> None:
    """Set PostgreSQL session variables for RLS predicates."""
    if tenant_id is not None:
        try:
            conn.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_id,))
        except Exception:
            conn.execute("SELECT set_config('app.tenant_id', ?, false)", (tenant_id,))
    if classification is not None:
        try:
            conn.execute("SELECT set_config('app.classification', %s, false)", (classification,))
        except Exception:
            conn.execute("SELECT set_config('app.classification', ?, false)", (classification,))


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------

def log_rls_event(
    conn,
    table: str,
    action: str,
    tenant_id: Optional[str],
    details: Optional[dict] = None,
) -> None:
    """Log an append-only RLS audit event."""
    try:
        from datetime import datetime, timezone
        conn.execute(
            """
            INSERT INTO rls_audit (table_name, action, tenant_id, details, recorded_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                table,
                action,
                tenant_id,
                json.dumps(details or {}),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    except Exception as exc:
        logger.debug("Could not log RLS event: %s", exc)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Row Security CLI")
    parser.add_argument("--test", action="store_true", help="Run self-test")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.test:
        sql1, params1 = inject_row_predicate(
            "SELECT * FROM projects WHERE status = ?", "tenant_a", "CUI"
        )
        sql2, params2 = inject_row_predicate(
            "SELECT * FROM projects", "tenant_a", classifications={"CUI", "SECRET"}
        )
        results = {
            "where_injected": sql1,
            "where_params": params1,
            "no_where_injected": sql2,
            "no_where_params": params2,
        }
        print(json.dumps(results, indent=2) if args.json else str(results))


if __name__ == "__main__":
    main()
