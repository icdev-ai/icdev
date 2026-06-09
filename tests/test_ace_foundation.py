"""Foundation tests for ACE (Agentic Collaboration Environment) DB tables."""
import sqlite3
from tests.conftest import MINIMAL_ICDEV_SCHEMA

ACE_TABLES = [
    "ace_instances",
    "ace_coworkers",
    "ace_messages",
    "ace_artifacts",
    "ace_audit_log",
]


def test_db_tables_exist(tmp_path):
    db_path = tmp_path / "ace_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(MINIMAL_ICDEV_SCHEMA)
    conn.commit()

    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing = {row[0] for row in cur.fetchall()}
    conn.close()

    for table in ACE_TABLES:
        assert table in existing, f"Expected table '{table}' not found in schema"
