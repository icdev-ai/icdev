"""A CREATE TABLE preceded by a ``--`` comment must actually run on PostgreSQL.

The PG branch of four canvas ``init_db()`` functions used to do::

    for stmt in SCHEMA.split(";"):
        stmt = stmt.strip()
        if stmt and not stmt.startswith("--"):
            try: conn.execute(stmt)
            except Exception: pass

Splitting on ``;`` leaves every comment that sits ABOVE a statement at the
front of that statement's chunk, so the ``startswith("--")`` test threw away
the comment AND the CREATE TABLE beneath it. Measured 2026-10-07: 100 of the
network canvas's 144 CREATE TABLE statements, and ``zig_pillars`` in the
security canvas, were skipped on every start; ``nc_conflict_resolutions``
existed by no other path and was absent on the live database.

Two more defects rode along. A ``;`` INSIDE a comment ("-- Drag onto canvas;
all STIG ...") split the comment, so the next chunk began with prose and the
CREATE TABLE after it was a syntax error. And the bare ``except: pass`` never
rolled back, so on PostgreSQL that first error aborted the transaction and
every later statement failed too.

The fix routes the PG branch through ``StorageConnection.executescript``,
which strips line comments BEFORE splitting and isolates each statement in a
SAVEPOINT. These tests drive the real ``init_db()`` against a fake psycopg
connection that behaves like PostgreSQL in the one way that matters here: a
failed statement poisons the transaction until ``ROLLBACK TO SAVEPOINT``.
The fake's ``commit()`` ends the test, so nothing past the schema phase runs.
"""

from __future__ import annotations

import importlib
import re

import pytest

from tools.db.storage import StorageConnection

_CREATE_TABLE = re.compile(r"^CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)", re.I)
_ALL_CREATE_TABLES = re.compile(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)", re.I)
_SQL_START = re.compile(
    r"^(CREATE|SAVEPOINT|RELEASE|ROLLBACK|SELECT|INSERT|UPDATE|DELETE|ALTER|DROP|WITH|SET|END)\b",
    re.I,
)

# (module, attribute holding the backend switch)
CANVASES = [
    ("tools.network.db.init_db", "_NC_BACKEND"),
    ("tools.security_canvas.db.init_db", "_SC_BACKEND"),
    ("tools.boundary_canvas.db.init_db", "_BDC_BACKEND"),
    ("tools.aisg.db.init_db", "_AISG_BACKEND"),
]


class _SchemaPhaseDone(Exception):
    """Raised by the fake commit: the schema phase is over, stop here."""


class _FakePgCursor:
    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql, params=None):
        text = sql.strip()
        if self._raw.aborted and not text.upper().startswith("ROLLBACK"):
            raise RuntimeError("current transaction is aborted")
        if not _SQL_START.match(text):
            # Prose that leaked into a statement is a syntax error on PG,
            # and an error aborts the transaction.
            self._raw.aborted = True
            raise RuntimeError(f"syntax error at or near {text.split()[0]!r}")
        if text.upper().startswith("ROLLBACK TO SAVEPOINT"):
            self._raw.aborted = False
        self._raw.executed.append(text)

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class _FakePgConn:
    def __init__(self):
        self.executed: list[str] = []
        self.aborted = False

    def cursor(self, *a, **k):
        return _FakePgCursor(self)

    def commit(self):
        raise _SchemaPhaseDone()

    def rollback(self):
        self.aborted = False

    def close(self):
        pass


def _run_schema_phase(monkeypatch, module_name, backend_attr):
    mod = importlib.import_module(module_name)
    raw = _FakePgConn()
    conn = StorageConnection(raw, "postgresql")
    monkeypatch.setattr(mod, backend_attr, "postgresql")
    monkeypatch.setattr(mod, "get_connection", lambda *a, **k: conn)
    with pytest.raises(_SchemaPhaseDone):
        mod.init_db()
    return mod, raw.executed


@pytest.mark.parametrize("module_name,backend_attr", CANVASES)
def test_every_create_table_runs_even_when_a_comment_precedes_it(monkeypatch, module_name, backend_attr):
    mod, executed = _run_schema_phase(monkeypatch, module_name, backend_attr)
    declared = set(_ALL_CREATE_TABLES.findall(mod.SCHEMA))
    created = {m.group(1) for s in executed if (m := _CREATE_TABLE.match(s))}
    assert declared, f"{module_name}.SCHEMA declares no tables"
    missing = sorted(declared - created)
    assert not missing, f"{len(missing)} CREATE TABLE statements never ran on PG: {missing[:10]}"


def test_comment_led_network_tables_named_in_the_incident_are_created(monkeypatch):
    # nc_conflict_resolutions: preceded by a comment, existed by no other path.
    # nc_collab_sessions: follows a comment that itself contains a ';'.
    _, executed = _run_schema_phase(monkeypatch, "tools.network.db.init_db", "_NC_BACKEND")
    created = {m.group(1) for s in executed if (m := _CREATE_TABLE.match(s))}
    assert {"nc_conflict_resolutions", "nc_collab_sessions"} <= created


@pytest.mark.parametrize("module_name,backend_attr", CANVASES)
def test_comment_only_chunks_are_still_skipped(monkeypatch, module_name, backend_attr):
    _, executed = _run_schema_phase(monkeypatch, module_name, backend_attr)
    for stmt in executed:
        body = "\n".join(ln for ln in stmt.splitlines() if not ln.strip().startswith("--")).strip()
        assert body, f"a comment-only chunk was sent to the database: {stmt[:80]!r}"
        assert not stmt.startswith("--"), f"statement still carries its leading comment: {stmt[:80]!r}"


def test_aisg_sqlite_triggers_are_not_sent_to_postgresql(monkeypatch):
    # SQLite-dialect triggers are a syntax error on PG, and the ';' split
    # turned each trailing `END` into a bare COMMIT mid-script.
    _, executed = _run_schema_phase(monkeypatch, "tools.aisg.db.init_db", "_AISG_BACKEND")
    assert not [s for s in executed if s.upper().startswith(("CREATE TRIGGER", "END"))]


def test_aisg_sqlite_still_gets_its_immutability_triggers(monkeypatch, tmp_path):
    import sqlite3

    mod = importlib.import_module("tools.aisg.db.init_db")
    db = tmp_path / "aisg.db"
    wrapped = StorageConnection(sqlite3.connect(str(db)), "sqlite")
    monkeypatch.setattr(mod, "get_connection", lambda: wrapped)
    mod.init_db()
    with sqlite3.connect(str(db)) as c:
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    assert {"aisg_audit_no_update", "aisg_audit_no_delete"} <= names
