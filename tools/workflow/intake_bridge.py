#!/usr/bin/env python3
# CUI // SP-CTI
"""Intake → Workflow Loop Bridge (Phase 66, D-WF-1 + RICOAS).

Bridges the gap between completed requirements intake (readiness >= 0.7)
and the workflow discipline engine by auto-creating a workflow loop with
acceptance criteria seeded from BDD stories in safe_decomposition.

Usage:
    python tools/workflow/intake_bridge.py --bridge --session-id "sess-xxx" --json
    python tools/workflow/intake_bridge.py --check --session-id "sess-xxx" --json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from tools.db.storage import get_connection, table_exists
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

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


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    # Backend-aware, translation-independent existence probe (pgrt-sweep-06).
    return table_exists(conn, name)


def check_bridge_readiness(
    session_id: str,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Check if an intake session is ready for workflow loop creation.

    Criteria:
    1. Session exists and has readiness_score >= 0.7
    2. Session has decomposed requirements (safe_decomposition entries)
    3. No existing workflow loop already linked to this session
    """
    conn = _get_db(db_path)
    try:
        session = conn.execute(
            "SELECT * FROM intake_sessions WHERE id = %s",
            (session_id,),
        ).fetchone()
        if not session:
            return {"ready": False, "reason": f"Session {session_id} not found"}

        readiness = float(session["readiness_score"] or 0.0)
        project_id = session["project_id"] or ""

        # Check readiness threshold
        if readiness < 0.7:
            return {
                "ready": False,
                "reason": f"Readiness score {readiness:.2f} below 0.7 threshold",
                "readiness_score": readiness,
                "session_id": session_id,
            }

        # Check for decomposed stories
        story_count = 0
        if _table_exists(conn, "safe_decomposition"):
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM safe_decomposition WHERE session_id = %s",
                (session_id,),
            ).fetchone()
            story_count = row[0] if isinstance(row, (tuple, list)) else row["cnt"]

        if story_count == 0:
            return {
                "ready": False,
                "reason": "No decomposed stories found. Run decomposition first.",
                "readiness_score": readiness,
                "session_id": session_id,
            }

        # Check for existing linked loop
        existing_loop = None
        if _table_exists(conn, "workflow_loops"):
            existing = conn.execute(
                """SELECT id, status FROM workflow_loops
                   WHERE project_id = %s AND phase_name LIKE %s
                   AND status NOT IN ('closed', 'abandoned')""",
                (project_id, f"%{session_id[-8:]}%"),
            ).fetchone()
            if existing:
                existing_loop = {"id": existing["id"], "status": existing["status"]}

        return {
            "ready": existing_loop is None,
            "reason": "Loop already exists" if existing_loop else "Ready for bridge",
            "readiness_score": readiness,
            "session_id": session_id,
            "project_id": project_id,
            "story_count": story_count,
            "existing_loop": existing_loop,
        }
    finally:
        conn.close()


