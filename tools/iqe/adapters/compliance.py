# CUI // SP-CTI
"""IQE compliance collection adapters.

Importing this module registers three collections on the module-level Executor:
  compliance.snapshots  — PI-level compliance score snapshots (pi_compliance_tracking)
  compliance.controls   — Per-project control implementation status (project_controls + compliance_controls)
  compliance.violations — Open findings / POA&M items (poam_items)
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def snapshots_adapter(conn: Any) -> list[dict]:
    """Return rows from pi_compliance_tracking."""
    if conn is None:
        from tools.db.storage import get_connection  # noqa: PLC0415
        conn = get_connection()
    cur = conn.execute(
        "SELECT id, project_id, pi_number, pi_start_date, pi_end_date, "
        "compliance_score_start, compliance_score_end, controls_implemented, "
        "controls_remaining, poam_items_closed, poam_items_opened, "
        "findings_remediated, notes, created_at "
        "FROM pi_compliance_tracking"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def controls_adapter(conn: Any) -> list[dict]:
    """Return project_controls rows enriched with compliance_controls metadata."""
    if conn is None:
        from tools.db.storage import get_connection  # noqa: PLC0415
        conn = get_connection()
    cur = conn.execute(
        "SELECT pc.id, pc.project_id, pc.control_id, "
        "cc.family, cc.title, cc.impact_level, "
        "pc.implementation_status, pc.implementation_description, "
        "pc.responsible_role "
        "FROM project_controls pc "
        "LEFT JOIN compliance_controls cc ON cc.id = pc.control_id"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def violations_adapter(conn: Any) -> list[dict]:
    """Return rows from poam_items."""
    if conn is None:
        from tools.db.storage import get_connection  # noqa: PLC0415
        conn = get_connection()
    cur = conn.execute(
        "SELECT id, project_id, weakness_id, weakness_description, "
        "severity, source, control_id, status, corrective_action, "
        "milestone_date, completion_date, responsible_party, created_at "
        "FROM poam_items"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


register_collection("compliance.snapshots", snapshots_adapter)
register_collection("compliance.controls", controls_adapter)
register_collection("compliance.violations", violations_adapter)
