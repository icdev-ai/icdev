# CUI // SP-CTI
"""
Tests for Dashboard Kanban Board (Issue #3).
Verifies the index route returns project list and stat bar context variables.

Run: pytest tests/test_dashboard_kanban.py -v
"""

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def dashboard_app(tmp_path):
    """Create a test Flask app with a temporary database."""
    db_path = str(tmp_path / "test_icdev.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT,
            description TEXT,
            type TEXT,
            classification TEXT,
            status TEXT,
            tech_stack_backend TEXT,
            tech_stack_frontend TEXT,
            tech_stack_database TEXT,
            directory_path TEXT,
            created_by TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY,
            name TEXT,
            status TEXT,
            type TEXT,
            port INTEGER,
            endpoint TEXT,
            last_heartbeat TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS alerts (
            id TEXT PRIMARY KEY,
            title TEXT,
            severity TEXT,
            source TEXT,
            status TEXT,
            project_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS poam_items (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            title TEXT,
            severity TEXT,
            status TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS audit_trail (
            id TEXT PRIMARY KEY,
            event_type TEXT,
            actor TEXT,
            action TEXT,
            project_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS stig_findings (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            severity TEXT,
            status TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS deployments (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            status TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS a2a_tasks (
            id TEXT PRIMARY KEY,
            target_agent_id TEXT,
            status TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    # Seed test data
    ins_proj = (
        "INSERT INTO projects (id, name, type, status, classification)"
        " VALUES (?, ?, ?, ?, ?)"
    )
    conn.execute(
        ins_proj, ("proj-001", "Alpha System", "microservice", "active", "CUI"),
    )
    conn.execute(
        ins_proj, ("proj-002", "Bravo Platform", "api", "planning", "UNCLASSIFIED"),
    )
    conn.execute(
        ins_proj, ("proj-003", "Charlie App", "web-app", "completed", "CUI"),
    )
    conn.execute(
        "INSERT INTO agents (id, name, status)"
        " VALUES (?, ?, ?)",
        ("agent-1", "Builder", "active"),
    )
    conn.execute(
        "INSERT INTO agents (id, name, status)"
        " VALUES (?, ?, ?)",
        ("agent-2", "Monitor", "inactive"),
    )
    conn.execute(
        "INSERT INTO alerts (id, title, severity, source, status)"
        " VALUES (?, ?, ?, ?, ?)",
        ("alert-1", "High CPU", "critical", "monitor", "firing"),
    )
    conn.execute(
        "INSERT INTO poam_items"
        " (id, project_id, title, severity, status)"
        " VALUES (?, ?, ?, ?, ?)",
        ("poam-1", "proj-001", "Patch needed", "high", "open"),
    )
    conn.commit()
    conn.close()

    with patch("tools.dashboard.config.DB_PATH", db_path), \
         patch("tools.dashboard.api.projects.DB_PATH", db_path):
        from tools.dashboard.app import create_app
        app = create_app()
        app.config["TESTING"] = True
        yield app


@pytest.fixture
def client(dashboard_app):
    """Create test client."""
    return dashboard_app.test_client()


class TestIndexRoute:
    """Test the dashboard home page (/) returns Kanban board context."""

    def test_index_returns_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_index_contains_kanban_board(self, client):
        resp = client.get("/")
        html = resp.data.decode("utf-8")
        assert "kanban-board" in html

    def test_index_contains_projects_in_columns(self, client):
        resp = client.get("/")
        html = resp.data.decode("utf-8")
        # Projects should appear in the rendered HTML
        assert "Alpha System" in html
        assert "Bravo Platform" in html
        assert "Charlie App" in html

    def test_index_contains_stat_bar(self, client):
        resp = client.get("/")
        html = resp.data.decode("utf-8")
        # Stat bar should show agents, alerts, POAM counts
        assert "Active Agents" in html or "active-agents" in html.lower()
        assert "Firing Alerts" in html or "firing-alerts" in html.lower()

    def test_index_contains_status_columns(self, client):
        resp = client.get("/")
        html = resp.data.decode("utf-8")
        assert "Planning" in html
        assert "Active" in html
        assert "Completed" in html

    def test_index_project_links_to_detail(self, client):
        resp = client.get("/")
        html = resp.data.decode("utf-8")
        assert "/projects/proj-001" in html
        assert "/projects/proj-002" in html
        assert "/projects/proj-003" in html

    def test_index_preserves_charts_section(self, client):
        resp = client.get("/")
        html = resp.data.decode("utf-8")
        assert "chart-compliance" in html
        assert "chart-alerts" in html

    def test_index_preserves_alerts_table(self, client):
        resp = client.get("/")
        html = resp.data.decode("utf-8")
        assert "Recent Alerts" in html

    def test_index_preserves_activity_table(self, client):
        resp = client.get("/")
        html = resp.data.decode("utf-8")
        assert "Recent Activity" in html


class TestIndexEmptyState:
    """Test the dashboard with no projects in the database."""

    @pytest.fixture
    def empty_app(self, tmp_path):
        db_path = str(tmp_path / "empty_icdev.db")
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY, name TEXT, description TEXT, type TEXT,
                classification TEXT, status TEXT, tech_stack_backend TEXT,
                tech_stack_frontend TEXT, tech_stack_database TEXT,
                directory_path TEXT, created_by TEXT,
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
                status TEXT, project_id TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
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
        """)
        conn.commit()
        conn.close()

        with patch("tools.dashboard.config.DB_PATH", db_path), \
             patch("tools.dashboard.api.projects.DB_PATH", db_path):
            from tools.dashboard.app import create_app
            app = create_app()
            app.config["TESTING"] = True
            yield app

    def test_empty_state_returns_200(self, empty_app):
        client = empty_app.test_client()
        resp = client.get("/")
        assert resp.status_code == 200

    def test_empty_state_shows_kanban(self, empty_app):
        client = empty_app.test_client()
        resp = client.get("/")
        html = resp.data.decode("utf-8")
        assert "kanban-board" in html


# CUI // SP-CTI
