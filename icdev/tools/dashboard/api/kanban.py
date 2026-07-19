from __future__ import annotations
# CUI // SP-CTI
"""Kanban Task Board API — CRUD for task cards on the dashboard Kanban."""

import json
import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List
from pathlib import Path

from flask import Blueprint, jsonify, request

from tools.awareness.value_scorer import annotate_tasks_with_value
from tools.db.storage import get_connection, sql_placeholder
from tools.dashboard.sse_manager import sse_manager
from tools.kanban.gates import is_manual_gate

try:
    from tools.kanban.des_audit_logger import DESAuditLogger as _DESAuditLogger
    _des_logger = _DESAuditLogger()
except Exception:
    _des_logger = None

kanban_api = Blueprint("kanban_api", __name__, url_prefix="/api/kanban")


def _utcnow():
    return datetime.now(timezone.utc).isoformat()


def _notify_task_done(task_id: str, title: str):
    """Mirror genesis scheduler notification for API-driven done transitions."""
    try:
        from tools.genesis.reflexes.kanban import _send_notification
        _send_notification({"id": task_id, "title": title}, event="done")
    except Exception:
        # Fallback: dashboard-only notification if genesis module unavailable
        try:
            conn = get_connection()
            conn.execute(
                "INSERT INTO notifications (id, title, message, severity, source, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    f"notif-kanban-{task_id}-done-{_utcnow()[:19]}",
                    f"Task done: {title}",
                    f"Kanban task '{title}' is completed.",
                    "success",
                    "kanban_api",
                    _utcnow(),
                ),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass


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
            "WHERE task_id = %s "
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
            "VALUES (%s, %s, %s, %s, %s)",
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

    try:
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
    except Exception:
        # Migration 025 not yet applied — degrade gracefully.
        # Tasks are still returned; attempt_count / reaper fields stay None.
        pass


def _annotate_task_tags(conn, tasks: list) -> None:
    """Tier 2: attach tags list to each task. Falls back gracefully if table absent."""
    for t in tasks:
        t["tags"] = []
    if not tasks:
        return
    task_ids = [t["id"] for t in tasks]
    ph = ",".join(["?" for _ in task_ids])
    try:
        rows = conn.execute(
            f"SELECT tt.task_id, tg.id, tg.name, tg.color "  # nosec B608
            f"FROM kanban_task_tags tt "
            f"JOIN kanban_tags tg ON tg.id = tt.tag_id "
            f"WHERE tt.task_id IN ({ph}) "
            f"ORDER BY tg.name",
            task_ids,
        ).fetchall()
        tag_map: dict = {}
        for r in rows:
            d = dict(r)
            tid = d["task_id"]
            tag_map.setdefault(tid, []).append({"id": d["id"], "name": d["name"], "color": d["color"]})
        for t in tasks:
            t["tags"] = tag_map.get(t["id"], [])
    except Exception:
        pass  # table may not exist on older DBs — degrade gracefully


_GATE_REFUSAL = (
    "This task is a manual-mode gate: a sentinel held in_progress by design so its "
    "dependents never auto-dispatch. Changing its status releases them. Use "
    "`python -m tools.kanban.cli --set-status {} done` if you really mean to release it."
)


def _external_landing_refusal(task_id: str, new_status, current_status):
    """Refuse `done` for an external-repo task whose work has not landed in ITS repo.

    Returns a Flask (response, code) tuple, or None to proceed.

    ## Why this is not bypassable

    The first live compass dispatch produced a PHANTOM COMPLETION. The agent built the
    work, pushed `kanban/prem-rpt-07` to compass, and then marked the task `done` through
    this API with `bypass_verification: true`, reason: "COMPASS repo, not ICDev: it has
    no CI and ICDev's coherence checker doesn't apply."

    Every word of that is TRUE, and the conclusion is wrong. It is true because the
    dispatcher's own instruction says it (see _external_repo_brief). ICDev's verification
    suite genuinely does not apply in compass — so the agent reasonably concluded the
    done-gate did not either, bypassed it, and the task went green with the work sitting
    on an unmerged branch nobody would look at again.

    The bypass exists for "ICDev's CodeLens/Coherence/E2E suite could not run". It was
    never meant to mean "this task's work does not have to land anywhere".

    So for an external task the gate is a DIFFERENT question, and it is one that has a
    factual answer in every repo: **is the work on the target repo's origin/<base>?**
    That is not an ICDev-shaped check, it needs no CI, and `bypass_verification` does not
    reach it. A task is done when its work landed. Nothing else counts.
    """
    if new_status != "done" or current_status == "done":
        return None

    try:
        from tools.genesis.reflexes.kanban import (
            _branch_has_unmerged_commits,
            _task_base_branch,
            _task_repo_target,
        )

        target = _task_repo_target(task_id)
        if target is None or not target.is_external:
            return None  # ICDev tasks keep the existing gates, unchanged.

        if not _branch_has_unmerged_commits(task_id):
            return None  # Nothing unmerged — it landed (or there was nothing to land).

        base = _task_base_branch(task_id)
    except Exception:  # noqa: BLE001
        # Fail OPEN on infrastructure errors, exactly as _branch_has_unmerged_commits
        # does: an unreachable git must never wedge every task's completion.
        return None

    return jsonify({
        "error": "external_work_not_landed",
        "detail": (
            f"Task {task_id} builds in the {target.name!r} repo, and its branch "
            f"kanban/{task_id} has commits that are NOT on {target.name}'s "
            f"origin/{base}. The work has not landed — it is sitting on a branch.\n\n"
            f"This is NOT bypassable, and `bypass_verification` does not reach it. That "
            f"flag means 'ICDev's verification suite could not run here', which is TRUE "
            f"in {target.name} and irrelevant: a task is done when its work landed, and "
            f"nothing else counts.\n\n"
            f"Open a PR against {target.name} and let it merge. The scheduler marks the "
            f"task done once the commits are on origin/{base}."
        ),
        "repo": target.name,
        "base_branch": base,
        "branch": f"kanban/{task_id}",
    }), 409


