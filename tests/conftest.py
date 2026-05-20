"""Minimal conftest for worktree task-8a35ad18d2 tests."""
import os
import sqlite3
import sys
from pathlib import Path

import pytest

# Ensure the main repo root is on sys.path so tools/icdev packages resolve.
_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Force SQLite backend for tests (override .env PostgreSQL setting — same as main conftest)
os.environ["ICDEV_STORAGE_BACKEND"] = "sqlite"
os.environ["NOCC_STORAGE_BACKEND"] = "sqlite"
os.environ["PMC_STORAGE_BACKEND"] = "sqlite"
os.environ["CCC_STORAGE_BACKEND"] = "sqlite"
os.environ["DSOC_STORAGE_BACKEND"] = "sqlite"
os.environ["AAC_STORAGE_BACKEND"] = "sqlite"

# Minimal CREATE TABLE stubs for AAC tables — SQLite-compatible, no FK/CHECK constraints.
# Used by tests that need the schema without importing the full canvas init_db.
MINIMAL_ICDEV_SCHEMA = {
    "aac_scans": (
        "CREATE TABLE IF NOT EXISTS aac_scans ("
        "  scan_id    INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  input_type TEXT NOT NULL,"
        "  input_ref  TEXT NOT NULL,"
        "  status     TEXT NOT NULL DEFAULT 'pending',"
        "  created_at TEXT DEFAULT CURRENT_TIMESTAMP"
        ")"
    ),
    "aac_opportunities": (
        "CREATE TABLE IF NOT EXISTS aac_opportunities ("
        "  opportunity_id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  scan_id        INTEGER NOT NULL,"
        "  module_path    TEXT NOT NULL,"
        "  function_name  TEXT NOT NULL,"
        "  language       TEXT NOT NULL,"
        "  pattern_type   TEXT NOT NULL,"
        "  ai_paradigm    TEXT NOT NULL,"
        "  created_at     TEXT DEFAULT CURRENT_TIMESTAMP"
        ")"
    ),
    "aac_scores": (
        "CREATE TABLE IF NOT EXISTS aac_scores ("
        "  score_id        INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  opportunity_id  INTEGER NOT NULL,"
        "  composite_score REAL,"
        "  scored_at       TEXT DEFAULT CURRENT_TIMESTAMP"
        ")"
    ),
    "aac_roadmaps": (
        "CREATE TABLE IF NOT EXISTS aac_roadmaps ("
        "  id         INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  scan_id    INTEGER NOT NULL,"
        "  roadmap_id TEXT NOT NULL UNIQUE,"
        "  title      TEXT NOT NULL,"
        "  created_at TEXT DEFAULT CURRENT_TIMESTAMP"
        ")"
    ),
    "aac_audit_log": (
        "CREATE TABLE IF NOT EXISTS aac_audit_log ("
        "  id         INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  event_type TEXT NOT NULL,"
        "  scan_id    INTEGER,"
        "  actor      TEXT NOT NULL DEFAULT 'system',"
        "  created_at TEXT DEFAULT CURRENT_TIMESTAMP"
        ")"
    ),
}


@pytest.fixture
def icdev_db(tmp_path):
    """Temporary SQLite DB for use-case tests; tables created on-demand by the API."""
    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.close()
    return db_path


@pytest.fixture
def nocc_db(tmp_path, monkeypatch):
    """In-memory SQLite NOCC DB for unit tests."""
    db_path = tmp_path / "noc_canvas.db"
    monkeypatch.setenv("NOCC_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("NOCC_DB_PATH", str(db_path))
    from tools.noc_canvas.db.init_db import init_db, get_connection
    init_db()
    conn = get_connection()
    yield conn
    conn.close()


@pytest.fixture
def ccc_db(tmp_path, monkeypatch):
    """In-memory SQLite CCC DB for unit tests."""
    db_path = tmp_path / "ccc_canvas.db"
    monkeypatch.setenv("CCC_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("CCC_DB_PATH", str(db_path))
    from tools.ccc_canvas.db.init_db import init_db, get_connection
    init_db()
    conn = get_connection()
    yield conn
    conn.close()


@pytest.fixture
def dsoc_db(tmp_path, monkeypatch):
    """In-memory SQLite DSOC DB for unit tests."""
    db_path = tmp_path / "dsoc_canvas.db"
    monkeypatch.setenv("DSOC_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("DSOC_DB_PATH", str(db_path))
    from tools.dsoc_canvas.db.init_db import init_db, get_connection
    init_db()
    conn = get_connection()
    yield conn
    conn.close()


@pytest.fixture
def pmc_db(tmp_path, monkeypatch):
    """In-memory SQLite PMC DB for unit tests."""
    db_path = tmp_path / "pmc_canvas.db"
    monkeypatch.setenv("PMC_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("PMC_DB_PATH", str(db_path))
    from tools.pmc_canvas.db.init_db import init_db, get_connection
    init_db()
    conn = get_connection()
    yield conn
    conn.close()


@pytest.fixture
def aac_db(tmp_path, monkeypatch):
    """In-memory SQLite AAC DB for unit tests."""
    db_path = tmp_path / "aac_canvas.db"
    monkeypatch.setenv("AAC_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("AAC_DB_PATH", str(db_path))
    from tools.ai_augmentation.db.init_db import init_db, get_connection
    init_db()
    conn = get_connection()
    yield conn
    conn.close()
