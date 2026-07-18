#!/usr/bin/env python3
# CUI // SP-CTI
"""Promote SAFe decomposition items from an intake session into the Kanban backlog.

Reads safe_decomposition WHERE session_id=? AND status != 'committed',
maps each level (epic/capability/feature/story/enabler) to a kanban task_type,
inserts kanban_tasks(status='suggested', scheduled_at=now()), and stamps
safe_decomposition.status='committed' for idempotency.

Priority is computed from WSJF score using thresholds in args/intake_promoter.yaml.

Pattern mirrors tools/innovation/kanban_promoter.py exactly.

Usage:
    python tools/requirements/intake_kanban_promoter.py --session-id sess-abc --json
    python tools/requirements/intake_kanban_promoter.py --session-id sess-abc --dry-run --json
    python tools/requirements/intake_kanban_promoter.py --session-id sess-abc --list --json
    python tools/requirements/intake_kanban_promoter.py --list-all --json

Programmatic:
    from tools.requirements.intake_kanban_promoter import promote
    result = promote(session_id="sess-abc")
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection, table_exists  # noqa: E402

CONFIG_PATH = BASE_DIR / "args" / "intake_promoter.yaml"

DEFAULT_CONFIG: dict[str, Any] = {
    "default_task_type": "build",
    "classification": "CUI // SP-CTI",
    "max_per_run": 100,
    "priority_thresholds": {"high": 75, "medium": 50},
    "level_type_map": {
        "epic": "research",
        "capability": "research",
        "feature": "build",
        "story": "build",
        "enabler": "chore",
    },
    "promoted_status": "suggested",
}


def _load_config() -> dict[str, Any]:
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        merged = dict(DEFAULT_CONFIG)
        merged.update(cfg)
        if "level_type_map" in cfg:
            merged["level_type_map"] = {**DEFAULT_CONFIG["level_type_map"], **cfg["level_type_map"]}
        if "priority_thresholds" in cfg:
            merged["priority_thresholds"] = {
                **DEFAULT_CONFIG["priority_thresholds"],
                **cfg["priority_thresholds"],
            }
        return merged
    return dict(DEFAULT_CONFIG)


def _short_id(prefix: str = "task") -> str:
    seed = str(datetime.now(timezone.utc).timestamp()).encode()
    import secrets
    seed += secrets.token_bytes(8)
    # usedforsecurity=False per FIPS guidance — this is a non-security ID
    digest = hashlib.sha256(seed).hexdigest()[:10]
    return f"{prefix}-{digest}"


def _table_exists(conn, name: str) -> bool:
    # Backend-aware, translation-independent existence probe (pgrt-sweep-06).
    return table_exists(conn, name)


def _wsjf_to_priority(wsjf_score: float, thresholds: dict) -> str:
    high_t = thresholds.get("high", 75)
    medium_t = thresholds.get("medium", 50)
    if wsjf_score >= high_t:
        return "high"
    if wsjf_score >= medium_t:
        return "medium"
    return "low"


def _build_task(item: Any, config: dict) -> dict:
    """Map a safe_decomposition row to a kanban_tasks dict."""
    level = (item["level"] if isinstance(item, dict) else item[3] or "story").lower()
    title = item["title"] if isinstance(item, dict) else item[4] or ""
    description = item["description"] if isinstance(item, dict) else item[5] or ""
    wsjf_score = float(item["wsjf_score"] if isinstance(item, dict) else item[11] or 0)
    safe_id = item["id"] if isinstance(item, dict) else item[0]

    level_map: dict = config.get("level_type_map", {})
    task_type = level_map.get(level, config.get("default_task_type", "build"))
    priority = _wsjf_to_priority(wsjf_score, config.get("priority_thresholds", {}))
    now = datetime.now(timezone.utc).isoformat()

    full_desc = description
    if item["acceptance_criteria"] if isinstance(item, dict) else item[6]:
        ac = item["acceptance_criteria"] if isinstance(item, dict) else item[6]
        full_desc = f"{description}\n\nAcceptance Criteria:\n{ac}".strip()

    return {
        "id": _short_id("task"),
        "title": f"[{level.upper()}] {title}",
        "description": full_desc[:2000],
        "task_type": task_type,
        "priority": priority,
        "status": config.get("promoted_status", "suggested"),
        "scheduled_at": now,
        "created_at": now,
        "updated_at": now,
        "source_prediction_id": safe_id,
        "dispatch_source": "intake_promoter",
        "classification": config.get("classification", "CUI // SP-CTI"),
    }


def promote(
    session_id: str,
    dry_run: bool = False,
    max_per_run: Optional[int] = None,
) -> dict[str, Any]:
    """Promote all un-committed SAFe items for session_id → kanban_tasks.

    Returns {inserted, skipped, dry_run, tasks} dict.
    """
    config = _load_config()
    limit = max_per_run or config.get("max_per_run", 100)

    conn = get_connection()

    if not _table_exists(conn, "safe_decomposition"):
        return {"error": "safe_decomposition table not found — run intake decomposition first"}

    if not _table_exists(conn, "kanban_tasks"):
        return {"error": "kanban_tasks table not found"}

    rows = conn.execute(
        """SELECT id, session_id, parent_id, level, title, description,
                  acceptance_criteria, story_points, t_shirt_size, wsjf_score,
                  ato_impact_tier, status
           FROM safe_decomposition
           WHERE session_id = %s
             AND (status IS NULL OR status != 'committed')
           ORDER BY level, wsjf_score DESC
           LIMIT %s""",
        (session_id, limit),
    ).fetchall()

    if not rows:
        return {
            "inserted": 0,
            "skipped": 0,
            "dry_run": dry_run,
            "tasks": [],
            "message": f"No un-committed SAFe items found for session {session_id}",
        }

    # Check which safe IDs are already in kanban
    existing_source_ids = set(
        r[0]
        for r in conn.execute(
            "SELECT source_prediction_id FROM kanban_tasks WHERE source_prediction_id IS NOT NULL"
        ).fetchall()
    )

    inserted = 0
    skipped = 0
    task_summaries = []

    for row in rows:
        safe_id = row[0]
        if safe_id in existing_source_ids:
            skipped += 1
            continue

        task = _build_task(
            {
                "id": row[0],
                "session_id": row[1],
                "parent_id": row[2],
                "level": row[3],
                "title": row[4],
                "description": row[5],
                "acceptance_criteria": row[6],
                "story_points": row[7],
                "t_shirt_size": row[8],
                "wsjf_score": row[9],
                "ato_impact_tier": row[10],
                "status": row[11],
            },
            config,
        )

        task_summaries.append({
            "task_id": task["id"],
            "safe_id": safe_id,
            "level": row[3],
            "title": task["title"],
            "task_type": task["task_type"],
            "priority": task["priority"],
        })

        if not dry_run:
            conn.execute(
                """INSERT INTO kanban_tasks
                   (id, title, description, task_type, priority, status,
                    scheduled_at, created_at, updated_at,
                    source_prediction_id, dispatch_source)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    task["id"],
                    task["title"],
                    task["description"],
                    task["task_type"],
                    task["priority"],
                    task["status"],
                    task["scheduled_at"],
                    task["created_at"],
                    task["updated_at"],
                    task["source_prediction_id"],
                    task["dispatch_source"],
                ),
            )
            conn.execute(
                "UPDATE safe_decomposition SET status='committed' WHERE id=%s",
                (safe_id,),
            )

        inserted += 1

    if not dry_run and inserted > 0:
        conn.commit()

    return {
        "inserted": inserted,
        "skipped": skipped,
        "dry_run": dry_run,
        "session_id": session_id,
        "tasks": task_summaries,
    }


