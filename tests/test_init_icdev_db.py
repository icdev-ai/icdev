# CUI // SP-CTI
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""Tests for tools.db.init_icdev_db — ICDEV database initialization."""

import sqlite3
from unittest.mock import patch

import pytest

from tools.db.init_icdev_db import (
    AGENTIC_ALTER_SQL,
    COMPLIANCE_PLATFORM_ALTER_SQL,
    FIPS_ALTER_SQL,
    MARKETPLACE_ALTER_SQL,
    MBSE_ALTER_SQL,
    MODERNIZATION_ALTER_SQL,
    MOSA_ALTER_SQL,
    RICOAS_ALTER_SQL,
    SCHEMA_SQL,
    _has_migration_system,
    init_db,
)


@pytest.fixture
def db_path(tmp_path):
    """Return a temp database path."""
    return tmp_path / "test_icdev.db"


class TestInitDb:
    """Tests for init_db function."""

    def test_init_db_creates_database_file(self, db_path):
        """init_db should create the database file at the specified path."""
        assert not db_path.exists()
        init_db(db_path=db_path)
        assert db_path.exists()

    def test_init_db_creates_expected_tables(self, db_path):
        """init_db should create core tables like projects, agents, audit_trail."""
        tables = init_db(db_path=db_path)
        assert isinstance(tables, list)
        assert len(tables) > 0
        # Check a set of key tables
        expected = {"projects", "agents", "audit_trail", "a2a_tasks", "compliance_controls"}
        assert expected.issubset(set(tables))

    def test_init_db_is_idempotent(self, db_path):
        """Running init_db twice should not raise errors."""
        tables1 = init_db(db_path=db_path)
        tables2 = init_db(db_path=db_path)
        assert len(tables1) == len(tables2)

    def test_init_db_applies_alter_table_columns(self, db_path):
        """init_db should apply ALTER TABLE statements (e.g., MBSE columns)."""
        init_db(db_path=db_path)
        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute("PRAGMA table_info(projects)")
        col_names = [row[1] for row in cursor.fetchall()]
        conn.close()

        # Check for columns added by ALTER statements
        assert "sysml_model_path" in col_names  # MBSE
        assert "modernization_status" in col_names  # Modernization
        assert "ricoas_enabled" in col_names  # RICOAS
        assert "agentic_enabled" in col_names  # Agentic
        assert "marketplace_enabled" in col_names  # Marketplace
        assert "fips199_overall" in col_names  # FIPS
        assert "multi_regime_enabled" in col_names  # Compliance Platform
        assert "mosa_enabled" in col_names  # MOSA


class TestHasMigrationSystem:
    """Tests for _has_migration_system helper."""

    def test_has_migration_system_false_for_new_db(self, db_path):
        """_has_migration_system should return False when no DB file exists."""
        assert _has_migration_system(db_path) is False

    def test_has_migration_system_true_when_schema_migrations_exists(self, db_path):
        """_has_migration_system should return True when schema_migrations table exists."""
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE schema_migrations (id INTEGER PRIMARY KEY, version TEXT)"
        )
        conn.commit()
        conn.close()
        assert _has_migration_system(db_path) is True

    def test_init_db_detects_migration_system_and_returns_early(self, db_path):
        """init_db should return empty list when migration system is detected."""
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE schema_migrations (id INTEGER PRIMARY KEY, version TEXT)"
        )
        conn.commit()
        conn.close()

        result = init_db(db_path=db_path)
        assert result == []


class TestMainFunction:
    """Tests for the main() CLI entry point."""

    def test_main_with_reset_flag(self, db_path):
        """main with --reset should delete and recreate the database."""
        # First create a DB
        init_db(db_path=db_path)
        assert db_path.exists()

        # Run main with --reset
        with patch(
            "sys.argv",
            ["init_icdev_db.py", "--db-path", str(db_path), "--reset"],
        ):
            from tools.db.init_icdev_db import main
            main()

        assert db_path.exists()
        # Verify tables exist (DB was recreated)
        conn = sqlite3.connect(str(db_path))
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        conn.close()
        assert len(tables) > 0


class TestSchemaConstants:
    """Tests for schema-related constants."""

    def test_schema_sql_is_nonempty_string(self):
        """SCHEMA_SQL should be a non-empty string containing CREATE TABLE."""
        assert isinstance(SCHEMA_SQL, str)
        assert len(SCHEMA_SQL) > 100
        assert "CREATE TABLE" in SCHEMA_SQL

    def test_all_alter_sql_lists_are_lists_of_strings(self):
        """All ALTER SQL lists should be lists containing string elements."""
        alter_lists = [
            MBSE_ALTER_SQL,
            MODERNIZATION_ALTER_SQL,
            RICOAS_ALTER_SQL,
            AGENTIC_ALTER_SQL,
            MARKETPLACE_ALTER_SQL,
            FIPS_ALTER_SQL,
            COMPLIANCE_PLATFORM_ALTER_SQL,
            MOSA_ALTER_SQL,
        ]
        for alter_list in alter_lists:
            assert isinstance(alter_list, list), f"{alter_list} is not a list"
            assert len(alter_list) > 0, f"{alter_list} is empty"
            for item in alter_list:
                assert isinstance(item, str), f"{item} is not a string"
                assert "ALTER TABLE" in item, f"{item} does not contain ALTER TABLE"
