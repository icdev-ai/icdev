
# [TEMPLATE: CUI // SP-CTI]
"""Automated runbook execution for ICDEV™ SRE module.

Manages runbooks with regex-based alert matching, risk-tiered execution,
and full audit trail of all executions.
"""

import argparse
import json
import logging
import re
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

import yaml  # noqa: E402

from tools.db.storage import get_connection  # noqa: E402

logger = get_logger(__name__)

CONFIG_PATH = BASE_DIR / "args" / "sre_config.yaml"

RISK_LEVELS = ("green", "yellow", "orange")
EXECUTION_STATUSES = ("pending", "running", "completed", "failed", "rolled_back")


def _load_config() -> dict:
    """Load SRE configuration from args/sre_config.yaml."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def _get_runbook_config() -> dict:
    """Get runbook-specific config with defaults."""
    cfg = _load_config().get("runbooks", {})
    return {
        "max_executions_per_hour": cfg.get("max_executions_per_hour", 3),
        "default_risk_level": cfg.get("default_risk_level", "green"),
        "auto_execute_green": cfg.get("auto_execute_green", True),
        "auto_execute_yellow": cfg.get("auto_execute_yellow", False),
        "auto_execute_orange": cfg.get("auto_execute_orange", False),
        "step_timeout_seconds": cfg.get("step_timeout_seconds", 300),
    }


def init_tables(conn: sqlite3.Connection) -> None:
    """Create SRE runbook tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sre_runbooks (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            trigger_pattern TEXT NOT NULL,
            steps_json TEXT NOT NULL,
            risk_level TEXT NOT NULL DEFAULT 'green' CHECK(risk_level IN ('green', 'yellow', 'orange')),
            auto_execute INTEGER NOT NULL DEFAULT 0,
            max_executions_per_hour INTEGER NOT NULL DEFAULT 3,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sre_runbook_executions (
            id TEXT PRIMARY KEY,
            runbook_id TEXT NOT NULL,
            trigger_source TEXT,
            trigger_text TEXT,
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'running', 'completed', 'failed', 'rolled_back')),
            steps_completed INTEGER NOT NULL DEFAULT 0,
            steps_total INTEGER NOT NULL DEFAULT 0,
            output_log TEXT,
            duration_ms INTEGER,
            executed_by TEXT NOT NULL DEFAULT 'manual',
            created_at TEXT NOT NULL,
            completed_at TEXT,
            FOREIGN KEY (runbook_id) REFERENCES sre_runbooks(id)
        );
    """)
    conn.commit()


def create_runbook(name: str, description: str, trigger_pattern: str, steps: list, risk_level: str = "green") -> dict:
    """Create a new runbook.

    Args:
        name: Runbook name.
        description: What this runbook does.
        trigger_pattern: Regex pattern to match against alert text.
        steps: List of step dicts with keys: action, command, timeout_seconds, rollback_command.
        risk_level: green, yellow, or orange (maps to Genesis trust tiers).

    Returns:
        dict with created runbook details.
    """
    if risk_level not in RISK_LEVELS:
        return {"status": "error", "message": f"Invalid risk_level: {risk_level}. Must be one of {RISK_LEVELS}"}

    # Validate regex pattern
    try:
        re.compile(trigger_pattern)
    except re.error as e:
        return {"status": "error", "message": f"Invalid trigger_pattern regex: {e}"}

    # Validate steps format
    for i, step in enumerate(steps):
        if not isinstance(step, dict) or "action" not in step or "command" not in step:
            return {"status": "error", "message": f"Step {i} must have 'action' and 'command' keys"}

    cfg = _get_runbook_config()
    runbook_id = f"rb-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    # Determine auto_execute based on risk level and config
    auto_execute = 0
    if risk_level == "green" and cfg["auto_execute_green"]:
        auto_execute = 1
    elif risk_level == "yellow" and cfg["auto_execute_yellow"]:
        auto_execute = 1
    elif risk_level == "orange" and cfg["auto_execute_orange"]:
        auto_execute = 1

    conn = get_connection()
    init_tables(conn)

    conn.execute(
        """INSERT INTO sre_runbooks
           (id, name, description, trigger_pattern, steps_json, risk_level,
            auto_execute, max_executions_per_hour, created_at, updated_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            runbook_id,
            name,
            description,
            trigger_pattern,
            json.dumps(steps),
            risk_level,
            auto_execute,
            cfg["max_executions_per_hour"],
            now,
            now,
        ),
    )
    conn.commit()

    logger.info("Created runbook %s: %s", runbook_id, name)
    return {
        "status": "ok",
        "runbook_id": runbook_id,
        "name": name,
        "risk_level": risk_level,
        "auto_execute": bool(auto_execute),
        "steps_count": len(steps),
        "trigger_pattern": trigger_pattern,
    }


