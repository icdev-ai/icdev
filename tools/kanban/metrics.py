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


# ---------------------------------------------------------------------------
# Board throughput stall detection (kax-stall-01)
# ---------------------------------------------------------------------------
# Nothing watched whether the board was actually COMPLETING work: 'done'
# transitions ran 63 (Aug 1) → 121 (Aug 2) → ZERO for Aug 4, 5 and 6, and the
# four-day flatline surfaced only because a human happened to ask. This
# computes the raw signal; tools/genesis/reflexes/self_monitor.py projects it
# into an operator alert on the existing alert surface.

# Statuses that mean "the board has work it is supposed to be moving". Zero
# throughput with none of these is an idle board, not a stalled one.
STALL_ACTIVE_STATUSES = ("scheduled", "in_progress")

# Documented defaults only — the live values come from genesis_config.yaml
# (self_monitor.board_throughput) / env, never from these constants.
DEFAULT_STALL_WINDOW_HOURS = 24.0
DEFAULT_STALL_MIN_ACTIVE_TASKS = 1

# The SQL pre-filter is deliberately loosened by this much before the exact
# comparison is redone in Python, so a stray timestamp format can never drop a
# real completion out of the window.
_STALL_PREFILTER_SLACK_HOURS = 48.0


# --- PR watcher liveness (kax-obs-02) ---------------------------------------
# "Nothing reached done in N hours" has two completely different causes that
# looked identical: the watcher is not polling at all, or the watcher is polling
# fine and the board has nothing mergeable. The watcher appends one row per
# COMPLETED poll to the existing `heartbeat_checks` table
# (tools/ci/pr_watcher.py::_record_heartbeat); this reads the newest one. It is
# deliberately not a process-exists check — the launcher already restarts a dead
# watcher, so the gap is a LIVE-but-not-progressing one.
WATCHER_HEARTBEAT_CHECK_TYPE = "pr_watcher_poll"

# The watcher polls every `poll_interval_seconds` (default 30s). 15 minutes is
# ~30 missed polls: long enough that a slow `gh` call or a restart cannot trip
# it, short enough that a wedged watcher is visible well inside the 24h stall
# window. Documented default only — callers may override.
DEFAULT_WATCHER_STALE_AFTER_MINUTES = 15.0


