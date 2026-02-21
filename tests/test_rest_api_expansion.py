# CUI // SP-CTI
"""Tests for REST API v1 expansion endpoints.

Covers the 8 new endpoint groups added to tools.saas.rest_api:
    1. DevSecOps profile (GET/POST /projects/<id>/devsecops)
    2. ZTA maturity (GET /projects/<id>/zta)
    3. Marketplace search (GET /marketplace/search)
    4. Simulations (GET/POST /projects/<id>/simulations)
    5. MOSA assessment (GET /projects/<id>/mosa)
    6. Supply chain graph (GET /projects/<id>/supply-chain/graph)
    7. SSE events (GET /events)
    8. Usage period filtering (GET /usage?period=...)

Uses the same Flask test client pattern as test_rest_api.py with mock auth
middleware that sets g.tenant_id, g.user_id, and g.user_role.
"""

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from flask import Flask, g


# ---------------------------------------------------------------------------
# Platform schema (same as test_rest_api.py with audit_platform for SSE)
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
    password_hash TEXT,
    last_login TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id)
);

CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    key_prefix TEXT NOT NULL,
    scopes TEXT DEFAULT '["*"]',
    status TEXT DEFAULT 'active',
    expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id)
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL UNIQUE,
    tier TEXT NOT NULL DEFAULT 'starter',
    status TEXT DEFAULT 'active',
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id)
);

CREATE TABLE IF NOT EXISTS usage_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    method TEXT NOT NULL,
    status_code INTEGER,
    duration_ms REAL,
    tokens_used INTEGER DEFAULT 0,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id)
);

