# CUI // SP-CTI
"""Gateway agent-mode (sag-gw-01).

Lets a *bound* user's free-text message route to the ICDEV standalone agent
runtime (SAG) instead of only the structured ``command_router`` path — a
conversational assistant reachable from Telegram/Slack/Teams/… — **without
weakening any gateway control**.

Design (thin, reuses the existing gateway spine):

- **Full 8-gate security chain is preserved.** Agent-mode does not add a bypass:
  a free-text message is treated as a synthetic ``agent`` command with its own
  allowlist entry (category / max_il / channels from config), so
  :func:`tools.gateway.security_chain.run_security_chain` runs unchanged —
  signature, bot/replay, identity binding, authentication, classification, RBAC,
  rate-limit, and domain-authority gates all apply.
- **User binding is respected.** The turn runs as the resolved
  ``envelope.icdev_user_id`` (populated by the identity gate); an unbound user
  never reaches agent-mode (gate 3 rejects first).
- **IL-aware response filtering is preserved.** The agent's reply passes through
  :func:`tools.gateway.response_filter.filter_response` against the channel's
  ``max_il`` before it is returned — identical to ``execute_command``.
- **Per-chat session.** ``(channel, chat_id)`` maps to a SAG ``ctx_id`` in
  ``remote_agent_sessions`` so a conversation resumes across messages.
- **Config-gated, default off**, with per-channel enablement.

The sag-rt-02 slash commands are reused: a message beginning with ``/`` is
dispatched as a bot command through the runtime's command registry.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.gateway.agent_mode")

# The synthetic command name a free-text agent turn is validated as.
AGENT_COMMAND = "agent"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _agent_cfg(config: Dict[str, Any]) -> Dict[str, Any]:
    cfg = config.get("agent_mode")
    return cfg if isinstance(cfg, dict) else {}


def is_agent_mode_enabled(config: Dict[str, Any], channel_name: str) -> bool:
    """True when agent-mode is on globally AND permitted on ``channel_name``."""
    cfg = _agent_cfg(config)
    if not cfg.get("enabled", False):
        return False
    channels = cfg.get("channels", "")
    if channels in ("*", "all"):
        return True
    if isinstance(channels, (list, tuple)):
        allowed = {str(c).strip() for c in channels}
    else:
        allowed = {c.strip() for c in str(channels).split(",") if c.strip()}
    return channel_name in allowed


def _agent_allowlist_entry(config: Dict[str, Any], channel_name: str) -> Dict[str, Any]:
    """Synthetic allowlist entry so the 8-gate chain validates the agent turn."""
    cfg = _agent_cfg(config)
    return {
        "command": AGENT_COMMAND,
        "category": cfg.get("category", "execute"),
        "channels": channel_name,
        "max_il": cfg.get("max_il", "IL5"),
        "requires_confirmation": bool(cfg.get("requires_confirmation", False)),
    }


def prepare_agent_envelope(
    envelope: Any,
    channel_name: str,
    config: Dict[str, Any],
    allowlist: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], bool]:
    """Decide whether this message is an agent turn; if so, prep it for the chain.

    Returns ``(effective_allowlist, is_agent)``. When the message is already a
    structured, allowlisted command — or agent-mode is disabled for the channel —
    the original allowlist is returned with ``is_agent=False`` (identical
    behaviour to before). Otherwise the raw text is preserved as the agent prompt,
    ``envelope.command`` is rewritten to :data:`AGENT_COMMAND`, and a synthetic
    ``agent`` allowlist entry is appended so ``run_security_chain`` still runs all
    8 gates.
    """
    from tools.gateway.command_router import is_command_allowed

    allowed, _entry = is_command_allowed(envelope.command, channel_name, allowlist)
    if allowed:
        return allowlist, False
    if not is_agent_mode_enabled(config, channel_name):
        return allowlist, False

    # Preserve the full original text as the prompt (parse_command_text only kept
    # the first token as `command`).
    envelope.args = dict(getattr(envelope, "args", {}) or {})
    envelope.args["agent_prompt"] = envelope.raw_text
    envelope.command = AGENT_COMMAND
    effective = list(allowlist) + [_agent_allowlist_entry(config, channel_name)]
    return effective, True


# ---------------------------------------------------------------------------
# Session mapping — (channel, chat_id) -> ctx_id
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure_schema(conn) -> None:
    """Self-create remote_agent_sessions (idempotent) so a checkout that has not
    run migration 288 still works. TEXT-only, dialect-neutral."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS remote_agent_sessions (
            id            TEXT PRIMARY KEY,
            channel       TEXT NOT NULL,
            chat_id       TEXT NOT NULL,
            icdev_user_id TEXT,
            tenant_id     TEXT DEFAULT '',
            context_id    TEXT NOT NULL,
            created_at    TEXT,
            last_activity_at TEXT,
            UNIQUE (channel, chat_id)
        )
        """
    )


def lookup_session(channel: str, chat_id: str) -> Optional[str]:
    """Return the mapped ``context_id`` for ``(channel, chat_id)`` or None."""
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT context_id FROM remote_agent_sessions "
            "WHERE channel = %s AND chat_id = %s",
            (channel, chat_id),
        ).fetchone()
        if not row:
            return None
        return row[0] if not hasattr(row, "keys") else row["context_id"]
    except Exception as exc:  # noqa: BLE001 — degrade to a fresh session
        logger.warning("agent_mode: lookup_session failed: %s", exc)
        return None
    finally:
        conn.close()


def store_session(
    channel: str,
    chat_id: str,
    icdev_user_id: str,
    tenant_id: str,
    context_id: str,
) -> None:
    """Persist a new ``(channel, chat_id) -> context_id`` mapping."""
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        _ensure_schema(conn)
        now = _now()
        conn.execute(
            "INSERT INTO remote_agent_sessions "
            "(id, channel, chat_id, icdev_user_id, tenant_id, context_id, "
            " created_at, last_activity_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (str(uuid.uuid4()), channel, chat_id, icdev_user_id or "",
             tenant_id or "", context_id, now, now),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 — best-effort; a lost mapping just
        # means the next message starts a fresh session.
        logger.warning("agent_mode: store_session failed: %s", exc)
    finally:
        conn.close()


def touch_session(channel: str, chat_id: str) -> None:
    """Bump ``last_activity_at`` for an existing mapping (best-effort)."""
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE remote_agent_sessions SET last_activity_at = %s "
            "WHERE channel = %s AND chat_id = %s",
            (_now(), channel, chat_id),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("agent_mode: touch_session failed: %s", exc)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Agent turn execution
# ---------------------------------------------------------------------------


def _result(
    success: bool,
    output: str,
    *,
    filtered: bool = False,
    detected_il: str = "",
    audit_id: str = "",
    start_time: float = 0.0,
    context_id: str = "",
) -> Dict[str, Any]:
    return {
        "success": success,
        "output": output,
        "raw_output": output,
        "filtered": filtered,
        "detected_il": detected_il,
        "execution_time_ms": int((time.time() - start_time) * 1000) if start_time else 0,
        "audit_id": audit_id,
        "context_id": context_id,
    }


def handle_agent_message(
    envelope: Any,
    channel_config: Dict[str, Any],
    gateway_config: Dict[str, Any],
) -> Dict[str, Any]:
    """Run one SAG agent turn for a (gate-cleared) free-text message.

    The 8-gate chain has already passed and ``envelope.icdev_user_id`` is bound.
    Resumes or creates the per-chat SAG session, runs the turn (or dispatches a
    ``/`` bot command), IL-filters the reply against the channel ``max_il``, logs
    to the audit trail, and returns an ``execute_command``-shaped result.
    """
    start_time = time.time()
    audit_id = str(uuid.uuid4())
    channel_max_il = channel_config.get("max_il", "IL4")
    channel = getattr(envelope, "channel", "")
    chat_id = getattr(envelope, "channel_thread_id", "") or getattr(
        envelope, "channel_user_id", ""
    )
    user_id = getattr(envelope, "icdev_user_id", "") or ""
    tenant_id = getattr(envelope, "tenant_id", "") or ""
    prompt = (getattr(envelope, "args", {}) or {}).get("agent_prompt") or getattr(
        envelope, "raw_text", ""
    )

    try:
        from tools.agent_runtime.commands import dispatch as _dispatch
        from tools.agent_runtime.runtime import AgentRuntime
        from tools.agent_runtime.sessions import ensure_chat_tables

        ensure_chat_tables()

        runtime = AgentRuntime(
            command_handler=_dispatch, user_id=user_id, tenant_id=tenant_id
        )

        existing = lookup_session(channel, chat_id)
        if existing:
            try:
                runtime.resume_session(existing)
            except Exception as exc:  # noqa: BLE001 — stale mapping → fresh session
                logger.info("agent_mode: could not resume %s: %s", existing, exc)
                existing = None
        if not existing:
            store_session(channel, chat_id, user_id, tenant_id, runtime.session.context_id)

        # Optional toolset restriction for remote agents (default: read-only).
        bundles = _agent_cfg(gateway_config).get("toolsets") or []
        if bundles:
            try:
                runtime.use_toolset(list(bundles))
            except Exception as exc:  # noqa: BLE001
                logger.warning("agent_mode: toolset restriction failed: %s", exc)

        text = (prompt or "").strip()
        if text.startswith("/"):
            _handled, raw_output, _exit = runtime.dispatch_command(text)
        else:
            result = runtime.run_turn(text)
            raw_output = getattr(result, "final_content", "") or "(no response)"

        touch_session(channel, chat_id)
        ctx_id = runtime.session.context_id
    except Exception as exc:  # noqa: BLE001 — never leak a stack trace to a channel
        logger.exception("agent_mode: agent turn failed")
        return _result(False, f"Agent error: {exc}", audit_id=audit_id, start_time=start_time)

    # IL-aware response filtering (identical to execute_command).
    from tools.gateway.response_filter import filter_response, truncate_response

    filtered_output, was_filtered, detected_il = filter_response(
        raw_output, channel_max_il, envelope.id
    )
    response_config = gateway_config.get("gateway", {}).get("response", {})
    max_length = response_config.get("max_length", 4000)
    filtered_output = truncate_response(filtered_output, max_length)

    _audit_agent_turn(envelope, audit_id, ctx_id, was_filtered, detected_il, start_time)

    return _result(
        True,
        filtered_output,
        filtered=was_filtered,
        detected_il=detected_il,
        audit_id=audit_id,
        start_time=start_time,
        context_id=ctx_id,
    )


def _audit_agent_turn(envelope, audit_id, ctx_id, was_filtered, detected_il, start_time) -> None:
    try:
        from tools.audit.audit_logger import log_event as audit_log_event

        audit_log_event(
            action="remote_agent_turn",
            actor=getattr(envelope, "icdev_user_id", "") or getattr(envelope, "channel_user_id", ""),
            details={
                "audit_id": audit_id,
                "channel": getattr(envelope, "channel", ""),
                "context_id": ctx_id,
                "response_filtered": was_filtered,
                "detected_il": detected_il,
                "execution_time_ms": int((time.time() - start_time) * 1000),
            },
        )
    except Exception as exc:  # noqa: BLE001 — audit is best-effort here
        logger.debug("agent_mode: audit log skipped: %s", exc)
