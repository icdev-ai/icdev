#!/usr/bin/env python3
"""ICDEV SaaS — CAC/PIV Client Certificate Authentication.
CUI // SP-CTI

In production, nginx or ALB terminates mutual TLS and passes:
  X-Client-Cert-CN: "LAST.FIRST.MIDDLE.EDIPI"
  X-Client-Cert-Serial: "serial_number"
"""
import logging
import os
import sqlite3
import sys
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

logger = logging.getLogger("saas.auth.cac")

PLATFORM_DB_PATH = Path(os.environ.get("PLATFORM_DB_PATH", str(BASE_DIR / "data" / "platform.db")))


def _get_platform_conn():
    conn = sqlite3.connect(str(PLATFORM_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def validate_cac_cert(client_cn: str, client_serial: Optional[str] = None) -> Optional[dict]:
    """Validate a CAC/PIV certificate by Common Name lookup.

    The CN is extracted from the client certificate by the TLS terminator
    (nginx/ALB) and passed via X-Client-Cert-CN header.

    CAC CN format: "LAST.FIRST.MIDDLE.EDIPI" (DoD standard)

    Returns dict with: tenant_id, user_id, role, auth_method="cac_piv"
    Returns None if invalid.
    """
    if not client_cn:
        return None

    client_cn = client_cn.strip()

    try:
        conn = _get_platform_conn()
        row = conn.execute("""
            SELECT u.id as user_id, u.tenant_id, u.email, u.role, u.status as user_status,
                   t.status as tenant_status, t.tier as tenant_tier,
                   t.impact_level, t.slug as tenant_slug
            FROM users u
            JOIN tenants t ON u.tenant_id = t.id
            WHERE u.cac_cn = ? AND u.auth_method = 'cac_piv'
                  AND u.status = 'active' AND t.status = 'active'
        """, (client_cn,)).fetchone()
        conn.close()

        if not row:
            logger.warning("No active user found for CAC CN: %s", client_cn[:20])
            return None

        row = dict(row)

        return {
            "tenant_id": row["tenant_id"],
            "user_id": row["user_id"],
            "email": row["email"],
            "role": row["role"],
            "scopes": [],
            "tenant_status": row["tenant_status"],
            "tenant_tier": row["tenant_tier"],
            "impact_level": row["impact_level"],
            "tenant_slug": row["tenant_slug"],
            "auth_method": "cac_piv",
        }
    except Exception as e:
        logger.error("CAC validation error: %s", e)
        return None
