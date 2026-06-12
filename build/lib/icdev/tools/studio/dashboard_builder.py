"""ICDEV™ Studio — Dashboard Builder.

Custom widget layouts with role-based defaults.  Users pin widgets,
arrange in a responsive grid, bind data sources, and save/share.

Widget data comes from NLQ queries, API endpoints, or DB tables.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str = "dash") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ── Widget library ────────────────────────────────────────────────────

WIDGET_TYPES: list[dict[str, Any]] = [
    {
        "id": "metric_card",
        "label": "Metric Card",
        "icon": "#",
        "description": "Single KPI with label, value, and trend indicator",
        "default_size": {"w": 3, "h": 2},
        "category": "metrics",
        "config_schema": {"title": "str", "query": "str", "format": "number|percent|text"},
    },
    {
        "id": "bar_chart",
        "label": "Bar Chart",
        "icon": "B",
        "description": "Vertical or horizontal bar chart",
        "default_size": {"w": 6, "h": 4},
        "category": "charts",
        "config_schema": {"title": "str", "query": "str", "orientation": "vertical|horizontal"},
    },
    {
        "id": "line_chart",
        "label": "Line Chart",
        "icon": "L",
        "description": "Time-series line chart with multiple series",
        "default_size": {"w": 6, "h": 4},
        "category": "charts",
        "config_schema": {"title": "str", "query": "str"},
    },
    {
        "id": "pie_chart",
        "label": "Pie / Donut",
        "icon": "O",
        "description": "Pie or donut chart for proportional data",
        "default_size": {"w": 4, "h": 4},
        "category": "charts",
        "config_schema": {"title": "str", "query": "str", "donut": "bool"},
    },
    {
        "id": "data_table",
        "label": "Data Table",
        "icon": "T",
        "description": "Sortable, filterable data table",
        "default_size": {"w": 12, "h": 6},
        "category": "tables",
        "config_schema": {"title": "str", "query": "str", "columns": "list"},
    },
    {
        "id": "gauge",
        "label": "Gauge",
        "icon": "G",
        "description": "Circular gauge for compliance scores or health",
        "default_size": {"w": 3, "h": 3},
        "category": "metrics",
        "config_schema": {"title": "str", "value": "number", "max": "number", "thresholds": "list"},
    },
    {
        "id": "status_list",
        "label": "Status List",
        "icon": "S",
        "description": "List of items with status indicators",
        "default_size": {"w": 4, "h": 5},
        "category": "status",
        "config_schema": {"title": "str", "query": "str"},
    },
    {
        "id": "case_board_mini",
        "label": "Case Board",
        "icon": "K",
        "description": "Mini Kanban board for case tracking",
        "default_size": {"w": 6, "h": 5},
        "category": "cases",
        "config_schema": {"title": "str", "case_type_id": "str"},
    },
    {
        "id": "agent_grid",
        "label": "Agent Grid",
        "icon": "A",
        "description": "Agent status cards with health indicators",
        "default_size": {"w": 12, "h": 4},
        "category": "monitoring",
        "config_schema": {"title": "str"},
    },
    {
        "id": "alert_feed",
        "label": "Alert Feed",
        "icon": "!",
        "description": "Live alert/notification feed",
        "default_size": {"w": 4, "h": 5},
        "category": "monitoring",
        "config_schema": {"title": "str", "severity_filter": "str"},
    },
    {
        "id": "compliance_gauge",
        "label": "Compliance Score",
        "icon": "C",
        "description": "Overall compliance posture gauge",
        "default_size": {"w": 3, "h": 3},
        "category": "compliance",
        "config_schema": {"title": "str", "framework": "str"},
    },
    {
        "id": "workflow_status",
        "label": "Workflow Status",
        "icon": "W",
        "description": "Recent workflow executions with status",
        "default_size": {"w": 6, "h": 4},
        "category": "operations",
        "config_schema": {"title": "str"},
    },
    {
        "id": "markdown",
        "label": "Markdown / Text",
        "icon": "M",
        "description": "Free-form markdown or HTML content",
        "default_size": {"w": 6, "h": 3},
        "category": "content",
        "config_schema": {"content": "str"},
    },
    {
        "id": "embed",
        "label": "Embed / iFrame",
        "icon": "E",
        "description": "Embed external URL or internal page",
        "default_size": {"w": 6, "h": 5},
        "category": "content",
        "config_schema": {"url": "str", "title": "str"},
    },
    {
        "id": "automation_feed",
        "label": "Automation Feed",
        "icon": "Z",
        "description": "Recent automation runs with status",
        "default_size": {"w": 4, "h": 5},
        "category": "studio",
        "config_schema": {"title": "str"},
    },
]

# ── Role-based default dashboards ─────────────────────────────────────

ROLE_DEFAULTS: dict[str, dict[str, Any]] = {
    "pm": {
        "name": "Program Manager View",
        "description": "Project health, milestones, compliance posture",
        "widgets": [
            {
                "widget_type": "metric_card",
                "x": 0,
                "y": 0,
                "w": 3,
                "h": 2,
                "config": {"title": "Active Projects", "query": "SELECT COUNT(*) FROM projects WHERE status='active'"},
            },
            {
                "widget_type": "metric_card",
                "x": 3,
                "y": 0,
                "w": 3,
                "h": 2,
                "config": {"title": "Open Findings", "query": "SELECT COUNT(*) FROM poam_items WHERE status='open'"},
            },
            {
                "widget_type": "metric_card",
                "x": 6,
                "y": 0,
                "w": 3,
                "h": 2,
                "config": {"title": "Compliance Score", "query": "SELECT 87", "format": "percent"},
            },
            {
                "widget_type": "metric_card",
                "x": 9,
                "y": 0,
                "w": 3,
                "h": 2,
                "config": {"title": "Deployments Today", "query": "SELECT 3"},
            },
            {
                "widget_type": "compliance_gauge",
                "x": 0,
                "y": 2,
                "w": 4,
                "h": 4,
                "config": {"title": "FedRAMP Posture", "framework": "fedramp"},
            },
            {"widget_type": "status_list", "x": 4, "y": 2, "w": 4, "h": 4, "config": {"title": "Recent Activity"}},
            {"widget_type": "alert_feed", "x": 8, "y": 2, "w": 4, "h": 4, "config": {"title": "Alerts"}},
        ],
    },
    "isso": {
        "name": "ISSO Security View",
        "description": "Security posture, STIG findings, vulnerability status",
        "widgets": [
            {
                "widget_type": "metric_card",
                "x": 0,
                "y": 0,
                "w": 3,
                "h": 2,
                "config": {"title": "CAT1 Findings", "query": "SELECT 0"},
            },
            {
                "widget_type": "metric_card",
                "x": 3,
                "y": 0,
                "w": 3,
                "h": 2,
                "config": {"title": "Open POA&Ms", "query": "SELECT COUNT(*) FROM poam_items WHERE status='open'"},
            },
            {
                "widget_type": "metric_card",
                "x": 6,
                "y": 0,
                "w": 3,
                "h": 2,
                "config": {"title": "Critical CVEs", "query": "SELECT 0"},
            },
            {
                "widget_type": "metric_card",
                "x": 9,
                "y": 0,
                "w": 3,
                "h": 2,
                "config": {"title": "STIG Pass Rate", "query": "SELECT 94", "format": "percent"},
            },
            {"widget_type": "case_board_mini", "x": 0, "y": 2, "w": 6, "h": 5, "config": {"title": "Finding Tracker"}},
            {
                "widget_type": "compliance_gauge",
                "x": 6,
                "y": 2,
                "w": 3,
                "h": 3,
                "config": {"title": "CMMC Score", "framework": "cmmc"},
            },
            {
                "widget_type": "alert_feed",
                "x": 9,
                "y": 2,
                "w": 3,
                "h": 5,
                "config": {"title": "Security Alerts", "severity_filter": "critical,high"},
            },
        ],
    },
    "developer": {
        "name": "Developer View",
        "description": "Build status, test results, code quality",
        "widgets": [
            {
                "widget_type": "metric_card",
                "x": 0,
                "y": 0,
                "w": 3,
                "h": 2,
                "config": {"title": "Test Pass Rate", "query": "SELECT 98", "format": "percent"},
            },
            {
                "widget_type": "metric_card",
                "x": 3,
                "y": 0,
                "w": 3,
                "h": 2,
                "config": {"title": "Code Coverage", "query": "SELECT 85", "format": "percent"},
            },
            {
                "widget_type": "metric_card",
                "x": 6,
                "y": 0,
                "w": 3,
                "h": 2,
                "config": {"title": "Open Issues", "query": "SELECT 12"},
            },
            {
                "widget_type": "metric_card",
                "x": 9,
                "y": 0,
                "w": 3,
                "h": 2,
                "config": {"title": "Deployments", "query": "SELECT 7"},
            },
            {"widget_type": "workflow_status", "x": 0, "y": 2, "w": 6, "h": 4, "config": {"title": "Recent Builds"}},
            {"widget_type": "agent_grid", "x": 6, "y": 2, "w": 6, "h": 4, "config": {"title": "Agent Status"}},
        ],
    },
}


def get_widget_types() -> list[dict]:
    return WIDGET_TYPES


def get_role_defaults() -> dict:
    return ROLE_DEFAULTS


# ── Dashboard CRUD ────────────────────────────────────────────────────


def create_dashboard(
    name: str,
    layout: list[dict],
    *,
    role_default: str | None = None,
    created_by: str = "studio",
    shared: bool = False,
) -> dict:
    dash_id = _new_id("dash")
    layout_json = json.dumps({"grid": layout})

    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO studio_dashboards
               (dashboard_id, name, layout_json, role_default, created_by,
                created_at, updated_at, shared)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (dash_id, name, layout_json, role_default, created_by, _now_iso(), _now_iso(), 1 if shared else 0),
        )
        conn.commit()
        return {"status": "ok", "dashboard_id": dash_id}
    finally:
        conn.close()


def list_dashboards(*, role: str | None = None) -> list[dict]:
    conn = get_connection()
    try:
        sql = "SELECT * FROM studio_dashboards"
        params: list[Any] = []
        if role:
            sql += " WHERE role_default = ? OR role_default IS NULL"
            params.append(role)
        sql += " ORDER BY updated_at DESC"
        rows = conn.execute(sql, params).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            layout = json.loads(d.get("layout_json", "{}"))
            d["widget_count"] = len(layout.get("grid", []))
            result.append(d)
        return result
    finally:
        conn.close()


def get_dashboard(dash_id: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM studio_dashboards WHERE dashboard_id = ?", (dash_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["layout"] = json.loads(d.get("layout_json", "{}"))
        return d
    finally:
        conn.close()


def update_dashboard(
    dash_id: str,
    *,
    name: str | None = None,
    layout: list[dict] | None = None,
    shared: bool | None = None,
) -> dict:
    existing = get_dashboard(dash_id)
    if not existing:
        return {"status": "error", "error": "Dashboard not found"}

    sets: list[str] = []
    vals: list[Any] = []

    if name is not None:
        sets.append("name = ?")
        vals.append(name)
    if layout is not None:
        sets.append("layout_json = ?")
        vals.append(json.dumps({"grid": layout}))
    if shared is not None:
        sets.append("shared = ?")
        vals.append(1 if shared else 0)

    if not sets:
        return {"status": "ok", "dashboard_id": dash_id}

    sets.append("updated_at = ?")
    vals.append(_now_iso())
    vals.append(dash_id)

    conn = get_connection()
    try:
        conn.execute(
            f"UPDATE studio_dashboards SET {', '.join(sets)} WHERE dashboard_id = ?",  # nosec B608
            vals,
        )
        conn.commit()
        return {"status": "ok", "dashboard_id": dash_id}
    finally:
        conn.close()


def delete_dashboard(dash_id: str) -> dict:
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM studio_dashboards WHERE dashboard_id = ?", (dash_id,))
        conn.commit()
        return {"status": "ok"} if cur.rowcount else {"status": "error", "error": "Not found"}
    finally:
        conn.close()


def create_from_role_default(role: str, *, created_by: str = "studio") -> dict:
    """Create a dashboard from a role-based default template."""
    defaults = ROLE_DEFAULTS.get(role)
    if not defaults:
        return {"status": "error", "error": f"No default for role: {role}"}

    return create_dashboard(
        name=defaults["name"],
        layout=defaults["widgets"],
        role_default=role,
        created_by=created_by,
    )


# ── CLI ───────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="ICDEV™ Studio Dashboard Builder")
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("widgets", help="List widget types")
    sub.add_parser("roles", help="List role default dashboards")
    sub.add_parser("list", help="List saved dashboards")

    p_create = sub.add_parser("create-default", help="Create dashboard from role default")
    p_create.add_argument("role", choices=list(ROLE_DEFAULTS.keys()))

    args = parser.parse_args()
    result: Any = None

    if args.command == "widgets":
        result = get_widget_types()
    elif args.command == "roles":
        result = get_role_defaults()
    elif args.command == "list":
        result = list_dashboards()
    elif args.command == "create-default":
        result = create_from_role_default(args.role)
    else:
        parser.print_help()
        return

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
