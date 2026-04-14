# [TEMPLATE: CUI // SP-CTI]
"""Tests for REST API v1 — Phase 11 Multi-Agent Architecture endpoints.

Covers the new agent/workflow/authority endpoint groups:
    1. Agent listing + single agent  (GET /api/v1/agents, GET /api/v1/agents/<id>)
    2. Agent heartbeat               (POST /api/v1/agents/<id>/heartbeat)
    3. Skill routing                 (GET /api/v1/agents/routing?skill=<skill_id>)
    4. Workflow listing + creation   (GET/POST /api/v1/workflows)
    5. Single workflow               (GET /api/v1/workflows/<id>)
    6. Authority matrix              (GET /api/v1/authority)
    7. Authority check               (POST /api/v1/authority/check)

Uses the same Flask test client / mock-auth pattern as test_rest_api.py.

NIST 800-53 controls mapped: SA-11 (developer testing), CM-3 (configuration change
control), AC-2 (account management), AU-12 (audit record generation).
"""

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from flask import Flask, g

# ---------------------------------------------------------------------------
# Platform schema (minimal subset needed for agent REST tests)
# ---------------------------------------------------------------------------
PLATFORM_SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    tier TEXT DEFAULT 'starter',
    impact_level TEXT DEFAULT 'IL4',
    status TEXT DEFAULT 'active',
    settings TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    email TEXT NOT NULL,
    display_name TEXT,
    role TEXT DEFAULT 'developer',
    auth_method TEXT DEFAULT 'api_key',
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# Agent schema — lives in the per-tenant / main icdev.db, but we share db_path for simplicity
AGENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    url TEXT NOT NULL DEFAULT 'http://localhost:8443',
    status TEXT NOT NULL DEFAULT 'active',
    capabilities TEXT,
    last_heartbeat TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_workflows (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    project_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_by TEXT DEFAULT 'orchestrator-agent',
    aggregated_result TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_subtasks (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    skill_id TEXT NOT NULL,
    description TEXT DEFAULT '',
    depends_on TEXT DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending',
    input_data TEXT,
    output_data TEXT,
    error_message TEXT DEFAULT '',
    attempt_count INTEGER DEFAULT 0,
    duration_ms INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_vetoes (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    topic TEXT NOT NULL,
    veto_type TEXT NOT NULL,
    project_id TEXT,
    reason TEXT,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS a2a_tasks (
    id TEXT PRIMARY KEY,
    source_agent TEXT,
    assigned_agent TEXT,
    status TEXT DEFAULT 'submitted',
    message TEXT,
    result TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_trail (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    project_id TEXT,
    details TEXT,
    classification TEXT DEFAULT 'CUI',
    session_id TEXT,
    source_ip TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

SEED_TENANT_ID = "tenant-agent-test-001"
SEED_USER_ID = "user-agent-test-001"
SEED_AGENT_ID = "builder-agent"
SEED_WORKFLOW_ID = "wf-test-001"


def _seed_db(conn):
    """Seed tenant, user, agent and workflow rows."""
    conn.execute(
        "INSERT OR IGNORE INTO tenants (id, name, slug, tier, impact_level, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (SEED_TENANT_ID, "Agent Test Org", "agent-test-org", "professional", "IL4", "active"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO users (id, tenant_id, email, role, display_name) "
        "VALUES (?, ?, ?, ?, ?)",
        (SEED_USER_ID, SEED_TENANT_ID, "admin@agent-test.gov", "tenant_admin", "Agent Admin"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO agents (id, name, description, url, status, capabilities) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            SEED_AGENT_ID,
            "Builder Agent",
            "TDD code generation agent",
            "https://localhost:8445",
            "active",
            json.dumps({"skills": [{"id": "code_generation", "name": "Code Generation"}]}),
        ),
    )
    conn.execute(
        "INSERT OR IGNORE INTO agent_workflows (id, name, project_id, status, created_by) "
        "VALUES (?, ?, ?, ?, ?)",
        (SEED_WORKFLOW_ID, "Build REST API", "proj-001", "pending", "orchestrator-agent"),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def agent_db_path(tmp_path):
    """Create and seed a temporary SQLite DB for agent REST tests."""
    db_path = tmp_path / "icdev_agents.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(PLATFORM_SCHEMA + AGENT_SCHEMA)
    _seed_db(conn)
    conn.close()
    return db_path


@pytest.fixture()
def rest_app(agent_db_path):
    """Flask test app with the rest_api blueprint and mock auth.

    Patches both PLATFORM_DB_PATH and the agent tools' db_path so all
    DB operations hit the test database.
    """
    app = Flask(__name__)
    app.config["TESTING"] = True

    with patch("tools.saas.rest_api.PLATFORM_DB_PATH", agent_db_path):
        from tools.saas.rest_api import api_bp

        try:
            app.register_blueprint(api_bp)
        except Exception:
            pass

        @app.before_request
        def mock_auth():
            g.tenant_id = SEED_TENANT_ID
            g.user_id = SEED_USER_ID
            g.user_role = "tenant_admin"

        yield app


@pytest.fixture()
def client(rest_app):
    """Flask test client."""
    return rest_app.test_client()


# ============================================================================
# GET /api/v1/agents
# ============================================================================


class TestListAgents:
    """Tests for GET /api/v1/agents."""

    def test_list_agents_returns_200(self, client, agent_db_path):
        """GET /api/v1/agents must return HTTP 200."""
        with patch("tools.saas.rest_api._get_agents_db_path", return_value=agent_db_path):
            resp = client.get("/api/v1/agents")
        assert resp.status_code == 200

    def test_list_agents_returns_json_with_agents_key(self, client, agent_db_path):
        """GET /api/v1/agents response must contain an 'agents' list."""
        with patch("tools.saas.rest_api._get_agents_db_path", return_value=agent_db_path):
            resp = client.get("/api/v1/agents")
        data = resp.get_json()
        assert "agents" in data
        assert isinstance(data["agents"], list)

    def test_list_agents_returns_total_count(self, client, agent_db_path):
        """GET /api/v1/agents response must contain a 'total' integer field."""
        with patch("tools.saas.rest_api._get_agents_db_path", return_value=agent_db_path):
            resp = client.get("/api/v1/agents")
        data = resp.get_json()
        assert "total" in data
        assert isinstance(data["total"], int)

    def test_list_agents_includes_seed_agent(self, client, agent_db_path):
        """Seed agent must appear in the agents list."""
        with patch("tools.saas.rest_api._get_agents_db_path", return_value=agent_db_path):
            resp = client.get("/api/v1/agents")
        data = resp.get_json()
        ids = [a["id"] for a in data["agents"]]
        assert SEED_AGENT_ID in ids


# ============================================================================
# GET /api/v1/agents/<agent_id>
# ============================================================================


class TestGetAgent:
    """Tests for GET /api/v1/agents/<agent_id>."""

    def test_get_existing_agent_returns_200(self, client, agent_db_path):
        """GET /api/v1/agents/<id> for an existing agent must return 200."""
        with patch("tools.saas.rest_api._get_agents_db_path", return_value=agent_db_path):
            resp = client.get(f"/api/v1/agents/{SEED_AGENT_ID}")
        assert resp.status_code == 200

    def test_get_existing_agent_returns_agent_data(self, client, agent_db_path):
        """GET /api/v1/agents/<id> must return an 'agent' object with id field."""
        with patch("tools.saas.rest_api._get_agents_db_path", return_value=agent_db_path):
            resp = client.get(f"/api/v1/agents/{SEED_AGENT_ID}")
        data = resp.get_json()
        assert "agent" in data
        assert data["agent"]["id"] == SEED_AGENT_ID

    def test_get_nonexistent_agent_returns_404(self, client, agent_db_path):
        """GET /api/v1/agents/<id> for unknown agent must return 404."""
        with patch("tools.saas.rest_api._get_agents_db_path", return_value=agent_db_path):
            resp = client.get("/api/v1/agents/ghost-agent-999")
        assert resp.status_code == 404

    def test_get_agent_name_matches_seed(self, client, agent_db_path):
        """Returned agent name must match the seeded value."""
        with patch("tools.saas.rest_api._get_agents_db_path", return_value=agent_db_path):
            resp = client.get(f"/api/v1/agents/{SEED_AGENT_ID}")
        data = resp.get_json()
        assert data["agent"]["name"] == "Builder Agent"


# ============================================================================
# POST /api/v1/agents/<agent_id>/heartbeat
# ============================================================================


class TestAgentHeartbeat:
    """Tests for POST /api/v1/agents/<agent_id>/heartbeat."""

    def test_heartbeat_existing_agent_returns_200(self, client, agent_db_path):
        """POST /api/v1/agents/<id>/heartbeat for existing agent must return 200."""
        with patch("tools.saas.rest_api._get_agents_db_path", return_value=agent_db_path):
            resp = client.post(f"/api/v1/agents/{SEED_AGENT_ID}/heartbeat")
        assert resp.status_code == 200

    def test_heartbeat_returns_acknowledged(self, client, agent_db_path):
        """Heartbeat response must contain acknowledged=True."""
        with patch("tools.saas.rest_api._get_agents_db_path", return_value=agent_db_path):
            resp = client.post(f"/api/v1/agents/{SEED_AGENT_ID}/heartbeat")
        data = resp.get_json()
        assert data.get("acknowledged") is True

    def test_heartbeat_unknown_agent_returns_404(self, client, agent_db_path):
        """POST heartbeat for unknown agent must return 404."""
        with patch("tools.saas.rest_api._get_agents_db_path", return_value=agent_db_path):
            resp = client.post("/api/v1/agents/ghost-agent-999/heartbeat")
        assert resp.status_code == 404


# ============================================================================
# GET /api/v1/agents/routing
# ============================================================================


class TestSkillRouting:
    """Tests for GET /api/v1/agents/routing."""

    def test_routing_without_skill_returns_200(self, client, agent_db_path):
        """GET /api/v1/agents/routing with no filter must return 200."""
        with patch("tools.saas.rest_api._get_agents_db_path", return_value=agent_db_path):
            resp = client.get("/api/v1/agents/routing")
        assert resp.status_code == 200

    def test_routing_returns_agents_key(self, client, agent_db_path):
        """Routing response must contain 'agents' key."""
        with patch("tools.saas.rest_api._get_agents_db_path", return_value=agent_db_path):
            resp = client.get("/api/v1/agents/routing")
        data = resp.get_json()
        assert "agents" in data

    def test_routing_with_skill_filter_returns_matching(self, client, agent_db_path):
        """GET /api/v1/agents/routing?skill=<id> must filter by skill capability."""
        with patch("tools.saas.rest_api._get_agents_db_path", return_value=agent_db_path):
            resp = client.get("/api/v1/agents/routing?skill=code_generation")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "agents" in data


# ============================================================================
# GET /api/v1/workflows
# ============================================================================


class TestListWorkflows:
    """Tests for GET /api/v1/workflows."""

    def test_list_workflows_returns_200(self, client, agent_db_path):
        """GET /api/v1/workflows must return 200."""
        with patch("tools.saas.rest_api._get_agents_db_path", return_value=agent_db_path):
            resp = client.get("/api/v1/workflows")
        assert resp.status_code == 200

    def test_list_workflows_returns_workflows_key(self, client, agent_db_path):
        """Workflows response must contain 'workflows' list."""
        with patch("tools.saas.rest_api._get_agents_db_path", return_value=agent_db_path):
            resp = client.get("/api/v1/workflows")
        data = resp.get_json()
        assert "workflows" in data
        assert isinstance(data["workflows"], list)

    def test_list_workflows_includes_seed_workflow(self, client, agent_db_path):
        """Seed workflow must appear in workflow listing."""
        with patch("tools.saas.rest_api._get_agents_db_path", return_value=agent_db_path):
            resp = client.get("/api/v1/workflows")
        data = resp.get_json()
        ids = [w["id"] for w in data["workflows"]]
        assert SEED_WORKFLOW_ID in ids


# ============================================================================
# GET /api/v1/workflows/<workflow_id>
# ============================================================================


class TestGetWorkflow:
    """Tests for GET /api/v1/workflows/<workflow_id>."""

    def test_get_existing_workflow_returns_200(self, client, agent_db_path):
        """GET /api/v1/workflows/<id> for existing workflow must return 200."""
        with patch("tools.saas.rest_api._get_agents_db_path", return_value=agent_db_path):
            resp = client.get(f"/api/v1/workflows/{SEED_WORKFLOW_ID}")
        assert resp.status_code == 200

    def test_get_workflow_returns_workflow_object(self, client, agent_db_path):
        """GET /api/v1/workflows/<id> must return workflow with id field."""
        with patch("tools.saas.rest_api._get_agents_db_path", return_value=agent_db_path):
            resp = client.get(f"/api/v1/workflows/{SEED_WORKFLOW_ID}")
        data = resp.get_json()
        assert "workflow" in data
        assert data["workflow"]["id"] == SEED_WORKFLOW_ID

    def test_get_nonexistent_workflow_returns_404(self, client, agent_db_path):
        """GET /api/v1/workflows/<id> for unknown ID must return 404."""
        with patch("tools.saas.rest_api._get_agents_db_path", return_value=agent_db_path):
            resp = client.get("/api/v1/workflows/ghost-wf-999")
        assert resp.status_code == 404


# ============================================================================
# POST /api/v1/workflows
# ============================================================================


class TestCreateWorkflow:
    """Tests for POST /api/v1/workflows."""

    def test_create_workflow_returns_201(self, client, agent_db_path):
        """POST /api/v1/workflows with valid body must return 201."""
        payload = {"name": "New Integration Workflow", "project_id": "proj-002"}
        with patch("tools.saas.rest_api._get_agents_db_path", return_value=agent_db_path):
            resp = client.post(
                "/api/v1/workflows",
                data=json.dumps(payload),
                content_type="application/json",
            )
        assert resp.status_code == 201

    def test_create_workflow_returns_workflow_with_id(self, client, agent_db_path):
        """Created workflow must have an 'id' field."""
        payload = {"name": "Deploy Pipeline Workflow", "project_id": "proj-003"}
        with patch("tools.saas.rest_api._get_agents_db_path", return_value=agent_db_path):
            resp = client.post(
                "/api/v1/workflows",
                data=json.dumps(payload),
                content_type="application/json",
            )
        data = resp.get_json()
        assert "workflow" in data
        assert "id" in data["workflow"]

    def test_create_workflow_missing_name_returns_400(self, client, agent_db_path):
        """POST /api/v1/workflows without 'name' must return 400."""
        payload = {"project_id": "proj-004"}
        with patch("tools.saas.rest_api._get_agents_db_path", return_value=agent_db_path):
            resp = client.post(
                "/api/v1/workflows",
                data=json.dumps(payload),
                content_type="application/json",
            )
        assert resp.status_code == 400


# ============================================================================
# GET /api/v1/authority
# ============================================================================


class TestAuthorityMatrix:
    """Tests for GET /api/v1/authority."""

    def test_get_authority_matrix_returns_200(self, client):
        """GET /api/v1/authority must return 200."""
        resp = client.get("/api/v1/authority")
        assert resp.status_code == 200

    def test_get_authority_matrix_returns_matrix_key(self, client):
        """Authority response must contain 'matrix' dict."""
        resp = client.get("/api/v1/authority")
        data = resp.get_json()
        assert "matrix" in data
        assert isinstance(data["matrix"], dict)

    def test_authority_matrix_contains_security_agent(self, client):
        """Authority matrix must include the security-agent entry."""
        resp = client.get("/api/v1/authority")
        data = resp.get_json()
        # Either loaded from YAML or falls back to defaults — security-agent must be present
        assert "security-agent" in data["matrix"]


# ============================================================================
# POST /api/v1/authority/check
# ============================================================================


class TestAuthorityCheck:
    """Tests for POST /api/v1/authority/check."""

    def test_check_authority_returns_200(self, client):
        """POST /api/v1/authority/check must return 200."""
        payload = {"agent_id": "security-agent", "topic": "code_generation"}
        resp = client.post(
            "/api/v1/authority/check",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert resp.status_code == 200

    def test_check_authority_returns_has_authority_field(self, client):
        """Authority check response must contain 'has_authority' boolean."""
        payload = {"agent_id": "security-agent", "topic": "code_generation"}
        resp = client.post(
            "/api/v1/authority/check",
            data=json.dumps(payload),
            content_type="application/json",
        )
        data = resp.get_json()
        assert "has_authority" in data
        assert isinstance(data["has_authority"], bool)

    def test_security_agent_has_authority_over_code_generation(self, client):
        """security-agent must have hard-veto authority over code_generation."""
        payload = {"agent_id": "security-agent", "topic": "code_generation"}
        resp = client.post(
            "/api/v1/authority/check",
            data=json.dumps(payload),
            content_type="application/json",
        )
        data = resp.get_json()
        assert data["has_authority"] is True
        assert data.get("veto_type") == "hard"

    def test_check_authority_missing_fields_returns_400(self, client):
        """POST /api/v1/authority/check without required fields must return 400."""
        payload = {"agent_id": "security-agent"}  # missing topic
        resp = client.post(
            "/api/v1/authority/check",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert resp.status_code == 400
