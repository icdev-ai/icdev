# CUI // SP-CTI
"""Regression tests for ODC Kill Chain + AI-trace routes — RLS connection misuse.

Defect (obx-fix-01): oc_api_kill_chain and oc_api_ai_trace used the global
RLS-enforcing get_connection() to read canvas-namespaced tables
(canvas_kg_nodes / canvas_kg_edges / canvas_ai_decisions).  Those tables have no
tenant_id / classification columns, so the auto-injected RLS predicate raises
UndefinedColumn under any authenticated security context — kill-chain silently
returned an empty graph and ai-trace returned 500.  The fix switches both routes
to get_canvas_connection() (RLS bypassed).

These tests seed a SQLite DB (conftest forces ICDEV_STORAGE_BACKEND=sqlite) and
assert the routes surface the seeded rows.

NIST 800-53: AC-3, AU-2, SI-4
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_SEED_SCHEMA = """
CREATE TABLE IF NOT EXISTS canvas_kg_nodes (
    id            TEXT PRIMARY KEY,
    canvas        TEXT NOT NULL,
    design_id     TEXT NOT NULL,
    node_id       TEXT NOT NULL,
    node_type     TEXT,
    label         TEXT,
    metadata_json TEXT,
    updated_at    TEXT
);
CREATE TABLE IF NOT EXISTS canvas_kg_edges (
    id            TEXT PRIMARY KEY,
    canvas        TEXT NOT NULL,
    design_id     TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    target_id     TEXT NOT NULL,
    edge_type     TEXT,
    metadata_json TEXT,
    updated_at    TEXT
);
CREATE TABLE IF NOT EXISTS canvas_ai_decisions (
    id              TEXT PRIMARY KEY,
    canvas_type     TEXT NOT NULL,
    record_id       TEXT,
    decision_type   TEXT NOT NULL,
    decision        TEXT NOT NULL,
    rationale       TEXT,
    model_used      TEXT,
    confidence      REAL,
    alternatives    TEXT DEFAULT '[]',
    trace_id        TEXT,
    span_id         TEXT,
    actor           TEXT NOT NULL DEFAULT 'icdev-system',
    project_id      TEXT,
    classification  TEXT NOT NULL DEFAULT 'CUI',
    created_at      TEXT NOT NULL DEFAULT '2026-01-01T00:00:00Z'
);
"""


@pytest.fixture(scope="module")
def _seeded_db(tmp_path_factory):
    """Seed a SQLite DB with canvas KG + AI-decision rows and return its path."""
    db_path = tmp_path_factory.mktemp("odc_rls") / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(_SEED_SCHEMA)
        # Two kill-chain nodes (canvas='sg') connected by one edge.
        conn.execute(
            "INSERT INTO canvas_kg_nodes "
            "(id, canvas, design_id, node_id, node_type, label, metadata_json, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("n1", "sg", "d1", "actor-sandworm", "ThreatActor", "Sandworm",
             json.dumps({"technique_ids": ["T1059"], "tactic_ids": ["TA0002"]}),
             "2026-01-01T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO canvas_kg_nodes "
            "(id, canvas, design_id, node_id, node_type, label, metadata_json, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("n2", "sg", "d1", "tech-T1059", "Technique", "Command and Scripting Interpreter",
             json.dumps({"technique_ids": ["T1059"]}), "2026-01-01T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO canvas_kg_edges "
            "(id, canvas, design_id, source_id, target_id, edge_type, metadata_json, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("e1", "sg", "d1", "actor-sandworm", "tech-T1059", "uses",
             json.dumps({"delta_hours": 3}), "2026-01-01T00:00:00Z"),
        )
        # One ODC AI decision.
        conn.execute(
            "INSERT INTO canvas_ai_decisions "
            "(id, canvas_type, record_id, decision_type, decision, actor, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            ("dec1", "odc", "rec-1", "assessment", "approve", "icdev-system",
             "2026-01-01T00:00:00Z"),
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


@pytest.fixture(scope="module")
def client(_seeded_db):
    """Minimal Flask app hosting only the ODC blueprint, pointed at the seeded DB."""
    import os
    prev = os.environ.get("ICDEV_DB_PATH")
    os.environ["ICDEV_DB_PATH"] = str(_seeded_db)
    os.environ["ICDEV_STORAGE_BACKEND"] = "sqlite"
    os.environ["ICDEV_OBSERVABILITY_ENABLED"] = "true"
    try:
        from flask import Flask
        from tools.observability_canvas.blueprint import create_observability_blueprint

        bp = create_observability_blueprint()
        assert bp is not None, "blueprint disabled"

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret"
        app.register_blueprint(bp, url_prefix="/observability")

        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user_id"] = "test-admin"
            yield c
    finally:
        if prev is None:
            os.environ.pop("ICDEV_DB_PATH", None)
        else:
            os.environ["ICDEV_DB_PATH"] = prev


def test_kill_chain_returns_nodes_and_links(client):
    """Kill-chain route returns the seeded KG graph (not a swallowed empty error)."""
    resp = client.get("/observability/api/kill-chain")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.get_json()
    assert "error" not in data, f"RLS/query error leaked into payload: {data.get('error')}"
    assert len(data["nodes"]) >= 2, f"Expected >=2 nodes, got {data['nodes']}"
    node_ids = {n["id"] for n in data["nodes"]}
    assert {"actor-sandworm", "tech-T1059"} <= node_ids
    assert len(data["links"]) >= 1, f"Expected >=1 link, got {data['links']}"
    link = data["links"][0]
    assert link["source"] == "actor-sandworm" and link["target"] == "tech-T1059"


def test_kill_chain_actor_filter(client):
    """Actor filter keeps the matching ThreatActor node."""
    resp = client.get("/observability/api/kill-chain?actor=sandworm")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "error" not in data
    labels = {n["label"].lower() for n in data["nodes"]}
    assert any("sandworm" in lbl for lbl in labels)


def test_ai_trace_returns_200_with_rows(client):
    """AI-trace route returns 200 and the seeded ODC decision (no RLS 500)."""
    resp = client.get("/observability/api/ai-trace")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.get_json()
    assert data["ok"] is True, f"ai-trace not ok: {data}"
    assert data["canvas"] == "odc"
    assert len(data["decisions"]) >= 1, f"Expected >=1 decision, got {data['decisions']}"
    assert data["decisions"][0]["record_id"] == "rec-1"


def test_ai_trace_record_id_filter(client):
    """AI-trace record_id filter returns 200 and only that record's decisions."""
    resp = client.get("/observability/api/ai-trace?record_id=rec-1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert all(d["record_id"] == "rec-1" for d in data["decisions"])
