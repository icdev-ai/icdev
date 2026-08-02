# CUI // SP-CTI
"""Data Mesh — Product Registry.

Pure-function CRUD + SLA + subscriptions + discoverability scoring.
No Flask, no LLM. Uses get_connection() from tools/data_canvas/db/init_db.py.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from tools.data_canvas.constants import DM_PRODUCT_STATUS
from tools.data_canvas.db.init_db import get_connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def list_products(domain_id: str | None = None, status: str | None = None) -> list[dict]:
    try:
        with get_connection() as conn:
            clauses, params = [], []
            if domain_id:
                clauses.append("domain_id=?")
                params.append(domain_id)
            if status:
                clauses.append("status=?")
                params.append(status)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            rows = conn.execute(
                f"SELECT * FROM dm_data_products {where} ORDER BY name", params
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        return [{"error": str(exc)}]


def get_product(product_id: str) -> dict | None:
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM dm_data_products WHERE id=?", (product_id,)
            ).fetchone()
        return dict(row) if row else None
    except Exception as exc:
        return {"error": str(exc)}


def create_product(data: dict) -> dict:
    try:
        product_id = data.get("id") or str(uuid.uuid4())
        status = data.get("status", "draft")
        if status not in DM_PRODUCT_STATUS:
            status = "draft"
        now = _now()
        with get_connection() as conn:
            conn.execute(
                """INSERT INTO dm_data_products
                   (id, domain_id, name, description, status, output_port_type,
                    sla_tier, owner_team, classification, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    product_id,
                    data.get("domain_id", ""),
                    data.get("name", ""),
                    data.get("description", ""),
                    status,
                    data.get("output_port_type", "table"),
                    data.get("sla_tier", "standard"),
                    data.get("owner_team", ""),
                    data.get("classification", "CUI // SP-CTI"),
                    now,
                    now,
                ),
            )
            conn.commit()
        return get_product(product_id) or {"id": product_id}
    except Exception as exc:
        return {"error": str(exc)}


def update_product(product_id: str, data: dict) -> dict | None:
    try:
        now = _now()
        fields = {k: v for k, v in data.items()
                  if k in ("name", "description", "status", "output_port_type",
                           "sla_tier", "owner_team", "classification", "domain_id")}
        if not fields:
            return get_product(product_id)
        set_clause = ", ".join(f"{k}=?" for k in fields)
        values = list(fields.values()) + [now, product_id]
        with get_connection() as conn:
            conn.execute(
                f"UPDATE dm_data_products SET {set_clause}, updated_at=? WHERE id=?",
                values,
            )
            conn.commit()
        return get_product(product_id)
    except Exception as exc:
        return {"error": str(exc)}


def delete_product(product_id: str) -> bool:
    try:
        with get_connection() as conn:
            conn.execute("DELETE FROM dm_data_products WHERE id=?", (product_id,))
            conn.commit()
        return True
    except Exception:
        return False


def get_product_slas(product_id: str) -> list[dict]:
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM dm_product_slas WHERE product_id=? ORDER BY sla_type",
                (product_id,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        return [{"error": str(exc)}]


def add_product_sla(product_id: str, sla_type: str, target: float, unit: str) -> dict:
    try:
        sla_id = str(uuid.uuid4())
        with get_connection() as conn:
            conn.execute(
                """INSERT INTO dm_product_slas (id, product_id, sla_type, target_value, unit, created_at)
                   VALUES (?,?,?,?,?,?)""",
                (sla_id, product_id, sla_type, target, unit, _now()),
            )
            conn.commit()
        return {"id": sla_id, "product_id": product_id, "sla_type": sla_type,
                "target_value": target, "unit": unit}
    except Exception as exc:
        return {"error": str(exc)}


def subscribe_to_product(product_id: str, subscriber: dict) -> dict:
    try:
        sub_id = str(uuid.uuid4())
        with get_connection() as conn:
            # subscriber_team/approved are not columns (swp-scan-01): the live
            # shape is subscriber plus a status string, so the boolean
            # approved=0 becomes status='pending'.
            conn.execute(
                """INSERT INTO dm_product_subscriptions
                   (id, product_id, subscriber, purpose, status, created_at)
                   VALUES (?,?,?,?,'pending',?)""",
                (
                    sub_id,
                    product_id,
                    subscriber.get("subscriber_team", ""),
                    subscriber.get("purpose", ""),
                    _now(),
                ),
            )
            conn.commit()
        return {"id": sub_id, "product_id": product_id, "approved": False}
    except Exception as exc:
        return {"error": str(exc)}


def approve_subscription(sub_id: str) -> bool:
    try:
        with get_connection() as conn:
            conn.execute(
                "UPDATE dm_product_subscriptions SET approved=1 WHERE id=?", (sub_id,)
            )
            conn.commit()
        return True
    except Exception:
        return False


def compute_discoverability_score(product_id: str) -> dict:
    try:
        product = get_product(product_id)
        if not product or "error" in product:
            return {"error": "product not found", "score": 0}

        with get_connection() as conn:
            has_slas = bool(conn.execute(
                "SELECT COUNT(*) FROM dm_product_slas WHERE product_id=?",
                (product_id,),
            ).fetchone()[0])
            has_contract = bool(conn.execute(
                "SELECT COUNT(*) FROM dm_data_contracts WHERE product_id=? AND status='active'",
                (product_id,),
            ).fetchone()[0])
            has_lineage = bool(conn.execute(
                "SELECT COUNT(*) FROM dd_lineage WHERE design_id=?",
                (product.get("domain_id", ""),),
            ).fetchone()[0])
            seven_days_ago = datetime.now(timezone.utc).isoformat()[:10]
            has_quality = bool(conn.execute(
                """SELECT COUNT(*) FROM dd_quality_runs
                   WHERE passed=1 AND created_at >= ?""",
                (seven_days_ago,),
            ).fetchone()[0])

        dimensions = {
            "has_description": bool((product.get("description") or "").strip()),
            "has_slas": has_slas,
            "has_contract": has_contract,
            "has_lineage": has_lineage,
            "has_quality": has_quality,
        }
        score = sum(20 for v in dimensions.values() if v)
        if score >= 80:
            label = "Trusted"
        elif score >= 60:
            label = "Discoverable"
        elif score >= 40:
            label = "Emerging"
        else:
            label = "Undiscoverable"

        return {"product_id": product_id, "score": score,
                "dimensions": dimensions, "label": label}
    except Exception as exc:
        return {"error": str(exc), "score": 0}
