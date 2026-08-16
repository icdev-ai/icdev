#!/usr/bin/env python3
# CUI // SP-CTI
"""The append-only agent event log — "model-visible means logged" (hcx-evt-01).

WHAT WAS MISSING
----------------
``icdev/tools/llm/agent_loop_session.py::save_session`` persists an agent run as
one ``messages_json`` blob, UPSERT-overwritten every turn. That is enough to
RESUME a run and nothing else: once turn N+1 is written, turn N is gone, so
forking a run at an earlier point and replaying one step by step are not merely
unimplemented — the data they need has already been destroyed.

``llm_gateway_audit`` does not cover it either. It stores hashes only, and it is
imported by ``tools/cortex/*`` and ``tools/ops_hub/llmops_engine.py``, none of
which is on the agent-runtime path. SAG's own LLM calls are unaudited today.

This module is the missing half. One immutable row per model-visible event, in
order, hashed, in ``agent_session_events`` (migration
20260816122036_agent_session_events).

APPEND-ONLY IS ENFORCED BY THE ABSENCE OF A VERB
------------------------------------------------
The public surface is :func:`append`, :func:`read_session` and :func:`next_seq`.
There is no update, no delete, no upsert, and adding one would be the defect
rather than a feature: a correction is a new event. ``agent_session_events`` is
registered in ``APPEND_ONLY_TABLES`` in ``.claude/hooks/pre_tool_use.py``, so the
hook refuses an ``UPDATE``/``DELETE`` against it from any tool call.

ORDERING IS ``seq``, NOT THE CLOCK
----------------------------------
Several events inside one turn routinely share a millisecond, so ``occurred_at``
cannot totally order a session. ``seq`` is monotonic per session from 1 and the
database holds a UNIQUE index over ``(session_id, seq)``.

:func:`next_seq` therefore allocates OPTIMISTICALLY — it reads the current
maximum and returns it plus one — and :func:`append` retries on the constraint
violation a lost race produces. The alternative, a lock or a sequence table,
buys nothing here: the losing writer has to re-read either way, and a unique
index makes a duplicate position impossible rather than merely unlikely. What is
NOT acceptable is the read-modify-write with no constraint behind it, which is
the shape that gave this repo three colliding migration numbers in one session.

payload_hash IS ALWAYS WRITTEN; payload_json IS NOT
---------------------------------------------------
``payload_hash`` is NOT NULL and is computed by
:func:`tools.audit.row_hash.compute_payload_hash` — the same module, algorithm
and encoding constants as the migration-149 audit chain, so this codebase has
one hashing recipe and not two.

``payload_json`` is nullable because keeping verbatim model input at rest is a
classification decision. :func:`load_policy` reads ``args/agent_event_log.yaml``
and the payload is stored only when the event's classification is at or below
the configured ceiling. A NULL ``payload_json`` beside a NOT NULL
``payload_hash`` means WITHHELD BY POLICY — :attr:`Event.payload_withheld` says
so explicitly, because "suppressed" and "empty" are different facts and a replay
that conflated them would be wrong.

The policy fails CLOSED. A config file that exists but cannot be read or parsed
yields hash-only mode, not the defaults: guessing a retention rule in the
permissive direction is how transcripts end up somewhere they are not allowed to
be. An ABSENT file is different and uses the documented defaults — a fresh
checkout has no policy because nobody has written one, not because one was lost.

WHY THE MAIN DATABASE
---------------------
Stated explicitly because CLAUDE.md's canvas rule cuts the other way, and the
table this one complements (``agent_loop_sessions``) is canvas-resident:

* RLS. ``tenant_id`` and ``classification`` are here precisely so the predicate
  ``get_connection()`` injects applies. A canvas table has neither column, and
  these rows can hold verbatim model input.
* The join. ``tools/agent_case/session_timeline.py`` orders ``hook_events`` and
  ``audit_trail`` by ``session_id``; both are in the main DB and a canvas-
  resident event log could be joined to neither.

CONSUMERS
---------
hcx-evt-02 writes these rows from a real turn (``event_recorder.py``), and
hcx-evt-04 reads them back: ``tools/agent_case/session_timeline.py`` joins this
table as a fourth source and ``case_bundler.py`` carries it in every case bundle
— WITHOUT ``payload_json``, which can hold verbatim model input a forensic
bundle must not travel with. ``payload_hash`` goes instead, so a holder of the
payload can still prove what it was. hcx-evt-03, 05 and 06 (context injection,
fork, the gate registrations) build on the same rows.

CLI::

    python tools/agent_runtime/event_log.py --session <session_id> --json
    python tools/agent_runtime/event_log.py --session <session_id> --with-payload
    python tools/agent_runtime/event_log.py --session <session_id> --type tool_call
    python tools/agent_runtime/event_log.py --policy --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.audit.row_hash import compute_payload_hash  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("agent_runtime.event_log")

TABLE = "agent_session_events"
MIGRATION = "20260816122036_agent_session_events"

#: The event vocabulary. Deliberately smaller than DSH's.
#:
#: There is no per-chunk event and there will not be one: ICDEV's agent loop does
#: not stream into this log, so a chunk row would record the transport's framing
#: rather than anything the model saw, at one row per token. ``assistant_message``
#: is the assembled message, which is the unit a fork or a replay resumes from.
#:
#: Validated in Python rather than by a CHECK constraint — the call migrations
#: 20260803002224, 20260809203855 and 20260815063941 all made. A CHECK is a
#: second copy of this tuple and it drifts the first time a type is added.
EVENT_TYPES = (
    "turn_start",
    "request_context",
    "assistant_message",
    "tool_call",
    "tool_result",
    "turn_end",
)

#: Column order. The INSERT names every one of these explicitly, and every one
#: exists in the live schema per migration 20260816122036 — the parity CLAUDE.md
#: requires (``check_insert_schema_parity``).
COLUMNS = (
    "event_id",
    "session_id",
    "seq",
    "event_type",
    "occurred_at",
    "payload_hash",
    "payload_json",
    "tenant_id",
    "classification",
    "correlation_id",
)

DEFAULT_CLASSIFICATION = "CUI"
DEFAULT_TENANT = "default"

#: How many times :func:`append` re-allocates ``seq`` after losing a race. Five
#: is far past what concurrent writers on one session produce; a session with
#: enough contention to exhaust it has a problem this module should surface
#: rather than paper over.
MAX_SEQ_ATTEMPTS = 5

_POLICY_PATH = _REPO_ROOT / "args" / "agent_event_log.yaml"

#: Env override for the retention master switch. "0"/"false"/"no"/"off" force
#: hash-only mode; "1"/"true"/"yes"/"on" force retention on. Unset defers to the
#: config file. An operator standing retention down should be visible in the
#: process environment, not only in a file diff.
RETENTION_ENV = "ICDEV_AGENT_EVENT_PAYLOAD_RETENTION"

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class EventLogUnavailable(RuntimeError):
    """The event log could not be reached or written.

    Raised, never swallowed. An event that was not recorded must not be
    indistinguishable from one that was — that is precisely how
    ``module_budget_usage`` reported success while holding zero rows.
    """


# ---------------------------------------------------------------------------
# Retention policy
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RetentionPolicy:
    """Whether an event's verbatim payload may be stored beside its hash."""

    enabled: bool = True
    max_classification: str = DEFAULT_CLASSIFICATION
    never_store: tuple[str, ...] = ()
    #: Why the policy ended up as it did — surfaced by ``--policy`` so an
    #: operator seeing empty payloads can tell a deliberate setting from a
    #: fail-closed degradation.
    source: str = "default"
    unknown_event_types: tuple[str, ...] = ()

    def stores(self, event_type: str, classification: str) -> bool:
        """True if this event's payload may be persisted verbatim."""
        if not self.enabled:
            return False
        if event_type in self.never_store:
            return False
        return _clearance(classification) <= _clearance(self.max_classification)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "max_classification": self.max_classification,
            "never_store": list(self.never_store),
            "source": self.source,
            "unknown_event_types": list(self.unknown_event_types),
        }


