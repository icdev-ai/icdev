#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Coherence→Kanban Reflex — bridges coherence violations into kanban fix tasks.

Runs a targeted subset of coherence checks on each firing, diffs results against the
last-known-clean baseline stored in the DB, and files [COHERENCE] kanban fix tasks for
*new* violations only — so the same finding isn't re-filed every cycle.

GREEN tier — reads codebase, queries kanban_tasks, creates tasks. No file writes,
no subprocess execution beyond the coherence checks themselves.
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.kanban.task_factory import create_tasks  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("coherence_to_kanban_reflex")

IMPLEMENTATION_STATUS = "full"

# Checks to run on each cycle — keep fast/static only
_CHECKS_TO_RUN: List[str] = [
    "canvas_rls_bypass",
    "template_variable_parity",
    "blueprint_imports",
    "nav_route_parity",
    "canvas_completeness",
    "append_only",
]

_TASK_PREFIX = "[COHERENCE]"

# Severity mapping per check_id
_CHECK_PRIORITY: Dict[str, str] = {
    "canvas_rls_bypass": "critical",
    "blueprint_imports": "critical",
    "template_variable_parity": "high",
    "nav_route_parity": "high",
    "canvas_completeness": "medium",
    "append_only": "high",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _run_checks(check_ids: List[str]) -> Dict[str, dict]:
    """Run coherence checks by ID, return {check_id: result_dict}."""
    results: Dict[str, dict] = {}
    try:
        from tools.workflow.coherence_checker import CHECK_REGISTRY
    except Exception as exc:
        logger.warning("coherence_to_kanban: could not import CHECK_REGISTRY: %s", exc)
        return results

    for check_id in check_ids:
        fn = CHECK_REGISTRY.get(check_id)
        if fn is None:
            logger.debug("coherence_to_kanban: unknown check_id %s — skip", check_id)
            continue
        try:
            check = fn()
            results[check_id] = {
                "status": check.status,
                "message": check.message,
                "missing": list(check.missing or []),
            }
        except Exception as exc:
            logger.warning("coherence_to_kanban: check %s failed: %s", check_id, exc)
            results[check_id] = {"status": "error", "message": str(exc), "missing": []}

    return results


def _load_baseline(conn) -> Dict[str, List[str]]:
    """Load the last-known violation fingerprints per check from coherence_baseline table."""
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS coherence_baseline (
                check_id TEXT PRIMARY KEY,
                violations_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
        rows = conn.execute("SELECT check_id, violations_json FROM coherence_baseline").fetchall()
        return {dict(r)["check_id"]: json.loads(dict(r)["violations_json"]) for r in rows}
    except Exception as exc:
        logger.warning("coherence_to_kanban: could not load baseline: %s", exc)
        return {}


def _save_baseline(conn, check_id: str, violations: List[str]) -> None:
    try:
        now = _utcnow().isoformat()
        conn.execute(
            """
            INSERT INTO coherence_baseline (check_id, violations_json, updated_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (check_id) DO UPDATE
              SET violations_json = EXCLUDED.violations_json,
                  updated_at = EXCLUDED.updated_at
            """,
            (check_id, json.dumps(violations), now),
        )
    except Exception as exc:
        logger.warning("coherence_to_kanban: could not save baseline for %s: %s", check_id, exc)


def _pending_task_exists(conn, check_id: str, violation_key: str) -> bool:
    title_fragment = f"{_TASK_PREFIX} {check_id}"
    key_fragment = violation_key[:60]
    row = conn.execute(
        """
        SELECT id FROM kanban_tasks
        WHERE title LIKE %s
          AND description LIKE %s
          AND status IN ('backlog', 'in_progress')
        LIMIT 1
        """,
        (f"%{title_fragment}%", f"%{key_fragment}%"),
    ).fetchone()
    return row is not None


def _insert_coherence_task(
    check_id: str,
    violations: List[str],
    check_message: str,
) -> Optional[str]:
    """Seed one [COHERENCE] card through the canonical seeder.

    rem-hyg-06: this used to be a raw ``INSERT INTO kanban_tasks`` with
    ``task_type='bug'`` — a value ``kanban_tasks_task_type_check`` forbids, so
    on PostgreSQL (the primary backend) the statement raised every time a new
    violation was found and the reflex has never filed a card. There are zero
    ``bug`` rows on the live board. ``create_tasks`` refuses that value in
    Python instead, which is how it surfaced; the correct type is ``fix``.

    Takes no ``conn``: the seeder opens and commits its own.
    """
    task_id = f"task-coh-{uuid.uuid4().hex[:8]}"
    now = _utcnow().isoformat()
    priority = _CHECK_PRIORITY.get(check_id, "medium")
    title = f"{_TASK_PREFIX} {check_id} — {len(violations)} violation(s)"
    desc = (
        f"Coherence check **{check_id}** detected {len(violations)} violation(s).\n\n"
        f"**Message:** {check_message}\n\n"
        "**Violations:**\n"
        + "\n".join(f"- `{v}`" for v in violations[:20])
        + (f"\n\n…and {len(violations) - 20} more." if len(violations) > 20 else "")
        + "\n\n"
        "**To verify:**\n"
        "```\n"
        f"python tools/workflow/coherence_checker.py --check {check_id} --json\n"
        "```\n"
        "Fix the root cause and re-run the check. Close this task when the check passes."
    )
    created = create_tasks([{
        "id": task_id,
        "title": title,
        "description": desc,
        "task_type": "fix",
        "priority": priority,
        "status": "backlog",
        "scheduled_at": now,
        "dispatch_source": "coherence_to_kanban_reflex",
    }])
    return task_id if created else None


def run(config: dict, state: object) -> dict:
    """Main reflex entry point called by the Genesis daemon."""
    checks_to_run = list(config.get("checks", _CHECKS_TO_RUN))

    result: dict = {
        "reflex": "coherence_to_kanban_reflex",
        "timestamp": _utcnow().isoformat(),
        "checks_run": len(checks_to_run),
        "new_violations": 0,
        "tasks_created": 0,
        "task_ids": [],
        "check_results": {},
    }

    conn = None
    try:
        conn = get_connection()
        baseline = _load_baseline(conn)

        check_results = _run_checks(checks_to_run)
        result["check_results"] = {k: v["status"] for k, v in check_results.items()}

        new_task_ids: List[str] = []
        total_new_violations = 0

        for check_id, cr in check_results.items():
            current_violations: List[str] = cr.get("missing", [])
            prior_violations: List[str] = baseline.get(check_id, [])

            # New violations = in current but not in prior baseline
            prior_set = set(prior_violations)
            new_viols = [v for v in current_violations if v not in prior_set]

            if new_viols:
                # Only file one task per check per cycle (batches all new violations)
                total_new_violations += len(new_viols)
                first_viol = new_viols[0]
                if not _pending_task_exists(conn, check_id, first_viol):
                    task_id = _insert_coherence_task(check_id, new_viols, cr.get("message", ""))
                    if task_id:
                        new_task_ids.append(task_id)
                        logger.warning(
                            "coherence_to_kanban: task %s for %d new %s violation(s)",
                            task_id, len(new_viols), check_id,
                        )
                else:
                    logger.debug("coherence_to_kanban: task already pending for %s", check_id)

            # Always update baseline to current state
            _save_baseline(conn, check_id, current_violations)

        conn.commit()
        result["new_violations"] = total_new_violations
        result["tasks_created"] = len(new_task_ids)
        result["task_ids"] = new_task_ids

    except Exception as exc:
        result["error"] = str(exc)
        logger.exception("coherence_to_kanban_reflex failed: %s", exc)
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
    import json as _json
    print(_json.dumps(run({}, None), indent=2))
