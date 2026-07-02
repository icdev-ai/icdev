# CUI // SP-CTI
"""API key generation, verification, and auth middleware.

Keys are structured as:  ick_<token>
key_prefix is the first 12 characters (shown in UI for identification).
key_hash (SHA-256) is stored; the raw key is shown once at creation time.
"""
from __future__ import annotations

import functools
import hashlib
import secrets
from datetime import datetime, timezone
from typing import Optional, Tuple

from flask import g, jsonify, request

from tools.db.storage import get_connection

_PREFIX = "ick_"
_PREFIX_DISPLAY_LEN = 12  # "ick_" + 8 chars


def _hash(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def generate_api_key(
    tenant_id: str,
    name: str,
    scopes: str = "read",
    *,
    expires_at: Optional[str] = None,
) -> Tuple[str, str, str]:
    """Create a new API key, persist it, and return (key_prefix, raw_key, key_hash).

    The raw_key is shown only once; store key_hash, display key_prefix.
    """
    import uuid

    token = secrets.token_urlsafe(32)
    raw_key = _PREFIX + token
    key_prefix = raw_key[:_PREFIX_DISPLAY_LEN]
    key_hash = _hash(raw_key)
    row_id = str(uuid.uuid4())
    created_at = _now_iso()

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO api_keys
                (id, tenant_id, name, key_prefix, key_hash, scopes, expires_at, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (row_id, tenant_id, name, key_prefix, key_hash, scopes, expires_at, created_at),
        )
        conn.commit()

    return key_prefix, raw_key, key_hash


def verify_api_key(raw_key: str) -> Optional[Tuple[str, str]]:
    """Verify raw_key and return (tenant_id, scopes) or None if invalid/revoked/expired."""
    if not raw_key or not raw_key.startswith(_PREFIX):
        return None

    key_hash = _hash(raw_key)
    now = _now_iso()

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id, tenant_id, scopes, revoked_at, expires_at
            FROM   api_keys
            WHERE  key_hash = %s
            """,
            (key_hash,),
        ).fetchone()

    if not row:
        return None

    row_id, tenant_id, scopes, revoked_at, expires_at = row

    if revoked_at:
        return None
    if expires_at and expires_at < now:
        return None

    # Update last_used_at (mutable field — not append-only violation)
    with get_connection() as conn:
        conn.execute(
            "UPDATE api_keys SET last_used_at = %s WHERE id = %s",
            (now, row_id),
        )
        conn.commit()

    return tenant_id, scopes


def require_api_key(f):
    """Decorator: require a valid Bearer API key in the Authorization header.

    On success sets g.api_tenant_id and g.api_scopes before calling the view.
    """

    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Missing or invalid Authorization header"}), 401
        raw_key = auth_header[len("Bearer "):].strip()
        result = verify_api_key(raw_key)
        if result is None:
            return jsonify({"error": "Invalid, revoked, or expired API key"}), 401
        g.api_tenant_id, g.api_scopes = result
        return f(*args, **kwargs)

    return wrapper
