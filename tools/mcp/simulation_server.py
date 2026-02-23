#!/usr/bin/env python3
# CUI // SP-CTI
"""Digital Program Twin Simulation MCP server for RICOAS Phase 3.

Tools:
    create_scenario       - Create a what-if scenario
    run_simulation        - Run 6-dimension simulation
    run_monte_carlo       - Monte Carlo schedule/cost/risk estimation
    generate_coas         - Generate 3 COAs (Speed/Balanced/Comprehensive)
    generate_alternative_coa - Generate alternative COA for RED requirement
    compare_coas          - Compare COAs across all dimensions
    select_coa            - Select a COA with rationale
    manage_scenarios      - List/fork/archive/export/summarize scenarios

Runs as an MCP server over stdio with Content-Length framing.
"""

import json
import os
import sys
import traceback
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

sys.path.insert(0, str(BASE_DIR))
from tools.mcp.base_server import MCPServer  # noqa: E402


# ---------------------------------------------------------------------------
# Lazy tool imports
# ---------------------------------------------------------------------------

def _import_tool(module_path, func_name):
    """Dynamically import a function from a module. Returns None if unavailable."""
    try:
        import importlib
        mod = importlib.import_module(module_path)
        return getattr(mod, func_name, None)
    except (ImportError, ModuleNotFoundError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def handle_create_scenario(args: dict) -> dict:
    """Create a what-if scenario for simulation."""
    create_scenario = _import_tool("tools.simulation.simulation_engine", "create_scenario")
    if not create_scenario:
        return {"error": "simulation_engine module not available", "status": "pending"}

    project_id = args.get("project_id")
    scenario_name = args.get("scenario_name")
    if not project_id or not scenario_name:
        return {"error": "project_id and scenario_name are required"}

    scenario_type = args.get("scenario_type", "what_if")
    modifications = args.get("modifications")
    base_session_id = args.get("base_session_id")

    # Parse modifications from JSON string if provided
    if modifications and isinstance(modifications, str):
        try:
            modifications = json.loads(modifications)
        except json.JSONDecodeError:
            return {"error": "modifications must be a valid JSON string"}

    try:
        return create_scenario(
            project_id=project_id,
            scenario_name=scenario_name,
            scenario_type=scenario_type,
            modifications=modifications,
            base_session_id=base_session_id,
            db_path=str(DB_PATH),
        )
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_run_simulation(args: dict) -> dict:
    """Run 6-dimension simulation on a scenario."""
    run_simulation = _import_tool("tools.simulation.simulation_engine", "run_simulation")
    if not run_simulation:
        return {"error": "simulation_engine module not available", "status": "pending"}

    scenario_id = args.get("scenario_id")
    if not scenario_id:
        return {"error": "scenario_id is required"}

    dimensions = args.get("dimensions", "all")

    try:
        return run_simulation(
            scenario_id=scenario_id,
            dimensions=dimensions,
            db_path=str(DB_PATH),
        )
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_run_monte_carlo(args: dict) -> dict:
    """Run Monte Carlo schedule/cost/risk estimation."""
    run_monte_carlo = _import_tool("tools.simulation.monte_carlo", "run_monte_carlo")
    if not run_monte_carlo:
        return {"error": "monte_carlo module not available", "status": "pending"}

    scenario_id = args.get("scenario_id")
    dimension = args.get("dimension")
    if not scenario_id or not dimension:
        return {"error": "scenario_id and dimension are required"}

    iterations = int(args.get("iterations", 10000))
    confidence_levels_str = args.get("confidence_levels", "0.10,0.50,0.80,0.90")

    # Parse confidence levels
    try:
        confidence_levels = [float(x.strip()) for x in confidence_levels_str.split(",")]
    except ValueError:
        return {"error": "confidence_levels must be comma-separated floats (e.g. '0.10,0.50,0.80,0.90')"}

    if iterations < 100:
        return {
            "warning": "Low iteration count may produce unreliable results. Recommend >= 1000.",
            "iterations_requested": iterations,
        }

    try:
        return run_monte_carlo(
            scenario_id=scenario_id,
            dimension=dimension,
            iterations=iterations,
            confidence_levels=confidence_levels,
            db_path=str(DB_PATH),
        )
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_generate_coas(args: dict) -> dict:
    """Generate 3 COAs (Speed/Balanced/Comprehensive)."""
    generate_3_coas = _import_tool("tools.simulation.coa_generator", "generate_3_coas")
    if not generate_3_coas:
        return {"error": "coa_generator module not available", "status": "pending"}

    session_id = args.get("session_id")
    if not session_id:
        return {"error": "session_id is required"}

    project_id = args.get("project_id")
    simulate = args.get("simulate", False)
    if isinstance(simulate, str):
        simulate = simulate.lower() in ("true", "1", "yes")

    try:
        return generate_3_coas(
            session_id=session_id,
            project_id=project_id,
            simulate=simulate,
            db_path=str(DB_PATH),
        )
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_generate_alternative_coa(args: dict) -> dict:
    """Generate alternative COA for a RED requirement."""
    generate_alternative_coa = _import_tool("tools.simulation.coa_generator", "generate_alternative_coa")
    if not generate_alternative_coa:
        return {"error": "coa_generator module not available", "status": "pending"}

    session_id = args.get("session_id")
    requirement_id = args.get("requirement_id")
    if not session_id or not requirement_id:
        return {"error": "session_id and requirement_id are required"}

    project_id = args.get("project_id")

    try:
        return generate_alternative_coa(
            session_id=session_id,
            requirement_id=requirement_id,
            project_id=project_id,
            db_path=str(DB_PATH),
        )
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_compare_coas(args: dict) -> dict:
    """Compare COAs across all simulation dimensions."""
    compare_coas = _import_tool("tools.simulation.coa_generator", "compare_coas")
    if not compare_coas:
        return {"error": "coa_generator module not available", "status": "pending"}

    session_id = args.get("session_id")
    if not session_id:
        return {"error": "session_id is required"}

    try:
        return compare_coas(session_id=session_id, db_path=str(DB_PATH))
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_select_coa(args: dict) -> dict:
    """Select a COA with rationale."""
    select_coa = _import_tool("tools.simulation.coa_generator", "select_coa")
    if not select_coa:
        return {"error": "coa_generator module not available", "status": "pending"}

    coa_id = args.get("coa_id")
    selected_by = args.get("selected_by")
    rationale = args.get("rationale")
    if not all([coa_id, selected_by, rationale]):
        return {"error": "coa_id, selected_by, and rationale are required"}

    try:
        return select_coa(
            coa_id=coa_id,
            selected_by=selected_by,
            rationale=rationale,
            db_path=str(DB_PATH),
        )
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_manage_scenarios(args: dict) -> dict:
    """List, fork, archive, export, or summarize scenarios."""
    action = args.get("action")
    if not action:
        return {"error": "action is required"}

    valid_actions = ("list", "fork", "archive", "export", "summary")
    if action not in valid_actions:
        return {"error": f"Invalid action: {action}. Must be one of {valid_actions}"}

    # Dispatch to appropriate scenario_manager function
    func_map = {
        "list": "list_scenarios",
        "fork": "fork_scenario",
        "archive": "archive_scenario",
        "export": "export_scenario",
        "summary": "scenario_summary",
    }

    func = _import_tool("tools.simulation.scenario_manager", func_map[action])
    if not func:
        return {"error": f"scenario_manager.{func_map[action]} not available", "status": "pending"}

    project_id = args.get("project_id")
    scenario_id = args.get("scenario_id")
    new_name = args.get("new_name")
    output_path = args.get("output_path")

    try:
        if action == "list":
            if not project_id:
                return {"error": "project_id is required for list action"}
            return func(project_id=project_id, db_path=str(DB_PATH))

        elif action == "fork":
            if not scenario_id or not new_name:
                return {"error": "scenario_id and new_name are required for fork action"}
            return func(scenario_id=scenario_id, new_name=new_name, db_path=str(DB_PATH))

        elif action == "archive":
            if not scenario_id:
                return {"error": "scenario_id is required for archive action"}
            return func(scenario_id=scenario_id, db_path=str(DB_PATH))

        elif action == "export":
            if not scenario_id:
                return {"error": "scenario_id is required for export action"}
            return func(scenario_id=scenario_id, output_path=output_path, db_path=str(DB_PATH))

        elif action == "summary":
            if not scenario_id:
                return {"error": "scenario_id is required for summary action"}
            return func(scenario_id=scenario_id, db_path=str(DB_PATH))

    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

def create_server() -> MCPServer:
    """Create and configure the Simulation MCP server."""
    server = MCPServer(name="icdev-simulation", version="1.0.0")

    server.register_tool(
        name="create_scenario",
        description="Create a what-if scenario for Digital Program Twin simulation",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ICDEV project ID"},
                "scenario_name": {"type": "string", "description": "Human-readable scenario name"},
                "scenario_type": {
                    "type": "string",
                    "default": "what_if",
                    "enum": ["what_if", "coa_comparison", "risk_analysis"],
                    "description": "Type of scenario",
                },
                "modifications": {
                    "type": "string",
                    "description": "JSON string describing modifications (add/remove requirements, architecture changes)",
                },
                "base_session_id": {
                    "type": "string",
                    "description": "RICOAS session ID to use as baseline (optional)",
                },
            },
            "required": ["project_id", "scenario_name"],
        },
        handler=handle_create_scenario,
    )

    server.register_tool(
        name="run_simulation",
        description="Run 6-dimension simulation (architecture, compliance, supply chain, schedule, cost, risk)",
        input_schema={
            "type": "object",
            "properties": {
                "scenario_id": {"type": "string", "description": "Scenario ID to simulate"},
                "dimensions": {
                    "type": "string",
                    "default": "all",
                    "description": "Comma-separated dimensions or 'all' (architecture,compliance,supply_chain,schedule,cost,risk)",
                },
            },
            "required": ["scenario_id"],
        },
        handler=handle_run_simulation,
    )

    server.register_tool(
        name="run_monte_carlo",
        description="Run Monte Carlo estimation for schedule, cost, or risk with PERT distributions",
        input_schema={
            "type": "object",
            "properties": {
                "scenario_id": {"type": "string", "description": "Scenario ID to estimate"},
                "dimension": {
                    "type": "string",
                    "enum": ["schedule", "cost", "risk"],
                    "description": "Dimension to estimate",
                },
                "iterations": {
                    "type": "integer",
                    "default": 10000,
                    "description": "Number of Monte Carlo iterations (recommend >= 1000)",
                },
                "confidence_levels": {
                    "type": "string",
                    "default": "0.10,0.50,0.80,0.90",
                    "description": "Comma-separated confidence levels (e.g. P10, P50, P80, P90)",
                },
            },
            "required": ["scenario_id", "dimension"],
        },
        handler=handle_run_monte_carlo,
    )

    server.register_tool(
        name="generate_coas",
        description="Generate 3 Courses of Action (Speed/Balanced/Comprehensive) for a RICOAS session",
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "RICOAS session ID"},
                "project_id": {"type": "string", "description": "ICDEV project ID (optional)"},
                "simulate": {
                    "type": "boolean",
                    "default": False,
                    "description": "Run 6-dimension simulation on each COA",
                },
            },
            "required": ["session_id"],
        },
        handler=handle_generate_coas,
    )

    server.register_tool(
        name="generate_alternative_coa",
        description="Generate an alternative COA for a RED-tier requirement that stays within ATO boundary",
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "RICOAS session ID"},
                "requirement_id": {"type": "string", "description": "ID of the RED requirement"},
                "project_id": {"type": "string", "description": "ICDEV project ID (optional)"},
            },
            "required": ["session_id", "requirement_id"],
        },
        handler=handle_generate_alternative_coa,
    )

    server.register_tool(
        name="compare_coas",
        description="Compare all COAs in a session across 6 simulation dimensions side-by-side",
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "RICOAS session ID"},
            },
            "required": ["session_id"],
        },
        handler=handle_compare_coas,
    )

    server.register_tool(
        name="select_coa",
        description="Select a COA and record the decision with rationale for audit trail",
        input_schema={
            "type": "object",
            "properties": {
                "coa_id": {"type": "string", "description": "ID of the selected COA"},
                "selected_by": {"type": "string", "description": "Who selected this COA (name or role)"},
                "rationale": {"type": "string", "description": "Rationale for selecting this COA"},
            },
            "required": ["coa_id", "selected_by", "rationale"],
        },
        handler=handle_select_coa,
    )

    server.register_tool(
        name="manage_scenarios",
        description="List, fork, archive, export, or summarize simulation scenarios",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ICDEV project ID (for list action)"},
                "scenario_id": {"type": "string", "description": "Scenario ID (for fork/archive/export/summary)"},
                "action": {
                    "type": "string",
                    "enum": ["list", "fork", "archive", "export", "summary"],
                    "description": "Action to perform",
                },
                "new_name": {"type": "string", "description": "New scenario name (for fork action)"},
                "output_path": {"type": "string", "description": "Output file path (for export action)"},
            },
            "required": ["action"],
        },
        handler=handle_manage_scenarios,
    )

    return server


def main():
    """Run the Simulation MCP server."""
    server = create_server()
    server.run()


if __name__ == "__main__":
    main()
# [TEMPLATE: CUI // SP-CTI]
