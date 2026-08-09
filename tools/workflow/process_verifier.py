#!/usr/bin/env python3
# CUI // SP-CTI
"""Process Verifier — verify required processes were invoked (D-WF-6).

Checks the audit trail to confirm that required development processes
(security scan, compliance check, CUI marking, tests) were actually
executed during a workflow loop, not just planned.

Usage:
    python tools/workflow/process_verifier.py --verify --loop-id "wl-xxx" --json
    python tools/workflow/process_verifier.py --check --project-id "proj-123" --loop-type build --json
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
from datetime import datetime, timezone, timedelta
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


# Default required processes per loop type
DEFAULT_REQUIRED_PROCESSES = {
    "build": [
        "security.sast",
        "security.secret_detection",
        "compliance.cui_marking",
        "testing.unit",
    ],
    "compliance": [
        "compliance.ssp_generate",
        "compliance.control_map",
        "compliance.cui_marking",
    ],
    "deploy": [
        "security.sast",
        "security.dependency_audit",
        "compliance.sbom_generate",
        "testing.unit",
        "testing.bdd",
    ],
    "fix": [
        "testing.unit",
        "security.sast",
    ],
    "research": [],
    "custom": [],
}


def verify_loop_processes(loop_id: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Verify required processes were invoked during a loop's execution.

    Searches the audit_trail for evidence of each required process
    between the loop's apply_started_at and now.
    """
    conn = _get_db(db_path)
    try:
        loop = conn.execute("SELECT * FROM workflow_loops WHERE id = %s", (loop_id,)).fetchone()
        if not loop:
            return {"error": f"Loop {loop_id} not found"}

        config = _load_config()
        loop_type = loop["loop_type"] or "build"
        required = (
            config.get("process_verification", {})
            .get("required_processes", DEFAULT_REQUIRED_PROCESSES)
            .get(loop_type, DEFAULT_REQUIRED_PROCESSES.get(loop_type, []))
        )

        project_id = loop["project_id"]
        since = loop["apply_started_at"] or loop["created_at"]

        results: List[Dict[str, Any]] = []
        invoked_count = 0

        for proc in required:
            # Search audit trail for evidence
            try:
                evidence = conn.execute(
                    """SELECT action, created_at FROM audit_trail
                       WHERE event_type LIKE %s AND project_id = %s
                       AND created_at >= %s
                       ORDER BY created_at DESC LIMIT 1""",
                    (f"%{proc}%", project_id, since),
                ).fetchone()
            except Exception:
                evidence = None

            if evidence:
                invoked_count += 1
                results.append(
                    {
                        "process": proc,
                        "status": "invoked",
                        "evidence": evidence["action"],
                        "timestamp": evidence["created_at"],
                    }
                )
            else:
                results.append(
                    {
                        "process": proc,
                        "status": "missing",
                        "evidence": None,
                        "timestamp": None,
                    }
                )

        total = len(required)
        missing = [r["process"] for r in results if r["status"] == "missing"]
        pass_rate = invoked_count / max(total, 1)

        return {
            "loop_id": loop_id,
            "loop_type": loop_type,
            "project_id": project_id,
            "required_processes": total,
            "invoked_processes": invoked_count,
            "missing_processes": missing,
            "pass_rate": round(pass_rate, 2),
            "all_invoked": len(missing) == 0,
            "details": results,
            "verified_at": now_iso(),
        }
    finally:
        conn.close()


def check_project_processes(
    project_id: str,
    loop_type: str = "build",
    hours: int = 24,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Check if required processes have been run for a project recently.

    Useful outside of a specific loop — just check if the project has
    had its required processes executed in the last N hours.
    """
    conn = _get_db(db_path)
    try:
        config = _load_config()
        required = (
            config.get("process_verification", {})
            .get("required_processes", DEFAULT_REQUIRED_PROCESSES)
            .get(loop_type, DEFAULT_REQUIRED_PROCESSES.get(loop_type, []))
        )

        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")

        results: List[Dict[str, Any]] = []
        invoked_count = 0

        for proc in required:
            try:
                evidence = conn.execute(
                    """SELECT action, created_at FROM audit_trail
                       WHERE event_type LIKE %s AND project_id = %s
                       AND created_at >= %s
                       ORDER BY created_at DESC LIMIT 1""",
                    (f"%{proc}%", project_id, since),
                ).fetchone()
            except Exception:
                evidence = None

            if evidence:
                invoked_count += 1
                results.append(
                    {
                        "process": proc,
                        "status": "invoked",
                        "last_run": evidence["created_at"],
                    }
                )
            else:
                results.append(
                    {
                        "process": proc,
                        "status": "missing",
                        "last_run": None,
                    }
                )

        total = len(required)
        missing = [r["process"] for r in results if r["status"] == "missing"]

        return {
            "project_id": project_id,
            "loop_type": loop_type,
            "lookback_hours": hours,
            "required_processes": total,
            "invoked_processes": invoked_count,
            "missing_processes": missing,
            "pass_rate": round(invoked_count / max(total, 1), 2),
            "all_invoked": len(missing) == 0,
            "details": results,
            "checked_at": now_iso(),
        }
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Process Verifier — required process invocation check")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--db-path", type=Path, default=None)

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--verify", action="store_true", help="Verify processes for a loop")
    group.add_argument("--check", action="store_true", help="Check processes for a project")

    parser.add_argument("--loop-id", type=str, default="")
    parser.add_argument("--project-id", type=str, default="")
    parser.add_argument("--loop-type", type=str, default="build")
    parser.add_argument("--hours", type=int, default=24)

    args = parser.parse_args()

    try:
        if args.verify:
            if not args.loop_id:
                parser.error("--verify requires --loop-id")
            result = verify_loop_processes(args.loop_id, args.db_path)
        else:
            if not args.project_id:
                parser.error("--check requires --project-id")
            result = check_project_processes(args.project_id, args.loop_type, args.hours, args.db_path)

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            if "error" in result:
                print(f"ERROR: {result['error']}")
            else:
                status = "PASS" if result.get("all_invoked") else "FAIL"
                print(f"\n=== Process Verification [{status}] ===")
                for d in result.get("details", []):
                    icon = "+" if d["status"] == "invoked" else "-"
                    print(f"  [{icon}] {d['process']}: {d['status']}")
                missing = result.get("missing_processes", [])
                if missing:
                    print(f"\n  Missing: {', '.join(missing)}")
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
