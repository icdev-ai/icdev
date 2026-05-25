# CUI // SP-CTI
"""Tests for the IDC IaC Emit dashboard page (dt-idc-iac-09).

Covers:
  1.  GET /infra/emit — page loads with 200
  2.  GET /infra/emit — contains the form with required fields (project, target, csp)
  3.  GET /infra/emit — CUI banner present
  4.  GET /infra/emit — page title contains "Emit"
  5.  POST /infra/emit/run — missing project returns 400
  6.  POST /infra/emit/run — missing target returns 400
  7.  POST /infra/emit/run — missing csp returns 400
  8.  POST /infra/emit/run — valid terraform/aws graph returns JSON with files dict
  9.  POST /infra/emit/run — valid ansible/aws graph returns JSON with files dict
  10. POST /infra/emit/run — valid pulumi/azure graph returns JSON with files dict
  11. POST /infra/emit/run — valid helm/aws graph returns JSON with files dict
  12. POST /infra/emit/run — unsupported CSP/target combo returns 422
  13. POST /infra/emit/run — response JSON contains "files" key with tab content
  14. POST /infra/emit/run — response JSON contains "target" key
  15. POST /infra/emit/run — response JSON contains "csp" key

NIST 800-53 controls: SA-11, CM-3, AU-2
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ── Sample graphs ──────────────────────────────────────────────────────────────

_TF_GRAPH = {
    "nodes": [
        {"id": "vpc1", "type": "aws-vpc", "label": "my-vpc",
         "metadata": {"cidr_block": "10.0.0.0/16"}}
    ],
    "edges": [],
}

_ANSIBLE_GRAPH = {
    "nodes": [
        {"id": "pkg1", "type": "package", "label": "Install nginx",
         "metadata": {"name": "nginx", "state": "present"}}
    ],
    "edges": [],
}

_PULUMI_GRAPH = {
    "nodes": [
        {"id": "vnet1", "type": "az-vnet", "label": "my-vnet",
         "metadata": {"resource_group_name": "rg-test"}}
    ],
    "edges": [],
}

_HELM_GRAPH = {
    "nodes": [
        {"id": "dep1", "type": "k8s-deployment", "label": "api",
         "metadata": {"image": "nginx:latest", "replicas": 2}}
    ],
    "edges": [],
}


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def _idc_db(tmp_path_factory):
    """Spin up a minimal in-memory IDC SQLite DB and patch get_connection."""
    db_path = tmp_path_factory.mktemp("idc") / "infra_canvas.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS infra_designs (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            graph_json TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
            template_id TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS idc_templates (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT,
            description TEXT,
            graph_json TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
            tags TEXT DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS idc_assessments (
            id TEXT PRIMARY KEY,
            design_id TEXT NOT NULL,
            assessment_type TEXT DEFAULT 'compliance',
            findings_json TEXT DEFAULT '[]',
            score REAL DEFAULT 0.0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS idc_runbooks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            category TEXT,
            severity TEXT DEFAULT 'medium',
            trigger_event TEXT,
            description TEXT,
            steps_json TEXT DEFAULT '[]',
            nist_controls TEXT DEFAULT '[]',
            tags TEXT DEFAULT '[]',
            execution_count INTEGER DEFAULT 0,
            last_executed_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS idc_sops (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            category TEXT,
            status TEXT DEFAULT 'draft',
            owner TEXT,
            description TEXT,
            steps_json TEXT DEFAULT '[]',
            nist_controls TEXT DEFAULT '[]',
            tags TEXT DEFAULT '[]',
            approval_actor TEXT,
            approval_date TEXT,
            review_interval_days INTEGER DEFAULT 365,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO infra_designs(id, name, description, graph_json, classification, created_at, updated_at)
        VALUES ('test-design-01', 'Test Design', '', '{"nodes":[],"edges":[]}', 'CUI', '2026-01-01', '2026-01-01');
    """)
    conn.commit()
    return conn, db_path


@pytest.fixture(scope="module")
def _app(_idc_db):
    """Create Flask test app with IDC blueprint registered and IDC DB patched."""
    conn, db_path = _idc_db

    # Patch infra_canvas.db.init_db.get_connection to return our test DB
    def _fake_get_conn():
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return c

    # Patch auth so tests don't need a real user in the main DB
    _fake_user = {
        "id": "test-admin",
        "email": "test@icdev.local",
        "display_name": "Test Admin",
        "role": "admin",
        "status": "active",
    }

    def _noop_before_request():
        from flask import g
        g.current_user = _fake_user
        return None

    with patch("tools.infra_canvas.blueprint._get_conn", side_effect=_fake_get_conn), \
         patch("tools.dashboard.auth._auth_before_request", side_effect=_noop_before_request):
        from tools.dashboard.app import create_app
        app = create_app()
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret"
        yield app


@pytest.fixture(scope="module")
def client(_app):
    """Flask test client — auth already patched at app level."""
    with _app.test_client() as c:
        yield c


