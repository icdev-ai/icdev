# CUI // SP-CTI
"""Unit tests for tools/bi_dashboard/db/init_db.py SQLite schema."""
from __future__ import annotations

import sqlite3

import pytest

from tools.bi_dashboard.db.init_db import _SCHEMA_SQLITE


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    for stmt in _SCHEMA_SQLITE.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            c.execute(stmt)
    c.commit()
    yield c
    c.close()


def test_all_three_tables_created(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"bi_dashboards", "bi_data_sources", "bi_generation_log"} <= tables


def test_bi_dashboards_defaults(conn):
    conn.execute("INSERT INTO bi_dashboards (id, title) VALUES ('d1', 'My Dashboard')")
    conn.commit()
    row = conn.execute(
        "SELECT title, tiles_json, tenant_id, classification FROM bi_dashboards WHERE id='d1'"
    ).fetchone()
    assert row == ("My Dashboard", "[]", "default", "CUI")


def test_bi_data_sources_defaults(conn):
    conn.execute("INSERT INTO bi_data_sources (id, name, row_count) VALUES ('ds1', 'Sales', 3)")
    conn.commit()
    row = conn.execute(
        "SELECT source_type, row_count, tenant_id, classification FROM bi_data_sources WHERE id='ds1'"
    ).fetchone()
    assert row == ("upload", 3, "default", "CUI")


def test_bi_generation_log_defaults_and_method_check(conn):
    conn.execute("INSERT INTO bi_generation_log (id, prompt, method) VALUES ('g1', 'show sales', 'llm')")
    conn.commit()
    row = conn.execute(
        "SELECT method, accepted, tenant_id, classification FROM bi_generation_log WHERE id='g1'"
    ).fetchone()
    assert row == ("llm", 1, "default", "CUI")


def test_bi_generation_log_rejects_bad_method(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO bi_generation_log (id, prompt, method) VALUES ('g2', 'x', 'not_a_valid_method')"
        )


def test_bi_data_sources_rejects_bad_source_type(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO bi_data_sources (id, name, source_type) VALUES ('ds2', 'x', 'not_a_valid_type')"
        )
