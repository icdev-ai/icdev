#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis QA Agent Reflex — 6-hour coverage gap sweep + E2E run scheduling.

Fires every 6 hours and does two things:
  1. Calls qa_agent_runner.discover_coverage_gaps() — finds canvases in
     component_registry.yaml that have no matching tests/e2e/<key>*.spec.ts.
     Creates one Kanban task (task_type=test) per gap so the kanban scheduler
     can route it to a QA Agent session.
  2. Creates a full-suite E2E sweep task if none is already pending.

GREEN tier — reads component_registry.yaml, queries kanban_tasks, creates tasks.
No file writes, no subprocess execution.
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.kanban.task_factory import create_tasks  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("qa_agent_reflex")

IMPLEMENTATION_STATUS = "full"

_SMOKE_TASK_TITLE_PREFIX = "[QA-SMOKE]"
_SWEEP_TASK_TITLE = "[QA-AGENT] Full E2E Suite Sweep"
_SWEEP_TASK_DESC = (
    "QA Agent: run the full Playwright E2E suite and report results.\n\n"
    "Steps (execute via qa_agent ACE co-worker or CLI):\n\n"
    "1. Route smoke check:\n"
    "```\n"
    "python tools/testing/route_smoke.py --all --json\n"
    "```\n"
    "   Stop and report if any route returns 500.\n\n"
    "2. Full E2E run:\n"
    "```\n"
    "python tools/testing/qa_agent_runner.py --run --json\n"
    "```\n\n"
    "3. For any failures: file kanban bug tasks via qa_agent_runner "
    "file_failure_tasks() — screenshot path + error message required.\n\n"
    "4. Persist run to ace_qa_runs via record_run().\n"
)

