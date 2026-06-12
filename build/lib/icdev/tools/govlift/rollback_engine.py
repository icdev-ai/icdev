# CUI // SP-CTI
"""GovLift — Rollback Engine.

Manages govlift_rollback_events with 4-hour SLA enforcement.
When a migration fails, initiate_rollback() creates an event with a deadline
(started_at + 4h). complete_rollback() records SLA compliance.

SLA: revert workload within 4 hours of a failed migration.
All DB access via get_connection() — never sqlite3.connect().
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

_ICDEV_ROOT = Path(__file__).resolve().parents[2]
if str(_ICDEV_ROOT) not in sys.path:
    sys.path.insert(0, str(_ICDEV_ROOT))

from tools.db.storage import get_connection, translate_sql
from tools.govlift.constants import ROLLBACK_SLA_HOURS


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rb_id() -> str:
    return "rb-" + uuid4().hex[:10]


def _row_to_dict(row) -> dict:
    if row is None:
        return {}
    try:
        return dict(row)
    except Exception:
        return {}


def _deadline(from_dt: datetime | None = None) -> str:
    base = from_dt or datetime.now(timezone.utc)
    return (base + timedelta(hours=ROLLBACK_SLA_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _sla_remaining_minutes(deadline_str: str | None) -> float | None:
    dt = _parse_dt(deadline_str)
    if dt is None:
        return None
    remaining = (dt - datetime.now(timezone.utc)).total_seconds() / 60
    return round(remaining, 1)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def initiate_rollback(migration_id: str, workload_id: str, reason: str = "") -> dict:
    """Create a rollback event for a failed migration.

    Sets deadline to now + 4 hours to enforce the SLA.
    Also transitions the migration to 'rolled_back' status.
    """
    now_str = _now()
    deadline_str = _deadline()
    rb_id = _rb_id()

    try:
        conn = get_connection()
        try:
            initial_steps = json.dumps([
                {"step": 1, "action": "Snapshot pre-rollback state", "status": "pending"},
                {"step": 2, "action": "Stop workload services on target", "status": "pending"},
                {"step": 3, "action": "Restore from pre-migration snapshot", "status": "pending"},
                {"step": 4, "action": "Restart workload services on source", "status": "pending"},
                {"step": 5, "action": "Validate workload health on source", "status": "pending"},
                {"step": 6, "action": "Update DNS / routing to source", "status": "pending"},
                {"step": 7, "action": "Confirm rollback complete", "status": "pending"},
            ])
            conn.execute(
                translate_sql(
                    "INSERT INTO govlift_rollback_events "
                    "(id, migration_id, workload_id, status, initiated_at, "
                    " deadline_at, reason, steps_log, created_at) "
                    "VALUES (?,?,?,'initiated',?,?,?,?,?)"
                ),
                (rb_id, migration_id, workload_id, now_str, deadline_str, reason, initial_steps, now_str),
            )
            conn.execute(
                translate_sql(
                    "UPDATE govlift_migrations SET status='rolled_back', completed_at=?, "
                    "rollback_deadline=? WHERE id=?"
                ),
                (now_str, deadline_str, migration_id),
            )
            conn.commit()
            row = conn.execute(
                translate_sql("SELECT * FROM govlift_rollback_events WHERE id = ?"),
                (rb_id,),
            ).fetchone()
            result = _row_to_dict(row)
            result["sla_remaining_minutes"] = _sla_remaining_minutes(deadline_str)
            return result
        finally:
            conn.close()
    except Exception as exc:
        print(f"rollback_engine.initiate_rollback error: {exc}", file=sys.stderr)
        raise


def advance_rollback_step(rollback_id: str, step_num: int, step_status: str = "completed") -> dict:
    """Mark a step as completed/failed within a rollback event."""
    try:
        conn = get_connection()
        try:
            row = conn.execute(
                translate_sql("SELECT * FROM govlift_rollback_events WHERE id = ?"),
                (rollback_id,),
            ).fetchone()
            if not row:
                return {}
            rb = _row_to_dict(row)
            try:
                steps = json.loads(rb.get("steps_log") or "[]")
            except Exception:
                steps = []
            for s in steps:
                if s.get("step") == step_num:
                    s["status"] = step_status
                    break
            new_status = "in_progress" if rb.get("status") == "initiated" else rb.get("status", "in_progress")
            conn.execute(
                translate_sql(
                    "UPDATE govlift_rollback_events SET steps_log=?, status=? WHERE id=?"
                ),
                (json.dumps(steps), new_status, rollback_id),
            )
            conn.commit()
            row = conn.execute(
                translate_sql("SELECT * FROM govlift_rollback_events WHERE id = ?"),
                (rollback_id,),
            ).fetchone()
            result = _row_to_dict(row)
            result["sla_remaining_minutes"] = _sla_remaining_minutes(result.get("deadline_at"))
            return result
        finally:
            conn.close()
    except Exception as exc:
        print(f"rollback_engine.advance_rollback_step error: {exc}", file=sys.stderr)
        raise


def complete_rollback(rollback_id: str, success: bool = True) -> dict:
    """Mark a rollback as completed or failed; record SLA compliance."""
    now_str = _now()
    final_status = "completed" if success else "failed"
    try:
        conn = get_connection()
        try:
            row = conn.execute(
                translate_sql("SELECT * FROM govlift_rollback_events WHERE id = ?"),
                (rollback_id,),
            ).fetchone()
            if not row:
                return {}
            rb = _row_to_dict(row)
            deadline_dt = _parse_dt(rb.get("deadline_at"))
            now_dt = datetime.now(timezone.utc)
            sla_met = 1 if (deadline_dt is None or now_dt <= deadline_dt) else 0
            conn.execute(
                translate_sql(
                    "UPDATE govlift_rollback_events "
                    "SET status=?, completed_at=?, sla_met=? WHERE id=?"
                ),
                (final_status, now_str, sla_met, rollback_id),
            )
            conn.commit()
            row = conn.execute(
                translate_sql("SELECT * FROM govlift_rollback_events WHERE id = ?"),
                (rollback_id,),
            ).fetchone()
            result = _row_to_dict(row)
            result["sla_met"] = bool(sla_met)
            result["sla_remaining_minutes"] = _sla_remaining_minutes(result.get("deadline_at"))
            return result
        finally:
            conn.close()
    except Exception as exc:
        print(f"rollback_engine.complete_rollback error: {exc}", file=sys.stderr)
        raise


def list_rollback_events(
    migration_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Return rollback events with SLA status enrichment."""
    try:
        conn = get_connection()
        try:
            if migration_id and status:
                sql = translate_sql(
                    "SELECT * FROM govlift_rollback_events WHERE migration_id = ? AND status = ? "
                    "ORDER BY initiated_at DESC LIMIT ?"
                )
                params = [migration_id, status, limit]
            elif migration_id:
                sql = translate_sql(
                    "SELECT * FROM govlift_rollback_events WHERE migration_id = ? "
                    "ORDER BY initiated_at DESC LIMIT ?"
                )
                params = [migration_id, limit]
            elif status:
                sql = translate_sql(
                    "SELECT * FROM govlift_rollback_events WHERE status = ? "
                    "ORDER BY initiated_at DESC LIMIT ?"
                )
                params = [status, limit]
            else:
                sql = translate_sql(
                    "SELECT * FROM govlift_rollback_events "
                    "ORDER BY initiated_at DESC LIMIT ?"
                )
                params = [limit]
            rows = conn.execute(sql, params).fetchall()
            results = []
            for r in rows:
                d = _row_to_dict(r)
                d["sla_remaining_minutes"] = _sla_remaining_minutes(d.get("deadline_at"))
                d["sla_breached"] = (
                    d.get("status") not in ("completed", "cancelled")
                    and d["sla_remaining_minutes"] is not None
                    and d["sla_remaining_minutes"] < 0
                )
                results.append(d)
            return results
        finally:
            conn.close()
    except Exception as exc:
        print(f"rollback_engine.list_rollback_events error: {exc}", file=sys.stderr)
        return []


