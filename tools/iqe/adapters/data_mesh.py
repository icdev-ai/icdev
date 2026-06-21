# CUI // SP-CTI
"""IQE data mesh collection adapters.

Importing this module registers four collections on the module-level Executor:
  data_mesh.domains             — dm_domains rows
  data_mesh.products            — dm_data_products rows
  data_mesh.contracts           — dm_contracts rows
  data_mesh.governance_policies — dm_governance_policies rows
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def _canvas_conn() -> Any:
    from tools.db.storage import get_canvas_connection  # noqa: PLC0415
    return get_canvas_connection("DATA_CANVAS_DB_PATH")


def domains_adapter(conn: Any) -> list[dict]:
    if conn is None:
        conn = _canvas_conn()
    cur = conn.execute(
        "SELECT id, name, owner, owner_team, status, maturity_level, "
        "classification, created_at FROM dm_domains ORDER BY name"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def products_adapter(conn: Any) -> list[dict]:
    if conn is None:
        conn = _canvas_conn()
    cur = conn.execute(
        "SELECT id, name, domain_id, status, sla_tier, output_port_type, "
        "discoverability_score, classification, created_at "
        "FROM dm_data_products ORDER BY name"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def contracts_adapter(conn: Any) -> list[dict]:
    if conn is None:
        conn = _canvas_conn()
    cur = conn.execute(
        "SELECT id, title, product_id, version, status, schema_type, "
        "classification, created_at FROM dm_contracts ORDER BY created_at DESC"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def governance_policies_adapter(conn: Any) -> list[dict]:
    if conn is None:
        conn = _canvas_conn()
    cur = conn.execute(
        "SELECT id, name, policy_type, applies_to, status, rules_json, "
        "created_at FROM dm_governance_policies ORDER BY name"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


register_collection("data_mesh.domains", domains_adapter)
register_collection("data_mesh.products", products_adapter)
register_collection("data_mesh.contracts", contracts_adapter)
register_collection("data_mesh.governance_policies", governance_policies_adapter)
