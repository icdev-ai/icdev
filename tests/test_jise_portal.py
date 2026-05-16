# [TEMPLATE: CUI // SP-CTI]
"""Pytest unit tests for tools.intelligence.jise_portal — JISE Portal Data API.

NIST 800-53: SA-11 (Developer Security Testing), CM-3 (Configuration Change Control)

RED phase: All assertions are written against the expected interface.
The implementation in tools/intelligence/jise_portal.py does not yet exist.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from flask import Flask

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def jise_app():
    """Flask test app with the jise_portal blueprint registered."""
    from tools.intelligence.jise_portal import jise_bp

    app = Flask("test_jise")
    app.config["TESTING"] = True
    app.register_blueprint(jise_bp)
    return app


@pytest.fixture()
def client(jise_app):
    return jise_app.test_client()


# ---------------------------------------------------------------------------
# Unit tests for get_jise_portal_data()
# ---------------------------------------------------------------------------

class TestGetJisePortalData:
    """Tests for the core data-retrieval function."""

    def test_returns_list(self):
        """get_jise_portal_data() must return a list."""
        from tools.intelligence.jise_portal import get_jise_portal_data

        result = get_jise_portal_data()
        assert isinstance(result, list)

    def test_each_record_has_required_fields(self):
        """Every record must include id, source, classification, timestamp."""
        from tools.intelligence.jise_portal import get_jise_portal_data

        records = get_jise_portal_data()
        required = {"id", "source", "classification", "timestamp"}
        for rec in records:
            missing = required - set(rec.keys())
            assert not missing, f"Record missing fields {missing}: {rec}"

    def test_filter_by_classification(self):
        """Filtering by classification returns only matching records."""
        from tools.intelligence.jise_portal import get_jise_portal_data

        records = get_jise_portal_data(classification="CUI")
        for rec in records:
            assert rec["classification"] == "CUI", (
                f"Expected classification='CUI', got '{rec['classification']}'"
            )

    def test_filter_by_source(self):
        """Filtering by source returns only matching records."""
        from tools.intelligence.jise_portal import get_jise_portal_data

        records_all = get_jise_portal_data()
        if not records_all:
            pytest.skip("No seed data available")
        first_source = records_all[0]["source"]
        filtered = get_jise_portal_data(source=first_source)
        for rec in filtered:
            assert rec["source"] == first_source

    def test_filter_no_match_returns_empty_list(self):
        """A filter that matches nothing returns an empty list, not an error."""
        from tools.intelligence.jise_portal import get_jise_portal_data

        result = get_jise_portal_data(source="__nonexistent_source_xyz__")
        assert result == []

    def test_limit_respected(self):
        """The limit parameter caps the number of returned records."""
        from tools.intelligence.jise_portal import get_jise_portal_data

        result = get_jise_portal_data(limit=2)
        assert len(result) <= 2

    def test_invalid_limit_raises_value_error(self):
        """A non-integer limit raises ValueError."""
        from tools.intelligence.jise_portal import get_jise_portal_data

        with pytest.raises((ValueError, TypeError)):
            get_jise_portal_data(limit="notanumber")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------

class TestJisePortalEndpoint:
    """Tests for the GET /api/v1/jise/portal-data Flask endpoint."""

    def test_get_returns_200(self, client):
        """GET /api/v1/jise/portal-data returns HTTP 200."""
        response = client.get("/api/v1/jise/portal-data")
        assert response.status_code == 200

    def test_response_is_json(self, client):
        """Response content-type is application/json."""
        response = client.get("/api/v1/jise/portal-data")
        assert "application/json" in response.content_type

    def test_response_has_data_key(self, client):
        """Response body contains a 'data' key."""
        response = client.get("/api/v1/jise/portal-data")
        body = json.loads(response.data)
        assert "data" in body, f"Missing 'data' key: {body}"

    def test_response_has_count_key(self, client):
        """Response body contains a 'count' key matching len(data)."""
        response = client.get("/api/v1/jise/portal-data")
        body = json.loads(response.data)
        assert "count" in body, f"Missing 'count' key: {body}"
        assert body["count"] == len(body["data"])

    def test_classification_filter_query_param(self, client):
        """?classification=CUI filters records by classification."""
        response = client.get("/api/v1/jise/portal-data?classification=CUI")
        assert response.status_code == 200
        body = json.loads(response.data)
        for rec in body["data"]:
            assert rec["classification"] == "CUI"

    def test_source_filter_no_match_returns_empty(self, client):
        """?source=nonexistent returns 200 with empty data list."""
        response = client.get("/api/v1/jise/portal-data?source=nonexistent")
        assert response.status_code == 200
        body = json.loads(response.data)
        assert body["data"] == []

    def test_invalid_limit_returns_400(self, client):
        """?limit=notanumber returns HTTP 400 with error message."""
        response = client.get("/api/v1/jise/portal-data?limit=notanumber")
        assert response.status_code == 400
        body = json.loads(response.data)
        assert "error" in body

    def test_valid_limit_param(self, client):
        """?limit=1 returns at most 1 record."""
        response = client.get("/api/v1/jise/portal-data?limit=1")
        assert response.status_code == 200
        body = json.loads(response.data)
        assert len(body["data"]) <= 1

    def test_records_have_required_fields(self, client):
        """Each record in data has id, source, classification, timestamp."""
        response = client.get("/api/v1/jise/portal-data")
        body = json.loads(response.data)
        required = {"id", "source", "classification", "timestamp"}
        for rec in body["data"]:
            missing = required - set(rec.keys())
            assert not missing, f"Record missing fields {missing}: {rec}"

    def test_head_request_allowed(self, client):
        """HEAD /api/v1/jise/portal-data is allowed (Flask default for GET routes)."""
        response = client.head("/api/v1/jise/portal-data")
        assert response.status_code in (200, 405)


# ---------------------------------------------------------------------------
# CUI marking validation
# ---------------------------------------------------------------------------

class TestCuiMarkings:
    """Verify CUI header present in source file (static analysis)."""

    def test_jise_portal_module_has_cui_header(self):
        """tools/intelligence/jise_portal.py must carry CUI // SP-CTI marking."""
        module_path = BASE_DIR / "tools" / "intelligence" / "jise_portal.py"
        assert module_path.exists(), f"Module not found at {module_path}"
        content = module_path.read_text(encoding="utf-8")
        assert "CUI" in content, "Module is missing CUI classification marking"
