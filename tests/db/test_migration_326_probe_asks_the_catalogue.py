# CUI // SP-CTI
"""Migration 326's existence probe must not abort its own transaction (mfx-ci-05).

`326_studio_rls_column_backfill/up.py::_table_exists` probed with

    SELECT 1 FROM <table> LIMIT 1     # exception read as "absent"

so FAILURE WAS THE EXPECTED PATH -- and on PostgreSQL a failed statement ABORTS
THE TRANSACTION. Its docstring says it exists to "skip tables absent from this
database rather than aborting the run"; on PostgreSQL the skip WAS the abort.

WHY THE LOOP MAKES IT WORSE THAN A SINGLE PROBE, and why that is what this file
asserts. `up()` walks ten `studio_*` tables and probes each one. The FIRST
absent table poisons the transaction, so every later probe fails for that
reason and reads as ABSENT, every later `_add_column` fails too, and the
migration reports success having backfilled NOTHING. The deployment it bites is
the one the skip was written for: `studio_*` tables are created by
`tools/studio/init_db.py`, not by the migration chain, so a database where
studio has never been initialised legitimately lacks them -- the fresh
PostgreSQL path CI exercises. The identical defect in
`20260912122759_add_experiment_candidate_lane` took `Test (PostgreSQL)` and all
four E2E shards down (run 34696022955, 2026-09-12).

MEASURED, NO LIVE DAMAGE. On the canonical board 2026-09-12 all ten listed
tables exist and all ten carry both `classification` and `tenant_id` (17
`studio_*` tables present, none lacking either column; catalogue read, no
writes). The backfill completed here -- consistent with every table existing
when it ran, so the probe never had to fail. That is a finding, not an absence
of one: nothing on the live board needs repair, and the defect is real for
every database built after it.

STRUCTURAL RATHER THAN LIVE-POSTGRESQL, DELIBERATELY. The assertion below fails
on PostgreSQL and passes on SQLite, so a run under the SQLite-forced test
conftest could never see it. It is therefore driven through `_PgLikeConn`, a proxy
sitting IN FRONT of a real `StorageConnection(raw, "sqlite")` -- so production
`translate_sql` still runs -- that enforces THE ONE PostgreSQL behaviour this
defect turns on: a failed statement poisons every later statement until the
transaction ends. It answers `information_schema.tables` from `sqlite_master`
and refuses to fake any other catalogue read. The same class runs with the abort rule OFF as the `sqlite`
arm, because SQLite genuinely does not abort; that arm passes against the old
code too, which is exactly the asymmetry that hid this for a month. A skip would
have been the wrong answer here: a gated test that skips is an unmeasured test.
"""
from __future__ import annotations

import ast
import importlib.util
import inspect
import re
import sqlite3
import sys
import textwrap
from pathlib import Path

import pytest

from tools.db.storage import StorageConnection

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MIGRATION = (
    ROOT / "tools" / "db" / "migrations"
    / "326_studio_rls_column_backfill" / "up.py"
)

RLS_COLUMNS = ("classification", "tenant_id")

#: A table late in `_TABLES` — it must still be backfilled after an earlier
#: table was found absent. Pinned by name so the test states its own fixture.
LATER_TABLE = "studio_forms"

_ALTER_IF_NOT_EXISTS = re.compile(
    r"^\s*ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+(\w+)\s+(.+)$",
    re.I | re.S,
)
_CATALOGUE_TABLES = re.compile(r"FROM\s+information_schema\.tables\b", re.I)


