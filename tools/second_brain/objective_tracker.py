# CUI // SP-CTI
"""Objective auto-tracker — derives progress from kanban + git without manual input."""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)
_REPO_ROOT = Path(__file__).resolve().parents[2]


def sync_objective_progress(user_id: str, tenant_id: str = "default") -> list[dict[str, Any]]:
    """For each active objective, derive progress from kanban + git and persist."""
    try:
        from tools.second_brain.profile import get_objectives
        objectives = [
            o for o in (get_objectives(user_id, tenant_id) or [])
            if o.get("status") == "active"
        ]
    except Exception as exc:
        logger.warning("[objective_tracker] get_objectives failed: %s", exc)
        return []

    if not objectives:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    done_tasks = _get_recent_done_tasks(cutoff)
    recent_commits = _get_recent_commits(cutoff)

    updated = []
    for obj in objectives:
        title = obj.get("title", "")
        obj_id = obj.get("id", "")

        matched_tasks = [t for t in done_tasks if _fuzzy_match(title, t.get("title", ""))]
        matched_commits = [c for c in recent_commits if _fuzzy_match(title, c)]

        if not matched_tasks and not matched_commits:
            continue

        notes = []
        if matched_tasks:
            sample = ", ".join(t["title"][:40] for t in matched_tasks[:2])
            notes.append(f"Kanban: {len(matched_tasks)} task(s) completed — {sample}")
        if matched_commits:
            notes.append(f"Commits: {len(matched_commits)} — {matched_commits[0][:60]}")

        pct = min(100, len(matched_tasks) * 20 + len(matched_commits) * 10)
        note_entry = {
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "note": "; ".join(notes),
            "auto": True,
            "progress_pct": pct,
        }

        _persist_progress(obj_id, note_entry, pct)
        updated.append({
            "objective_id": obj_id,
            "title": title,
            "progress_pct": pct,
            "note": note_entry["note"],
        })

    return updated


def _get_recent_done_tasks(since: datetime) -> list[dict]:
    try:
        from tools.db.storage import get_connection
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT title FROM kanban_tasks WHERE status='done' AND updated_at > %s LIMIT 100",
                (since.isoformat(),),
            ).fetchall()
        return [{"title": r[0]} for r in rows]
    except Exception:
        return []


def _get_recent_commits(since: datetime) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "log", f"--since={since.strftime('%Y-%m-%d')}", "--oneline", "--no-merges"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
        )
        lines = result.stdout.strip().splitlines()
        return [line.split(" ", 1)[1] for line in lines if " " in line][:50]
    except Exception:
        return []


def _fuzzy_match(objective: str, candidate: str) -> bool:
    """True if 2+ significant words from objective appear in candidate."""
    obj_words = {w.lower() for w in objective.split() if len(w) > 3}
    cand_lower = candidate.lower()
    return sum(1 for w in obj_words if w in cand_lower) >= 2


def _persist_progress(obj_id: str, note: dict, pct: int) -> None:
    try:
        from tools.second_brain.constants import BRIEFING_ENV_FLAG
        from tools.db.storage import get_canvas_connection, sql_placeholder
        with get_canvas_connection(BRIEFING_ENV_FLAG) as conn:
            ph = sql_placeholder(conn)
            row = conn.execute(
                f"SELECT progress_notes FROM user_objectives WHERE id={ph}", (obj_id,)
            ).fetchone()
            if not row:
                return
            existing = json.loads(row[0] or "[]")
            existing.append(note)
            existing = existing[-20:]
            conn.execute(
                f"UPDATE user_objectives "
                f"SET progress_notes={ph}, auto_progress_pct={ph}, last_auto_update={ph} "
                f"WHERE id={ph}",
                (json.dumps(existing), pct, datetime.now(timezone.utc).isoformat(), obj_id),
            )
            conn.commit()
    except Exception as exc:
        logger.warning("[objective_tracker] persist failed for %s: %s", obj_id, exc)
