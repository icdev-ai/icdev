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


MINIMAL_ICDEV_SCHEMA = """
CREATE TABLE IF NOT EXISTS studio_workflows (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    template_yaml TEXT DEFAULT '',
    category TEXT DEFAULT 'custom',
    shared INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS studio_workflow_runs (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    started_at TEXT,
    finished_at TEXT,
    error TEXT DEFAULT '',
    FOREIGN KEY (workflow_id) REFERENCES studio_workflows(id)
);
CREATE TABLE IF NOT EXISTS studio_workflow_run_steps (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    step_name TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    output TEXT DEFAULT '',
    error TEXT DEFAULT '',
    started_at TEXT,
    finished_at TEXT,
    FOREIGN KEY (run_id) REFERENCES studio_workflow_runs(id)
);
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    email TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'viewer',
    classification TEXT DEFAULT 'CUI',
    status TEXT DEFAULT 'active',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS groups (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    classification TEXT DEFAULT 'CUI',
    status TEXT DEFAULT 'active',
    created_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS group_members (
    group_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    added_by TEXT,
    added_at TEXT NOT NULL,
    PRIMARY KEY (group_id, user_id)
);
CREATE TABLE IF NOT EXISTS group_roles (
    group_id TEXT NOT NULL,
    role TEXT NOT NULL,
    canvas_scope TEXT,
    granted_by TEXT,
    granted_at TEXT,
    PRIMARY KEY (group_id, role, canvas_scope)
);
CREATE TABLE IF NOT EXISTS canvas_access_grants (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    principal_type TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    canvas_name TEXT NOT NULL,
    access_level TEXT NOT NULL DEFAULT 'read',
    granted_by TEXT NOT NULL,
    granted_at TEXT NOT NULL,
    expires_at TEXT,
    revoked_at TEXT,
    UNIQUE (tenant_id, principal_type, principal_id, canvas_name)
);
CREATE TABLE IF NOT EXISTS abac_decisions (
    id TEXT PRIMARY KEY,
    user_id TEXT,
    role TEXT,
    tenant_id TEXT,
    resource TEXT,
    action TEXT,
    policy_matched TEXT,
    decision TEXT,
    reason TEXT,
    evaluated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_mfa (
    user_id TEXT PRIMARY KEY,
    secret TEXT NOT NULL,
    backup_codes TEXT NOT NULL DEFAULT '[]',
    enrolled_at TEXT NOT NULL,
    last_used_at TEXT,
    disabled_at TEXT,
    disabled_by TEXT
);
CREATE TABLE IF NOT EXISTS mfa_attempts (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    success INTEGER NOT NULL DEFAULT 0,
    method TEXT NOT NULL DEFAULT 'totp',
    ip_address TEXT,
    attempted_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_trail (
    id TEXT PRIMARY KEY,
    tenant_id TEXT,
    user_id TEXT,
    action TEXT NOT NULL,
    resource TEXT,
    details TEXT,
    classification TEXT DEFAULT 'CUI',
    recorded_at TEXT NOT NULL
);
"""


@pytest.fixture
def icdev_db(tmp_path):
    """Temporary SQLite DB for use-case tests; studio tables pre-created."""
    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(MINIMAL_ICDEV_SCHEMA)
    conn.commit()
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
