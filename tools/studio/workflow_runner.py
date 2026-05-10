"""ICDEV™ Studio — Per-Run SSE Workflow Execution Engine.

Wraps workflow_composer execution logic to emit per-step events to a
per-run queue instead of returning all results at the end. Each run_id
gets its own queue so multiple concurrent runs never mix events.

Architecture Decision D343+: per-run SSE (not global broadcast) keeps
multi-user and multi-tab runs isolated.
"""
# CUI // SP-CTI

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from graphlib import CycleError, TopologicalSorter
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402

# ── Per-run SSE queues ─────────────────────────────────────
# run_id → queue.Queue[dict]
_run_queues: dict[str, queue.Queue] = {}
_run_queues_lock = threading.Lock()


# ── DAG helpers ────────────────────────────────────────────

def _resolve_dag(steps: list) -> list:
    graph: dict[str, set] = {}
    for step in steps:
        graph[step["id"]] = set(step.get("depends_on", []) or [])
    sorter = TopologicalSorter(graph)
    return list(sorter.static_order())


def _build_command(step: dict, project_id: str) -> list:
    tool_path = step.get("tool", "")
    if not tool_path:
        return []
    cmd = [sys.executable, str(_ROOT / tool_path)]
    step_args = dict(step.get("args", {}) or {})
    if step.get("inject_project_id", True):
        cmd.extend(["--project-id", project_id])
    if step.get("json_output", True):
        cmd.append("--json")
    for key, value in step_args.items():
        if isinstance(value, bool) and value:
            cmd.append(f"--{key}")
        elif not isinstance(value, bool):
            cmd.extend([f"--{key}", str(value)])
    return cmd


# ── Step execution ─────────────────────────────────────────

def _exec_step(step: dict, project_id: str) -> dict:
    result: dict = {
        "step_id": step["id"],
        "step_name": step.get("name", step["id"]),
        "tool": step.get("tool", ""),
        "status": "pending",
        "stdout": None,
        "stderr": None,
        "exit_code": None,
        "duration_ms": 0,
    }

    node_type = step.get("node_type", "tool")
    if node_type in ("human", "approval"):
        result["status"] = "skipped"
        result["stderr"] = f"Human/approval gate — skipped in automated run"
        return result

    cmd = _build_command(step, project_id)
    if not cmd:
        result["status"] = "skipped"
        result["stderr"] = "No tool path configured"
        return result

    full_path = _ROOT / step.get("tool", "")
    if not full_path.exists():
        result["status"] = "skipped"
        result["stderr"] = f"Tool not found: {step.get('tool')}"
        return result

    timeout = step.get("timeout", 300)
    start = time.monotonic()
    try:
        _env = os.environ.copy()
        _env["PYTHONPATH"] = str(_ROOT) + os.pathsep + _env.get("PYTHONPATH", "")
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            cwd=str(_ROOT),
            env=_env,
        )
        result["duration_ms"] = int((time.monotonic() - start) * 1000)
        result["exit_code"] = proc.returncode
        result["stdout"] = proc.stdout.strip()[:4000] if proc.stdout else None
        result["stderr"] = proc.stderr.strip()[:2000] if proc.stderr else None
        result["status"] = "success" if proc.returncode == 0 else "failed"
    except subprocess.TimeoutExpired:
        result["status"] = "timeout"
        result["stderr"] = f"Timed out after {timeout}s"
        result["duration_ms"] = int((time.monotonic() - start) * 1000)
    except Exception as exc:
        result["status"] = "failed"
        result["stderr"] = str(exc)
        result["duration_ms"] = int((time.monotonic() - start) * 1000)

    return result


# ── DB helpers ─────────────────────────────────────────────

def _push(run_queue: queue.Queue, event: dict) -> None:
    try:
        run_queue.put_nowait(event)
    except queue.Full:
        pass


