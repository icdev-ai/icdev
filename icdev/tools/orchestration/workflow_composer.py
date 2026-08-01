#!/usr/bin/env python3
# CUI // SP-CTI
"""Cross-Phase Workflow Composer.

Declarative workflow engine that composes ICDEV™ tools into reusable DAG-based
workflows. Templates define tool sequences with dependencies; the engine
resolves execution order via topological sort and runs tools via subprocess.

Architecture Decisions:
  D26:  Declarative YAML templates (no code changes to add workflows).
  D40:  graphlib.TopologicalSorter (stdlib Python 3.9+, air-gap safe).
  D343: Workflow composer uses declarative YAML templates + DAG resolution.

Usage:
  python tools/orchestration/workflow_composer.py --list --json
  python tools/orchestration/workflow_composer.py --template ato_acceleration --project-id proj-test --dry-run --json
  python tools/orchestration/workflow_composer.py --template security_hardening --project-id proj-test --json
  python tools/orchestration/workflow_composer.py --template ato_acceleration --project-id proj-test --run-id run-1 --json
"""

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from graphlib import TopologicalSorter, CycleError
from pathlib import Path

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATE_DIR = BASE_DIR / "args" / "workflow_templates"

# ── MCP steps (dwo-mcp-03) ─────────────────────────────────
# A `node_type: mcp` step names a TOOL_REGISTRY tool in `mcp_tool` (with
# optional `mcp_params`) instead of a script path in `tool`. Every such step
# runs through this one executor — the same one Studio's workflow_runner
# dispatches to, so a template behaves identically headless and in the UI.
# tests/test_dwo_mcp_composer_parity.py asserts the two builders agree.
MCP_EXECUTOR = "tools/studio/executors/mcp_executor.py"


def _load_template(template_name: str) -> dict:
    """Load a workflow template from YAML.

    Args:
        template_name: Template name (without .yaml extension).

    Returns:
        Parsed template dict.

    Raises:
        FileNotFoundError: If template doesn't exist.
        ValueError: If template is invalid.
    """
    template_file = TEMPLATE_DIR / f"{template_name}.yaml"
    if not template_file.exists():
        raise FileNotFoundError(f"Template not found: {template_file}")

    with open(template_file, "r", encoding="utf-8") as f:
        template = yaml.safe_load(f)

    if not template or "steps" not in template:
        raise ValueError(f"Template {template_name} missing 'steps' key")

    return template


def _resolve_dag(steps: list) -> list:
    """Resolve step execution order via topological sort.

    Args:
        steps: List of step dicts with 'id' and optional 'depends_on'.

    Returns:
        Ordered list of step IDs respecting dependencies.

    Raises:
        CycleError: If circular dependencies detected.
    """
    graph = {}
    for step in steps:
        step_id = step["id"]
        deps = step.get("depends_on", [])
        graph[step_id] = set(deps)

    sorter = TopologicalSorter(graph)
    return list(sorter.static_order())


def _step_tool_path(step: dict) -> str:
    """Repo-relative script this step runs ('' when it declares none).

    An `mcp` node names a registry tool, not a script — every one of them runs
    through the shared executor, so the path is fixed rather than authored.
    """
    if step.get("node_type") == "mcp":
        return MCP_EXECUTOR
    return step.get("tool", "") or ""


def _mcp_params_json(step: dict) -> str:
    """Serialize a step's `mcp_params` for the executor's --params flag.

    A string is passed through untouched so a template may hand-author the JSON;
    anything else is dumped. The executor rejects non-object JSON either way.
    """
    params = step.get("mcp_params")
    if params is None:
        return "{}"
    if isinstance(params, str):
        return params.strip() or "{}"
    return json.dumps(params)


def _build_mcp_command(step: dict, project_id: str, run_id: str = "") -> list:
    """Command for a `node_type: mcp` step — dispatch via mcp_executor.py.

    Byte-identical to workflow_runner._build_mcp_command for the same step, so
    the headless composer and Studio run an mcp node exactly the same way. The
    step's `args` (and any composer overrides for them) are deliberately not
    forwarded: an MCP tool takes its arguments from `mcp_params` only.
    """
    mcp_tool = str(step.get("mcp_tool", "") or "").strip()
    if not mcp_tool:
        return []
    cmd = [
        sys.executable, str(BASE_DIR / MCP_EXECUTOR),
        "--tool", mcp_tool,
        "--params", _mcp_params_json(step),
    ]
    step_id = str(step.get("id", "") or "")
    if step_id:
        cmd.extend(["--step-id", step_id])
    if step.get("inject_project_id", True):
        cmd.extend(["--project-id", project_id])
    if run_id and step.get("inject_run_id", True):
        cmd.extend(["--run-id", run_id])
    if step.get("json_output", True):
        cmd.append("--json")
    return cmd


