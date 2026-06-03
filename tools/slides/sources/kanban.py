# CUI // SP-CTI
"""Source connector: Kanban epics + task burndown.

Reads from kanban_tasks and projects.yaml to produce a structured
summary of active projects, epics, and in-progress tasks.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_ICDEV_ROOT = Path(__file__).resolve().parents[4]
_PROJECTS_YAML = _ICDEV_ROOT / "args" / "projects.yaml"


def _load_projects() -> list[dict]:
    try:
        with open(_PROJECTS_YAML, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _load_task_counts() -> dict[str, dict]:
    """Query kanban_tasks for status counts per project prefix."""
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT task_id, status FROM kanban_tasks WHERE status != 'done' ORDER BY created_at DESC"
            ).fetchall()
        finally:
            conn.close()

        counts: dict[str, dict] = {}
        for row in rows:
            task_id = str(row["task_id"] if hasattr(row, "__getitem__") else row[0])
            status = str(row["status"] if hasattr(row, "__getitem__") else row[1])
            # Extract project prefix (e.g. "dic-foundation-01" → "dic")
            parts = task_id.split("-")
            prefix = parts[0] if parts else "unknown"
            if prefix not in counts:
                counts[prefix] = {"in_progress": 0, "backlog": 0, "total": 0}
            counts[prefix]["total"] += 1
            if status == "in_progress":
                counts[prefix]["in_progress"] += 1
            else:
                counts[prefix]["backlog"] += 1
        return counts
    except Exception:
        return {}


def gather(max_epics: int = 8) -> dict[str, Any]:
    """Return active projects + epics + burndown for slide generation."""
    projects = _load_projects()
    task_counts = _load_task_counts()

    active_projects: list[dict] = []
    for proj in projects:
        key = proj.get("key", "")
        prefix = proj.get("task_prefix", key + "-").rstrip("-")
        epics = proj.get("epics", [])[:max_epics]

        counts = task_counts.get(prefix, {"in_progress": 0, "backlog": 0, "total": 0})
        progress_pct = 0
        if counts["total"] > 0:
            done = counts.get("done", 0)
            progress_pct = round(done / counts["total"] * 100)

        active_projects.append({
            "key": key,
            "name": proj.get("name", key),
            "epics": [e.get("title", e.get("key", "")) for e in epics],
            "in_progress": counts["in_progress"],
            "backlog": counts["backlog"],
            "progress_pct": progress_pct,
        })

    in_progress_total = sum(p["in_progress"] for p in active_projects)
    backlog_total = sum(p["backlog"] for p in active_projects)

    return {
        "source": "kanban",
        "total_projects": len(active_projects),
        "in_progress_tasks": in_progress_total,
        "backlog_tasks": backlog_total,
        "projects": active_projects,
        "summary": (
            f"ICDEV™ has {len(active_projects)} active projects with "
            f"{in_progress_total} tasks in progress and {backlog_total} in backlog."
        ),
    }
