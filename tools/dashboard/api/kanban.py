# CUI // SP-CTI
"""Kanban Task Board API — CRUD for task cards on the dashboard Kanban."""

import os
import uuid
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from tools.awareness.value_scorer import annotate_tasks_with_value
from tools.db.storage import get_connection
from tools.dashboard.sse_manager import sse_manager

try:
    from tools.kanban.des_audit_logger import DESAuditLogger as _DESAuditLogger
    _des_logger = _DESAuditLogger()
except Exception:
    _des_logger = None

kanban_api = Blueprint("kanban_api", __name__, url_prefix="/api/kanban")


def _utcnow():
    return datetime.now(timezone.utc).isoformat()


def _gen_id():
    return f"task-{uuid.uuid4().hex[:10]}"


def _verification_gate_enabled() -> bool:
    # Operators can disable the gate by setting ICDEV_KANBAN_VERIFY_GATE=false
    # (e.g. during bulk migrations). Default ON — phantom completions are the
    # reason this gate exists.
    return os.environ.get("ICDEV_KANBAN_VERIFY_GATE", "true").strip().lower() not in (
        "0", "false", "no", "off"
    )


def _latest_verification(conn, task_id: str):
    """Return the most recent kanban_verifications row for a task, or None."""
    try:
        row = conn.execute(
            "SELECT result, codelens_passed, coherence_passed, "
            "e2e_ran, e2e_passed, companion_synced, verified_at, reason "
            "FROM kanban_verifications "
            "WHERE task_id = ? "
            "ORDER BY verified_at DESC LIMIT 1",
            (task_id,),
        ).fetchone()
    except Exception:
        return None
    return dict(row) if row else None


def _verification_passed(row) -> bool:
    """True iff every applicable gate is green on the latest verification.

    Logic:
      * result must be 'passed'
      * codelens_passed must be truthy (1) when set
      * coherence_passed must be truthy (1) when set
      * if e2e_ran, e2e_passed must be truthy
      * companion_synced is best-effort — not required
    """
    if not row or row.get("result") != "passed":
        return False
    for key in ("codelens_passed", "coherence_passed"):
        val = row.get(key)
        if val is not None and not val:
            return False
    if row.get("e2e_ran"):
        if not row.get("e2e_passed"):
            return False
    return True


def _log_verification_bypass(conn, task_id: str, reason: str) -> None:
    """Append an audit row recording an operator-approved bypass."""
    try:
        conn.execute(
            "INSERT INTO kanban_verifications "
            "(id, task_id, verified_at, result, reason) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                f"kv-bypass-{uuid.uuid4().hex[:12]}",
                task_id,
                _utcnow(),
                "bypassed",
                reason[:500],
            ),
        )
        conn.commit()
    except Exception:
        # Audit failure must not block the operator-intended move; the
        # bypass is logged downstream via dashboard SSE regardless.
        pass


def _annotate_in_progress_tasks(conn, tasks: list) -> None:
    """kv-viz-01: Enrich in_progress tasks with dispatch visibility fields.

    Adds three fields to every task (null for non-in_progress):
      attempt_count              — how many times scheduler has dispatched this task
      current_attempt_started_at — ISO timestamp when the current attempt began
      last_reaped_reason         — reason from most recent reap/demotion (null if never reaped)
    """
    for t in tasks:
        t["attempt_count"] = None
        t["current_attempt_started_at"] = None
        t["last_reaped_reason"] = None

    ip_ids = [t["id"] for t in tasks if t.get("status") == "in_progress"]
    if not ip_ids:
        return

    ph = ",".join(["?" for _ in ip_ids])

    # attempt_count + current_attempt_started_at from in_progress arrivals
    for row in conn.execute(
        f"SELECT task_id, COUNT(*) AS cnt, MAX(recorded_at) AS latest "  # nosec B608
        f"FROM kanban_status_transitions "
        f"WHERE task_id IN ({ph}) AND to_status = 'in_progress' "
        f"GROUP BY task_id",
        ip_ids,
    ).fetchall():
        d = dict(row)
        tid = d["task_id"]
        for t in tasks:
            if t.get("id") == tid:
                t["attempt_count"] = d.get("cnt") or 0
                sa = d.get("latest")
                t["current_attempt_started_at"] = (
                    sa.isoformat() if hasattr(sa, "isoformat") else (str(sa) if sa else None)
                )
                break

    # last_reaped_reason — most recent demotion out of in_progress (not to done)
    seen: set = set()
    for row in conn.execute(
        f"SELECT task_id, reason, recorded_at "  # nosec B608
        f"FROM kanban_status_transitions "
        f"WHERE task_id IN ({ph}) "
        f"  AND from_status = 'in_progress' "
        f"  AND to_status NOT IN ('done', 'in_progress') "
        f"ORDER BY recorded_at DESC",
        ip_ids,
    ).fetchall():
        d = dict(row)
        tid = d["task_id"]
        if tid in seen:
            continue
        seen.add(tid)
        for t in tasks:
            if t.get("id") == tid:
                t["last_reaped_reason"] = d.get("reason")
                break