def _load_migration():
    """Load 326 by path — `326_...` is not an importable module name."""
    spec = importlib.util.spec_from_file_location("m326_under_test", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _PgLikeConn:
    """A proxy IN FRONT of a real StorageConnection, adding one PostgreSQL rule.

    It wraps `StorageConnection(raw, "sqlite")` rather than a bare
    `sqlite3.connect`, so what reaches the migration still runs every statement
    through the production `translate_sql` — the SQL-recorder shape
    `coherence_checker.check_test_db_isolation` sanctions, and the reason a
    `%s`/`?` difference cannot hide in here.

    `aborts_on_error=True` models PostgreSQL: after a statement raises, every
    later statement in the same transaction raises "current transaction is
    aborted" until `commit()`/`rollback()` ends it. With it False the same
    class is plain SQLite, which does not abort — so the two arms differ only
    in the behaviour under test.

    `information_schema.tables` is answered from `sqlite_master`; any OTHER
    `information_schema` read raises rather than being quietly faked, so the
    emulation cannot silently widen. `ADD COLUMN IF NOT EXISTS` (PostgreSQL
    only) is honoured by asking the local catalogue first, which is what
    PostgreSQL does for it.
    """

    def __init__(self, inner, *, backend: str, aborts_on_error: bool):
        self._inner = inner
        self._cursor = None
        self._backend = backend
        self._aborts_on_error = aborts_on_error
        self.aborted = False
        self.rollbacks = 0
        self.statements: list[str] = []

    # -- the bits migration 326 actually calls ----------------------------
    def execute(self, sql: str, params=None):
        self.statements.append(sql)
        if self.aborted:
            raise RuntimeError(
                "current transaction is aborted, commands ignored until end "
                "of transaction block"
            )
        alter = _ALTER_IF_NOT_EXISTS.match(sql)
        if alter:
            table, column, definition = alter.groups()
            if self._has_column(table, column):
                sql, params = "SELECT 1 WHERE 0", None
            else:
                sql = f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
        elif _CATALOGUE_TABLES.search(sql):
            sql = "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?"
        elif "information_schema" in sql.lower():
            raise NotImplementedError(
                f"_PgLikeConn does not emulate this catalogue read: {sql!r}"
            )
        try:
            self._cursor = self._inner.execute(sql, params)
        except Exception:
            if self._aborts_on_error:
                self.aborted = True
            raise
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def commit(self):
        self._inner.commit()
        self.aborted = False

    def rollback(self):
        self.rollbacks += 1
        self._inner.rollback()
        self.aborted = False

    def close(self):
        self._inner.close()

    # -- internal bookkeeping, not part of the emulated surface -----------
    def _has_column(self, table: str, column: str) -> bool:
        rows = self._inner.execute(f"PRAGMA table_info({table})").fetchall()
        names = [
            r[1] if isinstance(r, (list, tuple)) else dict(r).get("name")
            for r in rows
        ]
        return column in names


def _seed(path: Path) -> None:
    """A database holding a LATER `_TABLES` entry and missing the first one."""
    conn = sqlite3.connect(str(path))
    conn.execute(f"CREATE TABLE {LATER_TABLE} (id TEXT PRIMARY KEY, name TEXT)")
    conn.execute(f"INSERT INTO {LATER_TABLE} (id, name) VALUES ('f1', 'intake')")
    conn.commit()
    conn.close()


def _columns(path: Path, table: str) -> set:
    conn = sqlite3.connect(str(path))
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def test_the_fixture_is_the_shape_the_defect_needs():
    """Guard the guard: an absent first table and a present later one."""
    module = _load_migration()
    assert module._TABLES[0] != LATER_TABLE
    assert module._TABLES.index(LATER_TABLE) > 0, (
        f"{LATER_TABLE} must come AFTER the table this test leaves absent, "
        "or the loop never reaches it"
    )


@pytest.mark.parametrize(
    "backend,aborts_on_error",
    [("postgresql", True), ("sqlite", False)],
)
def test_a_later_table_is_backfilled_after_an_earlier_one_is_absent(
    tmp_path, monkeypatch, backend, aborts_on_error
):
    """The loop must not no-op from the first absent table onwards.

    RED before mfx-ci-05 on the `postgresql` arm: the probe of the absent
    `_TABLES[0]` raised, poisoning the transaction, so every later probe raised
    too, every table read as absent, and `studio_forms` got no columns while
    `up()` returned normally.
    """
    db = tmp_path / "m326.db"
    _seed(db)
    module = _load_migration()
    first = module._TABLES[0]
    assert not _columns(db, first), f"{first} must be absent from the fixture"

    real = StorageConnection(sqlite3.connect(str(db)), "sqlite")
    conn = _PgLikeConn(real, backend=backend, aborts_on_error=aborts_on_error)
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", backend)
    monkeypatch.setattr(module, "get_connection", lambda *a, **k: conn)

    module.up()

    present = _columns(db, LATER_TABLE)
    for column in RLS_COLUMNS:
        assert column in present, (
            f"{LATER_TABLE} lacks {column!r} after up() on {backend}: the "
            f"probe of the absent {first} stopped the loop. "
            f"Columns seen: {sorted(present)}"
        )

    assert conn.aborted is False, "up() left the transaction poisoned"
    assert conn.rollbacks == 0, (
        "up() ran on a connection get_connection() owns — a rollback there "
        "discards the caller's uncommitted work"
    )
    offenders = [
        s for s in conn.statements
        for t in module._TABLES
        if re.search(rf"\bFROM\s+{t}\b", s, re.I)
    ]
    assert not offenders, (
        f"the probe queried the relation under test: {offenders}"
    )


@pytest.mark.parametrize(
    "backend,aborts_on_error",
    [("postgresql", True), ("sqlite", False)],
)
def test_a_database_with_no_studio_tables_is_a_clean_no_op(
    tmp_path, monkeypatch, backend, aborts_on_error
):
    """The motivating deployment: studio never initialised, nothing to do."""
    db = tmp_path / "empty.db"
    sqlite3.connect(str(db)).close()

    module = _load_migration()
    real = StorageConnection(sqlite3.connect(str(db)), "sqlite")
    conn = _PgLikeConn(real, backend=backend, aborts_on_error=aborts_on_error)
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", backend)
    monkeypatch.setattr(module, "get_connection", lambda *a, **k: conn)

    module.up()

    assert conn.aborted is False
    assert not any(
        re.search(r"\bALTER\s+TABLE\b", s, re.I) for s in conn.statements
    ), "nothing may be altered in a database holding none of these tables"


def test_the_probe_reads_a_catalogue_and_not_the_relation():
    """Stated in source, because the shape is the defect.

    A catalogue read returns a row or no row and raises in neither case. A
    `SELECT` against the relation under test raises by design, which is what
    made the skip an abort.
    """
    source = inspect.getsource(_load_migration()._table_exists)
    assert "information_schema.tables" in source
    assert "sqlite_master" in source

    # The docstring NAMES the rejected fixes, so read the CODE only.
    tree = ast.parse(textwrap.dedent(source)).body[0]
    body = tree.body[1:] if ast.get_docstring(tree) else tree.body
    code = "\n".join(ast.unparse(node) for node in body)
    assert "rollback" not in code, (
        "a rollback on a get_connection()-owned connection discards the "
        "caller's uncommitted work — ask the catalogue instead"
    )
    assert not re.search(r"FROM\s*\{table\}", code), (
        "_table_exists still queries the relation under test"
    )


#: The ONE other site the mfx-ci-05 census found once catalogue reads, raw
#: sqlite3 source-file reads and runner-undiscoverable files were excluded BY
#: PREDICATE. `down()`'s `_row_count` read `SELECT COUNT(*) FROM <table>` and
#: returned 0 on any exception ("missing table counts as empty"), so on
#: PostgreSQL an absent table poisoned the rollback it was protecting.
MIGRATION_327 = (
    ROOT / "tools" / "db" / "migrations"
    / "327_nc_simulation_results_rename" / "down.py"
)


def test_327_row_count_asks_the_catalogue_too():
    """Same defect, same fix: absence is a catalogue question."""
    spec = importlib.util.spec_from_file_location("m327_under_test", MIGRATION_327)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    source = textwrap.dedent(inspect.getsource(module._row_count))
    tree = ast.parse(source).body[0]
    body = tree.body[1:] if ast.get_docstring(tree) else tree.body
    code = "\n".join(ast.unparse(node) for node in body)

    assert "table_exists" in code, (
        "_row_count must ask the catalogue for absence, not read a failed "
        "COUNT as zero — on PostgreSQL that aborts the rollback's transaction"
    )
    assert not any(isinstance(n, ast.Try) for n in ast.walk(tree)), (
        "a try/except around the COUNT is the defect: it cannot tell an empty "
        "table from a dead transaction"
    )
