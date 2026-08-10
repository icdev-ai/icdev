# CUI // SP-CTI
"""Pending-approval store for the unified approval inbox (agov-inbox-01).

``tools/agent_runtime/safety.py`` ``console_approver`` denies on EOF and
``approval_gate.py`` falls back to ``deny_all_approver``, so a headless overnight
run refuses every irreversible action today. The only workaround is to inject an
auto-approver, which discards the gate entirely.

The fix is not an autonomy setting. Unattended does not change *what* an agent
may do — the policy does — it changes *where* the ask is delivered. This module
is that destination: a durable queue of asks that outlives the console nobody was
watching.

## Two tables, two lifetimes — do not conflate them

``agent_approval_log``  migration 20260803002224. APPEND-ONLY, registered in
                        ``APPEND_ONLY_TABLES``. It is EVIDENCE: a decision made
                        once, by someone, for a stated reason.

``approval_items``      migration 20260809203855. MUTABLE, deliberately, and
                        deliberately NOT in ``APPEND_ONLY_TABLES``. It is STATE:
                        created ``pending``, then moved exactly once to
                        ``resolved`` / ``expired`` / ``cancelled``. That
                        transition IS an UPDATE.

The join happens here rather than in the schema: every ``pending`` → terminal
transition writes the permanent row through the existing
:func:`tools.agent_runtime.approval_gate.record_decision`. There is no second
decision log. An item may be pruned later without losing the decision.

Had ``approval_items`` been append-only, resolving an item would mean inserting a
second row and "which row is the current state" would have no answer the gate can
trust — which is how you end up unable to resolve an item without violating the
audit invariant.

## No argument VALUES, anywhere

``agent_approval_log`` stores argument KEY NAMES and a SHA-256 of the input,
never values, because tool arguments can carry CUI. This table keeps that
property and needs it *more*: its rows are mirrored out to Slack, Teams,
Telegram and email, so it is the last place a raw argument value may sit.

``title`` and ``body`` come from :func:`render_summary` — tier, rule, policy
prose and argument key names. **:meth:`ApprovalRequest.summary` is not safe
here**: it previews the ``command`` / ``path`` / ``file_path`` VALUE, which is
exactly the CUI this convention exists to keep out of a chat channel.

Callers may pass their own ``body``, and nothing can stop a caller pasting a
secret into a free-text string; what this module guarantees is that it never
derives one itself. :func:`enqueue` computes ``arg_keys``/``input_sha256`` from
``tool_input`` and then drops it.

## What this module is not

It is not the approver (agov-inbox-02), not the channel transport
(agov-inbox-03), and it does not decide anything. It stores, transitions, and
records. It also never creates its own table: a missing ``approval_items`` raises
:class:`ApprovalInboxUnavailable` rather than running ``CREATE TABLE IF NOT
EXISTS``, because that statement never ALTERs an existing table and silently
drifting away from the migration is how an INSERT starts failing inside a
swallowed exception (CLAUDE.md).

CLI::

    python tools/agent_runtime/approval_inbox.py --list --json
    python tools/agent_runtime/approval_inbox.py --list --state pending --inbox ops
    python tools/agent_runtime/approval_inbox.py --show <item_id> --json
    python tools/agent_runtime/approval_inbox.py --resolve <item_id> --deny \\
        --reason "not authorised" --json
    python tools/agent_runtime/approval_inbox.py --expire-due --json
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.agent_runtime.approval_gate import (  # noqa: E402
    ApprovalDecision,
    Classification,
    _arg_keys,
    _input_digest,
    record_decision,
    resolve_actor,
    resolve_mode,
)
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.agent_runtime.approval_inbox")

TABLE = "approval_items"

# --- Vocabularies ----------------------------------------------------------
# Single source of truth. Migration 20260809203855 deliberately ships no CHECK
# constraint over these, for the same reason migration 20260803002224 did not:
# a CHECK is a second hardcoded copy that drifts, and agov-inbox-05 adds origins.
# Validation happens here, before the INSERT.
ORIGIN_SAG = "sag"
ORIGIN_ACE = "ace"
ORIGIN_WORKFLOW_HITL = "workflow_hitl"
ORIGINS = (ORIGIN_SAG, ORIGIN_ACE, ORIGIN_WORKFLOW_HITL)

STATE_PENDING = "pending"
STATE_RESOLVED = "resolved"
STATE_EXPIRED = "expired"
STATE_CANCELLED = "cancelled"
STATES = (STATE_PENDING, STATE_RESOLVED, STATE_EXPIRED, STATE_CANCELLED)
TERMINAL_STATES = (STATE_RESOLVED, STATE_EXPIRED, STATE_CANCELLED)

RESOLUTION_APPROVED = "approved"
RESOLUTION_DENIED = "denied"
RESOLUTIONS = (RESOLUTION_APPROVED, RESOLUTION_DENIED)

DEFAULT_INBOX = "default"
DEFAULT_CLASSIFICATION = "CUI"

# Column order is the live schema's order. The INSERT names every column
# explicitly, so this list and migration 20260809203855 must agree — asserted by
# tests/test_approval_inbox.py against the migration's own DDL.
COLUMNS = (
    "item_id",
    "session_id",
    "origin",
    "tool_name",
    "tier",
    "title",
    "body",
    "inbox",
    "state",
    "resolution",
    "resolved_by",
    "resolved_at",
    "expires_at",
    "rule",
    "arg_keys",
    "input_sha256",
    "classification",
    "created_at",
    "updated_at",
)


class ApprovalInboxUnavailable(RuntimeError):
    """The ``approval_items`` table is absent or unreachable.

    Raised by :func:`enqueue` rather than swallowed. An approval that cannot be
    persisted must not look like an approval that was delivered: the caller has
    to treat this as a denial, which is the same fail-closed posture as
    ``deny_all_approver``. Reads degrade to empty instead, because a missing
    table is not evidence that nothing is pending — it is evidence that nothing
    can be known, and a read has no action to fail closed on.
    """


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ApprovalItem:
    """One pending (or settled) ask. Mirrors a row of ``approval_items``."""

    item_id: str
    origin: str
    tool_name: str
    tier: str
    title: str
    state: str = STATE_PENDING
    session_id: str = ""
    body: str = ""
    inbox: str = DEFAULT_INBOX
    resolution: str = ""
    resolved_by: str = ""
    resolved_at: str = ""
    expires_at: str = ""
    rule: str = ""
    arg_keys: str = ""
    input_sha256: str = ""
    classification: str = DEFAULT_CLASSIFICATION
    created_at: str = ""
    updated_at: str = ""

    @property
    def is_pending(self) -> bool:
        return self.state == STATE_PENDING

    @property
    def is_approved(self) -> bool:
        """Approved is the ONLY affirmative outcome.

        Deliberately not ``state != denied``: an expired or cancelled item is
        not an approval, and a property that reads "not explicitly refused" is
        how a timeout becomes an auto-approver.
        """
        return self.state == STATE_RESOLVED and self.resolution == RESOLUTION_APPROVED

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Rendering — the ONLY sanctioned way to turn a tool call into deliverable text
# ---------------------------------------------------------------------------
def render_summary(
    classification: Classification,
    tool_input: Any = None,
    *,
    actor: str = "",
) -> tuple[str, str]:
    """Render ``(title, body)`` for a tool call. Never includes an argument VALUE.

    The body names the tier, the policy rule that decided it, the policy's own
    prose about what will happen, and the argument KEY NAMES. That is enough for
    a human to decide and safe to put in a Slack message.

    Use this instead of :meth:`ApprovalRequest.summary`, which previews the
    ``command`` / ``path`` / ``file_path`` value.
    """
    title = f"[{classification.tier.upper()}] {classification.tool_name}"
    keys = _arg_keys(tool_input)
    lines = [
        f"Tool: {classification.tool_name}",
        f"Tier: {classification.tier}",
        f"Rule: {classification.rule}",
    ]
    if classification.detail:
        lines.append(f"Why: {classification.detail}")
    lines.append(f"Argument keys: {keys or '(none)'}")
    lines.append(
        "Argument values are deliberately not shown — they can carry CUI. "
        f"Input SHA-256: {_input_digest(tool_input)}"
    )
    if actor:
        lines.append(f"Requested by: {actor}")
    return title, "\n".join(lines)


# ---------------------------------------------------------------------------
# Connection plumbing
# ---------------------------------------------------------------------------
def _connect():
    """Open a connection, or raise :class:`ApprovalInboxUnavailable`.

    ``get_connection`` (never a raw driver) so the RLS predicate and the
    ``%s`` → ``?`` translation both apply — ``approval_items`` carries a
    ``classification`` column precisely so it is RLS-eligible.
    """
    try:
        from tools.db.storage import get_connection, table_exists
    except Exception as exc:  # noqa: BLE001
        raise ApprovalInboxUnavailable(f"storage layer unavailable: {exc}") from exc
    try:
        conn = get_connection()
    except Exception as exc:  # noqa: BLE001
        raise ApprovalInboxUnavailable(f"cannot open a connection: {exc}") from exc
    try:
        if not table_exists(conn, TABLE):
            raise ApprovalInboxUnavailable(
                f"{TABLE} is missing — run "
                "`python tools/db/migrate.py --up` (migration "
                "20260809203855_agov_approval_items)"
            )
    except ApprovalInboxUnavailable:
        _close(conn)
        raise
    except Exception as exc:  # noqa: BLE001
        _close(conn)
        raise ApprovalInboxUnavailable(f"cannot inspect {TABLE}: {exc}") from exc
    return conn


def _close(conn) -> None:
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        pass


def _row_to_item(row: Any) -> ApprovalItem:
    if isinstance(row, dict):
        values = {c: row.get(c) for c in COLUMNS}
    else:
        values = dict(zip(COLUMNS, row))
    return ApprovalItem(
        **{k: ("" if v is None else str(v)) for k, v in values.items()}
    )


_SELECT = f"SELECT {', '.join(COLUMNS)} FROM {TABLE}"


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------
def enqueue(
    *,
    tool_name: str,
    tier: str,
    title: str,
    origin: str = ORIGIN_SAG,
    session_id: str = "",
    body: str = "",
    inbox: str = DEFAULT_INBOX,
    tool_input: Any = None,
    rule: str = "",
    expires_in_seconds: Optional[float] = None,
    expires_at: str = "",
    classification: str = DEFAULT_CLASSIFICATION,
    item_id: Optional[str] = None,
) -> ApprovalItem:
    """Create one ``pending`` item and return it.

    ``tool_input`` is read to derive ``arg_keys`` and ``input_sha256`` and is
    then dropped — it is never stored and never rendered. Pass a ``body`` you
    produced with :func:`render_summary`.

    Raises :class:`ApprovalInboxUnavailable` if the item cannot be persisted. An
    ask that was never queued must not be mistaken for one awaiting an answer.
    """
    if origin not in ORIGINS:
        raise ValueError(f"unknown origin {origin!r}; expected one of {ORIGINS}")
    if not tool_name:
        raise ValueError("tool_name is required")
    if not title:
        raise ValueError("title is required")

    if not expires_at and expires_in_seconds is not None:
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=float(expires_in_seconds))
        ).isoformat()

    now = _now()
    item = ApprovalItem(
        item_id=item_id or f"ai-{uuid.uuid4().hex[:16]}",
        session_id=session_id or _session_id(),
        origin=origin,
        tool_name=tool_name,
        tier=tier,
        title=title,
        body=body,
        inbox=inbox or DEFAULT_INBOX,
        state=STATE_PENDING,
        resolution="",
        resolved_by="",
        resolved_at="",
        expires_at=expires_at,
        rule=rule,
        arg_keys=_arg_keys(tool_input),
        input_sha256=_input_digest(tool_input),
        classification=classification or DEFAULT_CLASSIFICATION,
        created_at=now,
        updated_at=now,
    )

    conn = _connect()
    try:
        placeholders = ", ".join(["%s"] * len(COLUMNS))
        conn.execute(
            f"INSERT INTO {TABLE} ({', '.join(COLUMNS)}) VALUES ({placeholders})",
            tuple(getattr(item, c) for c in COLUMNS),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 — surfaced, never swallowed
        raise ApprovalInboxUnavailable(f"could not enqueue {item.item_id}: {exc}") from exc
    finally:
        _close(conn)
    logger.info(
        "approval_inbox: queued %s (%s/%s) to inbox %s",
        item.item_id, item.tool_name, item.tier, item.inbox,
    )
    return item


def _settle(
    item_id: str,
    *,
    state: str,
    resolution: str,
    resolved_by: str,
    reason: str,
    mode: Optional[str] = None,
) -> Optional[ApprovalItem]:
    """Move one PENDING item to a terminal state and record the decision.

    The UPDATE is conditional on ``state = 'pending'`` and the transition is
    accepted only if it changed exactly one row, so two racing resolvers — a
    Slack reply and the expiry sweep, say — cannot both settle the same item and
    cannot both write a decision row. Returns ``None`` when the item is absent or
    already settled, which makes a repeated resolution an idempotent no-op rather
    than a second, contradictory audit record.
    """
    if state not in TERMINAL_STATES:
        raise ValueError(f"{state!r} is not a terminal state; expected {TERMINAL_STATES}")
    if resolution not in RESOLUTIONS:
        raise ValueError(f"unknown resolution {resolution!r}; expected {RESOLUTIONS}")

    now = _now()
    who = resolve_actor(resolved_by)

    conn = _connect()
    try:
        cur = conn.execute(
            f"UPDATE {TABLE} SET state = %s, resolution = %s, resolved_by = %s, "
            f"resolved_at = %s, updated_at = %s "
            f"WHERE item_id = %s AND state = %s",
            (state, resolution, who, now, now, item_id, STATE_PENDING),
        )
        changed = getattr(cur, "rowcount", 0) or 0
        if changed < 1:
            conn.commit()
            logger.info(
                "approval_inbox: %s not settled (absent or already terminal)", item_id
            )
            return None
        conn.commit()
        cur = conn.execute(f"{_SELECT} WHERE item_id = %s", (item_id,))
        row = cur.fetchone()
    finally:
        _close(conn)

    if row is None:  # pragma: no cover — the UPDATE just matched it
        return None
    item = _row_to_item(row)

    # The permanent record. Reuses the existing append-only writer rather than
    # adding a second decision log, and hands it the key names and digest this
    # item has carried since enqueue — the resolver (a chat reply, hours later)
    # never saw the arguments and must not need to.
    recorded = record_decision(
        tool_name=item.tool_name,
        tool_input=None,
        classification=Classification(
            tool_name=item.tool_name,
            tier=item.tier,
            rule=item.rule or "approval_inbox",
            detail=item.title,
            requires_approval=True,
        ),
        decision=ApprovalDecision(
            approved=(resolution == RESOLUTION_APPROVED),
            reason=reason or state,
            actor=who,
        ),
        mode=mode or resolve_mode(),
        session_id=item.session_id,
        arg_keys=item.arg_keys,
        input_sha256=item.input_sha256,
    )
    if not recorded:
        logger.error(
            "approval_inbox: %s settled as %s but the decision is UNRECORDED",
            item_id, resolution,
        )
    return item


def resolve(
    item_id: str,
    *,
    approved: bool,
    resolved_by: str = "",
    reason: str = "",
    mode: Optional[str] = None,
) -> Optional[ApprovalItem]:
    """Answer a pending item. ``None`` if it is absent or already settled."""
    return _settle(
        item_id,
        state=STATE_RESOLVED,
        resolution=RESOLUTION_APPROVED if approved else RESOLUTION_DENIED,
        resolved_by=resolved_by,
        reason=reason or ("operator approved" if approved else "operator denied"),
        mode=mode,
    )


def cancel(
    item_id: str,
    *,
    resolved_by: str = "",
    reason: str = "",
    mode: Optional[str] = None,
) -> Optional[ApprovalItem]:
    """Withdraw a pending item — the asker went away, nobody answered.

    Records ``denied``, not a third outcome. From the gate's side the only fact
    that matters is that the action never received approval.
    """
    return _settle(
        item_id,
        state=STATE_CANCELLED,
        resolution=RESOLUTION_DENIED,
        resolved_by=resolved_by,
        reason=reason or "cancelled before it was answered",
        mode=mode,
    )


def expire(
    item_id: str,
    *,
    resolved_by: str = "expiry-sweep",
    reason: str = "",
    mode: Optional[str] = None,
) -> Optional[ApprovalItem]:
    """Time one item out. Records ``denied`` — a timeout is never an approval."""
    return _settle(
        item_id,
        state=STATE_EXPIRED,
        resolution=RESOLUTION_DENIED,
        resolved_by=resolved_by,
        reason=reason or "expired before it was answered",
        mode=mode,
    )


def expire_due(*, now: Optional[str] = None, limit: int = 500) -> list[ApprovalItem]:
    """Expire every pending item whose ``expires_at`` has passed.

    ``expires_at`` is an ISO-8601 UTC string, so a lexical ``<`` comparison is a
    chronological one; items with no expiry are never swept.
    """
    cutoff = now or _now()
    due = [
        item
        for item in list_items(state=STATE_PENDING, limit=limit)
        if item.expires_at and item.expires_at <= cutoff
    ]
    return [x for x in (expire(item.item_id) for item in due) if x is not None]


# ---------------------------------------------------------------------------
# Reads — degrade to empty, never raise
# ---------------------------------------------------------------------------
def get(item_id: str) -> Optional[ApprovalItem]:
    """One item by id, or ``None``."""
    try:
        conn = _connect()
    except ApprovalInboxUnavailable as exc:
        logger.debug("approval_inbox: get(%s) unavailable: %s", item_id, exc)
        return None
    try:
        row = conn.execute(f"{_SELECT} WHERE item_id = %s", (item_id,)).fetchone()
    except Exception as exc:  # noqa: BLE001
        logger.warning("approval_inbox: get(%s) failed: %s", item_id, exc)
        return None
    finally:
        _close(conn)
    return _row_to_item(row) if row is not None else None


def list_items(
    *,
    state: Optional[str] = None,
    inbox: Optional[str] = None,
    session_id: Optional[str] = None,
    origin: Optional[str] = None,
    limit: int = 200,
) -> list[ApprovalItem]:
    """Items matching the given filters, newest first."""
    clauses: list[str] = []
    params: list[Any] = []
    for column, value in (
        ("state", state),
        ("inbox", inbox),
        ("session_id", session_id),
        ("origin", origin),
    ):
        if value:
            clauses.append(f"{column} = %s")
            params.append(value)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    # LIMIT is interpolated as an int, not bound. The RLS layer prepends its own
    # predicate params to a SELECT, and a trailing bound LIMIT is the one slot
    # whose position that reordering would silently shift. int() makes the
    # interpolation non-injectable.
    sql = f"{_SELECT}{where} ORDER BY created_at DESC LIMIT {int(limit)}"

    try:
        conn = _connect()
    except ApprovalInboxUnavailable as exc:
        logger.debug("approval_inbox: list unavailable: %s", exc)
        return []
    try:
        rows = conn.execute(sql, tuple(params)).fetchall() or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("approval_inbox: list failed: %s", exc)
        return []
    finally:
        _close(conn)
    return [_row_to_item(r) for r in rows]


def list_pending(
    *, inbox: Optional[str] = None, session_id: Optional[str] = None, limit: int = 200
) -> list[ApprovalItem]:
    """The asks still waiting for a human."""
    return list_items(
        state=STATE_PENDING, inbox=inbox, session_id=session_id, limit=limit
    )


def _session_id() -> str:
    for key in ("ICDEV_SESSION_ID", "CLAUDE_SESSION_ID"):
        val = os.environ.get(key)
        if val:
            return val
    return "unknown"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Inspect and settle the agent approval inbox (agov-inbox-01)."
    )
    parser.add_argument("--list", action="store_true", help="list items")
    parser.add_argument("--show", metavar="ITEM_ID", help="show one item")
    parser.add_argument("--resolve", metavar="ITEM_ID", help="settle one pending item")
    parser.add_argument("--approve", action="store_true", help="with --resolve: approve")
    parser.add_argument("--deny", action="store_true", help="with --resolve: deny")
    parser.add_argument("--reason", default="", help="with --resolve: recorded reason")
    parser.add_argument("--expire-due", action="store_true", help="sweep expired items")
    parser.add_argument("--state", help="with --list: filter by state")
    parser.add_argument("--inbox", help="with --list: filter by inbox")
    parser.add_argument("--session-id", help="with --list: filter by session")
    parser.add_argument("--limit", type=int, default=50, help="with --list: max rows")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    def emit(payload: Any) -> None:
        if args.json:
            print(json.dumps(payload, indent=2, default=str))
        else:
            print(payload)

    if args.show:
        item = get(args.show)
        if item is None:
            emit({"error": f"no item {args.show}"})
            return 1
        emit(item.to_dict())
        return 0

    if args.resolve:
        if args.approve == args.deny:
            # Neither or both. An approval must be an explicit, unambiguous act.
            parser.error("--resolve requires exactly one of --approve / --deny")
        item = resolve(
            args.resolve,
            approved=bool(args.approve),
            reason=args.reason,
        )
        if item is None:
            emit({"error": f"{args.resolve} is absent or already settled"})
            return 1
        emit(item.to_dict())
        return 0

    if args.expire_due:
        swept = expire_due()
        emit({"expired": [x.item_id for x in swept], "count": len(swept)})
        return 0

    if args.list:
        items = list_items(
            state=args.state,
            inbox=args.inbox,
            session_id=args.session_id,
            limit=args.limit,
        )
        if args.json:
            emit([x.to_dict() for x in items])
        else:
            for x in items:
                print(f"{x.item_id}  {x.state:<9}  {x.tier:<12}  {x.tool_name}")
            print(f"({len(items)} items)")
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
