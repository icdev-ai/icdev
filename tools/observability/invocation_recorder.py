# CUI // SP-CTI
"""Record what the runtime actually ran — MCP tools, agents, personas, roles.

Measured 2026-08-02 against the live PostgreSQL board, before this existed:

    surface    audit events emitted   declared types   runtime table
    agents     56 (3 of 16 types)     16               agent_executions: 5 rows
    personas   0                      0                ace_sessions: 0 rows
    roles      0                      0                (no table)
    MCP        0                      0                (no table)

512 MCP tools, used by every session, and not one recorded invocation.

## The contract

``record()`` is a context manager wrapped around the call you want observed:

    with record(SURFACE_MCP, tool_name, arg_keys=args) as inv:
        result = handler(args)

It writes a ``running`` row on entry and closes it on exit with duration and
status. An exception propagates unchanged after being recorded, so wrapping a
call never changes its behaviour.

## It must never break what it observes

This sits on the hot path of every MCP tool call and every role step. Three
rules follow, and all three are enforced rather than documented:

  1. **Never raise.** Every DB touch is wrapped. A telemetry failure that broke
     a tool call would be strictly worse than no telemetry.
  2. **Never block for long.** Two small INSERT/UPDATE statements, no joins.
     Set ``ICDEV_OBS_INVOCATIONS=0`` to disable entirely.
  3. **Never store argument VALUES.** ``arg_keys`` takes a dict and keeps its
     KEY NAMES only. MCP tool arguments can carry CUI and 512 tools have 512
     argument shapes; trusting a redactor to be right about all of them is a
     fail-open bet, whereas keys cannot leak a value at all. Mirrors what
     ``.claude/hooks/post_tool_use.py`` does with ``tool_input_keys``.

## Degrades before the migration runs

If ``runtime_invocations`` does not exist yet, the first failed INSERT sets a
process-level flag and every later call short-circuits — so an un-migrated
database costs one failed statement per process, not one per invocation.
"""
from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, Mapping, Optional, Sequence

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

#: Surfaces. Kept in sync with the CHECK-free `surface` column in migration 341;
#: this tuple is the single source of truth for what a valid surface is.
SURFACE_MCP = "mcp"
SURFACE_AGENT = "agent"
SURFACE_PERSONA = "persona"
SURFACE_ROLE = "role"
SURFACES = (SURFACE_MCP, SURFACE_AGENT, SURFACE_PERSONA, SURFACE_ROLE)

STATUS_RUNNING = "running"
STATUS_OK = "ok"
STATUS_ERROR = "error"

_MAX_ARG_KEYS = 50
_MAX_ERROR_CHARS = 500

#: Set once the table is found to be missing, so an un-migrated DB costs one
#: failed statement per process rather than one per invocation.
_table_missing = False


def enabled() -> bool:
    """False when ICDEV_OBS_INVOCATIONS is explicitly disabled."""
    return os.environ.get("ICDEV_OBS_INVOCATIONS", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def extract_arg_keys(args: Any) -> Optional[str]:
    """KEY NAMES ONLY, as a JSON array. Never values.

    A non-mapping (positional args, a bare string) yields None rather than a
    guess — recording the wrong thing is worse than recording nothing.
    """
    if not isinstance(args, Mapping):
        return None
    try:
        keys = [str(k) for k in list(args.keys())[:_MAX_ARG_KEYS]]
        return json.dumps(keys)
    except Exception:  # noqa: BLE001
        return None


class _Invocation:
    """Handle returned by :func:`record`. Mutable so callers can annotate."""

    __slots__ = ("id", "surface", "name", "status", "error_class",
                 "error_message", "extra")

    def __init__(self, inv_id: str, surface: str, name: str) -> None:
        self.id = inv_id
        self.surface = surface
        self.name = name
        self.status = STATUS_RUNNING
        self.error_class: Optional[str] = None
        self.error_message: Optional[str] = None
        self.extra: Dict[str, Any] = {}


def _open(inv: _Invocation, session_id: str, project_id: str,
          parent_id: Optional[str], arg_keys: Optional[str],
          started: datetime) -> None:
    global _table_missing
    if _table_missing:
        return
    try:
        from tools.db.storage import get_connection

        with get_connection() as conn:
            conn.execute(
                "INSERT INTO runtime_invocations "
                "(id, surface, name, session_id, project_id, parent_id, "
                " started_at, status, arg_keys) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (inv.id, inv.surface, inv.name[:255], session_id, project_id,
                 parent_id, started.isoformat(), STATUS_RUNNING, arg_keys),
            )
    except Exception as exc:  # noqa: BLE001 — telemetry must never break the call
        if "runtime_invocations" in str(exc) and (
            "does not exist" in str(exc) or "no such table" in str(exc)
        ):
            _table_missing = True
            logger.warning(
                "runtime_invocations is absent — invocation telemetry disabled for "
                "this process. Run: python tools/db/migrate.py --up"
            )
        else:
            logger.debug("invocation open failed for %s: %s", inv.name, exc)


