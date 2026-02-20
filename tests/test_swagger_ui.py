# CUI // SP-CTI
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from flask import Flask

from tools.saas.swagger_ui import swagger_bp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def app():
    """Create a minimal Flask app with the swagger blueprint registered."""
    application = Flask(__name__)
    application.config["TESTING"] = True
    application.register_blueprint(swagger_bp)
    return application


@pytest.fixture()
def client(app):
    """Flask test client."""
    return app.test_client()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSwaggerBlueprint:
    """Verify swagger_bp is a properly configured Flask Blueprint."""

    def test_swagger_bp_is_blueprint(self):
        from flask import Blueprint
        assert isinstance(swagger_bp, Blueprint)

    def test_swagger_bp_has_url_prefix(self):
        assert swagger_bp.url_prefix == "/api/v1"


class TestOpenAPIJSON:
    """Tests for GET /api/v1/openapi.json."""

    def test_openapi_json_returns_200(self, client):
        resp = client.get("/api/v1/openapi.json")
        assert resp.status_code == 200

    def test_openapi_json_content_type(self, client):
        resp = client.get("/api/v1/openapi.json")
        assert "application/json" in resp.content_type

    def test_openapi_json_has_openapi_key(self, client):
        resp = client.get("/api/v1/openapi.json")
        data = resp.get_json()
        assert "openapi" in data
        assert data["openapi"].startswith("3.")


class TestSwaggerDocsPage:
    """Tests for GET /api/v1/docs."""

    def test_docs_returns_200(self, client):
        resp = client.get("/api/v1/docs")
        assert resp.status_code == 200

    def test_docs_returns_html(self, client):
        resp = client.get("/api/v1/docs")
        assert "text/html" in resp.content_type

    def test_docs_contains_cui_banner(self, client):
        resp = client.get("/api/v1/docs")
        html = resp.data.decode("utf-8")
        assert "CUI // SP-CTI" in html

    def test_docs_contains_swagger_ui_reference(self, client):
        resp = client.get("/api/v1/docs")
        html = resp.data.decode("utf-8")
        assert "swagger-ui" in html.lower()
