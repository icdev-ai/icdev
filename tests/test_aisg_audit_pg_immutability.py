# CUI // SP-CTI
"""aisg_audit is append-only on PostgreSQL too — the SQL that makes it so.

``aisg_audit`` was immutable on SQLite only: its two ``RAISE(ABORT)`` triggers
are SQLite dialect, PR #2376 rightly stopped sending them to PostgreSQL, and
nothing took their place there. These tests pin the PostgreSQL replacement at
the two seams that install it:

* ``tools/aisg/db/init_db.py::init_db`` — a fresh PG install;
* migration ``20261008003850_aisg_audit_pg_immutability`` — an existing PG
  database whose ``aisg_audit`` predates the triggers.

They drive both against a recording fake psycopg connection wrapped in the real
``StorageConnection``, so what is asserted is exactly what would reach the
server. The behavioural half — that PostgreSQL actually REFUSES the UPDATE and
DELETE and still accepts the INSERT — needs a live server and lives in
``tests/pg_tier/test_aisg_audit_immutable_pg.py`` (Test (PostgreSQL) job).
"""
from __future__ import annotations

import importlib
import importlib.util
import re
import sqlite3
from pathlib import Path

import pytest

from tools.db.storage import StorageConnection

_REPO = Path(__file__).resolve().parents[1]
_MIGRATION_DIR = _REPO / "tools" / "db" / "migrations" / "20261008003850_aisg_audit_pg_immutability"


def _load(name: str):
    path = _MIGRATION_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_aisg_mig_{name}", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Cursor:
    def __init__(self, raw):
        self._raw = raw
        self._last = ""

    def execute(self, sql, params=None):
        self._last = sql.strip()
        self._raw.executed.append(self._last)

    def fetchone(self):
        if "to_regclass" in self._last:
            return {"reg": "aisg_audit" if self._raw.table_present else None}
        return None

    def fetchall(self):
        return []


class _FakePg:
    """Records every statement; behaves like an always-succeeding server."""

    def __init__(self, table_present: bool = True):
        self.executed: list[str] = []
        self.table_present = table_present
        self.commits = 0

    def cursor(self, *a, **k):
        return _Cursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        pass


def _ddl(executed):
    """Drop executescript's SAVEPOINT bookkeeping and the to_regclass probe."""
    return [
        s for s in executed
        if not re.match(r"^(SAVEPOINT|RELEASE|ROLLBACK)\b", s, re.I) and "to_regclass" not in s
    ]


def _norm(sql: str) -> str:
    return " ".join(sql.split())


def _assert_append_only_ddl(statements):
    norm = [_norm(s) for s in statements]
    fn = [s for s in norm if s.startswith("CREATE OR REPLACE FUNCTION aisg_audit_immutable()")]
    assert len(fn) == 1, f"trigger function not created as ONE statement: {norm}"
    assert "RETURNS TRIGGER" in fn[0] and "RAISE EXCEPTION" in fn[0] and "LANGUAGE plpgsql" in fn[0]
    for trig, event in (("aisg_audit_no_update", "UPDATE"), ("aisg_audit_no_delete", "DELETE")):
        create = (
            f"CREATE TRIGGER {trig} BEFORE {event} ON aisg_audit "
            f"FOR EACH ROW EXECUTE FUNCTION aisg_audit_immutable()"
        )
        drop = f"DROP TRIGGER IF EXISTS {trig} ON aisg_audit"
        assert create in norm, f"missing: {create}"
        # Idempotent: the DROP must precede the CREATE, or a re-run fails on
        # "trigger already exists".
        assert drop in norm and norm.index(drop) < norm.index(create), f"{trig} is not re-runnable"
    assert norm.index(fn[0]) < norm.index(
        "CREATE TRIGGER aisg_audit_no_update BEFORE UPDATE ON aisg_audit "
        "FOR EACH ROW EXECUTE FUNCTION aisg_audit_immutable()"
    ), "function must exist before a trigger references it"


# --------------------------------------------------------------------------- init_db


def test_fresh_postgresql_init_installs_the_immutability_triggers(monkeypatch):
    mod = importlib.import_module("tools.aisg.db.init_db")
    raw = _FakePg()
    conn = StorageConnection(raw, "postgresql")
    monkeypatch.setattr(mod, "_AISG_BACKEND", "postgresql")
    monkeypatch.setattr(mod, "get_connection", lambda *a, **k: conn)

    mod.init_db()

    ddl = _ddl(raw.executed)
    _assert_append_only_ddl(ddl)
    # Installed AFTER the table exists, not before.
    creates_audit = [i for i, s in enumerate(ddl) if re.match(r"CREATE TABLE IF NOT EXISTS aisg_audit\b", s)]
    first_trigger = next(i for i, s in enumerate(ddl) if s.startswith("CREATE TRIGGER"))
    assert creates_audit and creates_audit[0] < first_trigger


def test_sqlite_init_never_receives_the_postgresql_ddl(monkeypatch, tmp_path):
    mod = importlib.import_module("tools.aisg.db.init_db")
    db = tmp_path / "aisg.db"
    conn = StorageConnection(sqlite3.connect(str(db)), "sqlite")
    monkeypatch.setattr(mod, "get_connection", lambda *a, **k: conn)
    mod.init_db()
    with sqlite3.connect(str(db)) as c:
        c.execute("INSERT INTO aisg_audit (action) VALUES ('probe')")
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            c.execute("UPDATE aisg_audit SET action = 'x'")
        with pytest.raises(sqlite3.DatabaseError, match="cannot be deleted"):
            c.execute("DELETE FROM aisg_audit")


# --------------------------------------------------------------------------- migration


def test_migration_installs_the_triggers_when_aisg_audit_exists():
    up = _load("up")
    raw = _FakePg(table_present=True)
    result = up.up(StorageConnection(raw, "postgresql"))

    assert result["status"] == "applied"
    _assert_append_only_ddl(_ddl(raw.executed))
    assert raw.commits >= 1


def test_migration_is_a_noop_on_postgresql_without_aisg_audit():
    up = _load("up")
    raw = _FakePg(table_present=False)
    result = up.up(StorageConnection(raw, "postgresql"))

    assert result["status"] == "skipped"
    assert _ddl(raw.executed) == [], "DDL against a table that does not exist would fail the run"


def test_migration_is_a_noop_on_sqlite(tmp_path):
    up = _load("up")
    db = tmp_path / "icdev.db"
    conn = StorageConnection(sqlite3.connect(str(db)), "sqlite")
    assert up.up(conn)["status"] == "skipped"
    with sqlite3.connect(str(db)) as c:
        assert c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'").fetchone()[0] == 0


def test_migration_down_removes_both_triggers_and_the_function():
    down = _load("down")
    raw = _FakePg(table_present=True)
    down.down(StorageConnection(raw, "postgresql"))
    norm = [_norm(s) for s in _ddl(raw.executed)]
    assert "DROP TRIGGER IF EXISTS aisg_audit_no_update ON aisg_audit" in norm
    assert "DROP TRIGGER IF EXISTS aisg_audit_no_delete ON aisg_audit" in norm
    assert "DROP FUNCTION IF EXISTS aisg_audit_immutable()" in norm


def test_migration_down_without_the_table_only_drops_the_function():
    down = _load("down")
    raw = _FakePg(table_present=False)
    down.down(StorageConnection(raw, "postgresql"))
    assert [_norm(s) for s in _ddl(raw.executed)] == ["DROP FUNCTION IF EXISTS aisg_audit_immutable()"]
