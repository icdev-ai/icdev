#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for G-06 — PostgreSQL column GRANT execution (tools/security/column_security.py).

These tests run against SQLite (conftest forces ICDEV_STORAGE_BACKEND=sqlite).
They verify that:
  - apply_column_grants() reads security_config.yaml policies and calls GRANT DDL
  - SQLite backends skip GRANTs silently (unsupported DDL)
  - apply_column_grants() uses an injected conn when provided (avoids opening a new one)
  - grant_column_select() and revoke_column_select() produce correct DDL strings
  - apply_column_grants() returns a structured result dict

For live PostgreSQL integration, set ICDEV_STORAGE_BACKEND=postgresql and run with
a real PG connection: pytest tests/test_column_security_pg.py -v
"""

import os
import pytest

import tools.security.column_security as _cs


# ---------------------------------------------------------------------------
# DDL string helpers
# ---------------------------------------------------------------------------

class TestGrantRevokeDDL:
    def test_grant_single_column(self):
        ddl = _cs.grant_column_select("users", ["email"], "viewer")
        assert "GRANT SELECT" in ddl
        assert "email" in ddl
        assert "users" in ddl
        assert "viewer" in ddl

    def test_grant_multiple_columns(self):
        ddl = _cs.grant_column_select("dashboard_users", ["email", "api_key_hash"], "auditor")
        assert "email" in ddl
        assert "api_key_hash" in ddl
        assert "dashboard_users" in ddl
        assert "auditor" in ddl

    def test_revoke_produces_revoke_keyword(self):
        ddl = _cs.revoke_column_select("tenants", ["billing_info"], "viewer")
        assert "REVOKE" in ddl
        assert "billing_info" in ddl
        assert "tenants" in ddl
        assert "viewer" in ddl

    def test_grant_ends_with_semicolon(self):
        ddl = _cs.grant_column_select("t", ["c"], "r")
        assert ddl.strip().endswith(";")

    def test_revoke_ends_with_semicolon(self):
        ddl = _cs.revoke_column_select("t", ["c"], "r")
        assert ddl.strip().endswith(";")


# ---------------------------------------------------------------------------
# apply_column_grants — result structure
# ---------------------------------------------------------------------------

class TestApplyColumnGrantsResult:
    def test_returns_result_dict_keys(self, monkeypatch):
        """apply_column_grants always returns the required keys."""
        monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
        result = _cs.apply_column_grants()
        assert "backend" in result
        assert "grants_applied" in result
        assert "grants_skipped" in result
        assert "details" in result
        assert isinstance(result["details"], list)

    def test_backend_reflects_env(self, monkeypatch):
        monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
        result = _cs.apply_column_grants()
        assert result["backend"] == "sqlite"


# ---------------------------------------------------------------------------
# apply_column_grants — injected connection (no real PG needed)
# ---------------------------------------------------------------------------

class TestApplyColumnGrantsInjectedConn:
    def test_injected_conn_receives_grant_calls(self, monkeypatch):
        """With an injected mock conn, verify GRANT DDL is executed for each policy."""
        executed = []

        class MockConn:
            def execute(self, sql, *args):
                executed.append(sql)

            def commit(self):
                pass

            def close(self):
                pass

        # Patch config to return a known 2-policy set
        monkeypatch.setattr(_cs, "_load_config", lambda: {
            "column_policies": [
                {"table": "users", "role": "viewer", "columns": {"email": "redact", "phone": "null"}},
                {"table": "tenants", "role": "viewer", "columns": {"billing_info": "null"}},
            ]
        })
        monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "postgresql")

        result = _cs.apply_column_grants(conn=MockConn())

        # Both policies should have generated GRANT DDL
        assert len(executed) == 2
        grant_sqls = " ".join(executed)
        assert "GRANT SELECT" in grant_sqls
        assert "users" in grant_sqls
        assert "tenants" in grant_sqls
        assert result["grants_applied"] == 2
        assert result["grants_skipped"] == 0

    def test_sqlite_skips_grant_silently(self, monkeypatch):
        """On SQLite, GRANTs that fail are counted as skipped, not errors."""
        errors_logged = []

        class FailingConn:
            def execute(self, sql, *args):
                raise Exception("near 'SELECT': syntax error")  # SQLite GRANT error

            def commit(self):
                pass

            def close(self):
                pass

        monkeypatch.setattr(_cs, "_load_config", lambda: {
            "column_policies": [
                {"table": "users", "role": "viewer", "columns": {"email": "redact"}},
            ]
        })
        monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")

        result = _cs.apply_column_grants(conn=FailingConn())

        assert result["grants_applied"] == 0
        assert result["grants_skipped"] == 1
        assert result["details"][0]["status"] == "sqlite_skip"

    def test_empty_policies_no_calls(self, monkeypatch):
        monkeypatch.setattr(_cs, "_load_config", lambda: {"column_policies": []})
        called = []

        class SpyConn:
            def execute(self, sql, *args):
                called.append(sql)

            def commit(self):
                pass

            def close(self):
                pass

        result = _cs.apply_column_grants(conn=SpyConn())
        assert called == []
        assert result["grants_applied"] == 0
        assert result["grants_skipped"] == 0

    def test_policy_missing_columns_is_skipped(self, monkeypatch):
        monkeypatch.setattr(_cs, "_load_config", lambda: {
            "column_policies": [
                {"table": "users", "role": "viewer", "columns": {}},
            ]
        })
        monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "postgresql")

        class NullConn:
            def execute(self, sql, *args):
                raise AssertionError("should not be called")

            def commit(self):
                pass

            def close(self):
                pass

        result = _cs.apply_column_grants(conn=NullConn())
        assert result["grants_applied"] == 0
        assert result["grants_skipped"] == 1
        assert result["details"][0]["status"] in ("skipped_no_cols", "skipped_empty")

    def test_pg_grant_error_counted_as_skipped_with_error_status(self, monkeypatch):
        monkeypatch.setattr(_cs, "_load_config", lambda: {
            "column_policies": [
                {"table": "missing_table", "role": "viewer", "columns": {"col": "null"}},
            ]
        })
        monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "postgresql")

        class BrokenPGConn:
            def execute(self, sql, *args):
                raise Exception("relation 'missing_table' does not exist")

            def commit(self):
                pass

            def close(self):
                pass

        result = _cs.apply_column_grants(conn=BrokenPGConn())
        assert result["grants_applied"] == 0
        assert result["grants_skipped"] == 1
        assert result["details"][0]["status"] == "error"
        assert "missing_table" in result["details"][0].get("error", "")


# ---------------------------------------------------------------------------
# apply_column_grants reads real security_config.yaml policies
# ---------------------------------------------------------------------------

class TestApplyColumnGrantsRealConfig:
    def test_real_config_has_policies(self):
        """security_config.yaml must contain at least one column_policy."""
        config = _cs._load_config()
        policies = config.get("column_policies", [])
        assert len(policies) > 0, "No column_policies defined in args/security_config.yaml"

    def test_sqlite_run_against_real_config(self, monkeypatch):
        """Full run against real config on SQLite — should return skipped entries, not crash."""
        monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")

        class SkipConn:
            def execute(self, sql, *args):
                raise Exception("SQLite column grant not supported")

            def commit(self):
                pass

            def close(self):
                pass

        result = _cs.apply_column_grants(conn=SkipConn())
        config = _cs._load_config()
        expected_policies = len(config.get("column_policies", []))
        assert result["grants_applied"] == 0
        assert result["grants_skipped"] == expected_policies
