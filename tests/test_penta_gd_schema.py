# CUI // SP-CTI
"""penta-gd-03 — AI GameDay schema + dead-code regression tests.

Covers:
  * ttx_sessions / ttx_injects gained an ``ontology_tags_json`` column. The
    column was read/written by apps/ai_gameday/blueprint.py but existed in NO
    runtime DDL, so create-session 400'd and the ontology/leaderboard routes
    500'd on a schema without it. db.py::migrate() must:
      - create the column for a FRESH install (it is now in _DDL), and
      - add it via a guarded ALTER for a PRE-EXISTING table that predates it.
  * The sim_lifecycle_e2e demo and the stale flask_login scaffold in auth.py
    stay deleted. (The player-registration module penta-gd-03 removed alongside
    them was later re-introduced as a wired feature by gdx-reg-01 — see the note
    above test_sim_lifecycle_demo_deleted.)

Uses a temp SQLite DB seeded with the shared conftest schema + the storage
translate layer (get_connection with %s params) — no raw sqlite3 in the query
path, mirroring test_penta_gd_league.py.
"""

from __future__ import annotations

import importlib
import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
AI_GAMEDAY = REPO_ROOT / "apps" / "ai_gameday"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def gameday_db(tmp_path, monkeypatch):
    """Temp SQLite DB seeded with the shared schema; ttx_* tables are created by
    db.py::migrate() (they are NOT in MINIMAL_ICDEV_SCHEMA)."""
    from tests.conftest import MINIMAL_ICDEV_SCHEMA

    db_path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))

    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.executescript(MINIMAL_ICDEV_SCHEMA)
    conn.commit()
    conn.close()

    # migrate() is process-global; reset the guard so it runs against this DB.
    db_mod = importlib.import_module("apps.ai_gameday.db")
    db_mod._migrated = False
    return db_path, db_mod


# ---------------------------------------------------------------------------
# Dead-code removal
# ---------------------------------------------------------------------------

# NOTE: penta-gd-03 deleted apps/ai_gameday/registration.py as dead code and
# asserted here that it stayed deleted. gdx-reg-01 (6cfb79e1e) then re-introduced
# pre-session registration as a wired feature — blueprint.py imports it and
# serves /gameday/session/<id>/register — so that assertion has been removed
# rather than the live module re-deleted. The feature is covered by
# tests/test_gameday_registration.py and tests/test_gameday_registration_routes.py.


def test_sim_lifecycle_demo_deleted():
    assert not (REPO_ROOT / "tools" / "ttx" / "scenarios" / "sim_lifecycle_e2e.py").exists()
    assert not (
        REPO_ROOT / "icdev" / "tools" / "ttx" / "scenarios" / "sim_lifecycle_e2e.py"
    ).exists()


def test_auth_flask_login_scaffold_removed():
    src = (AI_GAMEDAY / "auth.py").read_text(encoding="utf-8")
    assert "flask_login" not in src, "dead flask_login import must be gone"
    assert "login_manager" not in src, "dead login_manager scaffold must be gone"
    assert "GameDayUser" not in src, "dead GameDayUser scaffold must be gone"
    # The real decorators from penta-gd-01 remain.
    assert "def login_required" in src
    assert "def require_facilitator" in src


# ---------------------------------------------------------------------------
# ontology_tags_json migration
# ---------------------------------------------------------------------------

