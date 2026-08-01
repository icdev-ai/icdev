# CUI // SP-CTI
"""Unit tests for tools/iqe/adapters/bi_dashboard.py.

Connections are obtained through ``tools.db.storage`` (the translate layer)
rather than a raw ``sqlite3.connect(":memory:")``. The bi.sql_query tests drive
a REAL ``tools.data_canvas.query_sandbox.execute_query`` against a seeded SQLite
data-source file (validate_query + sandbox execution actually run) instead of
mocking ``execute_query`` out, so the read-only SQL gate is genuinely exercised.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.iqe.executor import Executor
from tools.iqe.parser import parse

# bi_data_sources DDL — created THROUGH the storage layer (executescript), never
# raw sqlite3.connect. Mirrors the columns the adapter SELECTs.
_BI_DATA_SOURCES_DDL = """
CREATE TABLE IF NOT EXISTS bi_data_sources (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    columns_json     TEXT DEFAULT '[]',
    dimensions_json  TEXT DEFAULT '[]',
    measures_json    TEXT DEFAULT '[]',
    row_count        INTEGER DEFAULT 0,
    classification   TEXT DEFAULT 'CUI // SP-CTI',
    tenant_id        TEXT DEFAULT 'default',
    created_at       TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


@pytest.fixture
def bi_conn(tmp_path):
    """StorageConnection (SQLite backend under conftest) seeded with one
    bi_data_sources row via the storage translate layer."""
    from tools.db.storage import get_connection

    conn = get_connection(db_path=str(tmp_path / "bi_test.db"))
    conn.executescript(_BI_DATA_SOURCES_DDL)
    conn.execute(
        "INSERT INTO bi_data_sources (id, name, columns_json, row_count) "
        "VALUES (?, ?, ?, ?)",
        ("ds-01", "Sales", '["region","sales"]', 3),
    )
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def empty_conn(tmp_path):
    """A StorageConnection to an empty DB (no bi_data_sources table)."""
    from tools.db.storage import get_connection

    conn = get_connection(db_path=str(tmp_path / "bi_empty.db"))
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def sql_source(tmp_path):
    """A real SQLite data-source file seeded via the storage layer.

    Returns conn_params for query_sandbox.execute_query, which opens this path
    read-only and runs the (real) validate_query + execution path.
    """
    from tools.db.storage import get_connection

    db_path = tmp_path / "sqlsrc.db"
    conn = get_connection(db_path=str(db_path))
    conn.executescript("CREATE TABLE t (a TEXT, b TEXT);")
    conn.execute("INSERT INTO t (a, b) VALUES (?, ?)", ("1", "x"))
    conn.execute("INSERT INTO t (a, b) VALUES (?, ?)", ("2", "y"))
    conn.commit()
    conn.close()
    return {"db_type": "sqlite", "path": str(db_path)}


def test_uploaded_datasets_returns_rows(bi_conn) -> None:
    from tools.iqe.adapters.bi_dashboard import uploaded_datasets_adapter

    rows = uploaded_datasets_adapter(bi_conn)

    assert len(rows) == 1
    assert rows[0]["name"] == "Sales"
    assert rows[0]["row_count"] == 3


def test_uploaded_datasets_degrades_on_missing_table(empty_conn) -> None:
    from tools.iqe.adapters.bi_dashboard import uploaded_datasets_adapter

    assert uploaded_datasets_adapter(empty_conn) == []


def test_iqe_query_filters_uploaded_datasets(bi_conn) -> None:
    from tools.iqe.adapters.bi_dashboard import uploaded_datasets_adapter

    ast = parse("foreach d in bi.uploaded_datasets where d.name == 'Sales' select *")
    ex = Executor()
    ex.register_collection("bi.uploaded_datasets", uploaded_datasets_adapter)
    result = ex.run(ast, conn=bi_conn)

    assert len(result) == 1
    assert result[0]["id"] == "ds-01"


def test_kg_timeseries_no_graph_id_returns_empty() -> None:
    from tools.iqe.adapters.bi_dashboard import kg_timeseries_adapter

    assert kg_timeseries_adapter() == []


def test_kg_timeseries_wraps_graph_evolution() -> None:
    from tools.iqe.adapters.bi_dashboard import kg_timeseries_adapter

    fake_result = {
        "status": "ok",
        "time_series": [
            {"date": "2026-01-01", "nodes_added": 3, "edges_added": 1,
             "cumulative_nodes": 3, "cumulative_edges": 1},
        ],
    }
    with patch("tools.knowledge_graph.temporal.graph_evolution", return_value=fake_result) as mock_fn:
        rows = kg_timeseries_adapter(graph_id="proj-1", interval="week", limit=10)
    mock_fn.assert_called_once_with("proj-1", interval="week", limit=10)
    assert rows == fake_result["time_series"]


def test_kg_timeseries_degrades_on_error_status() -> None:
    from tools.iqe.adapters.bi_dashboard import kg_timeseries_adapter

    with patch("tools.knowledge_graph.temporal.graph_evolution",
               return_value={"status": "error", "error": "bad interval"}):
        assert kg_timeseries_adapter(graph_id="proj-1") == []


def test_kg_coverage_flattens_per_project_dict() -> None:
    from tools.iqe.adapters.bi_dashboard import kg_coverage_adapter

    fake_result = {
        "status": "ok",
        "per_project": {
            "proj-a": {"total_controls": 10, "unique_controls": 2, "coverage_pct": 80.0},
            "proj-b": {"total_controls": 5, "unique_controls": 1, "coverage_pct": 40.0},
        },
    }
    with patch("tools.knowledge_graph.federation.cross_project_coverage", return_value=fake_result):
        rows = kg_coverage_adapter(framework="nist")

    assert len(rows) == 2
    by_project = {r["project_id"]: r for r in rows}
    assert by_project["proj-a"]["coverage_pct"] == 80.0
    assert by_project["proj-b"]["total_controls"] == 5


def test_sql_query_no_sql_returns_empty() -> None:
    from tools.iqe.adapters.bi_dashboard import sql_query_adapter

    assert sql_query_adapter() == []


def test_sql_query_passes_through_columns_and_rows(sql_source) -> None:
    """Real execute_query: SELECT flows through validate_query + the sandbox."""
    from tools.iqe.adapters.bi_dashboard import sql_query_adapter

    rows = sql_query_adapter(sql="SELECT a, b FROM t ORDER BY a", conn_params=sql_source)
    assert rows == [{"a": "1", "b": "x"}, {"a": "2", "b": "y"}]


def test_sql_query_accepts_json_string_conn_params(sql_source) -> None:
    """conn_params supplied as a JSON string is parsed and reaches a real read."""
    from tools.iqe.adapters.bi_dashboard import sql_query_adapter

    rows = sql_query_adapter(sql="SELECT a FROM t WHERE a = '1'",
                             conn_params=json.dumps(sql_source))
    assert rows == [{"a": "1"}]


def test_sql_query_returns_error_row_on_validation_failure(sql_source) -> None:
    """A non-read-only statement is rejected by the real validate_query gate."""
    from tools.iqe.adapters.bi_dashboard import sql_query_adapter

    rows = sql_query_adapter(sql="DROP TABLE t", conn_params=sql_source)
    assert len(rows) == 1
    assert "error" in rows[0]
    # Real validate_query rejects the statement shape before any execution.
    assert "SELECT/WITH/EXPLAIN" in rows[0]["error"]
