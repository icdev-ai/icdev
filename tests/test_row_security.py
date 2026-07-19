#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for tools.security.row_security."""

from tools.security.row_security import (
    inject_row_predicate,
    generate_rls_policy,
    apply_tenant_rls,
    set_pg_session_vars,
)


class TestInjectRowPredicate:
    def test_with_where(self):
        sql, params, _ = inject_row_predicate(
            "SELECT * FROM projects WHERE status = ?", "tenant_a"
        )
        assert "tenant_id = ?" in sql
        assert "tenant_a" in params
        assert "AND" in sql

    def test_without_where(self):
        sql, params, _ = inject_row_predicate("SELECT * FROM projects", "tenant_a")
        assert "WHERE" in sql
        assert "tenant_id = ?" in sql
        assert params == ("tenant_a",)

    def test_with_order_by(self):
        sql, params, _ = inject_row_predicate(
            "SELECT * FROM projects ORDER BY created_at DESC", "tenant_a"
        )
        assert "WHERE" in sql
        assert "ORDER BY" in sql
        assert "tenant_id = ?" in sql

    def test_with_limit(self):
        sql, params, _ = inject_row_predicate(
            "SELECT * FROM projects LIMIT 10", "tenant_a"
        )
        assert "WHERE" in sql
        assert "LIMIT" in sql
        assert "tenant_id = ?" in sql

    def test_no_where_trailing_limit_param_offset(self):
        # Regression: a no-WHERE SELECT with a trailing "LIMIT ?" injects the
        # predicate BEFORE the LIMIT, so n_before must be 0 (the LIMIT param
        # comes AFTER). Previously n_before counted ALL params, binding the
        # LIMIT value into the injected predicate. Classification set → IN(...).
        sql, extra, n_before = inject_row_predicate(
            "SELECT * FROM gd_ai_tournaments ORDER BY created_at DESC LIMIT ?",
            "tenant_a",
            classifications={"CUI", "PUBLIC"},
        )
        # The injected WHERE precedes ORDER BY / LIMIT, and no placeholder
        # precedes that injection point, so the extra params insert at index 0.
        assert n_before == 0
        assert "WHERE" in sql and sql.index("WHERE") < sql.index("LIMIT")
        # extra params are only the classification labels (no LIMIT value)
        assert all(isinstance(p, str) for p in extra)

    def test_classification_predicate(self):
        sql, params, _ = inject_row_predicate(
            "SELECT * FROM docs", "tenant_a", classification="CUI"
        )
        assert "classification = ?" in sql
        assert params == ("tenant_a", "CUI")

    def test_classifications_set(self):
        sql, params, _ = inject_row_predicate(
            "SELECT * FROM docs", "tenant_a", classifications={"CUI", "SECRET"}
        )
        assert "classification IN" in sql
        assert len(params) == 3  # tenant + 2 classifications

    def test_insert_unchanged(self):
        sql, params, _ = inject_row_predicate(
            "INSERT INTO projects (id, name) VALUES (?, ?)", "tenant_a"
        )
        assert sql == "INSERT INTO projects (id, name) VALUES (?, ?)"
        assert params == ()

    def test_pragma_unchanged(self):
        sql, params, _ = inject_row_predicate("PRAGMA table_info(projects)", "tenant_a")
        assert "PRAGMA" in sql
        assert params == ()

    def test_no_tenant_no_classification(self):
        sql, params, _ = inject_row_predicate("SELECT * FROM projects", None)
        assert sql == "SELECT * FROM projects"
        assert params == ()

    def test_update_with_where(self):
        # UPDATE: predicate appended at END so caller can append params safely
        # (SET-slot params come before WHERE-slot params in SQLite binding)
        sql, params, _ = inject_row_predicate(
            "UPDATE projects SET name = ? WHERE id = ?", "tenant_a"
        )
        assert "tenant_id = ?" in sql
        assert params == ("tenant_a",)
        # Predicate must be at the END — after the existing WHERE condition
        assert sql.endswith("AND tenant_id = ?")

    def test_update_without_where(self):
        sql, params, _ = inject_row_predicate(
            "UPDATE projects SET status = ?", "tenant_a"
        )
        assert "WHERE tenant_id = ?" in sql
        assert params == ("tenant_a",)

    def test_delete_with_where(self):
        sql, params, _ = inject_row_predicate(
            "DELETE FROM projects WHERE id = ?", "tenant_a"
        )
        assert "tenant_id = ?" in sql
        assert params == ("tenant_a",)
        assert sql.endswith("AND tenant_id = ?")

    def test_delete_without_where(self):
        sql, params, _ = inject_row_predicate(
            "DELETE FROM projects", "tenant_a"
        )
        assert "WHERE tenant_id = ?" in sql
        assert params == ("tenant_a",)

    def test_subquery_where_injected_in_outer(self):
        """Predicate must be injected into the outermost WHERE, not a subquery's."""
        sql, params, _ = inject_row_predicate(
            "SELECT * FROM projects WHERE id IN (SELECT project_id FROM tasks WHERE status = ?)",
            "tenant_a",
        )
        # The outer WHERE is on 'projects'; the inner WHERE is on 'tasks'.
        # The injected predicate should appear in the outer scope.
        assert "WHERE tenant_id = ? AND id IN" in sql or "WHERE tenant_id = ?" in sql
        assert "tasks WHERE status = ?" in sql  # inner WHERE unchanged
        assert params == ("tenant_a",)


class TestGenerateRLSPolicy:
    def test_basic_ddl(self):
        ddl = generate_rls_policy(
            "projects", "tenant_id = current_setting('app.tenant_id')", ["admin"]
        )
        assert "CREATE POLICY" in ddl
        assert "ON projects" in ddl
        assert "FOR ALL" in ddl
        assert "TO admin" in ddl
        assert "tenant_id = current_setting('app.tenant_id')" in ddl

    def test_custom_policy_name(self):
        ddl = generate_rls_policy(
            "projects",
            "x = 1",
            ["admin", "viewer"],
            policy_name="custom_policy",
            command="SELECT",
        )
        assert "CREATE POLICY custom_policy" in ddl
        assert "FOR SELECT" in ddl
        assert "TO admin, viewer" in ddl


class TestApplyTenantRLS:
    def test_sqlite_conn_mock(self):
        class FakeConn:
            executed = []

            def execute(self, sql):
                self.executed.append(sql)

            def commit(self):
                pass

        conn = FakeConn()
        apply_tenant_rls(conn, "projects")
        assert any("CREATE POLICY" in s for s in conn.executed)


class TestSetPgSessionVars:
    def test_sets_vars(self):
        class FakeConn:
            executed = []

            def execute(self, sql, params=None):
                self.executed.append((sql, params))

        conn = FakeConn()
        set_pg_session_vars(conn, "t1", "CUI")
        assert len(conn.executed) == 2
        assert "app.tenant_id" in conn.executed[0][0]
        assert conn.executed[0][1] == ("t1",)
        assert "app.classification" in conn.executed[1][0]
        assert conn.executed[1][1] == ("CUI",)
