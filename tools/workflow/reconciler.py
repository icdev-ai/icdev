#!/usr/bin/env python3
# CUI // SP-CTI
"""Workflow Reconciler — planned-vs-actual delta for UNIFY phase (D-WF-7).

Compares what was planned in a loop against what actually happened,
producing a structured reconciliation record. This is the mandatory
UNIFY step that prevents orphaned work and creates NIST AU evidence.

Usage:
    python tools/workflow/reconciler.py --reconcile --loop-id "wl-xxx" --json
    python tools/workflow/reconciler.py --get --loop-id "wl-xxx" --json
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

from tools.db.storage import get_connection
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
        raise FileNotFoundError(f"Database not found: {path}")
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


def reconcile(loop_id: str, lessons: str = "", db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Produce a reconciliation record comparing planned vs actual.

    Checks:
    1. Task completion rate
    2. Acceptance criteria pass rate
    3. Required process invocation (from audit trail)
    4. Deviations (tasks added/removed during APPLY)

    Returns structured reconciliation with overall_result.
    """
    conn = _get_db(db_path)
    try:
        loop = conn.execute("SELECT * FROM workflow_loops WHERE id = %s", (loop_id,)).fetchone()
        if not loop:
            return {"error": f"Loop {loop_id} not found"}
        if loop["status"] not in ("unifying", "applied", "applying"):
            return {"error": f"Loop is in '{loop['status']}' state, expected 'unifying'/'applied'/'applying'"}

        # 1. Task completion
        planned_tasks = loop["task_count"]
        completed_tasks = loop["tasks_completed"]

        # 2. Acceptance criteria
        criteria = conn.execute(
            "SELECT * FROM workflow_acceptance_criteria WHERE loop_id = %s",
            (loop_id,),
        ).fetchall()
        ac_total = len(criteria)
        ac_pass = sum(1 for c in criteria if c["status"] == "pass")
        ac_fail = sum(1 for c in criteria if c["status"] == "fail")
        ac_pending = sum(1 for c in criteria if c["status"] == "pending")

        # 3. Process verification — check audit trail for required processes
        config = _load_config()
        loop_type = loop["loop_type"] or "build"
        required = config.get("process_verification", {}).get("required_processes", {}).get(loop_type, [])
        invoked_processes: List[str] = []
        missing_processes: List[str] = []
        for proc in required:
            # Check audit_trail for this process type
            try:
                found = conn.execute(
                    """SELECT COUNT(*) as cnt FROM audit_trail
                       WHERE event_type LIKE %s AND project_id = %s
                       ORDER BY created_at DESC LIMIT 1""",
                    (f"%{proc}%", loop["project_id"]),
                ).fetchone()["cnt"]
                if found > 0:
                    invoked_processes.append(proc)
                else:
                    missing_processes.append(proc)
            except Exception:
                missing_processes.append(proc)

        # 4. Deviations
        deviations: List[Dict[str, str]] = []
        if completed_tasks > planned_tasks:
            deviations.append(
                {
                    "type": "scope_expansion",
                    "detail": f"Completed {completed_tasks} tasks but only {planned_tasks} were planned",
                }
            )
        if completed_tasks < planned_tasks:
            deviations.append(
                {
                    "type": "incomplete",
                    "detail": f"Completed {completed_tasks}/{planned_tasks} planned tasks",
                }
            )
        if ac_fail > 0:
            deviations.append(
                {
                    "type": "acceptance_failure",
                    "detail": f"{ac_fail} acceptance criteria failed",
                }
            )
        if ac_pending > 0:
            deviations.append(
                {
                    "type": "unverified_criteria",
                    "detail": f"{ac_pending} acceptance criteria not yet verified",
                }
            )
        if missing_processes:
            deviations.append(
                {
                    "type": "missing_process",
                    "detail": f"Required processes not invoked: {', '.join(missing_processes)}",
                }
            )

        # 5. Coherence check (D-WF-8) — run if available
        coherence_summary: Optional[Dict] = None
        try:
            from tools.workflow.coherence_checker import run_checks as run_coherence

            coherence_report = run_coherence()
            if not coherence_report.overall_pass:
                failed_names = [c.check_name for c in coherence_report.checks if c.status == "fail"]
                deviations.append(
                    {
                        "type": "coherence_failure",
                        "detail": f"Coherence checks failed: {', '.join(failed_names)}",
                    }
                )
            coherence_summary = {
                "pass": coherence_report.overall_pass,
                "total": coherence_report.total_checks,
                "failed": coherence_report.failed_checks,
                "warned": coherence_report.warned_checks,
            }
        except (ImportError, Exception):
            pass  # Graceful — coherence checker is optional

        # 6. Artifact verification — check planned files exist
        artifact_missing: List[str] = []
        try:
            tasks = conn.execute(
                "SELECT * FROM workflow_tasks WHERE loop_id = %s",
                (loop_id,),
            ).fetchall()
            project_dir = Path(loop.get("project_id", "") or ".")
            for task in tasks:
                desc = (task["description"] or "").lower()
                # Detect file-creation tasks by keywords
                if any(
                    kw in desc
                    for kw in (
                        "create ",
                        "generate ",
                        "write ",
                        "build ",
                        "implement ",
                    )
                ):
                    # Extract potential file paths from description
                    import re as _re

                    paths = _re.findall(
                        r"[\w/\\]+\.(?:py|yaml|json|md|html|ts|js)",
                        task["description"] or "",
                    )
                    for p in paths:
                        full = project_dir / p
                        if not full.exists():
                            artifact_missing.append(f"{task['id']}: {p} not found")
            if artifact_missing:
                deviations.append(
                    {
                        "type": "missing_artifact",
                        "detail": (
                            f"{len(artifact_missing)} planned file(s) not created: " + "; ".join(artifact_missing[:5])
                        ),
                    }
                )
        except Exception:
            pass  # Graceful — tasks table may not exist

        # Overall result
        min_pass_rate = config.get("reconciliation", {}).get("min_pass_rate", 0.80)
        pass_rate = ac_pass / max(ac_total, 1)
        task_rate = completed_tasks / max(planned_tasks, 1)

        if ac_fail == 0 and ac_pending == 0 and task_rate >= 1.0 and pass_rate >= min_pass_rate:
            overall = "success"
        elif ac_fail > 0 or pass_rate < 0.5:
            overall = "failed"
        else:
            overall = "partial"

        # Store reconciliation (append-only)
        recon_id = _gen_id("wr")
        now = now_iso()
        process_checks = {
            "required": required,
            "invoked": invoked_processes,
            "missing": missing_processes,
        }
        conn.execute(
            """INSERT INTO workflow_reconciliations
               (id, loop_id, planned_tasks, completed_tasks, deviations,
                lessons_learned, process_checks, required_processes_invoked,
                required_processes_total, overall_result, reconciled_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                recon_id,
                loop_id,
                planned_tasks,
                completed_tasks,
                json.dumps(deviations),
                lessons,
                json.dumps(process_checks),
                len(invoked_processes),
                len(required),
                overall,
                now,
            ),
        )
        conn.commit()

        _audit(
            "workflow.reconciled",
            f"Reconciled loop {loop_id}: {overall}",
            {"loop_id": loop_id, "overall_result": overall, "pass_rate": round(pass_rate, 2)},
            loop["project_id"],
        )

        return {
            "reconciliation_id": recon_id,
            "loop_id": loop_id,
            "overall_result": overall,
            "tasks": {"planned": planned_tasks, "completed": completed_tasks, "completion_rate": round(task_rate, 2)},
            "acceptance_criteria": {
                "total": ac_total,
                "pass": ac_pass,
                "fail": ac_fail,
                "pending": ac_pending,
                "pass_rate": round(pass_rate, 2),
            },
            "process_verification": process_checks,
            "coherence": coherence_summary,
            "deviations": deviations,
            "lessons_learned": lessons,
            "reconciled_at": now,
        }
    finally:
        conn.close()


def get_reconciliation(loop_id: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Get the latest reconciliation for a loop."""
    conn = _get_db(db_path)
    try:
        row = conn.execute(
            """SELECT * FROM workflow_reconciliations
               WHERE loop_id = %s ORDER BY reconciled_at DESC LIMIT 1""",
            (loop_id,),
        ).fetchone()
        if not row:
            return {"error": f"No reconciliation found for loop {loop_id}"}
        return {
            "reconciliation_id": row["id"],
            "loop_id": row["loop_id"],
            "overall_result": row["overall_result"],
            "tasks": {"planned": row["planned_tasks"], "completed": row["completed_tasks"]},
            "deviations": json.loads(row["deviations"]) if row["deviations"] else [],
            "lessons_learned": row["lessons_learned"],
            "process_checks": json.loads(row["process_checks"]) if row["process_checks"] else {},
            "reconciled_at": row["reconciled_at"],
        }
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Workflow Reconciler — planned-vs-actual delta")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--db-path", type=Path, default=None)

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--reconcile", action="store_true", help="Produce reconciliation record")
    group.add_argument("--get", action="store_true", help="Get latest reconciliation")

    parser.add_argument("--loop-id", type=str, required=True)
    parser.add_argument("--lessons", type=str, default="")

    args = parser.parse_args()

    try:
        if args.reconcile:
            result = reconcile(args.loop_id, args.lessons, args.db_path)
        else:
            result = get_reconciliation(args.loop_id, args.db_path)

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
