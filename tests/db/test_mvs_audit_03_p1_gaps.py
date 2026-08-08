# CUI // SP-CTI
"""The two shadowed migrations that no remediation covered.

mvs-audit-03-d5 classified all 60 grandfathered shadowed migrations: 11 are gaps
on PostgreSQL, six break live code, PR #1296 fixes four. These are the other two
— present in no remediation, on main or on any open branch.

Both lost a version collision, so `MigrationRunner` (which keeps the FIRST entry
per version) never ran them anywhere:

    207_tenant_component_overrides  lost 207 to 207_mcip_dat_tables.sql
    257_idr_dic_doc_link.sql        lost 257 to 257_doc_modernization.sql

The live database happens to have both objects — its `schema_migrations` shows
207 applied as `tenant_component_overrides` on 2026-06-22, i.e. it won the
collision the other way round. A FRESH PostgreSQL build does not: all three
objects are absent from `tools/db/schema/pg_consolidated.sql`. So this is a
fresh-install defect that the live box cannot reveal, which is exactly why it
survived two prior passes at this problem.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (REPO_ROOT / "tools" / "db" / "migrations"
             / "20260808010301_mvs_audit_03_p1_tenant_overrides_and_dic_source_links")


def _module_constant(name: str) -> str:
    """Value of a module-level string constant in up.py.

    Read from the parsed AST, not the file text: the module docstring and the
    comments deliberately QUOTE the SQLite-only forms this migration avoids, so
    a substring search over the source matches the prose explaining the bug and
    reports the fix as broken.
    """
    import ast
    tree = ast.parse((MIGRATION / "up.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in up.py")


def _executable_source() -> str:
    """up.py with comments and docstrings stripped — code only."""
    import ast
    src = (MIGRATION / "up.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    # Drop every docstring, then unparse: comments never survive a parse.
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                node.body.pop(0)
    return ast.unparse(tree)


def test_migration_ships_up_and_down():
    """A directory with neither up.sql nor up.py is skipped SILENTLY by
    discover_migrations — 17 such directories exist in this repo."""
    assert MIGRATION.is_dir(), f"missing migration dir: {MIGRATION}"
    assert (MIGRATION / "up.py").is_file()
    assert (MIGRATION / "down.py").is_file()
    assert (MIGRATION / "meta.json").is_file()


def _load(name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(f"_mig_{name}", MIGRATION / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _apply(_unused, conn: sqlite3.Connection) -> None:
    _load("up").up(conn)


def _revert(conn: sqlite3.Connection) -> None:
    _load("down").down(conn)


@pytest.fixture
def db(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    # The ALTERs attach to an existing dic_documents, as they do in production.
    conn.execute("CREATE TABLE dic_documents (doc_id TEXT PRIMARY KEY, source_id TEXT)")
    conn.commit()
    yield conn
    conn.close()


def test_up_creates_the_tenant_override_table(db):
    _apply(None, db)
    cols = [r[1] for r in db.execute("PRAGMA table_info(tenant_component_overrides)")]
    assert cols == ["id", "tenant_id", "component_key", "enabled",
                    "updated_by", "updated_at"], cols


def test_up_adds_the_dic_source_links(db):
    """Without these the docgen -> Tech Writer bridge returns HTTP 500.

    tools/document_intelligence/blueprint.py writes both columns twice — an
    UPDATE (reuse path) and an INSERT (generate path) — inside one try whose
    handler returns 500, so every path through the route fails.
    """
    _apply(None, db)
    cols = {r[1] for r in db.execute("PRAGMA table_info(dic_documents)")}
    assert "source_wg_result_id" in cols
    assert "source_idr_session_id" in cols


def test_up_is_idempotent(db):
    """Runs against fresh PostgreSQL, fresh SQLite (where init_icdev_db.py
    already creates the table) and long-lived databases alike."""
    _apply(None, db)
    _apply(None, db)  # must not raise
    n = db.execute("SELECT count(*) FROM sqlite_master WHERE name='tenant_component_overrides'"
                   ).fetchone()[0]
    assert n == 1


def test_indexes_are_created(db):
    _apply(None, db)
    idx = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND tbl_name='tenant_component_overrides'")}
    assert "idx_tenant_component_overrides_tenant" in idx
    assert "idx_tenant_component_overrides_key" in idx


def test_constraints_bind(db):
    _apply(None, db)
    db.execute("INSERT INTO tenant_component_overrides "
               "(id,tenant_id,component_key,enabled) VALUES ('a','t1','k1',0)")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO tenant_component_overrides "
                   "(id,tenant_id,component_key,enabled) VALUES ('b','t2','k2',7)")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO tenant_component_overrides "
                   "(id,tenant_id,component_key,enabled) VALUES ('c','t1','k1',1)")


def test_ddl_is_not_sqlite_only():
    """207 declared ``updated_at TEXT NOT NULL DEFAULT (datetime('now'))``.

    ``datetime('now')`` is a syntax error on PostgreSQL — the backend this
    migration exists to repair — so replicating 207 verbatim would have failed
    on the only population that still needs it.
    """
    ddl = _module_constant("_CREATE_TABLE")
    assert "datetime('now')" not in ddl, "SQLite-only default would break PostgreSQL"
    assert "CURRENT_TIMESTAMP" in ddl


def test_column_add_is_not_pg_only_syntax():
    """``ALTER TABLE ... ADD COLUMN IF NOT EXISTS`` is PostgreSQL-only.

    SQLite rejects it outright, and both backends need these columns: on SQLite
    ``dic_documents`` is created at startup by
    ``document_intelligence/ingest_orchestrator.py``, whose DDL carries
    ``source_id`` but neither source link. So the guard has to be introspection,
    not SQL syntax — which is why this migration is up.py and not up.sql.
    """
    code = _executable_source()
    assert "ADD COLUMN IF NOT EXISTS" not in code, (
        "PostgreSQL-only syntax would fail every SQLite install"
    )
    assert "_columns(conn" in code, "column add must be guarded by introspection"


def test_introspection_is_not_sqlite_only():
    """``PRAGMA table_info`` is the existing migration idiom and fails on PG.

    PostgreSQL is the primary backend and the one this migration exists to
    repair, so existence checks must branch per backend.
    """
    code = _executable_source()
    assert "information_schema.columns" in code
    assert "PRAGMA table_info" in code


def test_down_drops_the_table_but_keeps_the_columns(db):
    """A down-migration that loses data is worse than two nullable columns.

    Dropping the dic_documents columns would destroy the provenance backlinks of
    every document created while the migration was applied.
    """
    _apply(None, db)
    _revert(db)
    n = db.execute("SELECT count(*) FROM sqlite_master "
                   "WHERE name='tenant_component_overrides'").fetchone()[0]
    assert n == 0
    cols = {r[1] for r in db.execute("PRAGMA table_info(dic_documents)")}
    assert "source_wg_result_id" in cols, "down must not destroy provenance"


def test_version_does_not_collide():
    """The defect being fixed is a version collision; the fix must not add one."""
    from tools.db.migration_versions import check
    result = check()
    assert result["new_violations"] == {}, result["new_violations"]


def test_postgres_introspection_is_pinned_to_the_current_schema():
    """`information_schema` spans EVERY schema in the database.

    An unqualified ``WHERE table_name = ...`` answers for whichever copy it
    finds first. Verifying this migration in a scratch schema caught it: both
    columns were reported as already present — because ``public.dic_documents``
    has them on this box — so the ALTER the migration exists to perform was
    silently skipped and the run still reported success.
    """
    code = _executable_source()
    for query_marker in ("information_schema.tables", "information_schema.columns"):
        assert query_marker in code
    assert code.count("table_schema = current_schema()") >= 2, (
        "each information_schema lookup must be pinned to current_schema(), or "
        "it answers for a different schema's copy of the table"
    )