@kanban_api.route("/tasks", methods=["GET"])
def list_tasks():
    """Return all kanban tasks, optionally filtered by status.

    For suggested tasks, LEFT JOINs oracle_predictions to include the exact
    confidence value and proposed_action from the originating prediction.
    """
    status_filter = request.args.get("status")
    # Optional sort override. For the Suggested lane operators want to rank
    # by oracle confidence or by the derived "value" score (confidence ×
    # rule_weight × dedup_boost). `created_at` preserves the historical
    # "most recent first" behavior. The sort applies after fetch for
    # `value` and `confidence` because value is computed client-side by
    # annotate_tasks_with_value, and confidence lives on the JOINed
    # oracle_predictions row which SQL ORDER BY can't drive portably.
    sort_param = (request.args.get("sort") or "").strip().lower()
    # Cap how many "history" tasks (done/in_progress/suggested) to return in
    # the unfiltered board view. Rendering thousands of done cards blocks the
    # browser for tens of seconds. Default 100; pass done_limit=0 to disable.
    try:
        done_limit = int(request.args.get("done_limit") or 100)
    except (ValueError, TypeError):
        done_limit = 100
    conn = get_connection()
    try:
        # Execution queue ordering: within the same priority, tasks that
        # will run first appear first. For `backlog` (queued for
        # execution) and `scheduled` (time-deferred), sort by created_at
        # ASC so the next-to-run is at the top — matches the kanban
        # reflex's _get_due_tasks() ordering in tools/genesis/reflexes/
        # kanban.py. For all other statuses (in_progress, done,
        # suggested, token_exhausted), DESC gives a "most recent first"
        # activity feed which is what operators expect.
        created_at_dir = "ASC" if status_filter in ("backlog", "scheduled") else "DESC"
        order = (
            "CASE kt.priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
            f"WHEN 'medium' THEN 2 ELSE 3 END, kt.created_at {created_at_dir}"
        )
        select = (
            "SELECT kt.*, "
            "op.confidence AS oracle_confidence, "
            "op.prediction_text AS oracle_proposed_action, "
            "op.lens_name AS oracle_lens, "
            "op.prediction_type AS oracle_prediction_type, "
            "dep.title  AS depends_on_title, "
            "dep.status AS depends_on_status "
            "FROM kanban_tasks kt "
            "LEFT JOIN oracle_predictions op "
            "ON kt.source_prediction_id = op.id "
            "LEFT JOIN kanban_tasks dep "
            "ON kt.depends_on_task_id = dep.id "
        )
        if status_filter:
            rows = conn.execute(
                f"{select}WHERE kt.status = ? ORDER BY {order}",  # nosec B608
                (status_filter,),
            ).fetchall()
        else:
            # No filter: client groups by status. For the queue-like
            # statuses (backlog, scheduled) we want ASC so the
            # next-to-run is at the top; for history-like statuses we
            # want DESC. Use two queries and concatenate — avoids a
            # DB-specific CASE in ORDER BY.
            priority_case = (
                "CASE kt.priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
                "WHEN 'medium' THEN 2 ELSE 3 END"
            )
            queue_rows = conn.execute(
                f"{select}WHERE kt.status IN ('backlog','scheduled') "
                f"ORDER BY {priority_case}, kt.created_at ASC"  # nosec B608
            ).fetchall()
            other_sql = (
                f"{select}WHERE kt.status NOT IN ('backlog','scheduled') "
                f"ORDER BY {priority_case}, kt.created_at DESC"  # nosec B608
            )
            if done_limit > 0:
                other_sql += f" LIMIT {done_limit}"  # nosec B608
            other_rows = conn.execute(other_sql).fetchall()
            rows = list(queue_rows) + list(other_rows)
        tasks = [dict(r) for r in rows]
        # Stringify datetimes for JSON + compute is_blocked + derive
        # a stable oracle_rule label.
        for t in tasks:
            for k in ("scheduled_at", "completed_at", "created_at", "updated_at"):
                if t.get(k) and hasattr(t[k], "isoformat"):
                    t[k] = t[k].isoformat()
            # Native dependency: a task is blocked whenever it has a
            # depends_on_task_id that is not yet `done`. NULL dependency
            # (no parent) → is_blocked = False, matches the listener's
            # _get_due_tasks gating exactly.
            if t.get("depends_on_task_id"):
                t["is_blocked"] = t.get("depends_on_status") != "done"
            else:
                t["is_blocked"] = False
            # Two prediction shapes coexist on the board:
            #   Legacy:  lens_name = '<rule>'   prediction_type = 'regression::<probe>' or 'gap::<rule>'
            #   New:     lens_name = 'internal_awareness'   prediction_type = 'gap::<rule>'
            # Normalize into a single ``oracle_rule`` field so the UI's
            # by-rule grouping is stable across both shapes. Strips the
            # ``gap::`` / ``regression::`` prefix so the rule name alone
            # is exposed.
            ptype = (t.get("oracle_prediction_type") or "")
            lens = (t.get("oracle_lens") or "")
            if ptype.startswith("gap::"):
                t["oracle_rule"] = ptype.split("::", 1)[1]
            elif ptype.startswith("regression::"):
                t["oracle_rule"] = ptype.split("::", 1)[1]
            elif lens and lens != "internal_awareness":
                t["oracle_rule"] = lens
            else:
                t["oracle_rule"] = lens or ""
            # Also keep the old oracle_lens field pointing at the rule
            # (not the lens name) for backward compat with existing UI
            # code that reads oracle_lens as the "group-by" key.
            if t["oracle_rule"] and t["oracle_rule"] != "internal_awareness":
                t["oracle_lens"] = t["oracle_rule"]

        # Annotate every row with oracle_value + oracle_dup_count. The
        # scorer is safe on non-Oracle tasks (null confidence → value
        # 0.0, dup_count 1), so the field is always present and the UI
        # can sort without special-casing. Annotation is done across the
        # full returned set so dedup counts are stable regardless of the
        # status filter.
        annotate_tasks_with_value(tasks)
        _annotate_in_progress_tasks(conn, tasks)

        # Apply sort override if requested. SQL-side ORDER BY can't drive
        # these cleanly because value is computed in Python and
        # confidence lives on the JOINed oracle_predictions row.
        if sort_param == "value":
            tasks.sort(
                key=lambda t: (t.get("oracle_value") or 0.0),
                reverse=True,
            )
        elif sort_param == "confidence":
            tasks.sort(
                key=lambda t: (t.get("oracle_confidence") or 0.0),
                reverse=True,
            )
        elif sort_param == "priority":
            # Priority sort: critical → high → medium → low. Within the
            # same priority class, fall through to value DESC so
            # high-priority + high-impact items always surface first.
            # Unknown priorities land at the bottom (rank 99) so schema
            # drift doesn't poison the ordering.
            _priority_rank = {
                "critical": 0,
                "high": 1,
                "medium": 2,
                "low": 3,
            }
            tasks.sort(
                key=lambda t: (
                    _priority_rank.get(t.get("priority") or "low", 99),
                    -(t.get("oracle_value") or 0.0),
                )
            )
        return jsonify({"tasks": tasks, "total": len(tasks), "done_limit": done_limit or None})
    finally:
        conn.close()


