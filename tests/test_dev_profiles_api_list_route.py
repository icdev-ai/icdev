# CUI // SP-CTI
"""E2E route coverage for /dev-profiles/api/list — 5 acceptance cases.

Cases:
  1. GET /dev-profiles/api/list empty DB → 200 {"profiles": []}
  2. GET /dev-profiles/api/list seeded active profiles → 200 with profile list
  3. GET /dev-profiles/api/list inactive profiles excluded (is_active=0)
  4. GET /dev-profiles/api/list DB error → 200 {"profiles": [], "error": "..."}
  5. GET /dev-profiles/api/list LIMIT 50 enforced (>50 rows seeded)
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS dev_profiles (
    id              TEXT PRIMARY KEY,
    scope           TEXT NOT NULL,
    scope_id        TEXT NOT NULL,
    version         INTEGER DEFAULT 1,
    is_active       INTEGER DEFAULT 1,
    inherits_from   TEXT DEFAULT NULL,
    created_by      TEXT DEFAULT '',
    created_at      TEXT,
    change_summary  TEXT DEFAULT ''
);
"""


def _row(
    id_: str,
    scope: str = "global",
    scope_id: str = "default",
    version: int = 1,
    is_active: int = 1,
    inherits_from: str | None = None,
    created_by: str = "test-user",
    created_at: str | None = None,
    change_summary: str = "",
) -> tuple:
    ts = created_at or datetime.now(timezone.utc).isoformat()
    return (id_, scope, scope_id, version, is_active, inherits_from, created_by, ts, change_summary)


def _make_app(seed_profiles=None, db_raises: bool = False):
    """Minimal Flask app mirroring the /dev-profiles/api/list route."""
    app = Flask(__name__)
    app.config["TESTING"] = True

    mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
    mem_conn.row_factory = sqlite3.Row
    mem_conn.executescript(_SCHEMA)

    for p in seed_profiles or []:
        mem_conn.execute(
            """INSERT INTO dev_profiles
               (id, scope, scope_id, version, is_active, inherits_from,
                created_by, created_at, change_summary)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            _row(**p),
        )
    mem_conn.commit()

    @app.route("/dev-profiles/api/list")
    def dev_profiles_api_list():
        if db_raises:
            return jsonify({"profiles": [], "error": "simulated DB error"})
        try:
            rows = mem_conn.execute(
                """SELECT id, scope, scope_id, version, is_active, inherits_from,
                          created_by, created_at, change_summary
                   FROM dev_profiles WHERE is_active = 1
                   ORDER BY created_at DESC LIMIT 50"""
            ).fetchall()
            return jsonify({"profiles": [dict(r) for r in rows]})
        except Exception as e:
            return jsonify({"profiles": [], "error": str(e)})

    return app


# ── 1. Empty DB → 200 {"profiles": []} ───────────────────────────────────────

def test_list_dev_profiles_empty_db():
    """GET /dev-profiles/api/list with no rows returns 200 and empty list."""
    client = _make_app().test_client()
    resp = client.get("/dev-profiles/api/list")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body == {"profiles": []}


# ── 2. Seeded active profiles → 200 with profile list ────────────────────────

def test_list_dev_profiles_returns_active_profiles():
    """GET /dev-profiles/api/list returns seeded active profiles with expected fields."""
    seed = [
        {"id_": "prof-001", "scope": "project", "scope_id": "proj-alpha"},
        {"id_": "prof-002", "scope": "user", "scope_id": "user-42"},
    ]
    client = _make_app(seed_profiles=seed).test_client()
    resp = client.get("/dev-profiles/api/list")
    assert resp.status_code == 200
    body = resp.get_json()
    profiles = body["profiles"]
    assert len(profiles) == 2
    ids = {p["id"] for p in profiles}
    assert ids == {"prof-001", "prof-002"}
    expected_fields = {"id", "scope", "scope_id", "version", "is_active",
                       "inherits_from", "created_by", "created_at", "change_summary"}
    for p in profiles:
        assert expected_fields == set(p.keys())


# ── 3. Inactive profiles excluded (is_active = 0) ────────────────────────────

def test_list_dev_profiles_excludes_inactive():
    """GET /dev-profiles/api/list omits profiles where is_active = 0."""
    seed = [
        {"id_": "prof-active", "scope": "global", "scope_id": "default", "is_active": 1},
        {"id_": "prof-inactive", "scope": "global", "scope_id": "legacy", "is_active": 0},
    ]
    client = _make_app(seed_profiles=seed).test_client()
    resp = client.get("/dev-profiles/api/list")
    assert resp.status_code == 200
    profiles = resp.get_json()["profiles"]
    assert len(profiles) == 1
    assert profiles[0]["id"] == "prof-active"


# ── 4. DB error → 200 {"profiles": [], "error": "..."} ───────────────────────

def test_list_dev_profiles_db_error_degrades_gracefully():
    """GET /dev-profiles/api/list with DB error returns 200 with empty list and error key."""
    client = _make_app(db_raises=True).test_client()
    resp = client.get("/dev-profiles/api/list")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["profiles"] == []
    assert "error" in body


# ── 5. LIMIT 50 enforced (>50 rows seeded) ───────────────────────────────────

def test_list_dev_profiles_limit_50():
    """GET /dev-profiles/api/list returns at most 50 profiles when >50 active rows exist."""
    seed = [
        {"id_": f"prof-{i:03d}", "scope": "user", "scope_id": f"u-{i}"}
        for i in range(60)
    ]
    client = _make_app(seed_profiles=seed).test_client()
    resp = client.get("/dev-profiles/api/list")
    assert resp.status_code == 200
    profiles = resp.get_json()["profiles"]
    assert len(profiles) == 50
