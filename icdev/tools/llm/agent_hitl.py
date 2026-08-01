# CUI // SP-CTI
"""Mid-turn HITL (Human-In-The-Loop) checkpoints for :func:`run_agent_loop`.

Provides :func:`build_hitl_hook` — returns an ``on_pre_tool_use``-compatible
callable that pauses the agent loop before executing a tool in the ``trigger_tools``
set, waits for human approval via the ACE dashboard, and unblocks when resolved.

Resolution flow
---------------
1. LLM emits a ``tool_use`` block for a HITL-triggerable tool name.
2. The hook writes:
   - ``agent_hitl_pending`` row (status='pending') — visible in the sessions UI.
   - ``ace_audit_log`` row (action='hitl_pending') — picked up by the existing
     ``/api/ace/<instance_id>/hitl`` endpoint and the ACE HITL dashboard card.
3. The hook polls ``ace_audit_log`` every ``poll_interval_seconds`` for a matching
   ``hitl_resolved`` (→ allow) or ``hitl_rejected`` (→ block) row written by
   :meth:`HITLGate.resolve` (coworker_id + detail match).
4. Returns:
   - ``None`` — tool execution allowed (approved or ``trigger_tools`` is empty).
   - Error string — tool blocked (rejected, timed out, or stop event fired).

The ``detail`` key is ``"hitl_agent:<tool_name>:<8-char hex>"`` — unique per call so
concurrent HITL requests for the same tool don't cross-resolve.

DB connection
-------------
Both tables live in the ACE canvas DB (``ICDEV_ACE_DB_URL`` env var).  Reads and
writes use ``get_canvas_connection("ICDEV_ACE_DB_URL")``.  If the DB is unavailable
the hook degrades gracefully: it logs a warning and returns ``None`` (allow).
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.llm.agent_hitl")

_DB_ENV = "ICDEV_ACE_DB_URL"
_DETAIL_PREFIX = "hitl_agent:"
_DEFAULT_TIMEOUT = 300     # 5 minutes
_DEFAULT_POLL    = 5.0     # seconds between DB polls

# Module-level reference — patchable in tests.
try:
    from icdev.tools.db.storage import get_canvas_connection
except Exception:  # noqa: BLE001
    get_canvas_connection = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_hitl_hook(
    trigger_tools: "set[str] | frozenset[str] | list[str]",
    instance_id: str,
    coworker_id: str,
    stop_event: "threading.Event | None" = None,
    timeout_seconds: int = _DEFAULT_TIMEOUT,
    poll_interval_seconds: float = _DEFAULT_POLL,
) -> Callable[[str, dict[str, Any]], "str | None"]:
    """Return an ``on_pre_tool_use`` hook that gates the given tools on human approval.

    Args:
        trigger_tools:        Tool names that trigger a HITL pause.  Pass an empty
                              set/list to get a pass-through no-op hook.
        instance_id:          ACE instance ID (used for audit log rows and DB records).
        coworker_id:          Co-worker ID (used for audit log rows and DB records).
        stop_event:           Optional :class:`threading.Event`; if set, the poll
                              loop exits immediately and returns an error string.
        timeout_seconds:      Maximum time (seconds) to wait before auto-rejecting.
        poll_interval_seconds: Interval between ``ace_audit_log`` polls.

    Returns:
        A callable ``(name: str, inp: dict) -> str | None`` suitable for passing
        to :func:`run_agent_loop` as ``on_pre_tool_use``.
    """
    _triggers = frozenset(trigger_tools)
    if not _triggers:
        return lambda name, inp: None  # pass-through

    def _hook(name: str, inp: dict[str, Any]) -> "str | None":
        if name not in _triggers:
            return None
        return _checkpoint(
            tool_name=name,
            tool_input=inp,
            instance_id=instance_id,
            coworker_id=coworker_id,
            stop_event=stop_event,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )

    return _hook


def get_pending_hitl(instance_id: str) -> list[dict[str, Any]]:
    """Return pending HITL requests for the given instance (for dashboard display).

    Returns a list of dicts with keys: id, coworker_id, tool_name, tool_input_json,
    detail, status, created_at.  Returns [] on any error.
    """
    if get_canvas_connection is None:
        return []
    try:
        conn = get_canvas_connection(_DB_ENV)
        try:
            cur = conn.execute(
                "SELECT id, coworker_id, tool_name, tool_input_json, detail, "
                "status, created_at "
                "FROM agent_hitl_pending "
                "WHERE instance_id = ? AND status = 'pending' "
                "ORDER BY created_at DESC",
                (instance_id,),
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent_hitl.get_pending: failed for %s: %s", instance_id, exc)
        return []


# ---------------------------------------------------------------------------
# Internal implementation
# ---------------------------------------------------------------------------


def _checkpoint(
    *,
    tool_name: str,
    tool_input: dict[str, Any],
    instance_id: str,
    coworker_id: str,
    stop_event: "threading.Event | None",
    timeout_seconds: int,
    poll_interval_seconds: float,
) -> "str | None":
    """Write pending records, poll for resolution; return None=allow or error string."""
    detail = f"{_DETAIL_PREFIX}{tool_name}:{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    pending_id = uuid.uuid4().hex

    # --- write pending records -----------------------------------------------
    _write_pending(
        pending_id=pending_id,
        detail=detail,
        tool_name=tool_name,
        tool_input=tool_input,
        instance_id=instance_id,
        coworker_id=coworker_id,
        now=now,
    )

    logger.info(
        "agent_hitl: HITL checkpoint for %s/%s tool=%r detail=%r — waiting up to %ds",
        instance_id, coworker_id, tool_name, detail, timeout_seconds,
    )

    # --- poll for resolution --------------------------------------------------
    deadline = time.monotonic() + timeout_seconds
    while True:
        if stop_event is not None and stop_event.is_set():
            _update_status(pending_id, "rejected", instance_id)
            return f"HITL checkpoint cancelled: stop event fired before '{tool_name}' was approved."

        outcome = _check_resolution(coworker_id, detail)
        if outcome == "approved":
            _update_status(pending_id, "approved", instance_id)
            logger.info("agent_hitl: approved %r for %s", tool_name, coworker_id)
            return None
        if outcome == "rejected":
            _update_status(pending_id, "rejected", instance_id)
            logger.info("agent_hitl: rejected %r for %s", tool_name, coworker_id)
            return (
                f"Tool '{tool_name}' was rejected by a human reviewer. "
                "The task cannot proceed with this action."
            )

        if time.monotonic() >= deadline:
            _update_status(pending_id, "timed_out", instance_id)
            logger.warning(
                "agent_hitl: timed out waiting for %r approval for %s",
                tool_name, coworker_id,
            )
            return (
                f"HITL approval for '{tool_name}' timed out after {timeout_seconds}s. "
                "The tool call was not executed. Try a different approach or a "
                "read-only alternative."
            )

        time.sleep(min(poll_interval_seconds, max(0.0, deadline - time.monotonic())))


def _write_pending(
    *,
    pending_id: str,
    detail: str,
    tool_name: str,
    tool_input: dict[str, Any],
    instance_id: str,
    coworker_id: str,
    now: str,
) -> None:
    """Write agent_hitl_pending + ace_audit_log rows (best-effort, never raises)."""
    if get_canvas_connection is None:
        return
    try:
        conn = get_canvas_connection(_DB_ENV)
        try:
            conn.execute(
                "INSERT INTO agent_hitl_pending "
                "(id, instance_id, coworker_id, tool_name, tool_input_json, detail, "
                "status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
                (
                    pending_id,
                    instance_id,
                    coworker_id,
                    tool_name,
                    json.dumps(tool_input, ensure_ascii=False),
                    detail,
                    now,
                ),
            )
            conn.execute(
                "INSERT INTO ace_audit_log "
                "(instance_id, coworker_id, action, detail, actor, created_at) "
                "VALUES (?, ?, 'hitl_pending', ?, 'agent_hitl', ?)",
                (instance_id, coworker_id, detail, now),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent_hitl._write_pending: DB write failed: %s", exc)


def _check_resolution(coworker_id: str, detail: str) -> "str | None":
    """Return 'approved', 'rejected', or None (still pending)."""
    if get_canvas_connection is None:
        return None
    try:
        conn = get_canvas_connection(_DB_ENV)
        try:
            resolved = conn.execute(
                "SELECT action FROM ace_audit_log "
                "WHERE coworker_id = ? AND detail = ? "
                "AND action IN ('hitl_resolved', 'hitl_rejected') "
                "ORDER BY created_at DESC LIMIT 1",
                (coworker_id, detail),
            ).fetchone()
        finally:
            conn.close()
        if resolved is None:
            return None
        return "approved" if resolved[0] == "hitl_resolved" else "rejected"
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent_hitl._check_resolution: DB read failed: %s", exc)
        return None


def _update_status(pending_id: str, status: str, instance_id: str) -> None:
    """Update agent_hitl_pending.status and resolved_at (best-effort)."""
    if get_canvas_connection is None:
        return
    try:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn = get_canvas_connection(_DB_ENV)
        try:
            conn.execute(
                "UPDATE agent_hitl_pending SET status = ?, resolved_at = ? WHERE id = ?",
                (status, now, pending_id),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        pass
