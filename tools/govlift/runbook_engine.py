# CUI // SP-CTI
"""GovLift — Runbook Engine.

Provides CRUD operations for govlift_runbook_templates.
All DB access via get_connection() — never sqlite3.connect().
SQL uses ? placeholders; StorageCursor translates to %s for PostgreSQL.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

_ICDEV_ROOT = Path(__file__).resolve().parents[2]
if str(_ICDEV_ROOT) not in sys.path:
    sys.path.insert(0, str(_ICDEV_ROOT))

from tools.db.storage import get_connection
from tools.govlift.constants import WORKLOAD_TYPES, RUNBOOK_CATEGORIES


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rbt_id() -> str:
    return "rbt-" + uuid4().hex[:10]


def _row_to_dict(row) -> dict:
    if row is None:
        return {}
    try:
        return dict(row)
    except Exception:
        return {}


_COMPLETE_STEP_STATUSES = ["completed", "failed", "skipped"]


def _rb_id() -> str:
    return "rb-" + uuid4().hex[:10]


def _exec_id() -> str:
    return "rbe-" + uuid4().hex[:10]


def _step_id() -> str:
    return "rbs-" + uuid4().hex[:10]


def create_template(
    name: str,
    category: str,
    steps: list[dict],
    description: str = "",
    workload_type: str = "web_app",
    estimated_min: int = 0,
    author: str = "",
) -> dict:
    """Insert a new runbook template and return the created record."""
    if category not in RUNBOOK_CATEGORIES:
        raise ValueError(f"category must be one of {RUNBOOK_CATEGORIES}")
    if workload_type not in WORKLOAD_TYPES:
        raise ValueError(f"workload_type must be one of {WORKLOAD_TYPES}")

    rbt_id = _rbt_id()
    now = _now()
    steps_json = json.dumps(steps)
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO govlift_runbook_templates "
            "(id, name, category, description, workload_type, author, steps_json, estimated_min, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (rbt_id, name, category, description, workload_type, author, steps_json, estimated_min, now),
        )
        conn.commit()
        return get_template(rbt_id)
    finally:
        conn.close()


def get_template(template_id: str) -> dict:
    """Return a runbook template by ID, or {} if not found."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM govlift_runbook_templates WHERE id = %s",
            (template_id,),
        ).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def list_templates(category: str | None = None, author: str | None = None) -> list[dict]:
    """Return templates filtered by optional category or author."""
    conn = get_connection()
    try:
        clauses: list[str] = []
        params: list = []
        if category:
            clauses.append("category = ?")
            params.append(category)
        if author:
            clauses.append("author = ?")
            params.append(author)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM govlift_runbook_templates {where} ORDER BY created_at DESC",
            params,
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Runbook instance CRUD
# ---------------------------------------------------------------------------


def create_runbook(template_id: str, name: str | None = None) -> dict:
    """Instantiate a runbook from a template and return the created record."""
    tpl = get_template(template_id)
    if not tpl:
        raise ValueError(f"Template '{template_id}' not found")
    steps = json.loads(tpl.get("steps_json", "[]"))
    rb_id = _rb_id()
    now = _now()
    rb_name = name or tpl["name"]
    conn = get_connection()
    try:
        conn.execute(
            # The live column is steps_count, not step_count (swp-scan-01).
            "INSERT INTO govlift_runbooks "
            "(id, template_id, name, status, steps_count, created_at, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (rb_id, template_id, rb_name, "draft", len(steps), now, now),
        )
        conn.commit()
        return get_runbook(rb_id)
    finally:
        conn.close()


