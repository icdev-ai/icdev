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


@pytest.fixture
def icdev_db(tmp_path):
    """Temporary SQLite DB for use-case tests; tables created on-demand by the API."""
    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.close()
    return db_path
