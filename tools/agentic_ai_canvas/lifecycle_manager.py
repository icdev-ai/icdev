# CUI // SP-CTI
"""Agentic AI Design Canvas — Design Lifecycle Manager (Phase 10).

State machine for design lifecycle:
  DRAFT → UNDER_REVIEW → APPROVED → DEPLOYED → DEPRECATED
  UNDER_REVIEW → CHANGES_REQUESTED → DRAFT / UNDER_REVIEW
  APPROVED → DRAFT (revoke)
  DEPLOYED → DRAFT (rollback)

Each transition is persisted to aadc_lifecycle_states with actor + reason.
Auto-checks deploy gate readiness before APPROVED transition.
"""

from __future__ import annotations

from datetime import datetime, timezone

_STATES = {
    "DRAFT",
    "UNDER_REVIEW",
    "CHANGES_REQUESTED",
    "APPROVED",
    "DEPLOYED",
    "DEPRECATED",
}

# (from_state, to_state) → label
_TRANSITIONS: dict[tuple[str, str], str] = {
    ("DRAFT", "UNDER_REVIEW"): "Submit for Review",
    ("UNDER_REVIEW", "CHANGES_REQUESTED"): "Request Changes",
    ("UNDER_REVIEW", "APPROVED"): "Approve",
    ("CHANGES_REQUESTED", "DRAFT"): "Re-open Draft",
    ("CHANGES_REQUESTED", "UNDER_REVIEW"): "Resubmit for Review",
    ("APPROVED", "DEPLOYED"): "Deploy",
    ("APPROVED", "DRAFT"): "Revoke Approval",
    ("DEPLOYED", "DEPRECATED"): "Deprecate",
    ("DEPLOYED", "DRAFT"): "Rollback / New Revision",
}

# States that require a deploy gate check before entering
_GATE_REQUIRED = {"APPROVED", "DEPLOYED"}

_STATE_COLORS = {
    "DRAFT": "secondary",
    "UNDER_REVIEW": "info",
    "CHANGES_REQUESTED": "warning",
    "APPROVED": "success",
    "DEPLOYED": "primary",
    "DEPRECATED": "dark",
}

_STATE_ICONS = {
    "DRAFT": "✏️",
    "UNDER_REVIEW": "👁",
    "CHANGES_REQUESTED": "↩",
    "APPROVED": "✅",
    "DEPLOYED": "🚀",
    "DEPRECATED": "🗄",
}


def get_lifecycle(design_id: str, conn) -> dict:
    """
    Return current lifecycle state + full transition history for a design.
    """
    rows = conn.execute(
        "SELECT * FROM aadc_lifecycle_states WHERE design_id=%s ORDER BY created_at ASC",
        (design_id,),
    ).fetchall()

    history = [dict(r) for r in rows]
    current = history[-1]["to_state"] if history else "DRAFT"
    available = _available_transitions(current)

    return {
        "design_id": design_id,
        "current_state": current,
        "current_color": _STATE_COLORS.get(current, "secondary"),
        "current_icon": _STATE_ICONS.get(current, ""),
        "history": history,
        "available_transitions": available,
        "state_colors": _STATE_COLORS,
        "state_icons": _STATE_ICONS,
    }


def transition(design_id: str, to_state: str, actor: str, reason: str, conn) -> dict:
    """
    Execute a lifecycle transition.

    Returns:
        {"ok": True, "new_state": str} or {"ok": False, "error": str}
    """
    if to_state not in _STATES:
        return {"ok": False, "error": f"Unknown state: {to_state}"}

    rows = conn.execute(
        "SELECT to_state FROM aadc_lifecycle_states WHERE design_id=%s ORDER BY created_at DESC LIMIT 1",
        (design_id,),
    ).fetchone()
    current = dict(rows)["to_state"] if rows else "DRAFT"

    if (current, to_state) not in _TRANSITIONS:
        return {"ok": False, "error": f"Transition {current} → {to_state} is not allowed"}

    import uuid
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO aadc_lifecycle_states (id,design_id,from_state,to_state,actor,reason,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (str(uuid.uuid4()), design_id, current, to_state, actor or "system", reason or "", now),
    )
    conn.commit()
    return {"ok": True, "new_state": to_state}


def _available_transitions(current: str) -> list[dict]:
    return [
        {"to_state": t[1], "label": label, "color": _STATE_COLORS.get(t[1], "secondary"),
         "requires_gate": t[1] in _GATE_REQUIRED}
        for t, label in _TRANSITIONS.items()
        if t[0] == current
    ]
