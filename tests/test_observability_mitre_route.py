# CUI // SP-CTI
"""Tests for ODC MITRE ATT&CK matrix/detail/sigma routes — 4 acceptance cases.

Cases:
  1. GET /observability/mitre returns 200 with MITRE matrix content
  2. GET /observability/mitre/T1059 returns 200 with technique details
  3. GET /observability/mitre/TXXX returns 404 for unknown technique
  4. POST /observability/mitre/sigma returns Sigma YAML for valid tid+sources

NIST 800-53: SI-3, SI-4, AU-2, SA-11
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

_SCHEMA = """
CREATE TABLE IF NOT EXISTS observability_designs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    graph_json TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    template_id TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS od_templates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT,
    description TEXT,
    graph_json TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    tags TEXT DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS od_snippets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT,
    description TEXT,
    graph_json TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    tags TEXT DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS od_assessments (
    id TEXT PRIMARY KEY,
    design_id TEXT,
    assessment_type TEXT NOT NULL,
    findings_json TEXT DEFAULT '[]',
    score REAL DEFAULT 0,
    grade TEXT DEFAULT 'F',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS od_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    design_id TEXT,
    user TEXT,
    action TEXT NOT NULL,
    detail TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS od_versions (
    id TEXT PRIMARY KEY,
    design_id TEXT,
    version_number INTEGER NOT NULL,
    graph_json TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    change_summary TEXT DEFAULT '',
    user_id TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS od_collab_sessions (
    id TEXT PRIMARY KEY,
    design_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    user_name TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL DEFAULT '#3498db',
    joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS odc_sops (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    sop_type TEXT NOT NULL DEFAULT 'custom',
    description TEXT DEFAULT '',
    approval_status TEXT NOT NULL DEFAULT 'draft',
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS odc_runbooks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'general',
    severity TEXT NOT NULL DEFAULT 'medium',
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


@pytest.fixture(scope="module")
def _db(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("mitre_route") / "observability_canvas.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn, db_path


@pytest.fixture(scope="module")
def _app(_db):
    _conn, db_path = _db

    def _fake_get_conn():
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return c

    def _noop_init_db():
        pass

    def _noop_before_request():
        return None

    with (
        patch("tools.dashboard.auth._auth_before_request", side_effect=_noop_before_request),
        patch("tools.observability_canvas.db.init_db.get_connection", side_effect=_fake_get_conn),
        patch("tools.observability_canvas.db.init_db.init_db", side_effect=_noop_init_db),
    ):
        from tools.dashboard.app import create_app

        app = create_app()
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret"
        yield app


@pytest.fixture(scope="module")
def client(_app):
    with _app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"] = "test-admin"
        yield c


# ── Case 1 ────────────────────────────────────────────────────────────────────


def test_mitre_matrix_returns_200(client):
    """Case 1: GET /observability/mitre renders MITRE ATT&CK matrix with technique cells."""
    resp = client.get("/observability/mitre")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    html = resp.data.decode("utf-8")
    assert "MITRE" in html, "MITRE heading missing from matrix page"
    assert "T1059" in html, "Known technique T1059 not rendered in matrix"


# ── Case 2 ────────────────────────────────────────────────────────────────────


def test_mitre_detail_known_tid(client):
    """Case 2: GET /observability/mitre/T1059 returns 200 with technique name."""
    resp = client.get("/observability/mitre/T1059")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    html = resp.data.decode("utf-8")
    assert "T1059" in html, "Technique ID not rendered on detail page"
    assert "Command" in html, "Technique name not rendered on detail page"


# ── Case 3 ────────────────────────────────────────────────────────────────────


def test_mitre_detail_unknown_tid_returns_404(client):
    """Case 3: GET /observability/mitre/TXXX returns 404 for an unknown technique."""
    resp = client.get("/observability/mitre/TXXX")
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"


# ── Case 4 ────────────────────────────────────────────────────────────────────


def test_mitre_sigma_post_returns_yaml(client):
    """Case 4: POST /observability/mitre/sigma returns Sigma YAML for T1059 + src-os-log."""
    payload = {"tid": "T1059", "sources": ["src-os-log"]}
    resp = client.post(
        "/observability/mitre/sigma",
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.get_json()
    assert data is not None, "Response is not JSON"
    assert data.get("rule_count", 0) >= 1, "Expected at least 1 generated rule"
    assert "sigma_yaml" in data, "sigma_yaml key missing from response"
    assert "T1059" in data["sigma_yaml"], "Technique ID not present in generated Sigma rule"
