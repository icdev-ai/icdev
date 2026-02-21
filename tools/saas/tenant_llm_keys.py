# CUI // SP-CTI
"""SaaS Tenant LLM Key Management (Phase 32 -- D141).

CRUD operations for tenant-scoped LLM provider API keys.
Encryption reuses Fernet AES-256 from tools.dashboard.byok.

Resolution order:
  1. Tenant BYOK key (from tenant_llm_keys table)
  2. Platform shared pool (from environment variables)
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("saas.tenant_llm_keys")

VALID_PROVIDERS = ("anthropic", "openai", "bedrock", "ollama", "vllm")

# Provider → environment variable for shared-pool fallback
PROVIDER_ENV_MAP = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "bedrock": "",       # Bedrock uses IAM credentials, not a single key
    "ollama": "",        # Ollama is local, no key needed
    "vllm": "",          # vLLM is local, no key needed
}


# ---------------------------------------------------------------------------
# Encryption helpers (delegated to dashboard byok module)
# ---------------------------------------------------------------------------
def _encrypt(plaintext):
    """Encrypt a plaintext key using Fernet AES-256."""
    try:
        from tools.dashboard.byok import encrypt_key
        return encrypt_key(plaintext)
    except ImportError:
        raise RuntimeError(
            "cryptography package is required for LLM key encryption. "
            "Install it with: pip install cryptography"
        )


def _decrypt(ciphertext):
    """Decrypt a ciphertext key."""
    try:
        from tools.dashboard.byok import decrypt_key
        return decrypt_key(ciphertext)
    except ImportError:
        raise RuntimeError(
            "cryptography package is required for LLM key decryption. "
            "Install it with: pip install cryptography"
        )


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def _get_conn():
    """Get platform DB connection with dict row factory."""
    from tools.saas.platform_db import get_platform_connection
    return get_platform_connection()


def _get_tenant_tier(tenant_id):
    """Look up the active subscription tier for a tenant."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT tier FROM subscriptions WHERE tenant_id = ? "
            "AND status = 'active' ORDER BY created_at DESC LIMIT 1",
            (tenant_id,),
        ).fetchone()
        if row:
            return row["tier"] if hasattr(row, "keys") else row[0]
        # Fallback to tenant table tier
        row = conn.execute(
            "SELECT tier FROM tenants WHERE id = ?", (tenant_id,),
        ).fetchone()
        if row:
            return row["tier"] if hasattr(row, "keys") else row[0]
        return "starter"
    finally:
        conn.close()


def _tier_allows_byok(tier):
    """Check if the subscription tier allows BYOK LLM keys."""
    return tier in ("professional", "enterprise")


def _audit(tenant_id, user_id, event_type, action, details=None):
    """Log an audit event (best-effort)."""
    try:
        from tools.saas.platform_db import log_platform_audit
        log_platform_audit(
            tenant_id=tenant_id,
            user_id=user_id or "",
            event_type=event_type,
            action=action,
            details=json.dumps(details or {}),
        )
    except Exception as exc:
        logger.debug("Audit logging failed: %s", exc)