def _close(inv: _Invocation, started: datetime) -> None:
    if _table_missing:
        return
    try:
        from tools.db.storage import get_connection

        completed = _now()
        duration_ms = int((completed - started).total_seconds() * 1000)
        with get_connection() as conn:
            conn.execute(
                "UPDATE runtime_invocations SET completed_at = %s, duration_ms = %s, "
                "status = %s, error_class = %s, error_message = %s WHERE id = %s",
                (completed.isoformat(), duration_ms, inv.status, inv.error_class,
                 (inv.error_message or "")[:_MAX_ERROR_CHARS] or None, inv.id),
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("invocation close failed for %s: %s", inv.name, exc)


@contextmanager
def record(
    surface: str,
    name: str,
    *,
    arg_keys: Any = None,
    session_id: str = "",
    project_id: str = "",
    parent_id: Optional[str] = None,
) -> Iterator[_Invocation]:
    """Observe one invocation. Never raises on its own account.

    Args:
        surface: one of :data:`SURFACES`.
        name: tool name / agent id / persona key / role key.
        arg_keys: the argument mapping. Only its KEY NAMES are stored.
        session_id: correlates invocations within one session.
        project_id: correlates invocations to a project.
        parent_id: id of the enclosing invocation, so a role step can be tied
            to the persona run that issued it.

    Yields:
        the handle; set ``.status`` / ``.error_message`` to annotate.
    """
    inv = _Invocation(f"inv-{uuid.uuid4().hex[:16]}", surface, str(name))
    if not enabled():
        yield inv
        return

    started = _now()
    # "Telemetry never breaks the call" is enforced HERE, at the boundary, not
    # by trusting each helper to be individually correct. _open and _close each
    # have their own try/except, but a bug or a future edit in either would
    # otherwise propagate straight into the wrapped call — which is exactly the
    # failure this module exists to avoid, and it would show up on the hot path
    # of every MCP tool call. Belt and braces is the right trade here.
    _safe(_open, inv, session_id or _session_id(), project_id, parent_id,
          _safe_call(extract_arg_keys, arg_keys), started)
    try:
        yield inv
    except BaseException as exc:  # noqa: BLE001 — record, then re-raise unchanged
        inv.status = STATUS_ERROR
        inv.error_class = type(exc).__name__
        inv.error_message = str(exc)
        _safe(_close, inv, started)
        raise
    else:
        if inv.status == STATUS_RUNNING:
            inv.status = STATUS_OK
        _safe(_close, inv, started)


def _safe(fn, *args) -> None:
    """Run a telemetry helper, swallowing anything it raises."""
    try:
        fn(*args)
    except Exception as exc:  # noqa: BLE001
        logger.debug("invocation telemetry helper %s failed: %s",
                     getattr(fn, "__name__", fn), exc)


def _safe_call(fn, *args):
    """Same, but for a helper whose return value is used."""
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001
        return None


def _session_id() -> str:
    """Best-effort session correlation from the ambient environment."""
    return (
        os.environ.get("ICDEV_SESSION_ID")
        or os.environ.get("CLAUDE_SESSION_ID")
        or os.environ.get("ICDEV_DISPATCH_TASK_ID")
        or ""
    )


def summary(surface: Optional[str] = None, limit: int = 20) -> Sequence[Dict[str, Any]]:
    """Per-name rollup: calls, errors, median-ish duration. Read-only.

    Backs ``icdev audit tail``-adjacent reporting and the coverage check; kept
    here so callers do not hand-roll SQL against the telemetry table.
    """
    try:
        from tools.db.storage import get_connection

        where, params = "", []
        if surface:
            where = "WHERE surface = %s "
            params.append(surface)
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT surface, name, count(*) AS calls, "
                "       sum(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors, "
                "       avg(duration_ms) AS avg_ms, max(duration_ms) AS max_ms "
                "FROM runtime_invocations " + where +
                "GROUP BY surface, name ORDER BY calls DESC LIMIT %s",
                tuple(params + [int(limit)]),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("invocation summary failed: %s", exc)
        return []
