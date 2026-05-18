"""Unit tests for _seed_canvas_artifacts in tools/dashboard/api/chat.py.

Strategy:
- Two temp SQLite DBs: canvas_db (has sc_templates) and main_db (canvas_instances).
- _seed_canvas_artifacts calls get_connection(db_path=<canvas_db>) for template lookup
  and get_connection() (no args) for the canvas_instances INSERT.
- We monkeypatch `tools.dashboard.api.chat._gc` to route both call forms to the
  correct temp DB via a StorageConnection wrapper.
- _load_canvas_catalog is patched to return a controlled catalog entry so the test
  is independent of args/cloud_vendor_policy.yaml.
"""
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.db.storage import StorageConnection


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_storage_conn(sqlite3_conn: sqlite3.Connection) -> StorageConnection:
    sqlite3_conn.row_factory = sqlite3.Row
    return StorageConnection(sqlite3_conn, "sqlite")


@contextmanager
def _storage_ctx(sqlite3_conn: sqlite3.Connection):
    """Context manager that yields a StorageConnection and commits/closes on exit."""
    sc = _make_storage_conn(sqlite3_conn)
    sc.set_security_context(None)
    try:
        yield sc
        sc.commit()
    except Exception:
        sc.rollback()
        raise
    finally:
        sc.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CANVAS_KEY = "sc"
TEMPLATES_TABLE = "sc_templates"
CANVAS_DB_REL = "data/sc_canvas.db"  # relative path used as the db_path arg

CATALOG = {
    CANVAS_KEY: {
        "db": CANVAS_DB_REL,
        "templates_table": TEMPLATES_TABLE,
        "snippets_table": "",
    }
}


@pytest.fixture()
def canvas_db(tmp_path: Path) -> sqlite3.Connection:
    """Canvas SQLite DB pre-populated with one sc_templates row."""
    conn = sqlite3.connect(str(tmp_path / "sc_canvas.db"))
    conn.execute("CREATE TABLE IF NOT EXISTS sc_templates (name TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO sc_templates (name) VALUES (?)", ("test_template",))
    conn.commit()
    return conn


