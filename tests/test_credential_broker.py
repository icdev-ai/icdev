#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Credential Broker — NemoClaw-adapted agent credential isolation (D-NC-1)."""

import sqlite3

import pytest

from tools.security.credential_broker import CredentialBroker, _hash_token


@pytest.fixture
def broker_db(tmp_path):
    """Create a temp DB for testing."""
    db_path = tmp_path / "test_broker.db"
    return db_path


@pytest.fixture
def config_enabled():
    """Config with broker enabled."""
    return {
        "enabled": True,
        "tokens": {
            "default_ttl_seconds": 3600,
            "max_ttl_seconds": 86400,
            "hash_algorithm": "sha256",
            "auto_revoke_on_trust_drop": True,
            "trust_revoke_threshold": 0.50,
        },
        "function_map": {
            "code_generation": {"provider": "ollama", "scope": "model:invoke"},
            "compliance_export": {"provider": "ollama", "scope": "model:invoke"},
            "secrets_read": {"provider": "aws_secrets", "scope": "secretsmanager:GetSecretValue"},
            "bedrock_invoke": {"provider": "bedrock", "scope": "model:invoke", "max_ttl_seconds": 7200},
        },
        "audit": {"log_grants": True, "log_denials": True, "redact_credentials": True},
        "fallback": {"allow_direct_env": True, "log_fallback_warning": True},
    }


@pytest.fixture
def config_disabled():
    """Config with broker disabled."""
    return {
        "enabled": False,
        # Prevent the real ICDEV_CREDENTIAL_BROKER_ENABLED env var from re-enabling
        # the broker when it happens to be set in .env.
        "env_override": "ICDEV_CREDENTIAL_BROKER_DISABLED_TEST_SENTINEL",
        "tokens": {"default_ttl_seconds": 3600, "max_ttl_seconds": 86400},
        "function_map": {
            "code_generation": {"provider": "ollama", "scope": "model:invoke"},
        },
        "fallback": {"allow_direct_env": True},
    }


@pytest.fixture
def broker(broker_db, config_enabled):
    return CredentialBroker(db_path=broker_db, config=config_enabled)


@pytest.fixture
def disabled_broker(broker_db, config_disabled):
    return CredentialBroker(db_path=broker_db, config=config_disabled)