def _maybe_auto_close_parent(conn, child_task_id: str) -> None:
    """If *child_task_id* has a parent, attempt to auto-close it.

    Tries three paths in order (all best-effort — never blocks the main update):
    1. FK depends_on_task_id: child explicitly declares its parent via the FK column
    2. Naming convention: child ID matches ``{parent}-d{N}`` — parent is decomposed
    3. Cascade: naming-convention result may unblock the grandparent via FK chain
    """
    try:
        from tools.kanban.state_machine import (
            auto_close_by_naming_convention,
            auto_close_parent_if_all_children_done,
        )
        row = conn.execute(
            "SELECT depends_on_task_id FROM kanban_tasks WHERE id = %s", (child_task_id,)
        ).fetchone()
        parent_id = (dict(row).get("depends_on_task_id") if hasattr(row, "keys") else row[0]) if row else None

        # Path 1 — FK-based parent
        if parent_id:
            auto_close_parent_if_all_children_done(parent_id, conn, actor="auto_close_hook")

        # Path 2 — naming-convention decomposed parent ({parent}-d{N})
        naming_result = auto_close_by_naming_convention(child_task_id, conn, actor="auto_close_hook")

        # Path 3 — if naming-convention closed a parent, that parent may itself
        # be a child (e.g. fd-floor-02 → fd-floor-02's own depends_on parent fd-floor-03
        # now becomes unblocked), so re-run the FK sweep one level up.
        if naming_result and naming_result.applied:
            naming_parent = naming_result.task_id
            np_row = conn.execute(
                "SELECT depends_on_task_id FROM kanban_tasks WHERE id = %s", (naming_parent,)
            ).fetchone()
            np_parent = (dict(np_row).get("depends_on_task_id") if hasattr(np_row, "keys") else np_row[0]) if np_row else None
            if np_parent:
                auto_close_parent_if_all_children_done(np_parent, conn, actor="auto_close_hook_cascade")

        conn.commit()
    except Exception:
        pass  # auto-close is best-effort — never block the main update


