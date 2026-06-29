# CUI // SP-CTI
"""F3EAD Targeting Workflow — Find, Fix, Finish, Exploit, Analyze, Disseminate.

Implements the US military targeting cycle as a Kanban-style tracker.
Each target card progresses through 6 phases. Phase transitions are
logged in sg_target_events (append-only, NIST AU).

Doctrinal basis: JP 3-60 Joint Targeting.
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import uuid
from datetime import datetime, timezone
from typing import Any

logger = get_logger("icdev.strategos.f3ead")

PHASES = ["find", "fix", "finish", "exploit", "analyze", "disseminate", "complete"]

PHASE_DESCRIPTIONS = {
    "find":         "Identify, locate, and characterize the target.",
    "fix":          "Track and maintain continuous contact with target.",
    "finish":       "Engage the target with lethal or non-lethal means.",
    "exploit":      "Collect intelligence from the aftermath.",
    "analyze":      "Process intelligence; assess damage and next steps.",
    "disseminate":  "Distribute products to stakeholders.",
    "complete":     "Cycle closed. Target processed.",
}

PHASE_COLORS = {
    "find":         "#3b82f6",
    "fix":          "#f59e0b",
    "finish":       "#e94560",
    "exploit":      "#8b5cf6",
    "analyze":      "#10b981",
    "disseminate":  "#06b6d4",
    "complete":     "#6b7280",
}


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_target(
    target_name: str,
    target_type: str = "node",
    description: str = "",
    responsible_element: str = "",
    priority: int = 3,
    theater: str = "unspecified",
    geo_hint: str = "",
) -> dict[str, Any]:
    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        target_id = str(uuid.uuid4())
        now = _now_utc()
        conn.execute(
            "INSERT INTO sg_f3ead_targets "
            "(id, target_name, target_type, description, phase, responsible_element, "
            " priority, theater, geo_hint, phase_entered_at, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, 'find', %s, %s, %s, %s, %s, %s, %s)",
            (target_id, target_name, target_type, description,
             responsible_element, priority, theater, geo_hint, now, now, now),
        )
        conn.execute(
            "INSERT INTO sg_target_events "
            "(id, target_id, from_phase, to_phase, actor, notes, created_at) "
            "VALUES (%s, %s, NULL, 'find', 'system', 'Target created', %s)",
            (str(uuid.uuid4()), target_id, now),
        )
        conn.commit()
        return {"target_id": target_id, "target_name": target_name, "phase": "find"}
    finally:
        conn.close()


def advance_phase(
    target_id: str,
    actor: str = "analyst",
    notes: str = "",
    evidence_notes: str = "",
) -> dict[str, Any]:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        row = conn.execute(
            f"SELECT phase FROM sg_f3ead_targets WHERE id = {ph}", (target_id,)  # nosec B608
        ).fetchone()
        if not row:
            return {"error": "target not found"}
        current = row[0] if not isinstance(row, dict) else row["phase"]
        if current == "complete":
            return {"error": "target already complete"}
        idx = PHASES.index(current)
        next_phase = PHASES[idx + 1]
        now = _now_utc()
        conn.execute(
            f"UPDATE sg_f3ead_targets SET phase = {ph}, phase_entered_at = {ph}, "  # nosec B608
            f"updated_at = {ph}"
            + (f", evidence_notes = {ph}" if evidence_notes else "")
            + f" WHERE id = {ph}",
            ((next_phase, now, now, evidence_notes, target_id) if evidence_notes
             else (next_phase, now, now, target_id)),
        )
        conn.execute(
            "INSERT INTO sg_target_events "
            "(id, target_id, from_phase, to_phase, actor, notes, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (str(uuid.uuid4()), target_id, current, next_phase, actor, notes, now),
        )
        conn.commit()
        return {"target_id": target_id, "from_phase": current,
                "to_phase": next_phase, "phase_entered_at": now}
    finally:
        conn.close()


def set_phase(
    target_id: str,
    phase: str,
    actor: str = "analyst",
    notes: str = "",
) -> dict[str, Any]:
    """Jump to any phase (for corrections)."""
    if phase not in PHASES:
        return {"error": f"invalid phase: {phase}"}
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        row = conn.execute(
            f"SELECT phase FROM sg_f3ead_targets WHERE id = {ph}", (target_id,)  # nosec B608
        ).fetchone()
        if not row:
            return {"error": "target not found"}
        current = row[0] if not isinstance(row, dict) else row["phase"]
        now = _now_utc()
        conn.execute(
            f"UPDATE sg_f3ead_targets SET phase = {ph}, phase_entered_at = {ph}, "  # nosec B608
            f"updated_at = {ph} WHERE id = {ph}",
            (phase, now, now, target_id),
        )
        conn.execute(
            "INSERT INTO sg_target_events "
            "(id, target_id, from_phase, to_phase, actor, notes, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (str(uuid.uuid4()), target_id, current, phase, actor,
             notes or f"Manual phase set by {actor}", now),
        )
        conn.commit()
        return {"target_id": target_id, "phase": phase}
    finally:
        conn.close()


def get_board(theater: str = "") -> dict[str, Any]:
    """Return all targets grouped by phase for Kanban board rendering."""
    from tools.db.storage import get_connection, is_pg
    ph_sym = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        if theater:
            rows = conn.execute(
                f"SELECT id, target_name, target_type, description, phase, "  # nosec B608
                f"responsible_element, priority, theater, phase_entered_at, created_at "
                f"FROM sg_f3ead_targets WHERE theater = {ph_sym} ORDER BY priority, created_at",
                (theater,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, target_name, target_type, description, phase, "  # nosec B608
                "responsible_element, priority, theater, phase_entered_at, created_at "
                "FROM sg_f3ead_targets ORDER BY priority, created_at"
            ).fetchall()
        cols = ("id", "target_name", "target_type", "description", "phase",
                "responsible_element", "priority", "theater",
                "phase_entered_at", "created_at")
        targets = [dict(zip(cols, r)) for r in rows]
        board = {p: [] for p in PHASES}
        for t in targets:
            board[t["phase"]].append(t)
        return {"phases": PHASES, "board": board,
                "phase_descriptions": PHASE_DESCRIPTIONS,
                "phase_colors": PHASE_COLORS,
                "total": len(targets)}
    finally:
        conn.close()


def delete_target(target_id: str) -> bool:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        conn.execute(
            f"DELETE FROM sg_target_events WHERE target_id = {ph}", (target_id,)  # nosec B608
        )
        conn.execute(
            f"DELETE FROM sg_f3ead_targets WHERE id = {ph}", (target_id,)  # nosec B608
        )
        conn.commit()
        return True
    except Exception as exc:
        logger.error("delete_target failed: %s", exc)
        return False
    finally:
        conn.close()
