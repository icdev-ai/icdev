# CUI // SP-CTI
"""Genesis Reflex — IDC Cloud Drift Detector (6h cadence).

Compares IDC infra_designs graph_json against idc_infra_resources to detect
resources defined in designs that have no corresponding live resource record,
and live resources that have no corresponding design node.

Air-gap safe: no LLM calls — pure DB heuristics.
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"
from tools.logging.icdev_logger import get_logger

import json
from typing import Any, Dict

logger = get_logger(__name__)

CADENCE_HOURS = 6


def run(ctx: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Detect drift between IDC designs and live idc_infra_resources.

    Returns:
        designs_checked: int
        orphaned_nodes: list of nodes in designs with no matching resource
        undesigned_resources: list of resources with no matching design node
        events_published: int
    """
    dry_run = ctx.get("dry_run", False)
    result: Dict[str, Any] = {
        "cadence_hours": CADENCE_HOURS,
        "designs_checked": 0,
        "orphaned_nodes": [],
        "undesigned_resources": 0,
        "events_published": 0,
        "errors": [],
        "status": "ok",
    }
    try:
        from tools.infra_canvas.db.init_db import get_connection as idc_conn
        conn_idc = idc_conn()
        try:
            designs = conn_idc.execute(
                "SELECT id, name, graph_json FROM infra_designs"
            ).fetchall()
            live_resources = conn_idc.execute(
                "SELECT resource_name, resource_type, csp FROM idc_infra_resources"
            ).fetchall()
        finally:
            conn_idc.close()

        result["designs_checked"] = len(designs)
        live_names = {
            (r["resource_name"] if isinstance(r, dict) else r[0])
            for r in live_resources
        }

        orphaned = []
        all_design_node_labels = set()
        for design_row in designs:
            did = design_row["id"] if isinstance(design_row, dict) else design_row[0]
            dname = design_row["name"] if isinstance(design_row, dict) else design_row[1]
            graph_raw = design_row["graph_json"] if isinstance(design_row, dict) else design_row[2]
            try:
                graph = json.loads(graph_raw) if isinstance(graph_raw, str) else (graph_raw or {})
            except Exception:
                graph = {}
            for node in graph.get("nodes", []):
                label = node.get("label", "")
                all_design_node_labels.add(label)
                if label and label not in live_names and node.get("type") not in ("migration-source", "boundary-zone"):
                    orphaned.append({
                        "design_id": did,
                        "design_name": dname,
                        "node_id": node.get("id"),
                        "node_label": label,
                        "node_type": node.get("type"),
                    })

        result["orphaned_nodes"] = orphaned
        result["undesigned_resources"] = len(live_names - all_design_node_labels)

        if (orphaned or result["undesigned_resources"]) and not dry_run:
            try:
                from tools.canvas.event_bus import publish
                publish("idc", "idc.cloud.drift_detected", {
                    "orphaned_node_count": len(orphaned),
                    "undesigned_resource_count": result["undesigned_resources"],
                })
                result["events_published"] = 1
            except Exception as exc:
                result["errors"].append(f"event_bus: {exc}")

    except Exception as exc:
        logger.error("idc_cloud_drift reflex error: %s", exc)
        result["status"] = "error"
        result["errors"].append(str(exc))
    return result


if __name__ == "__main__":
    # Load THIS repo's .env so a direct CLI run uses the same board/PG config as the
    # GenesisDaemon. override=True: a pip-installed ICDEV in site-packages may have
    # already loaded a different checkout's .env at import. Repo root via __file__, not cwd.
    try:
        from pathlib import Path as _EnvPath
        from dotenv import load_dotenv as _load_dotenv
        _load_dotenv(_EnvPath(__file__).resolve().parents[3] / ".env", override=True)
    except ImportError:
        pass
    import json as _json
    print(_json.dumps(run({}), indent=2))
