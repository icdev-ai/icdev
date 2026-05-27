# CUI // SP-CTI
"""TTX Engine — inject dispatch for live (timed) and async (sequential) modes."""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from tools.db.storage import get_connection

log = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Seed injects from loaded scenario
# ---------------------------------------------------------------------------

def seed_injects(session_id: int, scenario: dict) -> list[dict[str, Any]]:
    """Insert all scenario injects into ttx_injects for a session."""
    conn = get_connection()
    result = []
    for inj in scenario.get("injects", []):
        inject_id = str(uuid.uuid4())
        scoring = inj.get("scoring", {})
        config = {
            "inject_type": inj.get("inject_type", "standard"),
            "ai_tools_allowed": inj.get("ai_tools_allowed", []),
            "resources": inj.get("resources", []),
            "scoring": {
                "base_pts": scoring.get("base_pts", 100),
                "receipt_bonus_per_call": scoring.get("receipt_bonus_per_call", 50),
                "max_receipt_bonus": scoring.get("max_receipt_bonus", 150),
                "time_bonus": scoring.get("time_bonus", True),
                "rubric": scoring.get("rubric", {}),
            },
        }
        conn.execute(
            """INSERT INTO ttx_injects
               (inject_id, session_id, slug, title, body_md,
                at_minute, sequence_num, depends_on_slug,
                state, config_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (
                inject_id,
                session_id,
                inj["id"],
                inj["title"],
                inj.get("body_md", inj.get("body", "")),
                inj.get("at_minute"),
                inj.get("sequence"),
                inj.get("depends_on_slug") or inj.get("depends_on"),
                json.dumps(config),
                _now(),
            ),
        )
        result.append({"inject_id": inject_id, "slug": inj["id"]})
    conn.commit()
    return result


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def dispatch_inject(inject_id: str) -> bool:
    """Mark an inject as dispatched (available to teams)."""
    conn = get_connection()
    conn.execute(
        """UPDATE ttx_injects
           SET state = 'dispatched', dispatched_at = ?
           WHERE inject_id = ? AND state = 'pending'""",
        (_now(), inject_id),
    )
    conn.commit()
    row = conn.execute(
        "SELECT state FROM ttx_injects WHERE inject_id = ?", (inject_id,)
    ).fetchone()
    return bool(row and row["state"] == "dispatched")


def close_inject(inject_id: str) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE ttx_injects SET state = 'closed', closed_at = ? WHERE inject_id = ?",
        (_now(), inject_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

def get_dispatched_injects(session_id: int) -> list[dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute(
        """SELECT * FROM ttx_injects
           WHERE session_id = ? AND state IN ('dispatched', 'closed')
           ORDER BY sequence_num, dispatched_at""",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_pending_injects(session_id: int) -> list[dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM ttx_injects WHERE session_id = ? AND state = 'pending' ORDER BY sequence_num, at_minute",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_inject_by_id(inject_id: str) -> dict[str, Any] | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM ttx_injects WHERE inject_id = ?", (inject_id,)
    ).fetchone()
    return dict(row) if row else None


def get_all_injects(session_id: int) -> list[dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM ttx_injects WHERE session_id = ? ORDER BY sequence_num, at_minute",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Async-mode: check if next inject should unlock for a specific team
# ---------------------------------------------------------------------------

def unlock_next_async(session_id: int, team_id: int) -> dict[str, Any] | None:
    """For async mode: check if a pending inject is now unlockable for this team.

    An inject unlocks when its depends_on_slug has a submitted response from the team.
    Returns the newly-dispatched inject dict, or None.
    """
    conn = get_connection()
    pending = conn.execute(
        """SELECT * FROM ttx_injects
           WHERE session_id = ? AND state = 'pending'
           ORDER BY sequence_num LIMIT 1""",
        (session_id,),
    ).fetchone()
    if not pending:
        return None

    dep_slug = pending["depends_on_slug"]
    if dep_slug is None:
        # No dependency — dispatch immediately
        dispatch_inject(pending["inject_id"])
        return dict(pending)

    # Check if team has a response for the dependency inject
    dep_inject = conn.execute(
        "SELECT inject_id FROM ttx_injects WHERE session_id = ? AND slug = ?",
        (session_id, dep_slug),
    ).fetchone()
    if not dep_inject:
        return None

    has_response = conn.execute(
        "SELECT 1 FROM ttx_responses WHERE team_id = ? AND inject_id = ?",
        (team_id, dep_inject["inject_id"]),
    ).fetchone()
    if has_response:
        dispatch_inject(pending["inject_id"])
        return dict(pending)
    return None
