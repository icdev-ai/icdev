# CUI // SP-CTI
"""Tests for JSON-function translation in translate_sql() (pgp-tx-01).

Covers two correctness gaps:

* Rule 16 (json_array_length): the ``::jsonb`` cast must be applied to a
  *parenthesized* argument. When the argument is a translated json_extract
  (``graph_json::jsonb->>'nodes'``), an un-parenthesized cast binds ``::`` to
  the trailing key literal (``'nodes'::jsonb``), which is invalid JSON and
  500s the network home/project pages.

* Rule 16b (json_each): previously had NO rule and passed through verbatim,
  breaking on PostgreSQL. It must rewrite to ``jsonb_array_elements()`` over
  the extracted sub-document.

Each translation assertion is paired with a live PostgreSQL round-trip that
executes the translated SQL and checks the result. The round-trip tests skip
automatically when PostgreSQL is unreachable.
"""
import os

import pytest

from tools.db.storage import translate_sql


# --------------------------------------------------------------------------- #
# Live PostgreSQL helper — skip when unreachable
# --------------------------------------------------------------------------- #
def _pg_conn():
    """Return a live psycopg2 connection, or None if PG is unreachable."""
    try:
        import psycopg2
    except ImportError:
        return None
    try:
        conn = psycopg2.connect(
            host=os.environ.get("ICDEV_PG_HOST", "localhost"),
            port=int(os.environ.get("ICDEV_PG_PORT", "5432")),
            user=os.environ.get("ICDEV_PG_USER", "icdev"),
            password=os.environ.get("ICDEV_PG_PASSWORD", "icdev_dev_2026"),
            dbname=os.environ.get("ICDEV_PG_DATABASE", "icdev"),
            connect_timeout=int(os.environ.get("ICDEV_PG_CONNECT_TIMEOUT", "5")),
        )
        conn.autocommit = True
        return conn
    except Exception:
        return None


def _pg_scalar(sql: str):
    """Execute translated SQL on PG and return the first scalar, or skip."""
    conn = _pg_conn()
    if conn is None:
        pytest.skip("PostgreSQL unreachable — skipping live round-trip")
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Rule 16 — json_array_length precedence
# --------------------------------------------------------------------------- #
class TestJsonArrayLengthTranslation:
    def test_simple_column_is_parenthesized(self):
        result = translate_sql("SELECT json_array_length(tags) FROM x")
        assert "jsonb_array_length((tags)::jsonb)" in result

    def test_nested_json_extract_is_parenthesized(self):
        """The reported bug: nested json_extract must round-trip through (X)::jsonb."""
        sql = "json_array_length(json_extract(t.graph_json,'$.nodes')) AS node_count"
        result = translate_sql(sql)
        assert "jsonb_array_length((t.graph_json::jsonb->>'nodes')::jsonb)" in result
        # The broken form casts the key literal — must NOT appear.
        assert "'nodes'::jsonb" not in result

    def test_inside_sum_aggregate(self):
        sql = (
            "SELECT COALESCE(SUM(json_array_length("
            "json_extract(t.graph_json,'$.edges'))),0) FROM topo t"
        )
        result = translate_sql(sql)
        assert "jsonb_array_length((t.graph_json::jsonb->>'edges')::jsonb)" in result
        assert "'edges'::jsonb" not in result

    def test_sqlite_passthrough(self):
        sql = "SELECT json_array_length(json_extract(graph_json,'$.nodes')) FROM t"
        assert translate_sql(sql, backend="sqlite") == sql

    def test_live_pg_nested_array_length(self):
        sql = translate_sql(
            "SELECT json_array_length(json_extract(data,'$.nodes')) "
            "FROM (SELECT '{\"nodes\":[1,2,3,4]}' AS data) sub"
        )
        assert _pg_scalar(sql) == 4

    def test_live_pg_simple_array_length(self):
        sql = translate_sql(
            "SELECT json_array_length(data) "
            "FROM (SELECT '[10,20,30]' AS data) sub"
        )
        assert _pg_scalar(sql) == 3


# --------------------------------------------------------------------------- #
# Rule 16b — json_each
# --------------------------------------------------------------------------- #
class TestJsonEachTranslation:
    def test_two_arg_path_form(self):
        result = translate_sql("SELECT * FROM json_each(col, '$.p')")
        assert "jsonb_array_elements((col::jsonb)->'p')" in result
        assert "json_each" not in result

    def test_nested_path_chains_arrows(self):
        result = translate_sql("SELECT * FROM json_each(t.graph_json, '$.a.b')")
        assert "jsonb_array_elements((t.graph_json::jsonb)->'a'->'b')" in result

    def test_one_arg_form(self):
        result = translate_sql("SELECT * FROM json_each(payload)")
        assert "jsonb_array_elements((payload::jsonb))" in result
        assert "json_each" not in result

    def test_sqlite_passthrough(self):
        sql = "SELECT * FROM json_each(col, '$.p')"
        assert translate_sql(sql, backend="sqlite") == sql

    def test_live_pg_two_arg_expands_array(self):
        sql = translate_sql(
            "SELECT count(*) FROM "
            "(SELECT '{\"nodes\":[5,6,7]}' AS data) sub, "
            "json_each(sub.data, '$.nodes')"
        )
        assert _pg_scalar(sql) == 3

    def test_live_pg_one_arg_expands_array(self):
        sql = translate_sql(
            "SELECT count(*) FROM "
            "(SELECT '[1,2]' AS data) sub, "
            "json_each(sub.data)"
        )
        assert _pg_scalar(sql) == 2
