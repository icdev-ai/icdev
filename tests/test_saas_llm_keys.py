# [TEMPLATE: CUI // SP-CTI]
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""Tests for SaaS tenant LLM key management (Phase 32 -- D141).

Verifies store, list, revoke, get, resolve operations on tenant LLM keys
with Fernet encryption, tier gating, and ownership checks.

Run: pytest tests/test_saas_llm_keys.py -v
"""

import os
import sqlite3
from unittest.mock import patch

import pytest

try:
    from tools.saas.tenant_llm_keys import VALID_PROVIDERS
    _IMPORT_OK = True
except ImportError:
    _IMPORT_OK = False

pytestmark = pytest.mark.skipif(not _IMPORT_OK, reason="tools.saas.tenant_llm_keys not available")


# ---------------------------------------------------------------------------
# Test schema (minimal platform.db subset)
# ---------------------------------------------------------------------------
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, slug TEXT UNIQUE NOT NULL,
    status TEXT DEFAULT 'active',
    tier TEXT DEFAULT 'professional',
    impact_level TEXT DEFAULT 'IL4',
    settings TEXT DEFAULT '{}', artifact_config TEXT DEFAULT '{}',
    bedrock_config TEXT DEFAULT '{}', idp_config TEXT DEFAULT '{}',
    db_host TEXT, db_name TEXT,
    created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
    email TEXT NOT NULL, display_name TEXT,
    role TEXT DEFAULT 'tenant_admin', status TEXT DEFAULT 'active',
    auth_method TEXT DEFAULT 'api_key', last_login TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS subscriptions (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
    tier TEXT DEFAULT 'professional',
    max_projects INTEGER DEFAULT 25, max_users INTEGER DEFAULT 15,
    status TEXT DEFAULT 'active', created_at TEXT,
    starts_at TEXT, ends_at TEXT
);
CREATE TABLE IF NOT EXISTS tenant_llm_keys (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    provider TEXT NOT NULL CHECK (provider IN ('anthropic','openai','bedrock','ollama','vllm')),
    encrypted_key TEXT NOT NULL,
    key_label TEXT NOT NULL DEFAULT '',
    key_prefix TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked')),
    created_by TEXT,
    created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS audit_platform (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT, user_id TEXT, event_type TEXT NOT NULL,
    action TEXT NOT NULL, details TEXT DEFAULT '{}',
    ip_address TEXT, user_agent TEXT, recorded_at TEXT
);
"""

TENANT_PRO = "tenant-pro-001"
TENANT_STARTER = "tenant-starter-001"
USER_ID = "user-001"
TEST_KEY = "sk-ant-FAKE-test-key-1234567890abcdef"


@pytest.fixture()
def platform_db(tmp_path, monkeypatch):
    """Create a temporary platform DB with test data."""
    db_path = tmp_path / "test_platform.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA_SQL)

    # Professional-tier tenant
    conn.execute(
        "INSERT INTO tenants (id, name, slug, tier) VALUES (?, ?, ?, ?)",
        (TENANT_PRO, "Pro Org", "pro-org", "professional"),
    )
    conn.execute(
        "INSERT INTO users (id, tenant_id, email, role) VALUES (?, ?, ?, ?)",
        (USER_ID, TENANT_PRO, "admin@pro.mil", "tenant_admin"),
    )
    conn.execute(
        "INSERT INTO subscriptions (id, tenant_id, tier, status) "
        "VALUES (?, ?, ?, ?)",
        ("sub-pro", TENANT_PRO, "professional", "active"),
    )

    # Starter-tier tenant
    conn.execute(
        "INSERT INTO tenants (id, name, slug, tier) VALUES (?, ?, ?, ?)",
        (TENANT_STARTER, "Starter Org", "starter-org", "starter"),
    )
    conn.execute(
        "INSERT INTO subscriptions (id, tenant_id, tier, status) "
        "VALUES (?, ?, ?, ?)",
        ("sub-starter", TENANT_STARTER, "starter", "active"),
    )

    conn.commit()
    conn.close()

    # Patch get_platform_connection to use test DB
    def _mock_conn():
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(
        "tools.saas.platform_db.get_platform_connection", _mock_conn
    )
    # Silence audit logging in tests
    monkeypatch.setattr(
        "tools.saas.platform_db.log_platform_audit",
        lambda **kw: None,
    )

    return db_path


# ===========================================================================
# Test: store_tenant_llm_key
# ===========================================================================
class TestStoreTenantLlmKey:
    """Test storing LLM provider keys."""

    def test_store_creates_entry(self, platform_db):
        from tools.saas.tenant_llm_keys import store_tenant_llm_key

        result = store_tenant_llm_key(
            TENANT_PRO, "anthropic", TEST_KEY, "My Claude Key", USER_ID,
        )
        assert result["provider"] == "anthropic"
        assert result["key_label"] == "My Claude Key"
        assert result["status"] == "active"
        assert "id" in result
        assert result["key_prefix"] == TEST_KEY[:12]

    def test_store_encrypts_key(self, platform_db):
        from tools.saas.tenant_llm_keys import store_tenant_llm_key

        store_tenant_llm_key(TENANT_PRO, "openai", TEST_KEY, "Test")

        # Read raw encrypted_key from DB — must NOT be plaintext
        conn = sqlite3.connect(str(platform_db))
        row = conn.execute(
            "SELECT encrypted_key FROM tenant_llm_keys WHERE tenant_id = ?",
            (TENANT_PRO,),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] != TEST_KEY  # Must be encrypted

    def test_store_rejects_invalid_provider(self, platform_db):
        from tools.saas.tenant_llm_keys import store_tenant_llm_key

        with pytest.raises(ValueError, match="Invalid provider"):
            store_tenant_llm_key(TENANT_PRO, "google_ai", TEST_KEY, "Bad")

    def test_store_rejects_starter_tier(self, platform_db):
        from tools.saas.tenant_llm_keys import store_tenant_llm_key

        with pytest.raises(ValueError, match="Professional or Enterprise"):
            store_tenant_llm_key(
                TENANT_STARTER, "anthropic", TEST_KEY, "Blocked",
            )