def bridge_intake_to_loop(
    session_id: str,
    created_by: str = "intake-bridge",
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Create a workflow loop from a completed intake session.

    1. Reads session + decomposed stories
    2. Creates a workflow loop with the session's phase name
    3. Seeds acceptance criteria from BDD stories (Given/When/Then)
    4. Sets task_count from story count
    5. Finalizes the plan automatically

    Returns the created loop details.
    """
    from tools.workflow.loop_engine import (
        add_acceptance_criterion,
        create_loop,
        finalize_plan,
    )

    # Check readiness first
    check = check_bridge_readiness(session_id, db_path)
    if not check.get("ready"):
        return {"error": check.get("reason", "Not ready"), "check": check}

    conn = _get_db(db_path)
    try:
        session = conn.execute(
            "SELECT * FROM intake_sessions WHERE id = %s",
            (session_id,),
        ).fetchone()
        project_id = session["project_id"] or ""
        customer = session["customer_name"] or "Unknown"

        # Get context summary for phase name
        context_raw = session["context_summary"] or "{}"
        try:
            context = json.loads(context_raw) if isinstance(context_raw, str) else {}
        except (json.JSONDecodeError, TypeError):
            context = {}
        goal = context.get("goal", "intake-session")
        phase_name = f"intake-{session_id[-8:]}-{goal[:30]}"

        # Get stories from safe_decomposition
        stories: List[Dict] = []
        if _table_exists(conn, "safe_decomposition"):
            rows = conn.execute(
                """SELECT id, title, acceptance_criteria, level
                   FROM safe_decomposition
                   WHERE session_id = %s AND level IN ('story', 'feature', 'enabler')
                   ORDER BY created_at""",
                (session_id,),
            ).fetchall()
            stories = [dict(r) for r in rows]

        # Create the workflow loop
        loop_result = create_loop(
            project_id,
            phase_name,
            "build",
            created_by,
            db_path,
        )
        if "error" in loop_result:
            return loop_result

        loop_id = loop_result["loop_id"]
        ac_count = 0

        # Seed acceptance criteria from BDD stories
        for story in stories[:20]:  # Cap at 20 to keep plan manageable
            ac_text = story.get("acceptance_criteria") or ""
            title = story.get("title") or ""
            story_id = story.get("id") or ""

            # Parse Gherkin if present, otherwise use title
            given = "the system is set up per requirements"
            when = title
            then = "the acceptance criteria are satisfied"

            # Try to extract Given/When/Then from Gherkin text
            if "Given " in ac_text and "When " in ac_text and "Then " in ac_text:
                lines = ac_text.split("\n")
                for line in lines:
                    stripped = line.strip()
                    if stripped.startswith("Given "):
                        given = stripped[6:]
                    elif stripped.startswith("When "):
                        when = stripped[5:]
                    elif stripped.startswith("Then "):
                        then = stripped[5:]

            result = add_acceptance_criterion(
                loop_id,
                given,
                when,
                then,
                bdd_story_id=story_id,
                db_path=db_path,
            )
            if "criterion_id" in result:
                ac_count += 1

        # Finalize the plan
        task_count = min(len(stories), 20)
        boundaries_list = []
        if session["impact_level"] in ("IL5", "IL6"):
            boundaries_list.append("*.ato.*")
            boundaries_list.append("*.ssp.*")

        plan_summary = (
            f"Intake session {session_id} for {customer} "
            f"(readiness {check['readiness_score']:.2f}). "
            f"{task_count} stories, {ac_count} acceptance criteria."
        )

        plan_result = finalize_plan(
            loop_id,
            plan_summary,
            task_count,
            boundaries_list,
            db_path,
        )

        if _HAS_AUDIT:
            try:
                audit_log_event(
                    event_type="workflow.intake_bridged",
                    actor="intake-bridge",
                    action=f"Bridged session {session_id} to loop {loop_id}",
                    details=json.dumps(
                        {
                            "session_id": session_id,
                            "loop_id": loop_id,
                            "stories": task_count,
                            "ac_count": ac_count,
                        }
                    ),
                    project_id=project_id,
                )
            except Exception:
                pass

        return {
            "loop_id": loop_id,
            "session_id": session_id,
            "project_id": project_id,
            "phase_name": phase_name,
            "task_count": task_count,
            "acceptance_criteria_seeded": ac_count,
            "plan_status": plan_result.get("status", "error"),
            "plan_summary": plan_summary,
            "readiness_score": check["readiness_score"],
        }
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Intake → Workflow Loop Bridge")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--db-path", type=Path, default=None)

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--bridge", action="store_true", help="Create loop from intake session")
    group.add_argument("--check", action="store_true", help="Check bridge readiness")

    parser.add_argument("--session-id", type=str, required=True)
    parser.add_argument("--created-by", type=str, default="intake-bridge")

    args = parser.parse_args()

    try:
        if args.bridge:
            result = bridge_intake_to_loop(args.session_id, args.created_by, args.db_path)
        else:
            result = check_bridge_readiness(args.session_id, args.db_path)

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
