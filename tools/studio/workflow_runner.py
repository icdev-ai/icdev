"""ICDEV™ Studio — Per-Run SSE Workflow Execution Engine.

Wraps workflow_composer execution logic to emit per-step events to a
per-run queue instead of returning all results at the end. Each run_id
gets its own queue so multiple concurrent runs never mix events.

Architecture Decision D343+: per-run SSE (not global broadcast) keeps
multi-user and multi-tab runs isolated.
"""
# CUI // SP-CTI

from __future__ import annotations

import itertools
import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from graphlib import CycleError, TopologicalSorter
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402
from tools.studio import run_memory  # noqa: E402
# hgx-cond-01: the one condition DSL — see the "Conditional edges" section.
from tools.studio.automation_builder import evaluate_conditions  # noqa: E402

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

def _dag_graph(steps: list) -> dict:
    """{step_id: {dependency_id, ...}} for the template's steps."""
    graph: dict[str, set] = {}
    for step in steps:
        graph[step["id"]] = set(step.get("depends_on", []) or [])
    return graph


def _resolve_dag(steps: list) -> list:
    """A flattened topological order — the announcement order, not the run order.

    Execution walks a prepared sorter instead (see `_prepare_dag`), so a fan-out
    can dispatch concurrently. This flattening still backs the `run_started`
    payload and the generated-script path, both of which need a single list.
    """
    return list(TopologicalSorter(_dag_graph(steps)).static_order())


def _dependents(steps: list) -> dict:
    """{step_id: {ids of steps declaring it in depends_on}} — reverse of `_dag_graph`."""
    children: dict[str, set] = {step["id"]: set() for step in steps}
    for step in steps:
        for dep in step.get("depends_on", []) or []:
            if dep in children:
                children[dep].add(step["id"])
    return children


def _block_downstream(children: dict, failed_id: str, conditional: set | None = None) -> list:
    """Every step that transitively depends on `failed_id`, breadth-first.

    Ported from `tools/agent/team_orchestrator.py::_block_downstream`, which
    cascades through the whole graph rather than one level: if A fails, B
    depends on A and C on B, then both B and C are blocked. Iterative rather
    than recursive — a chain long enough to matter is a template typo, not a
    reason to raise RecursionError inside a pool thread.

    ONE addition the Studio runner needs that the orchestrator does not: the
    cascade stops at a step listed in `conditional` — a step declaring `when:`.
    That step IS the remediation branch. It asked to be routed on its
    predecessor's recorded outcome, so cancelling it for having a failed
    predecessor would make `fail -> remediate` unreachable and leave `when` able
    to express only conditions that never fire after a failure. Such a step is
    neither blocked nor walked through: it evaluates its own condition, and what
    it produces then governs its own descendants.
    """
    conditional = conditional or set()
    pending = [sid for sid in children.get(failed_id, ()) if sid not in conditional]
    seen = set(pending)
    blocked: list[str] = []
    while pending:
        step_id = pending.pop(0)
        blocked.append(step_id)
        for nxt in children.get(step_id, ()):
            if nxt not in seen and nxt not in conditional:
                seen.add(nxt)
                pending.append(nxt)
    return blocked


def _prepare_dag(steps: list) -> TopologicalSorter:
    """A prepared sorter for wave-parallel dispatch (`get_ready`/`done`).

    Mirrors `tools/agent/team_orchestrator.py::execute_workflow` — decisions D40
    (graphlib) and D36 (threads, not asyncio). `prepare()` raises CycleError here
    rather than at the first `get_ready()`, so a circular template fails at the
    same point in `_worker` as it did when the order was resolved eagerly.

    A join needs no barrier primitive: a step with several `depends_on` entries
    is simply not handed out by `get_ready()` until every one of them is `done()`.
    """
    sorter = TopologicalSorter(_dag_graph(steps))
    sorter.prepare()
    return sorter