def _clearance(classification: str) -> int:
    """Position on the classification ladder — ORDER, never string equality.

    Comparing labels as strings would make an unrecognised label incomparable,
    and the ladder in ``classification_manager`` already resolves an unknown
    label to CUI's order rather than to a sentinel. Falls back to an inline copy
    of that ladder only if the compliance package is unimportable, so this
    module keeps working in a stripped runtime.
    """
    try:
        from tools.compliance.classification_manager import get_clearance_order

        return get_clearance_order(classification or DEFAULT_CLASSIFICATION)
    except Exception:  # noqa: BLE001 — compliance package absent
        return {
            "PUBLIC": 0,
            "UNCLASSIFIED": 0,
            "CUI": 1,
            "ECI": 2,
            "SECRET": 3,
            "TOP SECRET": 4,
            "TOP SECRET//SCI": 5,
        }.get((classification or DEFAULT_CLASSIFICATION).upper(), 1)


def _env_retention() -> Optional[bool]:
    raw = (os.environ.get(RETENTION_ENV) or "").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    if raw:
        logger.warning(
            "event_log: %s=%r is not a boolean — ignoring it and deferring to %s",
            RETENTION_ENV, raw, _POLICY_PATH.name,
        )
    return None


def load_policy(path: Optional[Path] = None) -> RetentionPolicy:
    """Read the payload retention policy from ``args/agent_event_log.yaml``.

    Three outcomes, and they are deliberately not merged:

    ``file absent``
        The documented defaults (retain, ceiling CUI). A fresh checkout has no
        policy because nobody wrote one — that is not a reason to stop logging
        payloads a default deployment is expected to keep.

    ``file present and readable``
        Its values, with the env override applied on top of ``enabled``.

    ``file present and unreadable/unparseable``
        HASH-ONLY. Fails closed, and says so through ``source``. Guessing a
        retention rule in the permissive direction is how transcripts end up
        somewhere they are not allowed to be.
    """
    cfg_path = path or _POLICY_PATH
    env = _env_retention()

    if not cfg_path.exists():
        default = RetentionPolicy(source="default_no_config_file")
        if env is None:
            return default
        return RetentionPolicy(
            enabled=env,
            max_classification=default.max_classification,
            source=f"default_no_config_file+env:{RETENTION_ENV}",
        )

    try:
        import yaml  # lazy — pyyaml is optional in a stripped runtime

        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        block = data.get("payload_retention") or {}
        if not isinstance(block, dict):
            raise ValueError("payload_retention is not a mapping")
    except Exception as exc:  # noqa: BLE001 — fail CLOSED, loudly
        logger.error(
            "event_log: %s exists but could not be read (%s) — falling back to "
            "HASH-ONLY. No payload will be stored until this is fixed.",
            cfg_path, exc,
        )
        return RetentionPolicy(enabled=False, source=f"fail_closed:{exc}")

    never = tuple(str(t) for t in (block.get("never_store") or []))
    unknown = tuple(t for t in never if t not in EVENT_TYPES)
    if unknown:
        # Reported, not silently ignored: an entry that matches no event type
        # retains everything it was written to suppress.
        logger.warning(
            "event_log: never_store names %s, which %s no event type in "
            "EVENT_TYPES — those payloads are being RETAINED",
            ", ".join(repr(t) for t in unknown),
            "matches" if len(unknown) == 1 else "match",
        )

    enabled = bool(block.get("enabled", True))
    source = str(cfg_path)
    if env is not None:
        enabled = env
        source = f"{cfg_path}+env:{RETENTION_ENV}"

    return RetentionPolicy(
        enabled=enabled,
        max_classification=str(block.get("max_classification") or DEFAULT_CLASSIFICATION),
        never_store=never,
        source=source,
        unknown_event_types=unknown,
    )


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Event:
    """One row of ``agent_session_events``."""

    event_id: str
    session_id: str
    seq: int
    event_type: str
    occurred_at: str
    payload_hash: str
    payload: Any = None
    tenant_id: str = DEFAULT_TENANT
    classification: str = DEFAULT_CLASSIFICATION
    correlation_id: str = ""
    #: True when ``payload_json`` is NULL in the row. Carried explicitly rather
    #: than inferred from ``payload is None`` by every reader, because a payload
    #: that was legitimately ``None`` and one the policy suppressed are different
    #: facts and only this flag separates them.
    payload_withheld: bool = False

    def to_dict(self, *, include_payload: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "seq": self.seq,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at,
            "payload_hash": self.payload_hash,
            "tenant_id": self.tenant_id,
            "classification": self.classification,
            "correlation_id": self.correlation_id,
            "payload_withheld": self.payload_withheld,
        }
        if include_payload:
            out["payload"] = self.payload
        return out


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------
def _connect():
    """Open a connection, or raise :class:`EventLogUnavailable`.

    ``get_connection`` — never a raw driver — so the RLS predicate and the
    ``%s`` → ``?`` translation both apply. ``agent_session_events`` carries
    ``tenant_id`` and ``classification`` precisely so it is RLS-eligible.
    """
    try:
        from tools.db.storage import get_connection, table_exists
    except Exception as exc:  # noqa: BLE001
        raise EventLogUnavailable(f"storage layer unavailable: {exc}") from exc
    try:
        conn = get_connection()
    except Exception as exc:  # noqa: BLE001
        raise EventLogUnavailable(f"cannot open a connection: {exc}") from exc
    try:
        if not table_exists(conn, TABLE):
            raise EventLogUnavailable(
                f"{TABLE} is missing — run "
                f"`python tools/db/migrate.py --up` (migration {MIGRATION})"
            )
    except EventLogUnavailable:
        _close(conn)
        raise
    except Exception as exc:  # noqa: BLE001
        _close(conn)
        raise EventLogUnavailable(f"cannot inspect {TABLE}: {exc}") from exc
    return conn


