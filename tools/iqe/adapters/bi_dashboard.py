# CUI // SP-CTI
"""IQE data collection adapters for the BI Dashboard canvas.

Importing this module registers three collections on the module-level
Executor, giving the BI Dashboard's NL->query pipeline one uniform way to
fetch chart-ready rows regardless of source:

  bi.uploaded_datasets — rows parsed from user-uploaded CSV/XLSX/JSON
                         (tools.viz.dataset output), stored in bi_data_sources.
  bi.kg_timeseries     — Knowledge Graph growth over time, wrapping
                         tools.knowledge_graph.temporal.graph_evolution().
  bi.kg_coverage       — Cross-project compliance-control coverage, wrapping
                         tools.knowledge_graph.federation.cross_project_coverage().
  bi.sql_query         — ad hoc SELECT/WITH/EXPLAIN passthrough to
                         tools.data_canvas.query_sandbox.execute_query().

All adapters degrade to an empty list (or a single {"error": ...} row for
bi.sql_query) rather than raising, matching the convention in
tools/iqe/adapters/data.py.
"""
from __future__ import annotations

import json
from typing import Any

from tools.iqe.executor import register_collection


def uploaded_datasets_adapter(conn: Any = None) -> list[dict]:
    """Return dataset rows from bi_data_sources (populated by the upload endpoint)."""
    try:
        if conn is None:
            from tools.db.storage import get_canvas_connection  # noqa: PLC0415
            conn = get_canvas_connection("BI_DASHBOARD_DATABASE_URL")
        cur = conn.execute(
            "SELECT id, name, columns_json, dimensions_json, measures_json, "
            "row_count, classification, tenant_id, created_at FROM bi_data_sources "
            "ORDER BY created_at DESC LIMIT 200"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


def kg_timeseries_adapter(conn: Any = None, graph_id: str = "", interval: str = "day",
                         limit: str | int = 30) -> list[dict]:  # noqa: ARG001
    """Return KG growth over time as flat {date, nodes_added, edges_added, ...} rows."""
    if not graph_id:
        return []
    try:
        from tools.knowledge_graph.temporal import graph_evolution  # noqa: PLC0415
        result = graph_evolution(graph_id, interval=interval, limit=int(limit))
        if result.get("status") != "ok":
            return []
        return list(result.get("time_series", []))
    except Exception:
        return []


def kg_coverage_adapter(conn: Any = None, framework: str = "nist") -> list[dict]:  # noqa: ARG001
    """Return per-project compliance coverage as flat {project_id, ...} rows."""
    try:
        from tools.knowledge_graph.federation import cross_project_coverage  # noqa: PLC0415
        result = cross_project_coverage(framework)
        if result.get("status") != "ok":
            return []
        return [
            {"project_id": pid, **stats}
            for pid, stats in result.get("per_project", {}).items()
        ]
    except Exception:
        return []


def sql_query_adapter(conn: Any = None, sql: str = "",  # noqa: ARG001
                      conn_params: str | dict = "{}") -> list[dict]:
    """Ad hoc read-only SQL passthrough to the DDC query sandbox.

    ``conn_params`` may be a dict (direct Python callers) or a JSON string
    (IQE query-language literal args), e.g. {"db_type": "sqlite", "path": "..."}.
    """
    if not sql:
        return []
    try:
        from tools.data_canvas.query_sandbox import execute_query  # noqa: PLC0415
        params = conn_params if isinstance(conn_params, dict) else json.loads(conn_params or "{}")
        result = execute_query(sql, params)
        if result.get("error"):
            return [{"error": result["error"]}]
        cols = result.get("columns", [])
        return [dict(zip(cols, row)) for row in result.get("rows", [])]
    except Exception as exc:
        return [{"error": str(exc)}]


register_collection("bi.uploaded_datasets", uploaded_datasets_adapter)
register_collection("bi.kg_timeseries", kg_timeseries_adapter)
register_collection("bi.kg_coverage", kg_coverage_adapter)
register_collection("bi.sql_query", sql_query_adapter)
