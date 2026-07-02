# CUI // SP-CTI
"""Unit tests for tools/iqe/adapters/bi_dashboard.py."""
from __future__ import annotations

import sqlite3
from unittest.mock import patch

from tools.iqe.executor import Executor
from tools.iqe.parser import parse

_SCHEMA = """
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


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT INTO bi_data_sources (id, name, columns_json, row_count) "
        "VALUES ('ds-01', 'Sales', '[\"region\",\"sales\"]', 3)"
    )
    conn.commit()
    return conn


def test_uploaded_datasets_returns_rows() -> None:
    from tools.iqe.adapters.bi_dashboard import uploaded_datasets_adapter

    conn = _make_conn()
    rows = uploaded_datasets_adapter(conn)
    conn.close()

    assert len(rows) == 1
    assert rows[0]["name"] == "Sales"
    assert rows[0]["row_count"] == 3


def test_uploaded_datasets_degrades_on_missing_table() -> None:
    from tools.iqe.adapters.bi_dashboard import uploaded_datasets_adapter

    conn = sqlite3.connect(":memory:")  # no bi_data_sources table
    assert uploaded_datasets_adapter(conn) == []
    conn.close()


def test_iqe_query_filters_uploaded_datasets() -> None:
    from tools.iqe.adapters.bi_dashboard import uploaded_datasets_adapter

    conn = _make_conn()
    ast = parse("foreach d in bi.uploaded_datasets where d.name == 'Sales' select *")
    ex = Executor()
    ex.register_collection("bi.uploaded_datasets", uploaded_datasets_adapter)
    result = ex.run(ast, conn=conn)
    conn.close()

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


def test_sql_query_passes_through_columns_and_rows() -> None:
    from tools.iqe.adapters.bi_dashboard import sql_query_adapter

    fake_result = {"columns": ["a", "b"], "rows": [["1", "x"], ["2", "y"]], "error": None}
    with patch("tools.data_canvas.query_sandbox.execute_query", return_value=fake_result) as mock_fn:
        rows = sql_query_adapter(sql="SELECT a, b FROM t", conn_params={"db_type": "sqlite", "path": "x.db"})
    mock_fn.assert_called_once()
    assert rows == [{"a": "1", "b": "x"}, {"a": "2", "b": "y"}]


def test_sql_query_accepts_json_string_conn_params() -> None:
    from tools.iqe.adapters.bi_dashboard import sql_query_adapter

    fake_result = {"columns": ["a"], "rows": [["1"]], "error": None}
    with patch("tools.data_canvas.query_sandbox.execute_query", return_value=fake_result) as mock_fn:
        rows = sql_query_adapter(sql="SELECT a FROM t", conn_params='{"db_type": "sqlite", "path": "x.db"}')
    assert rows == [{"a": "1"}]
    assert mock_fn.call_args[0][1] == {"db_type": "sqlite", "path": "x.db"}


def test_sql_query_returns_error_row_on_validation_failure() -> None:
    from tools.iqe.adapters.bi_dashboard import sql_query_adapter

    fake_result = {"error": "Only SELECT/WITH/EXPLAIN queries are allowed", "columns": [], "rows": []}
    with patch("tools.data_canvas.query_sandbox.execute_query", return_value=fake_result):
        rows = sql_query_adapter(sql="DROP TABLE t", conn_params={"db_type": "sqlite", "path": "x.db"})
    assert rows == [{"error": "Only SELECT/WITH/EXPLAIN queries are allowed"}]