# ===========================================================================
# Test: list_tenant_llm_keys
# ===========================================================================
class TestListTenantLlmKeys:
    """Test listing LLM provider keys."""

    def test_list_returns_keys_without_secrets(self, platform_db):
        from tools.saas.tenant_llm_keys import (
            list_tenant_llm_keys,
            store_tenant_llm_key,
        )

        store_tenant_llm_key(TENANT_PRO, "anthropic", TEST_KEY, "Key 1")
        keys = list_tenant_llm_keys(TENANT_PRO)
        assert len(keys) == 1
        assert keys[0]["provider"] == "anthropic"
        assert "encrypted_key" not in keys[0]
        assert keys[0]["key_prefix"] == TEST_KEY[:12]

    def test_list_returns_empty_for_no_keys(self, platform_db):
        from tools.saas.tenant_llm_keys import list_tenant_llm_keys

        keys = list_tenant_llm_keys(TENANT_PRO)
        assert keys == []


# ===========================================================================
# Test: revoke_tenant_llm_key
# ===========================================================================
class TestRevokeTenantLlmKey:
    """Test revoking LLM provider keys."""

    def test_revoke_changes_status(self, platform_db):
        from tools.saas.tenant_llm_keys import (
            list_tenant_llm_keys,
            revoke_tenant_llm_key,
            store_tenant_llm_key,
        )

        result = store_tenant_llm_key(
            TENANT_PRO, "anthropic", TEST_KEY, "Revocable",
        )
        assert revoke_tenant_llm_key(TENANT_PRO, result["id"]) is True

        keys = list_tenant_llm_keys(TENANT_PRO)
        assert keys[0]["status"] == "revoked"

    def test_revoke_returns_false_for_wrong_tenant(self, platform_db):
        from tools.saas.tenant_llm_keys import (
            revoke_tenant_llm_key,
            store_tenant_llm_key,
        )

        result = store_tenant_llm_key(
            TENANT_PRO, "anthropic", TEST_KEY, "Pro Key",
        )
        # Try to revoke from a different tenant
        assert revoke_tenant_llm_key(TENANT_STARTER, result["id"]) is False


# ===========================================================================
# Test: get_active_key_for_provider
# ===========================================================================
class TestGetActiveKeyForProvider:
    """Test retrieving decrypted active keys."""

    def test_returns_decrypted_key(self, platform_db):
        from tools.saas.tenant_llm_keys import (
            get_active_key_for_provider,
            store_tenant_llm_key,
        )

        store_tenant_llm_key(TENANT_PRO, "anthropic", TEST_KEY, "Round Trip")
        key = get_active_key_for_provider(TENANT_PRO, "anthropic")
        assert key == TEST_KEY

    def test_returns_empty_for_revoked(self, platform_db):
        from tools.saas.tenant_llm_keys import (
            get_active_key_for_provider,
            revoke_tenant_llm_key,
            store_tenant_llm_key,
        )

        result = store_tenant_llm_key(
            TENANT_PRO, "openai", TEST_KEY, "Revoked Key",
        )
        revoke_tenant_llm_key(TENANT_PRO, result["id"])
        key = get_active_key_for_provider(TENANT_PRO, "openai")
        assert key == ""

    def test_returns_empty_for_missing(self, platform_db):
        from tools.saas.tenant_llm_keys import get_active_key_for_provider

        key = get_active_key_for_provider(TENANT_PRO, "ollama")
        assert key == ""


# ===========================================================================
# Test: resolve_tenant_llm_key
# ===========================================================================
class TestResolveTenantLlmKey:
    """Test key resolution chain."""

    def test_resolve_tenant_byok(self, platform_db):
        from tools.saas.tenant_llm_keys import (
            resolve_tenant_llm_key,
            store_tenant_llm_key,
        )

        store_tenant_llm_key(TENANT_PRO, "anthropic", TEST_KEY, "BYOK")
        key, source = resolve_tenant_llm_key(TENANT_PRO, "anthropic")
        assert key == TEST_KEY
        assert source == "tenant_byok"

    def test_resolve_shared_pool_fallback(self, platform_db, monkeypatch):
        from tools.saas.tenant_llm_keys import resolve_tenant_llm_key

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-shared-pool-key")
        key, source = resolve_tenant_llm_key(TENANT_PRO, "anthropic")
        assert key == "sk-shared-pool-key"
        assert source == "shared_pool"

    def test_resolve_empty_when_no_key(self, platform_db, monkeypatch):
        from tools.saas.tenant_llm_keys import resolve_tenant_llm_key

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        key, source = resolve_tenant_llm_key(TENANT_PRO, "anthropic")
        assert key == ""
        assert source == "shared_pool"


# [TEMPLATE: CUI // SP-CTI]