def watcher_heartbeat(
    conn=None,
    now: Optional[datetime] = None,
    stale_after_minutes: Optional[float] = None,
) -> Dict[str, Any]:
    """Last completed PR-watcher poll: when, how much it saw, and is it stale.

    Returns ``state`` ∈ {``never_polled``, ``stale``, ``polling``}. A stale
    watcher and a busy-but-idle watcher both report ``actions_taken == 0``; the
    timestamp is what separates them, which is the whole point of the row.

    Never raises — an install whose DB predates ``heartbeat_checks`` reports
    ``present: False`` rather than breaking whatever surface called it.
    """
    now = now or datetime.now(timezone.utc)
    stale_after = float(
        DEFAULT_WATCHER_STALE_AFTER_MINUTES
        if stale_after_minutes is None
        else stale_after_minutes
    )

    result: Dict[str, Any] = {
        "check_type": WATCHER_HEARTBEAT_CHECK_TYPE,
        "present": False,
        "last_poll_at": None,
        "minutes_since_last_poll": None,
        "tasks_checked": None,
        "actions_taken": None,
        "stale_after_minutes": stale_after,
        "stale": True,
        "state": "never_polled",
        "summary": "PR watcher has never recorded a completed poll",
        "checked_at": now.isoformat(),
    }

    own = conn is None
    if own:
        from tools.db.storage import get_connection  # noqa: PLC0415
        conn = get_connection()
    row = None
    try:
        # `result_summary`, not `details` — the live PG table has no `details`
        # column. See the matching note in pr_watcher.py::_record_heartbeat.
        row = conn.execute(
            "SELECT last_run, items_found, result_summary FROM heartbeat_checks "
            "WHERE check_type = %s ORDER BY last_run DESC, id DESC LIMIT 1",
            (WATCHER_HEARTBEAT_CHECK_TYPE,),
        ).fetchone()
    except Exception as exc:  # noqa: BLE001 — table may not exist yet
        logger.debug("watcher_heartbeat: read failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        if own:
            conn.close()

    if row is None:
        return result

    d = row if isinstance(row, dict) else {
        "last_run": row[0], "items_found": row[1], "result_summary": row[2],
    }
    last_poll = _sla_parse_ts(d.get("last_run"))
    if last_poll is None:
        return result

    actions_taken = None
    raw_summary = d.get("result_summary")
    if raw_summary:
        try:
            import json  # noqa: PLC0415

            parsed = json.loads(raw_summary)
            if isinstance(parsed, dict) and parsed.get("actions_taken") is not None:
                actions_taken = int(parsed["actions_taken"])
        except (ValueError, TypeError):
            pass

    minutes_since = round((now - last_poll).total_seconds() / 60.0, 2)
    stale = minutes_since > stale_after
    tasks_checked = int(d.get("items_found") or 0)

    result.update({
        "present": True,
        "last_poll_at": last_poll.isoformat(),
        "minutes_since_last_poll": minutes_since,
        "tasks_checked": tasks_checked,
        "actions_taken": actions_taken,
        "stale": stale,
        "state": "stale" if stale else "polling",
    })
    if stale:
        result["summary"] = (
            f"PR watcher last polled {minutes_since / 60.0:.1f}h ago "
            f"({last_poll.isoformat()})"
        )
    else:
        result["summary"] = (
            f"PR watcher polling ({minutes_since:.1f} min ago, "
            f"{tasks_checked} task(s) checked, "
            f"{'?' if actions_taken is None else actions_taken} action(s))"
        )
    return result


def throughput_stall_check(
    conn=None,
    window_hours: Optional[float] = None,
    min_active_tasks: Optional[int] = None,
    now: Optional[datetime] = None,
    watcher_stale_after_minutes: Optional[float] = None,
) -> Dict[str, Any]:
    """Is the board completing work? Reads the append-only transition timeline.

    Stalled ⇔ no task reached 'done' in the last ``window_hours`` WHILE at
    least ``min_active_tasks`` tasks sat in ``STALL_ACTIVE_STATUSES``. The
    second clause is what keeps an intentionally-empty board quiet: zero
    throughput with zero active work is idle, not broken.

    PG/SQLite portable — the cutoff comparison is done in Python (recorded_at
    is TEXT in both backends), so no date-dialect SQL is involved.
    """
    window = float(DEFAULT_STALL_WINDOW_HOURS if window_hours is None else window_hours)
    min_active = int(DEFAULT_STALL_MIN_ACTIVE_TASKS if min_active_tasks is None else min_active_tasks)
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=window)

    own = conn is None
    if own:
        from tools.db.storage import get_connection  # noqa: PLC0415
        conn = get_connection()
    try:
        # Date-prefix pre-filter: cheap, index-friendly, and safe to compare
        # lexicographically regardless of sub-second/offset formatting.
        prefilter = (cutoff - timedelta(hours=_STALL_PREFILTER_SLACK_HOURS)).strftime("%Y-%m-%d")
        done_rows = conn.execute(
            "SELECT recorded_at FROM kanban_status_transitions "
            "WHERE to_status = %s AND recorded_at >= %s",
            ("done", prefilter),
        ).fetchall()

        placeholders = ",".join(["%s"] * len(STALL_ACTIVE_STATUSES))
        active_rows = conn.execute(
            f"SELECT status, COUNT(*) AS cnt FROM kanban_tasks "  # noqa: S608 - placeholders only
            f"WHERE status IN ({placeholders}) GROUP BY status",
            list(STALL_ACTIVE_STATUSES),
        ).fetchall()

        completed = 0
        newest_in_window: Optional[datetime] = None
        for r in done_rows:
            ts = _sla_parse_ts(r["recorded_at"] if isinstance(r, dict) else r[0])
            if ts is None or ts < cutoff:
                continue
            completed += 1
            if newest_in_window is None or ts > newest_in_window:
                newest_in_window = ts

        last_done = newest_in_window
        if last_done is None:
            # Window is empty — reach past the pre-filter for "how long has it
            # been?", which is the number an operator actually wants to read.
            row = conn.execute(
                "SELECT MAX(recorded_at) AS mx FROM kanban_status_transitions WHERE to_status = %s",
                ("done",),
            ).fetchone()
            if row is not None:
                last_done = _sla_parse_ts(row["mx"] if isinstance(row, dict) else row[0])

        active_by_status: Dict[str, int] = {}
        for r in active_rows:
            d = r if isinstance(r, dict) else {"status": r[0], "cnt": r[1]}
            active_by_status[d["status"]] = int(d["cnt"])

        # Read the watcher liveness row LAST: on PG a missing `heartbeat_checks`
        # aborts the transaction, and everything above must already be read.
        watcher = watcher_heartbeat(
            conn=conn, now=now, stale_after_minutes=watcher_stale_after_minutes,
        )
    finally:
        if own:
            conn.close()

    active_total = sum(active_by_status.values())

    stalled = completed == 0 and active_total >= min_active
    if completed > 0:
        reason = "throughput_present"
    elif active_total < min_active:
        reason = "board_idle"
    else:
        reason = "stalled"

    hours_since = None
    if last_done is not None:
        hours_since = round((now - last_done).total_seconds() / 3600.0, 2)

    # Attribution is what makes zero throughput actionable: a stale watcher is a
    # broken pipe, a polling watcher with zero actions is a board with nothing
    # mergeable. `reason` keeps its original three values so existing callers
    # and the stall verifier are unaffected.
    if completed > 0:
        attribution = "throughput_present"
    elif watcher["state"] in ("stale", "never_polled"):
        attribution = "watcher_not_polling"
    else:
        attribution = "watcher_polling_nothing_mergeable"

    return {
        "stalled": stalled,
        "reason": reason,
        "stall_attribution": attribution,
        "watcher": watcher,
        "window_hours": window,
        "min_active_tasks": min_active,
        "completed_in_window": completed,
        "active_tasks": active_total,
        "active_by_status": active_by_status,
        "last_done_at": last_done.isoformat() if last_done else None,
        "hours_since_last_done": hours_since,
        "checked_at": now.isoformat(),
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
            # kax-obs-02: last completed PR-watcher poll. Read last — see the
            # note in throughput_stall_check about aborting a PG transaction.
            "watcher": watcher_heartbeat(conn=conn),
        }
    finally:
        if own:
            conn.close()


def _main(argv: Optional[List[str]] = None) -> int:
    import argparse  # noqa: PLC0415
    import json  # noqa: PLC0415

    parser = argparse.ArgumentParser(description="Kanban SLA + cycle-time metrics")
    parser.add_argument("--weeks", type=int, default=4)
    parser.add_argument("--stall", action="store_true",
                        help="Report only the board throughput stall check")
    parser.add_argument("--window-hours", type=float, default=None,
                        help="Stall window override (default: genesis_config self_monitor.board_throughput)")
    parser.add_argument("--watcher", action="store_true",
                        help="Report only the PR-watcher liveness heartbeat")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.watcher:
        print(json.dumps(watcher_heartbeat(), indent=2, default=str))
        return 0
    if args.stall:
        print(json.dumps(throughput_stall_check(window_hours=args.window_hours), indent=2, default=str))
        return 0
    print(json.dumps(board_metrics(weeks=args.weeks), indent=2, default=str))
    return 0


if __name__ == "__main__":
    import sys  # noqa: PLC0415

    sys.exit(_main(sys.argv[1:]))