# Concurrency is capped so a template typo (`max_parallel: 500`) cannot spawn a
# thread per step. Steps are subprocesses, so the useful ceiling is low anyway.
_MAX_PARALLEL_CAP = 16


def _max_parallel(data: dict) -> int:
    """Concurrent step slots for a run, from the template's `max_parallel` key.

    DEFAULT 1 — a template that does not declare it runs exactly as it did
    before parallel dispatch existed: one step at a time, in `_resolve_dag`
    order. An unparseable or out-of-range value degrades to the nearest legal
    one rather than failing the run.
    """
    try:
        value = int(data.get("max_parallel", 1) or 1)
    except (TypeError, ValueError):
        return 1
    return max(1, min(value, _MAX_PARALLEL_CAP))


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
    # argv only. The run's inputs reach the step through the environment
    # instead — see RUN_INPUTS_ENV_VAR / _build_step_env — because an inputs
    # payload is an arbitrarily large JSON object and Windows caps a command
    # line at ~32k characters.
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


# ── Run inputs exposed to steps (dwo-evt-04) ───────────────

# The contract a step process — and the dwo-mem-02 run-memory layer built on
# top of it — can rely on:
#
#   * When the run was started with inputs (`start_run(..., inputs={...})`,
#     recorded on the run row as `inputs_json`), every step subprocess is
#     handed DWF_RUN_INPUTS_JSON holding that JSON object.
#   * The value is the run row's stored text verbatim, NOT a re-serialization,
#     so a step reads exactly what start_run persisted.
#   * The variable is ABSENT when the run recorded no inputs (SQL NULL — the
#     `inputs=None` default). Absent means "no inputs", which stays distinct
#     from the value "{}", "started with an empty input set". A stale value
#     inherited from this process's own environment is stripped rather than
#     passed through, so absent always means absent.
#   * It is read-only from a step's point of view: the payload the run STARTED
#     with never changes mid-run. A step that wants to publish something for a
#     later step writes run memory (`tools/studio/run_memory.py`) instead.
RUN_INPUTS_ENV_VAR = "DWF_RUN_INPUTS_JSON"


def _run_inputs_json(run_id: str) -> str | None:
    """The run row's ``inputs_json`` as stored, or None when it has none.

    A lookup failure degrades to None: a step losing its inputs variable is a
    better outcome than a run dying because one SELECT failed.
    """
    if not run_id:
        return None
    try:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT inputs_json FROM studio_workflow_runs WHERE run_id = %s",
                (run_id,),
            ).fetchone()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("Could not read inputs for run %s: %s", run_id, exc)
        return None
    return dict(row).get("inputs_json") if row else None


