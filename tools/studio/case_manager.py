"""ICDEV™ Studio — Case Management Engine.

Visual lifecycle designer with finite-state-machine case tracking,
SLA monitoring, and append-only audit history.

Architecture Decision D364: FSM pattern — deterministic, auditable, NIST AU.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str = "case") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ── Pre-built lifecycle templates ─────────────────────────────────────

LIFECYCLE_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "tpl-finding-lifecycle",
        "name": "Finding Lifecycle",
        "description": "Track compliance/security findings from detection to closure",
        "category": "compliance",
        "states": [
            {"id": "open", "label": "Open", "color": "#ef4444", "initial": True},
            {"id": "triaging", "label": "Triaging", "color": "#f59e0b"},
            {"id": "in_remediation", "label": "In Remediation", "color": "#3b82f6"},
            {"id": "verification", "label": "Verification", "color": "#8b5cf6"},
            {"id": "closed", "label": "Closed", "color": "#22c55e", "terminal": True},
            {"id": "wont_fix", "label": "Won't Fix", "color": "#6b7280", "terminal": True},
        ],
        "transitions": [
            {"from": "open", "to": "triaging", "label": "Start Triage"},
            {"from": "triaging", "to": "in_remediation", "label": "Assign Fix"},
            {"from": "triaging", "to": "wont_fix", "label": "Accept Risk"},
            {"from": "in_remediation", "to": "verification", "label": "Submit Fix"},
            {"from": "verification", "to": "closed", "label": "Verify & Close"},
            {"from": "verification", "to": "in_remediation", "label": "Reopen"},
        ],
        "sla_rules": [
            {"state": "open", "max_hours": 24, "escalate_to": "isso"},
            {"state": "in_remediation", "max_hours": 168, "escalate_to": "pm"},
        ],
    },
    {
        "id": "tpl-change-request",
        "name": "Change Request",
        "description": "Configuration change management workflow",
        "category": "operations",
        "states": [
            {"id": "draft", "label": "Draft", "color": "#6b7280", "initial": True},
            {"id": "submitted", "label": "Submitted", "color": "#f59e0b"},
            {"id": "reviewing", "label": "Under Review", "color": "#3b82f6"},
            {"id": "approved", "label": "Approved", "color": "#22c55e"},
            {"id": "implementing", "label": "Implementing", "color": "#8b5cf6"},
            {"id": "completed", "label": "Completed", "color": "#22c55e", "terminal": True},
            {"id": "rejected", "label": "Rejected", "color": "#ef4444", "terminal": True},
            {"id": "rolled_back", "label": "Rolled Back", "color": "#ef4444", "terminal": True},
        ],
        "transitions": [
            {"from": "draft", "to": "submitted", "label": "Submit"},
            {"from": "submitted", "to": "reviewing", "label": "Begin Review"},
            {"from": "reviewing", "to": "approved", "label": "Approve"},
            {"from": "reviewing", "to": "rejected", "label": "Reject"},
            {"from": "approved", "to": "implementing", "label": "Start Implementation"},
            {"from": "implementing", "to": "completed", "label": "Complete"},
            {"from": "implementing", "to": "rolled_back", "label": "Rollback"},
        ],
        "sla_rules": [
            {"state": "submitted", "max_hours": 48, "escalate_to": "reviewer"},
        ],
    },
    {
        "id": "tpl-incident-response",
        "name": "Incident Response",
        "description": "NIST 800-61 incident handling lifecycle",
        "category": "security",
        "states": [
            {"id": "detected", "label": "Detected", "color": "#ef4444", "initial": True},
            {"id": "contained", "label": "Contained", "color": "#f59e0b"},
            {"id": "eradicated", "label": "Eradicated", "color": "#3b82f6"},
            {"id": "recovering", "label": "Recovering", "color": "#8b5cf6"},
            {"id": "closed", "label": "Closed", "color": "#22c55e", "terminal": True},
        ],
        "transitions": [
            {"from": "detected", "to": "contained", "label": "Contain"},
            {"from": "contained", "to": "eradicated", "label": "Eradicate"},
            {"from": "eradicated", "to": "recovering", "label": "Begin Recovery"},
            {"from": "recovering", "to": "closed", "label": "Close & Lessons Learned"},
            {"from": "contained", "to": "detected", "label": "Re-detected"},
        ],
        "sla_rules": [
            {"state": "detected", "max_hours": 1, "escalate_to": "incident_commander"},
            {"state": "contained", "max_hours": 4, "escalate_to": "ciso"},
        ],
    },
]


def get_lifecycle_templates() -> list[dict]:
    return LIFECYCLE_TEMPLATES


# ── Case Type CRUD ────────────────────────────────────────────────────


def create_case_type(
    name: str,
    lifecycle: dict,
    *,
    description: str = "",
) -> dict:
    """Create a case type with lifecycle definition (states + transitions + SLA)."""
    type_id = _new_id("ctype")

    # Validate lifecycle
    states = lifecycle.get("states", [])
    transitions = lifecycle.get("transitions", [])
    if not states:
        return {"status": "error", "error": "Lifecycle must have at least one state"}

    state_ids = {s["id"] for s in states}
    for t in transitions:
        if t["from"] not in state_ids or t["to"] not in state_ids:
            return {"status": "error", "error": f"Transition references unknown state: {t['from']} -> {t['to']}"}

    lifecycle["description"] = description

    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO studio_case_types (type_id, name, lifecycle_json, created_at)
               VALUES (%s, %s, %s, %s)""",
            (type_id, name, json.dumps(lifecycle), _now_iso()),
        )
        conn.commit()
        return {"status": "ok", "type_id": type_id}
    finally:
        conn.close()


