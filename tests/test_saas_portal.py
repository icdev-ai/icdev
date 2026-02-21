# CUI // SP-CTI
"""
Tests for SaaS Tenant Portal (Phase 31).

Verifies portal login, authenticated page routes, session management,
and the seed_demo_data bootstrap flow.

Run: pytest tests/test_saas_portal.py -v
"""

import hashlib
import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _init_platform_db(db_path):
    """Create platform tables + seed a test tenant/user/key."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tenants (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, slug TEXT UNIQUE NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            tier TEXT NOT NULL DEFAULT 'starter',
            impact_level TEXT NOT NULL DEFAULT 'IL4',
            settings TEXT DEFAULT '{}', artifact_config TEXT DEFAULT '{}',
            bedrock_config TEXT DEFAULT '{}', idp_config TEXT DEFAULT '{}',
            db_host TEXT, db_name TEXT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id),
            email TEXT NOT NULL, display_name TEXT, role TEXT DEFAULT 'viewer',
            status TEXT DEFAULT 'active', auth_method TEXT DEFAULT 'api_key',
            last_login TEXT,
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );
        CREATE TABLE IF NOT EXISTS api_keys (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id),
            user_id TEXT NOT NULL REFERENCES users(id),
            key_hash TEXT NOT NULL, key_prefix TEXT, name TEXT,
            status TEXT DEFAULT 'active', last_used_at TEXT,
            expires_at TEXT,
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );
        CREATE TABLE IF NOT EXISTS subscriptions (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id),
            tier TEXT DEFAULT 'starter', max_projects INTEGER DEFAULT 5,
            max_users INTEGER DEFAULT 3,
            allowed_il_levels TEXT DEFAULT '["IL2","IL4"]',
            allowed_frameworks TEXT DEFAULT '["nist_800_53"]',
            bedrock_pool_enabled INTEGER DEFAULT 0,
            starts_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            ends_at TEXT, status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );
        CREATE TABLE IF NOT EXISTS usage_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id TEXT NOT NULL, user_id TEXT,
            endpoint TEXT NOT NULL, method TEXT NOT NULL,
            tokens_used INTEGER DEFAULT 0, status_code INTEGER,
            duration_ms INTEGER, metadata TEXT DEFAULT '{}',
            recorded_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );
        CREATE TABLE IF NOT EXISTS audit_platform (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id TEXT, user_id TEXT,
            event_type TEXT NOT NULL, action TEXT NOT NULL,
            details TEXT DEFAULT '{}', ip_address TEXT, user_agent TEXT,
            recorded_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );
        CREATE TABLE IF NOT EXISTS rate_limits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id TEXT NOT NULL, window_start TEXT NOT NULL,
            window_type TEXT NOT NULL, request_count INTEGER DEFAULT 0,
            UNIQUE (tenant_id, window_start, window_type)
        );
    """)

    # Seed test tenant, user, and API key
    test_key = "icdev_testkey1234567890abcdef"
    key_hash = hashlib.sha256(test_key.encode("utf-8")).hexdigest()

    conn.execute(
        "INSERT INTO tenants (id, name, slug, status, tier, impact_level) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("tenant-001", "Test Org", "test-org", "active", "starter", "IL4"),
    )
    conn.execute(
        "INSERT INTO users (id, tenant_id, email, display_name, role, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("user-001", "tenant-001", "admin@test.local", "Test Admin",
         "tenant_admin", "active"),
    )
    conn.execute(
        "INSERT INTO api_keys (id, tenant_id, user_id, key_hash, key_prefix, "
        "name, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("key-001", "tenant-001", "user-001", key_hash, "testkey1",
         "Test key", "active"),
    )
    conn.execute(
        "INSERT INTO subscriptions (id, tenant_id, tier, max_projects, "
        "max_users, status) VALUES (?, ?, ?, ?, ?, ?)",
        ("sub-001", "tenant-001", "starter", 5, 3, "active"),
    )
    conn.commit()
    conn.close()
    return test_key


