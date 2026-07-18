#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis v2.0 daemon status chat extension (D-GEN-1 through D-GEN-12).

Hooks into ``chat_message_after`` to surface Genesis autonomous research lab
findings, GKP (Genesis Knowledge Packet) promotions pending review, and
reflex status when the user discusses improvement, research, or self-healing.

Advisory messages are throttled by a cooldown (default: 20 turns).

Loaded automatically by ExtensionManager._auto_load_builtins().

Exports:
    EXTENSION_HOOKS — dict mapping hook point names to handler metadata.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

from tools.db.storage import get_connection, table_exists as _table_exists
from pathlib import Path

logger = get_logger("icdev.extensions.genesis_status_chat")

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ADVISORY_COOLDOWN_TURNS = 20

GENESIS_KEYWORDS = [
    "genesis",
    "improve",
    "self-heal",
    "autonomous",
    "research",
    "experiment",
    "autoresearch",
    "reflex",
    "gkp",
    "knowledge packet",
    "evolve",
    "innovation",
    "signal",
    "pattern",
    "self-improvement",
    "heal",
    "auto-fix",
    "remediate",
    "bayesian",
]

_last_advisory_turn: dict = {}


def _should_advise(context_id: str, turn_number: int) -> bool:
    last = _last_advisory_turn.get(context_id, -ADVISORY_COOLDOWN_TURNS - 1)
    return (turn_number - last) >= ADVISORY_COOLDOWN_TURNS


def _record_advisory(context_id: str, turn_number: int):
    _last_advisory_turn[context_id] = turn_number


# ---------------------------------------------------------------------------
# Genesis status check
# ---------------------------------------------------------------------------


def _check_genesis_status() -> dict | None:
    """Check Genesis daemon status and pending GKPs."""
    if not DB_PATH.exists():
        return None

    try:
        conn = get_connection()
    except Exception:
        return None

    try:
        advisories = []

        # Check pending GKPs
        if _table_exists(conn, "genesis_knowledge_packets"):
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM genesis_knowledge_packets WHERE status = 'pending_review'",
            ).fetchone()
            pending = row[0] if isinstance(row, (tuple, list)) else row["cnt"]
            if pending > 0:
                advisories.append(f"{pending} Genesis Knowledge Packet(s) pending your review")

        # Check recent autoresearch experiments
        if _table_exists(conn, "autoresearch_experiments"):
            row = conn.execute(
                """SELECT COUNT(*) as cnt FROM autoresearch_experiments
                   WHERE status = 'completed'
                   AND created_at > datetime('now', '-7 days')""",
            ).fetchone()
            recent = row[0] if isinstance(row, (tuple, list)) else row["cnt"]
            if recent > 0:
                # Get best improvement
                best = conn.execute(
                    """SELECT domain, improvement_pct FROM autoresearch_experiments
                       WHERE status = 'completed' AND improvement_pct > 0
                       ORDER BY improvement_pct DESC LIMIT 1""",
                ).fetchone()
                if best:
                    domain = best["domain"] if isinstance(best, dict) else best[0]
                    pct = best["improvement_pct"] if isinstance(best, dict) else best[1]
                    advisories.append(f"{recent} experiments completed this week (best: +{pct:.1f}% in {domain})")

        # Check innovation signals
        if _table_exists(conn, "innovation_signals"):
            row = conn.execute(
                """SELECT COUNT(*) as cnt FROM innovation_signals
                   WHERE triage_status = 'approved'
                   AND created_at > datetime('now', '-7 days')""",
            ).fetchone()
            signals = row[0] if isinstance(row, (tuple, list)) else row["cnt"]
            if signals > 0:
                advisories.append(f"{signals} innovation signal(s) approved this week")

        if not advisories:
            return None

        return {
            "gap_id": "genesis_activity",
            "severity": "low",
            "message": "Genesis autonomous activity: " + "; ".join(advisories) + ".",
            "action": "python tools/genesis/promoter.py --list --status-filter pending_review --json",
        }
    except Exception as exc:
        logger.debug("Genesis status check failed: %s", exc)
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Hook handler
# ---------------------------------------------------------------------------


def handle(context: dict) -> dict:
    """chat_message_after handler — inject Genesis status advisory."""
    content = (context.get("content") or "").lower()
    context_id = context.get("context_id", "")
    turn_number = context.get("turn_number", 0)

    if context.get("role") != "assistant":
        return context

    # Check keywords
    user_query = (context.get("user_query") or "").lower()
    combined = content + " " + user_query
    if not any(kw in combined for kw in GENESIS_KEYWORDS):
        return context

    if not _should_advise(context_id, turn_number):
        return context

    advisory = _check_genesis_status()
    if not advisory:
        return context

    _record_advisory(context_id, turn_number)

    result = dict(context)
    result["genesis_advisory"] = advisory
    return result


# ---------------------------------------------------------------------------
# Extension registration
# ---------------------------------------------------------------------------

NAME = "genesis_status_chat"
PRIORITY = 70
ALLOW_MODIFICATION = True
DESCRIPTION = "Surface Genesis v2.0 autonomous research findings and pending GKPs in chat (D-GEN-1)"

EXTENSION_HOOKS = {
    "chat_message_after": {
        "handler": handle,
        "name": NAME,
        "priority": PRIORITY,
        "allow_modification": ALLOW_MODIFICATION,
        "description": DESCRIPTION,
    },
}