def list_runbooks(status: str | None = None) -> list[dict]:
    """Return runbook instances, optionally filtered by status."""
    conn = get_connection()
    try:
        clauses: list[str] = []
        params: list = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM govlift_runbooks {where} ORDER BY created_at DESC",
            params,
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_runbook(runbook_id: str) -> dict:
    """Return a runbook instance by ID, or {} if not found."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM govlift_runbooks WHERE id = %s",
            (runbook_id,),
        ).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Execution lifecycle
# ---------------------------------------------------------------------------


def _get_latest_execution(conn, runbook_id: str) -> dict:
    row = conn.execute(
        "SELECT * FROM govlift_runbook_executions WHERE runbook_id = %s ORDER BY created_at DESC LIMIT 1",
        (runbook_id,),
    ).fetchone()
    return _row_to_dict(row)


def _get_steps(conn, execution_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM govlift_runbook_step_results WHERE execution_id = %s ORDER BY step_num",
        (execution_id,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def start_execution(runbook_id: str) -> dict:
    """Transition a draft runbook to running and activate the first step."""
    rb = get_runbook(runbook_id)
    if not rb:
        raise ValueError(f"Runbook '{runbook_id}' not found")
    if rb["status"] != "draft":
        raise ValueError(f"Cannot start runbook in status '{rb['status']}'")

    tpl = get_template(rb["template_id"])
    steps = json.loads(tpl.get("steps_json", "[]"))
    now = _now()
    exec_id = _exec_id()

    conn = get_connection()
    try:
        conn.execute(
            # govlift_runbook_executions has no created_at — started_at is the
            # creation stamp (swp-scan-01).
            "INSERT INTO govlift_runbook_executions (id, runbook_id, status, started_at) "
            "VALUES (%s,%s,%s,%s)",
            (exec_id, runbook_id, "running", now),
        )
        for i, step in enumerate(steps, start=1):
            step_status = "running" if i == 1 else "pending"
            conn.execute(
                "INSERT INTO govlift_runbook_step_results "
                "(id, execution_id, runbook_id, step_num, step_name, status, output, started_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    _step_id(), exec_id, runbook_id, i,
                    step.get("name", f"Step {i}"), step_status, "",
                    now if i == 1 else None,
                ),
            )
        conn.execute(
            "UPDATE govlift_runbooks SET status = %s, updated_at = %s WHERE id = %s",
            ("running", now, runbook_id),
        )
        conn.commit()
        step_rows = _get_steps(conn, exec_id)
        return {"status": "running", "execution_id": exec_id, "steps": step_rows}
    finally:
        conn.close()


def get_execution_status(runbook_id: str) -> dict:
    """Return the latest execution status and step list for a runbook."""
    conn = get_connection()
    try:
        exec_rec = _get_latest_execution(conn, runbook_id)
        if not exec_rec:
            return {}
        steps = _get_steps(conn, exec_rec["id"])
        return {"execution_id": exec_rec["id"], "status": exec_rec["status"], "steps": steps}
    finally:
        conn.close()


def complete_step(runbook_id: str, step_num: int, status: str, output: str = "") -> dict:
    """Mark a step outcome and advance to the next step (or close the runbook)."""
    if status not in _COMPLETE_STEP_STATUSES:
        raise ValueError(f"status must be one of {_COMPLETE_STEP_STATUSES}")

    conn = get_connection()
    try:
        exec_rec = _get_latest_execution(conn, runbook_id)
        if not exec_rec:
            raise ValueError(f"No active execution for runbook '{runbook_id}'")
        exec_id = exec_rec["id"]
        now = _now()

        conn.execute(
            "UPDATE govlift_runbook_step_results SET status = %s, output = %s, completed_at = %s "
            "WHERE execution_id = %s AND step_num = %s",
            (status, output, now, exec_id, step_num),
        )

        rb = get_runbook(runbook_id)
        step_count = rb["step_count"]

        if status == "failed":
            conn.execute(
                "UPDATE govlift_runbooks SET status = %s, failed_step = %s, updated_at = %s WHERE id = %s",
                ("failed", step_num, now, runbook_id),
            )
            conn.execute(
                "UPDATE govlift_runbook_executions SET status = %s, completed_at = %s WHERE id = %s",
                ("failed", now, exec_id),
            )
        elif status == "completed":
            next_step = step_num + 1
            if next_step <= step_count:
                conn.execute(
                    "UPDATE govlift_runbook_step_results SET status = %s, started_at = %s "
                    "WHERE execution_id = %s AND step_num = %s",
                    ("running", now, exec_id, next_step),
                )
            else:
                conn.execute(
                    "UPDATE govlift_runbooks SET status = %s, updated_at = %s WHERE id = %s",
                    ("completed", now, runbook_id),
                )
                conn.execute(
                    "UPDATE govlift_runbook_executions SET status = %s, completed_at = %s WHERE id = %s",
                    ("completed", now, exec_id),
                )

        conn.commit()
        steps = _get_steps(conn, exec_id)
        return {"execution_id": exec_id, "steps": steps}
    finally:
        conn.close()


def trigger_rollback(runbook_id: str) -> dict:
    """Mark all completed steps as failed and set the runbook to rolled_back."""
    conn = get_connection()
    try:
        exec_rec = _get_latest_execution(conn, runbook_id)
        steps = _get_steps(conn, exec_rec["id"]) if exec_rec else []
        if not steps:
            raise ValueError(f"No steps found for runbook '{runbook_id}'")

        now = _now()
        exec_id = exec_rec["id"]

        conn.execute(
            "UPDATE govlift_runbook_step_results SET status = %s, completed_at = %s "
            "WHERE execution_id = %s AND status = 'completed'",
            ("failed", now, exec_id),
        )
        conn.execute(
            "UPDATE govlift_runbooks SET status = %s, updated_at = %s WHERE id = %s",
            ("rolled_back", now, runbook_id),
        )
        conn.execute(
            "UPDATE govlift_runbook_executions SET status = %s, completed_at = %s WHERE id = %s",
            ("rolled_back", now, exec_id),
        )
        conn.commit()

        updated_steps = _get_steps(conn, exec_id)
        return {"status": "rolled_back", "execution_id": exec_id, "steps": updated_steps}
    finally:
        conn.close()
