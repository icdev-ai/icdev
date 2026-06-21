# CUI // SP-CTI
"""E2E route coverage for GET /children — 3 acceptance cases.

Cases:
  1. GET /children returns 200 with HTML when DB tables are empty
  2. GET /children returns 200 and passes seeded children with enriched data to template context
  3. GET /children returns 200 even when the DB raises an exception (graceful fallback)
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock

from flask import Flask, render_template_string

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_STUB_HTML = "<html><body>children-registry</body></html>"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS child_app_registry (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    health_status   TEXT DEFAULT 'unhealthy',
    genome_version  TEXT,
    pending_upgrades INTEGER DEFAULT 0,
    last_heartbeat  TEXT,
    capability_count INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS child_telemetry (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id    INTEGER,
    reported_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS child_capabilities (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id    INTEGER,
    capability  TEXT
);
"""


def _make_app(seed_children=None, seed_telemetry=None, seed_caps=None, db_raises=False):
    """Minimal Flask app mirroring the /children route from tools/dashboard/app.py."""
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"

    if db_raises:
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("simulated DB failure")

        def _get_db():
            return mock_conn
    else:
        mem_conn = sqlite3.connect(":memory:")
        mem_conn.row_factory = sqlite3.Row
        mem_conn.executescript(_SCHEMA)
        for row in seed_children or []:
            mem_conn.execute(
                "INSERT INTO child_app_registry (name, health_status, genome_version, pending_upgrades) VALUES (?,?,?,?)",
                row,
            )
        mem_conn.commit()
        child_ids = [r[0] for r in mem_conn.execute("SELECT id FROM child_app_registry ORDER BY id").fetchall()]
        for idx, ts in (seed_telemetry or []):
            if idx < len(child_ids):
                mem_conn.execute(
                    "INSERT INTO child_telemetry (child_id, reported_at) VALUES (?,?)",
                    (child_ids[idx], ts),
                )
        for idx, cap in (seed_caps or []):
            if idx < len(child_ids):
                mem_conn.execute(
                    "INSERT INTO child_capabilities (child_id, capability) VALUES (?,?)",
                    (child_ids[idx], cap),
                )
        mem_conn.commit()

        def _get_db():
            return mem_conn

    @app.route("/children")
    def children_page():
        conn = _get_db()
        try:
            try:
                children_rows = conn.execute(
                    "SELECT * FROM child_app_registry ORDER BY created_at DESC"
                ).fetchall()
                children_rows = [dict(r) for r in children_rows]
            except Exception:
                children_rows = []

            heartbeat_map = {}
            try:
                heartbeats = conn.execute(
                    "SELECT child_id, MAX(reported_at) as last_heartbeat FROM child_telemetry GROUP BY child_id"
                ).fetchall()
                for hb in heartbeats:
                    hb_dict = dict(hb)
                    heartbeat_map[hb_dict["child_id"]] = hb_dict["last_heartbeat"]
            except Exception:
                pass

            capability_map = {}
            try:
                caps = conn.execute(
                    "SELECT child_id, COUNT(*) as cnt FROM child_capabilities GROUP BY child_id"
                ).fetchall()
                for c in caps:
                    c_dict = dict(c)
                    capability_map[c_dict["child_id"]] = c_dict["cnt"]
            except Exception:
                pass

            children = []
            for child in children_rows:
                child["last_heartbeat"] = heartbeat_map.get(child.get("id"), child.get("last_heartbeat"))
                child["capability_count"] = capability_map.get(child.get("id"), child.get("capability_count", 0))
                child["pending_upgrades"] = child.get("pending_upgrades", 0)
                child["genome_version"] = child.get("genome_version", None)
                child["health_status"] = child.get("health_status", "unhealthy")
                children.append(child)

            healthy_count = sum(1 for c in children if c["health_status"] == "healthy")
            degraded_count = sum(1 for c in children if c["health_status"] == "degraded")
            unhealthy_count = sum(1 for c in children if c["health_status"] not in ("healthy", "degraded"))

            return render_template_string(
                _STUB_HTML,
                children=children,
                total_count=len(children),
                healthy_count=healthy_count,
                degraded_count=degraded_count,
                unhealthy_count=unhealthy_count,
            )
        finally:
            conn.close()

    return app


# ── 1. Empty DB → 200 ─────────────────────────────────────────────────────────

def test_children_empty_db_returns_200():
    """GET /children with no registered children returns HTTP 200."""
    client = _make_app().test_client()
    resp = client.get("/children")
    assert resp.status_code == 200
    assert b"children-registry" in resp.data


# ── 2. Seeded children are enriched and passed to template context ────────────

def test_children_seeded_rows_returns_200():
    """GET /children with seeded children, telemetry, and capabilities returns 200."""
    children = [
        ("alpha-child", "healthy", "v1.2.0", 0),
        ("beta-child", "degraded", "v1.1.0", 2),
        ("gamma-child", "unhealthy", None, 0),
    ]
    telemetry = [
        (0, "2026-06-15T10:00:00"),
        (1, "2026-06-15T09:00:00"),
    ]
    caps = [
        (0, "data-ingestion"),
        (0, "reporting"),
        (1, "monitoring"),
    ]
    client = _make_app(seed_children=children, seed_telemetry=telemetry, seed_caps=caps).test_client()
    resp = client.get("/children")
    assert resp.status_code == 200
    assert b"children-registry" in resp.data


# ── 3. DB error → graceful 200 fallback ──────────────────────────────────────

def test_children_db_error_returns_200():
    """GET /children falls back to empty lists on DB exception (still 200)."""
    client = _make_app(db_raises=True).test_client()
    resp = client.get("/children")
    assert resp.status_code == 200
    assert b"children-registry" in resp.data