def _check_dependency_cycle_dfs(task_id: str, new_deps: list, conn) -> tuple:
    """DFS cycle detection over kanban_task_deps junction table.

    Returns (ok: bool, error: str|None).
    Traverses the dependency graph depth-first starting from each node in
    new_deps; if we ever reach task_id we have a cycle.
    """
    if not new_deps:
        return True, None
    if task_id in new_deps:
        return False, "task cannot depend on itself"

    visited: set = set()

    def _dfs(node: str) -> bool:
        if node == task_id:
            return True  # cycle found
        if node in visited:
            return False
        visited.add(node)
        rows = conn.execute(
            "SELECT depends_on_id FROM kanban_task_deps WHERE task_id = %s",
            (node,),
        ).fetchall()
        for r in rows:
            dep = dict(r).get("depends_on_id") if hasattr(r, "keys") else r[0]
            if dep and _dfs(dep):
                return True
        return False

    for dep_id in new_deps:
        row = conn.execute("SELECT id FROM kanban_tasks WHERE id = %s", (dep_id,)).fetchone()
        if not row:
            return False, f"depends_on_task_id {dep_id!r} not found"
        if _dfs(dep_id):
            return False, f"dependency on {dep_id!r} would create a cycle"

    return True, None


def _validate_dependency(conn, task_id: str, depends_on: str):
    """Validate a proposed depends_on_task_id.

    Returns (ok: bool, error: str|None). Checks:
      * target exists in kanban_tasks
      * no self-reference
      * no 2-hop cycle (A→B→A). A full graph walk would protect against
        longer cycles, but the dashboard+listener only ever materialize
        linear phase chains in practice — 2-hop is sufficient guard
        against accidental misuse from the UI and keeps the check O(1).
    """
    if not depends_on:
        return True, None
    if depends_on == task_id:
        return False, "task cannot depend on itself"
    row = conn.execute(
        "SELECT depends_on_task_id FROM kanban_tasks WHERE id = ?",
        (depends_on,),
    ).fetchone()
    if not row:
        return False, f"depends_on_task_id {depends_on!r} not found"
    parent_dep = dict(row).get("depends_on_task_id")
    if parent_dep == task_id:
        return False, "dependency would form a 2-hop cycle"
    return True, None


