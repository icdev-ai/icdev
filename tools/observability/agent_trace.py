# CUI // SP-CTI
"""Make one SAG run joinable — the correlation id, the turn span, the reader.

``tools/agent_runtime/`` contained zero references to spans or trace ids. The
agent loop already minted a per-run correlation id (``AgentLoopResult.trace_id``)
and the router already emitted ``gen_ai.invoke`` spans, but nothing tied the two
together, so a run's LLM calls, its tool calls and its turns were three
unrelated piles of rows.

This module is the seam, and it is deliberately small:

``current_correlation_id`` / ``correlation_scope``
    A contextvar carrying the id of the enclosing agent run. The loop opens the
    scope once; :mod:`tools.observability.invocation_recorder` reads it, so a
    tool call recorded from ``dispatch.py`` lands with the right run id without
    ``dispatch`` needing to be told what run it is in.

``submit_with_context``
    ``ThreadPoolExecutor.submit`` starts the callable in a **fresh, empty**
    context, so a contextvar set on the calling thread is invisible inside the
    worker. The agent loop runs its LLM call and its read-only tools through an
    executor, which is precisely where the correlation would be lost. This
    helper copies the caller's context in — and carries the active span with it,
    so a span opened inside a worker still nests under the turn.

``TurnTracer``
    A span per turn, with an EXPLICIT lifecycle rather than a ``with`` block.
    The loop's turn body has fourteen ``break`` statements spread over four
    hundred lines; wrapping it would mean re-indenting all of them. Instead
    ``begin()`` closes the previous turn's span before opening the next, and one
    ``finish()`` after the loop closes the last — so every exit path, including
    every ``break``, is covered by three call sites.

``spans_for_correlation``
    The join, as a function. Every span this module opens carries
    ``icdev.correlation_id``, and so does every ``gen_ai.invoke`` span the router
    emits for a request that carries one — an ATTRIBUTE rather than a shared
    ``trace_id``, because the router's span is frequently created on a worker
    thread whose trace context is its own. The attribute survives that; a
    parent-child trace_id would not.

Nothing here raises. Observability that can break the thing it observes is worse
than no observability, which is the same rule ``invocation_recorder`` follows.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import re
import uuid
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

#: Span attribute holding the run-level correlation id. One name, used by the
#: writer here, by the router's gen_ai.invoke span, and by the reader below.
CORRELATION_ATTR = "icdev.correlation_id"
SESSION_ATTR = "icdev.session_id"

#: Span name for a single agent-loop turn.
TURN_SPAN = "agent.turn"

_HEX32 = re.compile(r"^[0-9a-f]{32}$")

_correlation_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "icdev_agent_correlation_id", default=""
)


# ---------------------------------------------------------------------------
# Correlation propagation
# ---------------------------------------------------------------------------
def current_correlation_id() -> str:
    """The correlation id of the enclosing agent run, or "" outside one."""
    try:
        return _correlation_var.get() or ""
    except LookupError:  # pragma: no cover — default makes this unreachable
        return ""


@contextmanager
def correlation_scope(correlation_id: str) -> Iterator[str]:
    """Bind *correlation_id* for the duration of the block. Never raises.

    An empty id is honoured as "no run" rather than rejected, so a caller can
    pass a possibly-absent id straight through.
    """
    token = _correlation_var.set(correlation_id or "")
    try:
        yield correlation_id or ""
    finally:
        try:
            _correlation_var.reset(token)
        except (ValueError, LookupError):  # token from another context
            _correlation_var.set("")


def submit_with_context(executor: Any, fn: Any, *args: Any) -> Any:
    """``executor.submit(fn, *args)`` with the caller's context copied in.

    A ThreadPoolExecutor worker starts with an empty context: every contextvar
    reads its default, so the correlation id and the active span are both gone
    inside the worker. Copying the context restores both, which is what lets a
    tool executed in parallel record against the run that issued it.

    Falls back to a plain ``submit`` if context copying is unavailable, because
    losing a correlation id is an acceptable degradation and failing to run the
    tool is not.
    """
    try:
        ctx = contextvars.copy_context()
        return executor.submit(ctx.run, fn, *args)
    except Exception as exc:  # noqa: BLE001
        logger.debug("agent_trace: context propagation unavailable: %s", exc)
        return executor.submit(fn, *args)


def trace_id_for(correlation_id: str) -> str:
    """A 32-hex trace id derived deterministically from *correlation_id*.

    A uuid4 correlation id is 32 hex digits once its dashes are removed, so the
    common case is a straight reshaping and the two ids read as the same value.
    Anything else is hashed, so an operator-supplied id (a task id, a ticket
    number) is still a legal trace id rather than a malformed one.
    """
    candidate = (correlation_id or "").replace("-", "").strip().lower()
    if _HEX32.match(candidate):
        return candidate
    if not correlation_id:
        return uuid.uuid4().hex
    return hashlib.sha256(correlation_id.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Turn spans
# ---------------------------------------------------------------------------
class TurnTracer:
    """One span per agent-loop turn, with an explicit begin/finish lifecycle.

    Usage in the loop::

        tracer = TurnTracer(correlation_id, session_id=session_id)
        for turn in range(max_iterations):
            tracer.begin(turn)          # closes turn N-1, opens turn N
            ...
            tracer.annotate(model=...)  # optional, any time during the turn
        tracer.finish()                 # closes whichever turn was open

    Every method swallows its own errors. A tracer backend that is misconfigured,
    a database that is unreachable, an attribute that will not serialise — none
    of them may interrupt the agent run.
    """

    __slots__ = ("_correlation_id", "_session_id", "_trace_id", "_root",
                 "_span", "_turn", "_extra")

    def __init__(self, correlation_id: str, *, session_id: str = "") -> None:
        self._correlation_id = correlation_id or ""
        self._session_id = session_id or ""
        self._trace_id = trace_id_for(correlation_id)
        self._root = self._make_root()
        self._span: Any = None
        self._turn: int = -1
        self._extra: Dict[str, Any] = {}

    @property
    def trace_id(self) -> str:
        """The 32-hex trace id every turn span in this run shares."""
        return self._trace_id

    def _make_root(self) -> Any:
        """A parent stand-in carrying this run's trace id.

        ``Tracer.start_span`` has no ``trace_id`` argument — it derives one from
        the parent span. A ``NullSpan`` is a concrete ``Span`` that holds an
        arbitrary trace id and does nothing else, which is exactly the shape
        needed: the SQLite backend reads ``parent.trace_id``/``parent.span_id``
        off it, and the OTel backend ignores ``parent`` entirely and manages its
        own context. Neither can be broken by it.
        """
        try:
            from tools.observability.tracer import NullSpan

            return NullSpan(trace_id=self._trace_id, span_id=uuid.uuid4().hex[:16])
        except Exception as exc:  # noqa: BLE001
            logger.debug("agent_trace: root span unavailable: %s", exc)
            return None

    def begin(self, turn: int) -> None:
        """Open the span for *turn*, closing the previous turn's span first."""
        self.finish()
        self._turn = turn
        self._extra = {}
        try:
            from tools.observability import get_tracer

            attributes = {
                CORRELATION_ATTR: self._correlation_id,
                "agent.turn": turn,
            }
            if self._session_id:
                attributes[SESSION_ATTR] = self._session_id
            self._span = get_tracer().start_span(
                TURN_SPAN, parent=self._root, kind="INTERNAL", attributes=attributes
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("agent_trace: turn span %d not started: %s", turn, exc)
            self._span = None

    def annotate(self, **attributes: Any) -> None:
        """Set attributes on the open turn span. No-op when none is open."""
        if self._span is None:
            return
        for key, value in attributes.items():
            if value is None or value == "":
                continue
            try:
                self._span.set_attribute(key, value)
            except Exception as exc:  # noqa: BLE001
                logger.debug("agent_trace: attribute %s not set: %s", key, exc)

    def error(self, message: str) -> None:
        """Mark the open turn span as failed."""
        if self._span is None:
            return
        try:
            self._span.set_status("ERROR", str(message)[:500])
        except Exception as exc:  # noqa: BLE001
            logger.debug("agent_trace: turn status not set: %s", exc)

    def finish(self) -> None:
        """Close whichever turn span is open. Idempotent and safe when none is."""
        span, self._span = self._span, None
        if span is None:
            return
        try:
            if getattr(span, "_raw_status_code", lambda: "OK")() == "UNSET":
                span.set_status("OK")
        except Exception:  # noqa: BLE001
            pass
        try:
            span.end()
        except Exception as exc:  # noqa: BLE001
            logger.debug("agent_trace: turn span not ended: %s", exc)


# ---------------------------------------------------------------------------
# The reader — spans that belong to one run
# ---------------------------------------------------------------------------
def spans_for_correlation(correlation_id: str, limit: int = 500,
                          conn: Any = None) -> List[Dict[str, Any]]:
    """Every stored span carrying *correlation_id*, oldest first.

    This is the join that makes a run readable: the turn spans this module opens
    and the ``gen_ai.invoke`` spans the router opens beneath them both carry the
    attribute, so one query returns the whole run.

    The filter is a ``LIKE`` over the ``attributes`` JSON text and then an exact
    check in Python. It is deliberately NOT ``json_extract``: that is SQLite
    dialect, and this table is read on PostgreSQL first (CLAUDE.md — compute in
    Python rather than lean on ``translate_sql``). ``LIKE`` narrows the scan on
    both backends and the Python pass makes the match exact.

    Reads through the ambient ``get_connection()``, which is the right target
    under the PG-primary runtime. Note that ``SQLiteTracer`` WRITES to its
    ``db_path`` constructor argument, which defaults to ``<repo>/data/icdev.db``
    and does not consult ``ICDEV_DB_PATH`` — so on a SQLite deployment that has
    relocated its database, the tracer must be constructed with a matching
    ``db_path`` or this reader will look somewhere the spans were never written.
    Under PostgreSQL that path is ignored and the two always agree.

    Returns [] on any failure, including a table that does not exist yet.
    """
    if not correlation_id:
        return []
    own_conn = conn is None
    try:
        if own_conn:
            from tools.db.storage import get_connection

            conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT id, trace_id, parent_span_id, name, kind, start_time, "
                "       end_time, duration_ms, status_code, status_message, attributes "
                "FROM otel_spans WHERE attributes LIKE %s "
                "ORDER BY start_time ASC LIMIT %s",
                (f"%{correlation_id}%", int(limit)),
            ).fetchall()
        finally:
            if own_conn:
                conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent_trace: span lookup failed: %s", exc)
        return []

    out: List[Dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        attributes = record.get("attributes")
        try:
            parsed = json.loads(attributes) if isinstance(attributes, str) else (attributes or {})
        except (TypeError, ValueError):
            parsed = {}
        # The LIKE can match the id anywhere in the JSON blob; only a span whose
        # correlation attribute IS this id belongs to the run.
        if not isinstance(parsed, dict) or parsed.get(CORRELATION_ATTR) != correlation_id:
            continue
        record["attributes"] = parsed
        out.append(record)
    return out