def match_runbook(alert_text: str) -> dict:
    """Find a runbook whose trigger_pattern matches the given alert text.

    Args:
        alert_text: Alert text to match against runbook trigger patterns.

    Returns:
        dict with matched runbook details, or None if no match.
    """
    conn = get_connection()
    init_tables(conn)

    rows = conn.execute(
        "SELECT id, name, trigger_pattern, risk_level, auto_execute, steps_json FROM sre_runbooks",
    ).fetchall()

    for row in rows:
        rb_id, name, pattern, risk_level, auto_exec, steps_json = row
        try:
            if re.search(pattern, alert_text, re.IGNORECASE):
                return {
                    "status": "ok",
                    "matched": True,
                    "runbook_id": rb_id,
                    "name": name,
                    "risk_level": risk_level,
                    "auto_execute": bool(auto_exec),
                    "steps_count": len(json.loads(steps_json)),
                    "trigger_pattern": pattern,
                }
        except re.error:
            logger.warning("Invalid regex in runbook %s: %s", rb_id, pattern)
            continue

    return {"status": "ok", "matched": False, "message": "No matching runbook found"}


def execute_runbook(
    runbook_id: str, trigger_source: str = "manual", trigger_text: str = "", dry_run: bool = False
) -> dict:
    """Execute a runbook's steps.

    Args:
        runbook_id: Runbook identifier.
        trigger_source: What triggered this execution.
        trigger_text: The alert/trigger text.
        dry_run: If True, log steps without executing commands.

    Returns:
        dict with execution results and output log.
    """
    conn = get_connection()
    init_tables(conn)

    row = conn.execute(
        "SELECT id, name, steps_json, risk_level, max_executions_per_hour FROM sre_runbooks WHERE id = %s",
        (runbook_id,),
    ).fetchone()

    if not row:
        return {"status": "error", "message": f"Runbook not found: {runbook_id}"}

    rb_id, name, steps_json, risk_level, max_per_hour = row
    steps = json.loads(steps_json)
    cfg = _get_runbook_config()

    # Check rate limit
    datetime.now(timezone.utc).isoformat()
    recent_count = conn.execute(
        """SELECT COUNT(*) FROM sre_runbook_executions
           WHERE runbook_id = %s AND created_at >= datetime('now', '-1 hour')""",
        (runbook_id,),
    ).fetchone()[0]

    if recent_count >= max_per_hour:
        return {
            "status": "error",
            "message": f"Rate limit exceeded: {recent_count}/{max_per_hour} executions in last hour",
        }

    execution_id = f"rbe-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    start_time = time.monotonic()

    conn.execute(
        """INSERT INTO sre_runbook_executions
           (id, runbook_id, trigger_source, trigger_text, status,
            steps_completed, steps_total, output_log, executed_by, created_at)
           VALUES (%s, %s, %s, %s, 'running', 0, %s, '[]', %s, %s)""",
        (
            execution_id,
            runbook_id,
            trigger_source,
            trigger_text,
            len(steps),
            "auto" if trigger_source != "manual" else "manual",
            now,
        ),
    )
    conn.commit()

    output_log = []
    steps_completed = 0
    final_status = "completed"

    for i, step in enumerate(steps):
        step_action = step.get("action", f"step-{i}")
        step_command = step.get("command", "")
        step_timeout = step.get("timeout_seconds", cfg["step_timeout_seconds"])
        step_rollback = step.get("rollback_command")

        step_entry = {
            "step": i + 1,
            "action": step_action,
            "command": step_command,
            "dry_run": dry_run,
        }

        if dry_run:
            step_entry["result"] = "skipped (dry run)"
            step_entry["exit_code"] = 0
            output_log.append(step_entry)
            steps_completed += 1
            continue

        try:
            result = subprocess.run(  # nosec B602 — runbook commands are admin-defined
                step_command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=step_timeout,
                cwd=str(BASE_DIR),
            )
            step_entry["exit_code"] = result.returncode
            step_entry["stdout"] = result.stdout[:2000] if result.stdout else ""
            step_entry["stderr"] = result.stderr[:2000] if result.stderr else ""

            if result.returncode != 0:
                step_entry["result"] = "failed"
                final_status = "failed"

                # Attempt rollback if available
                if step_rollback:
                    logger.warning("Step %d failed, attempting rollback: %s", i + 1, step_rollback)
                    try:
                        rb_result = subprocess.run(  # nosec B602 — rollback commands are admin-defined
                            step_rollback,
                            shell=True,
                            capture_output=True,
                            text=True,
                            timeout=step_timeout,
                            cwd=str(BASE_DIR),
                        )
                        step_entry["rollback_exit_code"] = rb_result.returncode
                        step_entry["rollback_output"] = rb_result.stdout[:1000]
                        final_status = "rolled_back"
                    except Exception as rb_err:
                        step_entry["rollback_error"] = str(rb_err)

                output_log.append(step_entry)
                break
            else:
                step_entry["result"] = "success"
                steps_completed += 1

        except subprocess.TimeoutExpired:
            step_entry["result"] = "timeout"
            step_entry["exit_code"] = -1
            final_status = "failed"
            output_log.append(step_entry)
            break
        except Exception as e:
            step_entry["result"] = "error"
            step_entry["error"] = str(e)
            final_status = "failed"
            output_log.append(step_entry)
            break

        output_log.append(step_entry)

    duration_ms = int((time.monotonic() - start_time) * 1000)
    completed_at = datetime.now(timezone.utc).isoformat()

    conn.execute(
        """UPDATE sre_runbook_executions
           SET status = %s, steps_completed = %s, output_log = %s,
               duration_ms = %s, completed_at = %s
           WHERE id = %s""",
        (final_status, steps_completed, json.dumps(output_log), duration_ms, completed_at, execution_id),
    )
    conn.commit()

    logger.info(
        "Runbook %s execution %s: %s (%d/%d steps)", runbook_id, execution_id, final_status, steps_completed, len(steps)
    )

    return {
        "status": "ok",
        "execution_id": execution_id,
        "runbook_id": runbook_id,
        "runbook_name": name,
        "execution_status": final_status,
        "steps_completed": steps_completed,
        "steps_total": len(steps),
        "duration_ms": duration_ms,
        "dry_run": dry_run,
        "output_log": output_log,
    }


