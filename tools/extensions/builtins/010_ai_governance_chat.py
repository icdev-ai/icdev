#!/usr/bin/env python3
# CUI // SP-CTI
"""AI governance chat extension handler (D325, D327).

Hooks into ``chat_message_after`` to detect AI keywords in assistant responses,
check governance artifact status for the project, and inject advisory system
messages when gaps are found.  Advisory messages are throttled by a cooldown
(default: 5 turns) so the user is not spammed.

Loaded automatically by ExtensionManager._auto_load_builtins().

Exports:
    EXTENSION_HOOKS — dict mapping hook point names to handler metadata.
"""

import json
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger("icdev.extensions.ai_governance_chat")

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

_CONFIG_CACHE = None


def _load_config() -> dict:
    """Load chat governance config from YAML (cached)."""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE

    config_path = BASE_DIR / "args" / "ai_governance_config.yaml"
    defaults = {
        "advisory_cooldown_turns": 5,
        "ai_keywords": ["ai system", "machine learning", "ml model", "deep learning",
                        "neural network", "nlp", "computer vision", "recommendation",
                        "predictive model", "automated decision", "algorithmic",
                        "chatbot", "generative ai", "llm", "foundation model",
                        "model training", "model card", "model performance",
                        "ai governance", "responsible ai"],
        "advisory_priority_order": [
            "oversight_plan_missing", "impact_assessment_missing",
            "model_card_missing", "caio_not_designated",
            "fairness_not_assessed", "reassessment_overdue",
        ],
    }
    if config_path.exists():
        try:
            import yaml
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            chat_cfg = cfg.get("ai_governance", {}).get("chat_governance", {})
            _CONFIG_CACHE = {**defaults, **chat_cfg}
            return _CONFIG_CACHE
        except ImportError:
            pass

    _CONFIG_CACHE = defaults
    return _CONFIG_CACHE


# ---------------------------------------------------------------------------
# Cooldown tracking  (in-memory, per-context)
# ---------------------------------------------------------------------------

# Maps context_id -> last advisory turn number
_last_advisory_turn: dict = {}


def _should_advise(context_id: str, turn_number: int) -> bool:
    """Check if enough turns have passed since last advisory."""
    cfg = _load_config()
    cooldown = cfg.get("advisory_cooldown_turns", 5)
    last = _last_advisory_turn.get(context_id, -cooldown - 1)
    return (turn_number - last) >= cooldown


def _record_advisory(context_id: str, turn_number: int):
    _last_advisory_turn[context_id] = turn_number


# ---------------------------------------------------------------------------
# Governance gap checking
# ---------------------------------------------------------------------------

def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return (row[0] if isinstance(row, (tuple, list)) else row["cnt"]) > 0