def _build_step_env(run_id: str = "") -> dict:
    """Environment handed to a step subprocess."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    # dwo-mem-01: the step reads/writes run-scoped memory through this id.
    if run_id:
        env["ICDEV_RUN_ID"] = run_id
    # dwo-evt-04: see RUN_INPUTS_ENV_VAR for the contract.
    inputs_json = _run_inputs_json(run_id)
    if inputs_json is None:
        env.pop(RUN_INPUTS_ENV_VAR, None)
    else:
        env[RUN_INPUTS_ENV_VAR] = inputs_json
    return env


# ── Conditional edges (hgx-cond-01) ────────────────────────
#
# A step may declare `when:` — the SAME {field, operator, value} DSL that
# `studio_workflow_triggers.filter_json` and the automation surface already
# filter events with. `tools/studio/automation_builder.py` holds the one
# implementation of CONDITION_OPERATORS; this imports `evaluate_conditions`
# from it rather than growing a second rules DSL that would drift from the
# operator list the builder UI renders.
#
# Conditions are evaluated against the PREDECESSOR's recorded result:
#
#   * a flat name (`status`, `exit_code`, `stderr`, `duration_ms`) reads the
#     FIRST entry in this step's `depends_on` — "the predecessor" for a step
#     with one incoming edge, and a defined choice for a join;
#   * `steps.<step_id>.<field>` reaches any predecessor already recorded, which
#     is how a join addresses one branch in particular;
#   * `output.<path>` walks the predecessor's stdout parsed as JSON, so a step
#     can branch on what the tool actually reported rather than on its exit code.
#
# EVERY condition must be met (AND), matching how the automation surface reads
# the same list. A step with no `when` key is unconditional — byte-for-byte the
# behaviour that predates this.


def _when_conditions(step: dict) -> list:
    """A step's `when:` block normalised to a list of condition dicts.

    A single mapping is accepted as well as a list, and `value` is coerced to
    str: YAML types a bare `value: 0` as an int while the DSL compares strings.
    A malformed entry is dropped rather than raising mid-run — the linter is
    where an authoring mistake is meant to surface.
    """
    raw = step.get("when")
    if raw is None:
        return []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    conditions = []
    for cond in raw:
        if not isinstance(cond, dict):
            continue
        value = cond.get("value")
        conditions.append({
            "field": str(cond.get("field", "") or ""),
            "operator": str(cond.get("operator", "equals") or "equals"),
            "value": "" if value is None else str(value),
        })
    return conditions


def _step_view(result: dict) -> dict:
    """One recorded step result as the condition DSL sees it."""
    view = {
        "step_id": result.get("step_id", ""),
        "step_name": result.get("step_name", ""),
        "status": result.get("status", ""),
        "exit_code": result.get("exit_code"),
        "stdout": result.get("stdout") or "",
        "stderr": result.get("stderr") or "",
        "duration_ms": result.get("duration_ms", 0),
    }
    try:
        parsed = json.loads(result.get("stdout") or "")
    except (json.JSONDecodeError, TypeError):
        parsed = None
    view["output"] = parsed if isinstance(parsed, dict) else {}
    return view


def _condition_context(step: dict, recorded: dict) -> dict:
    """Evaluation context for a step's `when:` — see the section comment."""
    context: dict = {}
    for dep in step.get("depends_on", []) or []:
        if dep in recorded:
            context.update(_step_view(recorded[dep]))
            break
    # Set last: `steps` is the addressing map, never a predecessor field.
    context["steps"] = {sid: _step_view(res) for sid, res in recorded.items()}
    return context


def _evaluate_when(step: dict, recorded: dict) -> tuple[bool, str]:
    """(met, reason) for a step's `when:`. Met is True when it declares none."""
    conditions = _when_conditions(step)
    if not conditions:
        return True, ""

    results = evaluate_conditions(conditions, _condition_context(step, recorded))
    unmet = [r for r in results if not r["met"]]
    if not unmet:
        return True, ""
    detail = "; ".join(
        f"{r['field']} {r['operator']} {r['expected']!r} (actual: {r['actual']!r})"
        for r in unmet
    )
    return False, f"Condition not met: {detail}"


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
        _env = _build_step_env(run_id)
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


# ── Run memory (dwo-mem-02) ────────────────────────────────

def _step_artifacts(result: dict) -> list:
    """Artifacts a step declared in its stdout JSON contract, or []."""
    try:
        return json.loads(result.get("stdout") or "").get("artifacts", []) or []
    except (json.JSONDecodeError, AttributeError, TypeError):
        return []


def _remember_canvas(run_id: str, canvas: str) -> None:
    """Record the template's declared canvas as this run's authoritative slug."""
    if not run_id or not canvas:
        return
    try:
        run_memory.set(run_id, run_memory.CANVAS_KEY, canvas)
    except Exception as exc:
        logger.warning("Could not record canvas for run %s: %s", run_id, exc)


# The artifacts key is one JSON blob updated read-modify-write, so two steps of
# the same parallel wave publishing at once would drop one of the two. Held for
# the whole get/set pair, not just the set.
_artifacts_lock = threading.Lock()


