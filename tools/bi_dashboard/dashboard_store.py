# CUI // SP-CTI
"""CRUD for bi_dashboards / bi_generation_log.

Persists dashboards using the exact DashboardSpec.tiles shape
(``[{"spec": <viz spec dict>, "w": <1-12>}, ...]``) so a saved dashboard
round-trips straight into ``tools.viz.spec.DashboardSpec.from_dict``.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from tools.bi_dashboard.db.init_db import get_connection


def create_dashboard(title: str, tiles: list[dict], owner_id: str = "",
                     tenant_id: str = "default", classification: str = "CUI") -> str:
    dashboard_id = str(uuid.uuid4())
    conn = get_connection()
    conn.execute(
        "INSERT INTO bi_dashboards (id, title, owner_id, tiles_json, tenant_id, classification) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (dashboard_id, title, owner_id, json.dumps(tiles), tenant_id, classification),
    )
    conn.commit()
    conn.close()
    return dashboard_id


def get_dashboard(dashboard_id: str) -> dict[str, Any] | None:
    conn = get_connection()
    cur = conn.execute(
        "SELECT id, title, owner_id, tiles_json, created_at, updated_at, tenant_id, classification "
        "FROM bi_dashboards WHERE id = ?",
        (dashboard_id,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    cols = ["id", "title", "owner_id", "tiles_json", "created_at", "updated_at", "tenant_id", "classification"]
    d = dict(zip(cols, row))
    d["tiles"] = json.loads(d.pop("tiles_json") or "[]")
    return d


def list_dashboards(tenant_id: str = "default", limit: int = 100) -> list[dict[str, Any]]:
    conn = get_connection()
    cur = conn.execute(
        "SELECT id, title, owner_id, created_at, updated_at FROM bi_dashboards "
        "WHERE tenant_id = ? ORDER BY updated_at DESC LIMIT ?",
        (tenant_id, limit),
    )
    cols = ["id", "title", "owner_id", "created_at", "updated_at"]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()
    return rows


def update_dashboard_tiles(dashboard_id: str, tiles: list[dict]) -> bool:
    conn = get_connection()
    cur = conn.execute(
        "UPDATE bi_dashboards SET tiles_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (json.dumps(tiles), dashboard_id),
    )
    conn.commit()
    updated = cur.rowcount > 0
    conn.close()
    return updated


def delete_dashboard(dashboard_id: str) -> bool:
    conn = get_connection()
    cur = conn.execute("DELETE FROM bi_dashboards WHERE id = ?", (dashboard_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def log_generation(prompt: str, structure: dict, method: str, dashboard_id: str = "",
                   accepted: bool = True, tenant_id: str = "default",
                   classification: str = "CUI") -> str:
    """Append-only audit record of an AI chart-generation event."""
    log_id = str(uuid.uuid4())
    conn = get_connection()
    conn.execute(
        "INSERT INTO bi_generation_log "
        "(id, dashboard_id, prompt, structure_json, method, accepted, tenant_id, classification) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (log_id, dashboard_id, prompt, json.dumps(structure), method, int(accepted), tenant_id, classification),
    )
    conn.commit()
    conn.close()
    return log_id
