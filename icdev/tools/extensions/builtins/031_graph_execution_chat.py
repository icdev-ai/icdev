#!/usr/bin/env python3
# CUI // SP-CTI
"""Graph execution chat extension handler (hgx-doc-01).

Hooks into ``chat_message_after`` to surface Studio DAG run status in chat,
following the same pattern as 030_workflow_loop_chat.py: an advisory dict
returned on the context, throttled by a per-context turn cooldown.

This reads the EXISTING graph runtime — ``tools/studio/workflow_runner.py``
and its ``studio_workflow_runs`` / ``studio_workflow_run_steps`` tables. It
does not execute, schedule or mutate anything; it is a read-only view onto a
run in flight. Four things it answers, which are otherwise only visible in the
Studio run modal:

  - which nodes are done, and how many are still running;
  - what a barrier is waiting for (a node with several ``depends_on`` entries
    that ``TopologicalSorter.get_ready()`` has not handed out yet — see
    ``workflow_runner._prepare_dag``, "a join needs no barrier primitive");
  - which gate needs approval, and
  - the command to release it.

NUMBERING — the source design specified 040, but ``040_bayesian_learning_chat``
already owns that slot, and ``_auto_load_builtins`` loads builtins in a
lexicographic sort of the filename (extension_manager.py), so 040 would have
collided rather than ordered. 031 keeps this handler adjacent to the workflow
advisory it complements (030) and ahead of the free 032-039 range.

ADVISORY TYPE — registered in ``chat_manager._ADVISORY_TYPES`` under the key
``graph_advisory`` with the EXISTING ``workflow_status`` content type. That
content type is already in the live ``chat_messages.content_type`` CHECK
constraint and already has a badge in ``chat.js``'s ``ADVISORY_MAP``, so no
migration and no frontend change is needed; a graph run is workflow status.
The distinct hook key and ``[Graph Run]`` label keep it readable next to 030's
``[Workflow Status]``.

Loaded automatically by ExtensionManager._auto_load_builtins().

Exports:
    EXTENSION_HOOKS — dict mapping hook point names to handler metadata.
"""

from __future__ import annotations

import yaml

from tools.db.storage import get_connection, table_exists as _table_exists
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.extensions.graph_execution_chat")

# ---------------------------------------------------------------------------
# Cooldown tracking (in-memory, per-context)
# ---------------------------------------------------------------------------

ADVISORY_COOLDOWN_TURNS = 8
_last_advisory_turn: dict = {}


def _should_advise(context_id: str, turn_number: int) -> bool:
    last = _last_advisory_turn.get(context_id, -ADVISORY_COOLDOWN_TURNS - 1)
    return (turn_number - last) >= ADVISORY_COOLDOWN_TURNS


def _record_advisory(context_id: str, turn_number: int):
    _last_advisory_turn[context_id] = turn_number


# ---------------------------------------------------------------------------
# Run / step vocabulary
#
# Mirrors the CHECK constraints in tools/studio/init_db.py. A node counts as
# "done" when the DAG can hand out its dependents: `success`, an `approved`
# gate, and a `skipped` node (an unconfigured step, or one whose `when:` did
# not fire) all release their children, so all three satisfy a barrier.
# ---------------------------------------------------------------------------

ACTIVE_RUN_STATUSES = ("pending", "running", "awaiting_approval")
DONE_STEP_STATUSES = ("success", "approved", "skipped")
GATE_STEP_STATUS = "awaiting_approval"

# workflow_runner.py is a library — it declares no argparse and no __main__ —
# so the action is the documented import form, not an invented CLI.
_APPROVE_ACTION = (
    'python -c "from tools.studio.workflow_runner import approve_step; '
    "approve_step('{step_run_id}')\""
)