def _remember_artifacts(run_id: str, step_name: str, artifacts: list) -> None:
    """Record a step's artifacts under run memory's ``artifacts`` key.

    Executors read their inputs from here rather than re-parsing an earlier
    step's stdout. A write failure is not fatal: `executors/_base.py` still
    falls back to the stdout query.
    """
    if not run_id or not artifacts:
        return
    try:
        with _artifacts_lock:
            current = run_memory.get(run_id, run_memory.ARTIFACTS_KEY, default={})
            if not isinstance(current, dict):
                current = {}
            current[step_name] = artifacts
            run_memory.set(run_id, run_memory.ARTIFACTS_KEY, current)
    except Exception as exc:
        logger.warning("Could not record artifacts for run %s: %s", run_id, exc)


# ── DB helpers ─────────────────────────────────────────────

def _push(run_queue: queue.Queue, event: dict) -> None:
    try:
        run_queue.put_nowait(event)
    except queue.Full:
        pass


def _create_run_record(
    run_id: str,
    workflow_id: str,
    workflow_name: str,
    project_id: str,
    inputs: dict | None = None,
) -> None:
    # inputs None -> SQL NULL ("no inputs recorded"), which stays distinct from
    # '{}' ("started with an empty input set"). See migration 306.
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO studio_workflow_runs
               (run_id, workflow_id, workflow_name, status, started_at, project_id,
                inputs_json)
               VALUES (%s, %s, %s, 'pending', %s, %s, %s)""",
            (run_id, workflow_id, workflow_name, datetime.now(timezone.utc).isoformat(),
             project_id, None if inputs is None else json.dumps(inputs)),
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

def _notify_approval_gate(
    run_id: str, step_run_id: str, step_name: str, role: str,
    project_id: str = "default",
) -> None:
    """Publish a paused gate to the single reviewer inbox, then notify.

    dwo-dur-04: workflow_hitl owns the reviewer inbox, notification routing and
    the webhook-token completion path. Studio registers the gate there rather
    than standing up a second approval surface. Telegram's
    ``/approve <step_run_id>`` keeps working either way — the step_run_id in the
    message body is unchanged.
    """
    try:
        from tools.studio import gate_bridge  # noqa: PLC0415
        gate_bridge.open_gate(run_id, step_run_id, step_name, role, project_id)
    except Exception:
        pass

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


def _approve_step_local(step_run_id: str, actor: str = "approver") -> bool:
    """Release the Studio-side gate only. See approve_step for the bridged form."""
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


def _reject_step_local(step_run_id: str, reason: str = "", actor: str = "approver") -> bool:
    """Release the Studio-side gate only. See reject_step for the bridged form."""
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


def approve_step(step_run_id: str, actor: str = "approver") -> bool:
    """Approve a paused HITL step and close out its workflow_hitl external step.

    dwo-dur-04: a decision taken in either surface releases the other, exactly
    once. When the decision arrived *from* workflow_hitl the bridge suppresses
    the callback, so the external step is not completed twice.
    """
    released = _approve_step_local(step_run_id, actor)
    if released:
        try:
            from tools.studio import gate_bridge  # noqa: PLC0415
            gate_bridge.complete_external_step(step_run_id, "approved", actor)
        except Exception:
            logger.exception("external step sync failed for %s", step_run_id)
    return released


def reject_step(step_run_id: str, reason: str = "", actor: str = "approver") -> bool:
    """Reject a paused HITL step and close out its workflow_hitl external step."""
    released = _reject_step_local(step_run_id, reason, actor)
    if released:
        try:
            from tools.studio import gate_bridge  # noqa: PLC0415
            gate_bridge.complete_external_step(step_run_id, "rejected", actor)
        except Exception:
            logger.exception("external step sync failed for %s", step_run_id)
    return released


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
            sorter = _prepare_dag(steps)
        except CycleError as exc:
            msg = f"Circular dependency detected: {exc}"
            _update_run_status(run_id, "failed", json.dumps({"error": msg}))
            _push(run_queue, {"type": "error", "run_id": run_id, "message": msg})
            return

        step_map = {s["id"]: s for s in steps}
        ordered_steps = [step_map[sid] for sid in order if sid in step_map]
        parallel = _max_parallel(data)

        # The template's `canvas:` key is the authoritative slug for this run —
        # publish it before any step runs so executors read it from memory
        # rather than re-deriving it (dwo-mem-02).
        _remember_canvas(run_id, str(data.get("canvas") or "").strip().lower())

        _update_run_status(run_id, "running")
        _push(run_queue, {
            "type": "run_started",
            "run_id": run_id,
            "total_steps": len(ordered_steps),
            "max_parallel": parallel,
            "steps": [{"id": s["id"], "name": s.get("name", s["id"])} for s in ordered_steps],
        })

        results: list[dict] = []
        overall_ok = True
        all_artifacts: list[dict] = []
        prior = _load_prior_steps(run_id) if resume else {}
        total = len(ordered_steps)

        # Guards every mutation of results/all_artifacts/overall_ok, all of which
        # a parallel wave writes from several threads at once.
        state_lock = threading.Lock()
        # A rejected or timed-out human gate stops the run. Under the old linear
        # walk that was a `break`; the equivalent here has to be a flag, because
        # a sibling of the gate may already be sitting in the pool's queue.
        abort = threading.Event()
        # hgx-cond-01: step_id -> why it was cancelled. A required step that
        # fails writes every transitive dependent here, so a descendant is never
        # run against a precondition that is known to be broken. Safe without a
        # barrier: `sorter.done()` is called only after a node's future resolves,
        # so no dependent can have been dispatched yet when this is written.
        dependents = _dependents(steps)
        # A step declaring `when:` is exempt from the cascade — it is the
        # remediation branch, and routing on a failed predecessor is what it
        # asked for. See `_block_downstream`.
        conditional = {s["id"] for s in steps if _when_conditions(s)}
        blocked: dict[str, str] = {}
        # Emission is no longer ordered, so an event carries a monotonic sequence
        # number rather than its position in `ordered_steps`.
        _seq = itertools.count()

        def _next_seq() -> int:
            with state_lock:
                return next(_seq)

        def _skip_step(step: dict, reason: str) -> None:
            """Record a step that will not run, with why, and announce it.

            Uses the EXISTING `skipped` status and its existing reason field
            (`stderr`, where "Tool not found: …" already lands), so nothing
            downstream — the CHECK constraint, the run summary, the details
            modal — needs to learn a new value.
            """
            step_id = step["id"]
            step_name = step.get("name", step_id)
            result = {
                "step_id": step_id,
                "step_name": step_name,
                "tool": _step_tool_path(step),
                "status": "skipped",
                "stdout": None,
                "stderr": reason,
                "exit_code": None,
                "duration_ms": 0,
            }
            step_run_id = _create_step_record(
                run_id, step_id, step_name, _step_tool_path(step)
            )
            _update_step_record(step_run_id, result)
            with state_lock:
                results.append(result)
            _push(run_queue, {
                "type": "step_done",
                "run_id": run_id,
                "step_id": step_id,
                "step_name": step_name,
                "status": "skipped",
                "duration_ms": 0,
                "error": reason,
                "reason": reason,
                "artifacts": [],
                "seq": _next_seq(),
                "total": total,
            })

        def _run_node(step: dict) -> None:
            """Execute one DAG node. Runs on a pool thread; may block on a gate."""
            nonlocal overall_ok

            if abort.is_set():
                # Queued behind the gate that stopped the run. The old linear
                # walk never reached this step either, so it leaves no record.
                return

            step_id = step["id"]
            step_name = step.get("name", step_id)

            _push(run_queue, {
                "type": "step_started",
                "run_id": run_id,
                "step_id": step_id,
                "step_name": step_name,
                "seq": _next_seq(),
                "total": total,
            })

            prior_row = prior.get(step_id) or {}
            prior_status = prior_row.get("status")

            if prior_status in ("success", "approved", "skipped"):
                # Already satisfied before the restart — replay it, don't re-run.
                result = {
                    "step_id": step_id,
                    "step_name": step_name,
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
                # hgx-cond-01. Both checks sit on the about-to-execute path
                # only: a step already replayed or re-attached to its gate above
                # keeps the resume semantics it had.
                with state_lock:
                    cancel_reason = blocked.get(step_id)
                    recorded = {r["step_id"]: r for r in results}
                if cancel_reason:
                    _skip_step(step, cancel_reason)
                    return
                met, unmet_reason = _evaluate_when(step, recorded)
                if not met:
                    # A condition that did not hold is not a failure: this step's
                    # own dependents go on to evaluate their `when` against the
                    # `skipped` result recorded here.
                    _skip_step(step, unmet_reason)
                    return

                step_run_id = _create_step_record(
                    run_id, step_id, step_name, _step_tool_path(step)
                )
                result = _exec_step_with_retries(step, project_id, run_id)

            if result["status"] in ("success", "approved", "skipped") and prior_status:
                # Replayed step: emit its artifacts and move on without touching
                # the append-only record again.
                replayed_artifacts = _step_artifacts(result)
                with state_lock:
                    all_artifacts.extend(replayed_artifacts)
                    results.append(result)
                _remember_artifacts(run_id, result["step_name"], replayed_artifacts)
                _push(run_queue, {
                    "type": "step_done",
                    "run_id": run_id,
                    "step_id": step_id,
                    "step_name": step_name,
                    "status": result["status"],
                    "duration_ms": result.get("duration_ms", 0),
                    "resumed": True,
                    "seq": _next_seq(),
                    "total": total,
                })
                return

            if result["status"] == "awaiting_approval":
                # Persist the gate state and pause the run. Only THIS branch
                # parks: `_await_gate` blocks this pool thread, leaving the other
                # `max_parallel - 1` slots free for sibling branches.
                _update_step_record(step_run_id, result)
                _update_run_status(run_id, "awaiting_approval")
                _push(run_queue, {
                    "type": "step_awaiting_approval",
                    "run_id": run_id,
                    "step_id": step_id,
                    "step_name": step_name,
                    "step_run_id": step_run_id,
                    "role": step.get("role", "approver"),
                })
                if not prior_status:
                    # Only notify on the first park, not on every resume.
                    _notify_approval_gate(
                        run_id, step_run_id, step_name,
                        step.get("role", "approver"), project_id,
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
                    with state_lock:
                        overall_ok = False
                    _update_step_record(step_run_id, result)
            else:
                _update_step_record(step_run_id, result)

            with state_lock:
                results.append(result)
                if result["status"] in ("failed", "timeout", "rejected") and step.get("required", True):
                    overall_ok = False
                    # hgx-cond-01: a failed required step cancels its descendants
                    # instead of letting them run against a broken precondition.
                    # `setdefault` so the FIRST failure that reached a step is the
                    # reason it reports, not the last one to be recorded.
                    cancel_reason = (
                        f"Cancelled: required step '{step_name}' "
                        f"{result['status']}"
                    )
                    for dependent_id in _block_downstream(
                        dependents, step_id, conditional
                    ):
                        blocked.setdefault(dependent_id, cancel_reason)

            if result["status"] in ("rejected", "timeout") and step.get("node_type") in ("human", "approval"):
                # Stop the run after a rejected/timed-out approval. Steps already
                # queued behind this one return early on the `abort` check above.
                abort.set()
                _push(run_queue, {
                    "type": "step_done",
                    "run_id": run_id,
                    "step_id": step_id,
                    "step_name": step_name,
                    "status": result["status"],
                    "duration_ms": result.get("duration_ms", 0),
                    "artifacts": [],
                    "seq": _next_seq(),
                    "total": total,
                })
                return

            # Extract artifacts list from stdout JSON if present, and publish it
            # to run memory so a downstream step reads it there (dwo-mem-02).
            artifacts = _step_artifacts(result)
            with state_lock:
                all_artifacts.extend(artifacts)
            _remember_artifacts(run_id, step_name, artifacts)

            _push(run_queue, {
                "type": "step_done",
                "run_id": run_id,
                "step_id": step_id,
                "step_name": step_name,
                "status": result["status"],
                "duration_ms": result.get("duration_ms", 0),
                "output_preview": (result.get("stdout") or "")[:500],
                "error": result.get("stderr"),
                "attempts": result.get("attempts", 1),
                "artifacts": artifacts,
                "seq": _next_seq(),
                "total": total,
            })

        # ── Wave-parallel dispatch ─────────────────────────
        # get_ready() hands out every node whose dependencies are all done();
        # done() retires one and unblocks its successors. At max_parallel == 1
        # this walks the graph in exactly `_resolve_dag` order: the pool's queue
        # is FIFO, so submission order is execution order, and retiring in
        # submission order keeps graphlib's ready-queue in the same state
        # static_order() would have left it in.
        pool = ThreadPoolExecutor(
            max_workers=parallel, thread_name_prefix=f"dwf-{run_id}",
        )
        try:
            futures: dict = {}          # future -> (submission index, step_id)
            submitted = 0
            while sorter.is_active():
                ready = list(sorter.get_ready())
                for sid in ready:
                    if sid not in step_map or abort.is_set():
                        # A dangling depends_on target (no such step), or the run
                        # has been stopped by a rejected gate. Retire the node
                        # without executing it so the walk can finish.
                        sorter.done(sid)
                        continue
                    futures[pool.submit(_run_node, step_map[sid])] = (submitted, sid)
                    submitted += 1
                if not futures:
                    if not ready:
                        break       # nothing ready and nothing in flight
                    continue
                done_set, _pending = wait(list(futures), return_when=FIRST_COMPLETED)
                # Retire in SUBMISSION order, not set-iteration order: graphlib
                # appends each newly-unblocked node as its last dependency is
                # retired, so walking a set here would make the dispatch order
                # depend on thread scheduling.
                for fut in sorted(done_set, key=lambda f: futures[f][0]):
                    _, sid = futures.pop(fut)
                    fut.result()        # re-raise into the run-level handler
                    sorter.done(sid)
        finally:
            # wait=False: a sibling still parked at a human gate must not hold
            # this thread for the rest of the approval window when the loop exits
            # early. Every future is already resolved on the normal path.
            pool.shutdown(wait=False, cancel_futures=True)

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

def start_run(
    workflow_id: str,
    project_id: str = "default",
    inputs: dict | None = None,
) -> str:
    """Load a studio workflow from DB, spawn execution thread, return run_id.

    ``inputs`` (dwo-evt-04) is the payload the run was started with: for a
    triggered run, the event fields the trigger's ``input_mapping_json``
    selected; for a manual run, whatever the caller passed. It is recorded on
    the run row as ``inputs_json``, so "what was this run started with" is
    answerable from the run itself rather than by re-reading the trigger event
    and re-applying its mapping.

    The default ``None`` records SQL NULL — "no inputs" — which stays distinct
    from ``{}``, "started with an empty input set". Every caller that predates
    inputs therefore behaves exactly as it did before.
    """
    from tools.studio.workflow_editor import get_workflow  # noqa: PLC0415

    wf = get_workflow(workflow_id)
    if not wf:
        raise ValueError(f"Workflow not found: {workflow_id}")

    # Rejected before the run row exists, so a bad payload leaves no orphan run.
    if inputs is not None and not isinstance(inputs, dict):
        raise ValueError(
            f"inputs must be a JSON object (dict), not {type(inputs).__name__}"
        )

    run_id = f"run-{uuid.uuid4().hex[:12]}"
    run_queue: queue.Queue = queue.Queue(maxsize=500)

    with _run_queues_lock:
        _run_queues[run_id] = run_queue

    _create_run_record(
        run_id, workflow_id, wf.get("name", workflow_id), project_id, inputs=inputs,
    )

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