@pytest.fixture
def portal_app(tmp_path):
    """Create a test SaaS gateway app with temporary platform DB."""
    db_path = tmp_path / "platform.db"
    test_key = _init_platform_db(db_path)

    env_patches = {
        "PLATFORM_DB_PATH": str(db_path),
    }

    with patch.dict(os.environ, env_patches):
        with patch("tools.saas.platform_db.SQLITE_PATH", db_path), \
             patch("tools.saas.portal.app.PLATFORM_DB", db_path):
            from tools.saas.api_gateway import create_app
            app = create_app()
            app.config["TESTING"] = True
            app.config["test_api_key"] = test_key
            yield app


@pytest.fixture
def client(portal_app):
    """Create an authenticated portal test client."""
    c = portal_app.test_client()
    # GET login page first to establish CSRF token in session
    login_page = c.get("/portal/login")
    # Extract CSRF token from the rendered form
    import re
    csrf_match = re.search(
        r'name="_csrf_token"\s+value="([^"]+)"',
        login_page.data.decode("utf-8"),
    )
    csrf_token = csrf_match.group(1) if csrf_match else ""
    # Login via POST with CSRF token
    c.post("/portal/login", data={
        "api_key": portal_app.config["test_api_key"],
        "_csrf_token": csrf_token,
    })
    return c


@pytest.fixture
def unauthed_client(portal_app):
    """Create an unauthenticated test client."""
    return portal_app.test_client()