@kanban_api.route("/tasks", methods=["POST"])
def create_task():
    """Create a new kanban task."""
    data = request.get_json(force=True)
    if not data.get("title"):
        return jsonify({"error": "title is required"}), 400

    task_id = data.get("id") or _gen_id()
    now = _utcnow()
    depends_on = data.get("depends_on_task_id") or None
    # Multi-parent: depends_on_task_ids list (junction table); scalar is kept for compat.
    dep_ids: list = data.get("depends_on_task_ids") or ([] if depends_on is None else [depends_on])
    # Scalar takes priority over list when both present; deduplicate.
    if depends_on and depends_on not in dep_ids:
        dep_ids = [depends_on] + dep_ids
    dep_ids = list(dict.fromkeys(d for d in dep_ids if d))  # dedupe, preserve order

    conn = get_connection()
    try:
        # Validate all deps via DFS cycle detection
        ok, err = _check_dependency_cycle_dfs(task_id, dep_ids, conn)
        if not ok:
            return jsonify({"error": err}), 400
        conn.execute(
            "INSERT INTO kanban_tasks "
            "(id, title, description, task_type, priority, "
            "status, scheduled_at, executor_type, depends_on_task_id, "
            "created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                task_id,
                data["title"],
                data.get("description", ""),
                data.get("task_type", "build"),
                data.get("priority", "medium"),
                data.get("status", "backlog"),
                data.get("scheduled_at"),
                data.get("executor_type", "claude_cli"),
                depends_on or (dep_ids[0] if dep_ids else None),
                now,
                now,
            ),
        )
        # Write all deps to junction table
        for dep_id in dep_ids:
            conn.execute(
                "INSERT INTO kanban_task_deps (task_id, depends_on_id, created_at) "
                "VALUES (%s, %s, %s) ON CONFLICT (task_id, depends_on_id) DO NOTHING",
                (task_id, dep_id, now),
            )
        conn.commit()
        try:
            sse_manager.broadcast(
                {
                    "action": "task_created",
                    "task": {
                        "id": task_id,
                        "title": data["title"],
                        "status": data.get("status", "backlog"),
                        "priority": data.get("priority", "medium"),
                        "executor_type": data.get("executor_type", "claude_cli"),
                    },
                },
                "kanban",
            )
        except Exception:
            pass  # SSE is best-effort
        try:
            from tools.project.kanban_project_sync import sync_projects
            sync_projects()
        except Exception:
            pass  # best-effort — never block task creation
        return jsonify({"status": "created", "id": task_id}), 201
    finally:
        conn.close()


@kanban_api.route("/tasks/<task_id>", methods=["PATCH"])
def update_task(task_id):
    """Update a kanban task (status, priority, title, etc.)."""
    data = request.get_json(force=True)
    conn = get_connection()
    try:
        existing = conn.execute("SELECT * FROM kanban_tasks WHERE id = ?", (task_id,)).fetchone()
        if not existing:
            return jsonify({"error": "Task not found"}), 404

        allowed = (
            "title",
            "description",
            "task_type",
            "priority",
            "status",
            "scheduled_at",
            "executor_type",
            "depends_on_task_id",
        )
        # Multi-parent deps: validate via DFS before any DB writes
        new_dep_ids: list = data.get("depends_on_task_ids") or []
        if "depends_on_task_id" in data and data["depends_on_task_id"]:
            scalar = data["depends_on_task_id"]
            if scalar not in new_dep_ids:
                new_dep_ids = [scalar] + new_dep_ids
        new_dep_ids = list(dict.fromkeys(d for d in new_dep_ids if d))
        if new_dep_ids:
            ok, err = _check_dependency_cycle_dfs(task_id, new_dep_ids, conn)
            if not ok:
                return jsonify({"error": err}), 400
        elif "depends_on_task_id" in data:
            ok, err = _validate_dependency(conn, task_id, data["depends_on_task_id"])
            if not ok:
                return jsonify({"error": err}), 400

        sets = []
        vals = []
        for field in allowed:
            if field in data:
                sets.append(f"{field} = ?")
                vals.append(data[field])

        if not sets:
            return jsonify({"error": "No fields to update"}), 400

        # Auto-set completed_at when moving to done
        if data.get("status") == "done" and existing["status"] != "done":
            sets.append("completed_at = ?")
            vals.append(_utcnow())
        # Clear completed_at if moving out of done
        elif data.get("status") and data["status"] != "done" and existing["status"] == "done":
            sets.append("completed_at = NULL")

        sets.append("updated_at = ?")
        vals.append(_utcnow())
        vals.append(task_id)

        conn.execute(
            f"UPDATE kanban_tasks SET {', '.join(sets)} WHERE id = ?",  # nosec B608 -- table/column names are internal constants, not user input
            tuple(vals),
        )
        conn.commit()

        # Multi-parent: update junction table when depends_on_task_ids provided
        if new_dep_ids:
            now_ts = _utcnow()
            conn.execute("DELETE FROM kanban_task_deps WHERE task_id = %s", (task_id,))
            for dep_id in new_dep_ids:
                conn.execute(
                    "INSERT INTO kanban_task_deps (task_id, depends_on_id, created_at) "
                    "VALUES (%s, %s, %s) ON CONFLICT (task_id, depends_on_id) DO NOTHING",
                    (task_id, dep_id, now_ts),
                )
            conn.commit()

        # km-autoclose: when a child task is marked done, check if its parent
        # can now be auto-closed (all siblings also done).
        if data.get("status") == "done":
            _maybe_auto_close_parent(conn, task_id)

        try:
            sse_manager.broadcast(
                {
                    "action": "task_updated",
                    "task_id": task_id,
                    "changes": data,
                },
                "kanban",
            )
        except Exception:
            pass  # SSE is best-effort
        return jsonify({"status": "updated", "id": task_id})
    finally:
        conn.close()


