#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for MCP OAuth 2.1, Elicitation, and Tasks (Phase 55, D345-D346)."""

import hashlib
import json
import sqlite3
import time
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary platform DB with api_keys and users tables."""
    db_path = tmp_path / "platform.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE users (
            id TEXT PRIMARY KEY,
            email TEXT,
            role TEXT DEFAULT 'developer'
        )
    """)
    conn.execute("""
        CREATE TABLE api_keys (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            key_hash TEXT,
            is_active INTEGER DEFAULT 1,
            tenant_id TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    # Insert a test user and API key
    conn.execute("INSERT INTO users (id, email, role) VALUES (?, ?, ?)",
                 ("user-001", "test@icdev.mil", "developer"))
    test_key = "icdev_test_key_abc123"
    key_hash = hashlib.sha256(test_key.encode()).hexdigest()
    conn.execute(
        "INSERT INTO api_keys (id, user_id, key_hash, is_active, tenant_id) VALUES (?, ?, ?, ?, ?)",
        ("key-001", "user-001", key_hash, 1, "tenant-abc"),
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def verifier(tmp_db):
    """Create an MCPOAuthVerifier with test DB."""
    from tools.saas.mcp_oauth import MCPOAuthVerifier
    return MCPOAuthVerifier(db_path=tmp_db, secret_key="test-secret-key-123")


@pytest.fixture
def elicitation_handler():
    """Create an MCPElicitationHandler."""
    from tools.saas.mcp_oauth import MCPElicitationHandler
    return MCPElicitationHandler()


@pytest.fixture
def task_manager():
    """Create an MCPTaskManager."""
    from tools.saas.mcp_oauth import MCPTaskManager
    return MCPTaskManager()


# ---------------------------------------------------------------------------
# MCPOAuthVerifier Tests
# ---------------------------------------------------------------------------

class TestMCPOAuthVerifier:
    """Test OAuth 2.1 token verification."""

    def test_empty_token(self, verifier):
        result = verifier.verify_token("")
        assert result["verified"] is False
        assert "No token" in result["error"]

    def test_none_token(self, verifier):
        result = verifier.verify_token(None)
        assert result["verified"] is False

    def test_api_key_valid(self, verifier):
        result = verifier.verify_token("icdev_test_key_abc123")
        assert result["verified"] is True
        assert result["method"] == "api_key"
        assert result["user_id"] == "user-001"
        assert result["email"] == "test@icdev.mil"
        assert result["role"] == "developer"
        assert "mcp:read" in result["scopes"]
        assert result["tenant_id"] == "tenant-abc"

    def test_api_key_invalid(self, verifier):
        result = verifier.verify_token("icdev_wrong_key")
        assert result["verified"] is False

    def test_api_key_not_icdev_prefix(self, verifier):
        result = verifier._verify_api_key("not_icdev_key")
        assert result["verified"] is False
        assert "Not an ICDEV API key" in result["error"]

    def test_api_key_no_db(self, tmp_path):
        from tools.saas.mcp_oauth import MCPOAuthVerifier
        v = MCPOAuthVerifier(db_path=tmp_path / "nonexistent.db")
        result = v._verify_api_key("icdev_test")
        assert result["verified"] is False
        assert "database not found" in result["error"]

    def test_token_caching(self, verifier):
        # First call
        result1 = verifier.verify_token("icdev_test_key_abc123")
        assert result1["verified"] is True
        # Second call should hit cache
        result2 = verifier.verify_token("icdev_test_key_abc123")
        assert result2["verified"] is True
        assert result2["method"] == "api_key"
        # Verify cache has entry
        assert len(verifier._token_cache) == 1

    def test_unknown_token_fails(self, verifier):
        result = verifier.verify_token("random_gibberish_token")
        assert result["verified"] is False
        assert "Token verification failed" in result["error"]


class TestHMACToken:
    """Test HMAC offline token generation and verification."""

    def test_generate_and_verify(self, verifier):
        token = verifier.generate_offline_token(
            user_id="user-offline",
            email="offline@icdev.mil",
            role="isso",
            scopes=["mcp:read", "mcp:write"],
            tenant_id="tenant-airgap",
            ttl_seconds=3600,
        )
        assert token.startswith("hmac_")
        result = verifier.verify_token(token)
        assert result["verified"] is True
        assert result["method"] == "hmac"
        assert result["user_id"] == "user-offline"
        assert result["email"] == "offline@icdev.mil"
        assert result["role"] == "isso"
        assert result["tenant_id"] == "tenant-airgap"

    def test_expired_token(self, verifier):
        token = verifier.generate_offline_token(
            user_id="user-expired",
            ttl_seconds=-1,  # Already expired
        )
        # verify_token returns generic error; check HMAC method directly
        result = verifier._verify_hmac_token(token)
        assert result["verified"] is False
        assert "expired" in result["error"].lower()
        # Full verify_token should also fail
        result2 = verifier.verify_token(token)
        assert result2["verified"] is False

    def test_wrong_secret_key(self, tmp_db):
        from tools.saas.mcp_oauth import MCPOAuthVerifier
        v1 = MCPOAuthVerifier(db_path=tmp_db, secret_key="secret-A")
        v2 = MCPOAuthVerifier(db_path=tmp_db, secret_key="secret-B")
        token = v1.generate_offline_token(user_id="user-test")
        result = v2.verify_token(token)
        assert result["verified"] is False

    def test_invalid_hmac_format(self, verifier):
        result = verifier._verify_hmac_token("hmac_no_dot_here")
        assert result["verified"] is False
        assert "Invalid HMAC token format" in result["error"]

    def test_not_hmac_prefix(self, verifier):
        result = verifier._verify_hmac_token("not_hmac")
        assert result["verified"] is False
        assert "Not an HMAC token" in result["error"]


class TestJWTVerification:
    """Test JWT token verification."""

    def test_valid_jwt_structure(self, verifier):
        import base64
        header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(json.dumps({
            "sub": "user-jwt",
            "email": "jwt@icdev.mil",
            "role": "admin",
            "scope": "mcp:read mcp:write",
            "tenant_id": "tenant-jwt",
            "exp": int(time.time()) + 3600,
        }).encode()).rstrip(b"=").decode()
        sig = base64.urlsafe_b64encode(b"fake_signature").rstrip(b"=").decode()
        token = f"{header}.{payload}.{sig}"

        result = verifier.verify_token(token)
        assert result["verified"] is True
        assert result["method"] == "jwt"
        assert result["user_id"] == "user-jwt"
        assert result["tenant_id"] == "tenant-jwt"
        assert "mcp:read" in result["scopes"]

    def test_expired_jwt(self, verifier):
        import base64
        header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(json.dumps({
            "sub": "user-jwt",
            "exp": int(time.time()) - 100,  # Expired
        }).encode()).rstrip(b"=").decode()
        sig = base64.urlsafe_b64encode(b"sig").rstrip(b"=").decode()
        token = f"{header}.{payload}.{sig}"
        result = verifier.verify_token(token)
        assert result["verified"] is False

    def test_not_jwt(self, verifier):
        result = verifier._verify_jwt("no-dots-here")
        assert result["verified"] is False
        assert "Not a JWT" in result["error"]

    def test_jwt_scopes_as_list(self, verifier):
        import base64
        header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(json.dumps({
            "sub": "user-jwt",
            "scopes": ["mcp:read", "mcp:execute"],
            "exp": int(time.time()) + 3600,
        }).encode()).rstrip(b"=").decode()
        sig = base64.urlsafe_b64encode(b"sig").rstrip(b"=").decode()
        token = f"{header}.{payload}.{sig}"
        result = verifier.verify_token(token)
        assert result["verified"] is True
        assert "mcp:execute" in result["scopes"]


# ---------------------------------------------------------------------------
# MCPElicitationHandler Tests
# ---------------------------------------------------------------------------

class TestMCPElicitationHandler:
    """Test MCP Elicitation support."""

    def test_create_elicitation(self, elicitation_handler):
        e = elicitation_handler.create_elicitation(
            tool_name="ssp_generate",
            question="Which baseline: moderate or high?",
            options=["moderate", "high"],
            input_type="choice",
        )
        assert "elicitation_id" in e
        assert e["tool_name"] == "ssp_generate"
        assert e["status"] == "pending"
        assert e["input_type"] == "choice"
        assert e["options"] == ["moderate", "high"]

    def test_resolve_elicitation(self, elicitation_handler):
        e = elicitation_handler.create_elicitation(
            tool_name="deploy",
            question="Confirm deployment?",
            input_type="confirm",
        )
        resolved = elicitation_handler.resolve_elicitation(e["elicitation_id"], "yes")
        assert resolved["status"] == "resolved"
        assert resolved["response"] == "yes"
        assert "resolved_at" in resolved

    def test_resolve_nonexistent(self, elicitation_handler):
        result = elicitation_handler.resolve_elicitation("fake-id", "value")
        assert "error" in result

    def test_get_pending(self, elicitation_handler):
        elicitation_handler.create_elicitation("tool1", "q1?")
        elicitation_handler.create_elicitation("tool2", "q2?")
        e3 = elicitation_handler.create_elicitation("tool3", "q3?")
        elicitation_handler.resolve_elicitation(e3["elicitation_id"], "answer")

        pending = elicitation_handler.get_pending()
        assert len(pending) == 2

    def test_text_input_type_default(self, elicitation_handler):
        e = elicitation_handler.create_elicitation("tool1", "Enter value:")
        assert e["input_type"] == "text"
        assert e["options"] is None


# ---------------------------------------------------------------------------
# MCPTaskManager Tests
# ---------------------------------------------------------------------------

class TestMCPTaskManager:
    """Test MCP Tasks lifecycle management."""

    def test_create_task(self, task_manager):
        task = task_manager.create_task("sbom_generate", {"project_id": "proj-123"})
        assert "task_id" in task
        assert task["tool_name"] == "sbom_generate"
        assert task["status"] == "created"
        assert task["progress"] == 0

    def test_update_progress(self, task_manager):
        task = task_manager.create_task("sast_scan", {"project_dir": "/app"})
        updated = task_manager.update_progress(task["task_id"], 50)
        assert updated["progress"] == 50
        assert updated["status"] == "running"

    def test_complete_task(self, task_manager):
        task = task_manager.create_task("terraform_plan", {})
        completed = task_manager.complete_task(task["task_id"], {"plan": "ok"})
        assert completed["status"] == "completed"
        assert completed["progress"] == 100
        assert completed["result"] == {"plan": "ok"}
        assert "completed_at" in completed

    def test_fail_task(self, task_manager):
        task = task_manager.create_task("deploy", {})
        failed = task_manager.fail_task(task["task_id"], "Timeout after 300s")
        assert failed["status"] == "failed"
        assert failed["error"] == "Timeout after 300s"

    def test_get_task(self, task_manager):
        task = task_manager.create_task("lint", {})
        got = task_manager.get_task(task["task_id"])
        assert got["tool_name"] == "lint"

    def test_get_nonexistent_task(self, task_manager):
        got = task_manager.get_task("fake-id")
        assert "error" in got

    def test_list_tasks(self, task_manager):
        task_manager.create_task("tool1", {})
        task_manager.create_task("tool2", {})
        t3 = task_manager.create_task("tool3", {})
        task_manager.complete_task(t3["task_id"], {"ok": True})

        all_tasks = task_manager.list_tasks()
        assert len(all_tasks) == 3

        completed = task_manager.list_tasks(status="completed")
        assert len(completed) == 1
        assert completed[0]["tool_name"] == "tool3"

        created = task_manager.list_tasks(status="created")
        assert len(created) == 2

    def test_update_nonexistent(self, task_manager):
        result = task_manager.update_progress("fake", 50)
        assert "error" in result

    def test_complete_nonexistent(self, task_manager):
        result = task_manager.complete_task("fake", {})
        assert "error" in result

    def test_fail_nonexistent(self, task_manager):
        result = task_manager.fail_task("fake", "err")
        assert "error" in result