class TestCredentialRequest:
    def test_request_returns_token(self, broker):
        result = broker.request_credential("builder", "code_generation")
        assert "token" in result
        assert result["provider"] == "ollama"
        assert result["scope"] == "model:invoke"
        assert result["agent_id"] == "builder"
        assert result["function"] == "code_generation"
        assert result["ttl_seconds"] == 3600

    def test_request_custom_ttl(self, broker):
        result = broker.request_credential("builder", "code_generation", ttl_seconds=1800)
        assert result["ttl_seconds"] == 1800

    def test_request_ttl_capped_at_max(self, broker):
        result = broker.request_credential("builder", "code_generation", ttl_seconds=999999)
        assert result["ttl_seconds"] <= 86400

    def test_request_function_specific_max_ttl(self, broker):
        result = broker.request_credential("builder", "bedrock_invoke", ttl_seconds=999999)
        assert result["ttl_seconds"] <= 7200

    def test_request_unmapped_function_denied(self, broker):
        result = broker.request_credential("builder", "nonexistent_function")
        assert "error" in result
        assert result["error"] == "function_not_mapped"

    def test_request_creates_audit_entry(self, broker, broker_db):
        broker.request_credential("builder", "code_generation")
        conn = sqlite3.connect(str(broker_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM credential_broker_log WHERE action = 'grant' LIMIT 1").fetchone()
        conn.close()
        assert row is not None
        assert row["agent_id"] == "builder"
        assert row["function"] == "code_generation"

    def test_deny_creates_audit_entry(self, broker, broker_db):
        broker.request_credential("builder", "nonexistent")
        conn = sqlite3.connect(str(broker_db))
        count = conn.execute("SELECT COUNT(*) FROM credential_broker_log WHERE action = 'deny'").fetchone()[0]
        conn.close()
        assert count >= 1


class TestCredentialRevocation:
    def test_revoke_all_agent_tokens(self, broker):
        broker.request_credential("builder", "code_generation")
        broker.request_credential("builder", "compliance_export")
        result = broker.revoke_credentials("builder", reason="trust_drop")
        assert result["revoked_count"] == 2
        assert result["reason"] == "trust_drop"

    def test_revoke_empty_when_no_tokens(self, broker):
        result = broker.revoke_credentials("nonexistent_agent")
        assert result["revoked_count"] == 0

    def test_revoked_token_not_valid(self, broker):
        grant = broker.request_credential("builder", "code_generation")
        token = grant["token"]
        broker.revoke_credentials("builder")
        result = broker.validate_token(token)
        assert not result["valid"]
        assert result["reason"] == "token_not_found_or_revoked"


class TestTokenValidation:
    def test_validate_valid_token(self, broker):
        grant = broker.request_credential("builder", "code_generation")
        result = broker.validate_token(grant["token"])
        assert result["valid"] is True
        assert result["agent_id"] == "builder"
        assert result["function"] == "code_generation"

    def test_validate_unknown_token(self, broker):
        result = broker.validate_token("bogus_token_value")
        assert not result["valid"]
        assert result["reason"] == "token_not_found_or_revoked"

    def test_token_hash_is_sha256(self):
        token = "test_token_value"
        h = _hash_token(token)
        assert len(h) == 64  # SHA-256 hex digest


class TestFallback:
    def test_disabled_broker_returns_fallback(self, disabled_broker):
        result = disabled_broker.request_credential("builder", "code_generation")
        assert result.get("fallback") is True

    def test_disabled_broker_no_fallback_returns_error(self, broker_db):
        config = {
            "enabled": False,
            "env_override": "ICDEV_CREDENTIAL_BROKER_DISABLED_TEST_SENTINEL",
            "function_map": {"code_generation": {"provider": "ollama", "scope": "model:invoke"}},
            "fallback": {"allow_direct_env": False},
        }
        b = CredentialBroker(db_path=broker_db, config=config)
        result = b.request_credential("builder", "code_generation")
        assert "error" in result
        assert result["error"] == "broker_disabled_no_fallback"

    def test_fallback_creates_audit_entry(self, disabled_broker, broker_db):
        disabled_broker.request_credential("builder", "code_generation")
        conn = sqlite3.connect(str(broker_db))
        count = conn.execute("SELECT COUNT(*) FROM credential_broker_log WHERE action = 'fallback'").fetchone()[0]
        conn.close()
        assert count >= 1


class TestStatus:
    def test_status_returns_counts(self, broker):
        broker.request_credential("builder", "code_generation")
        broker.request_credential("security", "secrets_read")
        status = broker.get_status()
        assert status["enabled"] is True
        assert status["active_tokens"] == 2
        assert status["total_grants"] == 2
        assert status["mapped_functions"] == 4


class TestGateEvaluation:
    def test_gate_passes_when_enabled(self, broker):
        result = broker.evaluate_gate("proj-123")
        assert result["passed"] is True
        assert result["gate"] == "credential_isolation"

    def test_gate_warns_when_disabled_with_fallback(self, disabled_broker):
        result = disabled_broker.evaluate_gate("proj-123")
        assert result["passed"] is True  # Fallback allowed = not blocking
        assert any("fallback" in w for w in result["warnings"])

    def test_gate_blocks_when_disabled_no_fallback(self, broker_db):
        config = {
            "enabled": False,
            "env_override": "ICDEV_CREDENTIAL_BROKER_DISABLED_TEST_SENTINEL",
            "fallback": {"allow_direct_env": False},
        }
        b = CredentialBroker(db_path=broker_db, config=config)
        result = b.evaluate_gate("proj-123")
        assert result["passed"] is False
        assert len(result["blocking"]) > 0


class TestAuditLog:
    def test_audit_log_shows_events(self, broker):
        broker.request_credential("builder", "code_generation")
        broker.request_credential("builder", "nonexistent")
        log = broker.get_audit_log(agent_id="builder")
        assert len(log) >= 2
        actions = {entry["action"] for entry in log}
        assert "grant" in actions
        assert "deny" in actions

    def test_audit_log_limit(self, broker):
        for _ in range(10):
            broker.request_credential("builder", "code_generation")
        log = broker.get_audit_log(limit=5)
        assert len(log) == 5
