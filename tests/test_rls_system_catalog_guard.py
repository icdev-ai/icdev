#!/usr/bin/env python3
"""RLS must not inject predicates into system catalogs. CUI // SP-CTI.

`PgVectorStore._has_pgvector()` probes::

    SELECT 1 FROM pg_extension WHERE extname = 'vector'

With a security context attached, `_inject_rls` rewrote that to::

    SELECT 1 FROM pg_extension
    WHERE (classification IS NULL OR classification IN (...)) AND extname = ...

`pg_extension` is a PostgreSQL system catalog with no `classification` column,
so the statement raised UndefinedColumn. The probe's caller read that failure as
"pgvector is unavailable" and fell back to a path that returned nothing.

Net effect: **DIC search returned zero results for every query in the browser
while returning ten from a script** — because a script has no Flask request
context, so no RLS, so no injection. Nothing logged an error at any layer. It
presented exactly as bad retrieval.

The guard must be narrow. Skipping injection is a privilege escalation if it
ever applies to an application table, so the negative cases below matter more
than the positive ones.
"""
from __future__ import annotations

import pytest

from tools.security.row_security import _is_system_table, inject_row_predicate

_CLS = {"CUI", "UNCLASSIFIED", "PUBLIC"}


# --------------------------------------------------------------------------- #
# System catalogs are left alone
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("sql", [
    "SELECT 1 FROM pg_extension WHERE extname = 'vector'",
    "SELECT extname FROM pg_extension",
    "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
    "SELECT tablename FROM pg_catalog.pg_tables",
    "SELECT name FROM sqlite_master WHERE type = 'table'",
    "SELECT 1 FROM pg_indexes WHERE indexname = %s",
])
def test_system_catalog_queries_are_not_rewritten(sql):
    out, extra, n = inject_row_predicate(
        sql, tenant_id="acme", classifications=_CLS, placeholder="%s")
    assert out == sql, "system catalog query was rewritten"
    assert extra == ()


def test_the_exact_probe_that_broke_search():
    """Regression, verbatim from PgVectorStore._has_pgvector()."""
    sql = "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
    out, extra, _ = inject_row_predicate(
        sql, tenant_id=None, classifications=_CLS, placeholder="%s")
    assert "classification" not in out
    assert out == sql and extra == ()


@pytest.mark.parametrize("sql,expected", [
    ("SELECT 1 FROM pg_extension", True),
    ("select 1 from PG_EXTENSION", True),
    ("SELECT 1 FROM information_schema.columns", True),
    ("SELECT 1 FROM sqlite_master", True),
    ("SELECT 1 FROM rag_chunks", False),
    ("SELECT 1 FROM dic_documents", False),
    ("SELECT 1 FROM page_views", False),
])
def test_is_system_table_classification(sql, expected):
    assert _is_system_table(sql) is expected


def test_a_table_merely_containing_pg_is_not_a_catalog():
    """`pg_` is a PREFIX rule. A table named e.g. `campaign_pg_stats` is ours."""
    assert _is_system_table("SELECT 1 FROM campaign_pg_stats") is False


# --------------------------------------------------------------------------- #
# Application tables MUST still be protected — the important direction
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("table", ["rag_chunks", "dic_documents", "kanban_tasks", "proposals"])
def test_application_tables_are_still_filtered(table):
    """If the guard ever caught these it would be a silent tenant-isolation hole."""
    sql = f"SELECT id FROM {table} WHERE tier = %s"
    out, extra, _ = inject_row_predicate(
        sql, tenant_id="acme", classifications=_CLS, placeholder="%s")
    assert out != sql, f"{table} lost its RLS predicate"
    assert "tenant_id" in out
    assert "acme" in extra


def test_tenant_predicate_survives_on_a_plain_select():
    out, extra, _ = inject_row_predicate(
        "SELECT id FROM rag_chunks", tenant_id="acme", classifications=None,
        placeholder="%s")
    assert "tenant_id" in out and "acme" in extra


def test_classification_predicate_survives():
    out, extra, _ = inject_row_predicate(
        "SELECT id FROM rag_chunks", tenant_id=None, classifications=_CLS,
        placeholder="%s")
    assert "classification" in out
    assert set(extra) == _CLS


def test_outer_app_table_with_a_catalog_subquery_is_still_filtered():
    """The guard reads the OUTER primary table, not any nested reference.

    A catalog lookup nested inside a query over an application table must not
    exempt that query from row security.
    """
    sql = ("SELECT id FROM rag_chunks WHERE id IN "
           "(SELECT 1 FROM pg_extension WHERE extname = 'vector')")
    out, extra, _ = inject_row_predicate(
        sql, tenant_id="acme", classifications=_CLS, placeholder="%s")
    assert "tenant_id" in out, "app-table query exempted by a nested catalog reference"
    assert "acme" in extra


# --------------------------------------------------------------------------- #
# Placeholder dialect in the PostgreSQL vector store
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("module,func", [
    ("tools.db.storage", "_write_rls_audit"),
    ("tools.db.storage", "_write_column_audit"),
    ("tools.security.field_security", "_write_field_audit"),
])
def test_raw_sqlite_audit_writers_use_sqlite_placeholders(module, func):
    """All three security audit trails were silently empty.

    Each opens a RAW `sqlite3` connection — bypassing `translate_sql` entirely —
    but wrote `%s` placeholders. sqlite3 cannot parse those, so every INSERT
    raised, each function's bare `except: pass` swallowed it, and the audit
    tables stayed empty while reporting as enabled. NIST AU expects an audit
    trail to fail loudly, not to quietly record nothing.
    """
    import importlib
    import inspect

    fn = getattr(importlib.import_module(module), func)
    # Compare the VALUES clause only — the docstrings deliberately name `%s`
    # when explaining the defect, so a whole-source scan would match its own
    # explanation.
    src = inspect.getsource(fn)
    assert "VALUES (%s" not in src, f"{func} uses %s on a raw sqlite3 connection"
    assert "VALUES (?" in src, f"{func} should bind with sqlite ? placeholders"


def test_pg_vector_store_uses_only_pg_placeholders():
    """`?` mixed with `%s` in one statement only worked because translate_sql
    rewrote the strays — making a SQLite init-fallback load-bearing on the
    primary backend, which CLAUDE.md forbids. It broke as soon as RLS appended
    its own `%s` predicate to the same statement.
    """
    import inspect

    from tools.rag import pg_vector_store

    src = inspect.getsource(pg_vector_store)
    offenders = [ln.strip() for ln in src.splitlines()
                 if "where_parts.append(" in ln and "?" in ln]
    assert not offenders, f"bare ? placeholders in the PG store: {offenders}"
