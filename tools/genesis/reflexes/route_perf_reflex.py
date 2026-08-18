#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Route Performance Reflex — detects latency regressions across nav routes.

On each firing:
  1. Runs a full NAV_ROUTES smoke to collect fresh elapsed_ms data points
     (route_smoke.py auto-persists results to data/route_perf.db).
  2. Calls detect_perf_regressions() — routes whose recent p50 > baseline p50
     by more than threshold_multiplier are flagged.
  3. Files [PERF] kanban tasks for newly-detected regressions (dedup via DB).

GREEN tier — reads routes, writes to local SQLite perf DB, creates kanban tasks.
Skips gracefully when the dashboard server is not running.
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.kanban.task_factory import create_tasks  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("route_perf_reflex")

IMPLEMENTATION_STATUS = "full"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _pending_perf_task_exists(conn, route: str) -> bool:
    row = conn.execute(
        """
        SELECT id FROM kanban_tasks
        WHERE title LIKE %s
          AND status IN ('backlog', 'in_progress')
        LIMIT 1
        """,
        (f"%[PERF]%{route}%",),
    ).fetchone()
    return row is not None


def _insert_perf_task(route: str, baseline_ms: int, current_ms: int, ratio: float) -> str | None:
    """Seed one [PERF] card through the canonical seeder (rem-hyg-06).

    Takes no ``conn``: ``create_tasks`` opens and commits its own.
    """
    task_id = f"task-perf-{uuid.uuid4().hex[:8]}"
    now = _utcnow().isoformat()
    title = f"[PERF] {route} — {ratio:.1f}x latency regression"
    desc = (
        f"Route `{route}` has degraded significantly:\n\n"
        f"- **Baseline p50:** {baseline_ms} ms\n"
        f"- **Current p50:** {current_ms} ms\n"
        f"- **Ratio:** {ratio:.1f}x (threshold: 2.5x)\n\n"
        "Possible causes: slow DB query, missing index, template rendering bottleneck, "
        "or background task contention.\n\n"
        "Steps:\n"
        "1. Open the route in a browser with DevTools Network tab open.\n"
        "2. Check server logs for slow queries (`tools/logging/`).\n"
        "3. Run `python tools/testing/route_smoke.py --routes "
        f"{route} --json` to confirm current latency.\n"
        "4. Fix root cause and re-run smoke. Close this task when p50 returns to baseline."
    )
    created = create_tasks([{
        "id": task_id,
        "title": title,
        "description": desc,
        # rem-hyg-06: was "bug", which kanban_tasks_task_type_check forbids —
        # the raw INSERT would have raised the moment a regression was found.
        "task_type": "fix",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": now,
        "dispatch_source": "route_perf_reflex",
    }])
    return task_id if created else None


def run(config: dict, state: object) -> dict:
    """Main reflex entry point called by the Genesis daemon."""
    base = config.get("base", "http://localhost:5050")
    threshold = float(config.get("threshold_multiplier", 2.5))
    min_runs = int(config.get("min_runs", 5))

    result: dict = {
        "reflex": "route_perf_reflex",
        "timestamp": _utcnow().isoformat(),
        "smoke_ran": False,
        "regressions_found": 0,
        "tasks_created": 0,
        "task_ids": [],
    }

    try:
        from tools.testing.route_smoke import run_smoke, NAV_ROUTES, detect_perf_regressions

        # Step 1: run smoke to collect fresh data points (auto-persisted)
        _ok, smoke_results = run_smoke(NAV_ROUTES, base=base, verbose=False)
        result["smoke_ran"] = len(smoke_results) > 0

        # Step 2: detect regressions from accumulated history
        regressions = detect_perf_regressions(
            threshold_multiplier=threshold, min_runs=min_runs
        )
        result["regressions_found"] = len(regressions)

        if not regressions:
            return result

        # Step 3: file kanban tasks for new regressions
        conn = get_connection()
        try:
            task_ids: List[str] = []
            for reg in regressions:
                route = reg["route"]
                if _pending_perf_task_exists(conn, route):
                    logger.debug("route_perf_reflex: task already pending for %s", route)
                    continue
                tid = _insert_perf_task(
                    route=route,
                    baseline_ms=reg["baseline_p50_ms"],
                    current_ms=reg["current_p50_ms"],
                    ratio=reg["ratio"],
                )
                if not tid:
                    continue
                task_ids.append(tid)
                logger.warning(
                    "route_perf_reflex: perf regression %s (%s) ratio=%.1f",
                    route, tid, reg["ratio"],
                )
            conn.commit()
            result["tasks_created"] = len(task_ids)
            result["task_ids"] = task_ids
        finally:
            conn.close()

    except Exception as exc:
        result["error"] = str(exc)
        logger.exception("route_perf_reflex failed: %s", exc)

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
