#!/usr/bin/env python3
# CUI // SP-CTI
"""MBSE MCP server exposing SysML, DOORS NG, digital thread, and model-code tools.

Tools:
    import_xmi      - Import Cameo SysML XMI model file
    import_reqif    - Import DOORS NG ReqIF requirements file
    trace_forward   - Forward trace through digital thread
    trace_backward  - Backward trace through digital thread
    generate_code   - Generate code from SysML model elements
    detect_drift    - Check model-code sync status
    sync_model      - Trigger model-code synchronization
    des_assess      - Run DoDI 5000.87 DES compliance assessment
    thread_coverage - Digital thread coverage report
    model_snapshot  - Create PI model snapshot

Runs as an MCP server over stdio with Content-Length framing.
"""

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

def handle_import_xmi(args: dict) -> dict:
    """Import a Cameo SysML XMI model file."""
    import_xmi = _import_tool("tools.mbse.xmi_parser", "import_xmi")
    if not import_xmi:
        return {"error": "xmi_parser module not available", "status": "pending"}

    project_id = args.get("project_id")
    file_path = args.get("file_path")
    if not project_id or not file_path:
        return {"error": "project_id and file_path are required"}

    try:
        return import_xmi(project_id, file_path, db_path=str(DB_PATH))
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_import_reqif(args: dict) -> dict:
    """Import a DOORS NG ReqIF requirements file."""
    import_reqif = _import_tool("tools.mbse.reqif_parser", "import_reqif")
    if not import_reqif:
        return {"error": "reqif_parser module not available", "status": "pending"}

    project_id = args.get("project_id")
    file_path = args.get("file_path")
    if not project_id or not file_path:
        return {"error": "project_id and file_path are required"}

    try:
        return import_reqif(project_id, file_path, db_path=str(DB_PATH))
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_trace_forward(args: dict) -> dict:
    """Forward trace through the digital thread."""
    get_forward_trace = _import_tool("tools.mbse.digital_thread", "get_forward_trace")
    if not get_forward_trace:
        return {"error": "digital_thread module not available", "status": "pending"}

    project_id = args.get("project_id")
    source_type = args.get("source_type")
    source_id = args.get("source_id")
    if not all([project_id, source_type, source_id]):
        return {"error": "project_id, source_type, and source_id are required"}

    try:
        return get_forward_trace(project_id, source_type, source_id, db_path=str(DB_PATH))
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_trace_backward(args: dict) -> dict:
    """Backward trace through the digital thread."""
    get_backward_trace = _import_tool("tools.mbse.digital_thread", "get_backward_trace")
    if not get_backward_trace:
        return {"error": "digital_thread module not available", "status": "pending"}

    project_id = args.get("project_id")
    target_type = args.get("target_type")
    target_id = args.get("target_id")
    if not all([project_id, target_type, target_id]):
        return {"error": "project_id, target_type, and target_id are required"}

    try:
        return get_backward_trace(project_id, target_type, target_id, db_path=str(DB_PATH))
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_generate_code(args: dict) -> dict:
    """Generate code from SysML model elements."""
    generate_all = _import_tool("tools.mbse.model_code_generator", "generate_all")
    if not generate_all:
        return {"error": "model_code_generator module not available", "status": "pending"}

    project_id = args.get("project_id")
    language = args.get("language", "python")
    output_dir = args.get("output_dir")
    if not project_id or not output_dir:
        return {"error": "project_id and output_dir are required"}

    try:
        return generate_all(project_id, language, output_dir, db_path=str(DB_PATH))
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_detect_drift(args: dict) -> dict:
    """Check model-code synchronization status."""
    detect_drift = _import_tool("tools.mbse.sync_engine", "detect_drift")
    if not detect_drift:
        return {"error": "sync_engine module not available", "status": "pending"}

    project_id = args.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}

    try:
        return detect_drift(project_id, db_path=str(DB_PATH))
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_sync_model(args: dict) -> dict:
    """Trigger model-code synchronization."""
    direction = args.get("direction", "model_to_code")
    project_id = args.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}

    try:
        if direction == "model_to_code":
            sync_fn = _import_tool("tools.mbse.sync_engine", "sync_model_to_code")
            if not sync_fn:
                return {"error": "sync_engine module not available", "status": "pending"}
            return sync_fn(project_id, language=args.get("language", "python"), db_path=str(DB_PATH))
        elif direction == "code_to_model":
            sync_fn = _import_tool("tools.mbse.sync_engine", "sync_code_to_model")
            if not sync_fn:
                return {"error": "sync_engine module not available", "status": "pending"}
            output_path = args.get("output_path")
            if not output_path:
                return {"error": "output_path required for code_to_model sync"}
            return sync_fn(project_id, output_path, db_path=str(DB_PATH))
        else:
            return {"error": f"Invalid direction: {direction}. Use 'model_to_code' or 'code_to_model'"}
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_des_assess(args: dict) -> dict:
    """Run DoDI 5000.87 Digital Engineering Strategy compliance assessment."""
    run_des = _import_tool("tools.mbse.des_assessor", "run_des_assessment")
    if not run_des:
        return {"error": "des_assessor module not available", "status": "pending"}

    project_id = args.get("project_id")
    project_dir = args.get("project_dir")
    if not project_id or not project_dir:
        return {"error": "project_id and project_dir are required"}

    try:
        return run_des(project_id, project_dir, db_path=str(DB_PATH))
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_thread_coverage(args: dict) -> dict:
    """Digital thread coverage report."""
    compute_coverage = _import_tool("tools.mbse.digital_thread", "compute_coverage")
    if not compute_coverage:
        return {"error": "digital_thread module not available", "status": "pending"}

    project_id = args.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}

    try:
        return compute_coverage(project_id, db_path=str(DB_PATH))
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_model_snapshot(args: dict) -> dict:
    """Create a PI model snapshot."""
    create_snapshot = _import_tool("tools.mbse.pi_model_tracker", "create_pi_snapshot")
    if not create_snapshot:
        return {"error": "pi_model_tracker module not available", "status": "pending"}

    project_id = args.get("project_id")
    pi_number = args.get("pi_number")
    snapshot_type = args.get("snapshot_type", "manual")
    notes = args.get("notes")
    if not project_id:
        return {"error": "project_id is required"}

    try:
        return create_snapshot(project_id, pi_number, snapshot_type, notes, db_path=str(DB_PATH))
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