def _build_command(
    step: dict,
    project_id: str,
    overrides: dict = None,
    run_id: str = "",
) -> list:
    """Build subprocess command from step definition.

    Args:
        step: Step dict with 'tool' (Python module path) and optional 'args',
            or — for `node_type: mcp` — a registry tool name in 'mcp_tool'.
        project_id: Project ID to inject.
        overrides: Optional argument overrides (ignored for mcp steps).
        run_id: Optional run ID to inject as --run-id.

    Returns:
        Command list suitable for subprocess.run().
    """
    if step.get("node_type") == "mcp":
        return _build_mcp_command(step, project_id, run_id)

    tool_path = step.get("tool", "")
    if not tool_path:
        return []

    # Resolve tool path relative to project root
    full_path = BASE_DIR / tool_path
    cmd = [sys.executable, str(full_path)]

    # Add standard args
    step_args = step.get("args", {})
    if overrides:
        step_args.update(overrides)

    # Inject project_id if tool expects it
    if step.get("inject_project_id", True) and "--project-id" not in str(step_args):
        cmd.extend(["--project-id", project_id])

    # Inject run_id the same way workflow_runner does (dwo-mem-01 reads it)
    if run_id and step.get("inject_run_id", True):
        cmd.extend(["--run-id", run_id])

    # Add --json for structured output
    if step.get("json_output", True):
        cmd.append("--json")

    # Add step-specific args
    for key, value in step_args.items():
        if isinstance(value, bool) and value:
            cmd.append(f"--{key}")
        elif not isinstance(value, bool):
            cmd.extend([f"--{key}", str(value)])

    return cmd


def list_templates() -> list:
    """List all available workflow templates.

    Returns:
        List of template summaries with name, description, step count.
    """
    templates = []
    if not TEMPLATE_DIR.exists():
        return templates

    for f in sorted(TEMPLATE_DIR.glob("*.yaml")):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            if data and "steps" in data:
                templates.append(
                    {
                        "name": f.stem,
                        "description": data.get("description", ""),
                        "steps": len(data["steps"]),
                        "category": data.get("category", "general"),
                        "file": str(f),
                    }
                )
        except Exception:
            continue

    return templates


