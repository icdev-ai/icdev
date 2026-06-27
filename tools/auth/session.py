# CUI // SP-CTI
"""SSO session validation utilities."""
from __future__ import annotations

from datetime import datetime, timezone


def validate_sso_session(session_id: str) -> dict:
    """Return session info dict, or raise ValueError if not found or expired."""
    from tools.db import storage

    with storage.get_connection() as conn:
        row = conn.execute(
            "SELECT id, tenant_id, provider_id, name_id, expires_at "
            "FROM sso_sessions WHERE id = %s",
            (session_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"SSO session not found: {session_id!r}")

    expires_at = row["expires_at"]
    if expires_at:
        try:
            exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except Exception:
            exp_dt = None
        if exp_dt and datetime.now(timezone.utc) > exp_dt:
            raise ValueError(f"SSO session expired at {expires_at}")

    return {
        "id": row["id"],
        "tenant_id": row["tenant_id"],
        "provider_id": row["provider_id"],
        "name_id": row["name_id"],
        "expires_at": expires_at,
    }