@kanban_api.route("/tasks/<task_id>", methods=["DELETE"])
def delete_task(task_id):
    """Delete a kanban task."""
    conn = get_connection()
    try:
        existing = conn.execute("SELECT id FROM kanban_tasks WHERE id = ?", (task_id,)).fetchone()
        if not existing:
            return jsonify({"error": "Task not found"}), 404
        conn.execute("DELETE FROM kanban_tasks WHERE id = ?", (task_id,))
        conn.commit()
        try:
            sse_manager.broadcast(
                {
                    "action": "task_deleted",
                    "task_id": task_id,
                },
                "kanban",
            )
        except Exception:
            pass  # SSE is best-effort
        return jsonify({"status": "deleted", "id": task_id})
    finally:
        conn.close()


@kanban_api.route("/tasks/<task_id>/message", methods=["POST"])
def inject_message(task_id):
    """OPT-62: inject a mid-run message into a running kanban task.

    Adapted from langchain-ai/open-swe (MIT) — the 'message it while it's
    running' pattern. The message is appended to a JSONL queue that the
    task's executor loop drains before each LLM call. Returns 409 Conflict
    if the task is not currently running.
    """
    data = request.get_json(force=True, silent=True) or {}
    content = (data.get("message") or "").strip()
    sender = (data.get("sender") or "user").strip() or "user"
    if not content:
        return jsonify({"error": "message is required"}), 400

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT status, title FROM kanban_tasks WHERE id = ?", (task_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return jsonify({"error": "Task not found"}), 404

    running_states = ("in_progress", "scheduled")
    if row["status"] not in running_states:
        return jsonify({
            "error": "Task is not running",
            "status": row["status"],
        }), 409

    try:
        from tools.airgap.hook_compat import queue_message
    except Exception as exc:  # pragma: no cover - defensive
        return jsonify({"error": f"hook_compat unavailable: {exc}"}), 500

    result = queue_message(task_id, content, sender=sender)
    if not result.get("queued"):
        return jsonify(result), 400

    try:
        sse_manager.broadcast({
            "action": "message_queued",
            "task_id": task_id,
            "sender": sender,
        }, "kanban")
    except Exception:
        pass  # best-effort

    return jsonify({
        "status": "queued",
        "task_id": task_id,
        "sender": sender,
        "poll_at": _utcnow(),
    }), 200


_VALID_STATUSES = (
    "backlog",
    "scheduled",
    "in_progress",
    "done",
    "token_exhausted",
    "suggested",
)


