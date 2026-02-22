#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Innovation Engine MCP server exposing innovation tools.

Tools:
    scan_web         — Scan web sources for innovation signals
    score_signals    — Score new signals with 5-dimension weighted average
    triage_signals   — Run compliance-first triage on scored signals
    detect_trends    — Detect cross-signal trend patterns
    generate_solution — Generate solution spec from approved signal
    run_pipeline     — Run full innovation pipeline
    get_status       — Innovation engine status overview
    introspect       — Run introspective internal analysis
    competitive_scan — Run competitive intelligence scan
    standards_check  — Check standards body updates

Runs as MCP server over stdio with Content-Length framing.
"""

import os
import sys
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

sys.path.insert(0, str(BASE_DIR))
from tools.mcp.base_server import MCPServer


def _import_tool(module_path, func_name):
    """Dynamically import a function (graceful fallback)."""
    try:
        import importlib
        mod = importlib.import_module(module_path)
        return getattr(mod, func_name, None)
    except (ImportError, ModuleNotFoundError, AttributeError):
        return None


def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# =========================================================================
# TOOL HANDLERS
# =========================================================================
def handle_scan_web(args: dict) -> dict:
    """Scan web sources for innovation signals."""
    source = args.get("source")
    func = _import_tool("tools.innovation.web_scanner", "run_scan")
    if func:
        return func(source=source, db_path=str(DB_PATH))
    return {"error": "web_scanner not available"}


def handle_score_signals(args: dict) -> dict:
    """Score new or specific signals."""
    signal_id = args.get("signal_id")
    if signal_id:
        func = _import_tool("tools.innovation.signal_ranker", "score_signal")
        if func:
            return func(signal_id=signal_id, db_path=str(DB_PATH))
    else:
        func = _import_tool("tools.innovation.signal_ranker", "score_all_new")
        if func:
            return func(db_path=str(DB_PATH))
    return {"error": "signal_ranker not available"}


def handle_triage_signals(args: dict) -> dict:
    """Triage scored signals through compliance gates."""
    signal_id = args.get("signal_id")
    if signal_id:
        func = _import_tool("tools.innovation.triage_engine", "triage_signal")
        if func:
            return func(signal_id=signal_id, db_path=str(DB_PATH))
    else:
        func = _import_tool("tools.innovation.triage_engine", "triage_all_scored")
        if func:
            return func(db_path=str(DB_PATH))
    return {"error": "triage_engine not available"}


def handle_detect_trends(args: dict) -> dict:
    """Detect cross-signal trend patterns."""
    days = args.get("time_window_days", 30)
    min_signals = args.get("min_signals", 3)
    func = _import_tool("tools.innovation.trend_detector", "detect_trends")
    if func:
        return func(time_window_days=days, min_signals=min_signals, db_path=str(DB_PATH))
    return {"error": "trend_detector not available"}


def handle_generate_solution(args: dict) -> dict:
    """Generate solution spec from an approved signal."""
    signal_id = args.get("signal_id")
    if signal_id:
        func = _import_tool("tools.innovation.solution_generator", "generate_solution_spec")
        if func:
            return func(signal_id=signal_id, db_path=str(DB_PATH))
        return {"error": "solution_generator not available"}
    else:
        func = _import_tool("tools.innovation.solution_generator", "generate_all_approved")
        if func:
            return func(db_path=str(DB_PATH))
        return {"error": "solution_generator not available"}


def handle_run_pipeline(args: dict) -> dict:
    """Run the full innovation pipeline."""
    func = _import_tool("tools.innovation.innovation_manager", "run_full_pipeline")
    if func:
        return func(db_path=str(DB_PATH))
    return {"error": "innovation_manager not available"}


def handle_get_status(args: dict) -> dict:
    """Get innovation engine status overview."""
    func = _import_tool("tools.innovation.innovation_manager", "get_status")
    if func:
        return func(db_path=str(DB_PATH))
    return {"error": "innovation_manager not available"}


def handle_introspect(args: dict) -> dict:
    """Run introspective internal analysis."""
    analysis_type = args.get("type", "all")
    if analysis_type == "all":
        func = _import_tool("tools.innovation.introspective_analyzer", "analyze_all")
    else:
        func_name = f"analyze_{analysis_type}"
        func = _import_tool("tools.innovation.introspective_analyzer", func_name)
    if func:
        return func(db_path=str(DB_PATH))
    return {"error": f"introspective_analyzer.{analysis_type} not available"}


def handle_competitive_scan(args: dict) -> dict:
    """Run competitive intelligence scan."""
    competitor = args.get("competitor")
    if competitor:
        func = _import_tool("tools.innovation.competitive_intel", "scan_competitor")
        if func:
            return func(competitor_name=competitor, db_path=str(DB_PATH))
    else:
        func = _import_tool("tools.innovation.competitive_intel", "scan_all_competitors")
        if func:
            return func(db_path=str(DB_PATH))
    return {"error": "competitive_intel not available"}


def handle_standards_check(args: dict) -> dict:
    """Check standards body updates."""
    body = args.get("body")
    if body:
        func = _import_tool("tools.innovation.standards_monitor", f"check_{body}_updates")
        if func:
            config_func = _import_tool("tools.innovation.standards_monitor", "_load_config")
            config = config_func() if config_func else {}
            return func(config, db_path=str(DB_PATH))
    else:
        func = _import_tool("tools.innovation.standards_monitor", "check_all_bodies")
        if func:
            return func(db_path=str(DB_PATH))
    return {"error": "standards_monitor not available"}


# =========================================================================
# SERVER SETUP
# =========================================================================
if __name__ == "__main__":
    server = MCPServer(name="innovation-server")

    server.register_tool(
        name="scan_web",
        description="Scan web sources (GitHub, NVD, SO, HN) for innovation signals",
        handler=handle_scan_web,
        input_schema={
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "Specific source to scan (github, cve_databases, stackoverflow, hackernews) or omit for all",
                },
            },
        },
    )

    server.register_tool(
        name="score_signals",
        description="Score innovation signals with 5-dimension weighted average",
        handler=handle_score_signals,
        input_schema={
            "type": "object",
            "properties": {
                "signal_id": {
                    "type": "string",
                    "description": "Specific signal ID to score, or omit to score all new signals",
                },
            },
        },
    )

    server.register_tool(
        name="triage_signals",
        description="Run compliance-first 5-stage triage on scored signals",
        handler=handle_triage_signals,
        input_schema={
            "type": "object",
            "properties": {
                "signal_id": {
                    "type": "string",
                    "description": "Specific signal ID to triage, or omit to triage all scored",
                },
            },
        },
    )

    server.register_tool(
        name="detect_trends",
        description="Detect emerging trend patterns across innovation signals",
        handler=handle_detect_trends,
        input_schema={
            "type": "object",
            "properties": {
                "time_window_days": {"type": "integer", "default": 30},
                "min_signals": {"type": "integer", "default": 3},
            },
        },
    )

    server.register_tool(
        name="generate_solution",
        description="Generate solution specification from an approved innovation signal",
        handler=handle_generate_solution,
        input_schema={
            "type": "object",
            "properties": {
                "signal_id": {
                    "type": "string",
                    "description": "Signal ID to generate solution for, or omit for all approved",
                },
            },
        },
    )

    server.register_tool(
        name="run_pipeline",
        description="Run full innovation pipeline: discover → score → triage → generate",
        handler=handle_run_pipeline,
        input_schema={"type": "object", "properties": {}},
    )

    server.register_tool(
        name="get_status",
        description="Get innovation engine status overview with signal counts and health",
        handler=handle_get_status,
        input_schema={"type": "object", "properties": {}},
    )

    server.register_tool(
        name="introspect",
        description="Run introspective analysis on ICDEV internal telemetry",
        handler=handle_introspect,
        input_schema={
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "description": "Analysis type: all, failed_self_heals, gate_failures, unused_tools, slow_pipelines, nlq_gaps, knowledge_gaps",
                    "default": "all",
                },
            },
        },
    )

    server.register_tool(
        name="competitive_scan",
        description="Scan competitors for feature gaps and intelligence",
        handler=handle_competitive_scan,
        input_schema={
            "type": "object",
            "properties": {
                "competitor": {
                    "type": "string",
                    "description": "Specific competitor name or omit for all",
                },
            },
        },
    )

    server.register_tool(
        name="standards_check",
        description="Check standards bodies (NIST, CISA, DoD) for compliance updates",
        handler=handle_standards_check,
        input_schema={
            "type": "object",
            "properties": {
                "body": {
                    "type": "string",
                    "description": "Standards body: nist, cisa, dod, fedramp, iso, or omit for all",
                },
            },
        },
    )

    server.run()