def test_migrate_creates_every_ddl_table(gameday_db):
    """migrate() must not stop part-way through _DDL.

    Statements were split on a bare ``;``, so the semicolon inside the comment
    `-- Migration 325 is the shared fix; these blocks ...` cut the script mid-
    comment: the remainder was executed as SQL, raised, and every table declared
    after that point — ttx_registrations among them — was silently never created.
    """
    _db_path, db_mod = gameday_db
    from tools.db.storage import get_connection

    db_mod.migrate()
    conn = get_connection()

    declared = set(
        re.findall(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", db_mod._DDL, re.IGNORECASE)
    )
    assert "ttx_registrations" in declared, "test is stale — the DDL no longer declares it"
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    created = {r[0] for r in rows}
    assert declared - created == set(), f"migrate() skipped: {sorted(declared - created)}"


def test_fresh_migrate_creates_ontology_column(gameday_db):
    """A fresh install: migrate() creates ttx_sessions/ttx_injects WITH the column."""
    _db_path, db_mod = gameday_db
    from tools.db.storage import column_exists, get_connection

    db_mod.migrate()
    conn = get_connection()
    assert column_exists(conn, "ttx_sessions", "ontology_tags_json")
    assert column_exists(conn, "ttx_injects", "ontology_tags_json")


def test_migrate_adds_ontology_column_to_preexisting_tables(gameday_db):
    """A DB whose ttx_sessions/ttx_injects predate the column: migrate() must add
    it via the guarded ALTER path (CREATE TABLE IF NOT EXISTS is a no-op)."""
    _db_path, db_mod = gameday_db
    from tools.db.storage import column_exists, get_connection

    conn = get_connection()
    # The shared conftest schema creates ttx_sessions/ttx_injects WITH the column
    # (the test schema is ahead of production — exactly the mismatch this bug is
    # about). Drop and recreate them in the OLD shape to simulate a production DB
    # that predates ontology_tags_json.
    conn.execute("DROP TABLE IF EXISTS ttx_injects")
    conn.execute("DROP TABLE IF EXISTS ttx_sessions")
    conn.commit()
    conn.execute(
        """CREATE TABLE ttx_sessions (
               session_id INTEGER PRIMARY KEY AUTOINCREMENT,
               scenario_slug TEXT NOT NULL,
               session_mode TEXT NOT NULL DEFAULT 'live',
               state TEXT NOT NULL DEFAULT 'pending',
               join_code TEXT NOT NULL UNIQUE,
               config_json TEXT DEFAULT '{}',
               created_at TEXT NOT NULL DEFAULT (datetime('now'))
           )"""
    )
    conn.execute(
        """CREATE TABLE ttx_injects (
               inject_id TEXT PRIMARY KEY,
               session_id INTEGER NOT NULL,
               slug TEXT NOT NULL,
               title TEXT NOT NULL,
               config_json TEXT DEFAULT '{}',
               created_at TEXT NOT NULL DEFAULT (datetime('now'))
           )"""
    )
    conn.commit()

    assert not column_exists(conn, "ttx_sessions", "ontology_tags_json")
    assert not column_exists(conn, "ttx_injects", "ontology_tags_json")

    db_mod.migrate()

    conn = get_connection()
    assert column_exists(conn, "ttx_sessions", "ontology_tags_json"), (
        "migrate() must ALTER-ADD the column onto a pre-existing ttx_sessions"
    )
    assert column_exists(conn, "ttx_injects", "ontology_tags_json"), (
        "migrate() must ALTER-ADD the column onto a pre-existing ttx_injects"
    )


def test_create_session_persists_ontology_tags(gameday_db):
    """End-to-end: after migrate(), a session row round-trips ontology_tags_json —
    the exact write/read the blueprint create-session + ontology routes perform."""
    _db_path, db_mod = gameday_db
    db_mod.migrate()

    from tools.ttx.engine import TTXEngine
    from tools.db.storage import get_connection

    engine = TTXEngine()
    session = engine.create_session("ai_gameday", "Facilitator")
    sid = session["session_id"]

    conn = get_connection()
    # Mirror blueprint.api_create_session's enrichment write.
    conn.execute(
        "UPDATE ttx_sessions SET ontology_tags_json = %s WHERE session_id = %s",
        ('{"classes": ["ai_ops"]}', sid),
    )
    conn.commit()

    row = conn.execute(
        "SELECT ontology_tags_json FROM ttx_sessions WHERE session_id = %s", (sid,)
    ).fetchone()
    assert row["ontology_tags_json"] == '{"classes": ["ai_ops"]}'
