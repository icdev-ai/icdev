# CUI // SP-CTI — Tests for the DDC Query-tab SQL sandbox (dcpr-sec-01)
"""Parser-based read-only gate tests for tools/data_canvas/query_sandbox.py.

These assert on validate_query() directly — no mocking of execute_query, no
raw sqlite3 connections. The gate must reject stacked statements, COPY,
file/catalog read functions, and non-SELECT statements while accepting benign
SELECT / EXPLAIN queries.
"""

import pytest

from tools.data_canvas.query_sandbox import validate_query


# ── Accepted queries ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "select 1",
        "SELECT id, name FROM customers WHERE id = 5",
        "WITH c AS (SELECT 1 AS x) SELECT x FROM c",
        "EXPLAIN SELECT 1",
        "  SELECT 1  ",
        "SELECT 1 -- trailing comment",
    ],
)
def test_benign_queries_accepted(sql):
    result = validate_query(sql)
    assert result["valid"] is True, result
    assert result["error"] is None


# ── Rejected queries ──────────────────────────────────────────────────────────


def test_empty_rejected():
    for sql in ["", "   ", "\n\t", None]:
        result = validate_query(sql)
        assert result["valid"] is False
        assert result["error"]


def test_stacked_statement_rejected():
    # First word is SELECT (would pass a naive first-word check) but the second
    # statement is a COPY … TO PROGRAM RCE — psycopg2 would run both.
    result = validate_query("SELECT 1; COPY x TO PROGRAM 'sh -c id'")
    assert result["valid"] is False
    assert "Multiple statements" in result["error"]


def test_stacked_benign_second_statement_rejected():
    # Even a harmless-looking second SELECT must be rejected.
    result = validate_query("SELECT 1; SELECT 2")
    assert result["valid"] is False
    assert "Multiple statements" in result["error"]


def test_copy_rejected():
    result = validate_query("COPY customers TO '/tmp/out.csv'")
    assert result["valid"] is False
    assert result["error"]


def test_copy_to_program_rejected():
    result = validate_query("COPY t TO PROGRAM 'sh -c id'")
    assert result["valid"] is False
    assert result["error"]


def test_pg_read_file_rejected():
    result = validate_query("SELECT pg_read_file('/etc/passwd')")
    assert result["valid"] is False
    assert "pg_read_file" in result["error"]


def test_pg_ls_dir_rejected():
    result = validate_query("SELECT pg_ls_dir('/etc')")
    assert result["valid"] is False
    assert result["error"]


def test_information_schema_rejected():
    result = validate_query("SELECT table_name FROM information_schema.tables")
    assert result["valid"] is False
    assert "information_schema" in result["error"]


def test_pg_authid_rejected():
    result = validate_query("SELECT rolname, rolpassword FROM pg_authid")
    assert result["valid"] is False
    assert result["error"]


def test_duckdb_read_csv_auto_rejected():
    result = validate_query("SELECT * FROM read_csv_auto('/etc/passwd')")
    assert result["valid"] is False
    assert result["error"]


def test_install_load_rejected():
    for sql in ["INSTALL httpfs", "LOAD httpfs", "SELECT 1; INSTALL httpfs"]:
        result = validate_query(sql)
        assert result["valid"] is False, sql


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO t VALUES (1)",
        "UPDATE t SET x = 1",
        "DELETE FROM t",
        "DROP TABLE t",
        "ALTER TABLE t ADD COLUMN y int",
        "SET statement_timeout = 0",
        "SELECT * FROM dblink('...', 'SELECT 1')",
    ],
)
def test_dml_ddl_and_admin_rejected(sql):
    result = validate_query(sql)
    assert result["valid"] is False, sql
    assert result["error"]
