# CUI // SP-CTI
"""IQE GovLift Cloud Migration Tool canvas collection adapters.

Importing this module registers five collections on the module-level Executor:
  govlift.workloads   — Workload inventory (govlift_workloads); filter by name, workload_type, migration_status, risk_level, wave_id.
  govlift.waves       — Migration waves (govlift_waves); filter by name, status, sequence_num, workload_count.
  govlift.migrations  — Migration jobs (govlift_migrations); filter by workload_id, status, started_at, completed_at.
  govlift.stig        — STIG compliance checks (govlift_stig_checks); filter by check_name, severity, status, workload_id.
  govlift.audit       — Audit log (govlift_audit_log); filter by action, user_id, resource_type, timestamp.

Note: tables are created by tools/govlift/db/init_db.py. If tables are
absent the adapters return [] gracefully rather than raising an exception.
"""

from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def _govlift_conn(conn: Any) -> tuple[Any, bool]:
    if conn is not None:
        return conn, False
    from tools.db.storage import get_connection  # noqa: PLC0415
    return get_connection(), True


def _safe_fetch(conn: Any, sql: str) -> list[dict]:
    """Execute sql and return rows; return [] if the table does not exist yet."""
    try:
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


def workloads_adapter(conn: Any) -> list[dict]:
    """Return rows from govlift_workloads."""
    c, owned = _govlift_conn(conn)
    try:
        return _safe_fetch(
            c,
            "SELECT id, name, workload_type, migration_status, risk_level, wave_id, "
            "environment, ip_address, cpu_cores, memory_gb, storage_tb, "
            "os_name, os_version, notes, created_at, updated_at "
            "FROM govlift_workloads ORDER BY created_at DESC LIMIT 200",
        )
    finally:
        if owned:
            c.close()


def waves_adapter(conn: Any) -> list[dict]:
    """Return rows from govlift_waves."""
    c, owned = _govlift_conn(conn)
    try:
        return _safe_fetch(
            c,
            "SELECT id, name, status, sequence_num, workload_count, "
            "planned_start, planned_end, notes, created_at "
            "FROM govlift_waves ORDER BY sequence_num ASC",
        )
    finally:
        if owned:
            c.close()


def migrations_adapter(conn: Any) -> list[dict]:
    """Return rows from govlift_migrations."""
    c, owned = _govlift_conn(conn)
    try:
        return _safe_fetch(
            c,
            "SELECT id, workload_id, wave_id, status, started_at, completed_at, "
            "pre_check_passed, post_check_passed, created_at "
            "FROM govlift_migrations ORDER BY created_at DESC LIMIT 200",
        )
    finally:
        if owned:
            c.close()


def stig_adapter(conn: Any) -> list[dict]:
    """Return rows from govlift_stig_checks."""
    c, owned = _govlift_conn(conn)
    try:
        return _safe_fetch(
            c,
            "SELECT id, workload_id, stig_benchmark, check_id, check_name, "
            "severity, status, finding, remediation, checked_at, created_at "
            "FROM govlift_stig_checks ORDER BY severity ASC, created_at DESC LIMIT 200",
        )
    finally:
        if owned:
            c.close()


def audit_adapter(conn: Any) -> list[dict]:
    """Return rows from govlift_audit_log."""
    c, owned = _govlift_conn(conn)
    try:
        return _safe_fetch(
            c,
            "SELECT id, action, user_id, resource_type, resource_id, "
            "classification, ip_address, session_id, timestamp "
            "FROM govlift_audit_log ORDER BY timestamp DESC LIMIT 200",
        )
    finally:
        if owned:
            c.close()


register_collection("govlift.workloads", workloads_adapter)
register_collection("govlift.waves", waves_adapter)
register_collection("govlift.migrations", migrations_adapter)
register_collection("govlift.stig", stig_adapter)
register_collection("govlift.audit", audit_adapter)
