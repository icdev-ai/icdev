# [TEMPLATE: CUI // SP-CTI]
"""
Tests for Requirements Chat API and page routes.

Verifies intake session creation, turn processing, readiness scoring,
and chat page rendering.

Run: pytest tests/test_intake_api.py -v
"""

import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.db.storage import StorageConnection  # noqa: E402  (needs sys.path above)


def _init_test_db(db_path):
    """Create tables using the real init script for full schema compatibility."""
    import subprocess

    subprocess.run(
        [sys.executable, "tools/db/init_icdev_db.py", "--db-path", str(db_path)],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True,
    )
    # The minimal schema below is applied UNCONDITIONALLY, not behind the
    # `db_path.exists()` guard it used to sit behind. File existence is not a
    # success probe: the init script connects (creating the file) before it
    # finishes populating it, so a run that fails partway leaves a file that
    # looks initialized. Locally this script produces all 528 tables and the
    # guard was harmless; on the ubuntu CI runner it left a database with no
    # `projects` table, and GET / died on `no such table: projects` — the one
    # test in this module that renders the home page. Every statement here is
    # CREATE TABLE IF NOT EXISTS, so it is a no-op when the real init succeeded.
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY, name TEXT, description TEXT, type TEXT,
            classification TEXT, impact_level TEXT, status TEXT,
            tech_stack_backend TEXT, tech_stack_frontend TEXT,
            tech_stack_database TEXT, directory_path TEXT, created_by TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY, name TEXT, status TEXT, type TEXT,
            port INTEGER, endpoint TEXT, last_heartbeat TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS alerts (
            id TEXT PRIMARY KEY, title TEXT, severity TEXT, source TEXT,
            status TEXT, project_id TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS poam_items (
            id TEXT PRIMARY KEY, project_id TEXT, title TEXT, severity TEXT,
            status TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS audit_trail (
            id TEXT PRIMARY KEY, event_type TEXT, actor TEXT, action TEXT,
            project_id TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS stig_findings (
            id TEXT PRIMARY KEY, project_id TEXT, severity TEXT, status TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS deployments (
            id TEXT PRIMARY KEY, project_id TEXT, status TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS a2a_tasks (
            id TEXT PRIMARY KEY, target_agent_id TEXT, status TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS hook_events (
            id TEXT PRIMARY KEY, event_type TEXT, source TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS nlq_queries (
            id TEXT PRIMARY KEY, query_text TEXT, sql_text TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS ssp_documents (
            id TEXT PRIMARY KEY, project_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS sbom_records (
            id TEXT PRIMARY KEY, project_id TEXT,
            generated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS self_healing_events (
            id TEXT PRIMARY KEY, pattern_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS knowledge_patterns (
            id TEXT PRIMARY KEY, description TEXT
        );
        CREATE TABLE IF NOT EXISTS failure_log (
            id TEXT PRIMARY KEY, resolved INTEGER DEFAULT 0
        );
    """)
    conn.commit()
    conn.close()

    # Ensure dashboard auth tables exist + test user (Phase 30)
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS dashboard_users (
            id TEXT PRIMARY KEY, email TEXT UNIQUE, display_name TEXT,
            role TEXT DEFAULT 'admin', status TEXT DEFAULT 'active',
            created_by TEXT, created_at TIMESTAMP, updated_at TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS dashboard_api_keys (
            id TEXT PRIMARY KEY, user_id TEXT, key_hash TEXT, key_prefix TEXT,
            label TEXT, status TEXT DEFAULT 'active', last_used_at TIMESTAMP,
            expires_at TIMESTAMP, created_at TIMESTAMP, revoked_at TIMESTAMP,
            revoked_by TEXT
        );
        CREATE TABLE IF NOT EXISTS dashboard_auth_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, event_type TEXT,
            ip_address TEXT, user_agent TEXT, details TEXT, created_at TIMESTAMP
        );
        INSERT OR IGNORE INTO dashboard_users (id, email, display_name, role)
        VALUES ('test-admin', 'admin@test.local', 'Test Admin', 'admin');
    """)
    conn.commit()
    conn.close()


@pytest.fixture(scope="module")
def chat_app(tmp_path_factory):
    """Create a test Flask app with intake tables.

    Module-scoped: create_app() mounts every blueprint in the platform, which
    costs ~2s a call, and _init_test_db shells out to the real init script.
    Paying that 22 times put this file at ~3.5 minutes, which is why it is a
    single build shared by the module. Each test still creates its own intake
    session, so they do not observe each other's rows.
    """
    db_path = tmp_path_factory.mktemp("intake_api") / "test_icdev.db"
    _init_test_db(db_path)

    # Patch _get_db in all dashboard modules to return connections to the test
    # DB.  Patching DB_PATH alone is insufficient because get_connection() may
    # resolve paths independently.
    # The connection MUST be wrapped in a real StorageConnection. Dashboard SQL
    # is authored PG-natively (`%s` placeholders — PG is the primary backend)
    # and depends on StorageConnection/StorageCursor to translate for SQLite.
    # Handing the routes a bare sqlite3.Connection made every such statement
    # raise `near "%": syntax error`; auth's before_request hook calls
    # get_user_by_id() on every request, so that single mismatch turned all 22
    # tests in this module red at once.
    def _make_conn():
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return StorageConnection(c, "sqlite")

    # tools/dashboard/app.py does `from tools.dashboard.config import DB_PATH`,
    # which binds its OWN module-level name — patching the config module alone
    # leaves app.py's closure `_get_db()` pointed at the real data/icdev.db.
    # GET / therefore read the repo database, which passes on a developer box
    # that has one and fails on a CI runner that does not (`no such table:
    # projects`). Import the module up front so the attribute exists to patch.
    import tools.dashboard.app  # noqa: F401

    with (
        patch.dict(os.environ, {"ICDEV_DB_PATH": str(db_path)}),
        patch("tools.dashboard.config.DB_PATH", str(db_path)),
        patch("tools.dashboard.app.DB_PATH", str(db_path)),
        patch("tools.dashboard.auth._get_db", side_effect=lambda: _make_conn()),
        patch("tools.dashboard.api.projects._get_db", side_effect=lambda: _make_conn()),
        patch("tools.dashboard.api.intake._get_db", side_effect=lambda: _make_conn()),
        patch("tools.dashboard.api.intake.DB_PATH", db_path),
        patch("tools.requirements.intake_engine.DB_PATH", db_path),
        # readiness_scorer resolves its own module-level DB_PATH (the repo's
        # data/icdev.db) — without this patch /api/intake/readiness queries a
        # database that has none of this test's sessions, and 500s on the
        # missing/empty file rather than scoring the session just created.
        patch("tools.requirements.readiness_scorer.DB_PATH", db_path),
        patch("tools.requirements.intake_engine._HAS_LLM", False),
    ):
        from tools.dashboard.app import create_app

        app = create_app()
        app.config["TESTING"] = True
        yield app


@pytest.fixture(scope="module")
def client(chat_app):
    """Create authenticated test client."""
    c = chat_app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = "test-admin"
    return c


class TestChatPages:
    """Test chat page routes return 200."""

    def test_chat_new_page(self, client):
        resp = client.get("/chat")
        assert resp.status_code == 200
        # Heading renamed "Chat" -> "AI Assistant" in e709aead1 (chat-ux, cu-fix).
        assert b"<h1>AI Assistant</h1>" in resp.data

    def test_chat_new_with_wizard_params(self, client):
        resp = client.get("/chat?goal=build&role=developer&classification=il4")
        assert resp.status_code == 200
        # Heading renamed "Chat" -> "AI Assistant" in e709aead1 (chat-ux, cu-fix).
        assert b"<h1>AI Assistant</h1>" in resp.data

    def test_chat_session_not_found(self, client):
        resp = client.get("/chat/nonexistent-session")
        assert resp.status_code == 404


class TestIntakeAPI:
    """Test intake API endpoints."""

    def test_create_session(self, client):
        resp = client.post(
            "/api/intake/session",
            json={"goal": "build", "role": "developer", "classification": "il4"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "session_id" in data

    def test_process_turn(self, client):
        # Create session first
        resp = client.post(
            "/api/intake/session",
            json={"goal": "build", "role": "developer", "classification": "il4"},
        )
        session_id = resp.get_json()["session_id"]

        # Send a message
        resp = client.post(
            "/api/intake/turn",
            json={"session_id": session_id, "message": "We need a mission planning tool for 200 users"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "analyst_response" in data
        assert data["status"] == "ok"

    def test_turn_requires_session_id(self, client):
        resp = client.post(
            "/api/intake/turn",
            json={"message": "hello"},
        )
        assert resp.status_code == 400

    def test_turn_requires_message(self, client):
        resp = client.post(
            "/api/intake/turn",
            json={"session_id": "some-id"},
        )
        assert resp.status_code == 400

    def test_get_session_not_found(self, client):
        resp = client.get("/api/intake/session/nonexistent")
        assert resp.status_code == 404

    def test_get_session_info(self, client):
        # Create session
        resp = client.post(
            "/api/intake/session",
            json={"goal": "comply", "role": "isso", "classification": "il5"},
        )
        session_id = resp.get_json()["session_id"]

        # Get session info
        resp = client.get(f"/api/intake/session/{session_id}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "session" in data
        assert "messages" in data
        assert isinstance(data["messages"], list)

    def test_readiness_score(self, client):
        # Create session + send message to have data
        resp = client.post(
            "/api/intake/session",
            json={"goal": "build", "role": "developer", "classification": "il4"},
        )
        session_id = resp.get_json()["session_id"]
        client.post(
            "/api/intake/turn",
            json={"session_id": session_id, "message": "We shall build a web application"},
        )

        resp = client.get(f"/api/intake/readiness/{session_id}")
        assert resp.status_code == 200
        data = resp.get_json()
        # readiness_scorer returns overall_score and dimensions
        assert "overall_score" in data or "overall" in data

    def test_upload_no_file(self, client):
        resp = client.post(
            "/api/intake/upload",
            data={"session_id": "sess-123"},
        )
        assert resp.status_code == 400

    def test_export_requirements(self, client):
        # Create session
        resp = client.post(
            "/api/intake/session",
            json={"goal": "build", "role": "developer", "classification": "il4"},
        )
        session_id = resp.get_json()["session_id"]

        resp = client.post(f"/api/intake/export/{session_id}")
        assert resp.status_code == 200


class TestFrameworksAndPersona:
    """Test framework selection, custom roles, and persona-driven sessions."""

    def test_create_session_with_frameworks(self, client):
        """Session creation passes frameworks to backend."""
        resp = client.post(
            "/api/intake/session",
            json={
                "goal": "build",
                "role": "isso",
                "classification": "il5",
                "frameworks": ["fedramp_high", "cmmc_l2", "nist_800_171"],
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "session_id" in data
        assert data.get("wizard_context", {}).get("frameworks") == ["fedramp_high", "cmmc_l2", "nist_800_171"]

    def test_create_session_with_custom_role(self, client):
        """Custom role name and description are passed to backend."""
        resp = client.post(
            "/api/intake/session",
            json={
                "goal": "build",
                "classification": "il4",
                "custom_role_name": "logistics_officer",
                "custom_role_description": "I manage supply chain operations",
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "session_id" in data
        # Welcome message should exist (either LLM or fallback)
        assert "message" in data

    def test_trigger_build_endpoint(self, client):
        """Trigger-build returns session context for build pipeline."""
        # Create session with frameworks
        resp = client.post(
            "/api/intake/session",
            json={
                "goal": "build",
                "role": "developer",
                "classification": "il5",
                "frameworks": ["fedramp_high"],
            },
        )
        session_id = resp.get_json()["session_id"]

        # Trigger build
        resp = client.post(f"/api/intake/trigger-build/{session_id}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["session_id"] == session_id
        assert "requirements_count" in data
        assert "next_steps" in data
        assert isinstance(data["next_steps"], list)

    def test_trigger_build_not_found(self, client):
        """Trigger-build returns 404 for nonexistent session."""
        resp = client.post("/api/intake/trigger-build/nonexistent")
        assert resp.status_code == 404

    def test_bdd_previews_in_turn_response(self, client):
        """Process turn response includes bdd_previews field."""
        # Create session
        resp = client.post(
            "/api/intake/session",
            json={"goal": "build", "role": "developer", "classification": "il4"},
        )
        session_id = resp.get_json()["session_id"]

        # Send a message with requirement-like content
        resp = client.post(
            "/api/intake/turn",
            json={
                "session_id": session_id,
                "message": "The system shall authenticate users via CAC/PIV",
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        # bdd_previews key should always be present (may be empty list)
        assert "bdd_previews" in data
        assert isinstance(data["bdd_previews"], list)


class TestNewRoles:
    """Test that new roles appear in wizard and role selector."""

    def test_wizard_has_new_roles(self, client):
        resp = client.get("/wizard")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Analyst" in html
        assert "Solutions Architect" in html
        assert "Sales Engineer" in html
        assert "Innovator" in html
        assert "Business Development" in html

    def test_wizard_has_framework_step(self, client):
        """Wizard should include compliance framework selection step."""
        resp = client.get("/wizard")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "compliance frameworks" in html.lower() or "FedRAMP" in html

    def test_wizard_has_custom_role(self, client):
        """Wizard should include custom role option."""
        resp = client.get("/wizard")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Custom" in html or "custom" in html

    def test_chat_passes_framework_config(self, client):
        """Chat page passes framework config to JS."""
        resp = client.get("/chat?goal=build&role=isso&classification=il5&frameworks=fedramp_high,cmmc_l2")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "wizardFrameworks" in html
        assert "fedramp_high,cmmc_l2" in html

    def test_nav_has_chat_link(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b'href="/chat"' in resp.data
