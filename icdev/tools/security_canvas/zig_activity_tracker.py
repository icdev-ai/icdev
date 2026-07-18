# [CUI // SP-CTI]
"""ZIG Activity Tracker — CRUD for zig_activity_completions table.

Activities can be: not_started | in_progress | complete
Each completion can carry an evidence_note and completed_by fields.
"""

from datetime import datetime, timezone

from tools.security_canvas.db.init_db import get_connection


def get_activity_completion(activity_id: str) -> dict:
    """Get completion record for a single activity."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM zig_activity_completions WHERE activity_id=?",
            (activity_id,),
        ).fetchone()
        if row:
            return dict(row)
        return {"activity_id": activity_id, "status": "not_started", "evidence_note": None}
    finally:
        conn.close()


def set_activity_status(activity_id: str, status: str, evidence_note: str = None,
                        completed_by: str = None, target_id: str = "icdev-self") -> dict:
    """Mark an activity as not_started / in_progress / complete for one target.

    Completions are keyed by (activity_id, target_id) — the same activity is
    tracked independently per assessment target (zig_activity_completions unique
    index idx_zig_comp_act). ``target_id`` is the LAST parameter so the existing
    positional call site set_activity_status(id, status, evidence_note,
    completed_by) keeps working unchanged.

    Returns the updated completion record.
    """
    valid = {"not_started", "in_progress", "complete"}
    if status not in valid:
        raise ValueError(f"status must be one of {valid}")

    now = datetime.now(timezone.utc).isoformat()
    completed_at = now if status == "complete" else None

    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT id FROM zig_activity_completions WHERE activity_id=? AND target_id=?",
            (activity_id, target_id),
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE zig_activity_completions SET status=?, evidence_note=?, "
                "completed_by=?, completed_at=?, updated_at=? "
                "WHERE activity_id=? AND target_id=?",
                (status, evidence_note, completed_by, completed_at, now,
                 activity_id, target_id),
            )
        else:
            conn.execute(
                "INSERT INTO zig_activity_completions "
                "(activity_id, target_id, status, evidence_note, completed_by, "
                "completed_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (activity_id, target_id, status, evidence_note, completed_by,
                 completed_at, now),
            )
        conn.commit()
        return {"activity_id": activity_id, "target_id": target_id,
                "status": status, "updated_at": now}
    finally:
        conn.close()


def bulk_activity_status(activity_ids: list, status: str) -> int:
    """Set multiple activities to the same status. Returns count updated."""
    count = 0
    for aid in activity_ids:
        set_activity_status(aid, status)
        count += 1
    return count


def get_phase_completions(phase: str, target_id: str = "icdev-self") -> dict:
    """Get completion summary for a ZIG phase (discovery | phase1 | phase2)."""
    conn = get_connection()
    try:
        acts = conn.execute(
            "SELECT a.id FROM zig_activities a WHERE a.phase=?", (phase,)
        ).fetchall()
        act_ids = [a["id"] for a in acts]
        total = len(act_ids)
        if total == 0:
            return {"phase": phase, "total": 0, "complete": 0, "in_progress": 0,
                    "not_started": 0, "pct_complete": 0}

        placeholders = ",".join("?" * len(act_ids))
        completions = conn.execute(
            f"SELECT status FROM zig_activity_completions "
            f"WHERE activity_id IN ({placeholders}) AND target_id=?",
            act_ids + [target_id],
        ).fetchall()

        status_map = {c["status"]: 0 for c in completions}
        for c in completions:
            status_map[c["status"]] = status_map.get(c["status"], 0) + 1

        complete = status_map.get("complete", 0)
        in_progress = status_map.get("in_progress", 0)
        not_started = total - complete - in_progress

        return {
            "phase": phase,
            "total": total,
            "complete": complete,
            "in_progress": in_progress,
            "not_started": max(0, not_started),
            "pct_complete": round(complete / total * 100) if total > 0 else 0,
        }
    finally:
        conn.close()
