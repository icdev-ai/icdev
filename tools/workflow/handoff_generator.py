#!/usr/bin/env python3
# CUI // SP-CTI
"""Structured Session Handoff Generator (D-WF-5).

Generates markdown handoff documents that capture loop position, decisions,
blockers, and prioritised next actions for zero-context session resumption.
Output is consumable by any AI coding platform.

Usage:
    python tools/workflow/handoff_generator.py --generate --project-id "proj-123" --json
    python tools/workflow/handoff_generator.py --list --project-id "proj-123" --json
    python tools/workflow/handoff_generator.py --get --handoff-id "wh-xxx" --json
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


def _load_config() -> Dict[str, Any]:
    if not _HAS_YAML or not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def generate_handoff(
    project_id: str,
    created_by: str = "",
    handoff_to: str = "",
    chat_context_id: str = "",
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Generate a structured session handoff document.

    Collects:
    1. Open loop status and position
    2. Recent decisions from audit trail
    3. Blockers from failed criteria / deviations
    4. Prioritised next actions
    """
    config = _load_config()
    max_decisions = config.get("handoff", {}).get("max_recent_decisions", 10)
    max_actions = config.get("handoff", {}).get("max_next_actions", 5)

    conn = _get_db(db_path)
    try:
        # 1. Open loops
        loops = conn.execute(
            """SELECT * FROM workflow_loops
               WHERE project_id = %s AND status NOT IN ('closed', 'abandoned')
               ORDER BY created_at DESC""",
            (project_id,),
        ).fetchall()

        loop_id = loops[0]["id"] if loops else None
        loop_status = loops[0]["status"] if loops else None

        loop_summaries = []
        for lp in loops:
            loop_summaries.append(
                {
                    "id": lp["id"],
                    "phase": lp["phase_name"],
                    "status": lp["status"],
                    "tasks": f"{lp['tasks_completed']}/{lp['task_count']}",
                    "ac_pass": lp["acceptance_pass_count"],
                    "ac_fail": lp["acceptance_fail_count"],
                }
            )

        # 2. Recent decisions from audit trail
        decisions: List[str] = []
        try:
            rows = conn.execute(
                """SELECT action, details, created_at FROM audit_trail
                   WHERE project_id = %s
                   ORDER BY created_at DESC LIMIT %s""",
                (project_id, max_decisions),
            ).fetchall()
            for r in rows:
                decisions.append(f"[{r['created_at']}] {r['action']}")
        except Exception:
            pass

        # 3. Blockers — failed criteria + deviations
        blockers: List[str] = []
        for lp in loops:
            failed = conn.execute(
                """SELECT * FROM workflow_acceptance_criteria
                   WHERE loop_id = %s AND status = 'fail'""",
                (lp["id"],),
            ).fetchall()
            for f in failed:
                blockers.append(
                    f"AC-{f['criterion_number']} FAILED in '{lp['phase_name']}': "
                    f"Given {f['given_text']} When {f['when_text']} Then {f['then_text']}"
                )

            recon = conn.execute(
                """SELECT deviations FROM workflow_reconciliations
                   WHERE loop_id = %s ORDER BY reconciled_at DESC LIMIT 1""",
                (lp["id"],),
            ).fetchone()
            if recon and recon["deviations"]:
                devs = json.loads(recon["deviations"])
                for d in devs:
                    blockers.append(f"Deviation in '{lp['phase_name']}': {d.get('detail', '')}")

        # 4. Next actions (from next_action recommender logic, simplified)
        next_actions: List[str] = []
        for lp in loops[:max_actions]:
            status = lp["status"]
            phase = lp["phase_name"]
            if status == "planning":
                next_actions.append(f"Finalize plan for '{phase}'")
            elif status == "planned":
                next_actions.append(f"Start applying '{phase}'")
            elif status == "applying":
                remaining = lp["task_count"] - lp["tasks_completed"]
                next_actions.append(f"Complete {remaining} remaining tasks in '{phase}'")
            elif status == "applied":
                next_actions.append(f"Run UNIFY for '{phase}' — reconcile planned vs actual")
            elif status == "unifying":
                next_actions.append(f"Complete reconciliation for '{phase}' and close loop")

        if not next_actions:
            next_actions.append("Create a new workflow loop for the next task")

        # Build context summary
        context_summary = f"Project {project_id}: {len(loops)} open loop(s). "
        if loops:
            current = loops[0]
            context_summary += (
                f"Current: '{current['phase_name']}' ({current['status']}, "
                f"{current['tasks_completed']}/{current['task_count']} tasks). "
            )
        if blockers:
            context_summary += f"{len(blockers)} blocker(s). "

        # Generate markdown handoff
        md_lines = [
            "# CUI // SP-CTI",
            f"# Session Handoff — {project_id}",
            f"Generated: {now_iso()}",
            "",
            "## Current Position",
        ]
        for ls in loop_summaries:
            md_lines.append(
                f"- **{ls['phase']}** ({ls['status']}) — Tasks: {ls['tasks']}, AC: {ls['ac_pass']}P/{ls['ac_fail']}F"
            )
        md_lines.extend(["", "## Priority Actions"])
        for i, a in enumerate(next_actions, 1):
            md_lines.append(f"{i}. {a}")
        if blockers:
            md_lines.extend(["", "## Blockers"])
            for b in blockers:
                md_lines.append(f"- {b}")
        if decisions:
            md_lines.extend(["", "## Recent Decisions"])
            for d in decisions[:5]:
                md_lines.append(f"- {d}")
        md_lines.append("")

        markdown = "\n".join(md_lines)

        # Store handoff (append-only)
        handoff_id = _gen_id("wh")
        now = now_iso()
        conn.execute(
            """INSERT INTO workflow_handoffs
               (id, project_id, loop_id, loop_status, chat_context_id,
                decisions_made, blockers, next_actions, context_summary,
                handoff_to, created_by, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                handoff_id,
                project_id,
                loop_id,
                loop_status,
                chat_context_id or None,
                json.dumps(decisions[:max_decisions]),
                json.dumps(blockers),
                json.dumps(next_actions),
                context_summary,
                handoff_to,
                created_by,
                now,
            ),
        )
        conn.commit()

        if _HAS_AUDIT:
            try:
                audit_log_event(
                    event_type="workflow.handoff_generated",
                    actor="workflow-engine",
                    action=f"Generated handoff {handoff_id}",
                    project_id=project_id,
                )
            except Exception:
                pass

        return {
            "handoff_id": handoff_id,
            "project_id": project_id,
            "open_loops": loop_summaries,
            "next_actions": next_actions,
            "blockers": blockers,
            "decisions": decisions[:max_decisions],
            "context_summary": context_summary,
            "markdown": markdown,
            "created_at": now,
        }
    finally:
        conn.close()


def list_handoffs(project_id: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    """List all handoffs for a project."""
    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM workflow_handoffs WHERE project_id = %s ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()
        return {
            "project_id": project_id,
            "total": len(rows),
            "handoffs": [
                {
                    "id": r["id"],
                    "loop_id": r["loop_id"],
                    "loop_status": r["loop_status"],
                    "context_summary": r["context_summary"],
                    "handoff_to": r["handoff_to"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ],
        }
    finally:
        conn.close()


def get_handoff(handoff_id: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Get a specific handoff document."""
    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM workflow_handoffs WHERE id = %s",
            (handoff_id,),
        ).fetchone()
        if not row:
            return {"error": f"Handoff {handoff_id} not found"}
        return {
            "handoff_id": row["id"],
            "project_id": row["project_id"],
            "loop_id": row["loop_id"],
            "loop_status": row["loop_status"],
            "chat_context_id": row["chat_context_id"] if "chat_context_id" in row.keys() else None,
            "decisions_made": json.loads(row["decisions_made"]) if row["decisions_made"] else [],
            "blockers": json.loads(row["blockers"]) if row["blockers"] else [],
            "next_actions": json.loads(row["next_actions"]) if row["next_actions"] else [],
            "context_summary": row["context_summary"],
            "handoff_to": row["handoff_to"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
        }
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Session Handoff Generator")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--db-path", type=Path, default=None)

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--generate", action="store_true")
    group.add_argument("--list", action="store_true")
    group.add_argument("--get", action="store_true")

    parser.add_argument("--project-id", type=str, default="")
    parser.add_argument("--handoff-id", type=str, default="")
    parser.add_argument("--created-by", type=str, default="")
    parser.add_argument("--handoff-to", type=str, default="")
    parser.add_argument("--chat-context-id", type=str, default="", help="Link to chat context")

    args = parser.parse_args()

    try:
        if args.generate:
            result = generate_handoff(
                args.project_id, args.created_by, args.handoff_to, args.chat_context_id, args.db_path
            )
        elif args.list:
            result = list_handoffs(args.project_id, args.db_path)
        else:
            result = get_handoff(args.handoff_id, args.db_path)

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            if "markdown" in result:
                print(result["markdown"])
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
