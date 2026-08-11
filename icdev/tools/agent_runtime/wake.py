# CUI // SP-CTI
"""The wake store — agent-scheduled resumption (agov-wake-01).

ICDEV has a great deal of scheduling and every bit of it is EXTERNAL to the
agent: 124 Genesis reflexes on fixed cadences, ``agent_cron_jobs``
(:mod:`tools.agent_runtime.cron`, migration 289) where an operator declares a
recurring schedule up front, and the kanban scheduler. None of them can express
"the agent decided, mid-turn, to stop here and be resumed when PR #1342 goes
CI-green". This module is that missing primitive: one row per self-suspension,
created by the agent, carrying a condition something else in the platform
satisfies later.

    ``agent_cron_jobs``  operator-declared, recurring, schedule known up front.
    ``agent_wakes``      agent-declared, single-shot, condition not yet met.

**The state machine is one-directional.**

.. code-block:: text

    pending --(condition met)--> due --(mark_fired)--> fired
    pending --(cancel)--> cancelled
    due     --(cancel)--> cancelled

There is no edge back, and nothing here reads a state and then writes one. Every
transition is a **conditional UPDATE naming the state it may move from**, and the
caller learns from ``rowcount`` whether it was the one that moved it::

    UPDATE agent_wakes SET state = 'fired' WHERE wake_id = %s AND state = 'due'

That is not defensive style, it is the requirement. ICDEV runs many sessions
against one database and the tick (agov-wake-03) can overlap with itself, so
read-then-write would let two callers both observe ``due`` and both fire the same
wake — and a wake firing twice means an agent resumed twice from one suspension.
:func:`mark_fired` is therefore both **idempotent** (calling it again is a
harmless ``False``, never an exception) and **exactly-once** (only one concurrent
caller can ever get ``True``).

**Three kinds, three conditions.**

``timer``       fires when ``fire_at`` has passed. Promoted by :func:`due`.
``completion``  fires when :func:`complete_job` is called with its ``job_id``.
``event``       fires when :func:`fire_event` is called with its ``event_key``.

Promotion to ``due`` is what evaluates the condition; :func:`due` never returns a
wake whose condition is unmet, because an unmet wake is still ``pending``.

**Failure posture is asymmetric, on purpose.**

Writes (:func:`add_timer`, :func:`add_completion`, :func:`add_event`) raise
:class:`WakeStoreUnavailable` rather than swallow. A dropped wake is an agent
that suspends and is never resumed — invisible, and indistinguishable from an
agent that is merely slow. Reads (:func:`due`, :func:`pending`, :func:`get`)
degrade to empty and log, because the caller is the Genesis reflex tick and a
raise there wedges the daemon for every other reflex.

The table is MUTABLE and deliberately **not** in ``APPEND_ONLY_TABLES``: making
the pending -> due -> fired transitions hook violations would break the only
thing the table exists for. The permanent record of what an agent did after
waking is the existing append-only trail, which this does not duplicate.

Library — no CLI. Import it::

    from tools.agent_runtime.wake import add_timer, due, mark_fired
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.agent_runtime.wake")

TABLE = "agent_wakes"

# --- vocabulary -------------------------------------------------------------
# The single source. Migration 20260809221051 deliberately carries no CHECK
# constraint, so these are not a second copy of anything and cannot drift.
KIND_TIMER = "timer"
KIND_COMPLETION = "completion"
KIND_EVENT = "event"
KINDS = (KIND_TIMER, KIND_COMPLETION, KIND_EVENT)

STATE_PENDING = "pending"
STATE_DUE = "due"
STATE_FIRED = "fired"
STATE_CANCELLED = "cancelled"
STATES = (STATE_PENDING, STATE_DUE, STATE_FIRED, STATE_CANCELLED)

#: States a wake can still leave. Anything else is spent.
LIVE_STATES = (STATE_PENDING, STATE_DUE)

COLUMNS = (
    "wake_id",
    "session_id",
    "kind",
    "state",
    "fire_at",
    "job_id",
    "event_key",
    "note",
    "tenant_id",
    "classification",
    "created_at",
    "updated_at",
)

_DEFAULT_CLASSIFICATION = "CUI"
_MAX_NOTE = 2000


class WakeStoreUnavailable(RuntimeError):
    """A wake could not be persisted.

    Raised only from the write path. The caller is an agent about to suspend
    itself, and the correct behaviour when the suspension cannot be recorded is
    to fail loudly — an agent that suspends against a store that did not accept
    the row is an agent that never comes back.
    """


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    """UTC ISO-8601 at a FIXED microsecond width.

    Fixed width is load-bearing, not cosmetic: ``fire_at`` is compared as TEXT by
    the timer sweep on both backends, so every stored timestamp has to be the
    same shape for lexicographic order to equal chronological order.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _coerce_when(value: Any) -> datetime:
    """Accept a datetime or an ISO-8601 string; naive input is treated as UTC."""
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"fire_at is not a datetime or ISO-8601 string: {value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Record
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Wake:
    """One self-suspension. Immutable — re-read to observe a transition."""

    wake_id: str
    session_id: str
    kind: str
    state: str
    fire_at: Optional[str] = None
    job_id: Optional[str] = None
    event_key: Optional[str] = None
    note: str = ""
    tenant_id: str = ""
    classification: str = _DEFAULT_CLASSIFICATION
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @property
    def is_pending(self) -> bool:
        return self.state == STATE_PENDING

    @property
    def is_due(self) -> bool:
        return self.state == STATE_DUE

    @property
    def is_spent(self) -> bool:
        """Fired or cancelled — no transition can move it again."""
        return self.state in (STATE_FIRED, STATE_CANCELLED)

    @property
    def condition(self) -> Optional[str]:
        """The one populated condition column for this kind."""
        if self.kind == KIND_TIMER:
            return self.fire_at
        if self.kind == KIND_COMPLETION:
            return self.job_id
        if self.kind == KIND_EVENT:
            return self.event_key
        return None

    def to_dict(self) -> dict[str, Any]:
        return {col: getattr(self, col) for col in COLUMNS}


