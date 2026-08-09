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

## Replay is an opt-in WIDENING of rule 3, never a silent one

Rule 3 makes a run unreplayable by construction, and that is the right default.
An operator who needs replay — reproducing a SAG run's tool sequence, diffing a
regression against the arguments that caused it — turns on ``ICDEV_OBS_REPLAY``
and gets two more columns written: ``arg_values`` and ``result_preview``, both
passed through ``tools.llm.output_redactor.redact`` first and both truncated.

Three properties hold that flag honest, and each is asserted by a test:

  * **Off is the default.** An unset variable, an empty one, and anything that
    is not an affirmative token all mean off.
  * **Off writes nothing, anywhere.** Not a truncated value, not a hash, not a
    length. :func:`extract_arg_values` and :func:`capture_result` return None
    without touching their input, and the INSERT/UPDATE omits the columns.
  * **On is still redacted.** The flag widens what is stored; it does not
    disable the redactor. Turning it on cannot be a way to get raw secrets into
    the telemetry table.

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
#: Replay payload ceilings. A tool result can be 200 KB (dispatch's own cap);
#: storing that per invocation would turn a telemetry table into a blob store.
_MAX_ARG_VALUE_CHARS = 4000
_MAX_RESULT_CHARS = 4000

#: Only an affirmative token turns replay on. Anything else — unset, empty,
#: "0", "maybe" — is off, so a typo fails closed rather than starting to
#: persist argument values.
_AFFIRMATIVE = frozenset({"1", "true", "yes", "on"})

#: Set once the table is found to be missing, so an un-migrated DB costs one
#: failed statement per process rather than one per invocation.
_table_missing = False

#: Optional columns (the 20260808161052 migration) discovered once per process.
#: None means "not probed yet". A database that has not run the migration keeps
#: writing the 341 column set rather than failing every INSERT.
_optional_columns: Optional[frozenset] = None
_OPTIONAL_COLUMNS = ("correlation_id", "arg_values", "result_preview")


