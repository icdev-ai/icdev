# CUI // SP-CTI
"""Agentic AI Design Canvas — Checkpoint/fork service.

Inspired by LangGraph's checkpoint/fork pattern:
  - checkpoint: snapshot the full graph state at a point in time
  - restore: roll the live design back to a checkpoint
  - fork: create a new independent design branched from a checkpoint

All state is persisted in aadc_checkpoints (migration 105).
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

def save_checkpoint(design_id: str, graph_json: str | dict,
                    label: str = "", node_id: str = "") -> dict:
    """Snapshot the current graph state as a named checkpoint."""
    if isinstance(graph_json, dict):
        graph_json = json.dumps(graph_json)

    cid = f"ckpt-{_uid()}"
    now = _now()
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO aadc_checkpoints (id, design_id, node_id, label, graph_json, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (cid, design_id, node_id, label or f"Checkpoint {now[:10]}", graph_json, now),
        )
        conn.commit()
    finally:
        conn.close()
    return {"id": cid, "design_id": design_id, "label": label, "created_at": now}


def list_checkpoints(design_id: str) -> list[dict]:
    """Return all checkpoints for a design, newest first."""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT id, design_id, node_id, label, created_at FROM aadc_checkpoints "
            "WHERE design_id=? ORDER BY created_at DESC",
            (design_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_checkpoint(checkpoint_id: str) -> dict | None:
    """Return a checkpoint row including graph_json, or None if not found."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM aadc_checkpoints WHERE id=?", (checkpoint_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def restore_checkpoint(design_id: str, checkpoint_id: str) -> dict:
    """Overwrite the live design graph with the checkpoint snapshot."""
    ckpt = get_checkpoint(checkpoint_id)
    if not ckpt or ckpt["design_id"] != design_id:
        raise ValueError(f"Checkpoint {checkpoint_id} not found for design {design_id}")

    now = _now()
    conn = _conn()
    try:
        conn.execute(
            "UPDATE aadc_designs SET graph_json=?, updated_at=? WHERE id=?",
            (ckpt["graph_json"], now, design_id),
        )
        # Record restore as a new version snapshot
        ver = (conn.execute(
            "SELECT MAX(version_number) FROM aadc_versions WHERE design_id=?", (design_id,)
        ).fetchone()[0] or 0) + 1
        conn.execute(
            "INSERT INTO aadc_versions (id, design_id, version_number, graph_json, created_at) "
            "VALUES (?,?,?,?,?)",
            (f"v-{_uid()}", design_id, ver, ckpt["graph_json"], now),
        )
        conn.commit()
    finally:
        conn.close()

    return {"status": "restored", "checkpoint_id": checkpoint_id,
            "design_id": design_id, "version_number": ver}


def fork_design(design_id: str, checkpoint_id: str, new_name: str) -> dict:
    """Create a new independent design branched from the given checkpoint.

    Returns the new design id and a redirect URL.
    """
    ckpt = get_checkpoint(checkpoint_id)
    if not ckpt or ckpt["design_id"] != design_id:
        raise ValueError(f"Checkpoint {checkpoint_id} not found for design {design_id}")

    new_id = f"aadc-{_uid()}"
    now = _now()
    conn = _conn()
    try:
        # Copy the parent design metadata
        parent = conn.execute(
            "SELECT domain, classification FROM aadc_designs WHERE id=?", (design_id,)
        ).fetchone()
        domain = parent["domain"] if parent else ""
        classification = parent["classification"] if parent else "CUI"

        conn.execute(
            "INSERT INTO aadc_designs "
            "(id, name, description, domain, classification, graph_json, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (new_id, new_name,
             f"Forked from {design_id} at checkpoint {checkpoint_id[:8]}",
             domain, classification, ckpt["graph_json"], now, now),
        )
        # Seed initial version
        conn.execute(
            "INSERT INTO aadc_versions (id, design_id, version_number, graph_json, created_at) "
            "VALUES (?,?,?,?,?)",
            (f"v-{_uid()}", new_id, 1, ckpt["graph_json"], now),
        )
        conn.commit()
    finally:
        conn.close()

    return {"status": "forked", "new_design_id": new_id, "new_name": new_name,
            "source_checkpoint": checkpoint_id,
            "redirect": f"/agentic-ai/canvas/{new_id}"}


def delete_checkpoint(design_id: str, checkpoint_id: str) -> dict:
    """Delete a checkpoint."""
    conn = _conn()
    try:
        conn.execute(
            "DELETE FROM aadc_checkpoints WHERE id=? AND design_id=?",
            (checkpoint_id, design_id),
        )
        conn.commit()
    finally:
        conn.close()
    return {"status": "deleted", "id": checkpoint_id}
