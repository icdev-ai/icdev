# CUI // SP-CTI
"""
Flask Blueprint for batch operation execution and status tracking.

Provides endpoints to start multi-step batch operations (e.g., full ATO
package, security scan suite), poll their progress, cancel running batches,
list the catalog, and view run history.

All batch steps run as subprocesses in a background thread so the HTTP
request returns immediately.  Status is held in an in-memory dict keyed
by run_id (capped at 50 entries, oldest evicted on overflow).

Completed runs are persisted to ``data/icdev.db`` tables ``batch_runs``
and ``batch_run_steps`` for auditing and history review.

Enhancements:
  - stop_on_failure mode: skip remaining steps when a step fails
  - cancel: set cancel event to stop between steps
  - DB persistence: completed runs written to icdev.db
  - rate limiting: max 3 concurrent + 10/hour per project
"""

import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
MAX_RUNS = 50
STEP_TIMEOUT = 300  # 5 minutes per step
RATE_MAX_CONCURRENT = 3
RATE_MAX_PER_HOUR = 10


# ---------------------------------------------------------------------------
# DB persistence (batch_runs + batch_run_steps tables)
# ---------------------------------------------------------------------------

def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_tables():
    """Create batch history tables if they don't exist."""
    conn = _get_db()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS batch_runs (
                run_id        TEXT PRIMARY KEY,
                batch_id      TEXT NOT NULL,
                batch_name    TEXT NOT NULL,
                project_id    TEXT NOT NULL,
                status        TEXT NOT NULL,
                stop_on_failure INTEGER DEFAULT 0,
                start_time    TEXT,
                end_time      TEXT,
                created_at    TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS batch_run_steps (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id        TEXT NOT NULL REFERENCES batch_runs(run_id),
                step_index    INTEGER NOT NULL,
                name          TEXT NOT NULL,
                tool_path     TEXT,
                status        TEXT NOT NULL,
                return_code   INTEGER,
                output_summary TEXT,
                start_time    TEXT,
                end_time      TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_batch_runs_project
                ON batch_runs(project_id, start_time);
            CREATE INDEX IF NOT EXISTS idx_batch_run_steps_run
                ON batch_run_steps(run_id);
        """)
        conn.commit()
    except Exception:
        pass  # Best-effort; table may already exist
    finally:
        conn.close()


def _persist_run(run: dict) -> None:
    """Write a completed run and its steps to icdev.db for history."""
    conn = _get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO batch_runs "
            "(run_id, batch_id, batch_name, project_id, status, "
            " stop_on_failure, start_time, end_time) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run["run_id"], run["batch_id"], run["batch_name"],
                run["project_id"], run["status"],
                1 if run.get("stop_on_failure") else 0,
                run.get("start_time"), run.get("end_time"),
            ),
        )
        # Delete any old step rows for this run (in case of re-persist)
        conn.execute(
            "DELETE FROM batch_run_steps WHERE run_id = ?",
            (run["run_id"],),
        )
        for idx, step in enumerate(run.get("steps", [])):
            conn.execute(
                "INSERT INTO batch_run_steps "
                "(run_id, step_index, name, tool_path, status, "
                " return_code, output_summary, start_time, end_time) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run["run_id"], idx, step["name"],
                    step.get("tool_path", ""),
                    step["status"], step.get("return_code"),
                    step.get("output_summary", ""),
                    step.get("start_time"), step.get("end_time"),
                ),
            )
        conn.commit()
    except Exception:
        pass  # Best-effort persist
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Rate limiter — simple in-memory token bucket per project
# ---------------------------------------------------------------------------

_rate_lock = threading.Lock()
_rate_running: dict[str, int] = {}           # project_id -> count of running batches
_rate_history: dict[str, list[float]] = {}   # project_id -> list of start timestamps


def _rate_check(project_id: str) -> str | None:
    """Return an error message if rate limit exceeded, else None."""
    with _rate_lock:
        # Check concurrent
        running = _rate_running.get(project_id, 0)
        if running >= RATE_MAX_CONCURRENT:
            return (f"Rate limit: {running} batches already running for "
                    f"project {project_id} (max {RATE_MAX_CONCURRENT})")
        # Check hourly
        now = time.time()
        cutoff = now - 3600
        history = _rate_history.get(project_id, [])
        history = [t for t in history if t > cutoff]
        _rate_history[project_id] = history
        if len(history) >= RATE_MAX_PER_HOUR:
            return (f"Rate limit: {len(history)} batches in last hour for "
                    f"project {project_id} (max {RATE_MAX_PER_HOUR}/hr)")
    return None


def _rate_acquire(project_id: str) -> None:
    """Mark a batch as started for rate limiting."""
    with _rate_lock:
        _rate_running[project_id] = _rate_running.get(project_id, 0) + 1
        _rate_history.setdefault(project_id, []).append(time.time())


def _rate_release(project_id: str) -> None:
    """Mark a batch as finished for rate limiting."""
    with _rate_lock:
        _rate_running[project_id] = max(0, _rate_running.get(project_id, 1) - 1)

# ---------------------------------------------------------------------------
# Batch catalog — 4 built-in operations
# ---------------------------------------------------------------------------

BATCH_CATALOG = {
    "quick_ato": {
        "name": "Full ATO Package",
        "description": (
            "Generate all ATO artifacts: FIPS 199 categorization, "
            "FIPS 200 validation, SSP, POA&M, STIG checks, and SBOM"
        ),
        "icon": "shield",
        "steps": [
            {
                "name": "FIPS 199 Categorization",
                "tool": "tools/compliance/fips199_categorizer.py",
                "args": "--categorize",
            },
            {
                "name": "FIPS 200 Validation",
                "tool": "tools/compliance/fips200_validator.py",
                "args": "",
            },
            {
                "name": "SSP Generation",
                "tool": "tools/compliance/ssp_generator.py",
                "args": "",
            },
            {
                "name": "POA&M Generation",
                "tool": "tools/compliance/poam_generator.py",
                "args": "",
            },
            {
                "name": "STIG Checks",
                "tool": "tools/compliance/stig_checker.py",
                "args": "",
            },
            {
                "name": "SBOM Generation",
                "tool": "tools/compliance/sbom_generator.py",
                "args": "--project-dir .",
            },
        ],
    },
    "security_scan": {
        "name": "Security Scan Suite",
        "description": (
            "Run the full security scanning pipeline: SAST analysis, "
            "dependency audit, secret detection, and container scanning"
        ),
        "icon": "lock",
        "steps": [
            {
                "name": "SAST Analysis",
                "tool": "tools/security/sast_runner.py",
                "args": "--project-dir .",
            },
            {
                "name": "Dependency Audit",
                "tool": "tools/security/dependency_auditor.py",
                "args": "--project-dir .",
            },
            {
                "name": "Secret Detection",
                "tool": "tools/security/secret_detector.py",
                "args": "--project-dir .",
            },
            {
                "name": "Container Scan",
                "tool": "tools/security/container_scanner.py",
                "args": "--image latest",
            },
        ],
    },
    "compliance_check": {
        "name": "Multi-Framework Check",
        "description": (
            "Assess compliance across NIST crosswalk, FedRAMP, CMMC, "
            "and generate OSCAL machine-readable output"
        ),
        "icon": "clipboard",
        "steps": [
            {
                "name": "Crosswalk Coverage",
                "tool": "tools/compliance/crosswalk_engine.py",
                "args": "--coverage",
            },
            {
                "name": "FedRAMP Assessment",
                "tool": "tools/compliance/fedramp_assessor.py",
                "args": "--baseline moderate",
            },
            {
                "name": "CMMC Assessment",
                "tool": "tools/compliance/cmmc_assessor.py",
                "args": "--level 2",
            },
            {
                "name": "OSCAL Generation",
                "tool": "tools/compliance/oscal_generator.py",
                "args": "--artifact ssp",
            },
        ],
    },
    "build_validate": {
        "name": "Build & Validate",
        "description": (
            "Lint, format, run the full test suite, and regenerate "
            "the SBOM for a clean build validation"
        ),
        "icon": "hammer",
        "steps": [
            {
                "name": "Lint",
                "tool": "tools/builder/linter.py",
                "args": "--project-dir .",
            },
            {
                "name": "Format",
                "tool": "tools/builder/formatter.py",
                "args": "--project-dir .",
            },
            {
                "name": "Test Suite",
                "tool": "tools/testing/test_orchestrator.py",
                "args": "--project-dir .",
            },
            {
                "name": "SBOM Refresh",
                "tool": "tools/compliance/sbom_generator.py",
                "args": "--project-dir .",
            },
        ],
    },
}

# ---------------------------------------------------------------------------
# In-memory run store (OrderedDict for LRU-style eviction)
# ---------------------------------------------------------------------------

_runs: OrderedDict = OrderedDict()
_runs_lock = threading.Lock()
_cancel_events: dict[str, threading.Event] = {}


def _store_run(run_id: str, data: dict) -> None:
    """Insert a run record, evicting the oldest if at capacity."""
    with _runs_lock:
        if run_id in _runs:
            _runs.move_to_end(run_id)
            _runs[run_id] = data
        else:
            if len(_runs) >= MAX_RUNS:
                oldest_id, _ = _runs.popitem(last=False)
                _cancel_events.pop(oldest_id, None)
            _runs[run_id] = data


def _get_run(run_id: str) -> dict | None:
    """Retrieve a run record by id."""
    with _runs_lock:
        return _runs.get(run_id)


# ---------------------------------------------------------------------------
# Background batch executor
# ---------------------------------------------------------------------------


def _execute_batch(run_id: str, batch_id: str, project_id: str,
                   stop_on_failure: bool = False) -> None:
    """Run each step of a batch sequentially in a background thread.

    Args:
        run_id: Unique identifier for this run.
        batch_id: Key into BATCH_CATALOG.
        project_id: Project context for each tool.
        stop_on_failure: If True, skip remaining steps after first failure.
    """
    run = _get_run(run_id)
    if run is None:
        return

    cancel_event = _cancel_events.get(run_id)
    catalog_entry = BATCH_CATALOG.get(batch_id)
    if catalog_entry is None:
        run["status"] = "failed"
        return

    python = sys.executable or "python"
    had_failure = False

    for idx, step_record in enumerate(run["steps"]):
        # Check for cancellation between steps
        if cancel_event and cancel_event.is_set():
            step_record["status"] = "cancelled"
            step_record["output_summary"] = "Cancelled by user"
            # Mark all remaining steps as cancelled
            for remaining in run["steps"][idx + 1:]:
                remaining["status"] = "cancelled"
                remaining["output_summary"] = "Cancelled by user"
            run["status"] = "cancelled"
            run["end_time"] = datetime.now(timezone.utc).isoformat()
            _persist_run(run)
            _rate_release(project_id)
            return

        # Stop-on-failure: skip remaining steps after a failure
        if had_failure and stop_on_failure:
            step_record["status"] = "skipped"
            step_record["output_summary"] = "Skipped due to prior step failure (stop-on-failure mode)"
            continue

        step_def = catalog_entry["steps"][idx]
        tool_path = str(BASE_DIR / step_def["tool"])

        # Build command
        cmd = [python, tool_path, "--project-id", project_id, "--json"]
        extra_args = step_def.get("args", "")
        if extra_args:
            cmd.extend(extra_args.split())

        step_record["status"] = "running"
        step_record["start_time"] = datetime.now(timezone.utc).isoformat()

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=STEP_TIMEOUT,
                cwd=str(BASE_DIR),
            )
            step_record["return_code"] = result.returncode
            stdout = result.stdout or ""
            stderr = result.stderr or ""
            output = stdout if stdout else stderr
            step_record["output_summary"] = output[:500]

            if result.returncode == 0:
                step_record["status"] = "completed"
            else:
                step_record["status"] = "failed"
                had_failure = True

        except subprocess.TimeoutExpired:
            step_record["status"] = "failed"
            step_record["return_code"] = -1
            step_record["output_summary"] = (
                f"Step timed out after {STEP_TIMEOUT}s"
            )
            had_failure = True
        except FileNotFoundError:
            step_record["status"] = "failed"
            step_record["return_code"] = -2
            step_record["output_summary"] = f"Tool not found: {tool_path}"
            had_failure = True
        except Exception as exc:
            step_record["status"] = "failed"
            step_record["return_code"] = -3
            step_record["output_summary"] = str(exc)[:500]
            had_failure = True

        step_record["end_time"] = datetime.now(timezone.utc).isoformat()

    # Determine overall status
    statuses = [s["status"] for s in run["steps"]]
    if all(s == "completed" for s in statuses):
        run["status"] = "completed"
    elif any(s == "skipped" for s in statuses):
        run["status"] = "stopped_on_failure"
    elif any(s == "failed" for s in statuses):
        run["status"] = "completed_with_failures"
    else:
        run["status"] = "completed"

    run["end_time"] = datetime.now(timezone.utc).isoformat()
    _cancel_events.pop(run_id, None)

    # Persist completed run to DB for history (best-effort)
    _persist_run(run)
    _rate_release(project_id)


# ---------------------------------------------------------------------------
# Flask Blueprint
# ---------------------------------------------------------------------------

batch_api = Blueprint("batch_api", __name__, url_prefix="/api/batch")

# Ensure history tables exist on import (best-effort)
_ensure_tables()


@batch_api.route("/catalog", methods=["GET"])
def catalog():
    """List available batch operations."""
    items = []
    for batch_id, entry in BATCH_CATALOG.items():
        items.append({
            "batch_id": batch_id,
            "name": entry["name"],
            "description": entry["description"],
            "icon": entry["icon"],
            "step_count": len(entry["steps"]),
            "steps": [s["name"] for s in entry["steps"]],
        })
    return jsonify({"catalog": items})


@batch_api.route("/execute", methods=["POST"])
def execute():
    """Start a batch operation.

    Body: {
        "batch_id": "quick_ato",
        "project_id": "proj-123",
        "stop_on_failure": false    // optional, default false
    }
    Returns: {"run_id": "...", "status": "running"}
    """
    body = request.get_json(silent=True) or {}
    batch_id = body.get("batch_id", "")
    project_id = body.get("project_id", "")
    stop_on_failure = bool(body.get("stop_on_failure", False))

    if not batch_id or batch_id not in BATCH_CATALOG:
        return jsonify({
            "error": "Invalid batch_id",
            "valid_ids": list(BATCH_CATALOG.keys()),
        }), 400

    if not project_id:
        return jsonify({"error": "project_id is required"}), 400

    # Rate limit check
    rate_err = _rate_check(project_id)
    if rate_err:
        return jsonify({"error": rate_err}), 429

    catalog_entry = BATCH_CATALOG[batch_id]
    run_id = str(uuid.uuid4())

    # Build step records
    steps = []
    for step_def in catalog_entry["steps"]:
        steps.append({
            "name": step_def["name"],
            "tool_path": step_def["tool"],
            "status": "pending",
            "start_time": None,
            "end_time": None,
            "output_summary": None,
            "return_code": None,
        })

    run_data = {
        "run_id": run_id,
        "batch_id": batch_id,
        "batch_name": catalog_entry["name"],
        "project_id": project_id,
        "status": "running",
        "stop_on_failure": stop_on_failure,
        "start_time": datetime.now(timezone.utc).isoformat(),
        "end_time": None,
        "steps": steps,
    }

    _store_run(run_id, run_data)
    _rate_acquire(project_id)

    # Create cancel event for this run
    cancel_event = threading.Event()
    _cancel_events[run_id] = cancel_event

    # Launch background thread
    thread = threading.Thread(
        target=_execute_batch,
        args=(run_id, batch_id, project_id, stop_on_failure),
        daemon=True,
    )
    thread.start()

    return jsonify({
        "run_id": run_id,
        "status": "running",
        "stop_on_failure": stop_on_failure,
    }), 202


@batch_api.route("/cancel/<run_id>", methods=["POST"])
def cancel(run_id):
    """Cancel a running batch operation.

    Sets a threading.Event that the executor checks between steps.
    Already-running steps will complete, but no further steps start.

    Returns: {"run_id": "...", "status": "cancelling"}
    """
    run = _get_run(run_id)
    if run is None:
        return jsonify({"error": "Run not found", "run_id": run_id}), 404

    if run["status"] != "running":
        return jsonify({
            "error": "Cannot cancel — batch is not running",
            "current_status": run["status"],
        }), 409

    cancel_event = _cancel_events.get(run_id)
    if cancel_event:
        cancel_event.set()

    return jsonify({"run_id": run_id, "status": "cancelling"})


@batch_api.route("/status/<run_id>", methods=["GET"])
def status(run_id):
    """Get status of a running or completed batch.

    Returns steps with status (pending/running/completed/failed/skipped/cancelled).
    """
    run = _get_run(run_id)
    if run is None:
        return jsonify({"error": "Run not found", "run_id": run_id}), 404

    return jsonify(run)


@batch_api.route("/history", methods=["GET"])
def history():
    """Return persisted batch run history from icdev.db.

    Query params:
        project_id — filter by project (optional)
        limit      — max rows (default 25, max 100)
        offset     — pagination offset (default 0)

    Returns: {runs: [...], total: N, classification: "CUI"}
    """
    project_id = request.args.get("project_id", "")
    limit = min(int(request.args.get("limit", 25)), 100)
    offset = int(request.args.get("offset", 0))

    conn = _get_db()
    try:
        if project_id:
            runs = conn.execute(
                "SELECT * FROM batch_runs WHERE project_id = ? "
                "ORDER BY start_time DESC LIMIT ? OFFSET ?",
                (project_id, limit, offset),
            ).fetchall()
            total = conn.execute(
                "SELECT COUNT(*) as cnt FROM batch_runs WHERE project_id = ?",
                (project_id,),
            ).fetchone()["cnt"]
        else:
            runs = conn.execute(
                "SELECT * FROM batch_runs ORDER BY start_time DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            total = conn.execute(
                "SELECT COUNT(*) as cnt FROM batch_runs",
            ).fetchone()["cnt"]

        result = []
        for r in runs:
            run_dict = dict(r)
            # Attach steps for each run
            steps = conn.execute(
                "SELECT * FROM batch_run_steps WHERE run_id = ? ORDER BY step_index",
                (r["run_id"],),
            ).fetchall()
            run_dict["steps"] = [dict(s) for s in steps]
            result.append(run_dict)

        return jsonify({
            "runs": result,
            "total": total,
            "limit": limit,
            "offset": offset,
            "classification": "CUI",
        })
    finally:
        conn.close()