def _gate_refusal(task_id: str, title, new_status, current_status):
    """Refuse any board-driven status change to a manual-mode gate.

    Returns a Flask (response, code) tuple to return, or None to proceed.

    A gate has MORE THAN ONE DOOR: /move, PATCH /tasks/<id>, and /tasks/bulk-move
    can all write `status`. Guarding only /move is what let a PATCH complete
    prem-gate-00 on 2026-07-12 and promote all 28 gated tasks (3 reached dispatch)
    before it was caught. Every writer calls this.
    """
    if new_status is None or new_status == current_status:
        return None
    if not is_manual_gate(task_id, title):
        return None
    return jsonify({
        "error": "Manual-mode gate status cannot be changed from the board",
        "detail": _GATE_REFUSAL.format(task_id),
    }), 409


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
    # Cap backlog+scheduled rows — 771 backlog rows × 3 LEFT JOINs + correlated
    # subquery per row hangs the browser. Default 100; pass backlog_limit=0 to disable.
    try:
        backlog_limit = int(request.args.get("backlog_limit") or 100)
    except (ValueError, TypeError):
        backlog_limit = 100
    conn = get_connection()
    if hasattr(conn, "set_security_context"):
        conn.set_security_context(None)  # rls-bypass: kanban_tasks has no classification/tenant_id columns
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
            "dep.status AS depends_on_status, "
            "kv.phantom_ratio AS phantom_ratio "
            "FROM kanban_tasks kt "
            "LEFT JOIN oracle_predictions op "
            "ON kt.source_prediction_id = op.id "
            "LEFT JOIN kanban_tasks dep "
            "ON kt.depends_on_task_id = dep.id "
            "LEFT JOIN kanban_verifications kv "
            "ON kv.id = ("
            "  SELECT id FROM kanban_verifications "
            "  WHERE task_id = kt.id "
            "  ORDER BY verified_at DESC LIMIT 1"
            ") "
        )
        if status_filter:
            rows = conn.execute(
                f"{select}WHERE kt.status = %s ORDER BY {order}",  # nosec B608
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
            queue_sql = (
                f"{select}WHERE kt.status IN ('backlog','scheduled') "
                f"ORDER BY {priority_case}, kt.created_at ASC"  # nosec B608
            )
            if backlog_limit > 0:
                queue_sql += f" LIMIT {backlog_limit}"  # nosec B608
            queue_rows = conn.execute(queue_sql).fetchall()
            # in_progress + suggested are always returned in full so they
            # never get buried by a cap on the done bucket (which can be
            # thousands of rows). Only "done" is capped.
            active_rows = conn.execute(
                f"{select}WHERE kt.status IN ('in_progress','suggested','token_exhausted',"
                "'validating','pr_opened','ci_failed','merge_conflict',"
                "'changes_requested','failed','needs_decomposition','decomposed') "
                f"ORDER BY {priority_case}, kt.created_at DESC"  # nosec B608
            ).fetchall()
            done_sql = (
                f"{select}WHERE kt.status = 'done' "
                f"ORDER BY {priority_case}, kt.created_at DESC"  # nosec B608
            )
            if done_limit > 0:
                done_sql += f" LIMIT {done_limit}"  # nosec B608
            done_rows = conn.execute(done_sql).fetchall()
            rows = list(queue_rows) + list(active_rows) + list(done_rows)
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

        # Manual-mode gates are SENTINELS, not work. They sit in_progress forever by
        # design, so the board rendered them with a live "Running 81m" timer and a
        # reaper progress bar — indistinguishable from a hung task. Flag them so the
        # UI can say what they actually are, and count what each one is holding back.
        _gate_ids = {
            t["id"] for t in tasks if is_manual_gate(t.get("id"), t.get("title"))
        }
        _holding: dict[str, int] = {gid: 0 for gid in _gate_ids}
        for t in tasks:
            dep = t.get("depends_on_task_id")
            if dep in _holding and t.get("status") != "done":
                _holding[dep] += 1
        for t in tasks:
            t["is_manual_gate"] = t["id"] in _gate_ids
            t["gate_holding"] = _holding.get(t["id"], 0)

        # Annotate every row with oracle_value + oracle_dup_count. The
        # scorer is safe on non-Oracle tasks (null confidence → value
        # 0.0, dup_count 1), so the field is always present and the UI
        # can sort without special-casing. Annotation is done across the
        # full returned set so dedup counts are stable regardless of the
        # status filter.
        annotate_tasks_with_value(tasks)
        _annotate_in_progress_tasks(conn, tasks)
        _annotate_task_tags(conn, tasks)

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


@kanban_api.route("/tasks/<task_id>/pipeline", methods=["GET"])
def task_pipeline(task_id):
    """Delivery-pipeline view for a task: per-stage states, gate outcomes, and
    the status-transition timeline (read-only; surfaces existing gates).

    ``?live=1`` additionally attaches live PR/CI state via gh (best-effort;
    omitted when gh / network is unavailable, so the default view stays fast
    and air-gap-safe).
    """
    try:
        from tools.kanban import pipeline as _pipeline
        data = _pipeline.assemble(task_id)
        if str(request.args.get("live", "")).lower() in ("1", "true", "yes"):
            pr = _pipeline.resolve_pr_live(task_id)
            if pr:
                data["pr"] = pr
        return jsonify(data)
    except Exception as exc:  # noqa: BLE001 - endpoint must not 500 the board
        return jsonify({"error": str(exc), "task_id": task_id}), 500


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
        "SELECT depends_on_task_id FROM kanban_tasks WHERE id = %s",
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
    if hasattr(conn, "set_security_context"):
        conn.set_security_context(None)  # rls-bypass: kanban_tasks has no classification/tenant_id columns
    try:
        # Validate all deps via DFS cycle detection
        ok, err = _check_dependency_cycle_dfs(task_id, dep_ids, conn)
        if not ok:
            return jsonify({"error": err}), 400
        conn.execute(
            "INSERT INTO kanban_tasks "
            "(id, title, description, task_type, priority, "
            "status, scheduled_at, executor_type, depends_on_task_id, "
            "start_date, target_date, "
            "created_at, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
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
                data.get("start_date"),
                data.get("target_date"),
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
            import threading
            from tools.project.kanban_project_sync import sync_projects
            threading.Thread(target=sync_projects, daemon=True).start()
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
    ph = sql_placeholder(conn)
    try:
        existing = conn.execute("SELECT * FROM kanban_tasks WHERE id = %s", (task_id,)).fetchone()
        if not existing:
            return jsonify({"error": "Task not found"}), 404

        _existing = dict(existing)
        _refusal = _gate_refusal(
            task_id, _existing.get("title"), data.get("status"), _existing.get("status")
        )
        if _refusal:
            return _refusal

        # An external task is done when its work is on ITS repo's origin/<base>.
        # Not bypassable — see _external_landing_refusal.
        _landing = _external_landing_refusal(
            task_id, data.get("status"), _existing.get("status")
        )
        if _landing:
            return _landing

        allowed = (
            "title",
            "description",
            "task_type",
            "priority",
            "status",
            "scheduled_at",
            "executor_type",
            "depends_on_task_id",
            "start_date",
            "target_date",
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
                sets.append(f"{field} = {ph}")
                vals.append(data[field])

        if not sets:
            return jsonify({"error": "No fields to update"}), 400

        # Auto-set completed_at when moving to done
        if data.get("status") == "done" and existing["status"] != "done":
            sets.append(f"completed_at = {ph}")
            vals.append(_utcnow())
        # Clear completed_at if moving out of done
        elif data.get("status") and data["status"] != "done" and existing["status"] == "done":
            sets.append("completed_at = NULL")

        sets.append(f"updated_at = {ph}")
        vals.append(_utcnow())
        vals.append(task_id)

        conn.execute(
            f"UPDATE kanban_tasks SET {', '.join(sets)} WHERE id = %s",  # nosec B608 -- table/column names are internal constants, not user input
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
        existing = conn.execute(
            "SELECT id, title FROM kanban_tasks WHERE id = %s", (task_id,)
        ).fetchone()
        if not existing:
            return jsonify({"error": "Task not found"}), 404
        # Deleting a gate does not release its dependents (_deps_satisfied fails
        # closed on a missing parent) — it STRANDS them: they can never be promoted
        # again, by anything. That is quieter than a release but just as wrong.
        if is_manual_gate(task_id, dict(existing).get("title")):
            return jsonify({
                "error": "Manual-mode gate cannot be deleted from the board",
                "detail": (
                    "Deleting this sentinel would permanently strand its dependents: "
                    "a task whose parent row is missing never satisfies its dependency "
                    "and can never be promoted again."
                ),
            }), 409
        conn.execute("DELETE FROM kanban_tasks WHERE id = %s", (task_id,))
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
            "SELECT status, title FROM kanban_tasks WHERE id = %s", (task_id,)
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


@kanban_api.route("/tasks/<task_id>/heartbeat", methods=["POST"])
def task_heartbeat(task_id):
    """Hermes-style heartbeat: agent pings this while running to prove it is alive.

    The scheduler's zombie-reclaim sweep demotes tasks that go silent for
    >2h back to token_exhausted so another attempt can be dispatched.
    Returns 409 if task is not in_progress.
    """
    conn = get_connection()
    if hasattr(conn, "set_security_context"):
        conn.set_security_context(None)  # rls-bypass: kanban_tasks has no tenant_id column; internal system table
    try:
        row = conn.execute(
            "SELECT status FROM kanban_tasks WHERE id = %s", (task_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "Task not found"}), 404
        if dict(row)["status"] != "in_progress":
            return jsonify({
                "error": "Task is not in_progress",
                "status": dict(row)["status"],
            }), 409
        now = _utcnow()
        conn.execute(
            "UPDATE kanban_tasks SET last_heartbeat_at = %s, updated_at = %s WHERE id = %s",
            (now, now, task_id),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"status": "ok", "task_id": task_id, "heartbeat_at": now}), 200


@kanban_api.route("/tasks/<task_id>/handoff", methods=["POST"])
def task_handoff(task_id):
    """Hermes-style structured handoff: executor submits completion summary + metadata JSON.

    Stored on kanban_tasks (last_run_summary, last_run_metadata) AND on the
    most-recent kanban_executions row so downstream dependent tasks can
    consume the machine-parseable output of their parent.

    Body: {"summary": "...", "metadata": {...any JSON...}}
    """
    import json as _json
    data = request.get_json(force=True, silent=True) or {}
    summary = (data.get("summary") or "").strip()
    metadata = data.get("metadata")
    if metadata is not None and not isinstance(metadata, (dict, list)):
        return jsonify({"error": "metadata must be a JSON object or array"}), 400

    metadata_str = _json.dumps(metadata) if metadata is not None else None

    conn = get_connection()
    if hasattr(conn, "set_security_context"):
        conn.set_security_context(None)  # rls-bypass: kanban_tasks has no tenant_id column; internal system table
    try:
        row = conn.execute(
            "SELECT id FROM kanban_tasks WHERE id = %s", (task_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "Task not found"}), 404

        now = _utcnow()
        conn.execute(
            "UPDATE kanban_tasks "
            "SET last_run_summary = %s, last_run_metadata = %s, updated_at = %s "
            "WHERE id = %s",
            (summary or None, metadata_str, now, task_id),
        )
        exec_row = conn.execute(
            "SELECT id FROM kanban_executions WHERE task_id = %s "
            "ORDER BY created_at DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        if exec_row:
            conn.execute(
                "UPDATE kanban_executions SET run_summary = %s, run_metadata = %s WHERE id = %s",
                (summary or None, metadata_str, dict(exec_row)["id"]),
            )
        conn.commit()
    finally:
        conn.close()

    try:
        sse_manager.broadcast({"action": "task_handoff", "task_id": task_id}, "kanban")
    except Exception:
        pass
    return jsonify({"status": "ok", "task_id": task_id}), 200


@kanban_api.route("/tasks/<task_id>/subscribe", methods=["POST"])
def task_subscribe(task_id):
    """Register a webhook subscription for terminal events on this task.
    Body: {"channel": "webhook"|"slack"|"teams", "target": "<url>", "events": ["done","token_exhausted"]}
    """
    data = request.get_json(force=True, silent=True) or {}
    channel = (data.get("channel") or "webhook").strip()
    target = (data.get("target") or "").strip()
    events_raw = data.get("events") or ["done", "token_exhausted"]
    if not target:
        return jsonify({"error": "target URL is required"}), 400
    if isinstance(events_raw, list):
        events_str = ",".join(str(e).strip() for e in events_raw if e)
    else:
        events_str = str(events_raw)

    conn = get_connection()
    if hasattr(conn, "set_security_context"):
        conn.set_security_context(None)  # rls-bypass: kanban_task_subscriptions has no tenant_id column; internal system table
    try:
        row = conn.execute("SELECT id FROM kanban_tasks WHERE id = %s", (task_id,)).fetchone()
        if not row:
            return jsonify({"error": "Task not found"}), 404
        sub_id = f"sub-{uuid.uuid4().hex[:12]}"
        now = _utcnow()
        conn.execute(
            "INSERT INTO kanban_task_subscriptions (id, task_id, channel, target, events, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sub_id, task_id, channel, target, events_str, now),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"status": "subscribed", "subscription_id": sub_id, "task_id": task_id, "events": events_str}), 201


@kanban_api.route("/tasks/<task_id>/subscriptions", methods=["GET"])
def list_subscriptions(task_id):
    """List all webhook subscriptions for a task."""
    conn = get_connection()
    if hasattr(conn, "set_security_context"):
        conn.set_security_context(None)  # rls-bypass: kanban_task_subscriptions has no tenant_id column; internal system table
    try:
        rows = conn.execute(
            "SELECT id, channel, target, events, created_at "
            "FROM kanban_task_subscriptions WHERE task_id = %s ORDER BY created_at",
            (task_id,),
        ).fetchall()
        return jsonify({"subscriptions": [dict(r) for r in rows]}), 200
    finally:
        conn.close()


@kanban_api.route("/subscriptions/<sub_id>", methods=["DELETE"])
def delete_subscription(sub_id):
    """Remove a webhook subscription."""
    conn = get_connection()
    if hasattr(conn, "set_security_context"):
        conn.set_security_context(None)  # rls-bypass: kanban_task_subscriptions has no tenant_id column; internal system table
    try:
        conn.execute("DELETE FROM kanban_task_subscriptions WHERE id = %s", (sub_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"status": "deleted", "subscription_id": sub_id}), 200


@kanban_api.route("/tasks/specify", methods=["POST"])
def specify_task():
    """Inline spec enrichment: LLM rewrites a rough title+description into
    a structured goal, approach, and acceptance criteria.

    Body: {"title": "...", "description": "...", "task_id": "<optional — saves back to task>"}
    Returns: {"goal": "...", "approach": "...", "acceptance_criteria": "..."}
    """
    import json as _json
    data = request.get_json(force=True, silent=True) or {}
    title = (data.get("title") or "").strip()
    description = (data.get("description") or "").strip()
    task_id = (data.get("task_id") or "").strip() or None

    if not title:
        return jsonify({"error": "title is required"}), 400

    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        prompt = (
            f"Rewrite this rough task into a structured specification.\n\n"
            f"Title: {title}\n"
            f"Description: {description or '(none)'}\n\n"
            "Return ONLY valid JSON:\n"
            '{"goal": "one-sentence goal statement", '
            '"approach": "2-4 sentence implementation approach", '
            '"acceptance_criteria": "bullet-point acceptance criteria, one per line"}'
        )
        req = LLMRequest(
            system_prompt=(
                "You are a senior software engineer writing precise task specifications. "
                "Return valid JSON only."
            ),
            messages=[{"role": "user", "content": prompt}],
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            temperature=0.3,
            skip_injection_scan=True,
            classification="CUI",
        )
        resp = LLMRouter().invoke("task_specify", req)
        if not resp or not resp.content:
            return jsonify({"error": "LLM returned no content"}), 502

        raw = resp.content.strip()
        import re as _re
        raw = _re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=_re.MULTILINE).strip()
        spec = _json.loads(raw)
    except Exception as exc:
        return jsonify({"error": f"Spec generation failed: {exc}"}), 500

    # Optionally save acceptance_criteria back to the task
    if task_id and spec.get("acceptance_criteria"):
        try:
            conn = get_connection()
            if hasattr(conn, "set_security_context"):
                conn.set_security_context(None)  # rls-bypass: kanban_tasks has no tenant_id column; internal system table
            try:
                conn.execute(
                    "UPDATE kanban_tasks SET acceptance_criteria = %s, updated_at = %s WHERE id = %s",
                    (spec["acceptance_criteria"], _utcnow(), task_id),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass  # best-effort save

    return jsonify(spec), 200


@kanban_api.route("/tasks/<task_id>/judge", methods=["POST"])
def judge_task(task_id):
    """Goal-mode judge: evaluate task output against acceptance_criteria.

    Body: {"output": "<task output text>"}
    Returns: {"passed": true/false, "reasoning": "..."}
    """
    import json as _json
    data = request.get_json(force=True, silent=True) or {}
    output_text = (data.get("output") or "").strip()

    conn = get_connection()
    if hasattr(conn, "set_security_context"):
        conn.set_security_context(None)  # rls-bypass: kanban_tasks has no tenant_id column; internal system table
    try:
        row = conn.execute(
            "SELECT title, acceptance_criteria FROM kanban_tasks WHERE id = %s", (task_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "Task not found"}), 404
        d = dict(row)
        criteria = (d.get("acceptance_criteria") or "").strip()
        if not criteria:
            return jsonify({"passed": True, "reasoning": "No acceptance criteria defined"}), 200
    finally:
        conn.close()

    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest
        import re as _re

        prompt = (
            f"Evaluate whether the task output meets the acceptance criteria.\n\n"
            f"ACCEPTANCE CRITERIA:\n{criteria}\n\n"
            f"TASK OUTPUT (last 3000 chars):\n{output_text[-3000:] if output_text else '(no output)'}\n\n"
            'Return ONLY valid JSON: {"passed": true/false, "reasoning": "..."}'
        )
        req = LLMRequest(
            system_prompt="You are a quality acceptance evaluator. Return valid JSON only.",
            messages=[{"role": "user", "content": prompt}],
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            temperature=0.0,
            skip_injection_scan=True,
            classification="CUI",
        )
        resp = LLMRouter().invoke("acceptance_judge", req)
        if not resp or not resp.content:
            return jsonify({"passed": True, "reasoning": "Judge unavailable"}), 200

        raw = _re.sub(
            r"^```(?:json)?\s*|\s*```$", "", resp.content.strip(), flags=_re.MULTILINE
        ).strip()
        result = _json.loads(raw)
        return jsonify({"passed": bool(result.get("passed", True)), "reasoning": str(result.get("reasoning", ""))}), 200
    except Exception as exc:
        return jsonify({"passed": True, "reasoning": f"Judge error: {exc}"}), 200


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
    ph = sql_placeholder(conn)
    try:
        # Gather source_prediction_id for dismiss path before we
        # UPDATE so we can mark the predictions in the same transaction.
        # title is selected so the manual-gate guard below can match on the title
        # marker, not just the id suffix.
        rows = conn.execute(
            "SELECT id, title, status, source_prediction_id FROM kanban_tasks "
            f"WHERE id IN ({','.join([ph] * len(task_ids))})",  # nosec B608 -- placeholders only
            tuple(task_ids),
        ).fetchall()
        by_id = {dict(r)["id"]: dict(r) for r in rows}

        for tid in task_ids:
            existing = by_id.get(tid)
            if not existing:
                failed.append({"id": tid, "error": "not found"})
                continue
            # A gate swept up in a bulk move is still a released gate. Skip it and
            # report it, rather than failing the whole batch.
            if is_manual_gate(tid, existing.get("title")) and new_status != existing.get("status"):
                failed.append({"id": tid, "error": "manual-mode gate — refused"})
                continue
            try:
                sql = f"UPDATE kanban_tasks SET status = {ph}, updated_at = {ph}"
                vals = [new_status, now]
                if new_status == "done" and existing["status"] != "done":
                    sql += f", completed_at = {ph}"
                    vals.append(now)
                elif new_status != "done" and existing["status"] == "done":
                    sql += ", completed_at = NULL"
                sql += f" WHERE id = {ph}"
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
                            "WHERE id = %s AND outcome IN ('pending', '', NULL)",
                            (existing["source_prediction_id"],),
                        )
                    except Exception:
                        # Postgres will reject `IN (..., NULL)`, fall back
                        # to the portable form that treats NULL as pending.
                        try:
                            conn.execute(
                                "UPDATE oracle_predictions "
                                "SET outcome = 'dismissed' "
                                "WHERE id = %s "
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
    """Move suggested cards to backlog, with optional value/confidence/rule gates.

    Body (all optional):
        {
          "min_confidence": 0.90,   # only promote cards with oracle_confidence >= N
          "min_value": 1.0,         # only promote cards with oracle_value >= N
          "rule": "route_not_listed" # only promote cards matching this oracle_rule
        }

    When no body is provided, promotes ALL non-quarantined suggested cards
    (legacy behaviour preserved for backward compat).

    Returns ``{"promoted": N, "filtered": M, "new_status": "backlog"}``.
    Used by the "Promote All" button on the Suggested column header.
    """
    data = request.get_json(force=True, silent=True) or {}
    min_confidence = data.get("min_confidence")
    min_value = data.get("min_value")
    rule_filter = data.get("rule")

    now = _utcnow()
    conn = get_connection()
    ph = sql_placeholder(conn)
    try:
        # Fetch suggested cards with oracle metadata (same JOIN as list_tasks)
        select = (
            "SELECT kt.id, kt.title, "
            "op.confidence AS oracle_confidence, "
            "op.prediction_text AS oracle_proposed_action, "
            "op.lens_name AS oracle_lens, "
            "op.prediction_type AS oracle_prediction_type "
            "FROM kanban_tasks kt "
            "LEFT JOIN oracle_predictions op "
            "ON kt.source_prediction_id = op.id "
            "WHERE kt.status = 'suggested' "
            f"AND (kt.last_failure_reason IS NULL OR kt.last_failure_reason NOT LIKE {ph}) "
        )
        rows = conn.execute(select, ("QUARANTINED by self_debug%",)).fetchall()
        tasks = [dict(r) for r in rows]

        # Normalise oracle_rule exactly as list_tasks does
        for t in tasks:
            ptype = (t.get("oracle_prediction_type") or "")
            lens = (t.get("oracle_lens") or "")
            if ptype.startswith("gap::") or ptype.startswith("regression::"):
                t["oracle_rule"] = ptype.split("::", 1)[1]
            elif lens and lens != "internal_awareness":
                t["oracle_rule"] = lens
            else:
                t["oracle_rule"] = lens or ""
            if t["oracle_rule"] and t["oracle_rule"] != "internal_awareness":
                t["oracle_lens"] = t["oracle_rule"]

        # Compute oracle_value so we can gate on it
        annotate_tasks_with_value(tasks)

        filtered = 0
        eligible_ids = []
        for t in tasks:
            # Rule filter
            if rule_filter and t.get("oracle_rule") != rule_filter:
                filtered += 1
                continue
            # Confidence filter
            conf = t.get("oracle_confidence")
            if min_confidence is not None:
                if conf is None or conf < float(min_confidence):
                    filtered += 1
                    continue
            # Value filter
            val = t.get("oracle_value")
            if min_value is not None:
                if val is None or val < float(min_value):
                    filtered += 1
                    continue
            eligible_ids.append(t["id"])

        count = len(eligible_ids)
        if count == 0:
            return jsonify({
                "promoted": 0,
                "filtered": filtered,
                "message": "No suggested cards matched the promotion gate",
            })

        # Batch update by IDs (safe cap at 1000 per existing bulk_move limit)
        ph = ",".join(["%s"] * len(eligible_ids))
        conn.execute(
            f"UPDATE kanban_tasks SET status = 'backlog', updated_at = %s "  # nosec B608
            f"WHERE id IN ({ph})",
            (now, *eligible_ids),
        )
        conn.commit()

        try:
            sse_manager.broadcast(
                {"action": "bulk_promoted", "count": count, "filtered": filtered},
                "kanban",
            )
        except Exception:
            pass
        return jsonify({"promoted": count, "filtered": filtered, "new_status": "backlog"})
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
    ph = sql_placeholder(conn)
    if hasattr(conn, "set_security_context"):
        conn.set_security_context(None)  # rls-bypass: kanban_tasks has no classification/tenant_id columns
    try:
        existing = conn.execute("SELECT status, title FROM kanban_tasks WHERE id = %s", (task_id,)).fetchone()
        if not existing:
            return jsonify({"error": "Task not found"}), 404

        _e = dict(existing)
        _refusal = _gate_refusal(task_id, _e.get("title"), new_status, _e.get("status"))
        if _refusal:
            return _refusal

        # An external task is done when its work is on ITS repo's origin/<base>.
        # Checked BEFORE the bypass branch below, because bypass must not reach it.
        _landing = _external_landing_refusal(task_id, new_status, _e.get("status"))
        if _landing:
            return _landing

        # guard-dep: block done transition if depends_on_task_id parent is not done.
        # Mirrors the _parent_is_done check in kanban.py _move_task so the HTTP
        # path (used by Claude CLI subprocess) enforces the same dependency gate.
        moving_to_done = new_status == "done" and existing["status"] != "done"
        if moving_to_done:
            dep_row = conn.execute(
                "SELECT depends_on_task_id FROM kanban_tasks WHERE id = %s",
                (task_id,),
            ).fetchone()
            # dict() first: on PostgreSQL a row is a RealDictRow (which has .get),
            # but on the SQLite fallback it is a sqlite3.Row, which does not.
            # Calling .get() straight on the row is an AttributeError on one
            # backend and fine on the other.
            parent_id = dict(dep_row or {}).get("depends_on_task_id")
            parent_status = None
            if parent_id:
                parent_row = conn.execute(
                    "SELECT status FROM kanban_tasks WHERE id = %s",
                    (parent_id,),
                ).fetchone()
                parent_status = (parent_row or {}).get("status")
            if parent_id and parent_status not in ("done", "decomposed", None):
                    return jsonify({
                        "error": "dependency_not_done",
                        "detail": (
                            f"Cannot mark task done: dependency {parent_id!r} "
                            f"is still {parent_status!r}. Complete the parent task first."
                        ),
                        "depends_on_task_id": parent_id,
                        "parent_status": parent_status,
                    }), 409

        # guard-22: block direct transitions to "done" unless verification
        # passed (or operator explicitly bypasses with a reason).
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

        sql = f"UPDATE kanban_tasks SET status = {ph}, updated_at = {ph}"
        vals = [new_status, now]
        if new_status == "done" and existing["status"] != "done":
            sql += f", completed_at = {ph}"
            vals.append(now)
            if bypass:
                sql += ", completed_via_bypass = 1"
        elif new_status != "done" and existing["status"] == "done":
            sql += ", completed_at = NULL, completed_via_bypass = 0"
        sql += f" WHERE id = {ph}"
        vals.append(task_id)

        conn.execute(sql, tuple(vals))

        # Record transition for kv-viz reaper-bar / TIF counter.
        # state_machine.py does this for scheduler-driven transitions; the
        # HTTP move path previously skipped it, leaving current_attempt_started_at=None.
        try:
            import secrets as _sec
            conn.execute(
                "INSERT INTO kanban_status_transitions "
                "(id, task_id, from_status, to_status, actor, reason, recorded_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    "kst-" + _sec.token_hex(6),
                    task_id,
                    existing["status"],
                    new_status,
                    "dashboard",
                    bypass_reason or None,
                    now,
                ),
            )
        except Exception:
            pass  # Best-effort; annotator degrades gracefully if row absent

        conn.commit()

        # Notify on done transitions (matches genesis scheduler behavior)
        if moving_to_done:
            _notify_task_done(task_id, existing.get("title") or task_id)

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


@kanban_api.route("/tasks/<task_id>/comments", methods=["GET"])
def list_comments(task_id):
    """Return comments for a task, oldest first."""
    conn = get_connection()
    try:
        if not conn.execute("SELECT id FROM kanban_tasks WHERE id = %s", (task_id,)).fetchone():
            return jsonify({"error": "Task not found"}), 404
        try:
            rows = conn.execute(
                "SELECT id, author, body, created_at FROM kanban_task_comments "
                "WHERE task_id = %s ORDER BY created_at ASC",
                (task_id,),
            ).fetchall()
            return jsonify({"comments": [dict(r) for r in rows]})
        except Exception:
            return jsonify({"comments": []})
    finally:
        conn.close()


@kanban_api.route("/tasks/<task_id>/comments", methods=["POST"])
def add_comment(task_id):
    """Add a comment to a task."""
    data = request.get_json(force=True, silent=True) or {}
    body = (data.get("body") or "").strip()
    author = (data.get("author") or "user").strip() or "user"
    if not body:
        return jsonify({"error": "body is required"}), 400

    conn = get_connection()
    try:
        if not conn.execute("SELECT id FROM kanban_tasks WHERE id = %s", (task_id,)).fetchone():
            return jsonify({"error": "Task not found"}), 404
        comment_id = f"kc-{uuid.uuid4().hex[:12]}"
        now = _utcnow()
        conn.execute(
            "INSERT INTO kanban_task_comments (id, task_id, author, body, created_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            (comment_id, task_id, author[:64], body[:2000], now),
        )
        conn.commit()
        return jsonify({"status": "created", "id": comment_id, "created_at": now}), 201
    finally:
        conn.close()


@kanban_api.route("/last-update", methods=["GET"])
def last_update():
    """Return the most recently completed task — used by the dashboard completion poller.

    Clients poll this every 15s; when `last_update` advances they trigger a
    full project-progress refresh without waiting for the 5-minute cycle.

    Returns:
        {last_update: ISO|null, task_id: str|null, task_title: str|null,
         completed_count: int}
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, title, completed_at FROM kanban_tasks "
            "WHERE completed_at IS NOT NULL ORDER BY completed_at DESC LIMIT 1"
        ).fetchone()
        count_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM kanban_tasks WHERE status = 'done'"
        ).fetchone()
        count = count_row["cnt"] if count_row else 0

        # Include latest notification as a change signal so the 15-second poller
        # also fires on in_progress transitions, not only on task completion.
        notif_row = conn.execute(
            "SELECT MAX(created_at) AS latest FROM notifications WHERE source = 'genesis.kanban'"
        ).fetchone()
        latest_notif = str(notif_row["latest"]) if notif_row and notif_row["latest"] else None

        task_ts = None
        task_id = None
        task_title = None
        if row:
            row = dict(row)
            ca = row["completed_at"]
            task_ts = ca.isoformat() if hasattr(ca, "isoformat") else str(ca) if ca else None
            task_id = row["id"]
            task_title = row["title"]

        # Use whichever timestamp is more recent
        candidates = [t for t in (task_ts, latest_notif) if t]
        last_update = max(candidates) if candidates else None

        return jsonify({
            "last_update": last_update,
            "task_id": task_id,
            "task_title": task_title,
            "completed_count": count,
        })
    finally:
        conn.close()


# ── Tier 2: Dependency DAG ────────────────────────────────────────────────

@kanban_api.route("/deps-graph", methods=["GET"])
def deps_graph():
    """Return the full dependency graph as nodes + edges for Mermaid rendering.

    Nodes: all tasks that appear in at least one edge (or requested via ?all=1).
    Edges: rows from kanban_task_deps junction table.
    """
    include_all = request.args.get("all", "0") == "1"
    conn = get_connection()
    try:
        edges = []
        node_ids = set()
        try:
            rows = conn.execute(
                "SELECT task_id, depends_on_id FROM kanban_task_deps"
            ).fetchall()
            for r in rows:
                d = dict(r)
                edges.append({"from": d["task_id"], "to": d["depends_on_id"]})
                node_ids.add(d["task_id"])
                node_ids.add(d["depends_on_id"])
        except Exception:
            pass

        # Also include scalar depends_on_task_id links not in junction table
        try:
            scalar_rows = conn.execute(
                "SELECT id, depends_on_task_id FROM kanban_tasks "
                "WHERE depends_on_task_id IS NOT NULL"
            ).fetchall()
            for r in scalar_rows:
                d = dict(r)
                edge = {"from": d["id"], "to": d["depends_on_task_id"]}
                if edge not in edges:
                    edges.append(edge)
                node_ids.add(d["id"])
                node_ids.add(d["depends_on_task_id"])
        except Exception:
            pass

        if include_all:
            all_ids = conn.execute("SELECT id FROM kanban_tasks").fetchall()
            for r in all_ids:
                node_ids.add(dict(r)["id"] if hasattr(r, "keys") else r[0])

        nodes = []
        if node_ids:
            ph = ",".join(["?" for _ in node_ids])
            task_rows = conn.execute(
                f"SELECT id, title, status, priority FROM kanban_tasks WHERE id IN ({ph})",  # nosec B608
                list(node_ids),
            ).fetchall()
            nodes = [dict(r) for r in task_rows]

        return jsonify({"nodes": nodes, "edges": edges})
    finally:
        conn.close()


# ── Tier 2: Tag System ────────────────────────────────────────────────────

@kanban_api.route("/tags", methods=["GET"])
def list_tags():
    """Return all available tags."""
    conn = get_connection()
    try:
        try:
            rows = conn.execute(
                "SELECT id, name, color, created_at FROM kanban_tags ORDER BY name"
            ).fetchall()
            return jsonify({"tags": [dict(r) for r in rows]})
        except Exception:
            return jsonify({"tags": []})
    finally:
        conn.close()


@kanban_api.route("/tags", methods=["POST"])
def create_tag():
    """Create a new tag. Body: {name, color?}"""
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()[:64]
    color = (data.get("color") or "#6b7280").strip()[:16]
    if not name:
        return jsonify({"error": "name is required"}), 400

    tag_id = f"tag-{uuid.uuid4().hex[:10]}"
    conn = get_connection()
    try:
        try:
            conn.execute(
                "INSERT INTO kanban_tags (id, name, color, created_at) VALUES (%s, %s, %s, %s)",
                (tag_id, name, color, _utcnow()),
            )
            conn.commit()
        except Exception as exc:
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                return jsonify({"error": f"Tag '{name}' already exists"}), 409
            raise
        try:
            sse_manager.broadcast({"action": "tag_created", "tag": {"id": tag_id, "name": name, "color": color}}, "kanban")
        except Exception:
            pass
        return jsonify({"status": "created", "id": tag_id}), 201
    finally:
        conn.close()


@kanban_api.route("/tags/<tag_id>", methods=["DELETE"])
def delete_tag(tag_id):
    """Delete a tag and its task associations."""
    conn = get_connection()
    try:
        if not conn.execute("SELECT id FROM kanban_tags WHERE id = %s", (tag_id,)).fetchone():
            return jsonify({"error": "Tag not found"}), 404
        conn.execute("DELETE FROM kanban_task_tags WHERE tag_id = %s", (tag_id,))
        conn.execute("DELETE FROM kanban_tags WHERE id = %s", (tag_id,))
        conn.commit()
        return jsonify({"status": "deleted", "id": tag_id})
    finally:
        conn.close()


@kanban_api.route("/tasks/<task_id>/tags", methods=["GET"])
def list_task_tags(task_id):
    """Return tags assigned to a task."""
    conn = get_connection()
    try:
        try:
            rows = conn.execute(
                "SELECT t.id, t.name, t.color FROM kanban_tags t "
                "JOIN kanban_task_tags tt ON tt.tag_id = t.id "
                "WHERE tt.task_id = %s ORDER BY t.name",
                (task_id,),
            ).fetchall()
            return jsonify({"tags": [dict(r) for r in rows]})
        except Exception:
            return jsonify({"tags": []})
    finally:
        conn.close()


@kanban_api.route("/tasks/<task_id>/tags", methods=["POST"])
def add_task_tag(task_id):
    """Assign a tag to a task. Body: {tag_id}"""
    data = request.get_json(force=True, silent=True) or {}
    tag_id = (data.get("tag_id") or "").strip()
    if not tag_id:
        return jsonify({"error": "tag_id is required"}), 400
    conn = get_connection()
    try:
        if not conn.execute("SELECT id FROM kanban_tasks WHERE id = %s", (task_id,)).fetchone():
            return jsonify({"error": "Task not found"}), 404
        if not conn.execute("SELECT id FROM kanban_tags WHERE id = %s", (tag_id,)).fetchone():
            return jsonify({"error": "Tag not found"}), 404
        try:
            conn.execute(
                "INSERT INTO kanban_task_tags (task_id, tag_id, created_at) VALUES (%s, %s, %s)",
                (task_id, tag_id, _utcnow()),
            )
            conn.commit()
        except Exception as exc:
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                return jsonify({"status": "already_assigned"}), 200
            raise
        return jsonify({"status": "assigned"}), 201
    finally:
        conn.close()


@kanban_api.route("/tasks/<task_id>/tags/<tag_id>", methods=["DELETE"])
def remove_task_tag(task_id, tag_id):
    """Remove a tag from a task."""
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM kanban_task_tags WHERE task_id = %s AND tag_id = %s",
            (task_id, tag_id),
        )
        conn.commit()
        return jsonify({"status": "removed"})
    finally:
        conn.close()


# ── Executor chain toggle ───────────────────────────────────────────────

def _read_env_file(key: str) -> str | None:
    """Read a key from the .env file, or None if absent."""
    env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{key}="):
            return stripped[len(key) + 1 :]
    return None


def _update_env_file(key: str, value: str) -> None:
    """Update or append a key in the .env file."""
    env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    found = False
    new_lines = []
    for line in lines:
        if line.strip().startswith(f"{key}="):
            new_lines.append(f"{key}={value}")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"{key}={value}")
    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


@kanban_api.route("/settings/executor-chain", methods=["GET", "POST"])
def executor_chain_setting():
    """Read or update the global ICDEV_KANBAN_EXECUTOR_CHAIN env override."""
    if request.method == "GET":
        chain = os.environ.get("ICDEV_KANBAN_EXECUTOR_CHAIN", "")
        if not chain:
            chain = _read_env_file("ICDEV_KANBAN_EXECUTOR_CHAIN") or ""
        if not chain:
            return jsonify({"chain": None, "message": "Using fallback from args/strategos_config.yaml"})
        return jsonify({"chain": [x.strip() for x in chain.split(",") if x.strip()]})

    data = request.get_json(force=True, silent=True) or {}
    chain_list = data.get("chain")
    if not isinstance(chain_list, list) or not chain_list:
        return jsonify({"error": "chain must be a non-empty list of executor names"}), 400
    valid = {"claude_cli", "gitlab", "github_actions", "ollama_local"}
    invalid = [x for x in chain_list if x not in valid]
    if invalid:
        return jsonify({"error": f"Invalid executors: {invalid}. Valid: {sorted(valid)}"}), 400
    chain_str = ",".join(chain_list)
    try:
        _update_env_file("ICDEV_KANBAN_EXECUTOR_CHAIN", chain_str)
        os.environ["ICDEV_KANBAN_EXECUTOR_CHAIN"] = chain_str
        return jsonify({"status": "updated", "chain": chain_list})
    except Exception as exc:
        return jsonify({"error": str(exc)[:200]}), 500


@kanban_api.route("/iqe-query", methods=["POST"])
def kanban_iqe_query():
    """Natural-language IQE query against Kanban collections."""
    import logging as _log
    import tools.iqe.adapters.core_kanban  # noqa: F401 — registers kanban.* collections
    from tools.iqe.nl_to_iqe import nl_to_iqe
    from tools.iqe.parser import Parser
    from tools.iqe.executor import execute_query

    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question is required"}), 400

    collections = ["kanban.tasks", "kanban.epics"]
    iqe_str = ""
    try:
        result = nl_to_iqe(question, collections)
        iqe_str = result.get("iqe", "")
        explanation = result.get("explanation", "")
        ast = Parser().parse(iqe_str)
        conn = get_connection()
        try:
            rows = execute_query(ast, conn)
        finally:
            conn.close()
        return jsonify({"ok": True, "iqe": iqe_str, "explanation": explanation,
                        "results": rows, "row_count": len(rows)})
    except Exception as exc:
        _log.getLogger(__name__).warning("kanban IQE error: %s", exc)
        return jsonify({"error": str(exc), "iqe": iqe_str}), 500


# ---------------------------------------------------------------------------
# Plan ingestion helpers — preview and create from markdown PRD / plan text
# ---------------------------------------------------------------------------

def _parse_plan_markdown(markdown: str):
    """Extract candidate task titles from markdown headings and list items.

    Handles:
      - ATX headings: #, ##, ###, #### (any depth)
      - Bold-only lines used as headings: **Title**
      - Unordered list items: -, *, + (with optional checkbox)
      - Ordered list items: 1., 2., 10., etc.
    """
    import re as _re
    tasks = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        title = None
        # ATX headings: any number of leading # characters
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip().strip("*_").strip()
        # Unordered list items: -, *, + with optional checkbox [x]/[ ]
        elif stripped[:2] in ("- ", "* ", "+ "):
            raw = stripped[2:].strip()
            raw = _re.sub(r"^\[[xX ]?\]\s*", "", raw)
            title = raw.strip("*_").strip()
        # Ordered list items: one or more digits followed by ". "
        elif _re.match(r"^\d+\.\s", stripped):
            title = _re.split(r"^\d+\.\s+", stripped, maxsplit=1)[-1].strip().strip("*_").strip()
        # Bold-only lines used as section headings: **Title**
        elif stripped.startswith("**") and stripped.endswith("**") and len(stripped) > 4:
            title = stripped[2:-2].strip()
        if title and len(title) > 3:
            tasks.append(title)
    seen = set()
    unique = []
    for t in tasks:
        key = t.lower()
        if key not in seen:
            seen.add(key)
            unique.append(t)
    return unique


def _plan_priority(title: str) -> str:
    """Heuristic priority based on keywords."""
    lowered = title.lower()
    if any(k in lowered for k in ("critical", "security", "compliance", "sast", "timeline", "budget", "deadline")):
        return "high"
    return "medium"


@kanban_api.route("/preview-plan", methods=["POST"])
def preview_plan():
    """Return a preview of tasks that would be created from a markdown plan."""
    data = request.get_json(silent=True) or {}
    markdown = (data.get("markdown") or "").strip()
    if not markdown:
        return jsonify({"error": "markdown is required"}), 400
    titles = _parse_plan_markdown(markdown)
    tasks = [{"title": t, "priority": _plan_priority(t)} for t in titles]
    return jsonify({"count": len(tasks), "tasks": tasks})


@kanban_api.route("/from-plan", methods=["POST"])
def create_from_plan():
    """Create Kanban backlog tasks extracted from a markdown plan."""
    data = request.get_json(silent=True) or {}
    markdown = (data.get("markdown") or "").strip()
    if not markdown:
        return jsonify({"error": "markdown is required"}), 400
    titles = _parse_plan_markdown(markdown)
    if not titles:
        return jsonify({"error": "No tasks could be extracted from the plan"}), 400
    conn = get_connection()
    now = _utcnow()
    created = 0
    try:
        for t in titles:
            task_id = f"task-plan-{uuid.uuid4().hex[:12]}"
            priority = _plan_priority(t)
            conn.execute(
                "INSERT INTO kanban_tasks "
                "(id, title, description, task_type, priority, "
                "status, scheduled_at, executor_type, depends_on_task_id, "
                "start_date, target_date, "
                "created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    task_id,
                    t,
                    "",
                    "build",
                    priority,
                    "backlog",
                    None,
                    "claude_cli",
                    None,
                    None,
                    None,
                    now,
                    now,
                ),
            )
            created += 1
        conn.commit()
        try:
            sse_manager.broadcast(
                {"action": "plan_imported", "tasks_created": created},
                "kanban",
            )
        except Exception:
            pass
        return jsonify({"tasks_created": created})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@kanban_api.route("/lessons", methods=["GET"])
def list_lessons():
    """Return lesson_learned memory entries for kanban tasks.

    Query params:
      pattern — filter by pattern (e.g. token_exhaustion)
      systemic — 1/0 filter
      days — look-back window (default 30)
      limit — cap results (default 200)
    """
    pattern_filter = request.args.get("pattern")
    systemic_filter = request.args.get("systemic")
    try:
        days = int(request.args.get("days") or 30)
    except (ValueError, TypeError):
        days = 30
    try:
        limit = int(request.args.get("limit") or 200)
    except (ValueError, TypeError):
        limit = 200

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conn = get_connection()
    ph = sql_placeholder(conn)
    try:
        sql = (
            "SELECT id, content, created_at, importance "
            f"FROM memory_entries WHERE type = {ph} AND created_at >= {ph}"
        )
        params: List[Any] = ["lesson_learned", since]
        if pattern_filter:
            sql += f" AND content LIKE {ph}"
            params.append(f'%"pattern": "{pattern_filter}"%')
        if systemic_filter is not None:
            sql += f" AND content LIKE {ph}"
            target = "true" if systemic_filter in ("1", "true", "yes") else "false"
            params.append(f'%"is_systemic": {target}%')
        sql += f" ORDER BY created_at DESC LIMIT {ph}"
        params.append(limit)
        rows = conn.execute(sql, tuple(params)).fetchall()
        lessons: List[Dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            payload: Dict[str, Any] = {}
            try:
                payload = json.loads(d.get("content") or "{}")
            except Exception:
                pass
            lessons.append({
                "id": d.get("id"),
                "created_at": d.get("created_at"),
                "importance": d.get("importance"),
                "task_id": payload.get("task_id"),
                "task_title": payload.get("task_title"),
                "outcome": payload.get("outcome"),
                "pattern": payload.get("pattern"),
                "category": payload.get("category"),
                "failure_count": payload.get("failure_count"),
                "recurrence_score": payload.get("recurrence_score"),
                "is_systemic": payload.get("is_systemic"),
                "recommendation": payload.get("recommendation"),
            })
        return jsonify({"lessons": lessons, "total": len(lessons), "days": days})
    finally:
        conn.close()
