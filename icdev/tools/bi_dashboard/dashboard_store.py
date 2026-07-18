# CUI // SP-CTI
"""CRUD for bi_dashboards / bi_generation_log.

Persists dashboards using the exact DashboardSpec.tiles shape
(``[{"spec": <viz spec dict>, "w": <1-12>}, ...]``) so a saved dashboard
round-trips straight into ``tools.viz.spec.DashboardSpec.from_dict``.

Owner/tenant scoping (cnr-bi-01/02): every read/mutation accepts a
``tenant_id`` so the ``bi_*`` RLS columns actually separate tenants, and
reads/lists can additionally be scoped to an ``owner_id``. Callers (the
blueprint) derive both from the authenticated session — this module never
invents a tenant. When ``tenant_id`` is ``None`` the filter is omitted
(back-compat / admin/system reads); the blueprint always passes a concrete
tenant for user-facing routes.
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
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (dashboard_id, title, owner_id, json.dumps(tiles), tenant_id, classification),
    )
    conn.commit()
    conn.close()
    return dashboard_id


def get_dashboard(dashboard_id: str, tenant_id: str | None = None) -> dict[str, Any] | None:
    conn = get_connection()
    sql = (
        "SELECT id, title, owner_id, tiles_json, created_at, updated_at, tenant_id, classification "
        "FROM bi_dashboards WHERE id = %s"
    )
    params: list[Any] = [dashboard_id]
    if tenant_id is not None:
        sql += " AND tenant_id = %s"
        params.append(tenant_id)
    cur = conn.execute(sql, tuple(params))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    cols = ["id", "title", "owner_id", "tiles_json", "created_at", "updated_at", "tenant_id", "classification"]
    d = dict(zip(cols, row))
    d["tiles"] = json.loads(d.pop("tiles_json") or "[]")
    return d


def list_dashboards(tenant_id: str = "default", owner_id: str | None = None,
                    limit: int = 100) -> list[dict[str, Any]]:
    """List dashboards for a tenant.

    When ``owner_id`` is provided, results are scoped to that owner so a user
    only sees their own dashboards (admins/system callers pass ``owner_id=None``
    to see every dashboard in the tenant).
    """
    conn = get_connection()
    sql = (
        "SELECT id, title, owner_id, created_at, updated_at FROM bi_dashboards "
        "WHERE tenant_id = %s"
    )
    params: list[Any] = [tenant_id]
    if owner_id is not None:
        sql += " AND owner_id = %s"
        params.append(owner_id)
    sql += " ORDER BY updated_at DESC LIMIT %s"
    params.append(limit)
    cur = conn.execute(sql, tuple(params))
    cols = ["id", "title", "owner_id", "created_at", "updated_at"]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()
    return rows


def update_dashboard_tiles(dashboard_id: str, tiles: list[dict],
                           tenant_id: str | None = None) -> bool:
    conn = get_connection()
    sql = "UPDATE bi_dashboards SET tiles_json = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s"
    params: list[Any] = [json.dumps(tiles), dashboard_id]
    if tenant_id is not None:
        sql += " AND tenant_id = %s"
        params.append(tenant_id)
    cur = conn.execute(sql, tuple(params))
    conn.commit()
    updated = cur.rowcount > 0
    conn.close()
    return updated


def delete_dashboard(dashboard_id: str, tenant_id: str | None = None) -> bool:
    conn = get_connection()
    sql = "DELETE FROM bi_dashboards WHERE id = %s"
    params: list[Any] = [dashboard_id]
    if tenant_id is not None:
        sql += " AND tenant_id = %s"
        params.append(tenant_id)
    cur = conn.execute(sql, tuple(params))
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
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (log_id, dashboard_id, prompt, json.dumps(structure), method, int(accepted), tenant_id, classification),
    )
    conn.commit()
    conn.close()
    return log_id
