#!/usr/bin/env python3
# CUI // SP-CTI
"""Seed the OBS project card — Observability → CLI wiring.

Scope comes from a verification pass over
``docs/observability_cli_kanban_integration_analysis.md``, recorded in
``docs/observability_cli_kanban_suitability_review.md``. That review measured the
live PostgreSQL board rather than taking the analysis at its word, and most of
the analysis' twelve proposals turned out to rest on tables that no code writes:

    audit_trail                15,173 rows   165 INSERT sites   <- real
    kanban_status_transitions  11,465 rows                      <- real
    hook_events                   208 rows     1 INSERT site
    alerts                         10 rows     4 INSERT sites   (0 firing)
    otel_spans                      0 rows     0 INSERT sites   <- dead
    metric_snapshots                0 rows     2 INSERT sites   <- dead in practice

So this card ships the one proposal with data behind it (an audit tail CLI over
audit_trail + hook_events) and records the rest as explicitly-blocked tasks
carrying the measurement that blocks them. A task nobody can act on is worse
than no task, so the blocked ones are dependents of the gate and are not
dispatchable.

MANUAL-ONLY. Every task depends on ``obs-gate-00``, which is held ``in_progress``
forever. The gate alone does NOT stop promotion — ``depends_on_task_id`` is what
actually holds a task back — so the dependency is the mechanism and the gate is
the anchor.

Usage:
    python tools/kanban/seed_obs_observability_cli.py            # seed
    python tools/kanban/seed_obs_observability_cli.py --dry-run  # preview
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

GATE_ID = "obs-gate-00"

TASKS: list[dict] = [
    {
        "id": GATE_ID,
        "title": "MANUAL-MODE GATE — OBS observability CLI (do not dispatch)",
        "description": (
            "Sentinel task. Held in_progress forever so the OBS tasks below are "
            "never auto-dispatched: this work is done by a CLI session, and the "
            "blocked items below are waiting on decisions, not on an agent.\n\n"
            "Do not move, reap, or complete this task."
        ),
        "status": "in_progress",
        "priority": "low",
        "task_type": "chore",
    },
    # ── SHIPPED: the one proposal with data behind it ──────────────────────
    {
        "id": "obs-tail-01",
        "title": "AuditStore — reusable query layer over audit_trail + hook_events",
        "description": (
            "tools/audit/store.py. A read-only query abstraction so CLI and other "
            "callers stop hand-rolling SQL against audit_trail.\n\n"
            "Scoped deliberately small: it exists to back obs-tail-02 and nothing "
            "else. The analysis proposed a subscribe()/audit_sink callback API too; "
            "that is not included, because no consumer needs it yet and an "
            "unexercised callback surface is a liability.\n\n"
            "Backends: PostgreSQL primary (ICDEV_STORAGE_BACKEND=postgresql), "
            "SQLite fallback. Must not use SQLite-dialect JSON SQL in runtime "
            "paths — compute in Python or branch on is_pg."
        ),
        "acceptance_criteria": (
            "1. AuditStore.tail(n, project_id, event_types, since) returns merged "
            "audit_trail + hook_events rows newest-first.\n"
            "2. Works against live PostgreSQL AND SQLite.\n"
            "3. No raw SQL string interpolation of user input.\n"
            "4. Unit tests cover the merge ordering and each filter."
        ),
        "depends_on_task_id": GATE_ID,
        "priority": "high",
        "task_type": "build",
    },
    {
        "id": "obs-tail-02",
        "title": "icdev audit tail [--follow] — CLI reader for the audit feed",
        "description": (
            "New subcommand under the EXISTING `icdev audit` group "
            "(tools/cli/audit.py already owns `audit export`). audit_trail holds "
            "15,173 rows across 246 event types and has had no CLI reader.\n\n"
            "Note the analysis proposed a top-level `icdev status` health command. "
            "That name is already taken — `icdev status` reports which canvases/"
            "subsystems are toggled on — and `python tools/testing/health_check.py "
            "--json` already covers health. Not doing it."
        ),
        "acceptance_criteria": (
            "1. `icdev audit tail` prints the most recent N events.\n"
            "2. --follow polls and prints only new rows; Ctrl-C exits 0 cleanly.\n"
            "3. --json emits one JSON object per line (jq-able).\n"
            "4. Filters: --project, --event-type, --since, --limit.\n"
            "5. Does not crash when hook_events is empty."
        ),
        "depends_on_task_id": GATE_ID,
        "priority": "high",
        "task_type": "build",
    },
    # ── BLOCKED: recorded with the measurement that blocks them ────────────
    {
        "id": "obs-trace-01",
        "title": "BLOCKED — emit otel_spans before building any trace viewer",
        "description": (
            "The analysis' marquee item is a Langfuse-style trace-tree CLI over "
            "otel_spans. Verified 2026-08-02 against live PostgreSQL:\n\n"
            "  otel_spans rows                      : 0\n"
            "  `INSERT INTO otel_spans` sites in tools/ : 0\n\n"
            "The table is created (init_icdev_db + migration 122) and READ by "
            "tools/dashboard/api/traces.py, but nothing in the tree has ever "
            "written a span. A waterfall renderer would render an empty table.\n\n"
            "The real prerequisite is span emission, and that is a design "
            "question first: what is a span here — an LLM call, a tool call, a "
            "reflex cycle, a kanban dispatch? Decide that before writing code."
        ),
        "acceptance_criteria": (
            "Blocked pending a decision on span semantics. Do not implement a "
            "viewer until otel_spans has non-zero rows from a real workload."
        ),
        "depends_on_task_id": GATE_ID,
        "priority": "low",
        "task_type": "chore",
    },
    {
        "id": "obs-metric-01",
        "title": "BLOCKED — metric_snapshots is empty; health CLI has no source",
        "description": (
            "The analysis' `icdev status` proposal reads metric_snapshots + alerts "
            "+ otel_spans. Verified 2026-08-02:\n\n"
            "  metric_snapshots rows : 0  (2 INSERT sites exist but never run)\n"
            "  alerts rows           : 10 (0 firing)\n"
            "  otel_spans rows       : 0\n\n"
            "Two of the three sources are empty. Separately, the command name is "
            "taken (`icdev status` = component toggles) and health is already "
            "served by tools/testing/health_check.py --json.\n\n"
            "If a health CLI is still wanted, the prerequisite is finding out why "
            "the two metric_snapshots writers never fire."
        ),
        "acceptance_criteria": (
            "Blocked. Requires (a) metric_snapshots actually being written, and "
            "(b) a command name that does not collide with `icdev status`."
        ),
        "depends_on_task_id": GATE_ID,
        "priority": "low",
        "task_type": "chore",
    },
    {
        "id": "obs-react-01",
        "title": "DECLINED — auto-create kanban tasks from alerts/events",
        "description": (
            "The analysis' Priority 2 proposes observability_reactor.py and "
            "alert_bridge.py to auto-create kanban tasks from high-severity events "
            "and firing alerts.\n\n"
            "Declined on measured grounds: the board already holds 2,618 tasks, and "
            "a 2026-08-02 analysis of 4,115 dispatch cycles found 55% end in "
            "backlog having produced nothing. Adding an automated task SOURCE to a "
            "board whose problem is that half its existing work is discarded would "
            "make the signal worse, not better.\n\n"
            "Revisit once the discard rate is understood — every in_progress→backlog "
            "transition now records a reason (PR #1183), so that data is arriving."
        ),
        "acceptance_criteria": (
            "Declined by decision, not blocked by capability. Re-open only with "
            "discard-rate data showing the board can absorb an automated source."
        ),
        "depends_on_task_id": GATE_ID,
        "priority": "low",
        "task_type": "chore",
    },
    {
        "id": "obs-done-01",
        "title": "ALREADY DONE — scheduler overlap guard exists",
        "description": (
            "The analysis recommends adopting CoWorker's `_running_ids` overlap "
            "guard, saying ICDEV's kanban scheduler 'may have overlap'.\n\n"
            "It already has three independent guards, verified 2026-08-02:\n"
            "  1. single-instance PID lockfile (.tmp/kanban_scheduler.pid), "
            "re-checked every cycle — observed refusing a second launch with "
            "'Another kanban scheduler is alive (pid=29028). Exiting to avoid "
            "duplicate dispatch.'\n"
            "  2. in-process `_running` dict gating dispatch at MAX_IN_PROGRESS\n"
            "  3. DB-side `_count_in_progress()` slot math\n\n"
            "No action. Recorded so the recommendation is not re-adopted later.\n\n"
            "One REAL gap did surface while verifying this: the lockfile is "
            "per-checkout, so a scheduler launched from a git worktree runs "
            "alongside the canonical one and reads its own pause flag. That is "
            "obs-guard-02."
        ),
        "acceptance_criteria": "No action — closed as already satisfied.",
        "depends_on_task_id": GATE_ID,
        "priority": "low",
        "task_type": "chore",
    },
    {
        "id": "obs-guard-02",
        "title": "A scheduler started from a worktree bypasses the pause flag",
        "description": (
            "Found 2026-08-02 while pausing the runner. Two schedulers were live:\n"
            "  pid 18132  C:\\ai\\icdev            (canonical, honoured the pause)\n"
            "  pid 25224  C:\\AI\\.wt-tsh-d4-audit5 (worktree, did NOT)\n\n"
            "Both scheduler_control's pause sentinel and the single-instance "
            "lockfile resolve their path from __file__, so a scheduler launched "
            "from a worktree gets its own copies of both. It therefore neither "
            "sees the canonical pause nor collides with the canonical lock — it "
            "just dispatches, from stale code, into the same board and database.\n\n"
            "Options: resolve both paths from the git common-dir instead of "
            "__file__, or refuse to start when __file__ is inside a worktree."
        ),
        "acceptance_criteria": (
            "1. A scheduler started from a worktree either honours the canonical "
            "pause flag or refuses to start.\n"
            "2. The single-instance lock is shared across worktrees of the same repo.\n"
            "3. Covered by a test that simulates the worktree launch path."
        ),
        "depends_on_task_id": GATE_ID,
        "priority": "medium",
        # 'fix', not 'bug' — kanban_tasks_task_type_check allows exactly
        # build|run|fix|research|deploy|test|chore.
        "task_type": "fix",
    },
]


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed the OBS project card")
    ap.add_argument("--dry-run", action="store_true", help="preview without writing")
    ap.add_argument("--json", dest="as_json", action="store_true")
    args = ap.parse_args()

    if args.dry_run:
        for t in TASKS:
            dep = t.get("depends_on_task_id") or "-"
            print(f"  {t['id']:16} dep={dep:14} [{t.get('status','backlog'):11}] {t['title'][:60]}")
        print(f"\n{len(TASKS)} task(s) would be seeded (dry run)")
        return 0

    from tools.kanban.task_factory import create_tasks

    created = create_tasks(TASKS)
    if args.as_json:
        print(json.dumps({"created": created, "total": len(TASKS)}, indent=2))
    else:
        print(f"seeded {len(created)} / {len(TASKS)} task(s)")
        for t in created:
            print("  +", t)
        if len(created) < len(TASKS):
            print("  (already-present tasks were skipped)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
