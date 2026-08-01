#!/usr/bin/env python3
# CUI // SP-CTI
"""Build completion → Kanban V&V chain extension.

Hooks into ``chat_message_after``. When an assistant message contains
build-completion signals, auto-creates a 3-task CodeLens + Coherence + E2E
chain in kanban_tasks, linked to the current chat context.

Throttle: one V&V chain per context per 20 turns (prevents spam during
conversational refinement that isn't a real build event).

Exports:
    EXTENSION_HOOKS — dict mapping hook point names to handler metadata.
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import re
from tools.db.storage import get_connection

logger = get_logger("icdev.extensions.build_kanban_sync")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

VV_CHAIN_COOLDOWN_TURNS = 20

BUILD_COMPLETION_SIGNALS = [
    r"\broutes? verified\b",
    r"\ball \d+ routes?\b.*\b200\b",
    r"\bcanvas build complete\b",
    r"\bcoherence gate.{0,30}\b0 failures?\b",
    r"\bshippe?d\b.{0,50}\bOK\b",
    r"\bcanvas.*complete\b",
    r"\bblueprint.*rewritten\b",
    r"\bimplementation complete\b",
    r"\ball tests? pass(ed|ing)?\b",
    r"\bphase \d+ complete\b",
    r"\btasks? (seeded|created|inserted)\b",
]

_last_vv_turn: dict[str, int] = {}


def _is_build_complete(content: str) -> bool:
    lower = content.lower()
    return any(re.search(pat, lower) for pat in BUILD_COMPLETION_SIGNALS)


def _should_create_chain(context_id: str, turn_number: int) -> bool:
    last = _last_vv_turn.get(context_id, -VV_CHAIN_COOLDOWN_TURNS - 1)
    return (turn_number - last) >= VV_CHAIN_COOLDOWN_TURNS


def _detect_canvas(content: str) -> str:
    """Infer canvas name from message content."""
    patterns = {
        "govlift": r"\bgovlift\b",
        "network": r"\bnetwork.?(canvas|migration)\b",
        "geosigint": r"\bgeosigint\b",
        "fathomdesk": r"\bfathomdesk\b",
        "strategos": r"\bstrategos\b",
        "innovation": r"\binnovation\b",
    }
    lower = content.lower()
    for canvas, pat in patterns.items():
        if re.search(pat, lower):
            return canvas
    return ""


def handle_chat_message_after(event: dict, ctx: object | None) -> None:
    """Fire after each assistant message is stored."""
    context_id = event.get("context_id", "")
    role = event.get("role", "")
    content = event.get("content", "")
    turn_number = event.get("turn_number", 0)

    if role != "assistant" or not content or not context_id:
        return

    if not _is_build_complete(content):
        return

    if not _should_create_chain(context_id, turn_number):
        return

    canvas = _detect_canvas(content)

    try:
        from tools.chat.kanban_bridge import create_vv_chain
        tasks = create_vv_chain(context_id, canvas=canvas)
        _last_vv_turn[context_id] = turn_number
        logger.info(
            "build_kanban_sync: created %d V&V tasks for context %s (canvas=%s)",
            len(tasks), context_id, canvas or "unspecified",
        )
        # Inject a system advisory into the chat as an assistant follow-up
        _inject_advisory(context_id, tasks, canvas)
    except Exception:
        logger.exception("build_kanban_sync: failed to create V&V chain for %s", context_id)


def _inject_advisory(context_id: str, tasks: list[dict], canvas: str) -> None:
    """Write a brief advisory message into chat_messages so the Tasks tab lights up."""
    try:
        conn = get_connection()
        from datetime import datetime, timezone
        import hashlib
        import time
        msg_id = "msg-" + hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:10]
        label = f" [{canvas}]" if canvas else ""
        body = (
            f"**V&V chain queued{label}** — {len(tasks)} tasks created in Kanban:\n"
            + "\n".join(f"- `{t['id']}` {t['title'][:60]}" for t in tasks)
            + "\n\nOpen the **Tasks** tab to monitor progress."
        )
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT OR IGNORE INTO chat_messages
               (id, context_id, role, content, content_type, created_at)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (msg_id, context_id, "system", body, "build_advisory", now),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        # advisory is best-effort
        logger.warning("_inject_advisory: best-effort INSERT into chat_messages failed (non-blocking): %s", exc)


# ---------------------------------------------------------------------------
# Extension registration
# ---------------------------------------------------------------------------

EXTENSION_HOOKS = {
    "chat_message_after": {
        "handler": handle_chat_message_after,
        "priority": 81,
        "description": "Auto-create CodeLens+Coherence+E2E Kanban chain on build completion",
        "enabled": True,
    }
}
