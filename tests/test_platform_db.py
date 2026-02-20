# CUI // SP-CTI
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlite3
from unittest import mock

import pytest

import tools.saas.platform_db as platform_db_mod
from tools.saas.platform_db import (
    EXPECTED_TABLES,
    init_platform_db,
    verify_platform_db,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _patch_sqlite_path(tmp_path):
    """Return mock patches that redirect platform_db to a temp directory."""
    db_path = tmp_path / "platform.db"
    return (
        mock.patch.object(platform_db_mod, "DATA_DIR", tmp_path),
        mock.patch.object(platform_db_mod, "SQLITE_PATH", db_path),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestInitCreatesDatabase:
    """init_platform_db should create the SQLite database file."""

    def test_creates_database_file(self, tmp_path):
        db_path = tmp_path / "platform.db"
        with mock.patch.object(platform_db_mod, "DATA_DIR", tmp_path), \
             mock.patch.object(platform_db_mod, "SQLITE_PATH", db_path), \
             mock.patch.dict("os.environ", {}, clear=False):
            # Ensure PLATFORM_DB_URL is unset so SQLite is used
            with mock.patch.object(platform_db_mod, "_get_db_url", return_value=None):
                init_platform_db()
        assert db_path.exists()


class TestInitCreatesExpectedTables:
    """Each of the 6 expected tables must exist after init."""

    @pytest.fixture(autouse=True)
    def _setup_db(self, tmp_path):
        self.db_path = tmp_path / "platform.db"
        with mock.patch.object(platform_db_mod, "DATA_DIR", tmp_path), \
             mock.patch.object(platform_db_mod, "SQLITE_PATH", self.db_path), \
             mock.patch.object(platform_db_mod, "_get_db_url", return_value=None):
            self.result = init_platform_db()

    def test_creates_tenants_table(self):
        assert "tenants" in self.result["tables_created"]

    def test_creates_users_table(self):
        assert "users" in self.result["tables_created"]

    def test_creates_api_keys_table(self):
        assert "api_keys" in self.result["tables_created"]

    def test_creates_subscriptions_table(self):
        assert "subscriptions" in self.result["tables_created"]

    def test_creates_usage_records_table(self):
        assert "usage_records" in self.result["tables_created"]

    def test_creates_audit_platform_table(self):
        assert "audit_platform" in self.result["tables_created"]


class TestInitIdempotent:
    """Running init_platform_db twice should not raise errors."""

    def test_init_twice_succeeds(self, tmp_path):
        db_path = tmp_path / "platform.db"
        with mock.patch.object(platform_db_mod, "DATA_DIR", tmp_path), \
             mock.patch.object(platform_db_mod, "SQLITE_PATH", db_path), \
             mock.patch.object(platform_db_mod, "_get_db_url", return_value=None):
            result1 = init_platform_db()
            result2 = init_platform_db()
        assert result1["status"] == "ok"
        assert result2["status"] == "ok"


class TestColumnTypes:
    """Verify selected columns have the correct SQLite type affinity."""

    def test_tenants_has_expected_columns(self, tmp_path):
        db_path = tmp_path / "platform.db"
        with mock.patch.object(platform_db_mod, "DATA_DIR", tmp_path), \
             mock.patch.object(platform_db_mod, "SQLITE_PATH", db_path), \
             mock.patch.object(platform_db_mod, "_get_db_url", return_value=None):
            init_platform_db()

        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute("PRAGMA table_info(tenants)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}
        conn.close()

        assert "id" in columns
        assert "name" in columns
        assert "slug" in columns
        assert "impact_level" in columns
        assert "status" in columns
        assert "tier" in columns
        assert "created_at" in columns


class TestParentDirectoryCreation:
    """init should create parent directories if they do not exist."""

    def test_creates_parent_directories(self, tmp_path):
        nested = tmp_path / "a" / "b" / "c"
        db_path = nested / "platform.db"
        with mock.patch.object(platform_db_mod, "DATA_DIR", nested), \
             mock.patch.object(platform_db_mod, "SQLITE_PATH", db_path), \
             mock.patch.object(platform_db_mod, "_get_db_url", return_value=None):
            init_platform_db()
        assert nested.exists()
        assert db_path.exists()


class TestReturnStructure:
    """Verify the dict returned by init_platform_db."""

    def test_result_contains_status_and_backend(self, tmp_path):
        db_path = tmp_path / "platform.db"
        with mock.patch.object(platform_db_mod, "DATA_DIR", tmp_path), \
             mock.patch.object(platform_db_mod, "SQLITE_PATH", db_path), \
             mock.patch.object(platform_db_mod, "_get_db_url", return_value=None):
            result = init_platform_db()
        assert result["status"] == "ok"
        assert result["backend"] == "sqlite"
        assert isinstance(result["tables_created"], list)
        assert result["missing_tables"] == []
