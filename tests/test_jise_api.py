# CUI // SP-CTI
"""Tests for the JISE Portal REST API (tools/dashboard/api/jise.py).

Validates all four feed endpoints:
  GET /api/v1/jise/status
  GET /api/v1/jise/requirements
  GET /api/v1/jise/intelligence
  GET /api/v1/jise/compliance

Uses a minimal Flask app (JISE blueprint only) to avoid full dashboard startup cost.

Run: pytest tests/test_jise_api.py -v --tb=short
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def jise_client(tmp_path_factory):
    """Minimal Flask test client exposing only the JISE blueprint."""
    from flask import Flask
    from tools.dashboard.api.jise import jise_api

    db_path = tmp_path_factory.mktemp("jise_db") / "icdev.db"

    # Create the minimal schema needed by JISE endpoints
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS requirements (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT '',
            description TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            priority TEXT DEFAULT 'medium',
            classification TEXT DEFAULT 'CUI',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS security_scan_results (
            id TEXT PRIMARY KEY,
            scan_type TEXT DEFAULT 'sast',
            severity TEXT DEFAULT 'low',
            title TEXT DEFAULT '',
            description TEXT,
            status TEXT DEFAULT 'open',
            project_id TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS compliance_controls (
            id TEXT PRIMARY KEY,
            family TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            description TEXT,
            status TEXT DEFAULT 'not_assessed',
            impact_level TEXT,
            supplemental_guidance TEXT,
            enhancements TEXT
        );
        CREATE TABLE IF NOT EXISTS poam_items (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            finding TEXT,
            status TEXT DEFAULT 'open',
            severity TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT (datetime('now'))
        );
        INSERT INTO requirements VALUES ('r-1', 'REQ-001: JISE interface', 'JISE downstream interface', 'open', 'high', 'CUI', datetime('now'), datetime('now'));
        INSERT INTO security_scan_results VALUES ('s-1', 'sast', 'high', 'SQL injection risk', 'Unparameterized query', 'open', 'proj-1', datetime('now'));
        INSERT INTO compliance_controls VALUES ('cc-1', 'AC', 'AC-3 Access Enforcement', 'Enforce access decisions', 'satisfied', 'IL5', NULL, NULL);
        INSERT INTO poam_items VALUES ('p-1', 'proj-1', 'Missing MFA', 'open', 'high', datetime('now'));
        INSERT INTO projects VALUES ('proj-1', 'JISE Test Project', 'active', datetime('now'));
        """
    )
    conn.commit()
    conn.close()

    os.environ["ICDEV_DB_PATH"] = str(db_path)
    os.environ["ICDEV_STORAGE_BACKEND"] = "sqlite"
    try:
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(jise_api)
        yield app.test_client()
    finally:
        os.environ.pop("ICDEV_DB_PATH", None)


# ---------------------------------------------------------------------------
# GET /api/v1/jise/status
# ---------------------------------------------------------------------------


class TestJiseStatus:
    """Connectivity health-check endpoint."""

    def test_returns_2xx_or_503(self, jise_client):
        resp = jise_client.get("/api/v1/jise/status")
        assert resp.status_code in (200, 503)

    def test_json_shape(self, jise_client):
        data = jise_client.get("/api/v1/jise/status").get_json()
        for field in ("service", "status", "timestamp", "version", "classification"):
            assert field in data, f"missing field: {field}"

    def test_classification_is_cui(self, jise_client):
        data = jise_client.get("/api/v1/jise/status").get_json()
        assert data["classification"] == "CUI"

    def test_service_name_contains_jise(self, jise_client):
        data = jise_client.get("/api/v1/jise/status").get_json()
        assert "JISE" in data["service"]

    def test_version_field_present(self, jise_client):
        data = jise_client.get("/api/v1/jise/status").get_json()
        assert data["version"] == "1.0"


# ---------------------------------------------------------------------------
# GET /api/v1/jise/requirements
# ---------------------------------------------------------------------------