@kanban_api.route("/tasks/bulk-move", methods=["POST"])
def bulk_move_tasks():
    """Promote / dismiss many suggested cards in a single call.

    Body:
        {
          "task_ids": ["task-...", ...],
          "status":   "backlog" | "done" | other valid status
        }

    Used by the Suggested column's bulk-promote UI. Semantics:
      * ``status="backlog"`` → promote to the execution queue
      * ``status="done"``    → dismiss; additionally marks each task's
        source oracle_prediction with ``outcome='dismissed'`` so the
        suggested_card_writer will not re-create the same card on the
        next awareness cycle (see tools/awareness/suggested_card_writer.py
        filter — it already excludes outcome='dismissed').

    Returns ``{"moved": N, "failed": [ids]}``. Per-row failures are
    collected; the endpoint does not abort on the first error so that
    large bulk operations can partially succeed. Broadcast fan-out is
    emitted once per successfully moved task for SSE consumers.
    """
    data = request.get_json(force=True, silent=True) or {}
    task_ids = data.get("task_ids") or []
    new_status = data.get("status")

    if not isinstance(task_ids, list) or not task_ids:
        return jsonify({"error": "task_ids must be a non-empty list"}), 400
    if new_status not in _VALID_STATUSES:
        return jsonify({"error": "Invalid status"}), 400
    # Hard cap — operators should never bulk-move more than this in one
    # request. Prevents a runaway UI from nuking the board.
    if len(task_ids) > 1000:
        return jsonify({"error": "task_ids exceeds cap of 1000"}), 400

    now = _utcnow()
    moved = 0
    failed = []
    conn = get_connection()
    try:
        # Gather source_prediction_id for dismiss path before we
        # UPDATE so we can mark the predictions in the same transaction.
        rows = conn.execute(
            "SELECT id, status, source_prediction_id FROM kanban_tasks "
            f"WHERE id IN ({','.join(['?'] * len(task_ids))})",  # nosec B608 -- placeholders only
            tuple(task_ids),
        ).fetchall()
        by_id = {dict(r)["id"]: dict(r) for r in rows}

        for tid in task_ids:
            existing = by_id.get(tid)
            if not existing:
                failed.append({"id": tid, "error": "not found"})
                continue
            try:
                sql = "UPDATE kanban_tasks SET status = ?, updated_at = ?"
                vals = [new_status, now]
                if new_status == "done" and existing["status"] != "done":
                    sql += ", completed_at = ?"
                    vals.append(now)
                elif new_status != "done" and existing["status"] == "done":
                    sql += ", completed_at = NULL"
                sql += " WHERE id = ?"
                vals.append(tid)
                conn.execute(sql, tuple(vals))

                # Dismiss path: also mark the originating oracle_prediction
                # so it does not re-surface next awareness cycle. Best
                # effort — prediction may not exist for manually-created
                # suggested cards, that is fine.
                if new_status == "done" and existing.get("source_prediction_id"):
                    try:
                        conn.execute(
                            "UPDATE oracle_predictions "
                            "SET outcome = 'dismissed' "
                            "WHERE id = ? AND outcome IN ('pending', '', NULL)",
                            (existing["source_prediction_id"],),
                        )
                    except Exception:
                        # Postgres will reject `IN (..., NULL)`, fall back
                        # to the portable form that treats NULL as pending.
                        try:
                            conn.execute(
                                "UPDATE oracle_predictions "
                                "SET outcome = 'dismissed' "
                                "WHERE id = ? "
                                "  AND (outcome IS NULL OR outcome = '' "
                                "       OR outcome = 'pending')",
                                (existing["source_prediction_id"],),
                            )
                        except Exception as exc:
                            # Don't fail the bulk-move just because
                            # prediction bookkeeping is unhappy.
                            failed.append({"id": tid, "warning": str(exc)[:120]})
                moved += 1
            except Exception as exc:
                failed.append({"id": tid, "error": str(exc)[:200]})

        conn.commit()

        # Broadcast per-task SSE events after commit so listeners only
        # see committed state.
        for tid in task_ids:
            if any(f.get("id") == tid and "error" in f for f in failed):
                continue
            try:
                sse_manager.broadcast(
                    {
                        "action": "task_updated",
                        "task_id": tid,
                        "changes": {"status": new_status},
                    },
                    "kanban",
                )
            except Exception:
                pass

        return jsonify({
            "status": "bulk_moved",
            "moved": moved,
            "failed": failed,
            "new_status": new_status,
        })
    finally:
        conn.close()


@kanban_api.route("/tasks/promote-all", methods=["POST"])
def promote_all_suggested():
    """Move ALL suggested cards to backlog in one shot.

    No request body needed.  Returns ``{"promoted": N}``.
    Used by the "Promote All" button on the Suggested column header.
    """
    now = _utcnow()
    conn = get_connection()
    try:
        # Exclude self_debug-quarantined rows from bulk-promote: they live in
        # 'suggested' as a durable quarantine, not as awaiting-approval
        # suggestions. Promoting them re-loops the scheduler.
        rows = conn.execute(
            "SELECT id FROM kanban_tasks WHERE status = 'suggested' "
            "AND (last_failure_reason IS NULL OR last_failure_reason NOT LIKE ?)",
            ("QUARANTINED by self_debug%",),
        ).fetchall()
        count = len(rows)
        if count == 0:
            return jsonify({"promoted": 0, "message": "No suggested cards to promote"})

        conn.execute(
            "UPDATE kanban_tasks SET status = 'backlog', updated_at = ? "
            "WHERE status = 'suggested' "
            "AND (last_failure_reason IS NULL OR last_failure_reason NOT LIKE ?)",
            (now, "QUARANTINED by self_debug%"),
        )
        conn.commit()

        try:
            sse_manager.broadcast(
                {"action": "bulk_promoted", "count": count},
                "kanban",
            )
        except Exception:
            pass
        return jsonify({"promoted": count, "new_status": "backlog"})
    except Exception as exc:
        return jsonify({"error": str(exc)[:200]}), 500
    finally:
        conn.close()