def list_promoted(session_id: str) -> dict[str, Any]:
    """List kanban tasks already promoted from this intake session."""
    conn = get_connection()

    if not _table_exists(conn, "safe_decomposition"):
        return {"tasks": [], "total": 0}

    rows = conn.execute(
        """SELECT kt.id, kt.title, kt.task_type, kt.priority, kt.status,
                  kt.source_prediction_id, kt.created_at
           FROM kanban_tasks kt
           JOIN safe_decomposition sd ON kt.source_prediction_id = sd.id
           WHERE sd.session_id = %s
           ORDER BY kt.created_at DESC""",
        (session_id,),
    ).fetchall()

    tasks = [
        {
            "task_id": r[0],
            "title": r[1],
            "task_type": r[2],
            "priority": r[3],
            "status": r[4],
            "safe_id": r[5],
            "created_at": r[6],
        }
        for r in rows
    ]
    return {"tasks": tasks, "total": len(tasks), "session_id": session_id}


def list_all_sessions() -> dict[str, Any]:
    """List all intake sessions that have been promoted."""
    conn = get_connection()
    if not _table_exists(conn, "safe_decomposition"):
        return {"sessions": []}

    rows = conn.execute(
        """SELECT sd.session_id, COUNT(kt.id) as task_count
           FROM safe_decomposition sd
           JOIN kanban_tasks kt ON kt.source_prediction_id = sd.id
           GROUP BY sd.session_id
           ORDER BY MAX(kt.created_at) DESC"""
    ).fetchall()

    return {
        "sessions": [{"session_id": r[0], "task_count": r[1]} for r in rows]
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

from typing import Optional  # noqa: E402 (already imported above, re-declared for clarity)


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="Promote SAFe decomposition items to Kanban")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--session-id", metavar="SESS_ID", help="Promote items for this session")
    group.add_argument("--list-all", action="store_true", help="List all promoted sessions")

    p.add_argument("--dry-run", action="store_true", help="Preview without writing")
    p.add_argument("--list", action="store_true", help="List already-promoted tasks for session")
    p.add_argument("--max", type=int, default=None, help="Max items to promote per run")
    p.add_argument("--json", action="store_true", help="JSON output")
    return p.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)

    if args.list_all:
        result = list_all_sessions()
    elif args.list:
        result = list_promoted(args.session_id)
    else:
        result = promote(
            session_id=args.session_id,
            dry_run=args.dry_run,
            max_per_run=args.max,
        )

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