@pytest.fixture()
def main_db(tmp_path: Path) -> sqlite3.Connection:
    """Main ICDEV SQLite DB with canvas_instances table."""
    conn = sqlite3.connect(str(tmp_path / "icdev.db"))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS canvas_instances (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            tenant_id TEXT,
            canvas TEXT,
            artifact_type TEXT,
            artifact_name TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_seed_canvas_artifacts_returns_one_item(canvas_db, main_db, tmp_path):
    """Returned list has 1 item with name='test_template' when template exists."""
    from tools.dashboard.api.chat import _seed_canvas_artifacts

    canvas_db_path = str(tmp_path / "sc_canvas.db")

    def fake_gc(db_path=None):
        # Route canvas DB lookups to canvas_db; all other calls to main_db.
        if db_path and Path(db_path).name == "sc_canvas.db":
            conn = sqlite3.connect(canvas_db_path)
            conn.row_factory = sqlite3.Row
            sc = StorageConnection(conn, "sqlite")
            sc.set_security_context(None)
            return sc
        else:
            conn = sqlite3.connect(str(tmp_path / "icdev.db"))
            conn.row_factory = sqlite3.Row
            sc = StorageConnection(conn, "sqlite")
            sc.set_security_context(None)
            return sc

    use_case = {
        "canvas_seeds": [
            {"canvas": CANVAS_KEY, "templates": ["test_template"]}
        ]
    }

    with (
        patch("tools.dashboard.api.chat._load_canvas_catalog", return_value=CATALOG),
        patch("tools.db.storage.get_connection", side_effect=fake_gc),
    ):
        result = _seed_canvas_artifacts(use_case, session_id="sess-1", tenant_id="tenant-1")

    assert len(result) == 1
    assert result[0]["name"] == "test_template"
    assert result[0]["canvas"] == CANVAS_KEY
    assert result[0]["type"] == "template"


def test_seed_canvas_artifacts_inserts_canvas_instance(canvas_db, main_db, tmp_path):
    """canvas_instances row is written to main_db after seeding."""
    from tools.dashboard.api.chat import _seed_canvas_artifacts

    canvas_db_path = str(tmp_path / "sc_canvas.db")
    main_db_path = str(tmp_path / "icdev.db")

    def fake_gc(db_path=None):
        if db_path and Path(db_path).name == "sc_canvas.db":
            conn = sqlite3.connect(canvas_db_path)
        else:
            conn = sqlite3.connect(main_db_path)
        conn.row_factory = sqlite3.Row
        sc = StorageConnection(conn, "sqlite")
        sc.set_security_context(None)
        return sc

    use_case = {
        "canvas_seeds": [
            {"canvas": CANVAS_KEY, "templates": ["test_template"]}
        ]
    }

    with (
        patch("tools.dashboard.api.chat._load_canvas_catalog", return_value=CATALOG),
        patch("tools.db.storage.get_connection", side_effect=fake_gc),
    ):
        result = _seed_canvas_artifacts(use_case, session_id="sess-1", tenant_id="tenant-1")

    assert result[0]["instance_id"] is not None, "INSERT should have succeeded"

    # Verify the row landed in main_db
    verify_conn = sqlite3.connect(main_db_path)
    verify_conn.row_factory = sqlite3.Row
    row = verify_conn.execute(
        "SELECT * FROM canvas_instances WHERE artifact_name = ?", ("test_template",)
    ).fetchone()
    verify_conn.close()

    assert row is not None, "canvas_instances row not found"
    assert row["canvas"] == CANVAS_KEY
    assert row["artifact_type"] == "template"
    assert row["session_id"] == "sess-1"
    assert row["tenant_id"] == "tenant-1"


def test_seed_canvas_artifacts_missing_template_returns_empty(canvas_db, main_db, tmp_path):
    """Template name not in DB → empty seeded list; no exception raised."""
    from tools.dashboard.api.chat import _seed_canvas_artifacts

    canvas_db_path = str(tmp_path / "sc_canvas.db")
    main_db_path = str(tmp_path / "icdev.db")

    def fake_gc(db_path=None):
        path = canvas_db_path if (db_path and Path(db_path).name == "sc_canvas.db") else main_db_path
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        sc = StorageConnection(conn, "sqlite")
        sc.set_security_context(None)
        return sc

    use_case = {
        "canvas_seeds": [
            {"canvas": CANVAS_KEY, "templates": ["nonexistent_template"]}
        ]
    }

    with (
        patch("tools.dashboard.api.chat._load_canvas_catalog", return_value=CATALOG),
        patch("tools.db.storage.get_connection", side_effect=fake_gc),
    ):
        result = _seed_canvas_artifacts(use_case, session_id="sess-2", tenant_id="tenant-1")

    assert result == []


def test_seed_canvas_artifacts_no_seeds_returns_empty():
    """use_case with no canvas_seeds → empty list without touching DB."""
    from tools.dashboard.api.chat import _seed_canvas_artifacts

    use_case: dict = {}
    result = _seed_canvas_artifacts(use_case, session_id="sess-3", tenant_id="tenant-1")
    assert result == []


def test_seed_canvas_artifacts_test_template_absent_from_db_returns_empty(tmp_path):
    """'test_template' requested but sc_templates table is empty → empty list; no exception raised.

    Exercises the parameterized WHERE-name query path (SELECT name FROM sc_templates WHERE name = ?)
    when the row simply does not exist, confirming the function returns [] rather than raising.
    """
    from tools.dashboard.api.chat import _seed_canvas_artifacts

    # Build an sc_canvas.db whose sc_templates table has NO rows.
    canvas_db_path = str(tmp_path / "sc_canvas.db")
    empty_conn = sqlite3.connect(canvas_db_path)
    empty_conn.execute("CREATE TABLE IF NOT EXISTS sc_templates (name TEXT PRIMARY KEY)")
    empty_conn.commit()
    empty_conn.close()

    main_db_path = str(tmp_path / "icdev.db")
    main_conn = sqlite3.connect(main_db_path)
    main_conn.execute("""
        CREATE TABLE IF NOT EXISTS canvas_instances (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            tenant_id TEXT,
            canvas TEXT,
            artifact_type TEXT,
            artifact_name TEXT,
            created_at TEXT
        )
    """)
    main_conn.commit()
    main_conn.close()

    def fake_gc(db_path=None):
        path = canvas_db_path if (db_path and Path(db_path).name == "sc_canvas.db") else main_db_path
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        sc = StorageConnection(conn, "sqlite")
        sc.set_security_context(None)
        return sc

    # Request 'test_template' — the name that would normally exist but is absent here.
    use_case = {
        "canvas_seeds": [
            {"canvas": CANVAS_KEY, "templates": ["test_template"]}
        ]
    }

    with (
        patch("tools.dashboard.api.chat._load_canvas_catalog", return_value=CATALOG),
        patch("tools.db.storage.get_connection", side_effect=fake_gc),
    ):
        result = _seed_canvas_artifacts(use_case, session_id="sess-5", tenant_id="tenant-1")

    assert result == [], (
        "Expected empty list when 'test_template' is absent from sc_templates, "
        f"got {result!r}"
    )

    # Confirm no canvas_instances row was written.
    verify_conn = sqlite3.connect(main_db_path)
    row = verify_conn.execute(
        "SELECT * FROM canvas_instances WHERE artifact_name = ?", ("test_template",)
    ).fetchone()
    verify_conn.close()
    assert row is None, "canvas_instances must not contain a row for a template that was never found"


def test_seed_canvas_artifacts_unknown_canvas_key_returns_empty(tmp_path):
    """Unknown canvas key → warning logged, empty list returned."""
    from tools.dashboard.api.chat import _seed_canvas_artifacts

    use_case = {
        "canvas_seeds": [
            {"canvas": "unknown_canvas", "templates": ["test_template"]}
        ]
    }

    with patch("tools.dashboard.api.chat._load_canvas_catalog", return_value=CATALOG):
        result = _seed_canvas_artifacts(use_case, session_id="sess-4", tenant_id="tenant-1")

    assert result == []
