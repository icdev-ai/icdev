#!/usr/bin/env python3
# CUI // SP-CTI
"""Workflow Loop Engine — PLAN→APPLY→UNIFY lifecycle manager (D-WF-1).

LLM-agnostic enforcement of disciplined development loops. Any AI coding
assistant calls these deterministic Python tools; the tools enforce the
discipline. Inspired by PAUL (Plan-Apply-Unify Loop) but implemented as
FORGE-compliant tools with DB-backed state.

Usage:
    python tools/workflow/loop_engine.py --create --project-id "proj-123" --phase "auth-module" --json
    python tools/workflow/loop_engine.py --plan --loop-id "wl-xxx" --summary "Implement auth" --json
    python tools/workflow/loop_engine.py --add-criteria --loop-id "wl-xxx" --given "..." --when "..." --then "..." --json
    python tools/workflow/loop_engine.py --start-apply --loop-id "wl-xxx" --json
    python tools/workflow/loop_engine.py --complete-task --loop-id "wl-xxx" --json
    python tools/workflow/loop_engine.py --verify-criterion --criterion-id "wac-xxx" --status pass --json
    python tools/workflow/loop_engine.py --start-unify --loop-id "wl-xxx" --json
    python tools/workflow/loop_engine.py --close --loop-id "wl-xxx" --json
    python tools/workflow/loop_engine.py --status --loop-id "wl-xxx" --json
    python tools/workflow/loop_engine.py --list --project-id "proj-123" --json
    python tools/workflow/loop_engine.py --abandon --loop-id "wl-xxx" --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import column_exists, get_connection
from tools.common.helpers import now_iso
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
CONFIG_PATH = BASE_DIR / "args" / "workflow_loop_config.yaml"

_HAS_YAML = False
try:
    import yaml  # type: ignore

    _HAS_YAML = True
except ImportError:
    pass

_HAS_AUDIT = False
try:
    from tools.audit.audit_logger import log_event as audit_log_event  # type: ignore

    _HAS_AUDIT = True
except (ImportError, Exception):
    pass


def _get_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py")
    conn = get_connection(db_path=str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


_ID_COUNTER = 0


def _gen_id(prefix: str) -> str:
    global _ID_COUNTER
    _ID_COUNTER += 1
    ts = now_iso()
    h = hashlib.sha256(f"{ts}-{_ID_COUNTER}-{id(object())}".encode()).hexdigest()[:12]
    return f"{prefix}-{h}"


def _audit(event_type: str, action: str, details: Optional[Dict] = None, project_id: str = "") -> None:
    if _HAS_AUDIT:
        try:
            audit_log_event(
                event_type=event_type,
                actor="workflow-engine",
                action=action,
                details=json.dumps(details) if details else None,
                project_id=project_id,
            )
        except Exception:
            pass


def _load_config() -> Dict[str, Any]:
    if not _HAS_YAML or not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Loop lifecycle operations
# ---------------------------------------------------------------------------


def create_loop(
    project_id: str,
    phase_name: str,
    loop_type: str = "build",
    created_by: str = "",
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Create a new workflow loop in PLANNING state."""
    loop_id = _gen_id("wl")
    now = now_iso()
    conn = _get_db(db_path)
    try:
        conn.execute(
            """INSERT INTO workflow_loops
               (id, project_id, phase_name, loop_type, status, created_by, created_at)
               VALUES (%s, %s, %s, %s, 'planning', %s, %s)""",
            (loop_id, project_id, phase_name, loop_type, created_by, now),
        )
        conn.commit()
        _audit(
            "workflow.loop_created",
            f"Created loop {loop_id} for {phase_name}",
            {"loop_id": loop_id, "phase_name": phase_name},
            project_id,
        )
        return {
            "loop_id": loop_id,
            "project_id": project_id,
            "phase_name": phase_name,
            "loop_type": loop_type,
            "status": "planning",
            "created_at": now,
        }
    finally:
        conn.close()


