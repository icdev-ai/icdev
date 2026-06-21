# CUI // SP-CTI
"""Seed Kanban tasks for the Connection-Hygiene Sweep (chyg).

Project: chyg (task_prefix 'chyg-') — eliminate leaked PostgreSQL connections
(bare `conn = get_connection()` without close/commit -> 'idle in transaction'
holders) across the genesis reflexes / dispatcher, esp. tools/genesis/reflexes/
kanban.py (~53 un-audited callsites). Follow-on to the kanban_tasks lock-storm
fixes (init_db conditional ALTER + eval_harness/_safe_close + kanban.py GA/CI
poll closes already shipped 2026-05-30).

Pattern to apply (precedent: eval_harness.py _safe_close, kanban.py GA/CI poll):
  - read-only/SELECT callsites -> `with get_connection() as conn:` (auto-close)
    or close in `finally`; never hold a connection across network/long loops.
  - write callsites -> keep commit, then close in `finally`.

Run:
    python tools/kanban/seed_chyg_kanban.py
    python tools/kanban/seed_chyg_kanban.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.db.storage import get_connection  # noqa: E402

PROJECT_ID = "chyg"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


TASKS = [
    {
        "id": "chyg-audit-01",
        "title": "Audit leaked get_connection() callsites across genesis reflexes / dispatcher",
        "description": (
            "Enumerate every bare `conn = get_connection()` (NOT inside a `with` and NOT followed by a "
            "guaranteed close) in tools/genesis/reflexes/kanban.py (~53 callsites: grep `get_connection()`) "
            "and its sibling dispatcher/reflex modules (tools/genesis/, tools/genesis/reflexes/). For each, "
            "record file:line, the enclosing function, whether it commits, and whether the connection is held "
            "across a network call or long loop (highest-priority leaks). Produce a checklist at "
            "docs/features/chyg-audit.md grouped by file with a fix recommendation per site (with-statement vs "
            "finally-close). Use the already-fixed sites as the canonical pattern: "
            "tools/genesis/harness/eval_harness.py (_safe_close) and the kanban.py GitHub-Actions / "
            "_detect_and_queue_ci_failures polls. Do NOT change code in this task — audit only."
        ),
        "task_type": "chore",
        "priority": "high",
        "depends_on_task_id": None,
    },
    {
        "id": "chyg-sweep-01",
        "title": "kanban.py connection-hygiene — batch 1 (top of file, lines ~50-1810)",
        "description": (
            "Using the chyg-audit-01 checklist, fix all leaking get_connection() callsites in the first third "
            "of tools/genesis/reflexes/kanban.py (and mirror to icdev/tools/genesis/reflexes/kanban.py — keep "
            "both copies identical). Convert read-only sites to `with get_connection() as conn:` where the "
            "connection is not reused after the block; otherwise add a `finally: conn.close()` (guarded). "
            "Preserve all existing commit() calls and behavior. Run `python -m py_compile` on both copies and "
            "`ruff check` on the file. NEVER hold a connection across a `requests`/network call or a sleep. "
            "Requires chyg-audit-01."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "chyg-audit-01",
    },
    {
        "id": "chyg-sweep-02",
        "title": "kanban.py connection-hygiene — batch 2 (middle, lines ~1810-3700)",
        "description": (
            "Same as chyg-sweep-01 for the middle third of tools/genesis/reflexes/kanban.py + icdev/ mirror. "
            "This range already contains the GA/CI poll functions that were fixed on 2026-05-30 — leave those "
            "as-is and fix the remaining bare callsites around them. py_compile + ruff both copies. "
            "Requires chyg-audit-01 (independent of chyg-sweep-01; can run in parallel)."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "chyg-audit-01",
    },
    {
        "id": "chyg-sweep-03",
        "title": "kanban.py connection-hygiene — batch 3 (bottom, lines ~3700-end) + sibling reflexes",
        "description": (
            "Same as chyg-sweep-01 for the final third of tools/genesis/reflexes/kanban.py + icdev/ mirror, "
            "PLUS any sibling reflex/dispatcher modules flagged by chyg-audit-01 outside kanban.py. py_compile "
            "+ ruff all touched files; keep tools/ and icdev/ copies identical. Requires chyg-audit-01."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": "chyg-audit-01",
    },
    {
        "id": "chyg-vv-01",
        "title": "Verify connection-hygiene sweep — no lingering idle-in-transaction + regression guard",
        "description": (
            "Validation gate for the sweep. (1) Restart kanban scheduler + genesis daemon so the fixed code "
            "loads. (2) After ~2 idle poll cycles, query pg_stat_activity (pattern in .tmp/_verify_kanban_leak.py) "
            "and assert total 'idle in transaction' in the icdev DB stays low (target < 3) and 0 leaks matching "
            "the GA/CI poll signature. (3) Add a lightweight regression check (e.g. tests/test_conn_hygiene.py "
            "that greps reflex modules for bare `get_connection()` not wrapped/closed, or a runtime probe). "
            "(4) python tools/workflow/coherence_checker.py --all --gate; companion sync. (5) Append results to "
            "docs/features/chyg-audit.md and update memory [[kanban-tasks-lock-storm]]. Requires chyg-sweep-01, "
            "chyg-sweep-02, chyg-sweep-03 (primary dep: chyg-sweep-03)."
        ),
        "task_type": "test",
        "priority": "high",
        "depends_on_task_id": "chyg-sweep-03",
    },
]


def seed(dry_run: bool = False) -> int:
    conn = get_connection()
    now = _now()
    inserted = 0
    skipped = 0
    try:
        for t in TASKS:
            existing = conn.execute(
                "SELECT id FROM kanban_tasks WHERE id=?", (t["id"],)
            ).fetchone()
            if existing:
                skipped += 1
                continue
            if dry_run:
                inserted += 1
                continue
            conn.execute(
                """INSERT INTO kanban_tasks
                   (id, title, description, task_type, priority, status,
                    scheduled_at, created_at, updated_at, depends_on_task_id,
                    project_id, classification)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    t["id"],
                    t["title"],
                    t["description"],
                    t.get("task_type", "build"),
                    t.get("priority", "medium"),
                    "scheduled",
                    now,
                    now,
                    now,
                    t.get("depends_on_task_id"),
                    PROJECT_ID,
                    "CUI",
                ),
            )
            inserted += 1
        if not dry_run:
            conn.commit()
    finally:
        conn.close()
    verb = "Would seed" if dry_run else "Seeded"
    print(f"{verb} {inserted} tasks, skipped {skipped} existing (total defined: {len(TASKS)}).")
    return inserted


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Seed connection-hygiene sweep kanban tasks")
    ap.add_argument("--dry-run", action="store_true", help="Print only, no DB write")
    args = ap.parse_args()
    seed(dry_run=args.dry_run)
