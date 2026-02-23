#!/usr/bin/env python3
# CUI // SP-CTI
"""Observability MCP Server — Trace, provenance, SHAP, and XAI tools (Phase 46, D280-D289).

Tools:
    trace_query      — Query traces and spans from the otel_spans table
    trace_summary    — Get aggregate trace statistics
    prov_lineage     — Query provenance lineage for an entity
    prov_export      — Export provenance graph as PROV-JSON
    shap_analyze     — Run AgentSHAP tool attribution on a trace
    xai_assess       — Run XAI compliance assessment for a project

Resources:
    observability://config    — Current observability configuration
    observability://stats     — Live trace/prov/SHAP statistics
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

try:
    from tools.compat.db_utils import get_db_connection
except ImportError:
    get_db_connection = None

sys.path.insert(0, str(BASE_DIR))
from tools.mcp.base_server import MCPServer

try:
    from tools.audit.audit_logger import log_event as audit_log_event
except ImportError:
    audit_log_event = None


def _get_db() -> sqlite3.Connection:
    if get_db_connection:
        return get_db_connection(DB_PATH)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _audit(event_type: str, actor: str, action: str, project_id: str = None, details: dict = None):
    if audit_log_event:
        try:
            audit_log_event(
                event_type=event_type, actor=actor, action=action,
                project_id=project_id, details=details,
            )
        except Exception:
            pass


# ── Tool handlers ────────────────────────────────────────────────

def trace_query_handler(args: dict):
    """Query traces and spans."""
    trace_id = args.get("trace_id")
    project_id = args.get("project_id")
    name = args.get("name")
    limit = min(int(args.get("limit", 50)), 200)

    try:
        conn = _get_db()

        if trace_id:
            rows = conn.execute(
                "SELECT * FROM otel_spans WHERE trace_id = ? ORDER BY start_time",
                (trace_id,),
            ).fetchall()
        else:
            clauses, params = [], []
            if project_id:
                clauses.append("project_id = ?")
                params.append(project_id)
            if name:
                clauses.append("name = ?")
                params.append(name)
            where = "WHERE " + " AND ".join(clauses) if clauses else ""
            params.append(limit)
            rows = conn.execute(
                f"SELECT * FROM otel_spans {where} ORDER BY start_time DESC LIMIT ?",
                params,
            ).fetchall()

        conn.close()
        return {"spans": [dict(r) for r in rows], "count": len(rows)}
    except sqlite3.Error as e:
        return {"error": str(e)}


def trace_summary_handler(args: dict):
    """Get trace statistics."""
    project_id = args.get("project_id")

    try:
        conn = _get_db()
        where = "WHERE project_id = ?" if project_id else ""
        params = (project_id,) if project_id else ()

        stats = {
            "total_spans": conn.execute(
                f"SELECT COUNT(*) FROM otel_spans {where}", params
            ).fetchone()[0],
            "total_traces": conn.execute(
                f"SELECT COUNT(DISTINCT trace_id) FROM otel_spans {where}", params
            ).fetchone()[0],
            "mcp_tool_calls": conn.execute(
                f"SELECT COUNT(*) FROM otel_spans {where} {'AND' if where else 'WHERE'} name = 'mcp.tool_call'",
                params,
            ).fetchone()[0],
            "error_spans": conn.execute(
                f"SELECT COUNT(*) FROM otel_spans {where} {'AND' if where else 'WHERE'} status_code = 'ERROR'",
                params,
            ).fetchone()[0],
        }

        avg = conn.execute(
            f"SELECT AVG(duration_ms) FROM otel_spans {where}", params
        ).fetchone()[0]
        stats["avg_duration_ms"] = round(avg, 2) if avg else 0

        conn.close()
        return stats
    except sqlite3.Error as e:
        return {"error": str(e)}


def prov_lineage_handler(args: dict):
    """Query provenance lineage."""
    entity_id = args.get("entity_id")
    if not entity_id:
        return {"error": "entity_id required"}

    direction = args.get("direction", "backward")
    max_depth = min(int(args.get("max_depth", 50)), 100)

    try:
        from tools.observability.provenance.prov_recorder import ProvRecorder
        recorder = ProvRecorder(db_path=DB_PATH)
        lineage = recorder.get_lineage(entity_id, direction=direction, max_depth=max_depth)
        return {"entity_id": entity_id, "direction": direction, "lineage": lineage}
    except Exception as e:
        return {"error": str(e)}


def prov_export_handler(args: dict):
    """Export provenance as PROV-JSON."""
    project_id = args.get("project_id")

    try:
        from tools.observability.provenance.prov_recorder import ProvRecorder
        recorder = ProvRecorder(db_path=DB_PATH, project_id=project_id)
        prov_json = recorder.export_prov_json(project_id=project_id)
        _audit("prov.entity_created", "icdev-observability", "Exported PROV-JSON", project_id)
        return prov_json
    except Exception as e:
        return {"error": str(e)}


def shap_analyze_handler(args: dict):
    """Run AgentSHAP tool attribution."""
    trace_id = args.get("trace_id")
    if not trace_id:
        return {"error": "trace_id required"}

    iterations = min(int(args.get("iterations", 1000)), 5000)

    try:
        from tools.observability.shap.agent_shap import AgentSHAP
        shap = AgentSHAP(db_path=DB_PATH)
        result = shap.analyze_trace(trace_id, iterations=iterations)
        _audit("shap.analysis_completed", "icdev-observability",
               f"SHAP analysis on trace {trace_id[:12]}", details={"trace_id": trace_id})
        return result
    except Exception as e:
        return {"error": str(e)}


def xai_assess_handler(args: dict):
    """Run XAI compliance assessment."""
    project_id = args.get("project_id")
    if not project_id:
        return {"error": "project_id required"}

    try:
        from tools.compliance.xai_assessor import XAIAssessor
        assessor = XAIAssessor(db_path=DB_PATH)
        project = {"id": project_id}
        results = assessor.get_automated_checks(project)

        satisfied = sum(1 for s in results.values() if s == "satisfied")
        total = len(results)
        coverage_pct = round((satisfied / total) * 100, 1) if total > 0 else 0

        _audit("xai.assessment_completed", "icdev-observability",
               f"XAI assessment: {coverage_pct}% coverage", project_id,
               details={"coverage_pct": coverage_pct})

        return {
            "project_id": project_id,
            "framework": "xai",
            "checks": results,
            "satisfied": satisfied,
            "total": total,
            "coverage_pct": coverage_pct,
        }
    except Exception as e:
        return {"error": str(e)}


# ── Resource handlers ────────────────────────────────────────────

def config_resource_handler(uri: str):
    """Return current observability config."""
    import yaml
    config_path = BASE_DIR / "args" / "observability_tracing_config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            config = yaml.safe_load(f)
        return config
    return {"error": "Config not found"}


def stats_resource_handler(uri: str):
    """Return live observability statistics."""
    try:
        conn = _get_db()
        stats = {
            "total_spans": conn.execute("SELECT COUNT(*) FROM otel_spans").fetchone()[0],
            "total_traces": conn.execute("SELECT COUNT(DISTINCT trace_id) FROM otel_spans").fetchone()[0],
            "prov_entities": conn.execute("SELECT COUNT(*) FROM prov_entities").fetchone()[0],
            "prov_activities": conn.execute("SELECT COUNT(*) FROM prov_activities").fetchone()[0],
            "prov_relations": conn.execute("SELECT COUNT(*) FROM prov_relations").fetchone()[0],
            "shap_analyses": conn.execute("SELECT COUNT(DISTINCT trace_id) FROM shap_attributions").fetchone()[0],
        }
        conn.close()
        return stats
    except sqlite3.Error:
        return {"error": "Database not available"}


# ── Server setup ─────────────────────────────────────────────────

server = MCPServer(name="icdev-observability", version="1.0.0")

# Tools
server.register_tool(
    name="trace_query",
    description="Query traces and spans from the distributed tracing system. Filter by trace_id, project_id, or span name.",
    input_schema={
        "type": "object",
        "properties": {
            "trace_id": {"type": "string", "description": "Specific trace ID to query"},
            "project_id": {"type": "string", "description": "Filter by project ID"},
            "name": {"type": "string", "description": "Filter by span name (e.g., 'mcp.tool_call')"},
            "limit": {"type": "integer", "description": "Max results (default 50, max 200)"},
        },
    },
    handler=trace_query_handler,
)

server.register_tool(
    name="trace_summary",
    description="Get aggregate trace statistics: total spans, traces, MCP tool calls, errors, average duration.",
    input_schema={
        "type": "object",
        "properties": {
            "project_id": {"type": "string", "description": "Filter by project ID"},
        },
    },
    handler=trace_summary_handler,
)

server.register_tool(
    name="prov_lineage",
    description="Query W3C PROV provenance lineage for an entity. Trace backward (what produced this?) or forward (what did this produce?).",
    input_schema={
        "type": "object",
        "properties": {
            "entity_id": {"type": "string", "description": "Entity ID to query lineage for"},
            "direction": {"type": "string", "enum": ["backward", "forward"], "description": "Lineage direction"},
            "max_depth": {"type": "integer", "description": "Max traversal depth (default 50)"},
        },
        "required": ["entity_id"],
    },
    handler=prov_lineage_handler,
)

server.register_tool(
    name="prov_export",
    description="Export provenance graph as W3C PROV-JSON format for interoperability.",
    input_schema={
        "type": "object",
        "properties": {
            "project_id": {"type": "string", "description": "Filter by project ID"},
        },
    },
    handler=prov_export_handler,
)

server.register_tool(
    name="shap_analyze",
    description="Run AgentSHAP Monte Carlo Shapley value analysis on a trace to determine tool importance attribution.",
    input_schema={
        "type": "object",
        "properties": {
            "trace_id": {"type": "string", "description": "Trace ID to analyze"},
            "iterations": {"type": "integer", "description": "Monte Carlo iterations (default 1000, max 5000)"},
        },
        "required": ["trace_id"],
    },
    handler=shap_analyze_handler,
)

server.register_tool(
    name="xai_assess",
    description="Run XAI compliance assessment: 10 automated checks covering tracing, provenance, SHAP, and explainability against NIST AI RMF and DoD RAI requirements.",
    input_schema={
        "type": "object",
        "properties": {
            "project_id": {"type": "string", "description": "Project ID to assess"},
        },
        "required": ["project_id"],
    },
    handler=xai_assess_handler,
)

# Resources
server.register_resource(
    uri="observability://config",
    name="Observability Configuration",
    description="Current observability tracing configuration (backend, sampling, retention, content policy)",
    handler=config_resource_handler,
    mime_type="application/json",
)

server.register_resource(
    uri="observability://stats",
    name="Observability Statistics",
    description="Live trace, provenance, and SHAP statistics",
    handler=stats_resource_handler,
    mime_type="application/json",
)

if __name__ == "__main__":
    server.run()