def compose_workflow(
    template_name: str,
    project_id: str,
    overrides: dict = None,
    run_id: str = "",
) -> dict:
    """Compose a workflow execution plan from a template.

    Args:
        template_name: Template name.
        project_id: Project ID for tool injection.
        overrides: Optional argument overrides per step.
        run_id: Optional run ID injected into each step as --run-id.

    Returns:
        Execution plan with ordered steps and commands.
    """
    template = _load_template(template_name)
    steps = template["steps"]

    # Resolve DAG order
    try:
        execution_order = _resolve_dag(steps)
    except CycleError as e:
        raise ValueError(f"Circular dependency in template {template_name}: {e}")

    # Build step lookup
    step_map = {s["id"]: s for s in steps}
    overrides = overrides or {}

    # Build execution plan
    plan_steps = []
    for step_id in execution_order:
        step = step_map[step_id]
        step_overrides = overrides.get(step_id, {})
        cmd = _build_command(step, project_id, step_overrides, run_id)

        plan_steps.append(
            {
                "id": step_id,
                "name": step.get("name", step_id),
                # An mcp node resolves to the shared executor, not to `tool` —
                # so the existence check below covers it like any other script.
                "tool": _step_tool_path(step),
                "node_type": step.get("node_type", "tool"),
                "depends_on": step.get("depends_on", []),
                "command": cmd,
                "description": step.get("description", ""),
                "required": step.get("required", True),
                "timeout_seconds": step.get("timeout", 300),
            }
        )

    return {
        "workflow_id": f"wf-{uuid.uuid4().hex[:8]}",
        "template": template_name,
        "project_id": project_id,
        "run_id": run_id,
        "description": template.get("description", ""),
        "category": template.get("category", "general"),
        "total_steps": len(plan_steps),
        "steps": plan_steps,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def execute_workflow(
    plan: dict,
    dry_run: bool = False,
) -> dict:
    """Execute a composed workflow plan.

    Args:
        plan: Execution plan from compose_workflow().
        dry_run: If True, print commands without executing.

    Returns:
        Execution results with per-step status.
    """
    run_id = plan.get("run_id", "")
    results = {
        "workflow_id": plan["workflow_id"],
        "template": plan["template"],
        "project_id": plan["project_id"],
        "run_id": run_id,
        "dry_run": dry_run,
        "steps": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    overall_success = True

    for step in plan["steps"]:
        step_result = {
            "id": step["id"],
            "name": step["name"],
            "command": " ".join(step["command"]),
            "status": "pending",
            "output": None,
            "error": None,
            "duration_ms": 0,
        }

        if dry_run:
            step_result["status"] = "dry_run"
            results["steps"].append(step_result)
            continue

        if not step["command"]:
            step_result["status"] = "skip"
            step_result["error"] = (
                "node_type: mcp step declares no 'mcp_tool'"
                if step.get("node_type") == "mcp"
                else "No tool path configured"
            )
            results["steps"].append(step_result)
            continue

        # Check tool file exists
        tool_path = step.get("tool", "")
        if tool_path:
            full_tool_path = BASE_DIR / tool_path
            if not full_tool_path.exists():
                step_result["status"] = "skip"
                step_result["error"] = f"Tool not found: {tool_path}"
                results["steps"].append(step_result)
                if step.get("required", True):
                    overall_success = False
                continue

        start = time.monotonic()
        try:
            env = os.environ.copy()
            env["PYTHONPATH"] = str(BASE_DIR) + os.pathsep + env.get("PYTHONPATH", "")
            # dwo-mem-01: the step reads/writes run-scoped memory through this id.
            if run_id:
                env["ICDEV_RUN_ID"] = run_id
            proc = subprocess.run(
                step["command"],
                capture_output=True,
                text=True,
                timeout=step.get("timeout_seconds", 300),
                stdin=subprocess.DEVNULL,
                cwd=str(BASE_DIR),
                env=env,
            )
            elapsed = int((time.monotonic() - start) * 1000)
            step_result["duration_ms"] = elapsed

            if proc.returncode == 0:
                step_result["status"] = "pass"
                # Try to parse JSON output
                try:
                    step_result["output"] = json.loads(proc.stdout)
                except (json.JSONDecodeError, ValueError):
                    step_result["output"] = proc.stdout.strip()[:2000] if proc.stdout else None
            else:
                step_result["status"] = "fail"
                step_result["error"] = proc.stderr.strip()[:2000] if proc.stderr else f"Exit code {proc.returncode}"
                if step.get("required", True):
                    overall_success = False

        except subprocess.TimeoutExpired:
            step_result["status"] = "timeout"
            step_result["error"] = f"Timed out after {step.get('timeout_seconds', 300)}s"
            if step.get("required", True):
                overall_success = False
        except Exception as e:
            step_result["status"] = "error"
            step_result["error"] = str(e)
            if step.get("required", True):
                overall_success = False

        results["steps"].append(step_result)

    results["finished_at"] = datetime.now(timezone.utc).isoformat()
    results["overall_status"] = "pass" if overall_success else "fail"
    results["passed"] = sum(1 for s in results["steps"] if s["status"] in ("pass", "dry_run"))
    results["failed"] = sum(1 for s in results["steps"] if s["status"] in ("fail", "timeout", "error"))
    results["skipped"] = sum(1 for s in results["steps"] if s["status"] == "skip")

    return results


def main():
    parser = argparse.ArgumentParser(description="Cross-Phase Workflow Composer (D343)")
    parser.add_argument("--template", help="Workflow template name")
    parser.add_argument("--project-id", help="Project ID", dest="project_id")
    parser.add_argument("--run-id", default="", dest="run_id", help="Run ID to inject into each step")
    parser.add_argument("--list", action="store_true", help="List available templates")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run", help="Preview commands without executing")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    args = parser.parse_args()

    if args.list:
        templates = list_templates()
        if args.json_output:
            print(json.dumps({"templates": templates, "count": len(templates)}, indent=2))
        else:
            print(f"\n=== Available Workflow Templates ({len(templates)}) ===")
            for t in templates:
                print(f"  {t['name']:30s} {t['steps']:3d} steps  {t['description']}")
        return

    if not args.template:
        parser.error("--template required (or use --list)")
    if not args.project_id:
        parser.error("--project-id required with --template")

    plan = compose_workflow(args.template, args.project_id, run_id=args.run_id)
    result = execute_workflow(plan, dry_run=args.dry_run)

    if args.json_output:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"\n=== Workflow: {result['template']} ===")
        print(f"  Status: {result['overall_status']}")
        print(f"  Steps: {result['passed']} passed, {result['failed']} failed, {result['skipped']} skipped")
        for step in result["steps"]:
            icon = {"pass": "+", "fail": "X", "skip": "-", "dry_run": "~", "timeout": "T", "error": "!"}
            print(f"  [{icon.get(step['status'], '?')}] {step['name']}: {step['status']} ({step['duration_ms']}ms)")


if __name__ == "__main__":
    main()
