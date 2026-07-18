# CUI // SP-CTI
"""Integration token storage helpers — encrypt/decrypt using Fernet + ICDEV_SECRET_KEY."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone

from tools.db.storage import get_canvas_connection, sql_placeholder
from tools.logging.icdev_logger import get_logger
from tools.second_brain.constants import BRIEFING_ENV_FLAG

logger = get_logger(__name__)
_ENV_FLAG = BRIEFING_ENV_FLAG


def _fernet():
    """Return a Fernet cipher keyed from ICDEV_SECRET_KEY (SHA-256 → 32 bytes → urlsafe-b64)."""
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        raise RuntimeError("cryptography package required for token encryption: pip install cryptography")
    secret = os.environ.get("ICDEV_SECRET_KEY", "icdev-default-dev-key-change-in-prod")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def _encrypt(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode()).decode()


def _decrypt(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except Exception:
        return ""


def _conn():
    return get_canvas_connection(_ENV_FLAG)


def save_integration(
    user_id: str,
    service: str,
    access_token: str = "",
    refresh_token: str = "",
    token_expiry: str = "",
    scopes: str = "",
    metadata: dict | None = None,
    tenant_id: str = "default",
) -> str:
    """Upsert an integration record (tokens encrypted). Returns the row id."""
    iid = f"int-{uuid.uuid4().hex[:12]}"
    with _conn() as conn:
        ph = sql_placeholder(conn)
        conn.execute(
            f"""
            INSERT INTO user_integrations
                (id, user_id, tenant_id, service, access_token_enc, refresh_token_enc,
                 token_expiry, scopes, metadata_json, status)
            VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},'active')
            ON CONFLICT(user_id, tenant_id, service) DO UPDATE SET
                access_token_enc = excluded.access_token_enc,
                refresh_token_enc = excluded.refresh_token_enc,
                token_expiry = excluded.token_expiry,
                scopes = excluded.scopes,
                metadata_json = excluded.metadata_json,
                status = 'active',
                last_sync_at = {ph}
            """,
            (iid, user_id, tenant_id, service,
             _encrypt(access_token), _encrypt(refresh_token),
             token_expiry, scopes, json.dumps(metadata or {}),
             datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        conn.commit()
    return iid


def get_decrypted_token(user_id: str, service: str, tenant_id: str = "default") -> str | None:
    """Return the decrypted access token for *user_id* + *service*, or None."""
    with _conn() as conn:
        ph = sql_placeholder(conn)
        row = conn.execute(
            f"SELECT access_token_enc FROM user_integrations WHERE user_id={ph} AND tenant_id={ph} AND service={ph} AND status='active'",
            (user_id, tenant_id, service),
        ).fetchone()
    if row is None:
        return None
    val = row[0] if not isinstance(row, dict) else row.get("access_token_enc", "")
    return _decrypt(val) or None


def get_integration_tokens(user_id: str, service: str, tenant_id: str = "default") -> dict:
    """Return decrypted tokens + expiry + metadata for an active integration.

    Uses the canvas connection (same DB every other integration read/write uses),
    fixing the msgraph split-brain where token IO went to the main RLS DB while
    everything else used the canvas DB (cnr-me-03). Returns {} if not found.
    """
    with _conn() as conn:
        ph = sql_placeholder(conn)
        row = conn.execute(
            f"SELECT access_token_enc, refresh_token_enc, token_expiry, metadata_json "
            f"FROM user_integrations WHERE user_id={ph} AND tenant_id={ph} AND service={ph} AND status='active'",
            (user_id, tenant_id, service),
        ).fetchone()
    if row is None:
        return {}
    if isinstance(row, dict):
        access_enc = row.get("access_token_enc", "")
        refresh_enc = row.get("refresh_token_enc", "")
        expiry = row.get("token_expiry", "")
        meta_raw = row.get("metadata_json", "{}")
    else:
        access_enc, refresh_enc, expiry, meta_raw = row[0], row[1], row[2], row[3]
    try:
        metadata = json.loads(meta_raw or "{}")
    except Exception:
        metadata = {}
    return {
        "access_token": _decrypt(access_enc or ""),
        "refresh_token": _decrypt(refresh_enc or ""),
        "token_expiry": expiry or "",
        "metadata": metadata,
    }


def update_integration_tokens(
    user_id: str,
    service: str,
    access_token: str,
    token_expiry: str = "",
    tenant_id: str = "default",
) -> None:
    """Update the (encrypted) access token + expiry for an integration on the
    canvas connection. Used after an OAuth refresh (cnr-me-03)."""
    with _conn() as conn:
        ph = sql_placeholder(conn)
        conn.execute(
            f"UPDATE user_integrations SET access_token_enc={ph}, token_expiry={ph} "
            f"WHERE user_id={ph} AND tenant_id={ph} AND service={ph}",
            (_encrypt(access_token), token_expiry, user_id, tenant_id, service),
        )
        conn.commit()


def get_integration_metadata(user_id: str, service: str, tenant_id: str = "default") -> dict:
    """Return the metadata_json dict for an integration (used for Jira base_url etc.)."""
    with _conn() as conn:
        ph = sql_placeholder(conn)
        row = conn.execute(
            f"SELECT metadata_json, access_token_enc FROM user_integrations WHERE user_id={ph} AND tenant_id={ph} AND service={ph} AND status='active'",
            (user_id, tenant_id, service),
        ).fetchone()
    if row is None:
        return {}
    if isinstance(row, dict):
        meta = json.loads(row.get("metadata_json") or "{}")
        meta["api_key"] = _decrypt(row.get("access_token_enc", ""))
    else:
        meta = json.loads(row[0] or "{}")
        meta["api_key"] = _decrypt(row[1] or "")
    return meta


def list_integrations(user_id: str, tenant_id: str = "default") -> list[dict]:
    """List all integrations for *user_id* (no decrypted tokens)."""
    with _conn() as conn:
        ph = sql_placeholder(conn)
        rows = conn.execute(
            f"SELECT service, status, scopes, metadata_json, last_sync_at, created_at FROM user_integrations WHERE user_id={ph} AND tenant_id={ph}",
            (user_id, tenant_id),
        ).fetchall()
    cols = ["service", "status", "scopes", "metadata_json", "last_sync_at", "created_at"]
    result = []
    for row in rows:
        d = dict(zip(cols, row)) if not isinstance(row, dict) else dict(row)
        try:
            d["metadata"] = json.loads(d.pop("metadata_json", "{}") or "{}")
        except Exception:
            d["metadata"] = {}
        result.append(d)
    return result


def revoke_integration(user_id: str, service: str, tenant_id: str = "default") -> bool:
    """Mark integration as revoked and clear encrypted tokens."""
    try:
        with _conn() as conn:
            ph = sql_placeholder(conn)
            conn.execute(
                f"UPDATE user_integrations SET status='revoked', access_token_enc='', refresh_token_enc='' WHERE user_id={ph} AND tenant_id={ph} AND service={ph}",
                (user_id, tenant_id, service),
            )
            conn.commit()
        return True
    except Exception as exc:
        logger.warning("[integrations] revoke failed: %s", exc)
        return False


def update_sync_time(user_id: str, service: str, tenant_id: str = "default") -> None:
    with _conn() as conn:
        ph = sql_placeholder(conn)
        conn.execute(
            f"UPDATE user_integrations SET last_sync_at={ph} WHERE user_id={ph} AND tenant_id={ph} AND service={ph}",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), user_id, tenant_id, service),
        )
        conn.commit()
