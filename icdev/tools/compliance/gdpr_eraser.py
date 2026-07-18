# CUI // SP-CTI
"""GDPR Right-to-Erasure implementation (ECR-DRES-03).

erase_tenant_data() enumerates all tables that have a tenant_id column,
NULLs PII columns (email, name, ip_address, etc.) in user-facing tables,
and writes an append-only erasure_audit record.

Append-only / audit / evidence tables are always skipped (NIST AU).
"""
from __future__ import annotations

import sqlite3 as _sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

# PII column names that are nulled on erasure.
_PII_COLUMNS: frozenset[str] = frozenset({
    "email",
    "name",
    "ip_address",
    "first_name",
    "last_name",
    "full_name",
    "phone",
    "address",
    "display_name",
})

# Tables that must never be modified (append-only NIST AU + evidence).
# Keep in sync with APPEND_ONLY_TABLES in .claude/hooks/pre_tool_use.py.
_SKIP_TABLES: frozenset[str] = frozenset({
    "erasure_audit",
    "component_audit_log",
    "evidence_items",
    "sso_sessions",
    "canvas_access_grants",
    "user_mfa",
    "mfa_attempts",
    "abac_audit",
    "session_risk_log",
    "rls_audit",
    "column_mask_audit",
    "field_filter_audit",
    "abac_decisions",
    "mac_violations",
    "security_context_log",
    "tenant_zone_assignments",
    "harness_eval",
    "kanban_executions",
    "kanban_task_deps",
    "canvas_ai_decisions",
    "cross_agency_transfers",
    "il5_ingestion_log",
    "canvas_instances",
    "noc_audit",
    "pmc_audit",
    "ccc_audit",
    "dsoc_audit",
    "osint_privacy_audit",
    "aac_scans",
    "aac_audit_log",
    "canvas_events",
    "genesis_reflex_log",
    "ace_audit_log",
    "integrity_findings",
    "integrity_verdicts",
    "integrity_authorizations",
    "foundry_runs",
    "foundry_signals",
    "foundry_specs",
    "foundry_tasks_emitted",
    "foundry_outcomes",
    "cwk_sessions",
    "centralized_logs",
    "mcip_dti_scores",
})


def _is_sqlite(conn: Any) -> bool:
    """Return True if the connection is SQLite (raw or via StorageConnection)."""
    if isinstance(conn, _sqlite3.Connection):
        return True
    return getattr(conn, "_backend", "sqlite") == "sqlite"


def _get_tables_with_tenant_id(conn: Any) -> list[str]:
    """Return names of all tables that have a tenant_id column."""
    if _is_sqlite(conn):
        # pg-portability: sqlite-only path — the PostgreSQL branch below uses
        # information_schema; sqlite_master / PRAGMA table_info run only on SQLite.
        all_tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        result = []
        for tbl in all_tables:
            try:
                cols = {
                    row[1]
                    for row in conn.execute(f"PRAGMA table_info({tbl})").fetchall()
                }
                if "tenant_id" in cols:
                    result.append(tbl)
            except Exception:
                pass
        return result
    # PostgreSQL via StorageConnection — information_schema is authoritative.
    rows = conn.execute(
        "SELECT DISTINCT table_name FROM information_schema.columns "
        "WHERE column_name = 'tenant_id' AND table_schema = 'public'"
    ).fetchall()
    return [row[0] for row in rows]


def _get_pii_cols(conn: Any, table: str) -> list[str]:
    """Return PII column names present in *table*."""
    if _is_sqlite(conn):
        # pg-portability: sqlite-only path — PG branch below uses information_schema.
        cols = {
            row[1]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
    else:
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table,),
        ).fetchall()
        cols = {row[0] for row in rows}
    return [c for c in cols if c in _PII_COLUMNS]


def erase_tenant_data(
    tenant_id: str,
    requested_by: str,
    scope: str = "pii",
    *,
    conn: Any = None,
) -> dict:
    """Erase PII data for a tenant and write an immutable audit record.

    Args:
        tenant_id:    Tenant identifier whose PII to erase.
        requested_by: Identity (email/user-id) who authorised the erasure.
        scope:        Erasure scope; currently only 'pii' is supported.
        conn:         Optional pre-opened connection (used in tests). When
                      None a fresh connection is obtained via get_connection().

    Returns:
        dict with erasure_id, tenant_id, scope, tables_affected, completed_at.
    """
    _close_conn = conn is None
    if _close_conn:
        from tools.db.storage import get_connection
        conn = get_connection()

    try:
        tables = _get_tables_with_tenant_id(conn)
        tables_affected: list[str] = []

        for table in tables:
            if table in _SKIP_TABLES:
                continue

            pii_cols = _get_pii_cols(conn, table)
            if not pii_cols:
                continue

            set_sql = ", ".join(f"{col} = NULL" for col in pii_cols)
            try:
                cur = conn.execute(
                    f"UPDATE {table} SET {set_sql} WHERE tenant_id = %s",
                    (tenant_id,),
                )
                if getattr(cur, "rowcount", 0) > 0:
                    tables_affected.append(table)
            except Exception:
                # Table may not have data or may fail — continue
                pass

        erasure_id = str(uuid.uuid4())
        completed_at = datetime.now(timezone.utc).isoformat()

        conn.execute(
            "INSERT INTO erasure_audit "
            "(id, tenant_id, requested_by, scope, tables_affected, completed_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (
                erasure_id,
                tenant_id,
                requested_by,
                scope,
                ",".join(tables_affected),
                completed_at,
            ),
        )
        conn.commit()

        return {
            "erasure_id": erasure_id,
            "tenant_id": tenant_id,
            "scope": scope,
            "tables_affected": tables_affected,
            "completed_at": completed_at,
        }
    finally:
        if _close_conn:
            conn.close()