@kanban_api.route("/tasks/<task_id>/move", methods=["POST"])
def move_task(task_id):
    """Move a task to a new status column.

    When moving to ``done``, the request is gated on the task having a
    passing ``kanban_verifications`` row (guard-7 — CodeLens + Coherence +
    E2E + Companion). Callers can bypass the gate by passing
    ``"bypass_verification": true`` **and** a non-empty ``"bypass_reason"``;
    every bypass is audit-logged into ``kanban_verifications``.

    The scheduler's own completion path (``_move_task`` in
    tools/genesis/reflexes/kanban.py) writes directly to the DB and is
    unaffected — it runs the full verification pipeline *before* calling
    the DB update, so the gate here only fires for external callers
    (dashboard drag-drop, curl, tests).
    """
    data = request.get_json(force=True)
    new_status = data.get("status")
    if new_status not in _VALID_STATUSES:
        return jsonify({"error": "Invalid status"}), 400

    bypass = bool(data.get("bypass_verification"))
    bypass_reason = (data.get("bypass_reason") or "").strip()

    now = _utcnow()
    conn = get_connection()
    try:
        existing = conn.execute("SELECT status FROM kanban_tasks WHERE id = ?", (task_id,)).fetchone()
        if not existing:
            return jsonify({"error": "Task not found"}), 404

        # guard-22: block direct transitions to "done" unless verification
        # passed (or operator explicitly bypasses with a reason).
        moving_to_done = new_status == "done" and existing["status"] != "done"
        if moving_to_done and _verification_gate_enabled():
            if bypass:
                if not bypass_reason:
                    return jsonify({
                        "error": "bypass_reason_required",
                        "detail": (
                            "bypass_verification=true requires a non-empty "
                            "bypass_reason (audit trail)."
                        ),
                    }), 400
                _log_verification_bypass(conn, task_id, bypass_reason)
                try:
                    if _des_logger:
                        _des_logger.log_gate_override(task_id, reason=bypass_reason, operator="operator")
                except Exception:
                    pass
            else:
                latest = _latest_verification(conn, task_id)
                passed = _verification_passed(latest)
                if not passed:
                    reason = (latest or {}).get("reason") or "no verification row"
                    return jsonify({
                        "error": "verification_required",
                        "detail": (
                            "Task has no passing kanban_verifications row. "
                            "Run the full validation suite (CodeLens + "
                            "Coherence + E2E + Companion) first, or POST "
                            "with `bypass_verification: true` and "
                            "`bypass_reason: \"...\"` to override."
                        ),
                        "last_verification": latest,
                        "last_reason": reason,
                    }), 409
                try:
                    if _des_logger:
                        _des_logger.log_verification(task_id, signals={
                            "codelens": (latest or {}).get("codelens_passed"),
                            "coherence": (latest or {}).get("coherence_passed"),
                            "e2e": (latest or {}).get("e2e_passed"),
                            "passed": passed,
                        })
                except Exception:
                    pass

        sql = "UPDATE kanban_tasks SET status = ?, updated_at = ?"
        vals = [new_status, now]
        if new_status == "done" and existing["status"] != "done":
            sql += ", completed_at = ?"
            vals.append(now)
            if bypass:
                sql += ", completed_via_bypass = 1"
        elif new_status != "done" and existing["status"] == "done":
            sql += ", completed_at = NULL, completed_via_bypass = 0"
        sql += " WHERE id = ?"
        vals.append(task_id)

        conn.execute(sql, tuple(vals))
        conn.commit()
        try:
            sse_manager.broadcast(
                {
                    "action": "task_updated",
                    "task_id": task_id,
                    "changes": {"status": new_status},
                },
                "kanban",
            )
        except Exception:
            pass  # SSE is best-effort
        return jsonify({"status": "moved", "id": task_id, "new_status": new_status})
    finally:
        conn.close()