# ── Page load tests (GET /infra/emit) ─────────────────────────────────────────

class TestEmitPageGet:
    """GET /infra/emit — page renders correctly."""

    def test_page_loads_200(self, client):
        """Case 1: GET /infra/emit returns 200."""
        resp = client.get("/infra/emit")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

    def test_page_has_project_field(self, client):
        """Case 2a: Form has project field."""
        resp = client.get("/infra/emit")
        html = resp.data.decode("utf-8")
        assert "project" in html.lower(), "Missing project field"

    def test_page_has_target_field(self, client):
        """Case 2b: Form has target field."""
        resp = client.get("/infra/emit")
        html = resp.data.decode("utf-8")
        assert "target" in html.lower(), "Missing target field"

    def test_page_has_csp_field(self, client):
        """Case 2c: Form has csp field."""
        resp = client.get("/infra/emit")
        html = resp.data.decode("utf-8")
        assert "csp" in html.lower(), "Missing CSP field"

    def test_page_has_cui_banner(self, client):
        """Case 3: CUI classification banner present."""
        resp = client.get("/infra/emit")
        html = resp.data.decode("utf-8")
        assert "CUI" in html, "Missing CUI banner"

    def test_page_title_contains_emit(self, client):
        """Case 4: Page title or heading contains 'Emit'."""
        resp = client.get("/infra/emit")
        html = resp.data.decode("utf-8")
        assert "Emit" in html or "emit" in html, "Missing 'Emit' in page content"


# ── POST /infra/emit/run — validation errors ───────────────────────────────────

class TestEmitRunValidation:
    """POST /infra/emit/run — request validation."""

    def test_missing_project_returns_400(self, client):
        """Case 5: Missing project field → 400."""
        resp = client.post(
            "/infra/emit/run",
            json={"target": "terraform", "csp": "aws"},
        )
        assert resp.status_code == 400

    def test_missing_target_returns_400(self, client):
        """Case 6: Missing target field → 400."""
        resp = client.post(
            "/infra/emit/run",
            json={"project": json.dumps(_TF_GRAPH), "csp": "aws"},
        )
        assert resp.status_code == 400

    def test_missing_csp_returns_400(self, client):
        """Case 7: Missing csp field → 400."""
        resp = client.post(
            "/infra/emit/run",
            json={"project": json.dumps(_TF_GRAPH), "target": "terraform"},
        )
        assert resp.status_code == 400

    def test_unsupported_csp_target_combo_returns_422(self, client):
        """Case 12: Pulumi + GCP (unsupported) → 422."""
        resp = client.post(
            "/infra/emit/run",
            json={
                "project": json.dumps(_TF_GRAPH),
                "target": "pulumi",
                "csp": "gcp",
            },
        )
        assert resp.status_code == 422


# ── POST /infra/emit/run — successful emits ───────────────────────────────────

class TestEmitRunSuccess:
    """POST /infra/emit/run — valid payloads produce file content."""

    def _post_run(self, client, graph, target, csp):
        return client.post(
            "/infra/emit/run",
            json={"project": json.dumps(graph), "target": target, "csp": csp},
        )

    def test_terraform_aws_returns_files(self, client):
        """Case 8: terraform/aws → JSON with files dict."""
        resp = self._post_run(client, _TF_GRAPH, "terraform", "aws")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "files" in data
        assert len(data["files"]) > 0

    def test_ansible_aws_returns_files(self, client):
        """Case 9: ansible/aws → JSON with files dict."""
        resp = self._post_run(client, _ANSIBLE_GRAPH, "ansible", "aws")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "files" in data
        assert len(data["files"]) > 0

    def test_pulumi_azure_returns_files(self, client):
        """Case 10: pulumi/azure → JSON with files dict."""
        resp = self._post_run(client, _PULUMI_GRAPH, "pulumi", "azure")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "files" in data
        assert len(data["files"]) > 0

    def test_helm_aws_returns_files(self, client):
        """Case 11: helm/aws → JSON with files dict."""
        resp = self._post_run(client, _HELM_GRAPH, "helm", "aws")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "files" in data
        assert len(data["files"]) > 0

    def test_response_has_files_key(self, client):
        """Case 13: Response JSON contains 'files' key as dict."""
        resp = self._post_run(client, _TF_GRAPH, "terraform", "aws")
        data = resp.get_json()
        assert isinstance(data["files"], dict), "'files' must be a dict of {filename: content}"

    def test_response_has_target_key(self, client):
        """Case 14: Response JSON contains 'target' key."""
        resp = self._post_run(client, _TF_GRAPH, "terraform", "aws")
        data = resp.get_json()
        assert "target" in data
        assert data["target"] == "terraform"

    def test_response_has_csp_key(self, client):
        """Case 15: Response JSON contains 'csp' key."""
        resp = self._post_run(client, _TF_GRAPH, "terraform", "aws")
        data = resp.get_json()
        assert "csp" in data
        assert data["csp"] == "aws"
