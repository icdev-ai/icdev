# CUI // SP-CTI
"""docmod-core-01 — Document Modernization Engine schema migration tests.

Asserts:
  1. Migration 257_doc_modernization.sql creates all seven docmod tables
     (idempotently) with the required indexes and RLS columns.
  2. The three append-only tables are registered in the pre_tool_use hook's
     APPEND_ONLY_TABLES and UPDATE/DELETE/DROP/TRUNCATE are blocked.
  3. tests/conftest.py MINIMAL_ICDEV_SCHEMA includes all seven tables.
"""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_FILE = REPO_ROOT / "tools" / "db" / "migrations" / "257_doc_modernization.sql"
HOOK_FILE = REPO_ROOT / ".claude" / "hooks" / "pre_tool_use.py"
# The APPEND_ONLY_TABLES literal moved out of the hook and into the shared
# registry both hook paths read (hgx-guard-01). The hook is still loaded as a
# module below — it re-exports the predicates — but the table names live here.
APPEND_ONLY_REGISTRY = REPO_ROOT / "tools" / "hooks" / "shared_checks.py"

ALL_TABLES = [
    "docmod_scan_runs",
    "docmod_findings",
    "docmod_eol_products",
    "docmod_defacto_standards",
    "docmod_doc_scan_state",
    "docmod_catalog_entries",
    "docmod_catalog_audit",
]
APPEND_ONLY = ["docmod_scan_runs", "docmod_findings", "docmod_catalog_audit"]
MUTABLE = [t for t in ALL_TABLES if t not in APPEND_ONLY]
REQUIRED_INDEXES = ["idx_docmod_findings_doc", "idx_docmod_findings_dedupe"]


def _table_names(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {r[0] for r in rows}


def _index_names(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()
    return {r[0] for r in rows}


@pytest.fixture
def migrated_conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript(MIGRATION_FILE.read_text(encoding="utf-8"))
    yield conn
    conn.close()


class TestMigrationSchema:
    def test_migration_file_exists(self):
        assert MIGRATION_FILE.exists(), f"missing migration file {MIGRATION_FILE}"

    def test_all_seven_tables_created(self, migrated_conn):
        tables = _table_names(migrated_conn)
        for t in ALL_TABLES:
            assert t in tables, f"table {t} not created by migration 257"

    def test_required_indexes_created(self, migrated_conn):
        indexes = _index_names(migrated_conn)
        for idx in REQUIRED_INDEXES:
            assert idx in indexes, f"index {idx} not created by migration 257"

    def test_migration_is_idempotent(self, migrated_conn):
        # Applying the migration a second time must not raise.
        migrated_conn.executescript(MIGRATION_FILE.read_text(encoding="utf-8"))
        assert set(ALL_TABLES) <= _table_names(migrated_conn)

    def test_all_tables_carry_tenant_and_classification(self, migrated_conn):
        for t in ALL_TABLES:
            cols = {
                r[1] for r in migrated_conn.execute(f"PRAGMA table_info({t})")
            }
            assert "tenant_id" in cols, f"{t} missing tenant_id"
            assert "classification" in cols, f"{t} missing classification"

    def test_findings_supersedes_column(self, migrated_conn):
        cols = {
            r[1] for r in migrated_conn.execute("PRAGMA table_info(docmod_findings)")
        }
        assert "supersedes_id" in cols
        assert "dedupe_key" in cols

    def test_catalog_unique_constraint(self, migrated_conn):
        migrated_conn.execute(
            "INSERT INTO docmod_catalog_entries "
            "(entry_id, domain, category, vendor, product, version) "
            "VALUES ('e1','software','os','canonical','ubuntu','22.04')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            migrated_conn.execute(
                "INSERT INTO docmod_catalog_entries "
                "(entry_id, domain, category, vendor, product, version) "
                "VALUES ('e2','software','os','canonical','ubuntu','22.04')"
            )

    def test_eol_products_unique_constraint(self, migrated_conn):
        migrated_conn.execute(
            "INSERT INTO docmod_eol_products (id, product, cycle) "
            "VALUES ('p1','python','3.9')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            migrated_conn.execute(
                "INSERT INTO docmod_eol_products (id, product, cycle) "
                "VALUES ('p2','python','3.9')"
            )


def _load_hook_module():
    spec = importlib.util.spec_from_file_location("pre_tool_use_hook", HOOK_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestAppendOnlyRegistration:
    def test_tables_registered_in_hook_source(self):
        source = APPEND_ONLY_REGISTRY.read_text(encoding="utf-8")
        for t in APPEND_ONLY:
            assert f'"{t}"' in source, f"{t} not in APPEND_ONLY_TABLES"

    @pytest.mark.parametrize("table", APPEND_ONLY)
    @pytest.mark.parametrize(
        "sql_tpl",
        [
            "UPDATE {t} SET state='x' WHERE 1=1",
            "DELETE FROM {t} WHERE 1=1",
            "DROP TABLE {t}",
            "TRUNCATE {t}",
        ],
    )
    def test_hook_blocks_mutations(self, table, sql_tpl):
        mod = _load_hook_module()
        cmd = f'sqlite3 data/icdev.db "{sql_tpl.format(t=table)}"'
        assert mod.is_append_only_table_modification(
            "Bash", {"command": cmd}
        ), f"hook did not block: {sql_tpl.format(t=table)}"

    @pytest.mark.parametrize("table", APPEND_ONLY)
    def test_hook_allows_select_and_insert(self, table):
        mod = _load_hook_module()
        assert not mod.is_append_only_table_modification(
            "Bash", {"command": f"sqlite3 data/icdev.db 'SELECT * FROM {table}'"}
        )
        assert not mod.is_append_only_table_modification(
            "Bash", {"command": f"psql -c \"INSERT INTO {table} VALUES ('x')\""}
        )

    @pytest.mark.parametrize("table", MUTABLE)
    def test_mutable_tables_not_blocked(self, table):
        mod = _load_hook_module()
        assert not mod.is_append_only_table_modification(
            "Bash",
            {"command": f"psql -c \"UPDATE {table} SET tenant_id='t' WHERE 1=0\""},
        ), f"mutable table {table} must not be append-only"


class TestConftestSchema:
    def test_minimal_schema_contains_all_tables(self):
        import tests.conftest as c

        for t in ALL_TABLES:
            assert (
                f"CREATE TABLE IF NOT EXISTS {t}" in c.MINIMAL_ICDEV_SCHEMA
            ), f"{t} missing from MINIMAL_ICDEV_SCHEMA"

    def test_icdev_db_fixture_creates_tables(self, icdev_db):
        conn = sqlite3.connect(str(icdev_db))
        try:
            tables = _table_names(conn)
            for t in ALL_TABLES:
                assert t in tables, f"icdev_db fixture missing {t}"
        finally:
            conn.close()
