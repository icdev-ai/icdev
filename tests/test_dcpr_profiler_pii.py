# CUI // SP-CTI
"""dcpr-fix-03 — data_profiler placeholder + profiler/pii table-name key + pii canvas conn.

Regression tests for four verified defects:

1. data_profiler._fetch_top_values used ``LIMIT %s`` with a bound param on the
   NON-PostgreSQL branch. sqlite3/duckdb use the qmark paramstyle ("?"), so the
   query raised, was swallowed by _profile_column's try/except, and top_values
   came back silently empty on SQLite/DuckDB.
2. data_profiler.profile_table returns the table under key "name" (not
   "table_name"); the CLI read "table_name" and printed "?".
3. pii_scanner.scan_profile stamped result["table"] from "table_name" — same
   key bug — so every finding's table label was "?".
4. pii_scanner.save_pii_scan used get_connection() instead of
   get_canvas_connection(); dd_pii_scans is a canvas table without
   classification/tenant_id columns, so the RLS predicate would raise.

Seeding uses the storage layer (get_connection), never raw sqlite3.connect.
"""
from __future__ import annotations

import importlib

import pytest

from tools.data_canvas import data_profiler, pii_scanner


@pytest.fixture
def seeded_sqlite_db(tmp_path):
    """Create an external SQLite data source via the storage layer and return its path.

    conftest forces ICDEV_STORAGE_BACKEND=sqlite, so get_connection(db_path=...)
    yields a SQLite-backed StorageConnection. The data_profiler later opens the
    same file read-only through its own (pg-ok) connection helper.
    """
    db_path = tmp_path / "profiler_src.db"
    from tools.db.storage import get_connection

    conn = get_connection(db_path=str(db_path))
    conn.execute("CREATE TABLE people (id INTEGER, email TEXT, city TEXT)")
    for row in [
        (1, "alice@example.com", "NYC"),
        (2, "bob@example.com", "NYC"),
        (3, "carol@example.com", "LA"),
        (4, "dave@example.com", "NYC"),
    ]:
        conn.execute("INSERT INTO people (id, email, city) VALUES (?, ?, ?)", row)
    conn.commit()
    conn.close()
    return str(db_path)


def test_fetch_top_values_populated_on_sqlite(seeded_sqlite_db):
    """Defect 1: top_values must populate on the default SQLite backend."""
    conn_params = {"db_type": "sqlite", "path": seeded_sqlite_db}
    profile = data_profiler.profile_table(conn_params, "people")
    assert "error" not in profile, profile.get("error")

    cols = {c["name"]: c for c in profile["columns"]}
    city = cols["city"]
    # If the "LIMIT %s" placeholder bug regresses, the column carries an "error"
    # key and top_values is empty.
    assert "error" not in city, city.get("error")
    assert city["top_values"], "top_values empty — placeholder bug regressed"
    counts = {tv["value"]: tv["count"] for tv in city["top_values"]}
    assert counts.get("NYC") == 3


def test_profile_table_emits_name_key(seeded_sqlite_db):
    """Defect 2: profile_table returns 'name' (CLI label reads this key)."""
    conn_params = {"db_type": "sqlite", "path": seeded_sqlite_db}
    profile = data_profiler.profile_table(conn_params, "people")
    assert profile.get("name") == "people"
    assert "table_name" not in profile
    # CLI label path: `tbl.get('name', '?')` must resolve to the real name.
    assert profile.get("name", "?") != "?"


def test_pii_scan_table_label_uses_name(seeded_sqlite_db):
    """Defect 3: pii findings carry the real table name, not '?'."""
    conn_params = {"db_type": "sqlite", "path": seeded_sqlite_db}
    db_profile = data_profiler.profile_database(conn_params)
    scan = pii_scanner.scan_profile(db_profile)

    email_findings = [f for f in scan["findings"] if f["column"] == "email"]
    assert email_findings, "expected the email column to be flagged as PII"
    assert email_findings[0]["table"] == "people"
    assert all(f["table"] != "?" for f in scan["findings"])


def test_save_pii_scan_uses_canvas_connection(monkeypatch):
    """Defect 4: save_pii_scan must use get_canvas_connection(), not get_connection()."""
    calls = {"canvas": 0, "plain": 0, "closed": 0, "insert_sql": None}

    class _FakeConn:
        def execute(self, sql, params=None):
            calls["insert_sql"] = sql

        def commit(self):
            pass

        def close(self):
            calls["closed"] += 1

    # Patch on the exact module object that save_pii_scan imports from, so the
    # shim (tools.* -> icdev.tools.*) resolves to the same object at call time.
    storage = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(storage, "get_canvas_connection", lambda *a, **k: (calls.__setitem__("canvas", calls["canvas"] + 1) or _FakeConn()))
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: (calls.__setitem__("plain", calls["plain"] + 1) or _FakeConn()))

    run_id = pii_scanner.save_pii_scan({"overall_risk": "high"}, design_id="d1")

    assert run_id
    assert calls["canvas"] == 1, "save_pii_scan must call get_canvas_connection()"
    assert calls["plain"] == 0, "save_pii_scan must NOT call get_connection()"
    assert calls["closed"] == 1, "connection must be closed"
    assert "dd_pii_scans" in (calls["insert_sql"] or "")
