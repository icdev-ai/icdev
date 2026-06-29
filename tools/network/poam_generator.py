# [TEMPLATE: CUI // SP-CTI]
"""Network Canvas — POAM (Plan of Action & Milestones) generation and export."""
from __future__ import annotations

import csv
import io
import json

from tools.network.db.init_db import get_connection


def list_poam_items(status: str | None = None, limit: int = 500) -> list[dict]:
    conn = get_connection()
    try:
        sql = "SELECT * FROM nc_poam_items WHERE 1=1"
        params: list = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def generate_poam_item(advisory_id: str, data: dict | None = None) -> dict:
    data = data or {}
    try:
        adv_id_int = int(advisory_id)
    except (TypeError, ValueError):
        adv_id_int = None
    conn = get_connection()
    try:
        adv = None
        if adv_id_int is not None:
            adv = conn.execute(
                "SELECT * FROM nc_advisories WHERE id = %s", (adv_id_int,)
            ).fetchone()
        seq = conn.execute("SELECT COUNT(*) FROM nc_poam_items").fetchone()[0] + 1
        poam_id = f"POAM-{seq:04d}"
        severity = adv["severity"] if adv else data.get("severity", "medium")
        weakness = (adv["title"] if adv else data.get("weakness", "")) or ""
        cur = conn.execute(
            """INSERT INTO nc_poam_items
               (poam_id, advisory_id, weakness_name, severity,
                scheduled_completion, status, responsible_party, resources_required)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                poam_id,
                adv_id_int,
                weakness,
                severity,
                data.get("scheduled_completion") or None,
                "open",
                data.get("responsible_party", ""),
                data.get("resources", ""),
            ),
        )
        conn.commit()
        new_id = cur.lastrowid
        row = conn.execute("SELECT * FROM nc_poam_items WHERE id = %s", (new_id,)).fetchone()
        return dict(row)
    finally:
        conn.close()


def export_poam(fmt: str = "csv") -> tuple[bytes, str]:
    """Return (content_bytes, mimetype)."""
    items = list_poam_items()
    if fmt == "json":
        data = json.dumps(items, indent=2)
        return data.encode(), "application/json"
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "POAM ID", "Weakness", "Severity", "Detection Source",
        "Scheduled Completion", "Status", "Responsible Party",
    ])
    for item in items:
        writer.writerow([
            item.get("poam_id", ""),
            item.get("weakness_name", ""),
            item.get("severity", ""),
            item.get("detection_source", ""),
            item.get("scheduled_completion", ""),
            item.get("status", ""),
            item.get("responsible_party", ""),
        ])
    return buf.getvalue().encode("utf-8"), "text/csv"