def _close(conn) -> None:
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_identity(
    tenant_id: Optional[str], classification: Optional[str]
) -> tuple[str, str]:
    """Tag the row with the caller's tenant and clearance.

    Order: explicit argument, then the ambient :class:`SecurityContext` (so a row
    written under a request is readable back through the same RLS predicate that
    filtered the request), then ``ICDEV_TENANT_ID``, then the column defaults.
    """
    tid = tenant_id
    cls = classification
    if tid is None or cls is None:
        try:
            from tools.security.security_context import get_security_context

            ctx = get_security_context()
        except Exception:  # noqa: BLE001 — security package absent
            ctx = None
        if ctx is not None:
            if tid is None:
                tid = getattr(ctx, "tenant_id", None)
            if cls is None:
                cls = getattr(ctx, "classification", None)
    if not tid:
        tid = os.environ.get("ICDEV_TENANT_ID") or DEFAULT_TENANT
    return str(tid), str(cls or DEFAULT_CLASSIFICATION)


# ---------------------------------------------------------------------------
# Sequence allocation
# ---------------------------------------------------------------------------
def next_seq(session_id: str, conn=None) -> int:
    """The next ``seq`` for ``session_id`` — 1 for a session with no events.

    OPTIMISTIC. Two writers can both be handed the same number; the UNIQUE index
    over ``(session_id, seq)`` is what stops both of them keeping it, and
    :func:`append` retries the loser. Read this as "the next free position as of
    now", not as a reservation.
    """
    if not session_id:
        raise ValueError("session_id is required")
    own = conn is None
    if own:
        conn = _connect()
    try:
        cur = conn.execute(
            f"SELECT MAX(seq) FROM {TABLE} WHERE session_id = %s", (session_id,)
        )
        row = cur.fetchone()
    except Exception as exc:  # noqa: BLE001
        raise EventLogUnavailable(
            f"could not read the sequence for session {session_id!r}: {exc}"
        ) from exc
    finally:
        if own:
            _close(conn)

    # MAX() over no rows is one row holding NULL, not zero rows — so both "no
    # such session" and "a session with events" come back the same shape and the
    # NULL is what distinguishes them.
    current = None
    if row is not None:
        if isinstance(row, dict):
            # A dict-row driver names an aggregate column however it likes
            # (`max`, `max_1`, `?column?`), so take the single value rather than
            # guessing the key.
            values = list(row.values())
            current = values[0] if values else None
        else:
            current = row[0]
    return int(current) + 1 if current is not None else 1


