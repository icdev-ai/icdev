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
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger(__name__)

# ── Per-run SSE queues ─────────────────────────────────────
# run_id → queue.Queue[dict]
_run_queues: dict[str, queue.Queue] = {}
_run_queues_lock = threading.Lock()

# ── HITL approval state ────────────────────────────────────
# step_run_id → threading.Event (set when approved or rejected)
_approval_lock = threading.Lock()
_approval_events: dict[str, threading.Event] = {}
_approval_results: dict[str, str] = {}   # "approved" | "rejected"
_approval_reasons: dict[str, str] = {}   # free-text reason from approver


# Runs left mid-flight by a dead process are handled by
# reconcile_runs_on_boot() — called explicitly from the app startup path, NOT
# at import time. Importing this module must not write to the database.


# ── Gate deadlines ─────────────────────────────────────────

# A parked approval gate expires this long after the step started, unless the
# step declares `approval_timeout`. The window is anchored to the step's
# started_at in the DB, so it does not restart when the process does.
_GATE_DEFAULT_TIMEOUT = 86400


# ── Resume ─────────────────────────────────────────────────

# Resume continues the original run row rather than forking a linked one.
# The decision and its rejected alternative are recorded in
# docs/features/dwo-durable-workflow-orchestration.md.
RESUME_MODE = "in_place"

# Statuses a run can be resumed from. 'failed' is included: a run that died
# part-way is exactly the case resume exists for, and steps already recorded
# success/approved/skipped are replayed rather than re-executed.
RESUMABLE_RUN_STATUSES = ("pending", "running", "awaiting_approval", "failed")


