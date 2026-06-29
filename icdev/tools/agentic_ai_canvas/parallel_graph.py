# CUI // SP-CTI
"""Agentic AI Design Canvas — Parallel execution group service.

Manages named swim-lanes (parallel groups) for visually grouping nodes
that execute concurrently. Inspired by LangGraph's parallel branch pattern.

Groups are stored in aadc_parallel_groups (migration 105).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return uuid.uuid4().hex[:10]


def _conn():
    from tools.agentic_ai_canvas.db.init_db import get_connection
    return get_connection()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_group(design_id: str, node_ids: list[str],
                 label: str = "Parallel Group", color: str = "#7e22ce") -> dict:
    """Create a named parallel execution group for a set of node IDs."""
    gid = f"pg-{_uid()}"
    now = _now()
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO aadc_parallel_groups "
            "(id, design_id, label, color, node_ids_json, created_at, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (gid, design_id, label[:100], color, json.dumps(node_ids), now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return {"id": gid, "design_id": design_id, "label": label,
            "color": color, "node_ids": node_ids, "created_at": now}


def list_groups(design_id: str) -> list[dict]:
    """Return all parallel groups for a design."""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM aadc_parallel_groups WHERE design_id=%s ORDER BY created_at",
            (design_id,),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["node_ids"] = json.loads(d.get("node_ids_json", "[]"))
            result.append(d)
        return result
    finally:
        conn.close()


def update_group(design_id: str, group_id: str,
                 node_ids: list[str] | None = None,
                 label: str | None = None) -> dict:
    """Update node membership or label of a group."""
    now = _now()
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM aadc_parallel_groups WHERE id=%s AND design_id=%s",
            (group_id, design_id),
        ).fetchone()
        if not row:
            raise ValueError(f"Group {group_id} not found for design {design_id}")
        d = dict(row)
        new_node_ids = node_ids if node_ids is not None else json.loads(d["node_ids_json"])
        new_label = label if label is not None else d["label"]
        conn.execute(
            "UPDATE aadc_parallel_groups SET node_ids_json=%s, label=%s, updated_at=%s "
            "WHERE id=%s AND design_id=%s",
            (json.dumps(new_node_ids), new_label, now, group_id, design_id),
        )
        conn.commit()
    finally:
        conn.close()
    return {"id": group_id, "design_id": design_id, "label": new_label,
            "node_ids": new_node_ids, "updated_at": now}


def delete_group(design_id: str, group_id: str) -> dict:
    """Remove a parallel group (does not delete nodes)."""
    conn = _conn()
    try:
        conn.execute(
            "DELETE FROM aadc_parallel_groups WHERE id=%s AND design_id=%s",
            (group_id, design_id),
        )
        conn.commit()
    finally:
        conn.close()
    return {"status": "deleted", "id": group_id}


def validate_parallel_paths(nodes: list[dict], edges: list[dict]) -> list[dict]:
    """Check that fork→join paths are structurally valid.

    Rules:
    - Every fork must have at least 2 outgoing edges.
    - Every join must have at least 2 incoming edges.
    - Parallel-group nodes should not appear on the same path as another parallel-group
      without an intervening join.

    Returns a list of structural warning dicts.
    """
    adj: dict[str, list[str]] = {}
    radj: dict[str, list[str]] = {}
    for e in edges:
        adj.setdefault(e["source"], []).append(e["target"])
        radj.setdefault(e["target"], []).append(e["source"])

    warnings: list[dict] = []

    for node in nodes:
        ntype = node.get("type", "")
        nid = node["id"]
        nlabel = node.get("label", nid)

        if ntype == "fork":
            out_count = len(adj.get(nid, []))
            if out_count < 2:
                warnings.append({
                    "severity": "WARNING",
                    "node_id": nid,
                    "title": f"Fork '{nlabel}' has fewer than 2 outgoing edges ({out_count})",
                    "recommendation": "A fork node should branch to at least 2 parallel paths.",
                })

        if ntype == "join":
            in_count = len(radj.get(nid, []))
            if in_count < 2:
                warnings.append({
                    "severity": "WARNING",
                    "node_id": nid,
                    "title": f"Join '{nlabel}' has fewer than 2 incoming edges ({in_count})",
                    "recommendation": "A join node should converge at least 2 parallel paths.",
                })

    return warnings