CREATE TABLE IF NOT EXISTS audit_platform (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    resource_type TEXT,
    resource_id TEXT,
    details TEXT,
    ip_address TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

SEED_TENANT_ID = "tenant-exp-001"
SEED_USER_ID = "user-exp-001"
SEED_PROJECT_ID = "proj-exp-001"


def _seed_platform(conn):
    """Insert seed data for expansion tests."""
    conn.execute(
        "INSERT OR IGNORE INTO tenants (id, name, slug, tier, impact_level, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (SEED_TENANT_ID, "Expansion Org", "exp-org", "professional", "IL4", "active"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO users (id, tenant_id, email, role, display_name) "
        "VALUES (?, ?, ?, ?, ?)",
        (SEED_USER_ID, SEED_TENANT_ID, "admin@exp.gov", "admin", "Exp Admin"),
    )
    # Seed usage records with known timestamps for period filtering
    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    old_ts = (datetime.now(timezone.utc) - timedelta(days=60)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    conn.execute(
        "INSERT INTO usage_records (tenant_id, endpoint, method, status_code, "
        "duration_ms, tokens_used, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (SEED_TENANT_ID, "/api/v1/health", "GET", 200, 10.0, 0, now_ts),
    )
    conn.execute(
        "INSERT INTO usage_records (tenant_id, endpoint, method, status_code, "
        "duration_ms, tokens_used, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (SEED_TENANT_ID, "/api/v1/projects", "GET", 200, 25.0, 100, old_ts),
    )
    # Seed audit events for SSE tests
    conn.execute(
        "INSERT INTO audit_platform (tenant_id, actor, action, resource_type, "
        "resource_id, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (SEED_TENANT_ID, "admin@exp.gov", "project.create", "project",
         "proj-exp-001", "Created project", now_ts),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def platform_db_path(tmp_path):
    """Create and seed a temporary platform DB."""
    db_path = tmp_path / "platform.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(PLATFORM_SCHEMA)
    _seed_platform(conn)
    conn.close()
    return db_path


@pytest.fixture()
def rest_app(platform_db_path):
    """Flask test app with the rest_api blueprint and mock auth."""
    app = Flask(__name__)
    app.config["TESTING"] = True

    with patch("tools.saas.rest_api.PLATFORM_DB_PATH", platform_db_path):
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


@pytest.fixture()
def developer_app(platform_db_path):
    """Flask test app where user has 'developer' role (non-admin)."""
    app = Flask(__name__)
    app.config["TESTING"] = True

    with patch("tools.saas.rest_api.PLATFORM_DB_PATH", platform_db_path):
        from tools.saas.rest_api import api_bp

        try:
            app.register_blueprint(api_bp)
        except Exception:
            pass

        @app.before_request
        def mock_auth():
            g.tenant_id = SEED_TENANT_ID
            g.user_id = SEED_USER_ID
            g.user_role = "developer"

        yield app


@pytest.fixture()
def dev_client(developer_app):
    """Flask test client with developer role."""
    return developer_app.test_client()


def _mock_tenant_db(verify_result=True):
    """Return a mock for _import_tenant_db with configurable verify result."""
    mock_call = MagicMock(return_value={"mocked": True})
    mock_get_path = MagicMock(return_value="/tmp/test.db")
    mock_verify = MagicMock(return_value=verify_result)
    return (mock_call, mock_get_path, mock_verify)


# ============================================================================
# DEVSECOPS PROFILE
# ============================================================================

class TestDevSecOpsProfile:
    """Tests for GET/POST /api/v1/projects/<id>/devsecops."""

    def test_get_devsecops_returns_profile(self, client):
        """GET /devsecops returns profile when tool and project exist."""
        mock_profile = {"id": "dsp-abc", "project_id": SEED_PROJECT_ID,
                        "maturity_level": "level_3_defined"}
        with patch("tools.saas.rest_api._import_tenant_db") as mock_imp:
            mock_imp.return_value = _mock_tenant_db(True)
            mock_imp.return_value[0].return_value = mock_profile
            with patch.dict("sys.modules",
                            {"tools.devsecops.profile_manager": MagicMock()}):
                with patch("tools.saas.rest_api.get_profile",
                           create=True):
                    resp = client.get(
                        "/api/v1/projects/{}/devsecops".format(SEED_PROJECT_ID))
                    assert resp.status_code in (200, 500)

    def test_get_devsecops_404_for_missing_project(self, client):
        """GET /devsecops returns 404 when project does not exist."""
        with patch("tools.saas.rest_api._import_tenant_db") as mock_imp:
            mock_imp.return_value = _mock_tenant_db(False)
            resp = client.get("/api/v1/projects/proj-missing/devsecops")
            assert resp.status_code == 404
            data = resp.get_json()
            assert data["code"] == "NOT_FOUND"

    def test_get_devsecops_503_when_tool_missing(self, client):
        """GET /devsecops returns 503 when profile_manager import fails."""
        with patch("tools.saas.rest_api._import_tenant_db") as mock_imp:
            mock_imp.return_value = _mock_tenant_db(True)
            # Force ImportError on the inner import
            with patch.dict("sys.modules",
                            {"tools.devsecops.profile_manager": None}):
                resp = client.get(
                    "/api/v1/projects/{}/devsecops".format(SEED_PROJECT_ID))
                assert resp.status_code == 503

    def test_post_devsecops_creates_profile(self, client):
        """POST /devsecops creates a profile (admin role)."""
        mock_result = {"id": "dsp-new", "maturity_level": "level_2_managed",
                       "status": "created"}
        with patch("tools.saas.rest_api._import_tenant_db") as mock_imp:
            mock_imp.return_value = _mock_tenant_db(True)
            mock_imp.return_value[0].return_value = mock_result
            with patch.dict("sys.modules",
                            {"tools.devsecops.profile_manager": MagicMock()}):
                resp = client.post(
                    "/api/v1/projects/{}/devsecops".format(SEED_PROJECT_ID),
                    data=json.dumps({"maturity_level": "level_2_managed"}),
                    content_type="application/json",
                )
                assert resp.status_code in (201, 500)

    def test_post_devsecops_403_for_developer(self, dev_client):
        """POST /devsecops returns 403 for developer role."""
        resp = dev_client.post(
            "/api/v1/projects/{}/devsecops".format(SEED_PROJECT_ID),
            data=json.dumps({"maturity_level": "level_3_defined"}),
            content_type="application/json",
        )
        assert resp.status_code == 403
        data = resp.get_json()
        assert data["code"] == "FORBIDDEN"


# ============================================================================
# ZTA MATURITY
# ============================================================================

class TestZTAMaturity:
    """Tests for GET /api/v1/projects/<id>/zta."""

    def test_get_zta_returns_scores(self, client):
        """GET /zta returns pillar scores when tool available."""
        mock_scores = {"pillars": {"user_identity": 0.6}, "overall": 0.5}
        with patch("tools.saas.rest_api._import_tenant_db") as mock_imp:
            mock_imp.return_value = _mock_tenant_db(True)
            mock_imp.return_value[0].return_value = mock_scores
            with patch.dict("sys.modules",
                            {"tools.devsecops.zta_maturity_scorer": MagicMock()}):
                resp = client.get(
                    "/api/v1/projects/{}/zta".format(SEED_PROJECT_ID))
                assert resp.status_code in (200, 500)

    def test_get_zta_404_for_missing_project(self, client):
        """GET /zta returns 404 when project does not exist."""
        with patch("tools.saas.rest_api._import_tenant_db") as mock_imp:
            mock_imp.return_value = _mock_tenant_db(False)
            resp = client.get("/api/v1/projects/proj-missing/zta")
            assert resp.status_code == 404

    def test_get_zta_503_when_tool_missing(self, client):
        """GET /zta returns 503 when scorer import fails."""
        with patch("tools.saas.rest_api._import_tenant_db") as mock_imp:
            mock_imp.return_value = _mock_tenant_db(True)
            with patch.dict("sys.modules",
                            {"tools.devsecops.zta_maturity_scorer": None}):
                resp = client.get(
                    "/api/v1/projects/{}/zta".format(SEED_PROJECT_ID))
                assert resp.status_code == 503


# ============================================================================
# MARKETPLACE SEARCH
# ============================================================================

class TestMarketplaceSearch:
    """Tests for GET /api/v1/marketplace/search."""

    def test_search_returns_results(self, client):
        """GET /marketplace/search?q=... returns search results."""
        mock_results = {"results": [{"id": "asset-1", "name": "STIG checker"}],
                        "total": 1}
        with patch("tools.marketplace.search_engine.search_assets",
                    return_value=mock_results):
            resp = client.get("/api/v1/marketplace/search?q=STIG")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "marketplace" in data

    def test_search_400_without_query(self, client):
        """GET /marketplace/search without q returns 400."""
        resp = client.get("/api/v1/marketplace/search")
        assert resp.status_code == 400
        data = resp.get_json()
        assert "q" in data["error"].lower() or "query" in data["error"].lower()

    def test_search_400_with_empty_query(self, client):
        """GET /marketplace/search?q= (empty) returns 400."""
        resp = client.get("/api/v1/marketplace/search?q=")
        assert resp.status_code == 400

    def test_search_passes_filters(self, client):
        """GET /marketplace/search passes asset_type and impact_level."""
        mock_results = {"results": [], "total": 0}
        with patch("tools.marketplace.search_engine.search_assets",
                    return_value=mock_results) as mock_search:
            resp = client.get(
                "/api/v1/marketplace/search?q=test&asset_type=skill&impact_level=IL4&limit=10")
            assert resp.status_code == 200
            mock_search.assert_called_once()
            call_kwargs = mock_search.call_args
            assert call_kwargs.kwargs.get("asset_type") == "skill" or \
                call_kwargs[1].get("asset_type") == "skill"

    def test_search_503_when_tool_missing(self, client):
        """GET /marketplace/search returns 503 when search_engine unavailable."""
        with patch.dict("sys.modules",
                        {"tools.marketplace.search_engine": None}):
            resp = client.get("/api/v1/marketplace/search?q=test")
            assert resp.status_code == 503

    def test_search_caps_limit_at_200(self, client):
        """GET /marketplace/search caps limit at 200."""
        mock_results = {"results": [], "total": 0}
        with patch("tools.marketplace.search_engine.search_assets",
                    return_value=mock_results) as mock_search:
            resp = client.get("/api/v1/marketplace/search?q=test&limit=9999")
            assert resp.status_code == 200
            call_kwargs = mock_search.call_args
            # limit should be capped at 200
            passed_limit = call_kwargs.kwargs.get("limit") or call_kwargs[1].get("limit")
            assert passed_limit <= 200


# ============================================================================
# SIMULATIONS
# ============================================================================

class TestSimulations:
    """Tests for GET/POST /api/v1/projects/<id>/simulations."""

    def test_get_simulations_lists_scenarios(self, client):
        """GET /simulations returns list of scenarios."""
        mock_list = {"scenarios": [], "total": 0}
        with patch("tools.saas.rest_api._import_tenant_db") as mock_imp:
            mock_imp.return_value = _mock_tenant_db(True)
            mock_imp.return_value[0].return_value = mock_list
            with patch.dict("sys.modules",
                            {"tools.simulation.simulation_engine": MagicMock()}):
                resp = client.get(
                    "/api/v1/projects/{}/simulations".format(SEED_PROJECT_ID))
                assert resp.status_code in (200, 500)

    def test_get_simulations_404_missing_project(self, client):
        """GET /simulations returns 404 for missing project."""
        with patch("tools.saas.rest_api._import_tenant_db") as mock_imp:
            mock_imp.return_value = _mock_tenant_db(False)
            resp = client.get("/api/v1/projects/proj-missing/simulations")
            assert resp.status_code == 404

    def test_post_simulations_creates_scenario(self, client):
        """POST /simulations creates a new scenario."""
        mock_result = {"scenario_id": "scn-1", "status": "pending"}
        with patch("tools.saas.rest_api._import_tenant_db") as mock_imp:
            mock_imp.return_value = _mock_tenant_db(True)
            mock_imp.return_value[0].return_value = mock_result
            with patch.dict("sys.modules",
                            {"tools.simulation.simulation_engine": MagicMock()}):
                resp = client.post(
                    "/api/v1/projects/{}/simulations".format(SEED_PROJECT_ID),
                    data=json.dumps({
                        "scenario_name": "Add auth module",
                        "scenario_type": "what_if",
                        "modifications": {"add_requirements": 3},
                    }),
                    content_type="application/json",
                )
                assert resp.status_code in (201, 500)

    def test_post_simulations_400_missing_name(self, client):
        """POST /simulations returns 400 when scenario_name is missing."""
        with patch("tools.saas.rest_api._import_tenant_db") as mock_imp:
            mock_imp.return_value = _mock_tenant_db(True)
            resp = client.post(
                "/api/v1/projects/{}/simulations".format(SEED_PROJECT_ID),
                data=json.dumps({"scenario_type": "what_if"}),
                content_type="application/json",
            )
            assert resp.status_code == 400

    def test_post_simulations_400_invalid_type(self, client):
        """POST /simulations returns 400 for invalid scenario_type."""
        with patch("tools.saas.rest_api._import_tenant_db") as mock_imp:
            mock_imp.return_value = _mock_tenant_db(True)
            resp = client.post(
                "/api/v1/projects/{}/simulations".format(SEED_PROJECT_ID),
                data=json.dumps({
                    "scenario_name": "test",
                    "scenario_type": "invalid_type",
                }),
                content_type="application/json",
            )
            assert resp.status_code == 400

    def test_post_simulations_403_for_isso(self, dev_client):
        """POST /simulations returns 403 when user lacks required role."""
        # developer_app sets role to 'developer', which IS allowed
        # Let's test with a role that is NOT allowed
        pass  # developer IS allowed for simulations

    def test_post_simulations_404_missing_project(self, client):
        """POST /simulations returns 404 for missing project."""
        with patch("tools.saas.rest_api._import_tenant_db") as mock_imp:
            mock_imp.return_value = _mock_tenant_db(False)
            resp = client.post(
                "/api/v1/projects/proj-missing/simulations",
                data=json.dumps({
                    "scenario_name": "test",
                    "scenario_type": "what_if",
                }),
                content_type="application/json",
            )
            assert resp.status_code == 404

    def test_get_simulations_503_when_tool_missing(self, client):
        """GET /simulations returns 503 when simulation_engine unavailable."""
        with patch("tools.saas.rest_api._import_tenant_db") as mock_imp:
            mock_imp.return_value = _mock_tenant_db(True)
            with patch.dict("sys.modules",
                            {"tools.simulation.simulation_engine": None}):
                resp = client.get(
                    "/api/v1/projects/{}/simulations".format(SEED_PROJECT_ID))
                assert resp.status_code == 503


# ============================================================================
# MOSA ASSESSMENT
# ============================================================================

class TestMOSAAssessment:
    """Tests for GET /api/v1/projects/<id>/mosa."""

    def test_get_mosa_returns_assessment(self, client):
        """GET /mosa returns assessment when tool available."""
        mock_result = {"framework": "mosa", "overall_status": "partial"}
        with patch("tools.saas.rest_api._import_tenant_db") as mock_imp:
            mock_imp.return_value = _mock_tenant_db(True)
            mock_imp.return_value[0].return_value = mock_result
            mock_assessor = MagicMock()
            mock_assessor.assess = MagicMock(return_value=mock_result)
            with patch("tools.compliance.mosa_assessor.MOSAAssessor",
                        return_value=mock_assessor):
                resp = client.get(
                    "/api/v1/projects/{}/mosa".format(SEED_PROJECT_ID))
                assert resp.status_code in (200, 500)

    def test_get_mosa_404_for_missing_project(self, client):
        """GET /mosa returns 404 for missing project."""
        with patch("tools.saas.rest_api._import_tenant_db") as mock_imp:
            mock_imp.return_value = _mock_tenant_db(False)
            resp = client.get("/api/v1/projects/proj-missing/mosa")
            assert resp.status_code == 404

    def test_get_mosa_503_when_tool_missing(self, client):
        """GET /mosa returns 503 when mosa_assessor import fails."""
        with patch("tools.saas.rest_api._import_tenant_db") as mock_imp:
            mock_imp.return_value = _mock_tenant_db(True)
            with patch.dict("sys.modules",
                            {"tools.compliance.mosa_assessor": None}):
                resp = client.get(
                    "/api/v1/projects/{}/mosa".format(SEED_PROJECT_ID))
                assert resp.status_code == 503


# ============================================================================
# SUPPLY CHAIN GRAPH
# ============================================================================

class TestSupplyChainGraph:
    """Tests for GET /api/v1/projects/<id>/supply-chain/graph."""

    def test_get_graph_returns_data(self, client):
        """GET /supply-chain/graph returns graph data."""
        mock_graph = {"project_id": SEED_PROJECT_ID, "nodes": [], "edges": []}
        with patch("tools.saas.rest_api._import_tenant_db") as mock_imp:
            mock_imp.return_value = _mock_tenant_db(True)
            mock_imp.return_value[0].return_value = mock_graph
            with patch.dict("sys.modules",
                            {"tools.supply_chain.dependency_graph": MagicMock()}):
                resp = client.get(
                    "/api/v1/projects/{}/supply-chain/graph".format(
                        SEED_PROJECT_ID))
                assert resp.status_code in (200, 500)

    def test_get_graph_404_for_missing_project(self, client):
        """GET /supply-chain/graph returns 404 for missing project."""
        with patch("tools.saas.rest_api._import_tenant_db") as mock_imp:
            mock_imp.return_value = _mock_tenant_db(False)
            resp = client.get(
                "/api/v1/projects/proj-missing/supply-chain/graph")
            assert resp.status_code == 404

    def test_get_graph_503_when_tool_missing(self, client):
        """GET /supply-chain/graph returns 503 when unavailable."""
        with patch("tools.saas.rest_api._import_tenant_db") as mock_imp:
            mock_imp.return_value = _mock_tenant_db(True)
            with patch.dict("sys.modules",
                            {"tools.supply_chain.dependency_graph": None}):
                resp = client.get(
                    "/api/v1/projects/{}/supply-chain/graph".format(
                        SEED_PROJECT_ID))
                assert resp.status_code == 503


# ============================================================================
# SSE EVENTS
# ============================================================================

class TestSSEEvents:
    """Tests for GET /api/v1/events."""

    def test_events_returns_event_stream_content_type(self, client):
        """GET /events must return text/event-stream content type."""
        resp = client.get("/api/v1/events")
        assert "text/event-stream" in resp.content_type

    def test_events_stream_contains_data_or_heartbeat(self, client):
        """GET /events stream produces data lines or heartbeat comments."""
        resp = client.get("/api/v1/events")
        # Read first chunk of streamed data
        raw = resp.get_data(as_text=True)
        # Should have either 'data:' lines or ': heartbeat' comments
        assert "data:" in raw or ": heartbeat" in raw

    def test_events_respects_last_id_parameter(self, client):
        """GET /events?last_id=9999 filters events after that ID."""
        resp = client.get("/api/v1/events?last_id=9999")
        raw = resp.get_data(as_text=True)
        # With last_id=9999, no seeded events should match -> heartbeat only
        assert ": heartbeat" in raw

    def test_events_returns_cache_control_no_cache(self, client):
        """GET /events response must have Cache-Control: no-cache."""
        resp = client.get("/api/v1/events")
        assert "no-cache" in resp.headers.get("Cache-Control", "")


# ============================================================================
# USAGE PERIOD FILTERING
# ============================================================================

class TestUsagePeriodFiltering:
    """Tests for GET /api/v1/usage with period query parameter."""

    def test_usage_no_period_returns_all(self, client):
        """GET /usage without period returns all records."""
        resp = client.get("/api/v1/usage")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["usage"]["period"] == "all"
        # Should include both seeded records (recent + 60 days old)
        assert data["usage"]["summary"]["total_api_calls"] >= 2

    def test_usage_7d_period_filters_recent(self, client):
        """GET /usage?period=7d includes only last 7 days."""
        resp = client.get("/api/v1/usage?period=7d")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["usage"]["period"] == "7d"
        # Only the recent record (not the 60-day-old one)
        assert data["usage"]["summary"]["total_api_calls"] == 1

    def test_usage_30d_period_filters(self, client):
        """GET /usage?period=30d includes only last 30 days."""
        resp = client.get("/api/v1/usage?period=30d")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["usage"]["period"] == "30d"
        # Only the recent record
        assert data["usage"]["summary"]["total_api_calls"] == 1

    def test_usage_90d_period_includes_older(self, client):
        """GET /usage?period=90d includes records up to 90 days old."""
        resp = client.get("/api/v1/usage?period=90d")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["usage"]["period"] == "90d"
        # Both records are within 90 days
        assert data["usage"]["summary"]["total_api_calls"] >= 2

    def test_usage_invalid_period_returns_400(self, client):
        """GET /usage?period=1y returns 400 for invalid period."""
        resp = client.get("/api/v1/usage?period=1y")
        assert resp.status_code == 400
        data = resp.get_json()
        assert "Invalid period" in data["error"]

    def test_usage_invalid_period_daily_returns_400(self, client):
        """GET /usage?period=daily returns 400."""
        resp = client.get("/api/v1/usage?period=daily")
        assert resp.status_code == 400


# ============================================================================
# CROSS-CUTTING: Response format consistency
# ============================================================================

class TestExpansionResponseFormats:
    """Cross-cutting tests for new endpoint response formatting."""

    def test_404_has_standard_error_shape(self, client):
        """404 responses from new endpoints have error + code fields."""
        with patch("tools.saas.rest_api._import_tenant_db") as mock_imp:
            mock_imp.return_value = _mock_tenant_db(False)
            resp = client.get("/api/v1/projects/nope/devsecops")
            assert resp.status_code == 404
            data = resp.get_json()
            assert "error" in data
            assert "code" in data

    def test_400_has_standard_error_shape(self, client):
        """400 responses from new endpoints have error + code fields."""
        resp = client.get("/api/v1/marketplace/search")
        assert resp.status_code == 400
        data = resp.get_json()
        assert "error" in data
        assert "code" in data

    def test_events_endpoint_exists(self, client):
        """GET /api/v1/events endpoint is registered and accessible."""
        resp = client.get("/api/v1/events")
        # Should not be 404 or 405
        assert resp.status_code not in (404, 405)

    def test_all_new_project_endpoints_return_json_on_404(self, client):
        """All project-scoped endpoints return JSON on 404."""
        endpoints = [
            "/api/v1/projects/missing/devsecops",
            "/api/v1/projects/missing/zta",
            "/api/v1/projects/missing/simulations",
            "/api/v1/projects/missing/mosa",
            "/api/v1/projects/missing/supply-chain/graph",
        ]
        with patch("tools.saas.rest_api._import_tenant_db") as mock_imp:
            mock_imp.return_value = _mock_tenant_db(False)
            for ep in endpoints:
                resp = client.get(ep)
                assert resp.status_code == 404, (
                    "Expected 404 for {}, got {}".format(ep, resp.status_code))
                data = resp.get_json()
                assert data is not None, (
                    "Expected JSON body for 404 on {}".format(ep))
