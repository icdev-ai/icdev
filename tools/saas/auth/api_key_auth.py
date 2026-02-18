#!/usr/bin/env python3
"""ICDEV SaaS — API Key Authentication.
CUI // SP-CTI
"""
import hashlib
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

logger = logging.getLogger("saas.auth.api_key")

PLATFORM_DB_PATH = Path(os.environ.get("PLATFORM_DB_PATH", str(BASE_DIR / "data" / "platform.db")))


def _get_platform_conn():
    """Get platform DB connection."""
    conn = sqlite3.connect(str(PLATFORM_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _hash_key(key: str) -> str:
    """SHA-256 hash of the API key."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def validate_api_key(key: str) -> Optional[dict]:
    """Validate an API key and return user/tenant info if valid.

    Returns dict with: tenant_id, user_id, role, scopes, tenant_status, tenant_tier
    Returns None if invalid.
    """
    if not key or not key.startswith("icdev_"):
        return None

    key_hash = _hash_key(key)

    try:
        conn = _get_platform_conn()
        row = conn.execute("""
            SELECT k.id as key_id, k.tenant_id, k.user_id, k.scopes, k.status as key_status,
                   k.expires_at,
                   u.role, u.status as user_status, u.email,
                   t.status as tenant_status, t.tier as tenant_tier,
                   t.impact_level, t.slug as tenant_slug
            FROM api_keys k
            JOIN users u ON k.user_id = u.id AND k.tenant_id = u.tenant_id
            JOIN tenants t ON k.tenant_id = t.id
            WHERE k.key_hash = ?
        """, (key_hash,)).fetchone()

        if not row:
            logger.warning("API key not found: prefix=%s", key[:12])
            return None

        row = dict(row)

        # Check key status
        if row["key_status"] != "active":
            logger.warning("API key %s is %s", key[:12], row["key_status"])
            return None

        # Check expiry
        if row["expires_at"]:
            expires = datetime.fromisoformat(row["expires_at"])
            if expires < datetime.now(timezone.utc):
                logger.warning("API key %s expired at %s", key[:12], row["expires_at"])
                return None

        # Check user status
        if row["user_status"] != "active":
            logger.warning("User %s is %s", row["user_id"], row["user_status"])
            return None

        # Check tenant status
        if row["tenant_status"] != "active":
            logger.warning("Tenant %s is %s", row["tenant_id"], row["tenant_status"])
            return None

        # Update last_used_at
        try:
            conn.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?",
                        (datetime.now(timezone.utc).isoformat(), row["key_id"]))
            conn.commit()
        except Exception:
            pass  # Non-critical

        conn.close()

        # Parse scopes
        scopes = []
        if row["scopes"]:
            try:
                import json
                scopes = json.loads(row["scopes"]) if isinstance(row["scopes"], str) else row["scopes"]
            except Exception:
                scopes = []

        return {
            "tenant_id": row["tenant_id"],
            "user_id": row["user_id"],
            "email": row["email"],
            "role": row["role"],
            "scopes": scopes,
            "tenant_status": row["tenant_status"],
            "tenant_tier": row["tenant_tier"],
            "impact_level": row["impact_level"],
            "tenant_slug": row["tenant_slug"],
            "auth_method": "api_key",
        }
    except Exception as e:
        logger.error("API key validation error: %s", e)
        return None
