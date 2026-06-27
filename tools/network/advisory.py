# [TEMPLATE: CUI // SP-CTI]
"""Network Canvas — CVE advisory data access."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from tools.network.db.init_db import get_connection


def list_advisories(
    vendor: str | None = None,
    severity: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 500,
) -> list[dict]:
    conn = get_connection()
    try:
        sql = "SELECT * FROM nc_advisories WHERE 1=1"
        params: list = []
        if vendor:
            sql += " AND vendor = ?"
            params.append(vendor)
        if severity:
            sql += " AND severity = ?"
            params.append(severity)
        if status:
            sql += " AND status = ?"
            params.append(status)
        if date_from:
            sql += " AND published_date >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND published_date <= ?"
            params.append(date_to)
        sql += " ORDER BY published_date DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_vendors() -> list[str]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT DISTINCT vendor FROM nc_advisories WHERE vendor != '' ORDER BY vendor"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def get_advisory(advisory_id: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM nc_advisories WHERE id = %s", (advisory_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_advisory(data: dict) -> dict:
    adv_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO nc_advisories
               (id, cve_id, vendor, severity, published_date, total_devices,
                impacted_devices, remediation_pct, data_source, hitl_status,
                description, remediation_guidance, status, created_at, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                adv_id,
                data.get("cve_id", ""),
                data.get("vendor", ""),
                data.get("severity", "medium"),
                data.get("published_date", now[:10]),
                data.get("total_devices", 0),
                data.get("impacted_devices", 0),
                data.get("remediation_pct", 0.0),
                data.get("data_source", "manual"),
                data.get("hitl_status", "pending"),
                data.get("description", ""),
                data.get("remediation_guidance", ""),
                data.get("status", "open"),
                now,
                now,
            ),
        )
        conn.commit()
        return get_advisory(adv_id)
    finally:
        conn.close()