def create_server() -> MCPServer:
    """Create and configure the MBSE MCP server."""
    server = MCPServer(name="icdev-mbse", version="1.0.0")

    server.register_tool(
        name="import_xmi",
        description="Import a Cameo SysML v1.6 XMI model file into the ICDEV database",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ICDEV project ID"},
                "file_path": {"type": "string", "description": "Path to the XMI file exported from Cameo"},
            },
            "required": ["project_id", "file_path"],
        },
        handler=handle_import_xmi,
    )

    server.register_tool(
        name="import_reqif",
        description="Import a DOORS NG ReqIF 1.2 requirements file into the ICDEV database",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ICDEV project ID"},
                "file_path": {"type": "string", "description": "Path to the ReqIF file exported from DOORS NG"},
            },
            "required": ["project_id", "file_path"],
        },
        handler=handle_import_reqif,
    )

    server.register_tool(
        name="trace_forward",
        description="Trace forward through the digital thread from a source element (requirement → model → code → test → control)",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ICDEV project ID"},
                "source_type": {"type": "string", "enum": ["doors_requirement", "sysml_element", "code_module", "test_file", "nist_control"]},
                "source_id": {"type": "string", "description": "ID of the source element"},
            },
            "required": ["project_id", "source_type", "source_id"],
        },
        handler=handle_trace_forward,
    )

    server.register_tool(
        name="trace_backward",
        description="Trace backward through the digital thread from a target element (control → test → code → model → requirement)",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ICDEV project ID"},
                "target_type": {"type": "string", "enum": ["doors_requirement", "sysml_element", "code_module", "test_file", "nist_control"]},
                "target_id": {"type": "string", "description": "ID of the target element"},
            },
            "required": ["project_id", "target_type", "target_id"],
        },
        handler=handle_trace_backward,
    )

    server.register_tool(
        name="generate_code",
        description="Generate code scaffolding from SysML model elements (blocks → classes, activities → functions)",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ICDEV project ID"},
                "language": {"type": "string", "default": "python", "enum": ["python", "java", "go", "rust", "csharp", "typescript"]},
                "output_dir": {"type": "string", "description": "Output directory for generated code"},
            },
            "required": ["project_id", "output_dir"],
        },
        handler=handle_generate_code,
    )

    server.register_tool(
        name="detect_drift",
        description="Check synchronization status between SysML model and generated code files",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ICDEV project ID"},
            },
            "required": ["project_id"],
        },
        handler=handle_detect_drift,
    )

    server.register_tool(
        name="sync_model",
        description="Trigger model-code synchronization in either direction",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ICDEV project ID"},
                "direction": {"type": "string", "default": "model_to_code", "enum": ["model_to_code", "code_to_model"]},
                "language": {"type": "string", "default": "python"},
                "output_path": {"type": "string", "description": "Output XMI path (for code_to_model)"},
            },
            "required": ["project_id"],
        },
        handler=handle_sync_model,
    )

    server.register_tool(
        name="des_assess",
        description="Run DoDI 5000.87 Digital Engineering Strategy compliance assessment",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ICDEV project ID"},
                "project_dir": {"type": "string", "description": "Project directory path"},
            },
            "required": ["project_id", "project_dir"],
        },
        handler=handle_des_assess,
    )

    server.register_tool(
        name="thread_coverage",
        description="Compute digital thread coverage metrics (requirements → model → code → test → control)",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ICDEV project ID"},
            },
            "required": ["project_id"],
        },
        handler=handle_thread_coverage,
    )

    server.register_tool(
        name="model_snapshot",
        description="Create a PI-cadenced model snapshot for SAFe traceability",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ICDEV project ID"},
                "pi_number": {"type": "string", "description": "PI identifier (e.g., PI-25.1)"},
                "snapshot_type": {"type": "string", "default": "manual", "enum": ["pi_start", "pi_end", "baseline", "milestone", "manual"]},
                "notes": {"type": "string", "description": "Optional notes for this snapshot"},
            },
            "required": ["project_id"],
        },
        handler=handle_model_snapshot,
    )

    return server


def main():
    """Run the MBSE MCP server."""
    server = create_server()
    server.run()


if __name__ == "__main__":
    main()
# CUI // SP-CTI