def finalize_plan(
    loop_id: str,
    summary: str,
    task_count: int = 0,
    boundaries: Optional[List[str]] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Finalize the plan for a loop, transitioning to PLANNED state."""
    conn = _get_db(db_path)
    try:
        row = conn.execute("SELECT * FROM workflow_loops WHERE id = %s", (loop_id,)).fetchone()
        if not row:
            return {"error": f"Loop {loop_id} not found"}
        if row["status"] != "planning":
            return {"error": f"Loop is in '{row['status']}' state, expected 'planning'"}

        config = _load_config()
        max_tasks = config.get("loop", {}).get("max_tasks_per_plan", 5)
        if task_count > max_tasks:
            return {"error": f"Task count {task_count} exceeds max {max_tasks}. Split into smaller plans."}

        # Check acceptance criteria requirement
        ac_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM workflow_acceptance_criteria WHERE loop_id = %s",
            (loop_id,),
        ).fetchone()["cnt"]
        min_ac = config.get("loop", {}).get("min_acceptance_criteria", 1)
        require_ac = config.get("loop", {}).get("require_acceptance_criteria", True)
        if require_ac and ac_count < min_ac:
            return {"error": f"Plan requires at least {min_ac} acceptance criteria, found {ac_count}"}

        now = now_iso()
        conn.execute(
            """UPDATE workflow_loops
               SET status = 'planned', plan_summary = %s, task_count = %s,
                   boundaries = %s, acceptance_criteria_count = %s, planned_at = %s
               WHERE id = %s""",
            (summary, task_count, json.dumps(boundaries or []), ac_count, now, loop_id),
        )
        conn.commit()
        _audit(
            "workflow.plan_finalized",
            f"Plan finalized for loop {loop_id}",
            {"loop_id": loop_id, "task_count": task_count},
            row["project_id"],
        )
        return {
            "loop_id": loop_id,
            "status": "planned",
            "task_count": task_count,
            "acceptance_criteria_count": ac_count,
            "planned_at": now,
        }
    finally:
        conn.close()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """Add column to table if missing."""
    try:
        # Backend-aware column probe (pgrt-sweep-06) — no PRAGMA/translation reliance.
        if not column_exists(conn, table, column):
            conn.execute(ddl)
    except Exception:
        pass


def add_acceptance_criterion(
    loop_id: str,
    given_text: str,
    when_text: str,
    then_text: str,
    bdd_story_id: str = "",
    cot_config: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Add a Given/When/Then acceptance criterion to a loop.

    Optionally links to a BDD story in safe_decomposition via bdd_story_id
    for traceability from loop-level AC down to requirement-level BDD.
    """
    conn = _get_db(db_path)
    try:
        row = conn.execute("SELECT * FROM workflow_loops WHERE id = %s", (loop_id,)).fetchone()
        if not row:
            return {"error": f"Loop {loop_id} not found"}
        if row["status"] not in ("planning",):
            return {"error": f"Cannot add criteria in '{row['status']}' state"}

        # Get next criterion number
        max_num = conn.execute(
            "SELECT COALESCE(MAX(criterion_number), 0) as mx FROM workflow_acceptance_criteria WHERE loop_id = %s",
            (loop_id,),
        ).fetchone()["mx"]

        crit_id = _gen_id("wac")
        now = now_iso()
        _ensure_column(
            conn,
            "workflow_acceptance_criteria",
            "cot_config",
            "ALTER TABLE workflow_acceptance_criteria ADD COLUMN cot_config TEXT",
        )
        conn.execute(
            """INSERT INTO workflow_acceptance_criteria
               (id, loop_id, criterion_number, given_text, when_text, then_text,
                bdd_story_id, status, cot_config, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s)""",
            (crit_id, loop_id, max_num + 1, given_text, when_text, then_text, bdd_story_id or None, json.dumps(cot_config) if cot_config else None, now),
        )
        conn.commit()
        return {
            "criterion_id": crit_id,
            "loop_id": loop_id,
            "criterion_number": max_num + 1,
            "status": "pending",
            "bdd_story_id": bdd_story_id or None,
            "cot_config": cot_config,
        }
    finally:
        conn.close()


def start_apply(loop_id: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Transition loop from PLANNED to APPLYING."""
    conn = _get_db(db_path)
    try:
        row = conn.execute("SELECT * FROM workflow_loops WHERE id = %s", (loop_id,)).fetchone()
        if not row:
            return {"error": f"Loop {loop_id} not found"}
        if row["status"] != "planned":
            return {"error": f"Loop is in '{row['status']}' state, expected 'planned'"}

        now = now_iso()
        conn.execute(
            "UPDATE workflow_loops SET status = 'applying', apply_started_at = %s WHERE id = %s",
            (now, loop_id),
        )
        conn.commit()
        _audit("workflow.apply_started", f"Apply started for loop {loop_id}", {"loop_id": loop_id}, row["project_id"])
        return {"loop_id": loop_id, "status": "applying", "apply_started_at": now}
    finally:
        conn.close()


def complete_task(loop_id: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Increment completed task count for a loop in APPLYING state."""
    conn = _get_db(db_path)
    try:
        row = conn.execute("SELECT * FROM workflow_loops WHERE id = %s", (loop_id,)).fetchone()
        if not row:
            return {"error": f"Loop {loop_id} not found"}
        if row["status"] != "applying":
            return {"error": f"Loop is in '{row['status']}' state, expected 'applying'"}

        new_count = row["tasks_completed"] + 1
        status = "applying"
        now = now_iso()
        if new_count >= row["task_count"]:
            status = "applied"

        conn.execute(
            "UPDATE workflow_loops SET tasks_completed = %s, status = %s, apply_completed_at = %s WHERE id = %s",
            (new_count, status, now if status == "applied" else row["apply_completed_at"], loop_id),
        )
        conn.commit()
        return {"loop_id": loop_id, "tasks_completed": new_count, "task_count": row["task_count"], "status": status}
    finally:
        conn.close()


def verify_criterion(
    criterion_id: str,
    status: str,
    evidence: str = "",
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Verify an acceptance criterion (pass/fail/skip)."""
    if status not in ("pass", "fail", "skip"):
        return {"error": f"Invalid status '{status}', must be pass/fail/skip"}

    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM workflow_acceptance_criteria WHERE id = %s",
            (criterion_id,),
        ).fetchone()
        if not row:
            return {"error": f"Criterion {criterion_id} not found"}

        now = now_iso()
        conn.execute(
            "UPDATE workflow_acceptance_criteria SET status = %s, evidence = %s, verified_at = %s WHERE id = %s",
            (status, evidence, now, criterion_id),
        )

        # Update loop counters
        loop_id = row["loop_id"]
        counts = conn.execute(
            """SELECT
                 SUM(CASE WHEN status = 'pass' THEN 1 ELSE 0 END) as pass_ct,
                 SUM(CASE WHEN status = 'fail' THEN 1 ELSE 0 END) as fail_ct,
                 SUM(CASE WHEN status = 'skip' THEN 1 ELSE 0 END) as skip_ct
               FROM workflow_acceptance_criteria WHERE loop_id = %s""",
            (loop_id,),
        ).fetchone()
        conn.execute(
            """UPDATE workflow_loops SET acceptance_pass_count = %s,
               acceptance_fail_count = %s, acceptance_skip_count = %s WHERE id = %s""",
            (counts["pass_ct"] or 0, counts["fail_ct"] or 0, counts["skip_ct"] or 0, loop_id),
        )
        conn.commit()
        return {"criterion_id": criterion_id, "status": status, "loop_id": loop_id, "verified_at": now}
    finally:
        conn.close()


def start_unify(loop_id: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Transition loop to UNIFYING state."""
    conn = _get_db(db_path)
    try:
        row = conn.execute("SELECT * FROM workflow_loops WHERE id = %s", (loop_id,)).fetchone()
        if not row:
            return {"error": f"Loop {loop_id} not found"}
        if row["status"] not in ("applied", "applying"):
            return {"error": f"Loop is in '{row['status']}' state, expected 'applied' or 'applying'"}

        now = now_iso()
        conn.execute(
            "UPDATE workflow_loops SET status = 'unifying', unify_started_at = %s WHERE id = %s",
            (now, loop_id),
        )
        conn.commit()
        return {"loop_id": loop_id, "status": "unifying", "unify_started_at": now}
    finally:
        conn.close()


def close_loop(loop_id: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Close a loop after UNIFY. Requires reconciliation record."""
    conn = _get_db(db_path)
    try:
        row = conn.execute("SELECT * FROM workflow_loops WHERE id = %s", (loop_id,)).fetchone()
        if not row:
            return {"error": f"Loop {loop_id} not found"}
        if row["status"] != "unifying":
            return {"error": f"Loop is in '{row['status']}' state, expected 'unifying'"}

        # Check reconciliation exists
        config = _load_config()
        require_unify = config.get("reconciliation", {}).get("require_unify", True)
        recon = conn.execute(
            "SELECT * FROM workflow_reconciliations WHERE loop_id = %s",
            (loop_id,),
        ).fetchone()
        if require_unify and not recon:
            return {"error": "Reconciliation required before closing. Run reconciler first."}

        # CoT evidence gate: if any criterion has cot_config, all criteria must have CoT evidence
        criteria = conn.execute(
            "SELECT id, status, evidence, cot_config FROM workflow_acceptance_criteria WHERE loop_id = %s",
            (loop_id,),
        ).fetchall()
        cot_required = False
        for c in criteria:
            if c.get("cot_config"):
                cot_required = True
                break
        if cot_required:
            missing_cot = [
                c["id"] for c in criteria
                if c["status"] in ("pass", "fail")
                and not (c.get("evidence") or "").strip().startswith("{\"cot_trace\"")
            ]
            if missing_cot:
                return {
                    "error": (
                        f"CoT evidence required for {len(missing_cot)} criterion(s). "
                        f"Missing: {', '.join(missing_cot)}"
                    ),
                }

        now = now_iso()
        conn.execute(
            "UPDATE workflow_loops SET status = 'closed', closed_at = %s WHERE id = %s",
            (now, loop_id),
        )
        conn.commit()
        _audit(
            "workflow.loop_closed",
            f"Loop {loop_id} closed",
            {"loop_id": loop_id, "result": recon["overall_result"] if recon else "unknown"},
            row["project_id"],
        )
        return {"loop_id": loop_id, "status": "closed", "closed_at": now}
    finally:
        conn.close()


def abandon_loop(loop_id: str, reason: str = "", db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Abandon a loop that won't be completed."""
    conn = _get_db(db_path)
    try:
        row = conn.execute("SELECT * FROM workflow_loops WHERE id = %s", (loop_id,)).fetchone()
        if not row:
            return {"error": f"Loop {loop_id} not found"}
        if row["status"] == "closed":
            return {"error": "Loop is already closed"}

        now = now_iso()
        conn.execute(
            "UPDATE workflow_loops SET status = 'abandoned', closed_at = %s WHERE id = %s",
            (now, loop_id),
        )
        conn.commit()
        _audit(
            "workflow.loop_abandoned",
            f"Loop {loop_id} abandoned: {reason}",
            {"loop_id": loop_id, "reason": reason},
            row["project_id"],
        )
        return {"loop_id": loop_id, "status": "abandoned", "reason": reason}
    finally:
        conn.close()


def get_loop_status(loop_id: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Get full status of a workflow loop."""
    conn = _get_db(db_path)
    try:
        row = conn.execute("SELECT * FROM workflow_loops WHERE id = %s", (loop_id,)).fetchone()
        if not row:
            return {"error": f"Loop {loop_id} not found"}

        criteria = conn.execute(
            "SELECT * FROM workflow_acceptance_criteria WHERE loop_id = %s ORDER BY criterion_number",
            (loop_id,),
        ).fetchall()

        recon = conn.execute(
            "SELECT * FROM workflow_reconciliations WHERE loop_id = %s ORDER BY reconciled_at DESC LIMIT 1",
            (loop_id,),
        ).fetchone()

        return {
            "loop_id": row["id"],
            "project_id": row["project_id"],
            "phase_name": row["phase_name"],
            "loop_type": row["loop_type"],
            "status": row["status"],
            "plan_summary": row["plan_summary"],
            "boundaries": json.loads(row["boundaries"]) if row["boundaries"] else [],
            "tasks": {"total": row["task_count"], "completed": row["tasks_completed"]},
            "acceptance_criteria": [
                {
                    "id": c["id"],
                    "number": c["criterion_number"],
                    "given": c["given_text"],
                    "when": c["when_text"],
                    "then": c["then_text"],
                    "status": c["status"],
                    "evidence": c["evidence"],
                    "bdd_story_id": c["bdd_story_id"] if "bdd_story_id" in c.keys() else None,
                }
                for c in criteria
            ],
            "acceptance_summary": {
                "total": row["acceptance_criteria_count"],
                "pass": row["acceptance_pass_count"],
                "fail": row["acceptance_fail_count"],
                "skip": row["acceptance_skip_count"],
            },
            "reconciliation": dict(recon) if recon else None,
            "timestamps": {
                "created": row["created_at"],
                "planned": row["planned_at"],
                "apply_started": row["apply_started_at"],
                "apply_completed": row["apply_completed_at"],
                "unify_started": row["unify_started_at"],
                "closed": row["closed_at"],
            },
        }
    finally:
        conn.close()


def list_loops(
    project_id: str,
    status_filter: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """List all workflow loops for a project."""
    conn = _get_db(db_path)
    try:
        query = "SELECT * FROM workflow_loops WHERE project_id = ?"
        params: list = [project_id]
        if status_filter:
            query += " AND status = ?"
            params.append(status_filter)
        query += " ORDER BY created_at DESC"

        rows = conn.execute(query, params).fetchall()
        return {
            "project_id": project_id,
            "total_loops": len(rows),
            "loops": [
                {
                    "id": r["id"],
                    "phase_name": r["phase_name"],
                    "loop_type": r["loop_type"],
                    "status": r["status"],
                    "tasks": f"{r['tasks_completed']}/{r['task_count']}",
                    "ac": f"{r['acceptance_pass_count']}P/{r['acceptance_fail_count']}F/{r['acceptance_skip_count']}S",
                    "created_at": r["created_at"],
                }
                for r in rows
            ],
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Workflow Loop Engine — PLAN→APPLY→UNIFY lifecycle")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--db-path", type=Path, default=None)

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--create", action="store_true", help="Create new loop")
    group.add_argument("--plan", action="store_true", help="Finalize plan")
    group.add_argument("--add-criteria", action="store_true", help="Add acceptance criterion")
    group.add_argument("--start-apply", action="store_true", help="Start apply phase")
    group.add_argument("--complete-task", action="store_true", help="Mark a task completed")
    group.add_argument("--verify-criterion", action="store_true", help="Verify criterion")
    group.add_argument("--start-unify", action="store_true", help="Start unify phase")
    group.add_argument("--close", action="store_true", help="Close loop")
    group.add_argument("--abandon", action="store_true", help="Abandon loop")
    group.add_argument("--status", action="store_true", help="Get loop status")
    group.add_argument("--list", action="store_true", help="List loops")

    parser.add_argument("--project-id", type=str, default="")
    parser.add_argument("--loop-id", type=str, default="")
    parser.add_argument("--criterion-id", type=str, default="")
    parser.add_argument("--phase", type=str, default="")
    parser.add_argument("--loop-type", type=str, default="build")
    parser.add_argument("--summary", type=str, default="")
    parser.add_argument("--task-count", type=int, default=0)
    parser.add_argument("--boundaries", type=str, default="")
    parser.add_argument("--given", type=str, default="")
    parser.add_argument("--when", type=str, default="")
    parser.add_argument("--then", type=str, default="")
    parser.add_argument("--criterion-status", type=str, default="pass", choices=["pass", "fail", "skip"])
    parser.add_argument("--evidence", type=str, default="")
    parser.add_argument("--reason", type=str, default="")
    parser.add_argument("--status-filter", type=str, default="")
    parser.add_argument("--created-by", type=str, default="")
    parser.add_argument("--bdd-story-id", type=str, default="", help="Link to safe_decomposition BDD story")
    parser.add_argument("--cot-config", type=str, default="", help="JSON cot_config dict for acceptance criterion")

    args = parser.parse_args()

    try:
        if args.create:
            result = create_loop(args.project_id, args.phase, args.loop_type, args.created_by, args.db_path)
        elif args.plan:
            boundaries = json.loads(args.boundaries) if args.boundaries else None
            result = finalize_plan(args.loop_id, args.summary, args.task_count, boundaries, args.db_path)
        elif args.add_criteria:
            cot_config = json.loads(args.cot_config) if args.cot_config else None
            result = add_acceptance_criterion(
                args.loop_id, args.given, args.when, args.then, args.bdd_story_id, cot_config, args.db_path
            )
        elif args.start_apply:
            result = start_apply(args.loop_id, args.db_path)
        elif args.complete_task:
            result = complete_task(args.loop_id, args.db_path)
        elif args.verify_criterion:
            result = verify_criterion(args.criterion_id, args.criterion_status, args.evidence, args.db_path)
        elif args.start_unify:
            result = start_unify(args.loop_id, args.db_path)
        elif args.close:
            result = close_loop(args.loop_id, args.db_path)
        elif args.abandon:
            result = abandon_loop(args.loop_id, args.reason, args.db_path)
        elif args.status:
            result = get_loop_status(args.loop_id, args.db_path)
        elif args.list:
            result = list_loops(args.project_id, args.status_filter or None, args.db_path)
        else:
            result = {"error": "No action specified"}

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(json.dumps(result, indent=2, default=str))
    except Exception as e:
        err = {"error": str(e)}
        if args.json:
            print(json.dumps(err, indent=2))
        else:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