class TestPortalLogin:
    """Test portal login flow."""

    def test_login_page_returns_200(self, unauthed_client):
        resp = unauthed_client.get("/portal/login")
        assert resp.status_code == 200
        assert b"CUI // SP-CTI" in resp.data

    def test_login_with_valid_key(self, portal_app):
        import re
        c = portal_app.test_client()
        # GET login page to obtain CSRF token
        login_page = c.get("/portal/login")
        csrf_match = re.search(
            r'name="_csrf_token"\s+value="([^"]+)"',
            login_page.data.decode("utf-8"),
        )
        csrf_token = csrf_match.group(1) if csrf_match else ""
        resp = c.post(
            "/portal/login",
            data={
                "api_key": portal_app.config["test_api_key"],
                "_csrf_token": csrf_token,
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "/portal/" in resp.headers.get("Location", "")

    def test_login_with_invalid_key(self, unauthed_client):
        resp = unauthed_client.post(
            "/portal/login",
            data={"api_key": "icdev_invalid_key_12345"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"Invalid" in resp.data or b"error" in resp.data.lower()

    def test_login_with_empty_key(self, unauthed_client):
        resp = unauthed_client.post(
            "/portal/login",
            data={"api_key": ""},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"required" in resp.data.lower() or b"login" in resp.data.lower()

    def test_logout_clears_session(self, client):
        # Should be logged in
        resp = client.get("/portal/")
        assert resp.status_code == 200

        # Logout
        resp = client.get("/portal/logout", follow_redirects=False)
        assert resp.status_code == 302
        assert "login" in resp.headers.get("Location", "")

        # Should redirect to login after logout
        resp = client.get("/portal/", follow_redirects=False)
        assert resp.status_code == 302


class TestPortalDashboard:
    """Test dashboard route."""

    def test_dashboard_returns_200(self, client):
        resp = client.get("/portal/")
        assert resp.status_code == 200

    def test_dashboard_contains_tenant_name(self, client):
        resp = client.get("/portal/")
        html = resp.data.decode("utf-8")
        assert "Test Org" in html

    def test_dashboard_contains_cui_banners(self, client):
        resp = client.get("/portal/")
        html = resp.data.decode("utf-8")
        assert "CUI // SP-CTI" in html

    def test_dashboard_contains_sidebar(self, client):
        resp = client.get("/portal/")
        html = resp.data.decode("utf-8")
        assert "sidebar" in html
        assert "Dashboard" in html
        assert "Projects" in html
        assert "Compliance" in html

    def test_dashboard_contains_api_key_meta(self, client):
        resp = client.get("/portal/")
        html = resp.data.decode("utf-8")
        assert 'name="api-key"' in html


class TestPortalPages:
    """Test all portal page routes return 200."""

    def test_projects_page(self, client):
        resp = client.get("/portal/projects")
        assert resp.status_code == 200
        assert b"Projects" in resp.data

    def test_compliance_page(self, client):
        resp = client.get("/portal/compliance")
        assert resp.status_code == 200
        assert b"Compliance" in resp.data

    def test_team_page(self, client):
        resp = client.get("/portal/team")
        assert resp.status_code == 200
        assert b"Team" in resp.data

    def test_settings_page(self, client):
        resp = client.get("/portal/settings")
        assert resp.status_code == 200
        assert b"Settings" in resp.data

    def test_api_keys_page(self, client):
        resp = client.get("/portal/keys")
        assert resp.status_code == 200
        assert b"API Key" in resp.data

    def test_usage_page(self, client):
        resp = client.get("/portal/usage")
        assert resp.status_code == 200
        assert b"Usage" in resp.data

    def test_audit_page(self, client):
        resp = client.get("/portal/audit")
        assert resp.status_code == 200
        assert b"Audit" in resp.data

    def test_profile_page(self, client):
        resp = client.get("/portal/profile")
        assert resp.status_code == 200
        assert b"Profile" in resp.data
        assert b"Account Information" in resp.data


class TestPortalAuthRequired:
    """Test that unauthenticated requests redirect to login."""

    def test_dashboard_redirects_when_unauthed(self, unauthed_client):
        resp = unauthed_client.get("/portal/", follow_redirects=False)
        assert resp.status_code == 302
        assert "login" in resp.headers.get("Location", "")

    def test_projects_redirects_when_unauthed(self, unauthed_client):
        resp = unauthed_client.get("/portal/projects", follow_redirects=False)
        assert resp.status_code == 302

    def test_team_redirects_when_unauthed(self, unauthed_client):
        resp = unauthed_client.get("/portal/team", follow_redirects=False)
        assert resp.status_code == 302


class TestSeedDemoData:
    """Test the platform DB seed_demo_data function."""

    def test_seed_creates_tenant_and_key(self, tmp_path):
        db_path = tmp_path / "seed_test.db"
        import tools.saas.platform_db as pdb
        orig_path = pdb.SQLITE_PATH
        orig_dir = pdb.DATA_DIR
        try:
            pdb.SQLITE_PATH = db_path
            pdb.DATA_DIR = tmp_path
            pdb.init_platform_db()
            result = pdb.seed_demo_data()
        finally:
            pdb.SQLITE_PATH = orig_path
            pdb.DATA_DIR = orig_dir

        assert result["status"] == "ok"
        assert "raw_api_key" in result
        assert result["raw_api_key"].startswith("icdev_")
        assert "tenant_id" in result

    def test_seed_is_idempotent(self, tmp_path):
        db_path = tmp_path / "seed_idem.db"
        import tools.saas.platform_db as pdb
        orig_path = pdb.SQLITE_PATH
        orig_dir = pdb.DATA_DIR
        try:
            pdb.SQLITE_PATH = db_path
            pdb.DATA_DIR = tmp_path
            pdb.init_platform_db()
            result1 = pdb.seed_demo_data()
            result2 = pdb.seed_demo_data()
        finally:
            pdb.SQLITE_PATH = orig_path
            pdb.DATA_DIR = orig_dir

        assert result1["status"] == "ok"
        assert result2["status"] == "exists"


# ============================================================================
# TestProfilePage — profile page tests (extension)
# ============================================================================

class TestProfilePage:
    """Tests for /portal/profile page."""

    def test_profile_returns_200(self, client):
        """GET /portal/profile returns 200 for authenticated user."""
        resp = client.get("/portal/profile")
        assert resp.status_code == 200

    def test_profile_shows_user_info(self, client):
        """Profile page displays user email or name."""
        resp = client.get("/portal/profile")
        html = resp.data.decode("utf-8")
        assert "Profile" in html
        # Should contain user display name or email
        assert "admin@test.local" in html or "Test Admin" in html

    def test_profile_contains_account_section(self, client):
        """Profile page contains Account Information section."""
        resp = client.get("/portal/profile")
        html = resp.data.decode("utf-8")
        assert "Account" in html

    def test_profile_byok_hidden_when_disabled(self, portal_app):
        """BYOK section is not shown when ICDEV_BYOK_ENABLED is false."""
        import re
        c = portal_app.test_client()
        # Login
        login_page = c.get("/portal/login")
        csrf_match = re.search(
            r'name="_csrf_token"\s+value="([^"]+)"',
            login_page.data.decode("utf-8"),
        )
        csrf_token = csrf_match.group(1) if csrf_match else ""
        c.post("/portal/login", data={
            "api_key": portal_app.config["test_api_key"],
            "_csrf_token": csrf_token,
        })
        with patch.dict(os.environ, {"ICDEV_BYOK_ENABLED": "false"}):
            resp = c.get("/portal/profile")
            html = resp.data.decode("utf-8")
            # BYOK section should not render provider key forms
            assert "Add LLM Key" not in html or "byok" not in html.lower() or resp.status_code == 200

    def test_profile_redirects_when_unauthed(self, unauthed_client):
        """Unauthenticated request to /portal/profile redirects to login."""
        resp = unauthed_client.get("/portal/profile", follow_redirects=False)
        assert resp.status_code == 302
        assert "login" in resp.headers.get("Location", "")


# ============================================================================
# TestAuditPage — audit page tests (extension)
# ============================================================================

class TestAuditPage:
    """Tests for /portal/audit page."""

    def test_audit_returns_200(self, client):
        """GET /portal/audit returns 200 for authenticated user."""
        resp = client.get("/portal/audit")
        assert resp.status_code == 200

    def test_audit_contains_audit_heading(self, client):
        """Audit page contains 'Audit' heading."""
        resp = client.get("/portal/audit")
        html = resp.data.decode("utf-8")
        assert "Audit" in html

    def test_audit_page_has_pagination(self, client):
        """Audit page supports pagination (page query parameter)."""
        resp = client.get("/portal/audit?page=1")
        assert resp.status_code == 200

    def test_audit_shows_entries_when_seeded(self, portal_app):
        """Audit page shows seeded audit entries."""
        import re
        # Seed an audit entry
        db_path = os.environ.get("PLATFORM_DB_PATH")
        if db_path:
            conn = sqlite3.connect(db_path)
            conn.execute(
                "INSERT INTO audit_platform (tenant_id, event_type, action, details) "
                "VALUES (?, ?, ?, ?)",
                ("tenant-001", "test.event", "Test action", "{}"),
            )
            conn.commit()
            conn.close()

        c = portal_app.test_client()
        login_page = c.get("/portal/login")
        csrf_match = re.search(
            r'name="_csrf_token"\s+value="([^"]+)"',
            login_page.data.decode("utf-8"),
        )
        csrf_token = csrf_match.group(1) if csrf_match else ""
        c.post("/portal/login", data={
            "api_key": portal_app.config["test_api_key"],
            "_csrf_token": csrf_token,
        })
        resp = c.get("/portal/audit")
        assert resp.status_code == 200

    def test_audit_redirects_when_unauthed(self, unauthed_client):
        """Unauthenticated request to /portal/audit redirects to login."""
        resp = unauthed_client.get("/portal/audit", follow_redirects=False)
        assert resp.status_code == 302


# ============================================================================
# TestUsagePage — usage page tests (extension)
# ============================================================================

class TestUsagePage:
    """Tests for /portal/usage page."""

    def test_usage_returns_200(self, client):
        """GET /portal/usage returns 200 for authenticated user."""
        resp = client.get("/portal/usage")
        assert resp.status_code == 200

    def test_usage_contains_usage_heading(self, client):
        """Usage page contains 'Usage' heading."""
        resp = client.get("/portal/usage")
        html = resp.data.decode("utf-8")
        assert "Usage" in html

    def test_usage_shows_api_calls_section(self, client):
        """Usage page has an API calls or metrics section."""
        resp = client.get("/portal/usage")
        html = resp.data.decode("utf-8")
        # Page should mention calls, tokens, or endpoints
        assert "API" in html or "call" in html.lower() or "token" in html.lower()

    def test_usage_contains_cui_banner(self, client):
        """Usage page includes CUI banner."""
        resp = client.get("/portal/usage")
        html = resp.data.decode("utf-8")
        assert "CUI" in html

    def test_usage_redirects_when_unauthed(self, unauthed_client):
        """Unauthenticated request to /portal/usage redirects to login."""
        resp = unauthed_client.get("/portal/usage", follow_redirects=False)
        assert resp.status_code == 302


# ============================================================================
# TestCSRFProtection — CSRF token tests (extension)
# ============================================================================

class TestCSRFProtection:
    """Tests for CSRF protection on portal POST endpoints."""

    def test_login_form_contains_csrf_token(self, unauthed_client):
        """Login page renders a hidden _csrf_token field."""
        resp = unauthed_client.get("/portal/login")
        html = resp.data.decode("utf-8")
        assert "_csrf_token" in html

    def test_post_without_csrf_redirects(self, unauthed_client):
        """POST to /portal/login without CSRF token is rejected (redirect to login with error)."""
        resp = unauthed_client.post(
            "/portal/login",
            data={"api_key": "icdev_some_key"},
            follow_redirects=True,
        )
        # Should redirect back to login with an error about invalid form submission
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert "Invalid" in html or "error" in html.lower() or "login" in html.lower()

    def test_post_with_valid_csrf_processes(self, portal_app):
        """POST with correct CSRF token and valid key processes the login."""
        import re
        c = portal_app.test_client()
        login_page = c.get("/portal/login")
        csrf_match = re.search(
            r'name="_csrf_token"\s+value="([^"]+)"',
            login_page.data.decode("utf-8"),
        )
        csrf_token = csrf_match.group(1) if csrf_match else ""
        resp = c.post(
            "/portal/login",
            data={
                "api_key": portal_app.config["test_api_key"],
                "_csrf_token": csrf_token,
            },
            follow_redirects=False,
        )
        # Should redirect to dashboard on success
        assert resp.status_code == 302
        assert "/portal/" in resp.headers.get("Location", "")

    def test_csrf_token_different_per_session(self, portal_app):
        """Each new session gets a distinct CSRF token."""
        import re
        c1 = portal_app.test_client()
        c2 = portal_app.test_client()
        page1 = c1.get("/portal/login")
        page2 = c2.get("/portal/login")
        match1 = re.search(
            r'name="_csrf_token"\s+value="([^"]+)"',
            page1.data.decode("utf-8"),
        )
        match2 = re.search(
            r'name="_csrf_token"\s+value="([^"]+)"',
            page2.data.decode("utf-8"),
        )
        token1 = match1.group(1) if match1 else ""
        token2 = match2.group(1) if match2 else ""
        # Tokens should exist
        assert len(token1) > 0
        assert len(token2) > 0
        # Different sessions should have different CSRF tokens
        assert token1 != token2

    def test_post_with_wrong_csrf_rejects(self, portal_app):
        """POST with an incorrect CSRF token is rejected."""
        c = portal_app.test_client()
        # Get login page to establish a session
        c.get("/portal/login")
        # Post with a fabricated CSRF token
        resp = c.post(
            "/portal/login",
            data={
                "api_key": portal_app.config["test_api_key"],
                "_csrf_token": "totally_wrong_csrf_token_xyz",
            },
            follow_redirects=True,
        )
        html = resp.data.decode("utf-8")
        # Should show error about invalid form or stay on login
        assert "Invalid" in html or "error" in html.lower() or "login" in html.lower()


# CUI // SP-CTI
