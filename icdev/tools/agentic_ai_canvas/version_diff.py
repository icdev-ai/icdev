# CUI // SP-CTI
"""Version history and diff utilities for the Agentic AI Design Canvas.

Uses the existing aadc_versions table (created by init_db.py):
    id, design_id, version_number, graph_json, change_summary, created_at
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone


def _conn():
    from tools.agentic_ai_canvas.db.init_db import get_connection
    return get_connection()


def _uid() -> str:
    return uuid.uuid4().hex[:10]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_version(design_id: str, snapshot_json: str | dict, label: str | None = None) -> int:
    """Insert a new version snapshot and return the new version number."""
    if isinstance(snapshot_json, dict):
        snapshot_json = json.dumps(snapshot_json)
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT MAX(version_number) FROM aadc_versions WHERE design_id=%s",
            (design_id,),
        ).fetchone()
        ver = (row[0] or 0) + 1
        conn.execute(
            "INSERT INTO aadc_versions "
            "(id, design_id, version_number, graph_json, change_summary, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (f"v-{_uid()}", design_id, ver, snapshot_json, label or "", _now()),
        )
        conn.commit()
        return ver
    finally:
        conn.close()


def list_versions(design_id: str) -> list[dict]:
    """Return all versions for a design, newest first."""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT id, design_id, version_number, change_summary, created_at "
            "FROM aadc_versions WHERE design_id=%s ORDER BY version_number DESC",
            (design_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def diff_versions(design_id: str, v1: int, v2: int) -> dict:
    """Compare two version snapshots.

    Returns {added_nodes, removed_nodes, changed_nodes, added_edges, removed_edges}
    where each item is a list of {id, label}.
    """
    conn = _conn()
    try:
        def _fetch(ver: int) -> dict | None:
            row = conn.execute(
                "SELECT graph_json FROM aadc_versions "
                "WHERE design_id=%s AND version_number=%s",
                (design_id, ver),
            ).fetchone()
            if row is None:
                return None
            return json.loads(row[0] or '{"nodes":[],"edges":[]}')

        g1 = _fetch(v1)
        g2 = _fetch(v2)
    finally:
        conn.close()

    if g1 is None:
        raise ValueError(f"Version {v1} not found for design {design_id}")
    if g2 is None:
        raise ValueError(f"Version {v2} not found for design {design_id}")

    nodes1 = {n["id"]: n for n in g1.get("nodes", [])}
    nodes2 = {n["id"]: n for n in g2.get("nodes", [])}
    edges1 = {e["id"]: e for e in g1.get("edges", [])}
    edges2 = {e["id"]: e for e in g2.get("edges", [])}

    return {
        "design_id": design_id,
        "v1": v1,
        "v2": v2,
        "added_nodes": [
            {"id": nid, "label": n.get("label", "")}
            for nid, n in nodes2.items() if nid not in nodes1
        ],
        "removed_nodes": [
            {"id": nid, "label": n.get("label", "")}
            for nid, n in nodes1.items() if nid not in nodes2
        ],
        "changed_nodes": [
            {"id": nid, "label": nodes2[nid].get("label", "")}
            for nid in nodes1 if nid in nodes2 and nodes1[nid] != nodes2[nid]
        ],
        "added_edges": [
            {"id": eid, "label": e.get("label", "")}
            for eid, e in edges2.items() if eid not in edges1
        ],
        "removed_edges": [
            {"id": eid, "label": e.get("label", "")}
            for eid, e in edges1.items() if eid not in edges2
        ],
    }
