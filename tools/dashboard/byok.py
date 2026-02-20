# CUI // SP-CTI
"""
BYOK — Bring Your Own Key management (Phase 30 — D175-D178).

Provides:
- Fernet AES-256 encryption/decryption for LLM API keys
- CRUD operations for user and department LLM keys
- Key resolution: user BYOK → department BYOK → env var → system config
- Integration with LLM router via api_key_override
"""

import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from tools.dashboard.config import BYOK_ENABLED, BYOK_ENCRYPTION_KEY, DB_PATH

# ---------------------------------------------------------------------------
# Fernet encryption (optional dependency — graceful fallback)
# ---------------------------------------------------------------------------

_fernet = None


def _get_fernet():
    """Lazy-init Fernet cipher from BYOK_ENCRYPTION_KEY env var."""
    global _fernet
    if _fernet is not None:
        return _fernet

    if not BYOK_ENCRYPTION_KEY:
        return None

    try:
        from cryptography.fernet import Fernet

        _fernet = Fernet(BYOK_ENCRYPTION_KEY.encode("utf-8"))
        return _fernet
    except ImportError:
        # cryptography not installed — fall back to base64 obfuscation
        return None
    except Exception:
        return None


def encrypt_key(plaintext: str) -> str:
    """Encrypt an API key for storage."""
    f = _get_fernet()
    if f:
        return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")
    # Fallback: base64 encoding (NOT secure — warns at startup)
    import base64

    return "b64:" + base64.b64encode(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_key(ciphertext: str) -> str:
    """Decrypt a stored API key."""
    if ciphertext.startswith("b64:"):
        import base64

        return base64.b64decode(ciphertext[4:]).decode("utf-8")
    f = _get_fernet()
    if f:
        return f.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    raise ValueError("Cannot decrypt: Fernet key not configured")


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ---------------------------------------------------------------------------
# LLM Key CRUD
# ---------------------------------------------------------------------------


def store_llm_key(
    user_id: str,
    provider: str,
    plaintext_key: str,
    key_label: str = "",
    department: str = "",
    is_department_key: bool = False,
) -> dict:
    """Store an encrypted LLM API key for a user or department."""
    key_id = str(uuid.uuid4())
    encrypted = encrypt_key(plaintext_key)
    now = datetime.now(timezone.utc).isoformat()

    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO dashboard_user_llm_keys
               (id, user_id, provider, encrypted_key, key_label,
                department, is_department_key, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                key_id,
                user_id,
                provider,
                encrypted,
                key_label,
                department,
                1 if is_department_key else 0,
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "id": key_id,
        "provider": provider,
        "key_label": key_label,
        "department": department,
        "is_department_key": is_department_key,
    }


def list_llm_keys(user_id: str) -> list:
    """List LLM keys for a user (encrypted_key redacted)."""
    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT id, provider, key_label, status, department,
                      is_department_key, created_at, updated_at
               FROM dashboard_user_llm_keys
               WHERE user_id = ? ORDER BY created_at DESC""",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def revoke_llm_key(key_id: str) -> bool:
    """Revoke an LLM key."""
    conn = _get_db()
    try:
        conn.execute(
            """UPDATE dashboard_user_llm_keys
               SET status = 'revoked', updated_at = ?
               WHERE id = ?""",
            (datetime.now(timezone.utc).isoformat(), key_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def get_llm_key_for_provider(user_id: str, provider: str) -> str:
    """Get the decrypted LLM key for a user+provider. Returns empty string if none."""
    conn = _get_db()
    try:
        row = conn.execute(
            """SELECT encrypted_key FROM dashboard_user_llm_keys
               WHERE user_id = ? AND provider = ? AND status = 'active'
                     AND is_department_key = 0
               ORDER BY created_at DESC LIMIT 1""",
            (user_id, provider),
        ).fetchone()
        if row:
            return decrypt_key(row["encrypted_key"])
        return ""
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Key Resolution (D175 — user → department → env → config)
# ---------------------------------------------------------------------------

# Maps provider names to their env-var equivalents
PROVIDER_ENV_MAP = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "bedrock": "",  # Bedrock uses IAM, no key
    "ollama": "",  # Ollama is local, no key
}


def resolve_api_key(
    user_id: str, provider: str, department: str = ""
) -> tuple:
    """Resolve the API key for a provider using the BYOK priority chain.

    Returns (api_key: str, source: str).
    Source is one of: 'user_byok', 'department_byok', 'env_var', 'config'.
    """
    if not BYOK_ENABLED:
        # BYOK disabled — skip user/dept keys entirely
        env_key = _get_env_key(provider)
        return (env_key, "env_var") if env_key else ("", "config")

    # 1. User BYOK key
    if user_id:
        user_key = get_llm_key_for_provider(user_id, provider)
        if user_key:
            return (user_key, "user_byok")

    # 2. Department BYOK key
    if department:
        conn = _get_db()
        try:
            row = conn.execute(
                """SELECT encrypted_key FROM dashboard_user_llm_keys
                   WHERE department = ? AND provider = ? AND status = 'active'
                         AND is_department_key = 1
                   ORDER BY created_at DESC LIMIT 1""",
                (department, provider),
            ).fetchone()
            if row:
                return (decrypt_key(row["encrypted_key"]), "department_byok")
        finally:
            conn.close()

    # 3. Environment variable
    env_key = _get_env_key(provider)
    if env_key:
        return (env_key, "env_var")

    # 4. System config (router handles this — return empty)
    return ("", "config")


def _get_env_key(provider: str) -> str:
    """Get API key from environment variable for a provider."""
    env_name = PROVIDER_ENV_MAP.get(provider, "")
    if env_name:
        return os.environ.get(env_name, "")
    return ""