def _row_to_wake(row: Iterable[Any]) -> Wake:
    values = list(row)
    return Wake(**{col: values[i] for i, col in enumerate(COLUMNS)})


# ---------------------------------------------------------------------------
# Connection + schema
# ---------------------------------------------------------------------------
_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    wake_id        TEXT PRIMARY KEY,
    session_id     TEXT NOT NULL,
    kind           TEXT NOT NULL,
    state          TEXT NOT NULL,
    fire_at        TEXT,
    job_id         TEXT,
    event_key      TEXT,
    note           TEXT,
    tenant_id      TEXT DEFAULT '',
    classification TEXT DEFAULT 'CUI',
    created_at     TEXT,
    updated_at     TEXT
)
"""


def _connect(conn=None):
    if conn is not None:
        return conn
    # Imported here, not at module scope: the tests patch get_connection on the
    # `tools.db.storage` shim, and a module-level `from ... import` would bind
    # the canonical icdev.tools.db.storage object instead — a different object,
    # so the patch would silently apply to nothing.
    from tools.db.storage import get_connection

    return get_connection()


def _ensure_schema(conn) -> None:
    """Best-effort self-creation, mirroring cron.py.

    A checkout that has not run migration 20260809221051 still works. This
    deliberately does not raise: on the read path a missing table degrades to
    empty, and on the write path the INSERT that follows raises with a far more
    useful message than a DDL failure would carry.
    """
    try:
        conn.execute(_DDL)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("wake: ensure_schema failed: %s", exc)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
def _insert(
    *,
    session_id: str,
    kind: str,
    fire_at: Optional[str],
    job_id: Optional[str],
    event_key: Optional[str],
    note: str,
    tenant_id: str,
    classification: str,
    conn,
) -> Wake:
    if kind not in KINDS:
        raise ValueError(f"unknown wake kind {kind!r}; expected one of {KINDS}")
    if not str(session_id or "").strip():
        raise ValueError("session_id is required — a wake nobody owns can never be delivered")
    # A wake with no condition would sit pending forever, never be promoted, and
    # never surface as a bug. Refuse it here rather than persist it.
    if not (fire_at or job_id or event_key):
        raise ValueError(f"a {kind} wake needs its condition; none was supplied")

    now = _iso(_utcnow())
    wake = Wake(
        wake_id="wake-" + uuid.uuid4().hex[:12],
        session_id=str(session_id),
        kind=kind,
        state=STATE_PENDING,
        fire_at=fire_at,
        job_id=job_id,
        event_key=event_key,
        note=(note or "")[:_MAX_NOTE],
        tenant_id=tenant_id or "",
        classification=classification or _DEFAULT_CLASSIFICATION,
        created_at=now,
        updated_at=now,
    )

    c = _connect(conn)
    _ensure_schema(c)
    placeholders = ", ".join(["%s"] * len(COLUMNS))
    try:
        c.execute(
            f"INSERT INTO {TABLE} ({', '.join(COLUMNS)}) VALUES ({placeholders})",
            tuple(getattr(wake, col) for col in COLUMNS),
        )
        c.commit()
    except Exception as exc:  # noqa: BLE001
        raise WakeStoreUnavailable(
            f"could not persist {kind} wake for session {session_id!r}: {exc}"
        ) from exc
    return wake


def add_timer(
    session_id: str,
    fire_at: Any,
    *,
    note: str = "",
    tenant_id: str = "",
    classification: str = _DEFAULT_CLASSIFICATION,
    conn=None,
) -> Wake:
    """Suspend until a wall-clock time. ``fire_at`` is a datetime or ISO string.

    Naive input is read as UTC. Backs ``sleep_until`` — and ``sleep_for``, which
    is this with ``now + delta`` (see :func:`add_timer_in`).
    """
    return _insert(
        session_id=session_id,
        kind=KIND_TIMER,
        fire_at=_iso(_coerce_when(fire_at)),
        job_id=None,
        event_key=None,
        note=note,
        tenant_id=tenant_id,
        classification=classification,
        conn=conn,
    )


def add_timer_in(
    session_id: str,
    seconds: float,
    *,
    note: str = "",
    tenant_id: str = "",
    classification: str = _DEFAULT_CLASSIFICATION,
    conn=None,
) -> Wake:
    """Suspend for a relative duration — ``add_timer`` with ``now + seconds``.

    Separate from :func:`add_timer` only so the relative case computes its
    absolute deadline in ONE place. A negative or zero duration is legal and
    means "due on the next sweep".
    """
    return add_timer(
        session_id,
        _utcnow() + timedelta(seconds=float(seconds)),
        note=note,
        tenant_id=tenant_id,
        classification=classification,
        conn=conn,
    )


def add_completion(
    session_id: str,
    job_id: str,
    *,
    note: str = "",
    tenant_id: str = "",
    classification: str = _DEFAULT_CLASSIFICATION,
    conn=None,
) -> Wake:
    """Suspend until :func:`complete_job` is called with this ``job_id``."""
    if not str(job_id or "").strip():
        raise ValueError("job_id is required for a completion wake")
    return _insert(
        session_id=session_id,
        kind=KIND_COMPLETION,
        fire_at=None,
        job_id=str(job_id),
        event_key=None,
        note=note,
        tenant_id=tenant_id,
        classification=classification,
        conn=conn,
    )


def add_event(
    session_id: str,
    event_key: str,
    *,
    note: str = "",
    tenant_id: str = "",
    classification: str = _DEFAULT_CLASSIFICATION,
    conn=None,
) -> Wake:
    """Suspend until :func:`fire_event` is called with this ``event_key``.

    The key is an opaque string by design — ``pr:1342:ci_green`` is a convention
    for the emitters (agov-wake-03 wires ``pr_watcher`` and the kanban pipeline),
    not a schema this store parses.
    """
    if not str(event_key or "").strip():
        raise ValueError("event_key is required for an event wake")
    return _insert(
        session_id=session_id,
        kind=KIND_EVENT,
        fire_at=None,
        job_id=None,
        event_key=str(event_key),
        note=note,
        tenant_id=tenant_id,
        classification=classification,
        conn=conn,
    )


# ---------------------------------------------------------------------------
# Transitions — every one is a conditional UPDATE
# ---------------------------------------------------------------------------
def _transition(c, wake_id: str, *, to_state: str, from_states: Iterable[str]) -> bool:
    """Move one wake, only if it is currently in one of ``from_states``.

    The single place a state is written. Returns whether THIS call performed the
    transition, which is how concurrent callers tell winner from loser without a
    lock: the database decides, and the loser's UPDATE matches zero rows.
    """
    froms = tuple(from_states)
    slots = ", ".join(["%s"] * len(froms))
    cur = c.execute(
        f"UPDATE {TABLE} SET state = %s, updated_at = %s "
        f"WHERE wake_id = %s AND state IN ({slots})",
        (to_state, _iso(_utcnow()), wake_id) + froms,
    )
    c.commit()
    return (cur.rowcount or 0) == 1


def _promote(c, wake_id: str) -> bool:
    """pending -> due. False when someone else already promoted it."""
    return _transition(c, wake_id, to_state=STATE_DUE, from_states=(STATE_PENDING,))


def _select(c, where: str, params: tuple) -> list[Wake]:
    cur = c.execute(
        f"SELECT {', '.join(COLUMNS)} FROM {TABLE} WHERE {where} ORDER BY created_at, wake_id",
        params,
    )
    return [_row_to_wake(r) for r in cur.fetchall()]


def mark_fired(wake_id: str, *, conn=None) -> bool:
    """``due`` -> ``fired``. Returns whether THIS call fired it.

    Idempotent and exactly-once. Calling it on an already-fired, still-pending or
    cancelled wake returns ``False`` and changes nothing — so a retried tick is
    harmless, while two overlapping ticks cannot both resume the same agent.

    Note that a ``pending`` wake cannot be fired directly: promotion is what
    evaluates the condition, so firing from ``pending`` would be firing a wake
    whose condition was never checked.
    """
    c = _connect(conn)
    _ensure_schema(c)
    return _transition(c, wake_id, to_state=STATE_FIRED, from_states=(STATE_DUE,))


def cancel(wake_id: str, *, conn=None) -> bool:
    """``pending``/``due`` -> ``cancelled``. Returns whether THIS call cancelled it.

    A spent wake cannot be cancelled — cancelling something that already fired
    would rewrite history, and there is nothing left to prevent.
    """
    c = _connect(conn)
    _ensure_schema(c)
    return _transition(c, wake_id, to_state=STATE_CANCELLED, from_states=LIVE_STATES)


def complete_job(job_id: str, *, conn=None) -> list[str]:
    """Signal a job finished; promote every pending wake awaiting it.

    Returns the ids THIS call promoted. Selecting the candidates and then
    promoting each conditionally — rather than one blanket UPDATE — is what makes
    the return value honest under concurrency: a wake another caller promoted
    first is simply absent from this list.
    """
    return _signal(
        column="job_id", value=job_id, kind=KIND_COMPLETION, conn=conn
    )


def fire_event(event_key: str, *, conn=None) -> list[str]:
    """Fire an event key; promote every pending wake awaiting it.

    Returns the ids THIS call promoted. Fire-and-forget: an event with no
    listeners is not an error, it is the normal case.
    """
    return _signal(
        column="event_key", value=event_key, kind=KIND_EVENT, conn=conn
    )


def _signal(*, column: str, value: str, kind: str, conn) -> list[str]:
    if not str(value or "").strip():
        raise ValueError(f"{column} is required")
    c = _connect(conn)
    _ensure_schema(c)
    try:
        candidates = _select(
            c,
            f"state = %s AND kind = %s AND {column} = %s",
            (STATE_PENDING, kind, str(value)),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("wake: could not scan %s wakes for %r: %s", kind, value, exc)
        return []
    return [w.wake_id for w in candidates if _promote(c, w.wake_id)]


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------
def due(*, now: Optional[datetime] = None, session_id: Optional[str] = None, conn=None) -> list[Wake]:
    """Every wake whose condition is met and which has not fired yet.

    Two steps, in this order:

    1. **Promote elapsed timers.** A timer's condition is time passing, and
       nothing else in the platform is watching the clock on its behalf, so this
       sweep is where it is evaluated — one conditional ``pending -> due`` per
       elapsed row. Completion and event wakes were already promoted by
       :func:`complete_job` / :func:`fire_event` when their condition occurred.
    2. **Return the ``due`` rows.** Never ``pending`` (condition unmet), never
       ``fired`` (already spent), never ``cancelled``.

    Reading is the tick's job, so this degrades to ``[]`` and logs rather than
    raising — a raise here wedges the Genesis daemon for every other reflex.
    """
    c = _connect(conn)
    _ensure_schema(c)
    cutoff = _iso(now or _utcnow())
    try:
        elapsed = _select(
            c,
            "state = %s AND kind = %s AND fire_at IS NOT NULL AND fire_at <= %s",
            (STATE_PENDING, KIND_TIMER, cutoff),
        )
        for wake in elapsed:
            _promote(c, wake.wake_id)
        if session_id is None:
            return _select(c, "state = %s", (STATE_DUE,))
        return _select(c, "state = %s AND session_id = %s", (STATE_DUE, str(session_id)))
    except Exception as exc:  # noqa: BLE001
        logger.warning("wake: due() failed, reporting no due wakes: %s", exc)
        return []


def pending(session_id: str, *, conn=None) -> list[Wake]:
    """What one session is still waiting on — ``pending`` only.

    Deliberately excludes ``due``: a due wake is no longer waiting, it is waiting
    to be delivered, and :func:`due` owns that set.
    """
    c = _connect(conn)
    _ensure_schema(c)
    try:
        return _select(c, "state = %s AND session_id = %s", (STATE_PENDING, str(session_id)))
    except Exception as exc:  # noqa: BLE001
        logger.warning("wake: pending(%r) failed: %s", session_id, exc)
        return []


def get(wake_id: str, *, conn=None) -> Optional[Wake]:
    """One wake by id, or ``None``."""
    c = _connect(conn)
    _ensure_schema(c)
    try:
        rows = _select(c, "wake_id = %s", (str(wake_id),))
    except Exception as exc:  # noqa: BLE001
        logger.warning("wake: get(%r) failed: %s", wake_id, exc)
        return None
    return rows[0] if rows else None