_GAP_TASK_DESC_TPL = (
    "QA Agent: no Playwright spec exists for the **{display_name}** canvas "
    "(key: `{canvas_key}`, route: `{route}`).\n\n"
    "Steps:\n"
    "1. Generate a spec stub:\n"
    "```\n"
    "python -c \"\n"
    "from tools.testing.qa_agent_runner import generate_spec_stub\n"
    "print(generate_spec_stub('{canvas_key}', '{display_name}', '{route}'))\n"
    "\"\n"
    "```\n"
    "2. Review the generated spec for correctness.\n"
    "3. Write to `tests/e2e/{canvas_key}_smoke.spec.ts` (HITL-gated if via QA Agent).\n"
    "4. Run to verify: `npx playwright test tests/e2e/{canvas_key}_smoke.spec.ts --project=chromium`\n"
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _pending_sweep_exists(conn) -> bool:
    row = conn.execute(
        """
        SELECT id FROM kanban_tasks
        WHERE title LIKE '%Full E2E Suite Sweep%'
          AND status IN ('backlog', 'in_progress')
        LIMIT 1
        """
    ).fetchone()
    return row is not None


def _last_sweep_completed_at(conn) -> datetime | None:
    row = conn.execute(
        """
        SELECT completed_at FROM kanban_tasks
        WHERE title LIKE '%Full E2E Suite Sweep%'
          AND status = 'done'
        ORDER BY completed_at DESC
        LIMIT 1
        """
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


def _pending_gap_task_exists(conn, canvas_key: str) -> bool:
    title_fragment = f"[QA-GAP] {canvas_key}"
    row = conn.execute(
        """
        SELECT id FROM kanban_tasks
        WHERE title LIKE %s
          AND status IN ('backlog', 'in_progress')
        LIMIT 1
        """,
        (f"%{title_fragment}%",),
    ).fetchone()
    return row is not None


def _pending_smoke_bug_exists(conn, route: str) -> bool:
    fragment = f"{_SMOKE_TASK_TITLE_PREFIX} {route}"
    row = conn.execute(
        """
        SELECT id FROM kanban_tasks
        WHERE title LIKE %s
          AND status IN ('backlog', 'in_progress')
        LIMIT 1
        """,
        (f"%{fragment}%",),
    ).fetchone()
    return row is not None


def _insert_smoke_bug_task(route: str, status: object, error: str) -> str | None:
    """Seed one card through the canonical seeder (rem-hyg-06).

    Takes no ``conn``: ``create_tasks`` opens and commits its own. Returns None
    when the seeder skipped the row.
    """
    task_id = f"task-qa-smoke-{uuid.uuid4().hex[:8]}"
    now = _utcnow().isoformat()
    title = f"{_SMOKE_TASK_TITLE_PREFIX} {route} returned {status}"
    desc = (
        f"Route smoke detected a failure on `{route}` (HTTP {status}).\n\n"
        f"**Error:** {error}\n\n"
        "Steps to remediate:\n"
        "1. Start the dashboard: `python -m tools.dashboard.app --port 5050`\n"
        f"2. Open `{route}` in a browser and confirm the error.\n"
        "3. Check the relevant `blueprint.py` and `db/init_db.py` for the canvas.\n"
        "   - If 500: likely an RLS/`get_canvas_connection()` issue or missing template variable.\n"
        "   - If 404: route may be missing from blueprint or url_prefix mismatch in registry.\n"
        "4. Fix the root cause and re-run: `python tools/testing/route_smoke.py --all --json`\n"
        "5. Close this task when smoke passes.\n"
    )
    created = create_tasks([{
        "id": task_id,
        "title": title,
        "description": desc,
        # rem-hyg-06: was "bug", which kanban_tasks_task_type_check forbids.
        "task_type": "fix",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": now,
        "dispatch_source": "qa_agent_reflex",
    }])
    return task_id if created else None


def _run_route_smoke(base: str = "http://localhost:5050") -> list:
    """Run route smoke against NAV_ROUTES; return list of failed result dicts.

    Returns [] if the server is unreachable (skip gracefully, not a hard failure).
    """
    try:
        from tools.testing.route_smoke import run_smoke, NAV_ROUTES
        _ok, results = run_smoke(NAV_ROUTES, base=base, verbose=False)
        return [r for r in results if not r.get("ok")]
    except Exception as exc:
        logger.warning("qa_agent_reflex: route smoke failed to run: %s", exc)
        return []


def _insert_sweep_task() -> str | None:
    """Seed one card through the canonical seeder (rem-hyg-06).

    Takes no ``conn``: ``create_tasks`` opens and commits its own. Returns None
    when the seeder skipped the row.
    """
    task_id = f"task-qa-sweep-{uuid.uuid4().hex[:8]}"
    now = _utcnow().isoformat()
    created = create_tasks([{
        "id": task_id,
        "title": _SWEEP_TASK_TITLE,
        "description": _SWEEP_TASK_DESC,
        "task_type": "test",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": now,
        "dispatch_source": "qa_agent_reflex",
    }])
    return task_id if created else None


def _insert_gap_task(canvas_key: str, display_name: str, route: str) -> str | None:
    """Seed one card through the canonical seeder (rem-hyg-06).

    Takes no ``conn``: ``create_tasks`` opens and commits its own. Returns None
    when the seeder skipped the row.
    """
    task_id = f"task-qa-gap-{canvas_key[:20]}-{uuid.uuid4().hex[:6]}"
    now = _utcnow().isoformat()
    title = f"[QA-GAP] {canvas_key} — missing E2E spec"
    desc = _GAP_TASK_DESC_TPL.format(
        canvas_key=canvas_key,
        display_name=display_name,
        route=route,
    )
    created = create_tasks([{
        "id": task_id,
        "title": title,
        "description": desc,
        "task_type": "test",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": now,
        "dispatch_source": "qa_agent_reflex",
    }])
    return task_id if created else None


def run(config: dict, state: object) -> dict:
    """Main reflex entry point called by the Genesis daemon."""
    interval_hours = float(config.get("interval_hours", 6.0))
    max_gap_tasks_per_run = int(config.get("max_gap_tasks_per_run", 5))

    result: dict = {
        "reflex": "qa_agent_reflex",
        "timestamp": _utcnow().isoformat(),
        "sweep_task_created": False,
        "sweep_task_id": None,
        "coverage_gaps_found": 0,
        "gap_tasks_created": 0,
        "gap_task_ids": [],
        "skipped_pending_sweep": False,
        "smoke_failures": 0,
        "smoke_bug_task_ids": [],
    }

    conn = None
    try:
        conn = get_connection()

        # --- Sweep task ---
        if _pending_sweep_exists(conn):
            result["skipped_pending_sweep"] = True
            logger.info("qa_agent_reflex: sweep already pending — skip")
        else:
            last = _last_sweep_completed_at(conn)
            if last:
                elapsed_h = (_utcnow() - last).total_seconds() / 3600
                if elapsed_h < interval_hours:
                    result["skipped_pending_sweep"] = True
                    logger.info(
                        "qa_agent_reflex: last sweep %.1fh ago (interval=%.1fh) — skip",
                        elapsed_h, interval_hours,
                    )

            if not result["skipped_pending_sweep"]:
                sweep_id = _insert_sweep_task()
                result["sweep_task_created"] = sweep_id is not None
                result["sweep_task_id"] = sweep_id
                logger.info("qa_agent_reflex: created sweep task %s", sweep_id)

        # --- Coverage gap tasks ---
        try:
            from tools.testing.qa_agent_runner import discover_coverage_gaps
            gaps = discover_coverage_gaps()
        except Exception as exc:
            logger.warning("qa_agent_reflex: discover_coverage_gaps failed: %s", exc)
            gaps = []

        result["coverage_gaps_found"] = len(gaps)

        created_gap_ids: list[str] = []
        for gap in gaps[:max_gap_tasks_per_run]:
            canvas_key = gap.get("canvas_key", "")
            if not canvas_key:
                continue
            if _pending_gap_task_exists(conn, canvas_key):
                logger.debug("qa_agent_reflex: gap task already pending for %s", canvas_key)
                continue
            gap_id = _insert_gap_task(
                canvas_key=canvas_key,
                display_name=gap.get("display_name", canvas_key),
                route=gap.get("route", f"/{canvas_key}"),
            )
            if not gap_id:
                continue
            created_gap_ids.append(gap_id)
            logger.info("qa_agent_reflex: gap task %s for canvas %s", gap_id, canvas_key)

        # --- Phase 4: Direct route smoke (catches 500/404 regressions immediately) ---
        smoke_failed = _run_route_smoke()
        smoke_bug_ids: list[str] = []
        for fail in smoke_failed:
            route = fail.get("route", "")
            if not route:
                continue
            if _pending_smoke_bug_exists(conn, route):
                logger.debug("qa_agent_reflex: smoke bug task already pending for %s", route)
                continue
            bug_id = _insert_smoke_bug_task(
                route=route,
                status=fail.get("status", "?"),
                error=fail.get("error", "unknown"),
            )
            if not bug_id:
                continue
            smoke_bug_ids.append(bug_id)
            logger.warning("qa_agent_reflex: smoke BUG task %s for route %s", bug_id, route)

        conn.commit()
        result["gap_tasks_created"] = len(created_gap_ids)
        result["gap_task_ids"] = created_gap_ids
        result["smoke_failures"] = len(smoke_failed)
        result["smoke_bug_task_ids"] = smoke_bug_ids

    except Exception as exc:
        result["error"] = str(exc)
        logger.exception("qa_agent_reflex failed: %s", exc)
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    return result


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
    print(json.dumps(run({}, None), indent=2))
