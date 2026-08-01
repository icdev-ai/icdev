# CUI // SP-CTI
"""Kanban workflow metrics — process-health analytics for the Inspect & Adapt pipeline.

Provides deterministic SQL-backed metrics without LLM overhead.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)


def dispatch_success_rate(days: int = 7) -> float:
    """Percentage of tasks that reached `done` on first dispatch (failure_count == 0).

    Returns 0.0-1.0 float. A healthy pipeline should be > 0.70.
    """
    from tools.db.storage import get_connection  # noqa: PLC0415

    conn = get_connection()
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        total_row = conn.execute(
            "SELECT COUNT(*) FROM kanban_tasks WHERE status = 'done' AND completed_at >= %s",
            (since,),
        ).fetchone()
        total = int(total_row[0]) if total_row else 0
        if total == 0:
            return 0.0

        first_try_row = conn.execute(
            "SELECT COUNT(*) FROM kanban_tasks WHERE status = 'done' "
            "AND completed_at >= %s AND COALESCE(failure_count, 0) = 0",
            (since,),
        ).fetchone()
        first_try = int(first_try_row[0]) if first_try_row else 0
        return round(first_try / total, 3)
    finally:
        conn.close()


def retry_rate_by_prefix(prefix: str, days: int = 7) -> Dict[str, Any]:
    """Average failure_count per task matching the prefix.

    Returns dict with count, avg_failure_count, max_failure_count.
    """
    from tools.db.storage import get_connection  # noqa: PLC0415

    conn = get_connection()
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        pattern = f"{prefix}-%"
        rows = conn.execute(
            "SELECT COALESCE(failure_count, 0) FROM kanban_tasks "
            "WHERE id LIKE %s AND updated_at >= %s",
            (pattern, since),
        ).fetchall()
        counts = [int(r[0]) for r in rows]
        if not counts:
            return {"count": 0, "avg_failure_count": 0.0, "max_failure_count": 0}
        return {
            "count": len(counts),
            "avg_failure_count": round(sum(counts) / len(counts), 2),
            "max_failure_count": max(counts),
        }
    finally:
        conn.close()


def top_lesson_categories(days: int = 30, limit: int = 10) -> List[Dict[str, Any]]:
    """Most frequent lesson patterns in the look-back window."""
    try:
        from tools.db.storage import get_connection  # noqa: PLC0415
    except ImportError:
        return []

    conn = get_connection()
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = conn.execute(
            "SELECT content FROM memory_entries "
            "WHERE type = %s AND created_at >= %s",
            ("lesson_learned", since),
        ).fetchall()

        import json  # noqa: PLC0415
        from collections import Counter  # noqa: PLC0415

        patterns = Counter()
        for r in rows:
            try:
                payload = json.loads(r[0])
                patterns[payload.get("pattern", "unknown")] += 1
            except Exception:
                continue

        return [
            {"pattern": p, "count": c}
            for p, c in patterns.most_common(limit)
        ]
    finally:
        conn.close()


def recurring_patterns(days: int = 30, min_score: float = 0.3) -> List[Dict[str, Any]]:
    """Patterns with recurrence_score >= min_score in recent lessons."""
    try:
        from tools.db.storage import get_connection  # noqa: PLC0415
    except ImportError:
        return []

    conn = get_connection()
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = conn.execute(
            "SELECT content FROM memory_entries "
            "WHERE type = %s AND created_at >= %s",
            ("lesson_learned", since),
        ).fetchall()

        import json  # noqa: PLC0415

        recurring: List[Dict[str, Any]] = []
        for r in rows:
            try:
                payload = json.loads(r[0])
                score = payload.get("recurrence_score", 0.0)
                if score >= min_score:
                    recurring.append({
                        "task_id": payload.get("task_id", ""),
                        "pattern": payload.get("pattern", "unknown"),
                        "category": payload.get("category", "Unknown"),
                        "recurrence_score": score,
                        "recommendation": payload.get("recommendation", ""),
                    })
            except Exception:
                continue

        # Deduplicate by task_id (keep highest score)
        seen: Dict[str, Dict[str, Any]] = {}
        for item in recurring:
            tid = item["task_id"]
            if tid not in seen or item["recurrence_score"] > seen[tid]["recurrence_score"]:
                seen[tid] = item
        return list(seen.values())
    finally:
        conn.close()


def _days_ago_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# ---------------------------------------------------------------------------
# SLA / due-date + cycle-time metrics (crx-kan-01)
# ---------------------------------------------------------------------------
# Computed in Python from existing rows (PG/SQLite portable — no dialect date
# SQL): SLA classification uses the new nullable kanban_tasks.due_date /
# sla_hours columns; cycle time uses the append-only kanban_status_transitions
# timeline already written by state_machine.py / cli.py.

# Statuses that are "closed" — no longer accruing SLA risk / mark completion.
SLA_TERMINAL_STATUSES = ("done", "cancelled", "canceled")
# Fraction of the window remaining below which an open task is "at risk".
SLA_AT_RISK_FRACTION = 0.2


def _sla_parse_ts(value: Any) -> Optional[datetime]:
    """Parse an ISO-ish timestamp into an aware UTC datetime, or None."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip().replace("Z", "+00:00")
    if " " in s and "T" not in s:
        s = s.replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(s[: len(fmt) + 2], fmt)
                break
            except ValueError:
                continue
        else:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _sla_percentile(sorted_vals: List[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return float(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo))


def _sla_deadline(row: Dict[str, Any]) -> Optional[datetime]:
    """Resolve a task's deadline: explicit due_date, else created_at + sla_hours."""
    due = _sla_parse_ts(row.get("due_date"))
    if due:
        return due
    sla = row.get("sla_hours")
    if sla in (None, "", 0):
        return None
    created = _sla_parse_ts(row.get("created_at"))
    if not created:
        return None
    try:
        return created + timedelta(hours=float(sla))
    except (TypeError, ValueError):
        return None


def sla_snapshot(conn=None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Classify open tasks against their deadline into overdue / at-risk.

    Overdue: effective deadline is in the past. At-risk: less than
    SLA_AT_RISK_FRACTION of the created_at→deadline window remains. Terminal
    tasks and tasks with no deadline are ignored so the board can badge only
    what is actionable.
    """
    own = conn is None
    if own:
        from tools.db.storage import get_connection  # noqa: PLC0415
        conn = get_connection()
    now = now or datetime.now(timezone.utc)
    try:
        placeholders = ",".join(["%s"] * len(SLA_TERMINAL_STATUSES))
        rows = conn.execute(
            f"""
            SELECT id, title, status, priority, created_at, due_date, sla_hours
            FROM kanban_tasks
            WHERE status NOT IN ({placeholders})
            """,  # nosec B608 - placeholders is a constant count of %s
            list(SLA_TERMINAL_STATUSES),
        ).fetchall()
    finally:
        if own:
            conn.close()

    overdue: List[Dict[str, Any]] = []
    at_risk: List[Dict[str, Any]] = []
    tracked = 0
    for r in rows:
        row = r if isinstance(r, dict) else {
            "id": r[0], "title": r[1], "status": r[2], "priority": r[3],
            "created_at": r[4], "due_date": r[5], "sla_hours": r[6],
        }
        deadline = _sla_deadline(row)
        if deadline is None:
            continue
        tracked += 1
        created = _sla_parse_ts(row.get("created_at")) or deadline
        entry = {
            "id": row.get("id"),
            "title": row.get("title"),
            "status": row.get("status"),
            "priority": row.get("priority"),
            "due_date": deadline.isoformat(),
        }
        if now >= deadline:
            entry["overdue_hours"] = round((now - deadline).total_seconds() / 3600.0, 1)
            overdue.append(entry)
            continue
        window = (deadline - created).total_seconds()
        remaining = (deadline - now).total_seconds()
        if window > 0 and remaining <= window * SLA_AT_RISK_FRACTION:
            entry["remaining_hours"] = round(remaining / 3600.0, 1)
            at_risk.append(entry)

    overdue.sort(key=lambda e: e.get("overdue_hours", 0), reverse=True)
    at_risk.sort(key=lambda e: e.get("remaining_hours", 0))
    return {
        "tracked": tracked,
        "overdue_count": len(overdue),
        "at_risk_count": len(at_risk),
        "overdue": overdue,
        "at_risk": at_risk,
    }


def _iso_week(dt: datetime) -> str:
    iso = dt.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def cycle_time_metrics(conn=None, weeks: int = 4) -> Dict[str, Any]:
    """Cycle time + throughput for tasks completed in the last `weeks`.

    Cycle time per task = last transition INTO a terminal status minus the first
    transition OUT of 'backlog' (fallback: the task's created_at). Reads the
    append-only kanban_status_transitions timeline. Throughput = completed count
    per ISO week.
    """
    own = conn is None
    if own:
        from tools.db.storage import get_connection  # noqa: PLC0415
        conn = get_connection()
    cutoff = (datetime.now(timezone.utc) - timedelta(weeks=weeks)).isoformat()
    try:
        rows = conn.execute(
            "SELECT task_id, from_status, to_status, recorded_at "
            "FROM kanban_status_transitions ORDER BY task_id, recorded_at"
        ).fetchall()
        created_rows = conn.execute("SELECT id, created_at FROM kanban_tasks").fetchall()
    finally:
        if own:
            conn.close()

    created_map: Dict[str, Optional[datetime]] = {}
    for cr in created_rows:
        cid = cr["id"] if isinstance(cr, dict) else cr[0]
        cval = cr["created_at"] if isinstance(cr, dict) else cr[1]
        created_map[cid] = _sla_parse_ts(cval)

    per_task: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        d = r if isinstance(r, dict) else {
            "task_id": r[0], "from_status": r[1], "to_status": r[2], "recorded_at": r[3],
        }
        per_task.setdefault(d["task_id"], []).append(d)

    cycle_hours: List[float] = []
    throughput: Dict[str, int] = {}
    completed = 0
    for task_id, trans in per_task.items():
        done_ts = None
        for t in trans:
            if t["to_status"] in SLA_TERMINAL_STATUSES:
                done_ts = _sla_parse_ts(t["recorded_at"])  # last terminal wins
        if not done_ts or done_ts.isoformat() < cutoff:
            continue
        start_ts = None
        for t in trans:
            if t["from_status"] == "backlog" or t["to_status"] != "backlog":
                start_ts = _sla_parse_ts(t["recorded_at"])
                break
        if not start_ts:
            start_ts = created_map.get(task_id)
        if not start_ts or done_ts < start_ts:
            continue
        completed += 1
        cycle_hours.append((done_ts - start_ts).total_seconds() / 3600.0)
        wk = _iso_week(done_ts)
        throughput[wk] = throughput.get(wk, 0) + 1

    cycle_hours.sort()
    return {
        "window_weeks": weeks,
        "completed": completed,
        "p50_cycle_hours": round(_sla_percentile(cycle_hours, 50), 2),
        "p90_cycle_hours": round(_sla_percentile(cycle_hours, 90), 2),
        "throughput_by_week": dict(sorted(throughput.items())),
    }


def board_metrics(conn=None, weeks: int = 4) -> Dict[str, Any]:
    """Combined SLA + cycle-time snapshot for the board summary API."""
    own = conn is None
    if own:
        from tools.db.storage import get_connection  # noqa: PLC0415
        conn = get_connection()
    try:
        return {
            "sla": sla_snapshot(conn=conn),
            "cycle_time": cycle_time_metrics(conn=conn, weeks=weeks),
        }
    finally:
        if own:
            conn.close()


def _main(argv: Optional[List[str]] = None) -> int:
    import argparse  # noqa: PLC0415
    import json  # noqa: PLC0415

    parser = argparse.ArgumentParser(description="Kanban SLA + cycle-time metrics")
    parser.add_argument("--weeks", type=int, default=4)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    print(json.dumps(board_metrics(weeks=args.weeks), indent=2, default=str))
    return 0


if __name__ == "__main__":
    import sys  # noqa: PLC0415

    sys.exit(_main(sys.argv[1:]))
