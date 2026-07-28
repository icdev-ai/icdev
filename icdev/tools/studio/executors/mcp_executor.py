"""MCP Tool Executor — generic Studio workflow step.

Dispatches any tool declared in ``tools/mcp/tool_registry.py::TOOL_REGISTRY``
so a workflow step can reach every registered tool without a hand-written
executor per integration.

Dispatch is **in-process**: the registry entry's ``module`` is imported with
``importlib`` and its ``handler`` called directly. No MCP server is started and
no stdio transport is opened — this is the same lazy-import path
``mcp/unified_server.py::_resolve_handler`` uses, minus the protocol layer.

Usage::

    python tools/studio/executors/mcp_executor.py --tool health_check --params '{}'

Contract (matches the runner's expectations, see workflow_runner._exec_step):
  stdout  = single-line JSON object
  exit 0  = handler ran and returned
  exit 1  = unknown tool, invalid params, or handler raised

One deliberate divergence from the MCP protocol layer: unified_server catches a
raising handler and returns ``{"error": ...}`` as a *successful* tool call. Here
that exits 1, because a step whose handler blew up must fail the run rather than
pass a success record with an error buried in the payload.

Authorization is **not** enforced here — that is dwo-mcp-02. Until it lands,
this executor is deliberately not registered as a workflow node type and is not
referenced by any template, so it is unreachable from a run.
"""
from __future__ import annotations

import argparse
import difflib
import importlib
import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Reserved key prefix for step results in run memory (dwo-mem-01).
MEMORY_KEY_PREFIX = "step:"

_MAX_SUGGESTIONS = 5


# ── Registry lookup ────────────────────────────────────────────────────────

def resolve_entry(tool: str) -> dict:
    """Return the TOOL_REGISTRY entry for ``tool``.

    Raises LookupError with the closest matching names when unknown.
    """
    from tools.mcp.tool_registry import RESOURCE_REGISTRY, TOOL_REGISTRY

    entry = TOOL_REGISTRY.get(tool)
    if entry:
        return entry

    if tool in RESOURCE_REGISTRY:
        raise LookupError(
            f"'{tool}' is an MCP resource, not a tool — this executor dispatches "
            f"TOOL_REGISTRY entries only"
        )

    raise LookupError(_unknown_tool_message(tool, list(TOOL_REGISTRY)))


def _unknown_tool_message(tool: str, names: list[str]) -> str:
    """Build an unknown-tool error listing the closest registry names."""
    close = difflib.get_close_matches(tool, names, n=_MAX_SUGGESTIONS, cutoff=0.6)
    if not close:
        lowered = tool.lower()
        close = [n for n in names if lowered in n.lower()][:_MAX_SUGGESTIONS]
    msg = f"Unknown MCP tool '{tool}' ({len(names)} tools registered)"
    if close:
        msg += ". Closest matches: " + ", ".join(close)
    return msg


# ── Param validation ───────────────────────────────────────────────────────

def parse_params(raw: str) -> dict:
    """Parse the --params JSON string into a dict."""
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--params is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(
            f"--params must be a JSON object, got {type(value).__name__}"
        )
    return value


def validate_params(params: dict, schema: dict) -> list[str]:
    """Return a list of human-readable schema violations (empty == valid)."""
    if not schema:
        return []
    try:
        import jsonschema  # noqa: PLC0415
    except ImportError:
        return []  # validation is best-effort; dispatch still guarded by the handler

    validator = jsonschema.Draft7Validator(schema)
    errors = []
    for err in sorted(validator.iter_errors(params), key=lambda e: list(e.path)):
        field = ".".join(str(p) for p in err.path) or "(root)"
        errors.append(f"{field}: {err.message}")
    return errors


# ── Run memory (dwo-mem-01) ────────────────────────────────────────────────

def write_run_memory(run_id: str, step_id: str, value: dict) -> tuple[bool, str]:
    """Persist a step result to run-scoped memory under ``step:<step_id>``.

    Soft dependency: run_memory is delivered by dwo-mem-01. Until it lands this
    is a no-op that reports why, rather than a second state store.
    """
    if not run_id:
        return False, "no --run-id supplied"
    try:
        from tools.studio import run_memory  # noqa: PLC0415
    except ImportError:
        return False, "tools.studio.run_memory not available (dwo-mem-01)"
    try:
        run_memory.set(run_id, f"{MEMORY_KEY_PREFIX}{step_id}", value)
        return True, ""
    except Exception as exc:  # noqa: BLE001 — memory must never fail the step
        return False, str(exc)


# ── Dispatch ───────────────────────────────────────────────────────────────

def _jsonable(value):
    """Coerce a handler return value into something json.dumps can emit."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return {"repr": repr(value)[:4000]}


def run(tool: str, params: dict, run_id: str = "", step_id: str = "") -> dict:
    """Look up, validate, and dispatch a registry tool. Returns the step result."""
    entry = resolve_entry(tool)

    violations = validate_params(params, entry.get("input_schema") or {})
    if violations:
        raise ValueError(
            f"Invalid params for '{tool}' — "
            + "; ".join(violations[:10])
            + (f" (+{len(violations) - 10} more)" if len(violations) > 10 else "")
        )

    module_path, handler_name = entry["module"], entry["handler"]
    try:
        mod = importlib.import_module(module_path)
        handler = getattr(mod, handler_name)
    except (ImportError, AttributeError) as exc:
        raise RuntimeError(
            f"Cannot load handler {module_path}.{handler_name} for '{tool}': {exc}"
        ) from exc

    start = time.monotonic()
    result = handler(params)
    duration_ms = int((time.monotonic() - start) * 1000)

    step_id = step_id or f"mcp-{tool}"
    payload = {
        "tool": tool,
        "category": entry.get("category", ""),
        "handler": f"{module_path}.{handler_name}",
        "duration_ms": duration_ms,
        "result": _jsonable(result),
    }
    written, reason = write_run_memory(run_id, step_id, payload)
    payload["step_id"] = step_id
    payload["memory_key"] = f"{MEMORY_KEY_PREFIX}{step_id}"
    payload["memory_written"] = written
    if not written:
        payload["memory_skipped"] = reason
    return payload


def main():
    parser = argparse.ArgumentParser(description="MCP Tool Executor (shared)")
    parser.add_argument("--tool", required=True, help="TOOL_REGISTRY tool name")
    parser.add_argument("--params", default="{}", help="Tool arguments as a JSON object")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--step-id", default="", help="Run-memory key suffix")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--json", action="store_true", help="Accepted for runner parity")
    args = parser.parse_args()

    try:
        params = parse_params(args.params)
        payload = run(args.tool, params, args.run_id, args.step_id)
        print(json.dumps({"status": "success", **payload}))
        sys.exit(0)
    except LookupError as exc:
        print(json.dumps({"status": "failed", "error_type": "unknown_tool",
                          "tool": args.tool, "error": str(exc)}))
        sys.exit(1)
    except ValueError as exc:
        print(json.dumps({"status": "failed", "error_type": "invalid_params",
                          "tool": args.tool, "error": str(exc)}))
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "failed", "error_type": "dispatch_error",
                          "tool": args.tool,
                          "error": f"{type(exc).__name__}: {exc}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
