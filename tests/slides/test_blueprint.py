# CUI // SP-CTI
"""Unit tests for the Slide Deck Generator Flask blueprint.

Covers page routes and the native asset-generator smoke endpoint.
Database-backed routes use the slides canvas DB (SQLite in tests).
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def client():
    """Create a Flask test client from the full dashboard app so base.html context
    processors (nav_tree, etc.) are available."""
    from tools.dashboard.app import app
    from tools.slides.db.init_db import init_db

    app.config["TESTING"] = True
    with app.test_client() as test_client:
        with app.app_context():
            try:
                init_db()
            except Exception:
                pass
        yield test_client


class TestPageRoutes:
    def test_index_returns_200_and_heading(self, client):
        resp = client.get("/slides/")
        assert resp.status_code == 200
        assert b"Slide Deck Generator" in resp.data
        assert b"CUI // SP-CTI" in resp.data

    def test_new_wizard_returns_200_and_form(self, client):
        resp = client.get("/slides/new")
        assert resp.status_code == 200
        assert b"Generate New Deck" in resp.data or b"deck_type" in resp.data
        assert b"CUI // SP-CTI" in resp.data


class TestAssetSmokeEndpoint:
    def test_asset_smoke_returns_svg_path(self, client):
        resp = client.post(
            "/slides/api/asset-smoke",
            data=json.dumps({"title": "Unit Test Slide", "bullets": ["a", "b"]}),
            content_type="application/json",
        )
        assert resp.status_code == 200, resp.data.decode()
        data = resp.get_json()
        assert data["success"] is True
        assert data["method"] == "slides_svg"
        assert isinstance(data["path"], str)
        assert len(data["path"]) > 0

    def test_asset_smoke_handles_missing_body(self, client):
        resp = client.post("/slides/api/asset-smoke", content_type="application/json")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["method"] == "slides_svg"
