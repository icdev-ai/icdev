# CUI // SP-CTI
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""Tests for tools.db.migration_runner.MigrationRunner."""

import json
import sqlite3

import pytest

from tools.db.migration_runner import MigrationRunner, SCHEMA_MIGRATIONS_DDL


@pytest.fixture
def runner(tmp_path):
    """Create a MigrationRunner with temp DB and migrations dir."""
    db = tmp_path / "test.db"
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    return MigrationRunner(db_path=db, migrations_dir=migrations_dir, engine="sqlite")


@pytest.fixture
def runner_with_db(runner):
    """Runner with an initialized database (empty, but file exists)."""
    conn = sqlite3.connect(str(runner.db_path))
    conn.execute("CREATE TABLE IF NOT EXISTS placeholder (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    return runner


def _create_sql_migration(migrations_dir: Path, version: str, name: str, up_sql: str,
                          down_sql: str = "-- rollback"):
    """Helper to create a migration directory with up.sql, down.sql, and meta.json."""
    dir_name = f"{version}_{name}"
    mdir = migrations_dir / dir_name
    mdir.mkdir(parents=True, exist_ok=True)
    (mdir / "up.sql").write_text(up_sql, encoding="utf-8")
    (mdir / "down.sql").write_text(down_sql, encoding="utf-8")
    (mdir / "meta.json").write_text(json.dumps({
        "description": name,
        "date": "2026-01-01",
        "author": "test",
        "reversible": True,
    }), encoding="utf-8")
    return mdir


def _create_py_migration(migrations_dir: Path, version: str, name: str, up_body: str):
    """Helper to create a migration directory with up.py."""
    dir_name = f"{version}_{name}"
    mdir = migrations_dir / dir_name
    mdir.mkdir(parents=True, exist_ok=True)
    (mdir / "up.py").write_text(
        f"# CUI // SP-CTI\ndef up(conn):\n{up_body}\n",
        encoding="utf-8",
    )
    (mdir / "meta.json").write_text(json.dumps({
        "description": name,
        "date": "2026-01-01",
        "author": "test",
    }), encoding="utf-8")
    return mdir


class TestEnsureMigrationsTable:
    """Tests for migrations table creation."""

    def test_ensure_migrations_table_creates_table(self, runner):
        """ensure_migrations_table should create schema_migrations in a new DB."""
        runner.ensure_migrations_table()
        conn = sqlite3.connect(str(runner.db_path))
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        conn.close()
        assert row is not None

    def test_has_migrations_table_false_for_new_db(self, runner):
        """has_migrations_table should return False when the DB does not exist."""
        assert runner.has_migrations_table() is False

    def test_has_migrations_table_true_after_ensure(self, runner):
        """has_migrations_table should return True after ensure_migrations_table."""
        runner.ensure_migrations_table()
        assert runner.has_migrations_table() is True


class TestDiscoverMigrations:
    """Tests for migration discovery."""

    def test_discover_migrations_finds_matching_dirs(self, runner):
        """discover_migrations should find NNN_description directories."""
        _create_sql_migration(
            runner.migrations_dir, "001", "baseline",
            "CREATE TABLE test1 (id INTEGER PRIMARY KEY);"
        )
        _create_sql_migration(
            runner.migrations_dir, "002", "add_users",
            "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);"
        )
        migrations = runner.discover_migrations()
        assert len(migrations) == 2
        assert migrations[0]["version"] == "001"
        assert migrations[0]["name"] == "baseline"
        assert migrations[1]["version"] == "002"
        assert migrations[1]["name"] == "add_users"

    def test_discover_migrations_ignores_non_matching_dirs(self, runner):
        """Directories not matching NNN_description pattern should be ignored."""
        _create_sql_migration(
            runner.migrations_dir, "001", "baseline",
            "CREATE TABLE t (id INTEGER PRIMARY KEY);"
        )
        # Create non-matching dirs
        (runner.migrations_dir / "readme").mkdir()
        (runner.migrations_dir / "no_version_prefix").mkdir()
        (runner.migrations_dir / ".hidden").mkdir()

        migrations = runner.discover_migrations()
        assert len(migrations) == 1

    def test_discover_migrations_sorts_by_version(self, runner):
        """Migrations should be returned sorted by version number."""
        # Create out of order
        _create_sql_migration(runner.migrations_dir, "003", "third", "SELECT 1;")
        _create_sql_migration(runner.migrations_dir, "001", "first", "SELECT 1;")
        _create_sql_migration(runner.migrations_dir, "002", "second", "SELECT 1;")

        migrations = runner.discover_migrations()
        versions = [m["version"] for m in migrations]
        assert versions == ["001", "002", "003"]


class TestGetAppliedMigrations:
    """Tests for tracking applied migrations."""

    def test_get_applied_migrations_empty_on_new_db(self, runner):
        """Should return empty list when no migrations have been applied."""
        result = runner.get_applied_migrations()
        assert result == []


class TestApplyMigration:
    """Tests for applying migrations."""

    def test_apply_migration_records_in_schema_migrations(self, runner):
        """apply_migration should record the version in schema_migrations."""
        runner.ensure_migrations_table()
        mdir = _create_sql_migration(
            runner.migrations_dir, "001", "baseline",
            "CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT);"
        )
        migrations = runner.discover_migrations()
        result = runner.apply_migration(migrations[0])

        assert result["success"] is True
        assert result["version"] == "001"

        applied = runner.get_applied_migrations()
        assert len(applied) == 1
        assert applied[0]["version"] == "001"

    def test_apply_sql_migration_creates_table(self, runner):
        """An up.sql migration should execute and create the specified table."""
        runner.ensure_migrations_table()
        _create_sql_migration(
            runner.migrations_dir, "001", "create_items",
            "CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT);"
        )
        migrations = runner.discover_migrations()
        runner.apply_migration(migrations[0])

        conn = sqlite3.connect(str(runner.db_path))
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='items'"
        ).fetchone()
        conn.close()
        assert row is not None

    def test_apply_python_migration(self, runner):
        """An up.py migration should be loaded and its up() function called."""
        runner.ensure_migrations_table()
        _create_py_migration(
            runner.migrations_dir, "001", "py_migration",
            "    conn.execute('CREATE TABLE py_test (id INTEGER PRIMARY KEY, data TEXT)')\n    conn.commit()"
        )
        migrations = runner.discover_migrations()
        result = runner.apply_migration(migrations[0])
        assert result["success"] is True

        conn = sqlite3.connect(str(runner.db_path))
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='py_test'"
        ).fetchone()
        conn.close()
        assert row is not None


class TestRollbackMigration:
    """Tests for rollback."""

    def test_rollback_migration_marks_rolled_back_at(self, runner):
        """rollback_migration should set rolled_back_at on the schema_migrations row."""
        runner.ensure_migrations_table()
        _create_sql_migration(
            runner.migrations_dir, "001", "rollback_test",
            "CREATE TABLE rollback_t (id INTEGER PRIMARY KEY);",
            down_sql="DROP TABLE IF EXISTS rollback_t;"
        )
        migrations = runner.discover_migrations()
        runner.apply_migration(migrations[0])

        # Verify applied
        applied = runner.get_applied_migrations()
        assert len(applied) == 1

        # Rollback
        result = runner.rollback_migration(migrations[0])
        assert result["success"] is True

        # After rollback, get_applied_migrations should exclude rolled back
        applied_after = runner.get_applied_migrations()
        assert len(applied_after) == 0

        # But the row should still exist with rolled_back_at set
        conn = sqlite3.connect(str(runner.db_path))
        row = conn.execute(
            "SELECT rolled_back_at FROM schema_migrations WHERE version = '001'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] is not None  # rolled_back_at is set


class TestMigrateUp:
    """Tests for bulk migrate_up."""

    def test_migrate_up_applies_all_pending(self, runner):
        """migrate_up should apply all pending migrations."""
        _create_sql_migration(
            runner.migrations_dir, "001", "first",
            "CREATE TABLE t1 (id INTEGER PRIMARY KEY);"
        )
        _create_sql_migration(
            runner.migrations_dir, "002", "second",
            "CREATE TABLE t2 (id INTEGER PRIMARY KEY);"
        )

        results = runner.migrate_up()
        assert len(results) == 2
        assert all(r["success"] for r in results)

    def test_migrate_up_stops_on_failure(self, runner):
        """migrate_up should stop processing when a migration fails."""
        _create_sql_migration(
            runner.migrations_dir, "001", "good",
            "CREATE TABLE good_t (id INTEGER PRIMARY KEY);"
        )
        _create_sql_migration(
            runner.migrations_dir, "002", "bad",
            "THIS IS NOT VALID SQL AT ALL!!!"
        )
        _create_sql_migration(
            runner.migrations_dir, "003", "never_reached",
            "CREATE TABLE never_t (id INTEGER PRIMARY KEY);"
        )

        results = runner.migrate_up()
        assert len(results) == 2  # 001 succeeded, 002 failed, 003 never attempted
        assert results[0]["success"] is True
        assert results[1]["success"] is False


class TestValidateChecksums:
    """Tests for checksum validation."""

    def test_validate_checksums_detects_modified_files(self, runner):
        """validate_checksums should detect when a migration file has changed."""
        _create_sql_migration(
            runner.migrations_dir, "001", "original",
            "CREATE TABLE orig (id INTEGER PRIMARY KEY);"
        )
        runner.migrate_up()

        # Modify the migration file after it was applied
        up_sql = runner.migrations_dir / "001_original" / "up.sql"
        up_sql.write_text("-- MODIFIED CONTENT\nSELECT 1;", encoding="utf-8")

        issues = runner.validate_checksums()
        assert len(issues) == 1
        assert issues[0]["issue"] == "checksum_mismatch"
        assert issues[0]["version"] == "001"


class TestMarkApplied:
    """Tests for marking a migration as applied without executing."""

    def test_mark_applied_records_version(self, runner):
        """mark_applied should record the version without running the SQL."""
        _create_sql_migration(
            runner.migrations_dir, "001", "baseline",
            "CREATE TABLE should_not_run (id INTEGER PRIMARY KEY);"
        )
        runner.mark_applied("001")

        applied = runner.get_applied_migrations()
        assert len(applied) == 1
        assert applied[0]["version"] == "001"

        # The table should NOT have been created (mark_applied doesn't execute)
        conn = sqlite3.connect(str(runner.db_path))
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='should_not_run'"
        ).fetchone()
        conn.close()
        assert row is None


class TestCreateMigration:
    """Tests for scaffolding new migrations."""

    def test_create_migration_scaffolds_directory(self, runner):
        """create_migration should create a directory with up.sql, down.sql, meta.json."""
        result_path = runner.create_migration("Add new feature")
        mdir = Path(result_path)

        assert mdir.exists()
        assert mdir.is_dir()
        assert (mdir / "up.sql").exists()
        assert (mdir / "down.sql").exists()
        assert (mdir / "meta.json").exists()

        # Verify meta.json content
        meta = json.loads((mdir / "meta.json").read_text(encoding="utf-8"))
        assert meta["description"] == "Add new feature"
        assert "date" in meta


class TestFilterSql:
    """Tests for SQL engine directive filtering."""

    def test_filter_sql_sqlite_only(self, runner):
        """@sqlite-only blocks should be included for sqlite engine."""
        sql = """-- @sqlite-only
CREATE TABLE sqlite_only_t (id INTEGER);
-- @all
CREATE TABLE common_t (id INTEGER);
"""
        filtered = runner._filter_sql(sql)
        assert "sqlite_only_t" in filtered
        assert "common_t" in filtered

    def test_filter_sql_pg_only_excluded_for_sqlite(self, runner):
        """@pg-only blocks should be excluded when engine is sqlite."""
        sql = """-- @pg-only
CREATE TABLE pg_only_t (id SERIAL);
-- @all
CREATE TABLE common_t (id INTEGER);
"""
        filtered = runner._filter_sql(sql)
        assert "pg_only_t" not in filtered
        assert "common_t" in filtered

    def test_filter_sql_all_directive_resets(self, runner):
        """@all should reset inclusion to True after a directive block."""
        sql = """-- @pg-only
CREATE TABLE pg_t (id SERIAL);
-- @all
CREATE TABLE both_t (id INTEGER);
"""
        filtered = runner._filter_sql(sql)
        assert "pg_t" not in filtered
        assert "both_t" in filtered
