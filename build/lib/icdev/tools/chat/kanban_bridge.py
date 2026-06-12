#!/usr/bin/env python3
# CUI // SP-CTI
"""Chat ↔ Kanban bridge.

Links chat contexts to kanban_tasks via dispatch_source tag.
Provides CRUD for context-scoped tasks and auto-creates post-build V&V chains.

Usage (CLI):
    python tools/chat/kanban_bridge.py --list --context ctx-abc123
    python tools/chat/kanban_bridge.py --create --context ctx-abc --title "CodeLens" --type chore
    python tools/chat/kanban_bridge.py --vv-chain --context ctx-abc --canvas govlift
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402


def _dispatch_tag(context_id: str) -> str:
    return f"chat:{context_id}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _short_id(prefix: str = "task") -> str:
    import hashlib
    import time
    seed = str(time.time_ns()).encode()
    return f"{prefix}-{hashlib.sha256(seed).hexdigest()[:10]}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_context_tasks(context_id: str) -> list[dict]:
    """Return all kanban_tasks linked to a chat context, newest first."""
    conn = get_connection()
    tag = _dispatch_tag(context_id)
    rows = conn.execute(
        """SELECT id, title, task_type, priority, status, scheduled_at,
                  completed_at, depends_on_task_id, created_at
           FROM kanban_tasks
           WHERE dispatch_source = ?
           ORDER BY created_at DESC""",
        (tag,),
    ).fetchall()
    return [dict(r) for r in rows]


def create_context_task(
    context_id: str,
    title: str,
    description: str = "",
    task_type: str = "build",
    priority: str = "medium",
    depends_on: str | None = None,
) -> dict:
    """Create a kanban task linked to a chat context."""
    conn = get_connection()
    now = _now()
    task_id = _short_id("ck")
    conn.execute(
        """INSERT INTO kanban_tasks
           (id, title, description, task_type, priority, status,
            dispatch_source, depends_on_task_id, scheduled_at, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            task_id, title, description, task_type, priority, "scheduled",
            _dispatch_tag(context_id), depends_on, now, now, now,
        ),
    )
    conn.commit()
    return {"id": task_id, "title": title, "status": "scheduled", "context_id": context_id}


def create_vv_chain(context_id: str, canvas: str = "") -> list[dict]:
    """Auto-create CodeLens + Coherence + E2E tasks linked to a chat context.

    CodeLens and Coherence are independent (parallel). E2E depends on Coherence.
    """
    label = f" [{canvas}]" if canvas else ""

    cl = create_context_task(
        context_id,
        title=f"CodeLens{label} — py_compile + ruff + bandit static analysis",
        description=f"python -m py_compile tools/{canvas}/*.py && ruff check tools/{canvas}/ && python -m bandit -r tools/{canvas}/ --severity-level medium",
        task_type="chore",
        priority="high",
    )
    co = create_context_task(
        context_id,
        title=f"Coherence{label} — coherence_checker --all --fix --gate",
        description="python tools/workflow/coherence_checker.py --all --fix --gate",
        task_type="chore",
        priority="high",
    )
    e2e = create_context_task(
        context_id,
        title=f"E2E lifecycle{label} — full Selenium/Playwright test suite",
        description=f"python tools/testing/e2e_runner.py --run-all | Verify all {canvas} page + API routes",
        task_type="test",
        priority="high",
        depends_on=co["id"],
    )
    return [cl, co, e2e]


def mark_task_done(task_id: str) -> dict:
    conn = get_connection()
    now = _now()
    conn.execute(
        "UPDATE kanban_tasks SET status='done', completed_at=?, updated_at=? WHERE id=?",
        (now, now, task_id),
    )
    conn.commit()
    return {"id": task_id, "status": "done"}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Chat-Kanban bridge CLI")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--create", action="store_true")
    ap.add_argument("--vv-chain", action="store_true")
    ap.add_argument("--context", required=True, help="Chat context ID")
    ap.add_argument("--title", default="Task")
    ap.add_argument("--type", default="build", dest="task_type")
    ap.add_argument("--priority", default="medium")
    ap.add_argument("--canvas", default="")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    if args.list:
        result = list_context_tasks(args.context)
    elif args.create:
        result = create_context_task(
            args.context, args.title, task_type=args.task_type, priority=args.priority
        )
    elif args.vv_chain:
        result = create_vv_chain(args.context, canvas=args.canvas)
    else:
        ap.print_help()
        return

    if args.as_json:
        print(json.dumps(result, indent=2, default=str))
    else:
        if isinstance(result, list):
            for t in result:
                print(f"  [{t.get('status','?')}] {t.get('id','?')} {t.get('title','')[:70]}")
        else:
            print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
