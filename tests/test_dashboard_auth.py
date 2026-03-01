# [TEMPLATE: CUI // SP-CTI]
"""
Comprehensive tests for dashboard auth module (tools/dashboard/auth.py).

Covers: key generation, hashing, prefix extraction, user CRUD, API key
lifecycle, validation (active/expired/revoked/suspended), auth event
logging, RBAC matrix, require_role decorator, bootstrap_admin, and
register_dashboard_auth.
"""

import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask, g

# ---------------------------------------------------------------------------
# Schema SQL for temp DB setup
# ---------------------------------------------------------------------------
from tools.db.init_icdev_db import DASHBOARD_AUTH_ALTER_SQL, SCHEMA_SQL


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Create a temporary ICDEV database and monkeypatch DB_PATH."""
    db_file = tmp_path / "icdev_test.db"
    conn = sqlite3.connect(str(db_file))
    conn.executescript(SCHEMA_SQL)
    for stmt in DASHBOARD_AUTH_ALTER_SQL:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # Column may already exist
    conn.commit()
    conn.close()

    # Monkeypatch DB_PATH in both config and auth modules
    monkeypatch.setattr("tools.dashboard.config.DB_PATH", str(db_file))
    monkeypatch.setattr("tools.dashboard.auth.DB_PATH", str(db_file))

    return db_file


@pytest.fixture()
def auth(tmp_db):
    """Import auth module after DB_PATH is patched."""
    import tools.dashboard.auth as auth_mod
    return auth_mod


@pytest.fixture()
def flask_app(auth):
    """Create a minimal Flask app with auth registered."""
    app = Flask(__name__)
    app.config["TESTING"] = True

    @app.route("/")
    def index():
        return "home"

    @app.route("/admin-only")
    @auth.require_role("admin")
    def admin_only():
        return "admin area"

    @app.route("/dev-or-pm")
    @auth.require_role("developer", "pm")
    def dev_or_pm():
        return "dev/pm area"

    auth.register_dashboard_auth(app)
    return app


# ===================================================================
# 1. Key generation format and uniqueness
# ===================================================================

class TestKeyGeneration:
    def test_key_starts_with_prefix(self, auth):
        key = auth.generate_api_key()
        assert key.startswith("icdev_dash_")

    def test_key_hex_portion_length(self, auth):
        key = auth.generate_api_key()
        hex_part = key[len("icdev_dash_"):]
        assert len(hex_part) == 64  # 32 bytes = 64 hex chars

    def test_key_hex_portion_is_valid_hex(self, auth):
        key = auth.generate_api_key()
        hex_part = key[len("icdev_dash_"):]
        int(hex_part, 16)  # Should not raise

    def test_keys_are_unique(self, auth):
        keys = {auth.generate_api_key() for _ in range(50)}
        assert len(keys) == 50


# ===================================================================
# 2. Key hashing determinism
# ===================================================================

class TestKeyHashing:
    def test_hash_is_deterministic(self, auth):
        key = "icdev_dash_abcdef1234567890abcdef1234567890abcdef1234567890abcdef12345678"
        assert auth.hash_api_key(key) == auth.hash_api_key(key)

    def test_hash_is_sha256_hex(self, auth):
        hashed = auth.hash_api_key("test_key")
        assert len(hashed) == 64
        int(hashed, 16)  # valid hex

    def test_different_keys_produce_different_hashes(self, auth):
        h1 = auth.hash_api_key("key_a")
        h2 = auth.hash_api_key("key_b")
        assert h1 != h2


# ===================================================================
# 3. Key prefix extraction
# ===================================================================

class TestKeyPrefix:
    def test_prefix_is_first_8_hex_chars(self, auth):
        key = "icdev_dash_abcdef1234567890"
        assert auth.key_prefix(key) == "abcdef12"

    def test_prefix_short_hex(self, auth):
        key = "icdev_dash_abc"
        assert auth.key_prefix(key) == "abc"

    def test_prefix_from_generated_key(self, auth):
        key = auth.generate_api_key()
        prefix = auth.key_prefix(key)
        hex_part = key[len("icdev_dash_"):]
        assert prefix == hex_part[:8]


# ===================================================================
# 4. User CRUD (create, list, suspend, reactivate)
# ===================================================================

class TestUserCRUD:
    def test_create_user_returns_dict(self, auth):
        user = auth.create_user("alice@example.mil", "Alice", role="developer")
        assert user["email"] == "alice@example.mil"
        assert user["display_name"] == "Alice"
        assert user["role"] == "developer"
        assert user["status"] == "active"
        assert "id" in user

    def test_create_user_default_role(self, auth):
        user = auth.create_user("bob@example.mil", "Bob")
        assert user["role"] == "developer"

    def test_create_user_duplicate_email_raises(self, auth):
        auth.create_user("dup@example.mil", "Dup1")
        with pytest.raises(sqlite3.IntegrityError):
            auth.create_user("dup@example.mil", "Dup2")

    def test_list_users_returns_all(self, auth):
        auth.create_user("u1@example.mil", "U1")
        auth.create_user("u2@example.mil", "U2")
        users = auth.list_users()
        emails = {u["email"] for u in users}
        assert "u1@example.mil" in emails
        assert "u2@example.mil" in emails

    def test_list_users_filter_by_status(self, auth):
        user = auth.create_user("filter@example.mil", "Filter")
        auth.suspend_user(user["id"])
        active_users = auth.list_users(status="active")
        suspended_users = auth.list_users(status="suspended")
        assert all(u["status"] == "active" for u in active_users)
        assert any(u["email"] == "filter@example.mil" for u in suspended_users)

    def test_suspend_user(self, auth):
        user = auth.create_user("suspend@example.mil", "Suspend")
        auth.suspend_user(user["id"])
        fetched = auth.get_user_by_id(user["id"])
        assert fetched["status"] == "suspended"

    def test_reactivate_user(self, auth):
        user = auth.create_user("react@example.mil", "Reactivate")
        auth.suspend_user(user["id"])
        auth.reactivate_user(user["id"])
        fetched = auth.get_user_by_id(user["id"])
        assert fetched["status"] == "active"


# ===================================================================
# 5. API key creation and validation
# ===================================================================

class TestAPIKeyCreationValidation:
    def test_create_key_returns_raw_key_and_prefix(self, auth):
        user = auth.create_user("keytest@example.mil", "KeyTest")
        key_info = auth.create_api_key_for_user(user["id"], label="test key")
        assert key_info["raw_key"].startswith("icdev_dash_")
        assert len(key_info["prefix"]) == 8
        assert "key_id" in key_info

    def test_validate_valid_key_returns_user(self, auth):
        user = auth.create_user("valid@example.mil", "Valid")
        key_info = auth.create_api_key_for_user(user["id"])
        result = auth.validate_api_key(key_info["raw_key"])
        assert result is not None
        assert result["email"] == "valid@example.mil"
        assert result["role"] == "developer"


# ===================================================================
# 6. Key revocation
# ===================================================================

class TestKeyRevocation:
    def test_revoked_key_fails_validation(self, auth):
        user = auth.create_user("revoke@example.mil", "Revoke")
        key_info = auth.create_api_key_for_user(user["id"])
        auth.revoke_api_key(key_info["key_id"], revoked_by="admin")
        result = auth.validate_api_key(key_info["raw_key"])
        assert result is None

    def test_list_keys_shows_revoked_status(self, auth):
        user = auth.create_user("listr@example.mil", "ListR")
        key_info = auth.create_api_key_for_user(user["id"], label="will_revoke")
        auth.revoke_api_key(key_info["key_id"], revoked_by="admin")
        keys = auth.list_api_keys_for_user(user["id"])
        revoked = [k for k in keys if k["id"] == key_info["key_id"]]
        assert len(revoked) == 1
        assert revoked[0]["status"] == "revoked"
        assert revoked[0]["revoked_at"] is not None


# ===================================================================
# 7. Expired key rejection
# ===================================================================

class TestExpiredKey:
    def test_expired_key_returns_none(self, auth):
        user = auth.create_user("expired@example.mil", "Expired")
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        key_info = auth.create_api_key_for_user(
            user["id"], label="expired", expires_at=past
        )
        result = auth.validate_api_key(key_info["raw_key"])
        assert result is None

    def test_future_expiry_key_succeeds(self, auth):
        user = auth.create_user("future@example.mil", "Future")
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        key_info = auth.create_api_key_for_user(
            user["id"], label="future", expires_at=future
        )
        result = auth.validate_api_key(key_info["raw_key"])
        assert result is not None
        assert result["email"] == "future@example.mil"


# ===================================================================
# 8. Suspended user rejection
# ===================================================================

class TestSuspendedUserRejection:
    def test_suspended_user_key_fails_validation(self, auth):
        user = auth.create_user("susp@example.mil", "Suspended")
        key_info = auth.create_api_key_for_user(user["id"])
        auth.suspend_user(user["id"])
        result = auth.validate_api_key(key_info["raw_key"])
        assert result is None


# ===================================================================
# 9. Invalid key rejection
# ===================================================================

class TestInvalidKeyRejection:
    def test_none_key_returns_none(self, auth):
        assert auth.validate_api_key(None) is None

    def test_empty_string_returns_none(self, auth):
        assert auth.validate_api_key("") is None

    def test_wrong_prefix_returns_none(self, auth):
        assert auth.validate_api_key("wrong_prefix_abc123") is None

    def test_nonexistent_key_returns_none(self, auth):
        fake = "icdev_dash_" + "ff" * 32
        assert auth.validate_api_key(fake) is None


# ===================================================================
# 10. Auth event logging
# ===================================================================

class TestAuthEventLogging:
    def test_log_auth_event_inserts_row(self, auth, tmp_db):
        auth.log_auth_event(
            "user-123", "login_success",
            ip_address="10.0.0.1",
            user_agent="TestAgent/1.0",
            details="test detail",
        )
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM dashboard_auth_log WHERE user_id = 'user-123'"
        ).fetchall()
        conn.close()
        assert len(rows) >= 1
        row = rows[-1]
        assert row["event_type"] == "login_success"
        assert row["ip_address"] == "10.0.0.1"
        assert row["user_agent"] == "TestAgent/1.0"
        assert row["details"] == "test detail"

    def test_user_create_logs_event(self, auth, tmp_db):
        user = auth.create_user("logme@example.mil", "Logger")
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM dashboard_auth_log WHERE user_id = ? AND event_type = 'user_created'",
            (user["id"],),
        ).fetchall()
        conn.close()
        assert len(rows) == 1


# ===================================================================
# 11. RBAC matrix coverage
# ===================================================================

class TestRBACMatrix:
    def test_all_roles_can_access_home(self, auth):
        expected_roles = {"admin", "pm", "developer", "isso", "co", "cor"}
        assert auth.RBAC_MATRIX["home"] == expected_roles

    def test_gateway_is_admin_and_isso_only(self, auth):
        assert auth.RBAC_MATRIX["gateway"] == {"admin", "isso"}

    def test_admin_page_is_admin_only(self, auth):
        assert auth.RBAC_MATRIX["admin"] == {"admin"}

    def test_all_matrix_entries_have_valid_roles(self, auth):
        valid_roles = {"admin", "pm", "developer", "isso", "co", "cor"}
        for page, roles in auth.RBAC_MATRIX.items():
            assert roles.issubset(valid_roles), f"Page '{page}' has invalid roles: {roles - valid_roles}"


# ===================================================================
# 12. require_role decorator (allow/deny)
# ===================================================================

class TestRequireRoleDecorator:
    def test_allowed_role_passes(self, auth, flask_app):
        user = auth.create_user("admin@example.mil", "Admin", role="admin")
        with flask_app.test_request_context("/admin-only"):
            g.current_user = user
            # Calling the view function directly after decorator
            with flask_app.test_client() as client:
                with client.session_transaction() as sess:
                    sess["user_id"] = user["id"]
                # We test via the Flask test client with a session
                # But require_role checks g.current_user which is set by
                # before_request, so we need to use the API key approach
                key_info = auth.create_api_key_for_user(user["id"])
                resp = client.get(
                    "/admin-only",
                    headers={"Authorization": f"Bearer {key_info['raw_key']}"},
                )
                assert resp.status_code == 200

    def test_denied_role_gets_403(self, auth, flask_app):
        user = auth.create_user("dev403@example.mil", "Dev", role="developer")
        key_info = auth.create_api_key_for_user(user["id"])
        with flask_app.test_client() as client:
            resp = client.get(
                "/admin-only",
                headers={"Authorization": f"Bearer {key_info['raw_key']}"},
            )
            assert resp.status_code == 403

    def test_no_user_gets_401(self, auth, flask_app):
        with flask_app.test_client() as client:
            resp = client.get(
                "/admin-only",
                headers={
                    "Authorization": "Bearer icdev_dash_invalid",
                    "Content-Type": "application/json",
                },
            )
            assert resp.status_code == 401

    def test_multiple_allowed_roles(self, auth, flask_app):
        pm = auth.create_user("pm@example.mil", "PM", role="pm")
        key_info = auth.create_api_key_for_user(pm["id"])
        with flask_app.test_client() as client:
            resp = client.get(
                "/dev-or-pm",
                headers={"Authorization": f"Bearer {key_info['raw_key']}"},
            )
            assert resp.status_code == 200


# ===================================================================
# 13. bootstrap_admin CLI function
# ===================================================================

class TestBootstrapAdmin:
    def test_bootstrap_creates_admin_user(self, auth):
        user, raw_key = auth.bootstrap_admin("root@example.mil", "Root Admin")
        assert user["email"] == "root@example.mil"
        assert user["role"] == "admin"
        assert user["status"] == "active"
        assert user["display_name"] == "Root Admin"

    def test_bootstrap_returns_valid_api_key(self, auth):
        user, raw_key = auth.bootstrap_admin("boot@example.mil")
        assert raw_key.startswith("icdev_dash_")
        validated = auth.validate_api_key(raw_key)
        assert validated is not None
        assert validated["email"] == "boot@example.mil"
        assert validated["role"] == "admin"

    def test_bootstrap_default_display_name(self, auth):
        user, _ = auth.bootstrap_admin("default@example.mil")
        assert user["display_name"] == "Admin"


# ===================================================================
# 14. register_dashboard_auth sets secret_key
# ===================================================================

class TestRegisterDashboardAuth:
    def test_sets_secret_key_from_config(self, auth, monkeypatch):
        monkeypatch.setattr("tools.dashboard.auth.DASHBOARD_SECRET", "my-secret-123")
        app = Flask(__name__)
        auth.register_dashboard_auth(app)
        assert app.secret_key == "my-secret-123"

    def test_auto_generates_secret_when_empty(self, auth, monkeypatch):
        monkeypatch.setattr("tools.dashboard.auth.DASHBOARD_SECRET", "")
        app = Flask(__name__)
        auth.register_dashboard_auth(app)
        assert app.secret_key is not None
        assert len(app.secret_key) > 0

    def test_security_headers_added(self, auth, flask_app):
        user = auth.create_user("hdr@example.mil", "Hdr", role="admin")
        key_info = auth.create_api_key_for_user(user["id"])
        with flask_app.test_client() as client:
            resp = client.get(
                "/",
                headers={"Authorization": f"Bearer {key_info['raw_key']}"},
            )
            assert resp.headers.get("X-Content-Type-Options") == "nosniff"
            assert resp.headers.get("X-Frame-Options") == "DENY"
            assert resp.headers.get("X-XSS-Protection") == "1; mode=block"
            assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"

    def test_list_api_keys_excludes_raw_hash(self, auth):
        """Ensure list_api_keys_for_user never exposes the key_hash."""
        user = auth.create_user("nohash@example.mil", "NoHash")
        auth.create_api_key_for_user(user["id"], label="safe")
        keys = auth.list_api_keys_for_user(user["id"])
        assert len(keys) == 1
        # The returned dict should NOT contain key_hash
        assert "key_hash" not in keys[0]
        # But should contain safe fields
        assert "key_prefix" in keys[0]
        assert "label" in keys[0]
        assert "status" in keys[0]