class TestJiseRequirements:
    """Requirements feed endpoint."""

    def test_returns_200(self, jise_client):
        assert jise_client.get("/api/v1/jise/requirements").status_code == 200

    def test_json_shape(self, jise_client):
        data = jise_client.get("/api/v1/jise/requirements").get_json()
        assert data["feed"] == "requirements"
        assert isinstance(data["items"], list)
        assert "total" in data
        assert "limit" in data

    def test_classification_is_cui(self, jise_client):
        data = jise_client.get("/api/v1/jise/requirements").get_json()
        assert data["classification"] == "CUI"

    def test_default_limit_is_100(self, jise_client):
        data = jise_client.get("/api/v1/jise/requirements").get_json()
        assert data["limit"] == 100

    def test_custom_limit_respected(self, jise_client):
        data = jise_client.get("/api/v1/jise/requirements?limit=25").get_json()
        assert data["limit"] == 25

    def test_limit_capped_at_500(self, jise_client):
        data = jise_client.get("/api/v1/jise/requirements?limit=9999").get_json()
        assert data["limit"] == 500

    def test_invalid_limit_falls_back_to_default(self, jise_client):
        data = jise_client.get("/api/v1/jise/requirements?limit=abc").get_json()
        assert data["limit"] == 100

    def test_status_filter_accepted(self, jise_client):
        assert jise_client.get("/api/v1/jise/requirements?status=open").status_code == 200

    def test_total_matches_items_length(self, jise_client):
        data = jise_client.get("/api/v1/jise/requirements").get_json()
        assert data["total"] == len(data["items"])

    def test_seeded_requirement_present(self, jise_client):
        data = jise_client.get("/api/v1/jise/requirements").get_json()
        assert data["total"] >= 1
        titles = [i["title"] for i in data["items"]]
        assert any("REQ-001" in t for t in titles)


# ---------------------------------------------------------------------------
# GET /api/v1/jise/intelligence
# ---------------------------------------------------------------------------


class TestJiseIntelligence:
    """Intelligence assessments feed endpoint."""

    def test_returns_200(self, jise_client):
        assert jise_client.get("/api/v1/jise/intelligence").status_code == 200

    def test_json_shape(self, jise_client):
        data = jise_client.get("/api/v1/jise/intelligence").get_json()
        assert data["feed"] == "intelligence"
        assert isinstance(data["findings"], list)
        assert "total" in data
        assert "limit" in data
        assert "timestamp" in data

    def test_classification_is_cui(self, jise_client):
        data = jise_client.get("/api/v1/jise/intelligence").get_json()
        assert data["classification"] == "CUI"

    def test_default_limit_is_50(self, jise_client):
        data = jise_client.get("/api/v1/jise/intelligence").get_json()
        assert data["limit"] == 50

    def test_limit_capped_at_200(self, jise_client):
        data = jise_client.get("/api/v1/jise/intelligence?limit=9999").get_json()
        assert data["limit"] == 200

    def test_severity_filter_accepted(self, jise_client):
        assert jise_client.get("/api/v1/jise/intelligence?severity=critical").status_code == 200

    def test_total_matches_findings_length(self, jise_client):
        data = jise_client.get("/api/v1/jise/intelligence").get_json()
        assert data["total"] == len(data["findings"])

    def test_seeded_finding_present(self, jise_client):
        data = jise_client.get("/api/v1/jise/intelligence").get_json()
        assert data["total"] >= 1


# ---------------------------------------------------------------------------
# GET /api/v1/jise/compliance
# ---------------------------------------------------------------------------


class TestJiseCompliance:
    """Compliance posture summary endpoint."""

    def test_returns_200(self, jise_client):
        assert jise_client.get("/api/v1/jise/compliance").status_code == 200

    def test_json_shape(self, jise_client):
        data = jise_client.get("/api/v1/jise/compliance").get_json()
        assert data["feed"] == "compliance"
        assert "posture" in data
        assert "timestamp" in data

    def test_classification_is_cui(self, jise_client):
        data = jise_client.get("/api/v1/jise/compliance").get_json()
        assert data["classification"] == "CUI"

    def test_posture_fields_present(self, jise_client):
        posture = jise_client.get("/api/v1/jise/compliance").get_json()["posture"]
        assert "open_poam_items" in posture
        assert "control_summary" in posture
        assert "project_count" in posture

    def test_open_poam_count(self, jise_client):
        posture = jise_client.get("/api/v1/jise/compliance").get_json()["posture"]
        assert posture["open_poam_items"] == 1

    def test_project_count(self, jise_client):
        posture = jise_client.get("/api/v1/jise/compliance").get_json()["posture"]
        assert posture["project_count"] == 1

    def test_control_summary_is_dict(self, jise_client):
        posture = jise_client.get("/api/v1/jise/compliance").get_json()["posture"]
        assert isinstance(posture["control_summary"], dict)
