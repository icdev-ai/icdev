# CUI // SP-CTI
"""IQE observability collection adapters.

Importing this module registers three collections on the module-level Executor:
  mitre.techniques — MITRE ATT&CK Enterprise technique catalog entries;
                     filter by tactic_id, is_sub_technique, or parent_id.
  mitre.coverage   — Per-design technique coverage state, flattened from
                     observability_designs graph_json (cmp-baseline nodes);
                     filter by design_id, technique_id, or coverage_state.
  mitre.gaps       — Uncovered techniques (coverage_state == 'none') across
                     all designs; filter by tactic_id or design_id.
"""
from __future__ import annotations

import json
from typing import Any

from tools.iqe.executor import register_collection

# Sigma signal source hints per technique prefix — used by gaps_adapter.
_SIGNAL_MAP: dict[str, str] = {
    "T1003": "authentication",
    "T1021": "network_connection",
    "T1053": "scheduled_task",
    "T1055": "process_creation",
    "T1059": "process_creation",
    "T1071": "network_connection",
    "T1078": "authentication",
    "T1082": "system_info",
    "T1083": "file_access",
    "T1110": "authentication",
    "T1190": "network_connection",
    "T1486": "file_access",
    "T1562": "process_creation",
    "T1566": "email",
}


def _recommend_signal(technique_id: str) -> str:
    prefix = technique_id.split(".")[0]
    return _SIGNAL_MAP.get(technique_id, _SIGNAL_MAP.get(prefix, "generic_log"))


def _odc_conn(conn: Any) -> tuple[Any, bool]:
    """Return (connection, owned) — owned=True means caller must close it."""
    if conn is not None:
        return conn, False
    from tools.observability_canvas.db.init_db import get_connection  # noqa: PLC0415

    return get_connection(), True


def techniques_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return all MITRE ATT&CK Enterprise techniques from the catalog JSON."""
    from tools.observability_canvas.mitre_loader import load_techniques  # noqa: PLC0415

    return [
        {
            "id": t.id,
            "name": t.name,
            "description": t.description,
            "tactic_id": t.tactic_ids[0] if t.tactic_ids else None,
            "is_sub_technique": t.is_sub_technique,
            "parent_id": t.parent_id,
        }
        for t in load_techniques()
    ]


def coverage_adapter(conn: Any) -> list[dict]:
    """Flatten technique coverage from all observability_designs cmp-baseline nodes."""
    c, owned = _odc_conn(conn)
    try:
        cur = c.execute("SELECT id, name, graph_json FROM observability_designs")
        cols = [d[0] for d in cur.description]
        rows: list[dict] = []
        for raw in cur.fetchall():
            design = dict(zip(cols, raw))
            try:
                graph = json.loads(design["graph_json"] or '{"nodes":[]}')
            except (ValueError, TypeError):
                graph = {"nodes": []}
            for node in graph.get("nodes", []):
                if node.get("type") != "cmp-baseline":
                    continue
                try:
                    cfg = json.loads(node.get("config_json") or "{}")
                except (ValueError, TypeError):
                    cfg = {}
                for tech in cfg.get("techniques", []):
                    covered = bool(tech.get("covered", False))
                    rows.append(
                        {
                            "design_id": design["id"],
                            "design_name": design["name"],
                            "technique_id": tech.get("id", ""),
                            "technique_name": tech.get("name", ""),
                            "covered": covered,
                            "coverage_state": "full" if covered else "none",
                            "tactic_id": tech.get("tactic_id", ""),
                        }
                    )
        return rows
    finally:
        if owned:
            c.close()


def gaps_adapter(conn: Any) -> list[dict]:
    """Return uncovered techniques (coverage_state == 'none') across all designs."""
    return [
        {
            "design_id": r["design_id"],
            "design_name": r["design_name"],
            "technique_id": r["technique_id"],
            "technique_name": r["technique_name"],
            "tactic_id": r["tactic_id"],
            "coverage_state": r["coverage_state"],
            "recommended_signal": _recommend_signal(r["technique_id"]),
        }
        for r in coverage_adapter(conn)
        if r["coverage_state"] == "none"
    ]


def ai_decisions_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return AI decision records for ODC from canvas_ai_decisions (main icdev.db)."""
    try:
        from tools.db.storage import get_connection as _main_conn  # noqa: PLC0415
        with _main_conn() as _c:
            cur = _c.execute(
                "SELECT * FROM canvas_ai_decisions WHERE canvas_type='odc' ORDER BY created_at DESC"
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


register_collection("mitre.techniques", techniques_adapter)
register_collection("mitre.coverage", coverage_adapter)
register_collection("mitre.gaps", gaps_adapter)
register_collection("observability.ai_decisions", ai_decisions_adapter)