def _create_run_record(run_id: str, workflow_id: str, workflow_name: str, project_id: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO studio_workflow_runs
               (run_id, workflow_id, workflow_name, status, started_at, project_id)
               VALUES (?, ?, ?, 'pending', ?, ?)""",
            (run_id, workflow_id, workflow_name, datetime.now(timezone.utc).isoformat(), project_id),
        )
        conn.commit()
    finally:
        conn.close()


def _update_run_status(run_id: str, status: str, summary_json: str | None = None) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """UPDATE studio_workflow_runs
               SET status = ?, completed_at = ?, summary_json = ?
               WHERE run_id = ?""",
            (status, datetime.now(timezone.utc).isoformat(), summary_json, run_id),
        )
        conn.commit()
    finally:
        conn.close()


def _create_step_record(run_id: str, step_id: str, step_name: str, tool: str) -> str:
    step_run_id = f"sr-{uuid.uuid4().hex[:12]}"
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO studio_workflow_run_steps
               (step_run_id, run_id, step_id, step_name, tool, status, started_at)
               VALUES (?, ?, ?, ?, ?, 'running', ?)""",
            (step_run_id, run_id, step_id, step_name, tool, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
    return step_run_id


def _update_step_record(step_run_id: str, result: dict) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """UPDATE studio_workflow_run_steps
               SET status = ?, exit_code = ?, stdout = ?, stderr = ?,
                   duration_ms = ?, completed_at = ?
               WHERE step_run_id = ?""",
            (
                result["status"],
                result.get("exit_code"),
                result.get("stdout"),
                result.get("stderr"),
                result.get("duration_ms", 0),
                datetime.now(timezone.utc).isoformat(),
                step_run_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ── Worker thread ──────────────────────────────────────────

def _worker(run_id: str, workflow_id: str, wf: dict, project_id: str, run_queue: queue.Queue) -> None:
    try:
        template_yaml = wf.get("template_yaml", "")
        data = yaml.safe_load(template_yaml)
        steps = data.get("steps", [])

        if not steps:
            _update_run_status(run_id, "failed", json.dumps({"error": "No steps in workflow"}))
            _push(run_queue, {"type": "error", "run_id": run_id, "message": "No steps in workflow"})
            return

        try:
            order = _resolve_dag(steps)
        except CycleError as exc:
            msg = f"Circular dependency detected: {exc}"
            _update_run_status(run_id, "failed", json.dumps({"error": msg}))
            _push(run_queue, {"type": "error", "run_id": run_id, "message": msg})
            return

        step_map = {s["id"]: s for s in steps}
        ordered_steps = [step_map[sid] for sid in order if sid in step_map]

        _update_run_status(run_id, "running")
        _push(run_queue, {
            "type": "run_started",
            "run_id": run_id,
            "total_steps": len(ordered_steps),
            "steps": [{"id": s["id"], "name": s.get("name", s["id"])} for s in ordered_steps],
        })

        results: list[dict] = []
        overall_ok = True

        for i, step in enumerate(ordered_steps):
            _push(run_queue, {
                "type": "step_started",
                "run_id": run_id,
                "step_id": step["id"],
                "step_name": step.get("name", step["id"]),
                "index": i,
                "total": len(ordered_steps),
            })

            step_run_id = _create_step_record(
                run_id, step["id"], step.get("name", step["id"]), step.get("tool", "")
            )
            result = _exec_step(step, project_id)
            _update_step_record(step_run_id, result)
            results.append(result)

            if result["status"] in ("failed", "timeout") and step.get("required", True):
                overall_ok = False

            _push(run_queue, {
                "type": "step_done",
                "run_id": run_id,
                "step_id": step["id"],
                "step_name": step.get("name", step["id"]),
                "status": result["status"],
                "duration_ms": result.get("duration_ms", 0),
                "output_preview": (result.get("stdout") or "")[:500],
                "error": result.get("stderr"),
                "index": i,
                "total": len(ordered_steps),
            })

        summary = {
            "total": len(results),
            "success": sum(1 for r in results if r["status"] == "success"),
            "failed": sum(1 for r in results if r["status"] in ("failed", "timeout")),
            "skipped": sum(1 for r in results if r["status"] in ("skipped",)),
        }
        if not overall_ok:
            overall = "failed"
        elif summary["success"] == 0 and summary["skipped"] > 0:
            overall = "warning"
        else:
            overall = "success"
        _update_run_status(run_id, overall, json.dumps(summary))
        _push(run_queue, {
            "type": "run_complete",
            "run_id": run_id,
            "status": overall,
            "summary": summary,
        })

    except Exception as exc:
        _update_run_status(run_id, "failed")
        _push(run_queue, {"type": "error", "run_id": run_id, "message": str(exc)})
    finally:
        # Keep queue alive 10 min for late-connecting clients, then clean up
        def _cleanup() -> None:
            time.sleep(600)
            with _run_queues_lock:
                _run_queues.pop(run_id, None)

        threading.Thread(target=_cleanup, daemon=True).start()


# ── Public API ─────────────────────────────────────────────

def start_run(workflow_id: str, project_id: str = "default") -> str:
    """Load a studio workflow from DB, spawn execution thread, return run_id."""
    from tools.studio.workflow_editor import get_workflow  # noqa: PLC0415

    wf = get_workflow(workflow_id)
    if not wf:
        raise ValueError(f"Workflow not found: {workflow_id}")

    run_id = f"run-{uuid.uuid4().hex[:12]}"
    run_queue: queue.Queue = queue.Queue(maxsize=500)

    with _run_queues_lock:
        _run_queues[run_id] = run_queue

    _create_run_record(run_id, workflow_id, wf.get("name", workflow_id), project_id)

    t = threading.Thread(
        target=_worker,
        args=(run_id, workflow_id, wf, project_id, run_queue),
        daemon=True,
    )
    t.start()
    return run_id


def stream_run(run_id: str):
    """Generator yielding SSE-formatted lines for a specific run.

    Yields ``data: <json>\\n\\n`` lines until the run completes or errors.
    Sends heartbeats every 15 s to keep the connection alive.
    """
    with _run_queues_lock:
        run_queue = _run_queues.get(run_id)

    if run_queue is None:
        yield f"data: {json.dumps({'type': 'error', 'message': 'Run stream not found or expired'})}\n\n"
        return

    while True:
        try:
            event = run_queue.get(timeout=15)
            yield f"data: {json.dumps(event)}\n\n"
            if event.get("type") in ("run_complete", "error"):
                break
        except queue.Empty:
            yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"


def list_runs(workflow_id: str | None = None, limit: int = 50) -> list:
    conn = get_connection()
    try:
        if workflow_id:
            rows = conn.execute(
                "SELECT * FROM studio_workflow_runs WHERE workflow_id = ? ORDER BY started_at DESC LIMIT ?",
                (workflow_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM studio_workflow_runs ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_run(run_id: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM studio_workflow_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_run_steps(run_id: str) -> list:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM studio_workflow_run_steps WHERE run_id = ? ORDER BY started_at ASC",
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Code generation ────────────────────────────────────────

def generate_python_script(workflow_id: str) -> str:
    """Generate a standalone, runnable Python script from a saved workflow."""
    from tools.studio.workflow_editor import get_workflow  # noqa: PLC0415

    wf = get_workflow(workflow_id)
    if not wf:
        raise ValueError(f"Workflow not found: {workflow_id}")

    data = yaml.safe_load(wf.get("template_yaml", ""))
    steps = data.get("steps", [])
    if not steps:
        raise ValueError("Workflow has no steps")

    graph = {s["id"]: set(s.get("depends_on", []) or []) for s in steps}
    order = list(TopologicalSorter(graph).static_order())
    step_map = {s["id"]: s for s in steps}
    ordered = [step_map[sid] for sid in order if sid in step_map]

    now = datetime.now(timezone.utc).isoformat()
    lines = [
        "#!/usr/bin/env python3",
        f'"""Generated from ICDEV™ Studio Workflow: {wf["name"]}',
        f'Description: {data.get("description", "")}',
        f"Generated: {now}",
        '"""',
        "",
        "import json, subprocess, sys, time",
        "from pathlib import Path",
        "",
        "BASE_DIR = Path(__file__).resolve().parent",
        "",
        "",
        "def run_step(name, cmd, timeout=300):",
        '    print(f"  [{name}]...", end="", flush=True)',
        "    start = time.monotonic()",
        "    try:",
        "        proc = subprocess.run(cmd, capture_output=True, text=True,",
        "                              timeout=timeout, stdin=subprocess.DEVNULL,",
        "                              cwd=str(BASE_DIR))",
        "        ms = int((time.monotonic() - start) * 1000)",
        '        ok = proc.returncode == 0',
        '        print(f" {"PASS" if ok else "FAIL"} ({ms}ms)")',
        "        if not ok:",
        '            print(f"    stderr: {proc.stderr.strip()[:500]}")',
        "        return ok, proc.stdout, proc.stderr",
        "    except subprocess.TimeoutExpired:",
        '        print(" TIMEOUT")',
        '        return False, "", f"Timed out after {timeout}s"',
        "",
        "",
        "def main():",
        f'    print("Running: {wf["name"]}")',
        "    results = {}",
        "",
    ]

    for step in ordered:
        tool = step.get("tool", "")
        sid = step["id"]
        sname = step.get("name", sid).replace('"', '\\"')
        timeout = step.get("timeout", 300)
        step_args = dict(step.get("args", {}) or {})
        node_type = step.get("node_type", "tool")

        if node_type in ("human", "approval"):
            lines.append(f"    # {sname} — human/approval gate, skipped in automated run")
            lines.append(f"    results[{sid!r}] = True")
            lines.append("")
            continue

        deps = step.get("depends_on", []) or []
        if deps:
            lines.append(f"    # Step: {sname}")
            lines.append(f"    if not all(results.get(d) for d in {deps!r}):")
            lines.append(f'        print("  [{sname}] SKIPPED (dependency failed)")')
            lines.append(f"        results[{sid!r}] = False")
        else:
            cmd_parts = [f'sys.executable, str(BASE_DIR / "{tool}")', '"--json"']
            for k, v in step_args.items():
                if isinstance(v, bool) and v:
                    cmd_parts.append(f'"--{k}"')
                elif not isinstance(v, bool):
                    cmd_parts.append(f'"--{k}", "{v}"')
            cmd_str = "[" + ", ".join(cmd_parts) + "]"
            lines.append(f"    # Step: {sname}")
            lines.append(f"    ok, _, _ = run_step({sname!r}, {cmd_str}, timeout={timeout})")
            lines.append(f"    results[{sid!r}] = ok")
        lines.append("")

    lines += [
        "    passed = sum(1 for v in results.values() if v)",
        "    failed = len(results) - passed",
        '    print(f"\\nDone: {passed} passed, {failed} failed")',
        "    sys.exit(0 if failed == 0 else 1)",
        "",
        "",
        'if __name__ == "__main__":',
        "    main()",
    ]

    return "\n".join(lines)
