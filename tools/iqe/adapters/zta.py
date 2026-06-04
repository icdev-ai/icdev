# CUI // SP-CTI
"""IQE ZTA/LAC collection adapters.

Importing this module registers two collections on the module-level Executor:
  lac.audit_trail  — All LAC simulator access decisions from zta_lac_audit;
                     filter by decision, resource_is_eci, break_glass_activated, etc.
  lac.scenarios    — Static canonical LAC scenario definitions (4 scenarios).
"""
from __future__ import annotations

import json
from typing import Any

from tools.iqe.executor import register_collection


def lac_audit_trail_adapter(conn: Any) -> list[dict]:
    """Return one row per LAC access simulation decision from zta_lac_audit."""
    if conn is None:
        from tools.db.storage import get_connection  # noqa: PLC0415
        conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT id, scenario_name, decision, deny_reasons, "
            "principal_citizenship, principal_clearance, principal_cois, "
            "resource_id, resource_is_eci, action, environment, "
            "break_glass_activated, audit_json, created_at "
            "FROM zta_lac_audit ORDER BY created_at DESC"
        )
        cols = [d[0] for d in cur.description]
        rows = []
        for raw in cur.fetchall():
            row = dict(zip(cols, raw))
            for json_field in ("deny_reasons", "principal_cois", "environment", "audit_json"):
                try:
                    row[json_field] = json.loads(row.get(json_field) or "null")
                except (ValueError, TypeError):
                    pass
            rows.append(row)
        return rows
    except Exception:
        return []


def lac_scenarios_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return the 4 canonical LAC scenario definitions."""
    try:
        from tools.zta.lac_scenarios import list_scenarios  # noqa: PLC0415
        return list_scenarios()
    except Exception:
        return []


register_collection("lac.audit_trail", lac_audit_trail_adapter)
register_collection("lac.scenarios", lac_scenarios_adapter)
