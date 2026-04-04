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
        order = (
            "CASE kt.priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
            "WHEN 'medium' THEN 2 ELSE 3 END, kt.created_at DESC"
        )
        select = (
            "SELECT kt.*, "
            "op.confidence AS oracle_confidence, "
            "op.proposed_action AS oracle_proposed_action, "
            "op.lens_name AS oracle_lens "
            "FROM kanban_tasks kt "
            "LEFT JOIN oracle_predictions op "
            "ON kt.source_prediction_id = op.id "
        )
        if status_filter:
            rows = conn.execute(
                f"{select}WHERE kt.status = ? ORDER BY {order}",  # nosec B608
                (status_filter,),
            ).fetchall()
        else:
            rows = conn.execute(
                f"{select}ORDER BY {order}"  # nosec B608
            ).fetchall()
        tasks = [dict(r) for r in rows]
        # Stringify datetimes for JSON
        for t in tasks:
            for k in ("scheduled_at", "completed_at", "created_at", "updated_at"):
                if t.get(k) and hasattr(t[k], "isoformat"):
                    t[k] = t[k].isoformat()
        return jsonify({"tasks": tasks, "total": len(tasks)})
    finally:
        conn.close()


@kanban_api.route("/tasks", methods=["POST"])
def create_task():
    """Create a new kanban task."""
    data = request.get_json(force=True)
    if not data.get("title"):
        return jsonify({"error": "title is required"}), 400

    task_id = _gen_id()
    now = _utcnow()
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO kanban_tasks "
            "(id, title, description, task_type, priority, "
            "status, scheduled_at, executor_type, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                task_id,
                data["title"],
                data.get("description", ""),
                data.get("task_type", "build"),
                data.get("priority", "medium"),
                data.get("status", "backlog"),
                data.get("scheduled_at"),
                data.get("executor_type", "claude_cli"),
                now,
                now,
            ),
        )
        conn.commit()
        try:
            sse_manager.broadcast({
                "action": "task_created",
                "task": {
                    "id": task_id,
                    "title": data["title"],
                    "status": data.get("status", "backlog"),
                    "priority": data.get("priority", "medium"),
                },
            }, "kanban")
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
        existing = conn.execute(
            "SELECT * FROM kanban_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if not existing:
            return jsonify({"error": "Task not found"}), 404

        allowed = (
            "title", "description", "task_type",
            "priority", "status", "scheduled_at", "executor_type",
        )
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
        elif (data.get("status") and data["status"] != "done"
              and existing["status"] == "done"):
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
            sse_manager.broadcast({
                "action": "task_updated",
                "task_id": task_id,
                "changes": data,
            }, "kanban")
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
        existing = conn.execute(
            "SELECT id FROM kanban_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if not existing:
            return jsonify({"error": "Task not found"}), 404
        conn.execute("DELETE FROM kanban_tasks WHERE id = ?", (task_id,))
        conn.commit()
        try:
            sse_manager.broadcast({
                "action": "task_deleted",
                "task_id": task_id,
            }, "kanban")
        except Exception:
            pass  # SSE is best-effort
        return jsonify({"status": "deleted", "id": task_id})
    finally:
        conn.close()


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
        existing = conn.execute(
            "SELECT status FROM kanban_tasks WHERE id = ?", (task_id,)
        ).fetchone()
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
            sse_manager.broadcast({
                "action": "task_updated",
                "task_id": task_id,
                "changes": {"status": new_status},
            }, "kanban")
        except Exception:
            pass  # SSE is best-effort
        return jsonify({"status": "moved", "id": task_id, "new_status": new_status})
    finally:
        conn.close()
