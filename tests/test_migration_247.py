# CUI // SP-CTI
"""Migration 247 tests — extend dashboard_users.role CHECK constraint.

PG-only migration (SQLite gets the corrected constraint straight from
tools/db/init_icdev_db.py's CREATE TABLE). Uses a mock connection since a
real PostgreSQL instance isn't available in every test environment; the
mock records executed SQL and answers pg_get_constraintdef() queries so
the idempotency/already-current branch is exercised too.
"""
import importlib.util
import sys
from pathlib import Path


_MIGRATION_DIR = (
    Path(__file__).resolve().parent.parent
    / "tools" / "db" / "migrations" / "247_dashboard_users_role_check"
)


def _load_migration_module(name):
    spec = importlib.util.spec_from_file_location(
        f"migration_247_{name}", _MIGRATION_DIR / f"{name}.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


up_mod = _load_migration_module("up")
down_mod = _load_migration_module("down")


class _MockRow(dict):
    pass


class _MockConn:
    """Records executed SQL; answers table-exists / constraint-def queries."""

    def __init__(self, backend="postgresql", table_exists=True, constraint_def=""):
        self._backend = backend
        self._table_exists = table_exists
        self._constraint_def = constraint_def
        self.executed = []
        self.committed = False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "information_schema.tables" in sql or "sqlite_master" in sql:
            return _FakeCursor(_MockRow({"?column?": 1}) if self._table_exists else None)
        if "pg_get_constraintdef" in sql:
            return _FakeCursor(_MockRow({"def": self._constraint_def}) if self._constraint_def is not None else None)
        return _FakeCursor(None)

    def commit(self):
        self.committed = True


class _FakeCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class TestUpPostgres:
    def test_skips_when_table_absent(self):
        conn = _MockConn(table_exists=False)
        result = up_mod.up(conn)
        assert result["status"] == "skipped"
        assert "dashboard_users absent" in result["reason"]

    def test_skips_sqlite_backend(self):
        conn = _MockConn(backend="sqlite", table_exists=True)
        result = up_mod.up(conn)
        assert result["status"] == "skipped"
        assert "SQLite" in result["reason"]

    def test_already_current_skips_alter(self):
        current_def = "CHECK (role = ANY (ARRAY[" + ", ".join(
            f"'{r}'::text" for r in up_mod._ROLES
        ) + "]))"
        conn = _MockConn(constraint_def=current_def)
        result = up_mod.up(conn)
        assert result["status"] == "applied"
        assert result["actions"] == ["already_current"]
        assert not any("DROP CONSTRAINT" in sql for sql, _ in conn.executed)

    def test_expands_constraint_when_role_missing(self):
        # Old 6-role constraint, missing all 8 newer roles.
        old_def = "CHECK (role = ANY (ARRAY['admin'::text, 'pm'::text]))"
        conn = _MockConn(constraint_def=old_def)
        result = up_mod.up(conn)
        assert result["status"] == "applied"
        assert result["actions"] == ["role_check_expanded"]
        drop_calls = [sql for sql, _ in conn.executed if "DROP CONSTRAINT" in sql]
        add_calls = [sql for sql, _ in conn.executed if "ADD CONSTRAINT" in sql]
        assert len(drop_calls) == 1
        assert len(add_calls) == 1
        for role in up_mod._ROLES:
            assert f"'{role}'" in add_calls[0]
        assert conn.committed

    def test_all_fourteen_roles_present_in_new_constraint(self):
        old_def = ""
        conn = _MockConn(constraint_def=old_def)
        up_mod.up(conn)
        add_call = next(sql for sql, _ in conn.executed if "ADD CONSTRAINT" in sql)
        expected = {
            "admin", "pm", "developer", "isso", "co", "cor",
            "migration_engineer", "component_admin", "auditor", "ciso",
            "bd", "capture_mgr", "contract_mgr", "reviewer",
        }
        assert expected == set(up_mod._ROLES)
        for role in expected:
            assert f"'{role}'" in add_call


class TestDownPostgres:
    def test_skips_sqlite_backend(self):
        conn = _MockConn(backend="sqlite")
        result = down_mod.down(conn)
        assert result["status"] == "skipped"

    def test_reverts_to_original_six_roles(self):
        conn = _MockConn(backend="postgresql")
        result = down_mod.down(conn)
        assert result["status"] == "reverted"
        add_call = next(sql for sql, _ in conn.executed if "ADD CONSTRAINT" in sql)
        for role in ("admin", "pm", "developer", "isso", "co", "cor"):
            assert f"'{role}'" in add_call
        for role in ("bd", "capture_mgr", "migration_engineer", "ciso"):
            assert f"'{role}'" not in add_call


class TestRolesMatchAuthConstant:
    def test_migration_roles_match_valid_dashboard_roles(self):
        from tools.dashboard.auth import VALID_DASHBOARD_ROLES

        assert set(up_mod._ROLES) == set(VALID_DASHBOARD_ROLES)
