# CUI // SP-CTI
"""Data Mesh — Domain Manager.

Pure-function CRUD + maturity scoring for dm_domains.
No Flask, no LLM. Uses get_connection() from tools/data_canvas/db/init_db.py.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from tools.data_canvas.constants import (
    DM_DEFAULT_MATURITY_LEVEL,
    DM_MATURITY_LEVEL_MAP,
)
from tools.data_canvas.db.init_db import get_connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_maturity_level(value) -> int:
    """Coerce a maturity value to the INTEGER stored in dm_domains.maturity_level.

    Accepts an int, a numeric string, or a known label ("defined"/"managed"/
    "optimizing"). Anything unrecognised falls back to the default level so a
    bad input can never write a non-numeric value into the INTEGER column.
    """
    if isinstance(value, bool):  # bool is an int subclass — treat as default
        return DM_DEFAULT_MATURITY_LEVEL
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.lstrip("-").isdigit():
            return int(text)
        return DM_MATURITY_LEVEL_MAP.get(text.lower(), DM_DEFAULT_MATURITY_LEVEL)
    return DM_DEFAULT_MATURITY_LEVEL


def list_domains() -> list[dict]:
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM dm_domains ORDER BY name"
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        return [{"error": str(exc)}]


def get_domain(domain_id: str) -> dict | None:
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM dm_domains WHERE id=?", (domain_id,)
            ).fetchone()
        return dict(row) if row else None
    except Exception as exc:
        return {"error": str(exc)}


def create_domain(data: dict) -> dict:
    try:
        domain_id = data.get("id") or str(uuid.uuid4())
        now = _now()
        with get_connection() as conn:
            conn.execute(
                """INSERT INTO dm_domains
                   (id, name, description, owner_team, owner_email,
                    maturity_level, classification, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    domain_id,
                    data.get("name", ""),
                    data.get("description", ""),
                    data.get("owner_team", ""),
                    data.get("owner_email", ""),
                    _coerce_maturity_level(data.get("maturity_level", "defined")),
                    data.get("classification", "CUI // SP-CTI"),
                    now,
                    now,
                ),
            )
            conn.commit()
        return get_domain(domain_id) or {"id": domain_id}
    except Exception as exc:
        return {"error": str(exc)}


def update_domain(domain_id: str, data: dict) -> dict | None:
    try:
        now = _now()
        fields = {k: v for k, v in data.items()
                  if k in ("name", "description", "owner_team", "owner_email",
                           "maturity_level", "classification")}
        if not fields:
            return get_domain(domain_id)
        if "maturity_level" in fields:
            fields["maturity_level"] = _coerce_maturity_level(fields["maturity_level"])
        set_clause = ", ".join(f"{k}=?" for k in fields)
        values = list(fields.values()) + [now, domain_id]
        with get_connection() as conn:
            conn.execute(
                f"UPDATE dm_domains SET {set_clause}, updated_at=? WHERE id=?",
                values,
            )
            conn.commit()
        return get_domain(domain_id)
    except Exception as exc:
        return {"error": str(exc)}


def delete_domain(domain_id: str) -> bool:
    try:
        with get_connection() as conn:
            ref = conn.execute(
                "SELECT COUNT(*) FROM dm_data_products WHERE domain_id=?",
                (domain_id,),
            ).fetchone()[0]
            if ref:
                return False
            conn.execute("DELETE FROM dm_domains WHERE id=?", (domain_id,))
            conn.commit()
        return True
    except Exception:
        return False


def compute_domain_maturity(domain_id: str) -> dict:
    try:
        with get_connection() as conn:
            product_count = conn.execute(
                "SELECT COUNT(*) FROM dm_data_products WHERE domain_id=?",
                (domain_id,),
            ).fetchone()[0]
            contract_count = conn.execute(
                "SELECT COUNT(*) FROM dm_data_contracts WHERE domain_id=?",
                (domain_id,),
            ).fetchone()[0]
            policy_count = conn.execute(
                "SELECT COUNT(*) FROM dm_opa_policies WHERE domain_id=? AND enabled=1",
                (domain_id,),
            ).fetchone()[0]

        score = min(
            product_count * 0.4 + contract_count * 0.3 + policy_count * 0.3,
            1.0,
        )
        if score >= 0.7:
            label = "optimizing"
        elif score >= 0.4:
            label = "managed"
        else:
            label = "defined"

        return {
            "domain_id": domain_id,
            "product_count": product_count,
            "contract_count": contract_count,
            "policy_count": policy_count,
            "maturity_score": round(score, 3),
            "maturity_label": label,
        }
    except Exception as exc:
        return {"error": str(exc)}


def list_domain_products(domain_id: str) -> list[dict]:
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM dm_data_products WHERE domain_id=? ORDER BY name",
                (domain_id,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        return [{"error": str(exc)}]
