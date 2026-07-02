# CUI // SP-CTI
"""IQE adapter — Canvas Health Dashboard.

Collections:
    canvas_health.canvas_status       — per-canvas QA status (green/amber/red)
    canvas_health.coherence_violations — canvases with RLS or coherence issues
    canvas_health.e2e_coverage         — canvases missing E2E Playwright specs
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def _get_all() -> list[dict]:
    try:
        from tools.canvas_health.health_data import get_canvas_health
        return get_canvas_health()
    except Exception:
        return []


def canvas_status_adapter(conn: Any) -> list[dict]:
    return [
        {
            "key": c["key"],
            "display_name": c["display_name"],
            "status": c["status"],
            "route": c["route"],
            "enabled": c["enabled"],
            "issues": ", ".join(c["issues"]) if c["issues"] else "",
        }
        for c in _get_all()
    ]


def coherence_violations_adapter(conn: Any) -> list[dict]:
    return [
        {
            "key": c["key"],
            "display_name": c["display_name"],
            "rls_ok": c["rls_ok"],
            "issues": ", ".join(c["issues"]) if c["issues"] else "",
        }
        for c in _get_all()
        if c["status"] == "red" or not c["rls_ok"]
    ]


def e2e_coverage_adapter(conn: Any) -> list[dict]:
    return [
        {
            "key": c["key"],
            "display_name": c["display_name"],
            "e2e_exists": c["e2e_exists"],
            "blueprint_exists": c["blueprint_exists"],
        }
        for c in _get_all()
        if not c["e2e_exists"]
    ]


register_collection("canvas_health.canvas_status", canvas_status_adapter)
register_collection("canvas_health.coherence_violations", coherence_violations_adapter)
register_collection("canvas_health.e2e_coverage", e2e_coverage_adapter)