def _is_duplicate_seq(exc: Exception) -> bool:
    """True if this exception is the UNIQUE(session_id, seq) violation.

    Matched on text because the two backends raise unrelated types
    (``sqlite3.IntegrityError`` vs ``psycopg2.errors.UniqueViolation``) and the
    connection layer does not normalize them. Deliberately narrow: it must name
    the index or the constraint, so an unrelated integrity error is NOT retried
    into a loop.
    """
    text = str(exc).lower()
    if "idx_agent_session_events_seq" in text:
        return True
    return ("unique" in text or "duplicate key" in text) and "seq" in text


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------
def append(
    session_id: str,
    event_type: str,
    payload: Any = None,
    *,
    correlation_id: str = "",
    classification: Optional[str] = None,
    tenant_id: Optional[str] = None,
    occurred_at: Optional[str] = None,
    event_id: Optional[str] = None,
    policy: Optional[RetentionPolicy] = None,
) -> Event:
    """Append one event. The only write this module performs.

    ``payload`` is any JSON-able document, or a plain string for
    ``assistant_message``. Its SHA-256 is ALWAYS recorded; whether the document
    itself is stored is :meth:`RetentionPolicy.stores`.

    Raises :class:`EventLogUnavailable` if the row could not be written, and does
    NOT wrap the INSERT in a bare ``except``. A swallowed INSERT reports success
    while persisting nothing, which is how ``module_budget_usage`` held zero
    rows and ``tools/govcon`` never wrote an audit entry.
    """
    if not session_id:
        raise ValueError("session_id is required")
    if event_type not in EVENT_TYPES:
        raise ValueError(
            f"unknown event_type {event_type!r}; expected one of {EVENT_TYPES}"
        )

    tid, cls = _resolve_identity(tenant_id, classification)
    active = policy if policy is not None else load_policy()
    store = active.stores(event_type, cls)

    payload_hash = compute_payload_hash(payload)
    payload_json = _serialize(payload) if store else None
    stamp = occurred_at or _now()
    eid = event_id or f"ase-{uuid.uuid4().hex[:16]}"

    conn = _connect()
    try:
        placeholders = ", ".join(["%s"] * len(COLUMNS))
        sql = f"INSERT INTO {TABLE} ({', '.join(COLUMNS)}) VALUES ({placeholders})"
        last: Optional[Exception] = None
        for attempt in range(MAX_SEQ_ATTEMPTS):
            seq = next_seq(session_id, conn)
            try:
                conn.execute(
                    sql,
                    (
                        eid, session_id, seq, event_type, stamp,
                        payload_hash, payload_json, tid, cls, correlation_id or "",
                    ),
                )
                conn.commit()
                break
            except Exception as exc:  # noqa: BLE001 — inspected, then re-raised
                try:
                    conn.rollback()
                except Exception:  # noqa: BLE001
                    pass
                if not _is_duplicate_seq(exc):
                    raise EventLogUnavailable(
                        f"could not append {event_type} to session "
                        f"{session_id!r}: {exc}"
                    ) from exc
                last = exc
                logger.warning(
                    "event_log: seq %d for session %s was taken (attempt %d/%d)",
                    seq, session_id, attempt + 1, MAX_SEQ_ATTEMPTS,
                )
        else:
            raise EventLogUnavailable(
                f"could not allocate a seq for session {session_id!r} after "
                f"{MAX_SEQ_ATTEMPTS} attempts: {last}"
            )
    finally:
        _close(conn)

    logger.info(
        "event_log: %s seq=%d %s for session %s (payload %s)",
        eid, seq, event_type, session_id,
        "stored" if store else "WITHHELD by policy",
    )
    return Event(
        event_id=eid,
        session_id=session_id,
        seq=seq,
        event_type=event_type,
        occurred_at=stamp,
        payload_hash=payload_hash,
        payload=payload if store else None,
        tenant_id=tid,
        classification=cls,
        correlation_id=correlation_id or "",
        payload_withheld=not store,
    )


