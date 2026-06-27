"""Shared helper: read DDC canvas designs from data_canvas.db."""
from __future__ import annotations
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _dc_conn():
    from tools.data_canvas.db.init_db import get_connection
    return get_connection()


def load_designs(project_id: str) -> dict[str, dict]:
    """Return {design_id: {nodes: [...], edges: [...]}}.

    If project_id looks like a design UUID, load that specific design.
    Otherwise load all designs (up to 20, newest first).
    """
    conn = _dc_conn()
    try:
        import re
        is_uuid = bool(re.match(
            r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
            project_id, re.I
        ))
        if is_uuid:
            rows = conn.execute(
                "SELECT id, name, graph_json FROM data_designs WHERE id=%s",
                (project_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, name, graph_json FROM data_designs ORDER BY updated_at DESC LIMIT 20"
            ).fetchall()
    finally:
        conn.close()

    designs: dict[str, dict] = {}
    for row in (rows or []):
        did = row[0]
        try:
            g = json.loads(row[2]) if row[2] else {}
        except (json.JSONDecodeError, TypeError):
            g = {}
        raw_nodes = g.get("nodes", [])
        raw_edges = g.get("edges", [])
        nodes = [
            {
                "id": n.get("id", ""),
                "type": n.get("type", ""),
                "label": n.get("label") or n.get("id", ""),
                "classification": n.get("classification") or n.get("data", {}).get("classification", ""),
            }
            for n in raw_nodes
        ]
        edges = [
            {
                "source": e.get("source") or e.get("from", ""),
                "target": e.get("target") or e.get("to", ""),
                "type": e.get("type") or e.get("label", ""),
                "label": e.get("label", ""),
            }
            for e in raw_edges
        ]
        designs[did] = {"nodes": nodes, "edges": edges}
    return designs