def _template_steps(conn, workflow_id: str) -> list:
    """The workflow's authored steps, or [] if the template is gone/unparseable.

    A run outlives an edit of its workflow, so this can legitimately return a
    template that no longer matches the recorded steps. Everything derived from
    it below is therefore additive — the node counts come from the run's own
    step rows, not from here.
    """
    try:
        row = conn.execute(
            "SELECT template_yaml FROM studio_workflows WHERE workflow_id = %s",
            (workflow_id,),
        ).fetchone()
        if not row:
            return []
        data = yaml.safe_load(row["template_yaml"] or "") or {}
        steps = data.get("steps", []) or []
        return [s for s in steps if isinstance(s, dict) and s.get("id")]
    except Exception as exc:
        logger.debug("Could not read template for workflow %s: %s", workflow_id, exc)
        return []


def _find_barrier(steps: list, status_by_step_id: dict) -> dict | None:
    """The first join node still waiting on an unfinished dependency.

    A "barrier" here is not a runtime primitive — it is a node declaring two or
    more ``depends_on`` entries, which `get_ready()` withholds until every one
    of them is `done()`. Single-dependency nodes are excluded: a chain waiting
    on its one predecessor is just the run being in progress, and reporting it
    as a barrier would make every running graph look blocked.
    """
    for step in steps:
        deps = [d for d in (step.get("depends_on") or []) if d]
        if len(deps) < 2:
            continue
        if status_by_step_id.get(step["id"]) in DONE_STEP_STATUSES:
            continue
        waiting_on = [
            d for d in deps if status_by_step_id.get(d) not in DONE_STEP_STATUSES
        ]
        if waiting_on:
            return {"step": step, "waiting_on": waiting_on}
    return None


def _label(step_id: str, steps_by_id: dict, fallback: str = "") -> str:
    """Human name for a step id, falling back to the id itself."""
    step = steps_by_id.get(step_id) or {}
    return str(step.get("name") or fallback or step_id)


def _pick_run(runs: list) -> dict | None:
    """The most actionable active run: a parked gate outranks a running graph.

    Runs arrive newest-first, so within a status the most recent one wins.
    """
    for status in ("awaiting_approval", "running", "pending"):
        for run in runs:
            if run["status"] == status:
                return run
    return None


# ---------------------------------------------------------------------------
# Graph run status check
# ---------------------------------------------------------------------------


def _check_graph_status(project_id: str) -> dict | None:
    """Summarize the project's most actionable in-flight graph run.

    Returns an advisory dict, or None when the project has no active run —
    which is the common case, and the reason this stays quiet in a chat that
    has nothing to do with Studio.
    """
    try:
        conn = get_connection()
    except Exception as exc:
        logger.debug("No database connection for graph status: %s", exc)
        return None

    try:
        if not _table_exists(conn, "studio_workflow_runs"):
            return None

        placeholders = ", ".join(["%s"] * len(ACTIVE_RUN_STATUSES))
        runs = conn.execute(
            "SELECT run_id, workflow_id, workflow_name, status "
            "FROM studio_workflow_runs "
            f"WHERE project_id = %s AND status IN ({placeholders}) "
            "ORDER BY started_at DESC",
            (project_id, *ACTIVE_RUN_STATUSES),
        ).fetchall()

        run = _pick_run([dict(r) for r in runs])
        if not run:
            return None

        run_id = run["run_id"]
        step_rows = []
        if _table_exists(conn, "studio_workflow_run_steps"):
            step_rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT step_run_id, step_id, step_name, status "
                    "FROM studio_workflow_run_steps WHERE run_id = %s "
                    "ORDER BY started_at ASC",
                    (run_id,),
                ).fetchall()
            ]

        template = _template_steps(conn, run["workflow_id"])
    except Exception as exc:
        logger.debug("Error checking graph runs: %s", exc)
        return None
    finally:
        conn.close()

    return _build_advisory(run, step_rows, template)


