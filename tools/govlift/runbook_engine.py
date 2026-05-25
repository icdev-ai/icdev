# CUI // SP-CTI
"""GovLift — Runbook Engine.

Provides CRUD operations for govlift_runbook_templates.
All DB access via get_connection() — never sqlite3.connect().
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

_ICDEV_ROOT = Path(__file__).resolve().parents[2]
if str(_ICDEV_ROOT) not in sys.path:
    sys.path.insert(0, str(_ICDEV_ROOT))

from tools.db.storage import get_connection, translate_sql
from tools.govlift.constants import WORKLOAD_TYPES, RUNBOOK_CATEGORIES


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rbt_id() -> str:
    return "rbt-" + uuid4().hex[:10]


def _row_to_dict(row) -> dict:
    if row is None:
        return {}
    try:
        return dict(row)
    except Exception:
        return {}


def create_template(
    name: str,
    category: str,
    steps: list[dict],
    description: str = "",
    workload_type: str = "web_app",
    author: str = "",
) -> dict:
    """Insert a new runbook template and return the created record."""
    if category not in RUNBOOK_CATEGORIES:
        raise ValueError(f"Invalid category '{category}'. Valid: {RUNBOOK_CATEGORIES}")
    if workload_type not in WORKLOAD_TYPES:
        raise ValueError(f"Invalid workload_type '{workload_type}'. Valid: {WORKLOAD_TYPES}")

    rbt_id = _rbt_id()
    now = _now()
    steps_json = json.dumps(steps)
    conn = get_connection()
    try:
        sql = translate_sql(
            "INSERT INTO govlift_runbook_templates "
            "(id, name, category, description, workload_type, author, steps_json, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)"
        )
        conn.execute(sql, (rbt_id, name, category, description, workload_type, author, steps_json, now))
        conn.commit()
        return get_template(rbt_id)
    finally:
        conn.close()


def get_template(template_id: str) -> dict:
    """Return a runbook template by ID, or {} if not found."""
    conn = get_connection()
    try:
        sql = translate_sql("SELECT * FROM govlift_runbook_templates WHERE id = ?")
        row = conn.execute(sql, (template_id,)).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def list_templates(category: str | None = None, author: str | None = None) -> list[dict]:
    """Return templates filtered by optional category or author."""
    conn = get_connection()
    try:
        clauses: list[str] = []
        params: list = []
        if category:
            clauses.append("category = ?")
            params.append(category)
        if author:
            clauses.append("author = ?")
            params.append(author)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = translate_sql(
            f"SELECT * FROM govlift_runbook_templates {where} ORDER BY created_at DESC"
        )
        rows = conn.execute(sql, params).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()
