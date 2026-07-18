# CUI // SP-CTI
"""Tests for NDC backend-aware existence helpers and canvas-routed persistence.

Covers ndc-sql-03:
  (a) table_exists / col_exists true/false against a temp SQLite DB opened via
      the canvas init_db get_connection().
  (b) Process-level cache — a repeated probe does not re-query the connection.
  (c) config_parser.ingest_config persists via the canvas get_connection()
      (monkeypatched to a temp SQLite DB); the row reads back.
  (d) PostgreSQL branch — a mock connection reporting PG drives the
      information_schema SQL path (no live PG required).
"""

import os
import sqlite3

import pytest

from tools.network import blueprint_helpers as bh
from tools.network.blueprint_helpers import col_exists, table_exists


@pytest.fixture(autouse=True)
def _clear_caches():
    """Existence caches are process-global; isolate each test."""
    bh._TABLE_EXISTS_CACHE.clear()
    bh._COL_EXISTS_CACHE.clear()
    yield
    bh._TABLE_EXISTS_CACHE.clear()
    bh._COL_EXISTS_CACHE.clear()


def _canvas_sqlite_conn(tmp_path, monkeypatch):
    """A canvas-style SQLite connection at a temp path.

    Points init_db's module globals at a temp DB and forces the SQLite branch,
    then returns the StorageConnection the canvas get_connection() yields.
    """
    from tools.network.db import init_db

    db_file = tmp_path / "nc_helpers_test.db"
    monkeypatch.setattr(init_db, "_NC_BACKEND", "sqlite")
    monkeypatch.setattr(init_db, "DB_PATH", db_file)
    return init_db.get_connection()


# ── (a) true / false correctness ───────────────────────────────────────────────

def test_table_exists_true_and_false(tmp_path, monkeypatch):
    conn = _canvas_sqlite_conn(tmp_path, monkeypatch)
    conn.execute("CREATE TABLE nc_widgets (id TEXT PRIMARY KEY, label TEXT)")
    conn.commit()

    assert table_exists(conn, "nc_widgets") is True
    assert table_exists(conn, "nc_missing") is False


def test_col_exists_true_and_false(tmp_path, monkeypatch):
    conn = _canvas_sqlite_conn(tmp_path, monkeypatch)
    conn.execute("CREATE TABLE nc_gadgets (id TEXT PRIMARY KEY, label TEXT)")
    conn.commit()

    assert col_exists(conn, "nc_gadgets", "label") is True
    assert col_exists(conn, "nc_gadgets", "nonexistent") is False


# ── (b) cache behavior ─────────────────────────────────────────────────────────

def test_table_exists_uses_cache(tmp_path, monkeypatch):
    conn = _canvas_sqlite_conn(tmp_path, monkeypatch)
    conn.execute("CREATE TABLE nc_cached (id TEXT PRIMARY KEY)")
    conn.commit()

    # First call populates the cache (one real query).
    assert table_exists(conn, "nc_cached") is True

    calls = {"n": 0}
    orig_execute = conn.execute

    def counting_execute(*args, **kwargs):
        calls["n"] += 1
        return orig_execute(*args, **kwargs)

    monkeypatch.setattr(conn, "execute", counting_execute)

    # Second call must be served from the cache — no re-query.
    assert table_exists(conn, "nc_cached") is True
    assert calls["n"] == 0


def test_table_exists_does_not_cache_negatives(tmp_path, monkeypatch):
    """A table absent on the first probe but created later must be seen."""
    conn = _canvas_sqlite_conn(tmp_path, monkeypatch)

    # First probe: table does not exist yet.
    assert table_exists(conn, "nc_late") is False

    # It gets created in-process (migration / ensure-path).
    conn.execute("CREATE TABLE nc_late (id TEXT PRIMARY KEY)")
    conn.commit()

    # Second probe must re-query and see it — the False was not cached.
    assert table_exists(conn, "nc_late") is True


def test_col_exists_does_not_cache_negatives(tmp_path, monkeypatch):
    """A column absent on the first probe but added later must be seen."""
    conn = _canvas_sqlite_conn(tmp_path, monkeypatch)
    conn.execute("CREATE TABLE nc_grow (id TEXT PRIMARY KEY)")
    conn.commit()

    assert col_exists(conn, "nc_grow", "extra") is False

    conn.execute("ALTER TABLE nc_grow ADD COLUMN extra TEXT")
    conn.commit()

    assert col_exists(conn, "nc_grow", "extra") is True