def _build_advisory(run: dict, step_rows: list, template: list) -> dict | None:
    """Render one run's node/barrier/gate state into an advisory dict.

    Split out from the query so the phrasing is testable without a database.
    """
    if not step_rows:
        # The run row exists but no node has been dispatched yet — either it is
        # still `pending`, or a worker started within the last moment. Reporting
        # the template's joins here would announce a barrier on a graph where
        # nothing has happened.
        return None

    steps_by_id = {s["id"]: s for s in template}
    status_by_step_id = {}
    for row in step_rows:
        # A resumed run replays earlier steps, so a step_id can appear more than
        # once; the last row ordered by started_at is the live one.
        status_by_step_id[row["step_id"]] = row["status"]

    total = len(template) or len(status_by_step_id)
    done = sum(1 for s in status_by_step_id.values() if s in DONE_STEP_STATUSES)
    running = [r for r in step_rows if r["status"] == "running"]
    gate = next((r for r in step_rows if r["status"] == GATE_STEP_STATUS), None)

    name = run.get("workflow_name") or run["workflow_id"]
    progress = f"{done}/{total} nodes done" if total else f"{done} nodes done"

    if gate:
        gate_name = _label(gate["step_id"], steps_by_id, gate.get("step_name") or "")
        return {
            "gap_id": "graph_gate_pending",
            "severity": "high",
            "message": (
                f"Graph run '{name}' is paused at approval gate '{gate_name}' — "
                f"{progress}. The run stays parked until the gate is decided."
            ),
            "action": _APPROVE_ACTION.format(step_run_id=gate["step_run_id"]),
            "run_id": run["run_id"],
            "run_status": run["status"],
        }

    parts = [progress]
    if running:
        names = ", ".join(
            _label(r["step_id"], steps_by_id, r.get("step_name") or "")
            for r in running
        )
        parts.append(f"{len(running)} running ({names})")

    barrier = _find_barrier(template, status_by_step_id)
    if barrier:
        blocked = _label(barrier["step"]["id"], steps_by_id)
        waiting = ", ".join(
            _label(d, steps_by_id) for d in barrier["waiting_on"]
        )
        parts.append(f"node '{blocked}' is waiting on {waiting}")

    return {
        "gap_id": "graph_run_in_progress",
        "severity": "low",
        "message": f"Graph run '{name}' is in flight — " + "; ".join(parts) + ".",
        "run_id": run["run_id"],
        "run_status": run["status"],
    }


# ---------------------------------------------------------------------------
# Hook handler
# ---------------------------------------------------------------------------


def handle(context: dict) -> dict:
    """chat_message_after handler — inject graph run status advisory.

    Args:
        context: dict with keys context_id, role, content, turn_number,
                 and optionally project_id.

    Returns:
        context dict, possibly with ``graph_advisory`` key added.
    """
    context_id = context.get("context_id", "")
    turn_number = context.get("turn_number", 0)
    # Runs created without an explicit project land on 'default'
    # (workflow_runner.start_run), so an unscoped chat sees those rather than
    # nothing at all.
    project_id = context.get("project_id") or "default"

    # Only process assistant responses
    if context.get("role") != "assistant":
        return context

    # Cooldown check
    if not _should_advise(context_id, turn_number):
        return context

    advisory = _check_graph_status(project_id)
    if not advisory:
        return context

    _record_advisory(context_id, turn_number)

    result = dict(context)
    result["graph_advisory"] = advisory
    return result


# ---------------------------------------------------------------------------
# Extension registration metadata
# ---------------------------------------------------------------------------

NAME = "graph_execution_chat"
PRIORITY = 31
ALLOW_MODIFICATION = True
DESCRIPTION = "Surface Studio graph run node/barrier/gate status in chat (hgx-doc-01)"

EXTENSION_HOOKS = {
    "chat_message_after": {
        "handler": handle,
        "name": NAME,
        "priority": PRIORITY,
        "allow_modification": ALLOW_MODIFICATION,
        "description": DESCRIPTION,
    },
}