def list_runbooks() -> list:
    """List all runbooks.

    Returns:
        list of dicts with runbook details.
    """
    conn = get_connection()
    init_tables(conn)

    rows = conn.execute(
        """SELECT id, name, description, trigger_pattern, risk_level,
                  auto_execute, max_executions_per_hour, created_at, updated_at
           FROM sre_runbooks
           ORDER BY name""",
    ).fetchall()

    cols = [
        "id",
        "name",
        "description",
        "trigger_pattern",
        "risk_level",
        "auto_execute",
        "max_executions_per_hour",
        "created_at",
        "updated_at",
    ]

    return [dict(zip(cols, row)) for row in rows]


def get_execution_history(limit: int = 20) -> list:
    """Get recent runbook execution history.

    Args:
        limit: Maximum number of executions to return.

    Returns:
        list of dicts with execution details.
    """
    conn = get_connection()
    init_tables(conn)

    rows = conn.execute(
        """SELECT e.id, e.runbook_id, r.name AS runbook_name,
                  e.trigger_source, e.trigger_text, e.status,
                  e.steps_completed, e.steps_total, e.duration_ms,
                  e.executed_by, e.created_at, e.completed_at
           FROM sre_runbook_executions e
           LEFT JOIN sre_runbooks r ON e.runbook_id = r.id
           ORDER BY e.created_at DESC
           LIMIT %s""",
        (limit,),
    ).fetchall()

    cols = [
        "id",
        "runbook_id",
        "runbook_name",
        "trigger_source",
        "trigger_text",
        "status",
        "steps_completed",
        "steps_total",
        "duration_ms",
        "executed_by",
        "created_at",
        "completed_at",
    ]

    return [dict(zip(cols, row)) for row in rows]


