# [TEMPLATE: CUI // SP-CTI]
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""Tests for tools.dashboard.byok — BYOK (Bring Your Own Key) management."""

import os
import sqlite3

import pytest

from tools.dashboard import byok


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MINIMAL_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS dashboard_users (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'developer'
        CHECK(role IN ('admin', 'pm', 'developer', 'isso', 'co')),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'suspended')),
    created_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dashboard_user_llm_keys (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES dashboard_users(id),
    provider TEXT NOT NULL,
    encrypted_key TEXT NOT NULL,
    key_label TEXT,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'revoked')),
    department TEXT,
    is_department_key INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dash_llm_keys_user ON dashboard_user_llm_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_dash_llm_keys_provider ON dashboard_user_llm_keys(provider);
"""

TEST_USER_ID = "user-test-001"
TEST_PROVIDER = "anthropic"
TEST_KEY = "sk-ant-FAKE-test-key-1234567890"


def _init_db(db_path: Path):
    """Create a temp database with only the tables BYOK needs."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(MINIMAL_SCHEMA_SQL)
    # Seed a dashboard user so FK constraint is satisfiable
    conn.execute(
        "INSERT INTO dashboard_users (id, email, display_name) VALUES (?, ?, ?)",
        (TEST_USER_ID, "test@example.mil", "Test User"),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_fernet():
    """Reset module-level Fernet cache between tests."""
    byok._fernet = None
    yield
    byok._fernet = None


@pytest.fixture()
def byok_db(tmp_path, monkeypatch):
    """Provide a temporary DB and patch byok.DB_PATH + force base64 fallback."""
    db_path = str(tmp_path / "test_byok.db")
    _init_db(Path(db_path))
    monkeypatch.setattr("tools.dashboard.byok.DB_PATH", db_path)
    monkeypatch.setattr("tools.dashboard.byok.BYOK_ENABLED", True)
    monkeypatch.setattr("tools.dashboard.byok.BYOK_ENCRYPTION_KEY", "")
    return db_path


# ---------------------------------------------------------------------------
# 1-2. encrypt_key / decrypt_key
# ---------------------------------------------------------------------------

class TestEncryptDecrypt:
    """Encryption round-trip and prefix detection."""

    def test_encrypt_decrypt_roundtrip_b64_fallback(self, monkeypatch):
        """encrypt_key -> decrypt_key should return original text (b64 fallback)."""
        monkeypatch.setattr("tools.dashboard.byok.BYOK_ENCRYPTION_KEY", "")
        encrypted = byok.encrypt_key(TEST_KEY)
        assert encrypted.startswith("b64:")
        decrypted = byok.decrypt_key(encrypted)
        assert decrypted == TEST_KEY

    def test_b64_prefix_detection_in_decrypt(self, monkeypatch):
        """decrypt_key should detect 'b64:' prefix and base64-decode."""
        monkeypatch.setattr("tools.dashboard.byok.BYOK_ENCRYPTION_KEY", "")
        import base64
        manual = "b64:" + base64.b64encode(b"hello-world").decode("utf-8")
        assert byok.decrypt_key(manual) == "hello-world"

    def test_encrypt_returns_different_from_plaintext(self, monkeypatch):
        """Encrypted output should never equal the plaintext."""
        monkeypatch.setattr("tools.dashboard.byok.BYOK_ENCRYPTION_KEY", "")
        encrypted = byok.encrypt_key("my-secret")
        assert encrypted != "my-secret"

    def test_decrypt_without_fernet_raises_for_non_b64(self, monkeypatch):
        """decrypt_key should raise ValueError for non-b64 ciphertext when Fernet unavailable."""
        monkeypatch.setattr("tools.dashboard.byok.BYOK_ENCRYPTION_KEY", "")
        with pytest.raises(ValueError, match="Cannot decrypt"):
            byok.decrypt_key("not-a-b64-prefixed-string")


# ---------------------------------------------------------------------------
# 3. store_llm_key
# ---------------------------------------------------------------------------

class TestStoreLlmKey:
    """Tests for storing LLM keys."""

    def test_store_creates_entry(self, byok_db):
        """store_llm_key should insert a row into dashboard_user_llm_keys."""
        result = byok.store_llm_key(TEST_USER_ID, TEST_PROVIDER, TEST_KEY, key_label="My Key")
        assert "id" in result
        assert result["provider"] == TEST_PROVIDER
        assert result["key_label"] == "My Key"

        # Verify in DB
        conn = sqlite3.connect(byok_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM dashboard_user_llm_keys WHERE id = ?", (result["id"],)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["user_id"] == TEST_USER_ID
        assert row["status"] == "active"
        assert row["encrypted_key"].startswith("b64:")

    def test_store_department_key(self, byok_db):
        """store_llm_key with is_department_key=True should set department flag."""
        result = byok.store_llm_key(
            TEST_USER_ID, "openai", "sk-openai-fake",
            department="Engineering", is_department_key=True,
        )
        assert result["is_department_key"] is True
        assert result["department"] == "Engineering"

        conn = sqlite3.connect(byok_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT is_department_key, department FROM dashboard_user_llm_keys WHERE id = ?",
            (result["id"],),
        ).fetchone()
        conn.close()
        assert row["is_department_key"] == 1
        assert row["department"] == "Engineering"


# ---------------------------------------------------------------------------
# 4. list_llm_keys
# ---------------------------------------------------------------------------

class TestListLlmKeys:
    """Tests for listing LLM keys."""

    def test_list_returns_keys_without_encrypted_key(self, byok_db):
        """list_llm_keys should return key metadata but NOT encrypted_key."""
        byok.store_llm_key(TEST_USER_ID, TEST_PROVIDER, TEST_KEY, key_label="Key A")
        byok.store_llm_key(TEST_USER_ID, "openai", "sk-openai-fake", key_label="Key B")

        keys = byok.list_llm_keys(TEST_USER_ID)
        assert len(keys) == 2
        for k in keys:
            assert "encrypted_key" not in k
            assert "provider" in k
            assert "key_label" in k
            assert "status" in k

    def test_list_returns_empty_for_unknown_user(self, byok_db):
        """list_llm_keys should return empty list for a user with no keys."""
        keys = byok.list_llm_keys("nonexistent-user")
        assert keys == []


# ---------------------------------------------------------------------------
# 5. revoke_llm_key
# ---------------------------------------------------------------------------

class TestRevokeLlmKey:
    """Tests for revoking LLM keys."""

    def test_revoke_changes_status(self, byok_db):
        """revoke_llm_key should change status from 'active' to 'revoked'."""
        result = byok.store_llm_key(TEST_USER_ID, TEST_PROVIDER, TEST_KEY)
        key_id = result["id"]

        success = byok.revoke_llm_key(key_id)
        assert success is True

        conn = sqlite3.connect(byok_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT status FROM dashboard_user_llm_keys WHERE id = ?", (key_id,)
        ).fetchone()
        conn.close()
        assert row["status"] == "revoked"

    def test_revoke_returns_true_even_for_missing_id(self, byok_db):
        """revoke_llm_key returns True even when key_id does not exist (no-op UPDATE)."""
        result = byok.revoke_llm_key("nonexistent-key-id")
        assert result is True


# ---------------------------------------------------------------------------
# 6-7. get_llm_key_for_provider
# ---------------------------------------------------------------------------

class TestGetLlmKeyForProvider:
    """Tests for retrieving a decrypted key by provider."""

    def test_returns_decrypted_key(self, byok_db):
        """get_llm_key_for_provider should return the plaintext key."""
        byok.store_llm_key(TEST_USER_ID, TEST_PROVIDER, TEST_KEY)
        key = byok.get_llm_key_for_provider(TEST_USER_ID, TEST_PROVIDER)
        assert key == TEST_KEY

    def test_returns_empty_for_revoked_key(self, byok_db):
        """get_llm_key_for_provider should return '' when the key is revoked."""
        result = byok.store_llm_key(TEST_USER_ID, TEST_PROVIDER, TEST_KEY)
        byok.revoke_llm_key(result["id"])
        key = byok.get_llm_key_for_provider(TEST_USER_ID, TEST_PROVIDER)
        assert key == ""

    def test_returns_empty_for_unknown_provider(self, byok_db):
        """get_llm_key_for_provider should return '' for a provider with no stored key."""
        key = byok.get_llm_key_for_provider(TEST_USER_ID, "unknown-provider")
        assert key == ""


# ---------------------------------------------------------------------------
# 8-12. resolve_api_key
# ---------------------------------------------------------------------------

class TestResolveApiKey:
    """Tests for the D175 key resolution chain."""

    def test_resolve_user_byok(self, byok_db):
        """resolve_api_key should return user key with source='user_byok'."""
        byok.store_llm_key(TEST_USER_ID, TEST_PROVIDER, TEST_KEY)
        api_key, source = byok.resolve_api_key(TEST_USER_ID, TEST_PROVIDER)
        assert api_key == TEST_KEY
        assert source == "user_byok"

    def test_resolve_department_byok(self, byok_db):
        """resolve_api_key should fall back to department key with source='department_byok'."""
        dept_key = "sk-ant-DEPT-key-999"
        byok.store_llm_key(
            TEST_USER_ID, TEST_PROVIDER, dept_key,
            department="Engineering", is_department_key=True,
        )
        # Use a different user who does NOT have a personal key
        other_user = "user-other-002"
        conn = sqlite3.connect(byok_db)
        conn.execute(
            "INSERT INTO dashboard_users (id, email, display_name) VALUES (?, ?, ?)",
            (other_user, "other@example.mil", "Other User"),
        )
        conn.commit()
        conn.close()

        api_key, source = byok.resolve_api_key(other_user, TEST_PROVIDER, department="Engineering")
        assert api_key == dept_key
        assert source == "department_byok"

    def test_resolve_env_var(self, byok_db, monkeypatch):
        """resolve_api_key should fall back to env var with source='env_var'."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-key-from-environ")
        api_key, source = byok.resolve_api_key(TEST_USER_ID, TEST_PROVIDER)
        assert api_key == "sk-env-key-from-environ"
        assert source == "env_var"

    def test_resolve_config_fallback(self, byok_db, monkeypatch):
        """resolve_api_key should return ('', 'config') when no key found anywhere."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        api_key, source = byok.resolve_api_key(TEST_USER_ID, TEST_PROVIDER)
        assert api_key == ""
        assert source == "config"

    def test_resolve_byok_disabled_skips_user_and_dept(self, byok_db, monkeypatch):
        """When BYOK_ENABLED is False, user and department keys are skipped."""
        byok.store_llm_key(TEST_USER_ID, TEST_PROVIDER, TEST_KEY)
        monkeypatch.setattr("tools.dashboard.byok.BYOK_ENABLED", False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        api_key, source = byok.resolve_api_key(TEST_USER_ID, TEST_PROVIDER)
        # Should NOT find the stored key because BYOK is disabled
        assert source in ("env_var", "config")
        assert api_key != TEST_KEY

    def test_resolve_byok_disabled_falls_to_env(self, byok_db, monkeypatch):
        """When BYOK_ENABLED is False, env var still works."""
        monkeypatch.setattr("tools.dashboard.byok.BYOK_ENABLED", False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-env-val")

        api_key, source = byok.resolve_api_key(TEST_USER_ID, "openai")
        assert api_key == "sk-openai-env-val"
        assert source == "env_var"

    def test_resolve_priority_user_over_department(self, byok_db):
        """User BYOK key takes priority over department BYOK key."""
        user_key = "sk-user-personal"
        dept_key = "sk-dept-shared"
        byok.store_llm_key(
            TEST_USER_ID, TEST_PROVIDER, dept_key,
            department="Ops", is_department_key=True,
        )
        byok.store_llm_key(TEST_USER_ID, TEST_PROVIDER, user_key)

        api_key, source = byok.resolve_api_key(TEST_USER_ID, TEST_PROVIDER, department="Ops")
        assert api_key == user_key
        assert source == "user_byok"

    def test_resolve_priority_user_over_env(self, byok_db, monkeypatch):
        """User BYOK key takes priority over environment variable."""
        byok.store_llm_key(TEST_USER_ID, TEST_PROVIDER, TEST_KEY)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-should-not-be-used")

        api_key, source = byok.resolve_api_key(TEST_USER_ID, TEST_PROVIDER)
        assert api_key == TEST_KEY
        assert source == "user_byok"


# ---------------------------------------------------------------------------
# 13. PROVIDER_ENV_MAP
# ---------------------------------------------------------------------------

class TestProviderEnvMap:
    """Tests for the PROVIDER_ENV_MAP constant."""

    def test_has_expected_providers(self):
        """PROVIDER_ENV_MAP should contain anthropic, openai, bedrock, ollama."""
        expected = {"anthropic", "openai", "bedrock", "ollama"}
        assert expected.issubset(set(byok.PROVIDER_ENV_MAP.keys()))

    def test_anthropic_maps_to_correct_env(self):
        """anthropic should map to ANTHROPIC_API_KEY."""
        assert byok.PROVIDER_ENV_MAP["anthropic"] == "ANTHROPIC_API_KEY"

    def test_openai_maps_to_correct_env(self):
        """openai should map to OPENAI_API_KEY."""
        assert byok.PROVIDER_ENV_MAP["openai"] == "OPENAI_API_KEY"

    def test_bedrock_has_empty_env(self):
        """bedrock uses IAM — env var should be empty string."""
        assert byok.PROVIDER_ENV_MAP["bedrock"] == ""

    def test_ollama_has_empty_env(self):
        """ollama is local — env var should be empty string."""
        assert byok.PROVIDER_ENV_MAP["ollama"] == ""