# ---------------------------------------------------------------------------
# CRUD Operations
# ---------------------------------------------------------------------------
def store_tenant_llm_key(
    tenant_id,
    provider,
    plaintext_key,
    key_label="",
    created_by=None,
):
    """Store an encrypted LLM API key for a tenant.

    Args:
        tenant_id: Platform tenant ID.
        provider: One of VALID_PROVIDERS.
        plaintext_key: The raw API key (encrypted before storage).
        key_label: Human-friendly label.
        created_by: User ID of the admin who added the key.

    Returns:
        dict with id, provider, key_label, key_prefix, status.

    Raises:
        ValueError: If provider is invalid or tier doesn't allow BYOK.
    """
    provider = provider.strip().lower()
    if provider not in VALID_PROVIDERS:
        raise ValueError(
            "Invalid provider '{}'. Must be one of: {}".format(
                provider, ", ".join(VALID_PROVIDERS)
            )
        )

    # Check tier gate
    tier = _get_tenant_tier(tenant_id)
    if not _tier_allows_byok(tier):
        raise ValueError(
            "BYOK LLM keys require a Professional or Enterprise subscription. "
            "Current tier: {}".format(tier)
        )

    key_id = str(uuid.uuid4())
    key_prefix = plaintext_key[:12] if len(plaintext_key) >= 12 else plaintext_key
    encrypted = _encrypt(plaintext_key)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO tenant_llm_keys "
            "(id, tenant_id, provider, encrypted_key, key_label, key_prefix, "
            "status, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (key_id, tenant_id, provider, encrypted, key_label or provider,
             key_prefix, "active", created_by, now, now),
        )
        conn.commit()
    finally:
        conn.close()

    _audit(tenant_id, created_by, "llm_key.created", "store_tenant_llm_key", {
        "key_id": key_id, "provider": provider, "key_label": key_label or provider,
    })

    logger.info(
        "Stored LLM key for tenant=%s provider=%s label=%s",
        tenant_id, provider, key_label or provider,
    )

    return {
        "id": key_id,
        "provider": provider,
        "key_label": key_label or provider,
        "key_prefix": key_prefix,
        "status": "active",
    }


def list_tenant_llm_keys(tenant_id):
    """List LLM keys for a tenant (encrypted_key redacted).

    Returns:
        list of dicts with id, provider, key_label, key_prefix,
        status, created_by, created_at, updated_at.
    """
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, provider, key_label, key_prefix, status, "
            "created_by, created_at, updated_at "
            "FROM tenant_llm_keys WHERE tenant_id = ? "
            "ORDER BY created_at DESC",
            (tenant_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def revoke_tenant_llm_key(tenant_id, key_id):
    """Revoke a tenant LLM key.

    Args:
        tenant_id: Ensures key belongs to this tenant (ownership check).
        key_id: The key to revoke.

    Returns:
        True if revoked, False if key not found for this tenant.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = _get_conn()
    try:
        cursor = conn.execute(
            "UPDATE tenant_llm_keys SET status = 'revoked', updated_at = ? "
            "WHERE id = ? AND tenant_id = ? AND status = 'active'",
            (now, key_id, tenant_id),
        )
        conn.commit()
        revoked = cursor.rowcount > 0
    finally:
        conn.close()

    if revoked:
        _audit(tenant_id, None, "llm_key.revoked", "revoke_tenant_llm_key", {
            "key_id": key_id,
        })
        logger.info("Revoked LLM key %s for tenant %s", key_id, tenant_id)

    return revoked


def get_active_key_for_provider(tenant_id, provider):
    """Get the decrypted active LLM key for a tenant+provider.

    Returns:
        Plaintext API key, or empty string if none found.
    """
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT encrypted_key FROM tenant_llm_keys "
            "WHERE tenant_id = ? AND provider = ? AND status = 'active' "
            "ORDER BY created_at DESC LIMIT 1",
            (tenant_id, provider),
        ).fetchone()
        if not row:
            return ""
        encrypted = row["encrypted_key"] if hasattr(row, "keys") else row[0]
        return _decrypt(encrypted)
    except Exception as exc:
        logger.warning("Failed to retrieve LLM key: %s", exc)
        return ""
    finally:
        conn.close()


def resolve_tenant_llm_key(tenant_id, provider):
    """Resolve the LLM key for a tenant+provider.

    Resolution order:
      1. Tenant BYOK key (from tenant_llm_keys table)
      2. Platform shared pool (from environment variables)

    Returns:
        (api_key, source) where source is 'tenant_byok' or 'shared_pool'.
    """
    # 1. Check tenant BYOK key
    tenant_key = get_active_key_for_provider(tenant_id, provider)
    if tenant_key:
        return (tenant_key, "tenant_byok")

    # 2. Fallback to environment variables (platform shared pool)
    env_var = PROVIDER_ENV_MAP.get(provider, "")
    if env_var:
        env_key = os.environ.get(env_var, "")
        if env_key:
            return (env_key, "shared_pool")

    return ("", "shared_pool")


# CUI // SP-CTI
