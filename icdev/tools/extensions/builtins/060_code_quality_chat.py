#!/usr/bin/env python3
# CUI // SP-CTI
"""Code quality advisory chat extension (Phase 52, D331-D337).

Hooks into ``chat_message_after`` to surface code quality insights when
the user discusses code, refactoring, or implementation topics.  Uses
the code_quality_metrics table to surface maintainability trends, smell
alerts, and complexity warnings.

Advisory messages are throttled by a cooldown (default: 15 turns).

Loaded automatically by ExtensionManager._auto_load_builtins().

Exports:
    EXTENSION_HOOKS — dict mapping hook point names to handler metadata.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

from tools.db.storage import get_connection, table_exists as _table_exists
from pathlib import Path

logger = get_logger("icdev.extensions.code_quality_chat")

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ADVISORY_COOLDOWN_TURNS = 15

CODE_KEYWORDS = [
    "code",
    "refactor",
    "function",
    "class",
    "complexity",
    "smell",
    "maintainab",
    "quality",
    "coverage",
    "test",
    "lint",
    "dead code",
    "cyclomatic",
    "nesting",
    "clean up",
    "tech debt",
    "code review",
    "implement",
    "build",
    "scaffold",
    "generate code",
]

_last_advisory_turn: dict = {}


def _should_advise(context_id: str, turn_number: int) -> bool:
    last = _last_advisory_turn.get(context_id, -ADVISORY_COOLDOWN_TURNS - 1)
    return (turn_number - last) >= ADVISORY_COOLDOWN_TURNS


def _record_advisory(context_id: str, turn_number: int):
    _last_advisory_turn[context_id] = turn_number


# ---------------------------------------------------------------------------
# Code quality check
# ---------------------------------------------------------------------------


def _check_code_quality(project_id: str) -> dict | None:
    """Check latest code quality metrics for advisory generation."""
    if not DB_PATH.exists():
        return None

    try:
        conn = get_connection()
    except Exception:
        return None

    try:
        if not _table_exists(conn, "code_quality_metrics"):
            return None

        # Get latest scan
        row = conn.execute(
            """SELECT maintainability_score, avg_complexity, total_smells,
                      total_functions, dead_code_pct, scanned_at
               FROM code_quality_metrics
               WHERE project_id = %s
               ORDER BY scanned_at DESC LIMIT 1""",
            (project_id,),
        ).fetchone()

        if not row:
            return {
                "gap_id": "code_quality_no_scan",
                "severity": "low",
                "message": (
                    "No code quality scan found for this project. "
                    "Run a scan to get maintainability insights and smell detection."
                ),
                "action": "python tools/analysis/code_analyzer.py --project-dir tools/ --store --json",
            }

        score = row["maintainability_score"] if isinstance(row, dict) else row[0]
        complexity = row["avg_complexity"] if isinstance(row, dict) else row[1]
        smells = row["total_smells"] if isinstance(row, dict) else row[2]
        functions = row["total_functions"] if isinstance(row, dict) else row[3]
        dead_pct = row["dead_code_pct"] if isinstance(row, dict) else row[4]

        issues = []
        if score is not None and score < 0.5:
            issues.append(f"maintainability score is low ({score:.2f}/1.00)")
        if complexity is not None and complexity > 15:
            issues.append(f"average complexity is high ({complexity:.1f}, threshold: 10)")
        if smells is not None and functions and smells / max(functions, 1) > 0.15:
            issues.append(f"{smells} code smells across {functions} functions")
        if dead_pct is not None and dead_pct > 5:
            issues.append(f"{dead_pct:.1f}% dead code detected")

        if not issues:
            return None

        return {
            "gap_id": "code_quality_issues",
            "severity": "medium" if (score is not None and score < 0.4) else "low",
            "message": (
                f"Code quality alert: {'; '.join(issues)}. Consider addressing high-complexity functions first."
            ),
            "action": "python tools/analysis/code_analyzer.py --project-dir tools/ --trend --json",
            "score": score,
        }
    except Exception as exc:
        logger.debug("Code quality check failed: %s", exc)
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Hook handler
# ---------------------------------------------------------------------------


def handle(context: dict) -> dict:
    """chat_message_after handler — inject code quality advisory."""
    content = (context.get("content") or "").lower()
    context_id = context.get("context_id", "")
    turn_number = context.get("turn_number", 0)
    project_id = context.get("project_id", "")

    if context.get("role") != "assistant":
        return context

    # Check keywords in response or user query
    user_query = (context.get("user_query") or "").lower()
    combined = content + " " + user_query
    if not any(kw in combined for kw in CODE_KEYWORDS):
        return context

    if not _should_advise(context_id, turn_number):
        return context

    if not project_id:
        return context

    advisory = _check_code_quality(project_id)
    if not advisory:
        return context

    _record_advisory(context_id, turn_number)

    result = dict(context)
    result["code_quality_advisory"] = advisory
    return result


# ---------------------------------------------------------------------------
# Extension registration
# ---------------------------------------------------------------------------

NAME = "code_quality_chat"
PRIORITY = 60
ALLOW_MODIFICATION = True
DESCRIPTION = "Surface code quality insights (maintainability, smells, complexity) in chat (Phase 52)"

EXTENSION_HOOKS = {
    "chat_message_after": {
        "handler": handle,
        "name": NAME,
        "priority": PRIORITY,
        "allow_modification": ALLOW_MODIFICATION,
        "description": DESCRIPTION,
    },
}
