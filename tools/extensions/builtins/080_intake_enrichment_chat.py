#!/usr/bin/env python3
# CUI // SP-CTI
"""Intake enrichment chat extension (RICOAS + Bayesian + RAG integration).

Hooks into ``chat_message_after`` to enrich intake conversations with:
- Bayesian readiness dimension scoring (7th dimension, D323)
- Gap-aware next-question suggestions from clarification engine
- Readiness score updates when intake topics are discussed
- Supply chain and boundary impact awareness

Advisory messages are throttled by a cooldown (default: 6 turns).
Only activates when the chat context has an associated intake session.

Loaded automatically by ExtensionManager._auto_load_builtins().

Exports:
    EXTENSION_HOOKS — dict mapping hook point names to handler metadata.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

from tools.db.storage import get_connection, table_exists as _table_exists
from pathlib import Path

logger = get_logger("icdev.extensions.intake_enrichment_chat")

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ADVISORY_COOLDOWN_TURNS = 6

# Keywords that indicate intake-related discussion
INTAKE_KEYWORDS = [
    "requirement",
    "requirement ",
    "shall",
    "must",
    "should",
    "gap",
    "readiness",
    "intake",
    "decompos",
    "epic",
    "story",
    "feature",
    "capability",
    "acceptance criteria",
    "bdd",
    "gherkin",
    "coa",
    "course of action",
    "boundary",
    "ato boundary",
    "supply chain",
    "vendor",
    "dependency",
    "isa",
    "impact level",
    "classification",
    "il4",
    "il5",
    "il6",
    "customer",
    "stakeholder",
    "mission",
    "objective",
]

_last_advisory_turn: dict = {}


def _should_advise(context_id: str, turn_number: int) -> bool:
    last = _last_advisory_turn.get(context_id, -ADVISORY_COOLDOWN_TURNS - 1)
    return (turn_number - last) >= ADVISORY_COOLDOWN_TURNS


def _record_advisory(context_id: str, turn_number: int):
    _last_advisory_turn[context_id] = turn_number


# ---------------------------------------------------------------------------
# Intake enrichment checks
# ---------------------------------------------------------------------------


def _check_intake_enrichment(session_id: str, project_id: str) -> dict | None:
    """Check intake session status and suggest enrichment actions."""
    if not DB_PATH.exists():
        return None

    try:
        conn = get_connection()
    except Exception:
        return None

    try:
        # Check session exists
        if not _table_exists(conn, "intake_sessions"):
            return None

        session = conn.execute(
            "SELECT * FROM intake_sessions WHERE id = %s",
            (session_id,),
        ).fetchone()

        if not session:
            return None

        advisories = []

        # Check requirement count
        if _table_exists(conn, "intake_requirements"):
            req_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM intake_requirements WHERE session_id = %s",
                (session_id,),
            ).fetchone()
            req_n = req_count[0] if isinstance(req_count, (tuple, list)) else req_count["cnt"]

            if req_n == 0:
                advisories.append(
                    "No requirements extracted yet. Continue the conversation to "
                    "capture specific requirements, or upload a SOW/CDD document"
                )
            elif req_n < 5:
                advisories.append(
                    f"Only {req_n} requirement(s) captured so far. "
                    "Typical projects need 10-20+ for meaningful decomposition"
                )

        # Check gap detection status
        if _table_exists(conn, "intake_gaps"):
            gap_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM intake_gaps WHERE session_id = %s AND severity = 'critical'",
                (session_id,),
            ).fetchone()
            gaps = gap_count[0] if isinstance(gap_count, (tuple, list)) else gap_count["cnt"]
            if gaps > 0:
                advisories.append(f"{gaps} critical gap(s) detected. Address these before proceeding to decomposition")

        # Suggest readiness scoring if enough requirements
        if _table_exists(conn, "intake_requirements"):
            req_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM intake_requirements WHERE session_id = %s",
                (session_id,),
            ).fetchone()
            req_n = req_count[0] if isinstance(req_count, (tuple, list)) else req_count["cnt"]
            if req_n >= 5:
                advisories.append(
                    f"With {req_n} requirements captured, consider running readiness "
                    "scoring to evaluate completeness across 7 dimensions"
                )

        # Check boundary impact if project has ATO context
        if project_id and _table_exists(conn, "boundary_assessments"):
            red_count = conn.execute(
                """SELECT COUNT(*) as cnt FROM boundary_assessments
                   WHERE project_id = %s AND impact_tier = 'RED'
                   AND status != 'resolved'""",
                (project_id,),
            ).fetchone()
            reds = red_count[0] if isinstance(red_count, (tuple, list)) else red_count["cnt"]
            if reds > 0:
                advisories.append(f"{reds} RED-tier boundary impact(s) require alternative COAs before proceeding")

        if not advisories:
            return None

        return {
            "gap_id": "intake_enrichment",
            "severity": "medium" if any("critical" in a or "RED" in a for a in advisories) else "low",
            "message": "Intake session insights: " + "; ".join(advisories) + ".",
            "action": f"python tools/requirements/readiness_scorer.py --session-id {session_id} --json",
        }
    except Exception as exc:
        logger.debug("Intake enrichment check failed: %s", exc)
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Hook handler
# ---------------------------------------------------------------------------


def handle(context: dict) -> dict:
    """chat_message_after handler — inject intake enrichment advisory."""
    content = (context.get("content") or "").lower()
    context_id = context.get("context_id", "")
    turn_number = context.get("turn_number", 0)
    project_id = context.get("project_id", "")
    intake_session_id = context.get("intake_session_id", "")

    if context.get("role") != "assistant":
        return context

    # Must have intake session or keyword trigger
    has_intake = bool(intake_session_id)
    user_query = (context.get("user_query") or "").lower()
    combined = content + " " + user_query
    has_keywords = any(kw in combined for kw in INTAKE_KEYWORDS)

    if not has_intake and not has_keywords:
        return context

    if not _should_advise(context_id, turn_number):
        return context

    # If we have an intake session, check enrichment
    if intake_session_id:
        advisory = _check_intake_enrichment(intake_session_id, project_id)
        if advisory:
            _record_advisory(context_id, turn_number)
            result = dict(context)
            result["intake_advisory"] = advisory
            return result

    # Even without session, if keywords match, suggest starting intake
    if has_keywords and not has_intake and project_id:
        _record_advisory(context_id, turn_number)
        result = dict(context)
        result["intake_advisory"] = {
            "gap_id": "intake_not_started",
            "severity": "low",
            "message": (
                "You're discussing requirements but no intake session is active. "
                "Starting a RICOAS intake session enables AI-guided requirements "
                "capture, gap detection, and 7-dimension readiness scoring."
            ),
            "action": f"python tools/requirements/intake_engine.py --project-id {project_id} --json",
        }
        return result

    return context


# ---------------------------------------------------------------------------
# Extension registration
# ---------------------------------------------------------------------------

NAME = "intake_enrichment_chat"
PRIORITY = 80
ALLOW_MODIFICATION = True
DESCRIPTION = "Enrich intake conversations with readiness scoring, gap detection, and boundary awareness (RICOAS)"

EXTENSION_HOOKS = {
    "chat_message_after": {
        "handler": handle,
        "name": NAME,
        "priority": PRIORITY,
        "allow_modification": ALLOW_MODIFICATION,
        "description": DESCRIPTION,
    },
}