def enabled() -> bool:
    """False when ICDEV_OBS_INVOCATIONS is explicitly disabled."""
    return os.environ.get("ICDEV_OBS_INVOCATIONS", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def replay_enabled() -> bool:
    """True only when ICDEV_OBS_REPLAY is explicitly affirmative.

    Off by default and on every malformed value: this gates whether argument
    VALUES and tool RESULTS are persisted at all, so the failure direction has
    to be "store less".
    """
    return os.environ.get("ICDEV_OBS_REPLAY", "").strip().lower() in _AFFIRMATIVE


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


def _redact(text: str) -> str:
    """Run the shared output redactor over *text*. Falls back to dropping it.

    If the redactor cannot be imported or fails, the safe answer is the empty
    string, not the raw text — this is only ever called on the replay path,
    where "store nothing" is the behaviour the flag-off default already has.
    """
    try:
        from tools.llm.output_redactor import redact as _redact_fn

        return _redact_fn(text, mode="redact").redacted_text
    except Exception as exc:  # noqa: BLE001
        logger.debug("invocation replay: redaction unavailable, dropping: %s", exc)
        return ""


def extract_arg_values(args: Any) -> Optional[str]:
    """Redacted argument VALUES as JSON — only when replay is on.

    Returns None when :func:`replay_enabled` is False, which is the default.
    That None is what makes the flag-off guarantee structural: there is no
    truncated form, no hash and no length to leak, because nothing is produced.
    """
    if not replay_enabled():
        return None
    try:
        raw = json.dumps(args, default=str, sort_keys=True)
    except Exception:  # noqa: BLE001
        try:
            raw = str(args)
        except Exception:  # noqa: BLE001
            return None
    cleaned = _redact(raw)
    return cleaned[:_MAX_ARG_VALUE_CHARS] or None


def capture_result(result: Any) -> Optional[str]:
    """Redacted, truncated tool RESULT — only when replay is on. Else None."""
    if not replay_enabled():
        return None
    try:
        text = result if isinstance(result, str) else json.dumps(result, default=str)
    except Exception:  # noqa: BLE001
        try:
            text = str(result)
        except Exception:  # noqa: BLE001
            return None
    cleaned = _redact(text)
    return cleaned[:_MAX_RESULT_CHARS] or None


class _Invocation:
    """Handle returned by :func:`record`. Mutable so callers can annotate."""

    __slots__ = ("id", "surface", "name", "status", "error_class",
                 "error_message", "extra", "result_preview")

    def __init__(self, inv_id: str, surface: str, name: str) -> None:
        self.id = inv_id
        self.surface = surface
        self.name = name
        self.status = STATUS_RUNNING
        self.error_class: Optional[str] = None
        self.error_message: Optional[str] = None
        self.extra: Dict[str, Any] = {}
        #: Set by a caller via :meth:`record_result`; written on close, and only
        #: ever non-None when replay is enabled.
        self.result_preview: Optional[str] = None

    def record_result(self, result: Any) -> None:
        """Attach a tool result for persistence — a no-op unless replay is on."""
        self.result_preview = _safe_call(capture_result, result)


def _available_optional_columns(conn: Any) -> frozenset:
    """Which of the migration-added columns this database actually has.

    Probed once per process. A checkout whose database predates the
    20260808161052 migration keeps writing the original 341 column set instead
    of failing every INSERT on an unknown column — the same degrade-don't-break
    stance as the missing-table latch above.
    """
    global _optional_columns
    if _optional_columns is not None:
        return _optional_columns
    present = set()
    try:
        from tools.db.storage import column_exists

        for column in _OPTIONAL_COLUMNS:
            if column_exists(conn, "runtime_invocations", column):
                present.add(column)
    except Exception as exc:  # noqa: BLE001
        logger.debug("invocation column probe failed: %s", exc)
    _optional_columns = frozenset(present)
    if len(_optional_columns) < len(_OPTIONAL_COLUMNS):
        logger.debug(
            "runtime_invocations is missing %s — run python tools/db/migrate.py --up",
            sorted(set(_OPTIONAL_COLUMNS) - _optional_columns),
        )
    return _optional_columns


def _open(inv: _Invocation, session_id: str, project_id: str,
          parent_id: Optional[str], arg_keys: Optional[str],
          started: datetime, correlation_id: str = "",
          arg_values: Optional[str] = None) -> None:
    global _table_missing
    if _table_missing:
        return
    try:
        from tools.db.storage import get_connection

        with get_connection() as conn:
            columns = ["id", "surface", "name", "session_id", "project_id",
                       "parent_id", "started_at", "status", "arg_keys"]
            values: list = [inv.id, inv.surface, inv.name[:255], session_id,
                            project_id, parent_id, started.isoformat(),
                            STATUS_RUNNING, arg_keys]
            optional = _available_optional_columns(conn)
            if "correlation_id" in optional:
                columns.append("correlation_id")
                values.append(correlation_id or None)
            # arg_values is appended ONLY when the caller produced one, which
            # extract_arg_values only does with the replay flag on.
            if arg_values is not None and "arg_values" in optional:
                columns.append("arg_values")
                values.append(arg_values)
            placeholders = ", ".join(["%s"] * len(columns))
            conn.execute(
                f"INSERT INTO runtime_invocations ({', '.join(columns)}) "  # nosec B608 — column names are module constants, never caller input
                f"VALUES ({placeholders})",
                tuple(values),
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
            sets = ["completed_at = %s", "duration_ms = %s", "status = %s",
                    "error_class = %s", "error_message = %s"]
            values: list = [completed.isoformat(), duration_ms, inv.status,
                            inv.error_class,
                            (inv.error_message or "")[:_MAX_ERROR_CHARS] or None]
            if (
                inv.result_preview is not None
                and "result_preview" in _available_optional_columns(conn)
            ):
                sets.append("result_preview = %s")
                values.append(inv.result_preview)
            values.append(inv.id)
            conn.execute(
                f"UPDATE runtime_invocations SET {', '.join(sets)} WHERE id = %s",  # nosec B608 — column names are module constants, never caller input
                tuple(values),
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
    correlation_id: str = "",
) -> Iterator[_Invocation]:
    """Observe one invocation. Never raises on its own account.

    Args:
        surface: one of :data:`SURFACES`.
        name: tool name / agent id / persona key / role key.
        arg_keys: the argument mapping. Only its KEY NAMES are stored — unless
            replay is explicitly enabled, in which case the redacted VALUES are
            stored alongside them. See the module docstring.
        session_id: correlates invocations within one session.
        project_id: correlates invocations to a project.
        parent_id: id of the enclosing invocation, so a role step can be tied
            to the persona run that issued it.
        correlation_id: the run-level id shared with the trace spans, so one
            agent run's tool calls and its spans answer to the same key.
            Defaults to the ambient one from :func:`agent_trace.current_correlation_id`.

    Yields:
        the handle; set ``.status`` / ``.error_message`` to annotate, or call
        ``.record_result(out)`` to attach a result for the replay path.
    """
    inv = _Invocation(f"inv-{uuid.uuid4().hex[:16]}", surface, str(name))
    if not enabled():
        yield inv
        return

    correlation_id = correlation_id or _safe_call(_ambient_correlation_id) or ""
    started = _now()
    # "Telemetry never breaks the call" is enforced HERE, at the boundary, not
    # by trusting each helper to be individually correct. _open and _close each
    # have their own try/except, but a bug or a future edit in either would
    # otherwise propagate straight into the wrapped call — which is exactly the
    # failure this module exists to avoid, and it would show up on the hot path
    # of every MCP tool call. Belt and braces is the right trade here.
    _safe(_open, inv, session_id or _session_id(), project_id, parent_id,
          _safe_call(extract_arg_keys, arg_keys), started, correlation_id,
          _safe_call(extract_arg_values, arg_keys))
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


def _ambient_correlation_id() -> str:
    """The correlation id of the enclosing agent run, if there is one.

    Imported lazily: ``agent_trace`` imports nothing from this module, but this
    module sits on the hot path of every MCP tool call and should not pay for an
    import that most callers never need.
    """
    from tools.observability.agent_trace import current_correlation_id

    return current_correlation_id()


def summary(surface: Optional[str] = None, limit: int = 20,
            conn: Any = None) -> Sequence[Dict[str, Any]]:
    """Per-name rollup: calls, errors, median-ish duration. Read-only.

    Backs ``icdev audit tail``-adjacent reporting and the coverage check; kept
    here so callers do not hand-roll SQL against the telemetry table.

    Args:
        surface: restrict to one surface, or None for all of them.
        limit: number of NAMES returned, busiest first.
        conn: an open connection to read through. Callers that need the query
            issued WITHOUT a row-security context — the dashboard panel, which
            runs inside a Flask request where ``get_connection()`` auto-attaches
            one — pass their own de-scoped connection here. ``runtime_invocations``
            has no ``tenant_id`` column, so an injected ``tenant_id = %s``
            predicate makes this query fail; because the failure is swallowed
            below, that would render as "no invocations" rather than as an
            error. The caller owns the connection it passes and closes it; the
            default path still opens and closes its own.
    """
    own_conn = conn is None
    try:
        if own_conn:
            from tools.db.storage import get_connection

            conn = get_connection()

        where, params = "", []
        if surface:
            where = "WHERE surface = %s "
            params.append(surface)
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
            if own_conn:
                conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("invocation summary failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Long-lived invocations (obs-cov-01 correction)
# ---------------------------------------------------------------------------
# `record()` is a context manager, which fits anything that starts and finishes
# inside one call. The kanban runner's primary build path does not: it spawns a
# `claude` subprocess in one scheduler cycle and notices the exit in a LATER one,
# minutes or tens of minutes afterwards. There is no scope to wrap.
#
# That mattered more than it sounds. The agent surface was instrumented at
# `tools/agent/agent_executor.py::execute_agent`, and the kanban runner never
# calls it — it uses `subprocess.Popen(claude ...)` directly. So the single most
# important thing to observe, an actual agent build, produced no telemetry at
# all: `agent` had zero rows for its entire existence while `mcp` recorded fine.
#
# These two functions are that missing shape: open when the process starts, close
# when it is reaped. Same table, same fail-safe rules as `record()` — neither
# raises, and both no-op when telemetry is disabled.


def open_invocation(
    surface: str,
    name: str,
    *,
    arg_keys: Any = None,
    session_id: str = "",
    project_id: str = "",
    parent_id: Optional[str] = None,
    correlation_id: str = "",
) -> Optional["_Invocation"]:
    """Open an invocation that will be closed later, by someone else.

    Returns a handle to pass to :func:`close_invocation`, or None when telemetry
    is disabled — callers should treat None as "nothing to close" rather than an
    error.
    """
    if not enabled():
        return None
    inv = _Invocation(f"inv-{uuid.uuid4().hex[:16]}", surface, str(name))
    correlation_id = correlation_id or _safe_call(_ambient_correlation_id) or ""
    started = _now()
    # The handle has to carry its own start time: the closer runs in a different
    # call, often a different scheduler cycle, and cannot know it. `extra` is an
    # existing slot, so this needs no change to _Invocation itself.
    inv.extra["started"] = started
    _safe(_open, inv, session_id or _session_id(), project_id, parent_id,
          _safe_call(extract_arg_keys, arg_keys), started, correlation_id,
          _safe_call(extract_arg_values, arg_keys))
    return inv


def close_invocation(
    inv: Optional["_Invocation"],
    *,
    status: str = STATUS_OK,
    error_class: Optional[str] = None,
    error_message: Optional[str] = None,
) -> None:
    """Close a handle from :func:`open_invocation`. Safe with None."""
    if inv is None:
        return
    inv.status = status
    inv.error_class = error_class
    inv.error_message = error_message
    started = inv.extra.get("started") or _now()
    _safe(_close, inv, started)
