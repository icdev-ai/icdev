# CUI // SP-CTI
"""Kanban Task Board API — CRUD for task cards on the dashboard Kanban."""

import uuid
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from tools.awareness.value_scorer import annotate_tasks_with_value
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
    # Optional sort override. For the Suggested lane operators want to rank
    # by oracle confidence or by the derived "value" score (confidence ×
    # rule_weight × dedup_boost). `created_at` preserves the historical
    # "most recent first" behavior. The sort applies after fetch for
    # `value` and `confidence` because value is computed client-side by
    # annotate_tasks_with_value, and confidence lives on the JOINed
    # oracle_predictions row which SQL ORDER BY can't drive portably.
    sort_param = (request.args.get("sort") or "").strip().lower()
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
            other_rows = conn.execute(
                f"{select}WHERE kt.status NOT IN ('backlog','scheduled') "
                f"ORDER BY {priority_case}, kt.created_at DESC"  # nosec B608
            ).fetchall()
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


@kanban_api.route("/tasks/<task_id>/move", methods=["POST"])
def move_task(task_id):
    """Move a task to a new status column."""
    data = request.get_json(force=True)
    new_status = data.get("status")
    if new_status not in _VALID_STATUSES:
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