def _parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp, returning None if unusable."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _gate_timeout_seconds(step: dict) -> int:
    """Approval window for a human step, in seconds."""
    try:
        return int(step.get("approval_timeout") or _GATE_DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        return _GATE_DEFAULT_TIMEOUT


def _gate_deadline(step: dict, started_at: str | None) -> float:
    """Absolute epoch deadline for a gate, anchored to the step's started_at.

    Falls back to "now + window" when started_at is missing or unparseable, so
    a malformed row degrades to the old behaviour rather than expiring instantly.
    """
    window = _gate_timeout_seconds(step)
    started = _parse_iso(started_at)
    if started is None:
        return time.time() + window
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    return time.time() + max(0.0, window - elapsed)


# ── MCP steps (dwo-mcp-03) ─────────────────────────────────

# A `node_type: mcp` step names a TOOL_REGISTRY tool in `mcp_tool` (with
# optional `mcp_params`) instead of a script path in `tool`. Every such step
# runs through this one executor, delivered by dwo-mcp-01.
MCP_EXECUTOR = "tools/studio/executors/mcp_executor.py"


# ── DAG helpers ────────────────────────────────────────────

def _resolve_dag(steps: list) -> list:
    graph: dict[str, set] = {}
    for step in steps:
        graph[step["id"]] = set(step.get("depends_on", []) or [])
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
    """Command for a `node_type: mcp` step — dispatch via mcp_executor.py."""
    mcp_tool = str(step.get("mcp_tool", "") or "").strip()
    if not mcp_tool:
        return []
    cmd = [
        sys.executable, str(_ROOT / MCP_EXECUTOR),
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


def _build_command(step: dict, project_id: str, run_id: str = "") -> list:
    if step.get("node_type") == "mcp":
        return _build_mcp_command(step, project_id, run_id)
    tool_path = step.get("tool", "")
    if not tool_path:
        return []
    cmd = [sys.executable, str(_ROOT / tool_path)]
    step_args = dict(step.get("args", {}) or {})
    if step.get("inject_project_id", True):
        cmd.extend(["--project-id", project_id])
    if run_id and step.get("inject_run_id", True):
        cmd.extend(["--run-id", run_id])
    if step.get("json_output", True):
        cmd.append("--json")
    for key, value in step_args.items():
        if isinstance(value, bool) and value:
            cmd.append(f"--{key}")
        elif not isinstance(value, bool):
            cmd.extend([f"--{key}", str(value)])
    return cmd


# ── Step execution ─────────────────────────────────────────

# A single backoff sleep is capped here so a typo in a template
# (`retry_backoff_seconds: 86400`) cannot wedge a worker thread for a day.
_MAX_RETRY_BACKOFF = 300.0

# Only these outcomes are worth re-running. A 'skipped' step has nothing to
# retry, and a human gate is decided by a person, not by re-execution.
_RETRYABLE_STATUSES = ("failed", "timeout")


def _retry_policy(step: dict) -> tuple[int, float]:
    """(retries, backoff_seconds) declared by a step.

    Both default to 0, so a template that declares neither behaves exactly as
    it did before per-step retries existed. Unparseable values degrade to the
    default rather than raising mid-run.
    """
    try:
        retries = max(0, int(step.get("retries", 0) or 0))
    except (TypeError, ValueError):
        retries = 0
    try:
        backoff = max(0.0, float(step.get("retry_backoff_seconds", 0) or 0))
    except (TypeError, ValueError):
        backoff = 0.0
    return retries, min(backoff, _MAX_RETRY_BACKOFF)


def _exec_step_with_retries(step: dict, project_id: str, run_id: str = "") -> dict:
    """Run a step, re-running it up to `retries` times while it fails.

    Backoff is linear (`backoff * attempt`) between attempts. The returned
    result carries `attempts` only when more than one was made, so an
    unretried step's payload is unchanged.
    """
    retries, backoff = _retry_policy(step)
    result = _exec_step(step, project_id, run_id)
    attempt = 0
    while attempt < retries and result["status"] in _RETRYABLE_STATUSES:
        attempt += 1
        if backoff:
            time.sleep(min(backoff * attempt, _MAX_RETRY_BACKOFF))
        result = _exec_step(step, project_id, run_id)
    if attempt:
        result["attempts"] = attempt + 1
    return result


def _exec_step(step: dict, project_id: str, run_id: str = "") -> dict:
    result: dict = {
        "step_id": step["id"],
        "step_name": step.get("name", step["id"]),
        "tool": _step_tool_path(step),
        "status": "pending",
        "stdout": None,
        "stderr": None,
        "exit_code": None,
        "duration_ms": 0,
    }

    node_type = step.get("node_type", "tool")
    if node_type in ("human", "approval"):
        result["status"] = "awaiting_approval"
        result["stderr"] = "Awaiting human approval — use the workflow Details modal to approve or reject"
        return result

    cmd = _build_command(step, project_id, run_id)
    if not cmd:
        result["status"] = "skipped"
        result["stderr"] = (
            "node_type: mcp step declares no 'mcp_tool'"
            if node_type == "mcp"
            else "No tool path configured"
        )
        return result

    tool_path = _step_tool_path(step)
    if not (_ROOT / tool_path).exists():
        result["status"] = "skipped"
        result["stderr"] = f"Tool not found: {tool_path}"
        return result

    timeout = step.get("timeout", 300)
    start = time.monotonic()
    try:
        _env = os.environ.copy()
        _env["PYTHONPATH"] = str(_ROOT) + os.pathsep + _env.get("PYTHONPATH", "")
        # dwo-mem-01: the step reads/writes run-scoped memory through this id.
        if run_id:
            _env["ICDEV_RUN_ID"] = run_id
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
        result["stdout"] = proc.stdout.strip()[:32000] if proc.stdout else None
        result["stderr"] = proc.stderr.strip()[:4000] if proc.stderr else None
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
               VALUES (%s, %s, %s, 'pending', %s, %s)""",
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
               SET status = %s, completed_at = %s, summary_json = %s
               WHERE run_id = %s""",
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
               VALUES (%s, %s, %s, %s, %s, 'running', %s)""",
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
               SET status = %s, exit_code = %s, stdout = %s, stderr = %s,
                   duration_ms = %s, completed_at = %s
               WHERE step_run_id = %s""",
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


# ── HITL helpers ───────────────────────────────────────────

def _notify_approval_gate(run_id: str, step_run_id: str, step_name: str, role: str) -> None:
    try:
        from tools.notifications.adapters.telegram import send  # noqa: PLC0415
        send(
            "Approval Required",
            f"Workflow run <code>{run_id}</code> is paused at <b>{step_name}</b> ({role})\n\n"
            f"Reply <b>approve</b> or <b>reject [reason]</b> to action this gate.\n\n"
            f"Step ID: <code>{step_run_id}</code>",
            severity="warning",
        )
    except Exception:
        pass


def approve_step(step_run_id: str, actor: str = "approver") -> bool:
    """Signal approval for a paused HITL step. Returns False if no pending gate."""
    # First try in-memory Event (same process — immediate)
    with _approval_lock:
        ev = _approval_events.get(step_run_id)
        if ev:
            _approval_results[step_run_id] = "approved"
            _approval_reasons[step_run_id] = f"Approved by {actor}"
            ev.set()
            return True

    # Fallback: write directly to DB so the DB-poll loop in _worker picks it up
    # (used when approval comes from a different process, e.g. Telegram listener)
    try:
        conn = get_connection()
        try:
            # rowcount lives on the cursor, not the connection — reading it from
            # the connection raised AttributeError into the bare except below,
            # so this path always reported failure even though it committed.
            cur = conn.execute(
                "UPDATE studio_workflow_run_steps SET status='approved', stderr=%s, completed_at=%s "
                "WHERE step_run_id=%s AND status='awaiting_approval'",
                (f"Approved by {actor}", datetime.now(timezone.utc).isoformat(), step_run_id),
            )
            affected = getattr(cur, "rowcount", 0) or 0
            conn.commit()
            return affected > 0
        finally:
            conn.close()
    except Exception:
        logger.exception("approve_step failed for %s", step_run_id)
        return False


def reject_step(step_run_id: str, reason: str = "", actor: str = "approver") -> bool:
    """Signal rejection for a paused HITL step. Returns False if no pending gate."""
    with _approval_lock:
        ev = _approval_events.get(step_run_id)
        if ev:
            _approval_results[step_run_id] = "rejected"
            _approval_reasons[step_run_id] = reason or f"Rejected by {actor}"
            ev.set()
            return True

    try:
        conn = get_connection()
        try:
            # See approve_step: rowcount is a cursor property, not a connection one.
            cur = conn.execute(
                "UPDATE studio_workflow_run_steps SET status='rejected', stderr=%s, completed_at=%s "
                "WHERE step_run_id=%s AND status='awaiting_approval'",
                (reason or f"Rejected by {actor}", datetime.now(timezone.utc).isoformat(), step_run_id),
            )
            affected = getattr(cur, "rowcount", 0) or 0
            conn.commit()
            return affected > 0
        finally:
            conn.close()
    except Exception:
        logger.exception("reject_step failed for %s", step_run_id)
        return False


def get_pending_approvals() -> list[str]:
    """Return step_run_ids currently awaiting approval.

    Reads the database, not the in-process `_approval_events` dict — a gate
    parked by a previous process is still pending, and a gate parked by this
    one is visible to every other process.
    """
    try:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT step_run_id FROM studio_workflow_run_steps "
                "WHERE status = 'awaiting_approval' ORDER BY started_at"
            ).fetchall()
            return [r["step_run_id"] for r in rows]
        finally:
            conn.close()
    except Exception:
        with _approval_lock:
            return list(_approval_events.keys())


def _await_gate(step: dict, step_run_id: str, started_at: str | None) -> tuple[str | None, str]:
    """Block until a gate is decided or expires.

    Returns (decision, reason) where decision is 'approved', 'rejected' or None
    for expiry. Waits on the in-process Event (fast path, same process) and
    polls the DB (any process) — either can release the gate.
    """
    ev = threading.Event()
    with _approval_lock:
        _approval_events[step_run_id] = ev

    deadline = _gate_deadline(step, started_at)
    decision: str | None = None
    reason = ""
    try:
        while time.time() < deadline:
            # 1. In-process signal (fast path).
            if ev.wait(timeout=min(10, max(1, deadline - time.time()))):
                with _approval_lock:
                    decision = _approval_results.pop(step_run_id, None)
                    reason = _approval_reasons.pop(step_run_id, "")
                break
            # 2. Cross-process decision recorded in the DB.
            try:
                conn = get_connection()
                try:
                    row = conn.execute(
                        "SELECT status, stderr FROM studio_workflow_run_steps "
                        "WHERE step_run_id = %s", (step_run_id,)
                    ).fetchone()
                    if row and row["status"] in ("approved", "rejected"):
                        decision = row["status"]
                        reason = row["stderr"] or ""
                        break
                finally:
                    conn.close()
            except Exception:
                pass
    finally:
        with _approval_lock:
            _approval_results.pop(step_run_id, None)
            _approval_reasons.pop(step_run_id, None)
            _approval_events.pop(step_run_id, None)

    return decision, reason


# ── Worker thread ──────────────────────────────────────────

def _load_prior_steps(run_id: str) -> dict[str, dict]:
    """Return {step_id: row} for steps already recorded against this run.

    Used when resuming: a step already recorded success/approved is satisfied
    and must not run again (terraform apply is not idempotent), and a step still
    parked at awaiting_approval keeps its original step_run_id so approvals
    issued before the restart still resolve it.
    """
    try:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT step_run_id, step_id, status, started_at, stdout, stderr, "
                "exit_code, duration_ms FROM studio_workflow_run_steps "
                "WHERE run_id = %s ORDER BY started_at", (run_id,)
            ).fetchall()
            return {r["step_id"]: dict(r) for r in rows}
        finally:
            conn.close()
    except Exception:
        return {}