def _serialize(payload: Any) -> str:
    """Render a payload for the ``payload_json`` TEXT column. NEVER returns None.

    Every payload is stored as a JSON document, including a bare string and
    including ``None`` (which becomes the JSON literal ``null``). That is what
    makes the column's NULL unambiguous: ``payload_json IS NULL`` means WITHHELD
    BY POLICY and nothing else. Storing a genuinely-``None`` payload as SQL NULL
    would collapse "the policy suppressed this" and "this event carries no
    payload" into one value, and a replay built on that distinction would be
    wrong in a way no reader could detect.

    Storing a string as ``"hello"`` rather than as ``hello`` is the same
    argument one level down: a raw ``123`` would read back as an integer.

    The hash is deliberately NOT computed over this rendering —
    :func:`~tools.audit.row_hash.canonical_payload` hashes a string as itself,
    so ``"hello"`` and ``hello`` are one event. Re-deriving the hash from a
    stored row means :func:`_deserialize` first, then hash.
    """
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)


def _deserialize(raw: Any) -> Any:
    """Parse ``payload_json`` back, degrading to the raw text rather than raising.

    A read has no action to fail closed on. A column that does not parse — hand-
    written, truncated, or produced by something that predates :func:`_serialize`
    — is still evidence that the event happened, so the text is returned as-is
    rather than hidden behind a decode error. ``payload_hash`` is still there to
    say what the bytes were.
    """
    if raw is None or not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("event_log: a payload_json column did not parse as JSON")
        return raw


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------
def read_session(
    session_id: str,
    *,
    event_types: Optional[Iterable[str]] = None,
    limit: Optional[int] = None,
    include_payload: bool = True,
) -> list[Event]:
    """Every event for ``session_id``, ordered by ``seq``.

    ``include_payload=False`` reads the row without ``payload_json`` — for a
    caller that wants the shape of a session (a timeline, a turn count, a digest
    check) and has no business pulling the transcript out of the database.
    ``payload_withheld`` is derived from the stored NULL either way, so it stays
    truthful under both settings.

    RLS applies: a caller whose security context names a tenant sees that
    tenant's events only. An empty list therefore means "nothing visible to you",
    which is not the same as "no such session" — and this function does not try
    to distinguish them, because doing so would leak the existence of another
    tenant's session.
    """
    if not session_id:
        raise ValueError("session_id is required")
    wanted = tuple(event_types) if event_types else ()
    for t in wanted:
        if t not in EVENT_TYPES:
            raise ValueError(f"unknown event_type {t!r}; expected one of {EVENT_TYPES}")

    # payload_json is always SELECTed: it is what distinguishes a withheld
    # payload from a stored NULL, and dropping it from the projection would make
    # every event look withheld under include_payload=False.
    cols = ", ".join(COLUMNS)
    sql = f"SELECT {cols} FROM {TABLE} WHERE session_id = %s"
    params: list[Any] = [session_id]
    if wanted:
        sql += f" AND event_type IN ({', '.join(['%s'] * len(wanted))})"
        params.extend(wanted)
    sql += " ORDER BY seq"
    if limit is not None:
        sql += " LIMIT %s"
        params.append(int(limit))

    conn = _connect()
    try:
        rows = conn.execute(sql, tuple(params)).fetchall()
    except Exception as exc:  # noqa: BLE001
        raise EventLogUnavailable(
            f"could not read session {session_id!r}: {exc}"
        ) from exc
    finally:
        _close(conn)

    return [_row_to_event(r, include_payload=include_payload) for r in rows]