def get_rollback_summary() -> dict:
    """Return rollback stats including SLA compliance rate."""
    try:
        conn = get_connection()
        try:
            total_row = conn.execute(
                translate_sql("SELECT COUNT(*) AS cnt FROM govlift_rollback_events")
            ).fetchone()
            status_rows = conn.execute(
                translate_sql(
                    "SELECT status, COUNT(*) AS cnt FROM govlift_rollback_events GROUP BY status"
                )
            ).fetchall()
            sla_row = conn.execute(
                translate_sql(
                    "SELECT "
                    "  SUM(CASE WHEN sla_met=1 THEN 1 ELSE 0 END) AS met, "
                    "  SUM(CASE WHEN sla_met=0 THEN 1 ELSE 0 END) AS breached "
                    "FROM govlift_rollback_events WHERE completed_at IS NOT NULL"
                )
            ).fetchone()
            sla_d = _row_to_dict(sla_row)
            met = sla_d.get("met") or 0
            breached = sla_d.get("breached") or 0
            total_completed = met + breached
            sla_rate = round(met / total_completed * 100) if total_completed else None

            return {
                "total": _row_to_dict(total_row).get("cnt", 0) if total_row else 0,
                "by_status": {r["status"]: r["cnt"] for r in status_rows},
                "sla_met": met,
                "sla_breached": breached,
                "sla_compliance_pct": sla_rate,
                "sla_hours": ROLLBACK_SLA_HOURS,
            }
        finally:
            conn.close()
    except Exception as exc:
        print(f"rollback_engine.get_rollback_summary error: {exc}", file=sys.stderr)
        return {"total": 0, "by_status": {}, "sla_compliance_pct": None, "sla_hours": ROLLBACK_SLA_HOURS}