def list_case_types() -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM studio_case_types ORDER BY created_at DESC").fetchall()
        result = []
        for r in rows:
            d = dict(r)
            lifecycle = json.loads(d.get("lifecycle_json", "{}"))
            d["states_count"] = len(lifecycle.get("states", []))
            d["transitions_count"] = len(lifecycle.get("transitions", []))
            result.append(d)
        return result
    finally:
        conn.close()


def get_case_type(type_id: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM studio_case_types WHERE type_id = %s", (type_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ── Case CRUD ─────────────────────────────────────────────────────────


def create_case(
    type_id: str,
    title: str,
    *,
    description: str = "",
    priority: str = "medium",
    assigned_to: str | None = None,
    created_by: str = "studio",
    form_submission_id: str | None = None,
) -> dict:
    """Create a case instance in the initial state."""
    case_type = get_case_type(type_id)
    if not case_type:
        return {"status": "error", "error": "Case type not found"}

    lifecycle = json.loads(case_type.get("lifecycle_json", "{}"))
    states = lifecycle.get("states", [])
    initial = next((s for s in states if s.get("initial")), states[0] if states else None)
    if not initial:
        return {"status": "error", "error": "No initial state defined"}

    case_id = _new_id("case")
    now = _now_iso()

    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO studio_cases
               (case_id, type_id, title, description, current_state, priority,
                assigned_to, created_by, created_at, updated_at, form_submission_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                case_id,
                type_id,
                title,
                description,
                initial["id"],
                priority,
                assigned_to,
                created_by,
                now,
                now,
                form_submission_id,
            ),
        )
        # Append-only history entry
        conn.execute(
            """INSERT INTO studio_case_history
               (history_id, case_id, from_state, to_state, changed_by, changed_at, comment)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (_new_id("hist"), case_id, None, initial["id"], created_by, now, "Case created"),
        )
        conn.commit()
        return {"status": "ok", "case_id": case_id, "initial_state": initial["id"]}
    finally:
        conn.close()


def list_cases(
    *,
    type_id: str | None = None,
    state: str | None = None,
    priority: str | None = None,
    assigned_to: str | None = None,
) -> list[dict]:
    conn = get_connection()
    try:
        sql = "SELECT * FROM studio_cases WHERE 1=1"
        params: list[Any] = []
        if type_id:
            sql += " AND type_id = ?"
            params.append(type_id)
        if state:
            sql += " AND current_state = ?"
            params.append(state)
        if priority:
            sql += " AND priority = ?"
            params.append(priority)
        if assigned_to:
            sql += " AND assigned_to = ?"
            params.append(assigned_to)
        sql += " ORDER BY updated_at DESC"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_case(case_id: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM studio_cases WHERE case_id = %s", (case_id,)).fetchone()
        if not row:
            return None
        case = dict(row)
        # Attach history
        history = conn.execute(
            "SELECT * FROM studio_case_history WHERE case_id = %s ORDER BY changed_at",
            (case_id,),
        ).fetchall()
        case["history"] = [dict(h) for h in history]
        return case
    finally:
        conn.close()


def transition_case(
    case_id: str,
    to_state: str,
    *,
    changed_by: str = "studio",
    comment: str = "",
) -> dict:
    """Transition a case to a new state (validates against lifecycle)."""
    case = get_case(case_id)
    if not case:
        return {"status": "error", "error": "Case not found"}

    case_type = get_case_type(case["type_id"])
    if not case_type:
        return {"status": "error", "error": "Case type not found"}

    lifecycle = json.loads(case_type.get("lifecycle_json", "{}"))
    transitions = lifecycle.get("transitions", [])

    from_state = case["current_state"]
    valid = any(t["from"] == from_state and t["to"] == to_state for t in transitions)
    if not valid:
        return {
            "status": "error",
            "error": f"Invalid transition: {from_state} -> {to_state}",
        }

    now = _now_iso()
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE studio_cases SET current_state = %s, updated_at = %s WHERE case_id = %s",
            (to_state, now, case_id),
        )
        conn.execute(
            """INSERT INTO studio_case_history
               (history_id, case_id, from_state, to_state, changed_by, changed_at, comment)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (_new_id("hist"), case_id, from_state, to_state, changed_by, now, comment),
        )
        conn.commit()
        return {"status": "ok", "case_id": case_id, "from": from_state, "to": to_state}
    finally:
        conn.close()


def get_case_board(type_id: str) -> dict:
    """Get Kanban-style board: cases grouped by state."""
    case_type = get_case_type(type_id)
    if not case_type:
        return {"status": "error", "error": "Case type not found"}

    lifecycle = json.loads(case_type.get("lifecycle_json", "{}"))
    states = lifecycle.get("states", [])
    cases = list_cases(type_id=type_id)

    board: dict[str, list[dict]] = {}
    for s in states:
        board[s["id"]] = []

    for c in cases:
        state = c.get("current_state", "")
        if state in board:
            board[state].append(c)

    return {
        "status": "ok",
        "type_id": type_id,
        "type_name": case_type.get("name", ""),
        "states": states,
        "transitions": lifecycle.get("transitions", []),
        "columns": board,
        "total_cases": len(cases),
    }


# ── CLI ───────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="ICDEV™ Studio Case Manager")
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("templates", help="List lifecycle templates")
    sub.add_parser("types", help="List case types")
    sub.add_parser("cases", help="List all cases")

    p_board = sub.add_parser("board", help="Get Kanban board for a case type")
    p_board.add_argument("type_id")

    args = parser.parse_args()
    result: Any = None

    if args.command == "templates":
        result = get_lifecycle_templates()
    elif args.command == "types":
        result = list_case_types()
    elif args.command == "cases":
        result = list_cases()
    elif args.command == "board":
        result = get_case_board(args.type_id)
    else:
        parser.print_help()
        return

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
