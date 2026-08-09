#!/usr/bin/env python3
# CUI // SP-CTI
"""Single Next Action Recommender — weighted priority routing (D-WF-4).

Analyzes project state across multiple dimensions and recommends exactly
ONE next action. Reduces decision fatigue in complex compliance workflows.

Usage:
    python tools/workflow/next_action.py --recommend --project-id "proj-123" --json
"""

from __future__ import annotations

import argparse
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
CONFIG_PATH = BASE_DIR / "args" / "workflow_loop_config.yaml"

_HAS_YAML = False
try:
    import yaml  # type: ignore

    _HAS_YAML = True
except ImportError:
    pass


def _get_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}")
    conn = get_connection(db_path=str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _load_config() -> Dict[str, Any]:
    if not _HAS_YAML or not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _hours_since(timestamp_str: Optional[str]) -> float:
    """Calculate hours since a timestamp string."""
    if not timestamp_str:
        return 999.0
    try:
        ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - ts
        return delta.total_seconds() / 3600.0
    except Exception:
        return 999.0


def recommend(project_id: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Analyze project state and recommend ONE next action.

    Evaluates 5 dimensions with configurable weights:
    1. staleness — how long since last activity
    2. compliance_gap — unresolved compliance items
    3. security_risk — open security findings
    4. loop_state — open loops needing closure
    5. handoff_age — stale handoff documents
    """
    config = _load_config()
    weights = config.get("next_action", {}).get(
        "weights",
        {
            "staleness": 0.30,
            "compliance_gap": 0.25,
            "security_risk": 0.20,
            "loop_state": 0.15,
            "handoff_age": 0.10,
        },
    )

    conn = _get_db(db_path)
    try:
        candidates: List[Tuple[float, str, str, str]] = []  # (score, action, command, reason)

        # 1. Open loops needing attention
        open_loops = conn.execute(
            """SELECT * FROM workflow_loops
               WHERE project_id = %s AND status NOT IN ('closed', 'abandoned')
               ORDER BY created_at ASC""",
            (project_id,),
        ).fetchall()

        for loop in open_loops:
            status = loop["status"]
            loop_id = loop["id"]
            phase = loop["phase_name"]

            if status == "planning":
                hours = _hours_since(loop["created_at"])
                score = min(1.0, hours / 24.0) * weights.get("loop_state", 0.15) + 0.3
                candidates.append(
                    (
                        score,
                        f"Finalize plan for '{phase}'",
                        f"python tools/workflow/loop_engine.py --plan --loop-id {loop_id} --json",
                        f"Loop {loop_id} has been in planning for {hours:.0f}h",
                    )
                )
            elif status == "planned":
                score = 0.6 * weights.get("loop_state", 0.15) + 0.4
                candidates.append(
                    (
                        score,
                        f"Start applying plan for '{phase}'",
                        f"python tools/workflow/loop_engine.py --start-apply --loop-id {loop_id} --json",
                        f"Plan for '{phase}' is approved but not started",
                    )
                )
            elif status == "applying":
                remaining = loop["task_count"] - loop["tasks_completed"]
                score = 0.7 * weights.get("loop_state", 0.15) + 0.3
                candidates.append(
                    (
                        score,
                        f"Continue applying '{phase}' ({remaining} tasks remaining)",
                        f"python tools/workflow/loop_engine.py --complete-task --loop-id {loop_id} --json",
                        f"{loop['tasks_completed']}/{loop['task_count']} tasks done",
                    )
                )
            elif status == "applied":
                score = 0.8 * weights.get("loop_state", 0.15) + 0.5
                candidates.append(
                    (
                        score,
                        f"Start UNIFY for '{phase}' — reconcile planned vs actual",
                        f"python tools/workflow/loop_engine.py --start-unify --loop-id {loop_id} --json",
                        "All tasks completed, reconciliation needed",
                    )
                )
            elif status == "unifying":
                score = 0.9 * weights.get("loop_state", 0.15) + 0.6
                candidates.append(
                    (
                        score,
                        f"Complete reconciliation for '{phase}'",
                        f"python tools/workflow/reconciler.py --reconcile --loop-id {loop_id} --json",
                        "Loop is in UNIFY state, needs reconciliation to close",
                    )
                )

        # 2. Compliance staleness
        try:
            ssp = conn.execute(
                """SELECT MAX(generated_at) as latest FROM compliance_artifacts
                   WHERE project_id = %s AND artifact_type = 'ssp'""",
                (project_id,),
            ).fetchone()
            if ssp and ssp["latest"]:
                hours = _hours_since(ssp["latest"])
                if hours > 720:  # 30 days
                    score = min(1.0, hours / 2160.0) * weights.get("compliance_gap", 0.25) + 0.3
                    candidates.append(
                        (
                            score,
                            f"Regenerate SSP (last generated {hours / 24:.0f} days ago)",
                            f"python tools/compliance/ssp_generator.py --project-id {project_id}",
                            f"SSP is {hours / 24:.0f} days old",
                        )
                    )
        except Exception:
            pass

        # 3. Security findings
        try:
            vulns = conn.execute(
                """SELECT COUNT(*) as cnt FROM security_findings
                   WHERE project_id = %s AND severity IN ('critical', 'high')
                   AND status = 'open'""",
                (project_id,),
            ).fetchone()
            if vulns and vulns["cnt"] > 0:
                score = min(1.0, vulns["cnt"] / 5.0) * weights.get("security_risk", 0.20) + 0.5
                candidates.append(
                    (
                        score,
                        f"Address {vulns['cnt']} critical/high security findings",
                        "python tools/security/sast_runner.py --project-dir .",
                        f"{vulns['cnt']} open critical/high findings",
                    )
                )
        except Exception:
            pass

        # 4. No open loops — suggest creating one
        if not open_loops:
            candidates.append(
                (
                    0.2,
                    "Create a new workflow loop to start your next task",
                    "python tools/workflow/loop_engine.py --create --project-id "
                    f'"{project_id}" --phase "next-task" --json',
                    "No active workflow loops",
                )
            )

        # Sort by score descending, pick top
        candidates.sort(key=lambda x: x[0], reverse=True)

        if not candidates:
            return {
                "project_id": project_id,
                "recommendation": "No actions needed — project appears healthy",
                "command": None,
                "reason": "All loops closed, no open issues",
                "score": 0.0,
                "all_candidates": [],
            }

        top = candidates[0]
        return {
            "project_id": project_id,
            "recommendation": top[1],
            "command": top[2],
            "reason": top[3],
            "score": round(top[0], 4),
            "open_loops": len(open_loops),
            "total_candidates": len(candidates),
            "all_candidates": [
                {"score": round(c[0], 4), "action": c[1], "command": c[2], "reason": c[3]} for c in candidates[:5]
            ],
            "evaluated_at": now_iso(),
        }
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Single Next Action Recommender")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--recommend", action="store_true", required=True)
    parser.add_argument("--project-id", type=str, required=True)

    args = parser.parse_args()

    try:
        result = recommend(args.project_id, args.db_path)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            rec = result.get("recommendation", "No recommendation")
            cmd = result.get("command", "")
            reason = result.get("reason", "")
            print("\n=== Next Action ===")
            print(f"  {rec}")
            if cmd:
                print(f"\n  Command: {cmd}")
            if reason:
                print(f"  Reason:  {reason}")
            print()
    except Exception as e:
        err = {"error": str(e)}
        if args.json:
            print(json.dumps(err, indent=2))
        else:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