def _worker(
    run_id: str,
    workflow_id: str,
    wf: dict,
    project_id: str,
    run_queue: queue.Queue,
    resume: bool = False,
) -> None:
    try:
        template_yaml = wf.get("template_yaml", "")
        data = yaml.safe_load(template_yaml)
        steps = data.get("steps", [])
        if project_id == "default" and data.get("project_id"):
            project_id = data["project_id"]

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
        all_artifacts: list[dict] = []
        prior = _load_prior_steps(run_id) if resume else {}

        for i, step in enumerate(ordered_steps):
            _push(run_queue, {
                "type": "step_started",
                "run_id": run_id,
                "step_id": step["id"],
                "step_name": step.get("name", step["id"]),
                "index": i,
                "total": len(ordered_steps),
            })

            prior_row = prior.get(step["id"]) or {}
            prior_status = prior_row.get("status")

            if prior_status in ("success", "approved", "skipped"):
                # Already satisfied before the restart — replay it, don't re-run.
                result = {
                    "step_id": step["id"],
                    "step_name": step.get("name", step["id"]),
                    "tool": _step_tool_path(step),
                    "status": prior_status,
                    "stdout": prior_row.get("stdout"),
                    "stderr": prior_row.get("stderr"),
                    "exit_code": prior_row.get("exit_code"),
                    "duration_ms": prior_row.get("duration_ms") or 0,
                }
                step_run_id = prior_row.get("step_run_id", "")
            elif prior_status == "awaiting_approval":
                # Re-attach to the existing gate; keep its step_run_id so an
                # approval issued before the restart still resolves it.
                step_run_id = prior_row.get("step_run_id", "")
                result = _exec_step(step, project_id, run_id)
                result["status"] = "awaiting_approval"
            else:
                step_run_id = _create_step_record(
                    run_id, step["id"], step.get("name", step["id"]), _step_tool_path(step)
                )
                result = _exec_step_with_retries(step, project_id, run_id)

            if result["status"] in ("success", "approved", "skipped") and prior_status:
                # Replayed step: emit its artifacts and move on without touching
                # the append-only record again.
                try:
                    if result.get("stdout"):
                        all_artifacts.extend(json.loads(result["stdout"]).get("artifacts", []))
                except (json.JSONDecodeError, AttributeError, TypeError):
                    pass
                results.append(result)
                _push(run_queue, {
                    "type": "step_done",
                    "run_id": run_id,
                    "step_id": step["id"],
                    "step_name": step.get("name", step["id"]),
                    "status": result["status"],
                    "duration_ms": result.get("duration_ms", 0),
                    "resumed": True,
                    "index": i,
                    "total": len(ordered_steps),
                })
                continue

            if result["status"] == "awaiting_approval":
                # Persist the gate state and pause the run
                _update_step_record(step_run_id, result)
                _update_run_status(run_id, "awaiting_approval")
                _push(run_queue, {
                    "type": "step_awaiting_approval",
                    "run_id": run_id,
                    "step_id": step["id"],
                    "step_name": step.get("name", step["id"]),
                    "step_run_id": step_run_id,
                    "role": step.get("role", "approver"),
                })
                if not prior_status:
                    # Only notify on the first park, not on every resume.
                    _notify_approval_gate(
                        run_id, step_run_id, step.get("name", step["id"]),
                        step.get("role", "approver"),
                    )

                decision, reason = _await_gate(
                    step, step_run_id, prior_row.get("started_at"),
                )
                signaled = decision is not None
                if signaled and decision == "approved":
                    result["status"] = "approved"
                    result["stderr"] = reason or "Approved"
                    _update_step_record(step_run_id, result)
                    _update_run_status(run_id, "running")
                else:
                    result["status"] = "rejected" if (signaled and decision == "rejected") else "timeout"
                    result["stderr"] = reason or ("Rejected" if decision == "rejected" else "Approval timed out after 24h")
                    overall_ok = False
                    _update_step_record(step_run_id, result)
            else:
                _update_step_record(step_run_id, result)

            results.append(result)

            if result["status"] in ("failed", "timeout", "rejected") and step.get("required", True):
                overall_ok = False
            if result["status"] in ("rejected", "timeout") and step.get("node_type") in ("human", "approval"):
                # Stop processing further steps after a rejected/timed-out approval
                _push(run_queue, {
                    "type": "step_done",
                    "run_id": run_id,
                    "step_id": step["id"],
                    "step_name": step.get("name", step["id"]),
                    "status": result["status"],
                    "duration_ms": result.get("duration_ms", 0),
                    "artifacts": [],
                    "index": i,
                    "total": len(ordered_steps),
                })
                break

            # Extract artifacts list from stdout JSON if present
            artifacts = []
            try:
                if result.get("stdout"):
                    parsed_out = json.loads(result["stdout"])
                    artifacts = parsed_out.get("artifacts", [])
                    all_artifacts.extend(artifacts)
            except (json.JSONDecodeError, AttributeError):
                pass

            _push(run_queue, {
                "type": "step_done",
                "run_id": run_id,
                "step_id": step["id"],
                "step_name": step.get("name", step["id"]),
                "status": result["status"],
                "duration_ms": result.get("duration_ms", 0),
                "output_preview": (result.get("stdout") or "")[:500],
                "error": result.get("stderr"),
                "attempts": result.get("attempts", 1),
                "artifacts": artifacts,
                "index": i,
                "total": len(ordered_steps),
            })

        summary = {
            "total": len(results),
            "success": sum(1 for r in results if r["status"] in ("success", "approved")),
            "failed": sum(1 for r in results if r["status"] in ("failed", "timeout", "rejected")),
            "skipped": sum(1 for r in results if r["status"] == "skipped"),
        }
        if not overall_ok:
            overall = "failed"
        else:
            overall = "success"
        if summary["success"] == 0 and summary["skipped"] > 0 and overall == "success":
            summary["all_skipped"] = True
        summary["artifacts"] = all_artifacts
        _update_run_status(run_id, overall, json.dumps(summary))
        _push(run_queue, {
            "type": "run_complete",
            "run_id": run_id,
            "status": overall,
            "summary": summary,
            "artifacts": all_artifacts,
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


def resume_run(run_id: str) -> bool:
    """Re-attach a worker to a run left mid-flight, and continue it.

    Steps already recorded success/approved/skipped are replayed, not re-executed
    — terraform apply is not idempotent. A step still parked at
    awaiting_approval keeps its original step_run_id, so an approval issued
    before the interruption still resolves it.

    Resume continues the ORIGINAL run row (`RESUME_MODE == "in_place"`); it does
    not fork a new run linked by a `resumed_from_run_id`. See
    docs/features/dwo-durable-workflow-orchestration.md for why: a gate parked
    at awaiting_approval keeps its step_run_id, and that step row is keyed to
    this run_id — forking would strand every approval issued before the
    interruption against a run nobody is executing.

    Returns False if the run does not exist, is already finished, or is already
    being executed by a live worker in this process.
    """
    from tools.studio.workflow_editor import get_workflow  # noqa: PLC0415

    run = get_run(run_id)
    if not run or run.get("status") not in RESUMABLE_RUN_STATUSES:
        return False

    with _run_queues_lock:
        if run_id in _run_queues:
            return False  # a live worker already owns this run
        run_queue: queue.Queue = queue.Queue(maxsize=500)
        _run_queues[run_id] = run_queue

    wf = get_workflow(run.get("workflow_id", ""))
    if not wf:
        with _run_queues_lock:
            _run_queues.pop(run_id, None)
        return False

    threading.Thread(
        target=_worker,
        args=(run_id, run.get("workflow_id", ""), wf,
              run.get("project_id") or "default", run_queue),
        kwargs={"resume": True},
        daemon=True,
    ).start()
    return True


def _expire_gate(run_id: str, step_run_id: str, reason: str) -> None:
    """Fail a gate whose approval window has closed, and fail its run."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE studio_workflow_run_steps SET status='timeout', stderr=%s, completed_at=%s "
            "WHERE step_run_id=%s AND status='awaiting_approval'",
            (reason, datetime.now(timezone.utc).isoformat(), step_run_id),
        )
        conn.execute(
            "UPDATE studio_workflow_runs SET status='failed', completed_at=%s, summary_json=%s "
            "WHERE run_id=%s",
            (datetime.now(timezone.utc).isoformat(), json.dumps({"error": reason}), run_id),
        )
        conn.commit()
    finally:
        conn.close()


def reconcile_runs_on_boot() -> dict:
    """Re-attach or expire runs left mid-flight by a process that died.

    Call once from the app startup path — NOT at import time.

    This replaces the previous behaviour, which force-failed every
    `awaiting_approval` run on every import (i.e. every dashboard start), so no
    approval could survive a restart or a deploy:

      - gate still inside its approval window -> resume the run;
      - gate past its window                  -> expire it and fail the run;
      - step stuck at `running`               -> its subprocess died with the
        old process and cannot be re-attached, so fail that step. The run is
        then resumable from the failed step.

    Returns a summary dict; never raises.
    """
    summary = {"resumed": [], "expired": [], "orphaned_steps": []}
    try:
        conn = get_connection()
        try:
            parked = conn.execute(
                "SELECT r.run_id, r.workflow_id, s.step_run_id, s.step_id, s.started_at "
                "FROM studio_workflow_runs r "
                "JOIN studio_workflow_run_steps s ON s.run_id = r.run_id "
                "WHERE r.status = 'awaiting_approval' AND s.status = 'awaiting_approval'"
            ).fetchall()
            orphaned = conn.execute(
                "SELECT step_run_id, run_id FROM studio_workflow_run_steps "
                "WHERE status = 'running'"
            ).fetchall()
        finally:
            conn.close()
    except Exception as exc:  # pragma: no cover - DB unavailable at boot
        logger.warning("Studio run reconciliation skipped: %s", exc)
        return summary

    # A 'running' step's subprocess died with the previous process.
    for row in orphaned:
        try:
            conn = get_connection()
            try:
                conn.execute(
                    "UPDATE studio_workflow_run_steps SET status='failed', stderr=%s, completed_at=%s "
                    "WHERE step_run_id=%s AND status='running'",
                    (
                        "Interrupted: the process executing this step exited. "
                        "Resume the run to retry from this step.",
                        datetime.now(timezone.utc).isoformat(),
                        row["step_run_id"],
                    ),
                )
                conn.commit()
            finally:
                conn.close()
            summary["orphaned_steps"].append(row["step_run_id"])
        except Exception:
            continue

    for row in parked:
        step = _find_step(row["workflow_id"], row["step_id"])
        deadline = _gate_deadline(step or {}, row["started_at"])
        if deadline <= time.time():
            try:
                _expire_gate(
                    row["run_id"], row["step_run_id"],
                    "Approval window expired while no worker was running.",
                )
                summary["expired"].append(row["run_id"])
            except Exception:
                pass
            continue
        try:
            if resume_run(row["run_id"]):
                summary["resumed"].append(row["run_id"])
        except Exception:
            continue

    if summary["resumed"] or summary["expired"] or summary["orphaned_steps"]:
        logger.info(
            "Studio run reconciliation: resumed=%d expired=%d orphaned_steps=%d",
            len(summary["resumed"]), len(summary["expired"]), len(summary["orphaned_steps"]),
        )
    return summary


def _find_step(workflow_id: str, step_id: str) -> dict | None:
    """Return a step definition from a workflow's template, or None."""
    try:
        from tools.studio.workflow_editor import get_workflow  # noqa: PLC0415
        wf = get_workflow(workflow_id)
        if not wf:
            return None
        data = yaml.safe_load(wf.get("template_yaml", "")) or {}
        for step in data.get("steps", []) or []:
            if step.get("id") == step_id:
                return step
    except Exception:
        pass
    return None


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
                "SELECT * FROM studio_workflow_runs WHERE workflow_id = %s ORDER BY started_at DESC LIMIT %s",
                (workflow_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM studio_workflow_runs ORDER BY started_at DESC LIMIT %s",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        # Table may not be initialized yet (e.g., fresh CI environment)
        return []
    finally:
        conn.close()


def get_run(run_id: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM studio_workflow_runs WHERE run_id = %s", (run_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_run_steps(run_id: str) -> list:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM studio_workflow_run_steps WHERE run_id = %s ORDER BY started_at ASC",
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_run(run_id: str) -> bool:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM studio_workflow_run_steps WHERE run_id = %s", (run_id,))
        cur = conn.execute("DELETE FROM studio_workflow_runs WHERE run_id = %s", (run_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_all_runs(workflow_id: str | None = None) -> int:
    conn = get_connection()
    try:
        if workflow_id:
            run_ids = [r[0] for r in conn.execute(
                "SELECT run_id FROM studio_workflow_runs WHERE workflow_id = %s", (workflow_id,)
            ).fetchall()]
            if run_ids:
                placeholders = ",".join(["%s"] * len(run_ids))
                conn.execute(f"DELETE FROM studio_workflow_run_steps WHERE run_id IN ({placeholders})", run_ids)
            cur = conn.execute(
                "DELETE FROM studio_workflow_runs WHERE workflow_id = %s", (workflow_id,)
            )
        else:
            conn.execute("DELETE FROM studio_workflow_run_steps")
            cur = conn.execute("DELETE FROM studio_workflow_runs")
        conn.commit()
        return cur.rowcount
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
        tool = _step_tool_path(step)
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
            if node_type == "mcp":
                # The tool name and its params are flags on the shared executor,
                # not free-form args.
                cmd_parts.append(f'"--tool", {step.get("mcp_tool", "")!r}')
                cmd_parts.append(f'"--params", {_mcp_params_json(step)!r}')
                cmd_parts.append(f'"--step-id", {sid!r}')
                step_args = {}
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