def check_runbook_health() -> dict:
    """Gate check for runbook system health.

    Checks for recent failures and rolled-back executions.

    Returns:
        dict with gate result.
    """
    conn = get_connection()
    init_tables(conn)

    # Check for failed/rolled_back executions in the last hour
    recent_failures = conn.execute(
        """SELECT e.id, r.name, e.status, e.created_at
           FROM sre_runbook_executions e
           LEFT JOIN sre_runbooks r ON e.runbook_id = r.id
           WHERE e.status IN ('failed', 'rolled_back')
             AND e.created_at >= datetime('now', '-1 hour')
           ORDER BY e.created_at DESC""",
    ).fetchall()

    total_recent = conn.execute(
        """SELECT COUNT(*) FROM sre_runbook_executions
           WHERE created_at >= datetime('now', '-1 hour')""",
    ).fetchone()[0]

    failing = []
    for row in recent_failures:
        failing.append(
            {
                "execution_id": row[0],
                "runbook_name": row[1],
                "status": row[2],
                "created_at": row[3],
            }
        )

    passed = len(failing) == 0
    return {
        "gate": "runbook_health",
        "passed": passed,
        "total_executions_last_hour": total_recent,
        "failed_count": len(failing),
        "failures": failing,
    }


def main():
    """CLI entry point for runbook executor."""
    parser = argparse.ArgumentParser(description="ICDEV™ SRE Runbook Executor")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--gate", action="store_true", help="Run gate check (exit 1 if recent failures)")

    # Actions
    parser.add_argument("--list", action="store_true", help="List all runbooks")
    parser.add_argument("--match", action="store_true", help="Match an alert against runbooks")
    parser.add_argument("--execute", action="store_true", help="Execute a runbook")
    parser.add_argument("--history", action="store_true", help="Show execution history")

    # Match/Execute args
    parser.add_argument("--alert", type=str, help="Alert text to match")
    parser.add_argument("--runbook-id", type=str, help="Runbook ID to execute")
    parser.add_argument("--trigger", type=str, default="", help="Trigger text for execution")
    parser.add_argument("--dry-run", action="store_true", help="Dry run (don't execute commands)")
    parser.add_argument("--limit", type=int, default=20, help="Limit for history (default 20)")

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if args.gate:
        result = check_runbook_health()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            status_str = "PASSED" if result["passed"] else "FAILED"
            print(f"Runbook Health Gate: {status_str}")
            print(f"  Executions (last hour): {result['total_executions_last_hour']}")
            print(f"  Failures: {result['failed_count']}")
            if result["failures"]:
                for f in result["failures"]:
                    print(f"    - {f['runbook_name']}: {f['status']} at {f['created_at']}")
        if not result["passed"]:
            sys.exit(1)

    elif args.list:
        runbooks = list_runbooks()
        if args.json:
            print(json.dumps(runbooks, indent=2))
        else:
            print(f"Runbooks ({len(runbooks)}):")
            for rb in runbooks:
                auto = "AUTO" if rb["auto_execute"] else "manual"
                print(f"  [{rb['risk_level']:>6}] {rb['id']} {rb['name']} ({auto})")

    elif args.match:
        if not args.alert:
            parser.error("--match requires --alert")
        result = match_runbook(args.alert)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            if result.get("matched"):
                print(f"Matched: {result['runbook_id']} ({result['name']}) risk={result['risk_level']}")
            else:
                print("No matching runbook found")

    elif args.execute:
        if not args.runbook_id:
            parser.error("--execute requires --runbook-id")
        result = execute_runbook(args.runbook_id, "manual", args.trigger, args.dry_run)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Execution {result.get('execution_id')}: {result.get('execution_status')}")
            print(f"  Steps: {result.get('steps_completed')}/{result.get('steps_total')}")
            print(f"  Duration: {result.get('duration_ms')}ms")

    elif args.history:
        history = get_execution_history(args.limit)
        if args.json:
            print(json.dumps(history, indent=2))
        else:
            print(f"Execution History ({len(history)} entries):")
            for h in history:
                print(
                    f"  {h['id']} [{h['status']:>11}] {h['runbook_name']} "
                    f"({h['steps_completed']}/{h['steps_total']} steps, {h['duration_ms']}ms)"
                )

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