def test_col_exists_uses_cache(tmp_path, monkeypatch):
    conn = _canvas_sqlite_conn(tmp_path, monkeypatch)
    conn.execute("CREATE TABLE nc_ccache (id TEXT PRIMARY KEY, label TEXT)")
    conn.commit()

    assert col_exists(conn, "nc_ccache", "label") is True

    calls = {"n": 0}
    orig_execute = conn.execute

    def counting_execute(*args, **kwargs):
        calls["n"] += 1
        return orig_execute(*args, **kwargs)

    monkeypatch.setattr(conn, "execute", counting_execute)

    assert col_exists(conn, "nc_ccache", "label") is True
    assert calls["n"] == 0


# ── (c) config_parser persists via the canvas helper ───────────────────────────

_IOS_CONFIG = """hostname R1
!
interface GigabitEthernet0/0
 ip address 10.0.0.1 255.255.255.0
 description uplink
!
ip route 0.0.0.0 0.0.0.0 10.0.0.254
"""


def test_ingest_config_persists_via_canvas_helper(tmp_path, monkeypatch):
    from tools.db.storage import StorageConnection
    from tools.network import config_parser

    db_file = tmp_path / "nc_configs.db"

    def _make_conn():
        raw = sqlite3.connect(str(db_file))
        raw.row_factory = sqlite3.Row
        return StorageConnection(raw, "sqlite")

    # Seed the target table.
    seed = _make_conn()
    seed.execute(
        "CREATE TABLE ni_device_configs ("
        "id TEXT PRIMARY KEY, device_id TEXT, config_type TEXT, "
        "config_text TEXT, config_hash TEXT, source TEXT, version INTEGER)"
    )
    seed.commit()
    seed.close()

    # Route the canvas helper (config_parser's default persistence path) to
    # our temp SQLite DB. config_parser binds get_connection at import, so we
    # patch the name on the config_parser module (shim-aware monkeypatch).
    monkeypatch.setattr(config_parser, "get_connection", _make_conn)

    result = config_parser.ingest_config(_IOS_CONFIG, device_ip="10.0.0.1")
    assert result["vendor"] == "cisco_ios"
    assert result["hostname"] == "R1"

    check = _make_conn()
    row = check.execute(
        "SELECT id, device_id, config_hash FROM ni_device_configs WHERE id=%s",
        (result["config_id"],),
    ).fetchone()
    check.close()

    assert row is not None
    assert row["config_hash"] == result["config_hash"]


# ── (d) PostgreSQL branch drives information_schema ────────────────────────────

class _FakePGResult:
    def __init__(self, one, many):
        self._one = one
        self._many = many

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._many


class _FakePGConn:
    """A connection object that reports the PostgreSQL backend."""

    _backend = "postgresql"

    def __init__(self, table_row=("nc_x",), col_rows=None):
        self.sqls = []
        self._table_row = table_row
        self._col_rows = col_rows if col_rows is not None else [("id",), ("label",)]

    def execute(self, sql, params=None):
        self.sqls.append(sql)
        if "information_schema.columns" in sql:
            match = params and params[-1] in {r[0] for r in self._col_rows}
            return _FakePGResult(("label",) if match else None, self._col_rows)
        return _FakePGResult(self._table_row, [])


def test_pg_branch_table_exists_uses_information_schema():
    conn = _FakePGConn(table_row=("nc_x",))
    assert table_exists(conn, "nc_x") is True
    assert any("information_schema.tables" in s for s in conn.sqls)
    assert not any("sqlite_master" in s for s in conn.sqls)


def test_pg_branch_col_exists_uses_information_schema():
    conn = _FakePGConn(col_rows=[("id",), ("label",)])
    assert col_exists(conn, "nc_x", "label") is True
    assert col_exists(conn, "nc_x", "missing") is False
    assert any("information_schema.columns" in s for s in conn.sqls)
    assert not any("PRAGMA" in s for s in conn.sqls)


@pytest.mark.skipif(
    os.environ.get("ICDEV_STORAGE_BACKEND", "sqlite").lower() != "postgresql",
    reason="live PostgreSQL not configured",
)
def test_pg_live_table_exists_smoke():
    from tools.network.db.init_db import get_connection

    conn = get_connection()
    # information_schema.tables always exists on PG; just exercise the path.
    assert table_exists(conn, "information_schema_probe_missing_table") in (True, False)
