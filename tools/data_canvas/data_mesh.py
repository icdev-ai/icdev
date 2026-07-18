# CUI // SP-CTI
"""Data Mesh canvas module — domain management, data products, ODCS contracts, and federated governance.

Provides CRUD operations for dm_* tables: domains, data products, input/output ports,
data contracts, domain maturity scoring, governance policies, and catalog entries.
All writes are audit-trailed via dm_audit (append-only, NIST AU-6).
"""

import uuid
from datetime import datetime, timezone

from tools.data_canvas.db.init_db import get_connection
from tools.data_canvas.constants import DM_DOMAIN_MATURITY_LEVELS


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return str(uuid.uuid4())


def _audit(conn, action: str, domain_id: str = "", product_id: str = "", detail: str = "", user: str = "system"):
    conn.execute(
        'INSERT INTO dm_audit (domain_id, product_id, "user", action, detail) VALUES (?, ?, ?, ?, ?)',
        (domain_id, product_id, user, action, detail),
    )


# ── Domains ───────────────────────────────────────────────────────────────────

def create_domain(name: str, **kwargs) -> dict:
    domain_id = _uid()
    now = _now()
    row = {
        "id": domain_id,
        "name": name,
        "description": kwargs.get("description", ""),
        "owner": kwargs.get("owner", ""),
        "steward": kwargs.get("steward", ""),
        "bounded_context": kwargs.get("bounded_context", ""),
        "maturity_level": kwargs.get("maturity_level", 0),
        "classification": kwargs.get("classification", "CUI // SP-CTI"),
        "status": kwargs.get("status", "active"),
        "created_at": now,
        "updated_at": now,
    }
    # Positional ? + ordered tuple: get_connection() is HYBRID (raw sqlite3 on the
    # SQLite branch — no translate wrapper; StorageConnection on PG). translate_sql
    # only rewrites ?→%s (never :name), and StorageCursor wraps a dict param into a
    # 1-tuple, so a :name mapping never reaches either driver. ? is the only portable
    # form. Column order MUST match _DM_DOMAIN_COLS.
    _DM_DOMAIN_COLS = (
        "id", "name", "description", "owner", "steward", "bounded_context",
        "maturity_level", "classification", "status", "created_at", "updated_at",
    )
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO dm_domains (id, name, description, owner, steward, bounded_context,
               maturity_level, classification, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            tuple(row[c] for c in _DM_DOMAIN_COLS),
        )
        _audit(conn, "domain.create", domain_id=domain_id, detail=name, user=kwargs.get("user", "system"))
        conn.commit()
    return row


def list_domains(status: str = "active") -> list:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM dm_domains WHERE status = ? ORDER BY name", (status,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_domain(domain_id: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM dm_domains WHERE id = ?", (domain_id,)).fetchone()
    return dict(row) if row else None


# ── Data Products ─────────────────────────────────────────────────────────────

def create_data_product(domain_id: str, name: str, **kwargs) -> dict:
    product_id = _uid()
    now = _now()
    row = {
        "id": product_id,
        "domain_id": domain_id,
        "name": name,
        "description": kwargs.get("description", ""),
        "owner": kwargs.get("owner", ""),
        "version": kwargs.get("version", "1.0.0"),
        "availability_sla": kwargs.get("availability_sla", 99.9),
        "latency_sla_ms": kwargs.get("latency_sla_ms", 500),
        "status": kwargs.get("status", "active"),
        "classification": kwargs.get("classification", "CUI // SP-CTI"),
        "created_at": now,
        "updated_at": now,
    }
    # Positional ? + ordered tuple (hybrid connection — see create_domain).
    _DM_PRODUCT_COLS = (
        "id", "domain_id", "name", "description", "owner", "version",
        "availability_sla", "latency_sla_ms", "status", "classification",
        "created_at", "updated_at",
    )
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO dm_data_products (id, domain_id, name, description, owner, version,
               availability_sla, latency_sla_ms, status, classification, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            tuple(row[c] for c in _DM_PRODUCT_COLS),
        )
        _audit(conn, "product.create", domain_id=domain_id, product_id=product_id, detail=name, user=kwargs.get("user", "system"))
        conn.commit()
    return row


def list_products(domain_id: str | None = None) -> list:
    with get_connection() as conn:
        if domain_id:
            rows = conn.execute(
                "SELECT * FROM dm_data_products WHERE domain_id = ? ORDER BY name", (domain_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM dm_data_products ORDER BY name").fetchall()
    return [dict(r) for r in rows]


# ── Domain Maturity ───────────────────────────────────────────────────────────

def assess_domain_maturity(domain_id: str, maturity_level: int, scores: dict | None = None, **kwargs) -> dict:
    import json
    entry_id = _uid()
    row = {
        "id": entry_id,
        "domain_id": domain_id,
        "maturity_level": maturity_level,
        "scores_json": json.dumps(scores or {}),
        "assessed_by": kwargs.get("assessed_by", "system"),
        "notes": kwargs.get("notes", ""),
        "classification": kwargs.get("classification", "CUI // SP-CTI"),
        "created_at": _now(),
    }
    level_label = next((l["label"] for l in DM_DOMAIN_MATURITY_LEVELS if l["level"] == maturity_level), str(maturity_level))
    # Positional ? + ordered tuple (hybrid connection — see create_domain).
    _DM_MATURITY_COLS = (
        "id", "domain_id", "maturity_level", "scores_json", "assessed_by",
        "notes", "classification", "created_at",
    )
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO dm_domain_maturity (id, domain_id, maturity_level, scores_json, assessed_by, notes, classification, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            tuple(row[c] for c in _DM_MATURITY_COLS),
        )
        conn.execute(
            "UPDATE dm_domains SET maturity_level = ?, updated_at = ? WHERE id = ?",
            (maturity_level, _now(), domain_id),
        )
        _audit(conn, "domain.maturity", domain_id=domain_id, detail=f"level={maturity_level} ({level_label})", user=kwargs.get("user", "system"))
        conn.commit()
    return row
