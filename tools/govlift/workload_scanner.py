# CUI // SP-CTI
"""GovLift — Workload Scanner.

Provides CRUD operations for govlift_workloads and summary statistics.
All DB access via get_connection() — never sqlite3.connect().
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

_ICDEV_ROOT = Path(__file__).resolve().parents[2]
if str(_ICDEV_ROOT) not in sys.path:
    sys.path.insert(0, str(_ICDEV_ROOT))

from tools.db.storage import get_connection, translate_sql
from tools.govlift.constants import WORKLOAD_STATUSES, WORKLOAD_TYPES, RISK_LEVELS


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _wl_id() -> str:
    return "wl-" + uuid4().hex[:10]


def _row_to_dict(row) -> dict:
    """Convert a DB row (sqlite3.Row or RealDictRow) to a plain dict."""
    if row is None:
        return {}
    try:
        return dict(row)
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_workloads(
    status: str | None = None,
    wave_id: str | None = None,
    risk_level: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Return workloads filtered by optional status, wave_id, or risk_level."""
    try:
        conn = get_connection()
        try:
            clauses = []
            params: list = []
            if status:
                clauses.append("migration_status = ?")
                params.append(status)
            if wave_id:
                clauses.append("wave_id = ?")
                params.append(wave_id)
            if risk_level:
                clauses.append("risk_level = ?")
                params.append(risk_level)

            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            sql = translate_sql(
                f"SELECT * FROM govlift_workloads {where} "
                f"ORDER BY created_at DESC LIMIT ?",
            )
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            return [_row_to_dict(r) for r in rows]
        finally:
            conn.close()
    except Exception as exc:
        print(f"workload_scanner.list_workloads error: {exc}", file=sys.stderr)
        return []


def get_workload(workload_id: str) -> dict | None:
    """Return a single workload by ID, or None if not found."""
    try:
        conn = get_connection()
        try:
            sql = translate_sql("SELECT * FROM govlift_workloads WHERE id = ?")
            row = conn.execute(sql, (workload_id,)).fetchone()
            return _row_to_dict(row) if row else None
        finally:
            conn.close()
    except Exception as exc:
        print(f"workload_scanner.get_workload error: {exc}", file=sys.stderr)
        return None


def create_workload(
    name: str,
    workload_type: str,
    os_name: str = "",
    os_version: str = "",
    environment: str = "production",
    ip_address: str = "",
    cpu_cores: int = 4,
    memory_gb: float = 8.0,
    storage_tb: float = 1.0,
    risk_level: str = "medium",
    notes: str = "",
) -> dict:
    """Insert a new workload and return the created record."""
    if workload_type not in WORKLOAD_TYPES:
        raise ValueError(f"Invalid workload_type '{workload_type}'. Valid: {WORKLOAD_TYPES}")
    if risk_level not in RISK_LEVELS:
        raise ValueError(f"Invalid risk_level '{risk_level}'. Valid: {RISK_LEVELS}")

    wl_id = _wl_id()
    now = _now()
    try:
        conn = get_connection()
        try:
            sql = translate_sql(
                "INSERT INTO govlift_workloads "
                "(id, name, workload_type, os_name, os_version, environment, "
                " ip_address, cpu_cores, memory_gb, storage_tb, risk_level, "
                " migration_status, notes, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,'discovered',?,?,?)"
            )
            conn.execute(
                sql,
                (
                    wl_id, name, workload_type, os_name, os_version,
                    environment, ip_address, cpu_cores, memory_gb, storage_tb,
                    risk_level, notes, now, now,
                ),
            )
            conn.commit()
            return get_workload(wl_id) or {}
        finally:
            conn.close()
    except Exception as exc:
        print(f"workload_scanner.create_workload error: {exc}", file=sys.stderr)
        raise


def update_workload_status(
    workload_id: str,
    migration_status: str,
    wave_id: str | None = None,
) -> dict:
    """Update migration_status (and optionally wave_id) on a workload."""
    if migration_status not in WORKLOAD_STATUSES:
        raise ValueError(f"Invalid migration_status '{migration_status}'. Valid: {WORKLOAD_STATUSES}")

    now = _now()
    try:
        conn = get_connection()
        try:
            if wave_id is not None:
                sql = translate_sql(
                    "UPDATE govlift_workloads "
                    "SET migration_status=?, wave_id=?, updated_at=? WHERE id=?"
                )
                conn.execute(sql, (migration_status, wave_id, now, workload_id))
            else:
                sql = translate_sql(
                    "UPDATE govlift_workloads "
                    "SET migration_status=?, updated_at=? WHERE id=?"
                )
                conn.execute(sql, (migration_status, now, workload_id))
            conn.commit()
            return get_workload(workload_id) or {}
        finally:
            conn.close()
    except Exception as exc:
        print(f"workload_scanner.update_workload_status error: {exc}", file=sys.stderr)
        raise


def assign_wave(workload_id: str, wave_id: str) -> dict:
    """Assign a workload to a wave and set status to wave_assigned."""
    return update_workload_status(workload_id, "wave_assigned", wave_id=wave_id)


def get_scanner_summary() -> dict:
    """Return counts grouped by migration_status and risk_level."""
    try:
        conn = get_connection()
        try:
            status_rows = conn.execute(
                translate_sql(
                    "SELECT migration_status, COUNT(*) AS cnt "
                    "FROM govlift_workloads GROUP BY migration_status"
                )
            ).fetchall()
            risk_rows = conn.execute(
                translate_sql(
                    "SELECT risk_level, COUNT(*) AS cnt "
                    "FROM govlift_workloads GROUP BY risk_level"
                )
            ).fetchall()
            total_row = conn.execute(
                translate_sql("SELECT COUNT(*) AS cnt FROM govlift_workloads")
            ).fetchone()

            by_status = {r["migration_status"]: r["cnt"] for r in status_rows}
            by_risk = {r["risk_level"]: r["cnt"] for r in risk_rows}
            total = _row_to_dict(total_row).get("cnt", 0) if total_row else 0

            return {
                "total_workloads": total,
                "discovered": by_status.get("discovered", 0),
                "assessed": by_status.get("assessed", 0),
                "wave_assigned": by_status.get("wave_assigned", 0),
                "in_migration": by_status.get("in_migration", 0),
                "migrated": by_status.get("migrated", 0),
                "failed": by_status.get("failed", 0),
                "total": total,
                "by_status": by_status,
                "by_risk": by_risk,
            }
        finally:
            conn.close()
    except Exception as exc:
        print(f"workload_scanner.get_scanner_summary error: {exc}", file=sys.stderr)
        return {"total": 0, "by_status": {}, "by_risk": {}}