def _row_to_event(row: Any, *, include_payload: bool = True) -> Event:
    if isinstance(row, dict):
        values = {c: row.get(c) for c in COLUMNS}
    else:
        values = dict(zip(COLUMNS, row))
    raw_payload = values.get("payload_json")
    return Event(
        event_id=str(values.get("event_id") or ""),
        session_id=str(values.get("session_id") or ""),
        seq=int(values.get("seq") or 0),
        event_type=str(values.get("event_type") or ""),
        occurred_at=str(values.get("occurred_at") or ""),
        payload_hash=str(values.get("payload_hash") or ""),
        payload=_deserialize(raw_payload) if include_payload else None,
        tenant_id=str(values.get("tenant_id") or ""),
        classification=str(values.get("classification") or ""),
        correlation_id=str(values.get("correlation_id") or ""),
        payload_withheld=raw_payload is None,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read the append-only agent session event log (hcx-evt-01)"
    )
    parser.add_argument("--session", help="Session id to read")
    parser.add_argument(
        "--type", action="append", dest="types", metavar="EVENT_TYPE",
        help=f"Filter by event type (repeatable). One of: {', '.join(EVENT_TYPES)}",
    )
    parser.add_argument("--limit", type=int, help="Maximum events to return")
    parser.add_argument(
        "--with-payload", action="store_true",
        help="Include stored payloads (omitted by default — they can be large "
             "and can carry verbatim model input)",
    )
    parser.add_argument(
        "--policy", action="store_true",
        help="Show the effective payload retention policy and exit",
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    if args.policy:
        policy = load_policy()
        if args.json:
            print(json.dumps(policy.to_dict(), indent=2))
        else:
            print(f"payload retention : {'ON' if policy.enabled else 'OFF (hash-only)'}")
            print(f"ceiling           : {policy.max_classification}")
            print(f"never_store       : {', '.join(policy.never_store) or '(none)'}")
            print(f"source            : {policy.source}")
            if policy.unknown_event_types:
                print(
                    "WARNING           : never_store names unknown event types "
                    f"({', '.join(policy.unknown_event_types)}) — those payloads "
                    "are RETAINED"
                )
        return 0

    if not args.session:
        parser.error("--session is required (or use --policy)")

    try:
        events = read_session(
            args.session,
            event_types=args.types,
            limit=args.limit,
            include_payload=args.with_payload,
        )
    except (EventLogUnavailable, ValueError) as exc:
        if args.json:
            print(json.dumps({"error": str(exc), "session_id": args.session}, indent=2))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(
            {
                "session_id": args.session,
                "count": len(events),
                "events": [e.to_dict(include_payload=args.with_payload) for e in events],
            },
            indent=2,
            default=str,
        ))
        return 0

    if not events:
        print(f"No events visible for session {args.session}")
        return 0
    print(f"{len(events)} event(s) for session {args.session}")
    for e in events:
        mark = " [payload withheld]" if e.payload_withheld else ""
        print(f"  {e.seq:>4}  {e.occurred_at}  {e.event_type:<18} "
              f"{e.payload_hash[:12]}{mark}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
