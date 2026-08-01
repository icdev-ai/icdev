#!/usr/bin/env python3
# CUI // SP-CTI
"""Bayesian Teaching Intelligence chat extension (D-BT-1 through D-BT-6).

Hooks into ``chat_message_after`` to suggest optimal next compliance controls
or learning items based on information-gain scoring.  When the user discusses
compliance, ATO, or controls, the extension recommends the highest-value next
action using Bayesian teaching order.

Advisory messages are throttled by a cooldown (default: 12 turns).

Loaded automatically by ExtensionManager._auto_load_builtins().

Exports:
    EXTENSION_HOOKS — dict mapping hook point names to handler metadata.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

from tools.db.storage import get_connection, table_exists
from pathlib import Path

logger = get_logger("icdev.extensions.bayesian_learning_chat")

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ADVISORY_COOLDOWN_TURNS = 12

# Keywords that trigger Bayesian learning advisory
LEARNING_KEYWORDS = [
    "compliance",
    "nist",
    "control",
    "ato",
    "fedramp",
    "cmmc",
    "stig",
    "poam",
    "ssp",
    "authorization",
    "accreditation",
    "800-53",
    "security control",
    "implement control",
    "control family",
    "assessment",
    "readiness",
    "what should i do next",
    "which control",
    "prioritize",
    "order of operations",
]

_last_advisory_turn: dict = {}


def _should_advise(context_id: str, turn_number: int) -> bool:
    last = _last_advisory_turn.get(context_id, -ADVISORY_COOLDOWN_TURNS - 1)
    return (turn_number - last) >= ADVISORY_COOLDOWN_TURNS


def _record_advisory(context_id: str, turn_number: int):
    _last_advisory_turn[context_id] = turn_number


# ---------------------------------------------------------------------------
# Bayesian teaching lookup
# ---------------------------------------------------------------------------


def _get_optimal_next_controls(project_id: str) -> dict | None:
    """Query Bayesian teacher for optimal next compliance control to implement.

    Returns advisory dict or None.
    """
    try:
        from tools.intelligence.bayesian_teacher import BayesianTeacher

        teacher = BayesianTeacher()

        # Get optimal ordering for compliance controls
        result = teacher.optimal_ordering(project_id=project_id)
        if not result or not result.get("ordered_items"):
            return None

        ordered = result["ordered_items"]
        # Find first un-implemented control
        next_items = ordered[:3]  # Top 3 recommendations
        if not next_items:
            return None

        items_text = ", ".join(
            f"{item.get('id', 'Unknown')} (info-gain: {item.get('score', 0):.2f})" for item in next_items
        )

        return {
            "gap_id": "bayesian_next_control",
            "severity": "low",
            "message": (
                f"Based on information-gain analysis, the highest-value controls to "
                f"implement next are: {items_text}. These maximize your ATO coverage "
                f"with the fewest additional steps."
            ),
            "action": f"python tools/intelligence/bayesian_teacher.py --optimal-order --project-id {project_id} --json",
            "score": next_items[0].get("score", 0) if next_items else 0,
            "fitness_domain": "compliance",
        }
    except (ImportError, Exception) as exc:
        logger.debug("Bayesian teacher unavailable: %s", exc)

    # Fallback: suggest running the teacher
    return _get_fallback_advisory(project_id)


def _get_fallback_advisory(project_id: str) -> dict | None:
    """Fallback advisory when Bayesian teacher data is unavailable."""
    if not DB_PATH.exists():
        return None

    try:
        conn = get_connection()
    except Exception:
        return None

    try:
        # Check if any compliance assessments exist
        tables = ["fedramp_assessments", "cmmc_assessments", "stig_findings"]
        assessment_count = 0
        for tbl in tables:
            # Shared backend-aware probe: the inline ``sqlite_master ... as cnt``
            # form bypassed translate_sql (the ``as cnt`` alias defeats rule-14)
            # and raised on PostgreSQL, silently yielding zero assessments.
            if table_exists(conn, tbl):
                cnt = conn.execute(  # nosec B608 — tbl from hardcoded constant list
                    f"SELECT COUNT(*) as cnt FROM {tbl} WHERE project_id = %s",  # nosec B608 -- table/column names are internal constants, not user input
                    (project_id,),
                ).fetchone()
                assessment_count += cnt[0] if isinstance(cnt, (tuple, list)) else cnt["cnt"]

        if assessment_count == 0:
            return {
                "gap_id": "bayesian_no_assessments",
                "severity": "medium",
                "message": (
                    "No compliance assessments found for this project. Run an initial "
                    "assessment to enable Bayesian learning recommendations that optimize "
                    "your control implementation order."
                ),
                "action": f"python tools/intelligence/bayesian_teacher.py --optimal-order --project-id {project_id} --json",
                "fitness_domain": "compliance",
            }
        return None
    except Exception as exc:
        logger.debug("Bayesian fallback check failed: %s", exc)
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Hook handler
# ---------------------------------------------------------------------------


def handle(context: dict) -> dict:
    """chat_message_after handler — inject Bayesian learning advisory."""
    content = (context.get("content") or "").lower()
    context_id = context.get("context_id", "")
    turn_number = context.get("turn_number", 0)
    project_id = context.get("project_id", "")

    # Only assistant responses
    if context.get("role") != "assistant":
        return context

    # Check keywords
    if not any(kw in content for kw in LEARNING_KEYWORDS):
        # Also check user query
        user_query = (context.get("user_query") or "").lower()
        if not any(kw in user_query for kw in LEARNING_KEYWORDS):
            return context

    # Cooldown
    if not _should_advise(context_id, turn_number):
        return context

    if not project_id:
        return context

    advisory = _get_optimal_next_controls(project_id)
    if not advisory:
        return context

    _record_advisory(context_id, turn_number)

    result = dict(context)
    result["bayesian_advisory"] = advisory
    return result


# ---------------------------------------------------------------------------
# Extension registration
# ---------------------------------------------------------------------------

NAME = "bayesian_learning_chat"
PRIORITY = 40
ALLOW_MODIFICATION = True
DESCRIPTION = "Suggest optimal compliance control ordering via Bayesian information-gain analysis (D-BT-1)"

EXTENSION_HOOKS = {
    "chat_message_after": {
        "handler": handle,
        "name": NAME,
        "priority": PRIORITY,
        "allow_modification": ALLOW_MODIFICATION,
        "description": DESCRIPTION,
    },
}
