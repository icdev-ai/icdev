# CUI // SP-CTI
"""IQE infrastructure collection adapters.

Importing this module registers two collections on the module-level Executor:
  infra.resources  — cloud/on-prem resource inventory (idc_infra_resources)
  infra.snapshots  — point-in-time infra state snapshots (idc_infra_snapshots)

Both collections support filtering by csp, region, and classification.
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def resources_adapter(conn: Any) -> list[dict]:
    """Return rows from idc_infra_resources."""
    if conn is None:
        from tools.db.storage import get_connection  # noqa: PLC0415
        conn = get_connection()
    cur = conn.execute(
        "SELECT id, csp, region, resource_type, resource_name, "
        "classification, tags, cost_per_month, config, created_at "
        "FROM idc_infra_resources"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def snapshots_adapter(conn: Any) -> list[dict]:
    """Return rows from idc_infra_snapshots."""
    if conn is None:
        from tools.db.storage import get_connection  # noqa: PLC0415
        conn = get_connection()
    cur = conn.execute(
        "SELECT id, snapshot_id, taken_at, csp, region, classification, "
        "resource_count, baseline_hash, notes, created_at "
        "FROM idc_infra_snapshots"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def ai_decisions_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return AI decision records for IDC from canvas_ai_decisions (main icdev.db)."""
    try:
        from tools.db.storage import get_connection as _main_conn  # noqa: PLC0415
        with _main_conn() as _c:
            cur = _c.execute(
                "SELECT * FROM canvas_ai_decisions WHERE canvas_type='idc' ORDER BY created_at DESC"
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


register_collection("infra.resources", resources_adapter)
register_collection("infra.snapshots", snapshots_adapter)
register_collection("infra.ai_decisions", ai_decisions_adapter)
