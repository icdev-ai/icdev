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
