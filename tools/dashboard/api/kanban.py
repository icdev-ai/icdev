# CUI // SP-CTI
"""Kanban Task Board API — CRUD for task cards on the dashboard Kanban."""

import uuid
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from tools.db.storage import get_connection
from tools.dashboard.sse_manager import sse_manager

kanban_api = Blueprint("kanban_api", __name__, url_prefix="/api/kanban")


def _utcnow():
    return datetime.now(timezone.utc).isoformat()


def _gen_id():
    return f"task-{uuid.uuid4().hex[:10]}"


@kanban_api.route("/tasks", methods=["GET"])
def list_tasks():
    """Return all kanban tasks, optionally filtered by status.

    For suggested tasks, LEFT JOINs oracle_predictions to include the exact
    confidence value and proposed_action from the originating prediction.
    """
    status_filter = request.args.get("status")
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
            other_rows = conn.execute(
                f"{select}WHERE kt.status NOT IN ('backlog','scheduled') "
                f"ORDER BY {priority_case}, kt.created_at DESC"  # nosec B608
            ).fetchall()
            rows = list(queue_rows) + list(other_rows)
        tasks = [dict(r) for r in rows]
        # Stringify datetimes for JSON + compute is_blocked
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
        return jsonify({"tasks": tasks, "total": len(tasks)})
    finally:
        conn.close()


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

    task_id = _gen_id()
    now = _utcnow()
    depends_on = data.get("depends_on_task_id") or None
    conn = get_connection()
    try:
        ok, err = _validate_dependency(conn, task_id, depends_on)
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
                depends_on,
                now,
                now,
            ),
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
        # Validate dependency change before staging the UPDATE
        if "depends_on_task_id" in data:
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


@kanban_api.route("/tasks/<task_id>/move", methods=["POST"])
def move_task(task_id):
    """Move a task to a new status column."""
    data = request.get_json(force=True)
    new_status = data.get("status")
    valid = ("backlog", "scheduled", "in_progress", "done", "token_exhausted", "suggested")
    if new_status not in valid:
        return jsonify({"error": "Invalid status"}), 400

    now = _utcnow()
    conn = get_connection()
    try:
        existing = conn.execute("SELECT status FROM kanban_tasks WHERE id = ?", (task_id,)).fetchone()
        if not existing:
            return jsonify({"error": "Task not found"}), 404

        sql = "UPDATE kanban_tasks SET status = ?, updated_at = ?"
        vals = [new_status, now]
        if new_status == "done" and existing["status"] != "done":
            sql += ", completed_at = ?"
            vals.append(now)
        elif new_status != "done" and existing["status"] == "done":
            sql += ", completed_at = NULL"
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
