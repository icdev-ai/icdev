# CUI // SP-CTI
"""GovLift — Migration Executor.

Manages govlift_migrations: create, start, complete, rollback, and summarize.
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


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mig_id() -> str:
    return "mig-" + uuid4().hex[:10]


def _row_to_dict(row) -> dict:
    if row is None:
        return {}
    try:
        return dict(row)
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_migrations(
    workload_id: str | None = None,
    wave_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Return migrations filtered by optional workload_id, wave_id, or status."""
    try:
        conn = get_connection()
        try:
            clauses = []
            params: list = []
            if workload_id:
                clauses.append("workload_id = ?")
                params.append(workload_id)
            if wave_id:
                clauses.append("wave_id = ?")
                params.append(wave_id)
            if status:
                clauses.append("status = ?")
                params.append(status)

            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            sql = translate_sql(
                f"SELECT * FROM govlift_migrations {where} "
                f"ORDER BY created_at DESC LIMIT ?"
            )
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            return [_row_to_dict(r) for r in rows]
        finally:
            conn.close()
    except Exception as exc:
        print(f"migration_executor.list_migrations error: {exc}", file=sys.stderr)
        return []


def get_migration(migration_id: str) -> dict | None:
    """Return a single migration by ID, or None if not found."""
    try:
        conn = get_connection()
        try:
            sql = translate_sql("SELECT * FROM govlift_migrations WHERE id = ?")
            row = conn.execute(sql, (migration_id,)).fetchone()
            return _row_to_dict(row) if row else None
        finally:
            conn.close()
    except Exception as exc:
        print(f"migration_executor.get_migration error: {exc}", file=sys.stderr)
        return None


def create_migration(workload_id: str, wave_id: str | None = None) -> dict:
    """Insert a new migration job in 'pending' state and return the record."""
    mig_id = _mig_id()
    now = _now()
    try:
        conn = get_connection()
        try:
            sql = translate_sql(
                "INSERT INTO govlift_migrations "
                "(id, workload_id, wave_id, status, created_at) "
                "VALUES (?,?,?,'pending',?)"
            )
            conn.execute(sql, (mig_id, workload_id, wave_id, now))
            conn.commit()
            return get_migration(mig_id) or {}
        finally:
            conn.close()
    except Exception as exc:
        print(f"migration_executor.create_migration error: {exc}", file=sys.stderr)
        raise


def start_migration(migration_id: str) -> dict:
    """Transition a migration to 'running' and record started_at."""
    now = _now()
    try:
        conn = get_connection()
        try:
            sql = translate_sql(
                "UPDATE govlift_migrations "
                "SET status='running', started_at=? WHERE id=?"
            )
            conn.execute(sql, (now, migration_id))
            conn.commit()
            return get_migration(migration_id) or {}
        finally:
            conn.close()
    except Exception as exc:
        print(f"migration_executor.start_migration error: {exc}", file=sys.stderr)
        raise


def complete_migration(
    migration_id: str,
    success: bool = True,
    log: str = "",
) -> dict:
    """Mark a migration as completed or failed and record completed_at."""
    final_status = "completed" if success else "failed"
    post_check = 1 if success else 0
    now = _now()
    try:
        conn = get_connection()
        try:
            sql = translate_sql(
                "UPDATE govlift_migrations "
                "SET status=?, completed_at=?, executor_log=?, "
                "    pre_check_passed=1, post_check_passed=? "
                "WHERE id=?"
            )
            conn.execute(sql, (final_status, now, log, post_check, migration_id))
            conn.commit()
            return get_migration(migration_id) or {}
        finally:
            conn.close()
    except Exception as exc:
        print(f"migration_executor.complete_migration error: {exc}", file=sys.stderr)
        raise


def rollback_migration(migration_id: str) -> dict:
    """Mark a migration as rolled_back and record completed_at."""
    now = _now()
    try:
        conn = get_connection()
        try:
            sql = translate_sql(
                "UPDATE govlift_migrations "
                "SET status='rolled_back', completed_at=?, post_check_passed=0 "
                "WHERE id=?"
            )
            conn.execute(sql, (now, migration_id))
            conn.commit()
            return get_migration(migration_id) or {}
        finally:
            conn.close()
    except Exception as exc:
        print(f"migration_executor.rollback_migration error: {exc}", file=sys.stderr)
        raise


def get_migration_summary() -> dict:
    """Return counts by status and average migration duration for completed runs."""
    try:
        conn = get_connection()
        try:
            total_row = conn.execute(
                translate_sql("SELECT COUNT(*) AS cnt FROM govlift_migrations")
            ).fetchone()
            status_rows = conn.execute(
                translate_sql(
                    "SELECT status, COUNT(*) AS cnt "
                    "FROM govlift_migrations GROUP BY status"
                )
            ).fetchall()

            total = _row_to_dict(total_row).get("cnt", 0) if total_row else 0
            by_status = {r["status"]: r["cnt"] for r in status_rows}

            # Average duration (seconds) for completed migrations that have both timestamps
            dur_rows = conn.execute(
                translate_sql(
                    "SELECT started_at, completed_at FROM govlift_migrations "
                    "WHERE status='completed' AND started_at IS NOT NULL "
                    "AND completed_at IS NOT NULL"
                )
            ).fetchall()

            durations: list[float] = []
            for r in dur_rows:
                d = _row_to_dict(r)
                try:
                    t_start = datetime.fromisoformat(d["started_at"].replace("Z", "+00:00"))
                    t_end = datetime.fromisoformat(d["completed_at"].replace("Z", "+00:00"))
                    durations.append((t_end - t_start).total_seconds())
                except Exception:
                    pass

            # Use numeric 0.0 (not None) on empty data so templates that render
            # these with |round don't crash ("NoneType doesn't define __round__")
            # when there are no completed migrations (e.g. a fresh schema-only DB).
            avg_duration_sec = (sum(durations) / len(durations)) if durations else 0.0
            avg_duration_h = avg_duration_sec / 3600 if avg_duration_sec else 0.0

            return {
                "total": total,
                "pending": by_status.get("pending", 0),
                "running": by_status.get("running", 0),
                "completed": by_status.get("completed", 0),
                "failed": by_status.get("failed", 0),
                "by_status": by_status,
                "avg_duration_h": avg_duration_h,
                "avg_duration_seconds": avg_duration_sec,
            }
        finally:
            conn.close()
    except Exception as exc:
        print(f"migration_executor.get_migration_summary error: {exc}", file=sys.stderr)
        return {"total": 0, "by_status": {}, "avg_duration_seconds": None}