def _check_governance_gaps(project_id: str) -> list:
    """Check for AI governance gaps for a project. Returns list of gap dicts."""
    if not DB_PATH.exists():
        return []

    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
    except Exception:
        return []

    gaps = []
    try:
        # Check oversight plan
        if _table_exists(conn, "ai_oversight_plans"):
            cnt = conn.execute(
                "SELECT COUNT(*) as cnt FROM ai_oversight_plans WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            if (cnt[0] if isinstance(cnt, (tuple, list)) else cnt["cnt"]) == 0:
                gaps.append({
                    "id": "oversight_plan_missing",
                    "severity": "high",
                    "message": "No human oversight plan registered for AI systems in this project.",
                    "action": "Register an oversight plan via /icdev-accountability.",
                })

        # Check impact assessment
        if _table_exists(conn, "ai_ethics_reviews"):
            cnt = conn.execute(
                "SELECT COUNT(*) as cnt FROM ai_ethics_reviews "
                "WHERE project_id = ? AND review_type = 'impact_assessment'",
                (project_id,),
            ).fetchone()
            if (cnt[0] if isinstance(cnt, (tuple, list)) else cnt["cnt"]) == 0:
                gaps.append({
                    "id": "impact_assessment_missing",
                    "severity": "high",
                    "message": "No algorithmic impact assessment has been completed.",
                    "action": "Conduct an impact assessment via /icdev-accountability.",
                })

        # Check model cards
        if _table_exists(conn, "ai_model_cards"):
            cnt = conn.execute(
                "SELECT COUNT(*) as cnt FROM ai_model_cards WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            if (cnt[0] if isinstance(cnt, (tuple, list)) else cnt["cnt"]) == 0:
                gaps.append({
                    "id": "model_card_missing",
                    "severity": "medium",
                    "message": "No model cards documented for AI models in this project.",
                    "action": "Create model cards via /icdev-transparency.",
                })

        # Check CAIO designation
        if _table_exists(conn, "ai_caio_registry"):
            cnt = conn.execute(
                "SELECT COUNT(*) as cnt FROM ai_caio_registry WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            if (cnt[0] if isinstance(cnt, (tuple, list)) else cnt["cnt"]) == 0:
                gaps.append({
                    "id": "caio_not_designated",
                    "severity": "medium",
                    "message": "No Chief AI Officer (CAIO) has been designated.",
                    "action": "Designate a CAIO via /icdev-accountability.",
                })

        # Check reassessment schedule
        if _table_exists(conn, "ai_reassessment_schedule"):
            cnt = conn.execute(
                "SELECT COUNT(*) as cnt FROM ai_reassessment_schedule WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            if (cnt[0] if isinstance(cnt, (tuple, list)) else cnt["cnt"]) == 0:
                gaps.append({
                    "id": "reassessment_overdue",
                    "severity": "low",
                    "message": "No reassessment schedule configured for AI systems.",
                    "action": "Schedule reassessments via /icdev-accountability.",
                })
    except Exception as exc:
        logger.debug("Error checking governance gaps: %s", exc)
    finally:
        conn.close()

    return gaps


# ---------------------------------------------------------------------------
# Hook handler
# ---------------------------------------------------------------------------

def handle(context: dict) -> dict:
    """chat_message_after handler — inject governance advisory if AI topic detected.

    Args:
        context: dict with keys context_id, role, content, turn_number,
                 and optionally project_id.

    Returns:
        context dict, possibly with ``governance_advisory`` key added.
    """
    content = (context.get("content") or "").lower()
    context_id = context.get("context_id", "")
    turn_number = context.get("turn_number", 0)
    project_id = context.get("project_id", "")

    # Only process assistant responses
    if context.get("role") != "assistant":
        return context

    # Check if content mentions AI keywords
    cfg = _load_config()
    ai_keywords = cfg.get("ai_keywords", [])
    if not any(kw in content for kw in ai_keywords):
        return context

    # Cooldown check
    if not _should_advise(context_id, turn_number):
        return context

    # No project context → skip
    if not project_id:
        return context

    # Check for governance gaps
    gaps = _check_governance_gaps(project_id)
    if not gaps:
        return context

    # Pick the highest-priority gap based on advisory_priority_order
    priority_order = cfg.get("advisory_priority_order", [])
    gap_ids = {g["id"]: g for g in gaps}
    selected = None
    for gid in priority_order:
        if gid in gap_ids:
            selected = gap_ids[gid]
            break
    if selected is None:
        selected = gaps[0]

    _record_advisory(context_id, turn_number)

    # Add advisory to context for chat_manager to pick up
    result = dict(context)
    result["governance_advisory"] = {
        "gap_id": selected["id"],
        "severity": selected["severity"],
        "message": selected["message"],
        "action": selected["action"],
        "total_gaps": len(gaps),
    }
    return result


# ---------------------------------------------------------------------------
# Extension registration metadata
# ---------------------------------------------------------------------------

NAME = "ai_governance_chat"
PRIORITY = 10
ALLOW_MODIFICATION = True
DESCRIPTION = "Detect AI keywords in chat and inject governance advisory messages (D325, D327)"

EXTENSION_HOOKS = {
    "chat_message_after": {
        "handler": handle,
        "name": NAME,
        "priority": PRIORITY,
        "allow_modification": ALLOW_MODIFICATION,
        "description": DESCRIPTION,
    },
}
