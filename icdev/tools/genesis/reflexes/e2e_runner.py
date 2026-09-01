#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis E2E Runner Reflex — daily Playwright smoke suite via Kanban scheduler.

Creates a Kanban task (task_type=test, title contains 'playwright') once per
day.  The Kanban scheduler picks it up, dispatches to Claude CLI, which runs
`npx playwright test tests/e2e/ --project=chromium --reporter=line`.

GREEN tier — only creates a DB record, reads nothing sensitive.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.kanban.lane_conflicts import TERMINAL_STATUSES  # noqa: E402
from tools.kanban.task_factory import create_tasks  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("e2e_runner")

IMPLEMENTATION_STATUS = "full"

# Anomaly detection defaults — all overridable via config dict passed to run()
_ANOMALY_DEFAULTS: dict = {
    "base_interval_hours": 24,
    "min_interval_hours": 6,
    "max_interval_hours": 48,
    "lookback_runs": 20,
    "failure_duration_threshold_hours": 4.0,
    "failure_rate_threshold": 0.3,
    "consecutive_fail_threshold": 2,
    "min_runs_for_analysis": 3,
}

_TASK_TITLE = "[AUTO-RUN] Playwright E2E Suite — full smoke"
_TASK_DESC = (
    "Run the full Playwright E2E smoke suite targeting all menus, use cases, "
    "canvases, and chat right panels.\n\n"
    "Steps (execute in order):\n\n"
    "1. Run route smoke to catch broken pages before spending time on full E2E:\n"
    "```\n"
    "python tools/testing/route_smoke.py --all\n"
    "```\n"
    "   If any route fails, STOP and report the failures. Do NOT proceed to step 2.\n\n"
    "2. Run the full Playwright suite:\n"
    "```\n"
    "npx playwright test tests/e2e/ --project=chromium --reporter=line 2>&1 | tee .tmp/pw_out.txt\n"
    "```\n\n"
    "3. Capture the results to the structured build log:\n"
    "```python\n"
    "import subprocess, re\n"
    "from tools.logging.build_logger import capture_playwright\n"
    "out = open('.tmp/pw_out.txt').read()\n"
    "rc = 0 if 'failed' not in out.lower() else 1\n"
    "capture_playwright(returncode=rc, stdout=out)\n"
    "```\n\n"
    "4. Report:\n"
    "   - Total pass / fail / skip count\n"
    "   - If any tests failed, list failing test titles (max 20)\n"
    "   - Screenshots: `.tmp/test_runs/screenshots/`\n"
    "   - HTML report: `npx playwright show-report`\n"
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _pending_run_exists(conn) -> bool:
    """True when a runner task is still OPEN -- in any non-terminal state.

    ENUMERATING THE LIVE STATES IS WHAT BROKE. This asked for
    ``status IN ('backlog', 'in_progress')`` and omitted `scheduled`, which is
    where a task spends most of its life: the scheduler promotes it out of
    backlog within minutes, the guard then sees nothing pending, and the next
    cycle seeds another. MEASURED on the live board 2026-08-31 --
    `task-e2e-393aebca` (seeded 08-30) sat in `scheduled` while
    `task-e2e-324a69a9` was seeded on 08-31 with a BYTE-IDENTICAL description.
    25 earlier runs never doubled only because each happened to complete
    before the next cycle.

    So the predicate is INVERTED: pending is "not terminal". A live state this
    function has never heard of -- `validating` is already on this board --
    then counts as pending by default, which is the safe direction: the cost
    of a false pending is one skipped cycle, and the cost of a false absence
    is a duplicate that a human has to reconcile.
    """
    placeholders = ", ".join(["%s"] * len(TERMINAL_STATUSES))
    row = conn.execute(
        f"""
        SELECT id FROM kanban_tasks
        WHERE title LIKE '%%Playwright E2E Suite%%'
          AND status NOT IN ({placeholders})
        LIMIT 1
        """,                                     # nosec B608 -- fixed set
        tuple(TERMINAL_STATUSES),
    ).fetchone()
    return row is not None


def _last_completed_at(conn) -> datetime | None:
    """Return when the last E2E runner task was completed, or None."""
    row = conn.execute(
        """
        SELECT completed_at FROM kanban_tasks
        WHERE title LIKE '%Playwright E2E Suite%'
          AND status = 'done'
        ORDER BY completed_at DESC
        LIMIT 1
        """,
    ).fetchone()
    if row:
        val = dict(row).get("completed_at")
        if val:
            if isinstance(val, datetime):
                return val
            try:
                return datetime.fromisoformat(str(val))
            except ValueError:
                pass
    return None


def _create_run_task() -> str | None:
    """Seed the nightly runner task through the canonical seeder (rem-hyg-06).

    Takes no ``conn``: ``create_tasks`` opens and commits its own. Returns None
    when the seeder skipped the row (an id that already exists).
    """
    import uuid

    task_id = f"task-e2e-{uuid.uuid4().hex[:8]}"
    now = _utcnow()
    created = create_tasks([{
        "id": task_id,
        "title": _TASK_TITLE,
        "description": _TASK_DESC,
        "task_type": "test",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": now.isoformat(),
        "dispatch_source": "e2e_runner_reflex",
    }])
    return task_id if created else None


def _get_recent_run_history(conn, lookback_runs: int) -> list[dict]:
    """Fetch recent completed E2E run records for anomaly analysis."""
    rows = conn.execute(
        """
        SELECT id, title, status, created_at, completed_at
        FROM kanban_tasks
        WHERE title LIKE '%Playwright E2E Suite%'
          AND status = 'done'
        ORDER BY completed_at DESC
        LIMIT %s
        """,
        (lookback_runs,),
    ).fetchall()
    return [dict(r) for r in rows]


def _detect_anomaly(history: list[dict], cfg: dict) -> tuple[bool, str, float]:
    """Analyse run history and return (anomaly_detected, reason, anomaly_score in [0,1]).

    A run is counted as a failure when its wall-clock duration exceeds
    failure_duration_threshold_hours (proxy for a stuck or crashing suite).
    """
    min_runs = int(cfg.get("min_runs_for_analysis", _ANOMALY_DEFAULTS["min_runs_for_analysis"]))
    fail_dur = float(cfg.get("failure_duration_threshold_hours", _ANOMALY_DEFAULTS["failure_duration_threshold_hours"]))
    fail_rate_thr = float(cfg.get("failure_rate_threshold", _ANOMALY_DEFAULTS["failure_rate_threshold"]))
    consec_thr = int(cfg.get("consecutive_fail_threshold", _ANOMALY_DEFAULTS["consecutive_fail_threshold"]))

    if len(history) < min_runs:
        return False, "insufficient_history", 0.0

    failures = 0
    consecutive = 0
    max_consecutive = 0

    for run in history:
        completed = run.get("completed_at")
        created = run.get("created_at")
        is_slow = False
        if completed and created:
            try:
                c_dt = datetime.fromisoformat(str(completed)) if not isinstance(completed, datetime) else completed
                s_dt = datetime.fromisoformat(str(created)) if not isinstance(created, datetime) else created
                if (c_dt - s_dt).total_seconds() / 3600 > fail_dur:
                    is_slow = True
            except (ValueError, TypeError):
                pass

        if is_slow:
            failures += 1
            consecutive += 1
            max_consecutive = max(max_consecutive, consecutive)
        else:
            consecutive = 0

    total = len(history)
    failure_rate = failures / total
    # Score weighs both overall rate and streak severity
    anomaly_score = min(1.0, failure_rate + (max_consecutive * 0.15))

    if max_consecutive >= consec_thr:
        return True, f"consecutive_slow_runs={max_consecutive}", anomaly_score
    if failure_rate >= fail_rate_thr:
        return True, f"high_failure_rate={failure_rate:.2f}", anomaly_score

    return False, "normal", anomaly_score


def _adaptive_interval(base: float, anomaly_detected: bool, anomaly_score: float, cfg: dict) -> float:
    """Return an adjusted interval clamped to [min_interval_hours, max_interval_hours]."""
    lo = float(cfg.get("min_interval_hours", _ANOMALY_DEFAULTS["min_interval_hours"]))
    hi = float(cfg.get("max_interval_hours", _ANOMALY_DEFAULTS["max_interval_hours"]))

    if anomaly_detected:
        # Scale toward lo proportionally to anomaly severity
        scale = max(0.25, 1.0 - anomaly_score)
        interval = base * scale
    else:
        interval = base

    return max(lo, min(hi, interval))


def run(config: dict, state: object) -> dict:
    """Main reflex entry point called by the Genesis daemon."""
    base_interval = float(config.get("interval_hours", config.get("base_interval_hours", _ANOMALY_DEFAULTS["base_interval_hours"])))
    lookback_runs = int(config.get("lookback_runs", _ANOMALY_DEFAULTS["lookback_runs"]))

    results: dict = {
        "reflex": "e2e_runner",
        "timestamp": _utcnow().isoformat(),
        "action": "skip",
        "task_id": None,
        "anomaly_detected": False,
        "anomaly_score": 0.0,
        "anomaly_reason": "not_evaluated",
        "interval_hours": base_interval,
    }

    conn = None
    try:
        conn = get_connection()

        # Skip if a run is already pending / in flight
        if _pending_run_exists(conn):
            results["reason"] = "run already pending or in_progress"
            return results

        # Anomaly detection — adaptively adjust the run interval
        history = _get_recent_run_history(conn, lookback_runs)
        anomaly_detected, anomaly_reason, anomaly_score = _detect_anomaly(history, config)
        interval_hours = _adaptive_interval(base_interval, anomaly_detected, anomaly_score, config)

        results["anomaly_detected"] = anomaly_detected
        results["anomaly_score"] = round(anomaly_score, 3)
        results["anomaly_reason"] = anomaly_reason
        results["interval_hours"] = round(interval_hours, 2)

        if anomaly_detected:
            logger.warning(
                "e2e_runner: anomaly detected (%s, score=%.2f) — interval reduced %.1f→%.1fh",
                anomaly_reason,
                anomaly_score,
                base_interval,
                interval_hours,
            )

        # Skip if last run completed less than interval_hours ago
        last = _last_completed_at(conn)
        if last:
            elapsed_hours = (_utcnow() - last).total_seconds() / 3600
            if elapsed_hours < interval_hours:
                results["reason"] = f"last run {elapsed_hours:.1f}h ago (interval={interval_hours:.1f}h)"
                return results

        # Create the task
        task_id = _create_run_task()
        conn.commit()
        if not task_id:
            results["reason"] = "seeder skipped the task (id already on the board)"
            return results
        results["action"] = "created"
        results["task_id"] = task_id
        logger.info("e2e_runner: created Kanban task %s", task_id)

    except Exception as exc:
        results["error"] = str(exc)
        logger.exception("e2e_runner reflex failed: %s", exc)
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    return results


if __name__ == "__main__":
    # Load THIS repo's .env so a direct CLI run uses the same board/PG config as the
    # GenesisDaemon. override=True: a pip-installed ICDEV in site-packages may have
    # already loaded a different checkout's .env at import. Repo root via __file__, not cwd.
    try:
        from pathlib import Path as _EnvPath
        from dotenv import load_dotenv as _load_dotenv
        _load_dotenv(_EnvPath(__file__).resolve().parents[3] / ".env", override=True)
    except ImportError:
        pass
    import json
    result = run({}, None)  # uses _ANOMALY_DEFAULTS for all thresholds
    print(json.dumps(result, indent=2))
