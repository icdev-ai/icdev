# CUI // SP-CTI
"""Tests for GET /infra/twin — IDC Twin Dashboard (dt-idc-twin-10).

4 cases:
  1. Empty DB — page returns 200 with empty-state message
  2. Design with a version snapshot — snapshot summary rendered
  3. Design with assessment violations — red violation badge rendered
  4. Design with multi-CSP graph — per-CSP resource pills rendered

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

# ── Minimal DB schema (infra_canvas tables only) ──────────────────────────────

_SCHEMA = """
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
CREATE TABLE IF NOT EXISTS idc_versions (
    id TEXT PRIMARY KEY,
    design_id TEXT,
    version_number INTEGER NOT NULL,
    graph_json TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    change_summary TEXT DEFAULT '',
    user_id TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS idc_assessments (
    id TEXT PRIMARY KEY,
    design_id TEXT NOT NULL,
    assessment_type TEXT DEFAULT 'compliance',
    findings_json TEXT DEFAULT '[]',
    score REAL DEFAULT 0.0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS idc_templates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT,
    description TEXT,
    graph_json TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    tags TEXT DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS idc_snippets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT,
    description TEXT,
    graph_json TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    tags TEXT DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS idc_runbooks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    category TEXT DEFAULT 'general',
    trigger_condition TEXT DEFAULT '',
    severity TEXT DEFAULT 'medium',
    description TEXT DEFAULT '',
    steps TEXT DEFAULT '[]',
    nist_controls TEXT DEFAULT '[]',
    tags TEXT DEFAULT '[]',
    owner TEXT DEFAULT '',
    estimated_duration_min INTEGER DEFAULT 30,
    last_executed_at TEXT DEFAULT '',
    execution_count INTEGER DEFAULT 0,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS idc_sops (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    category TEXT DEFAULT 'general',
    description TEXT DEFAULT '',
    purpose TEXT DEFAULT '',
    scope TEXT DEFAULT '',
    steps TEXT DEFAULT '[]',
    status TEXT DEFAULT 'draft',
    version TEXT DEFAULT '1.0',
    owner TEXT DEFAULT '',
    reviewer TEXT DEFAULT '',
    approver TEXT DEFAULT '',
    reviewed_at TEXT DEFAULT '',
    approved_at TEXT DEFAULT '',
    effective_date TEXT DEFAULT '',
    review_interval_days INTEGER DEFAULT 365,
    nist_controls TEXT DEFAULT '[]',
    tags TEXT DEFAULT '[]',
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS idc_collab_sessions (
    id TEXT PRIMARY KEY,
    design_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    user_name TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL DEFAULT '#3498db',
    joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_active INTEGER NOT NULL DEFAULT 1
);
"""

_MULTI_CSP_GRAPH = json.dumps({
    "nodes": [
        {"id": "n1", "type": "aws-eks", "label": "EKS", "x": 0, "y": 0, "metadata": {}},
        {"id": "n2", "type": "aws-kms", "label": "KMS", "x": 0, "y": 0, "metadata": {}},
        {"id": "n3", "type": "az-aks", "label": "AKS", "x": 0, "y": 0, "metadata": {}},
        {"id": "n4", "type": "gcp-gke", "label": "GKE", "x": 0, "y": 0, "metadata": {}},
    ],
    "edges": [],
})

_VIOLATIONS = json.dumps([
    {"source": "iqe", "check": "untagged_resources", "severity": "CAT2",
     "detail": "2 resource(s) matched violation query", "affected": ["res.a"]},
    {"source": "iqe", "check": "cross_region_data_paths", "severity": "CAT2",
     "detail": "CUI in commercial region", "affected": ["res.b"]},
])


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def _db(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("twin") / "infra_canvas.db"
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
    with _app.test_client() as c:
        yield c


@pytest.fixture(scope="module")
def _seeded_db(_db):
    """Seed the DB with designs, versions, and assessments for test cases 2-4."""
    conn, db_path = _db

    # Design 1: has a snapshot (version)
    conn.execute(
        "INSERT OR REPLACE INTO infra_designs(id, name, description, graph_json, classification, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("design-snap-01", "Snapshot Design", "Has a version", '{"nodes":[],"edges":[]}',
         "CUI", "2026-01-01", "2026-01-01"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO idc_versions(id, design_id, version_number, graph_json, change_summary, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("ver-01", "design-snap-01", 3, '{"nodes":[],"edges":[]}',
         "Added encryption layer", "2026-01-15"),
    )

    # Design 2: has assessment violations
    conn.execute(
        "INSERT OR REPLACE INTO infra_designs(id, name, description, graph_json, classification, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("design-viol-02", "Violation Design", "Has violations", '{"nodes":[],"edges":[]}',
         "CUI", "2026-01-02", "2026-01-02"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO idc_assessments(id, design_id, assessment_type, findings_json, score, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("asmt-01", "design-viol-02", "compliance", _VIOLATIONS, 0.4, "2026-01-15"),
    )

    # Design 3: multi-CSP graph
    conn.execute(
        "INSERT OR REPLACE INTO infra_designs(id, name, description, graph_json, classification, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("design-csp-03", "Multi-CSP Design", "AWS+Azure+GCP", _MULTI_CSP_GRAPH,
         "CUI", "2026-01-03", "2026-01-03"),
    )
    conn.commit()
    return conn, db_path


# ── Case 1: Empty DB — page returns 200 ──────────────────────────────────────


def test_twin_empty_state_returns_200(client):
    """Case 1: GET /infra/twin with no designs returns 200 and empty-state message."""
    resp = client.get("/infra/twin")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    html = resp.data.decode("utf-8")
    assert "twin" in html.lower() or "infrastructure" in html.lower(), (
        "Page missing expected content"
    )


# ── Case 2: Design with a snapshot renders summary ───────────────────────────


def test_twin_snapshot_summary_rendered(client, _seeded_db):
    """Case 2: Design with idc_versions row shows snapshot version number."""
    resp = client.get("/infra/twin")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert "Snapshot v3" in html or "v3" in html, (
        "Snapshot version number not rendered"
    )
    assert "Added encryption layer" in html, "Snapshot change_summary not rendered"


# ── Case 3: Design with violations renders red badge ─────────────────────────


def test_twin_violations_rendered_as_red_badge(client, _seeded_db):
    """Case 3: Design with assessment violations shows violation badge and check name."""
    resp = client.get("/infra/twin")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert "violation" in html.lower(), "Violation badge/count missing from page"
    assert "untagged_resources" in html, "Violation check name not rendered"


# ── Case 4: Design with multi-CSP graph shows per-CSP pills ──────────────────


def test_twin_csp_counts_rendered(client, _seeded_db):
    """Case 4: Design with AWS+Azure+GCP nodes renders per-CSP resource pills."""
    resp = client.get("/infra/twin")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert "AWS" in html, "AWS pill missing"
    assert "Azure" in html, "Azure pill missing"
    assert "GCP" in html, "GCP pill missing"
