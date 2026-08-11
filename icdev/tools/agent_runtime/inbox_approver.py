# CUI // SP-CTI
"""Inbox-backed :data:`Approver` for the reversibility gate (agov-inbox-02).

``tools/agent_runtime/approval_gate.py`` already halts an irreversible tool call
and asks a human. The problem is *where* it asks: :func:`~tools.agent_runtime.
approval_gate.console_approver` denies on EOF, and the fallback is
:func:`~tools.agent_runtime.approval_gate.deny_all_approver`. A headless
overnight run therefore refuses every irreversible action, and the only
workaround in the tree today is to inject an auto-approver — which throws the
gate away entirely.

This module is the third option. It is a **drop-in** ``Approver``: the gate's
``Approver = Callable[[ApprovalRequest], bool | ApprovalDecision]`` seam already
existed, so nothing here changes the gate's signature, forks it, or touches tier
classification. ``build_approval_hook(approver=inbox_approver)`` and the same
call is gated by exactly the same policy — only the delivery destination moves,
from a console nobody is watching to a durable queue somebody will answer.

That distinction is load-bearing and is the whole point of the AGOV INBOX epic:
unattended is **not** an autonomy setting. It does not widen what the agent may
do, it does not downgrade a tier, and it does not approve anything. If this
module ever allows on its own, the feature has silently become an auto-approver,
which is strictly worse than the deny-on-EOF behaviour it replaces.

## Flow

    enqueue an ``approval_items`` row   (agov-inbox-01, :mod:`approval_inbox`)
      → deliver to a channel            (agov-inbox-03, injected as ``deliver``)
      → wait                            (below)
      → resolve                         (a reply, a CLI, or the expiry sweep)

## The wait is reused, not reinvented

``tools/ace/coworker_thread.py`` already solved exactly this for ACE's
``HITLGate``: a per-id :class:`threading.Event` so an in-process resolver wakes
the waiter within milliseconds, plus a ~30 s fallback DB poll so a resolution
written by a *different* process (a Flask worker, the CLI, a chat webhook)
still lands even though it cannot set an Event in this one. The same shape is
copied here, including the ordering detail that makes it correct: the Event is
cleared **before** the state is read, so a resolution that lands between the
read and the ``wait()`` leaves the Event set and the next ``wait()`` returns at
once. Clearing afterwards is the classic lost-wakeup bug.

The poll is the load-bearing path and the Event is the optimisation, never the
other way round: :func:`tools.agent_runtime.approval_inbox.resolve` knows
nothing about this registry, because the process that answers usually is not
the process that asked.

## A timeout is a denial. Always.

An item that runs out its clock resolves ``denied`` with a reason naming the
expiry, matching the gate's existing posture — it denies on EOF
(``console_approver``) and denies when an approver raises
(``approval_gate.py``, "a broken approver denies"). A timeout that allowed would
turn an unattended session into precisely the auto-approver this epic exists to
avoid, and it would do so on the calls that matter most, since only
``irreversible`` and ``unknown`` reach an approver at all.

The single case where a lapsed deadline still returns allowed is a genuine race:
a human answered ``approve`` in the moment between the deadline passing and the
expiry landing. That is a real approval, recorded by a real actor, and
:func:`tools.agent_runtime.approval_inbox.expire` refuses to move an
already-settled item, so it is detected rather than assumed.

## Two audit rows, on purpose

Settling an item writes the permanent ``agent_approval_log`` row (the store
does that, through the gate's own ``record_decision``), and the gate then writes
its own row when this approver returns. They are not duplicates: the first
records *who answered and when*, the second records *what the gate did with that
answer* under which mode and tier. Both carry the same ``input_sha256``, which
is what joins them. Suppressing either would mean editing the gate, and the
gate's audit call is not this module's to remove.

Usage::

    from tools.agent_runtime.approval_gate import build_approval_hook
    from tools.agent_runtime.inbox_approver import inbox_approver

    hook = build_approval_hook(approver=inbox_approver)

    # or, configured:
    hook = build_approval_hook(
        approver=make_inbox_approver(inbox="ops", timeout_seconds=1800),
    )
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.agent_runtime.approval_gate import (  # noqa: E402
    ApprovalDecision,
    ApprovalRequest,
    Approver,
    resolve_actor,
)
from tools.agent_runtime.approval_inbox import (  # noqa: E402
    DEFAULT_INBOX,
    ORIGIN_SAG,
    STATE_CANCELLED,
    STATE_EXPIRED,
    ApprovalInboxUnavailable,
    ApprovalItem,
    enqueue,
    expire,
    get,
    render_summary,
)
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.agent_runtime.inbox_approver")

# How long an ask waits before it is denied. One hour: long enough for a human
# to come back from lunch, short enough that a forgotten item does not pin an
# agent thread overnight. The item's own ``expires_at`` is set to the same
# instant, so the cross-process sweep (``approval_inbox.expire_due``) reaches the
# same verdict as the waiter even if this process died.
DEFAULT_TIMEOUT_SECONDS = 3600.0

# Cross-process fallback re-check interval — the same 30 s the ACE HITL wait
# uses (``coworker_thread._HITL_EVENT_WAIT``), and for the same reason: a
# resolution written by another process cannot set our in-process Event.
DEFAULT_POLL_SECONDS = 30.0

TIMEOUT_ENV = "ICDEV_APPROVAL_INBOX_TIMEOUT"
POLL_ENV = "ICDEV_APPROVAL_INBOX_POLL"
INBOX_ENV = "ICDEV_APPROVAL_INBOX"

# What a delivery channel is handed (agov-inbox-03). Injected, never imported:
# this module must not grow a transport, and a delivery failure must never
# resolve or drop the item.
Deliverer = Callable[[ApprovalItem], Any]


# ---------------------------------------------------------------------------
# Wake registry — one threading.Event per item_id
#
# Copied from tools/ace/coworker_thread.py's HITL wake registry. An in-process
# resolver (the dashboard's Flask worker, an inbound chat handler running in
# this interpreter) calls :func:`wake` and the waiter observes the resolution
# immediately instead of after the next poll. Access is locked because the
# resolver and the waiter are different threads by construction.
# ---------------------------------------------------------------------------
_wake_lock = threading.Lock()
_wake_events: dict[str, threading.Event] = {}


def _get_wake_event(item_id: str) -> threading.Event:
    """Return (creating if absent) the wake Event for ``item_id``."""
    with _wake_lock:
        ev = _wake_events.get(item_id)
        if ev is None:
            ev = threading.Event()
            _wake_events[item_id] = ev
        return ev


def _discard_wake_event(item_id: str) -> None:
    with _wake_lock:
        _wake_events.pop(item_id, None)


def wake(item_id: str) -> bool:
    """Wake a waiter parked on ``item_id``. Best-effort; never raises.

    Purely an optimisation for a resolver that happens to share this
    interpreter. Returns ``False`` when nobody is parked here — which is the
    normal case, not an error, because the answer usually arrives in another
    process and the poll is what catches it.
    """
    with _wake_lock:
        ev = _wake_events.get(item_id)
    if ev is None:
        return False
    ev.set()
    return True


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def _positive_float(raw: Any, fallback: float) -> float:
    """A non-positive or unparseable value falls back rather than disabling the wait."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return fallback
    return value if value > 0 else fallback


def resolve_timeout(timeout_seconds: Optional[float] = None) -> float:
    """arg → ``ICDEV_APPROVAL_INBOX_TIMEOUT`` → :data:`DEFAULT_TIMEOUT_SECONDS`."""
    if timeout_seconds is not None:
        return _positive_float(timeout_seconds, DEFAULT_TIMEOUT_SECONDS)
    return _positive_float(os.environ.get(TIMEOUT_ENV), DEFAULT_TIMEOUT_SECONDS)


def resolve_poll(poll_seconds: Optional[float] = None) -> float:
    """arg → ``ICDEV_APPROVAL_INBOX_POLL`` → :data:`DEFAULT_POLL_SECONDS`."""
    if poll_seconds is not None:
        return _positive_float(poll_seconds, DEFAULT_POLL_SECONDS)
    return _positive_float(os.environ.get(POLL_ENV), DEFAULT_POLL_SECONDS)


def resolve_inbox(inbox: Optional[str] = None) -> str:
    """arg → ``ICDEV_APPROVAL_INBOX`` → ``default``."""
    for candidate in (inbox, os.environ.get(INBOX_ENV)):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    return DEFAULT_INBOX


# ---------------------------------------------------------------------------
# The wait
# ---------------------------------------------------------------------------
def wait_for_resolution(
    item_id: str,
    *,
    timeout_seconds: float,
    poll_seconds: float,
) -> Optional[ApprovalItem]:
    """Block until ``item_id`` leaves ``pending``, or the deadline passes.

    Returns the settled :class:`ApprovalItem`, or ``None`` if the deadline
    passed with the item still pending (or the item could no longer be read).
    ``None`` is never an approval — the caller expires and denies.
    """
    deadline = time.monotonic() + timeout_seconds
    ev = _get_wake_event(item_id)
    try:
        while True:
            # Clear BEFORE reading, so a resolution landing between the read and
            # the wait() still leaves the Event set and the next wait() returns
            # immediately. Clearing after the read is the lost-wakeup bug.
            ev.clear()
            item = get(item_id)
            if item is None:
                # Absent or unreadable. Not an approval either way, and there is
                # nothing left to wait on.
                logger.warning("inbox_approver: %s could not be read while waiting", item_id)
                return None
            if not item.is_pending:
                return item
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            ev.wait(timeout=min(poll_seconds, remaining))
    finally:
        _discard_wake_event(item_id)


# ---------------------------------------------------------------------------
# The approver
# ---------------------------------------------------------------------------
def _deny(reason: str, actor: str) -> ApprovalDecision:
    return ApprovalDecision(approved=False, reason=reason, actor=actor)


def _decide(item: ApprovalItem, actor: str) -> ApprovalDecision:
    """Turn a settled item into a decision. Only ``resolved+approved`` allows."""
    who = item.resolved_by or actor
    if item.is_approved:
        return ApprovalDecision(
            approved=True,
            reason=f"approved via approval inbox item {item.item_id} by {who}",
            actor=who,
        )
    if item.state == STATE_EXPIRED:
        return _deny(
            f"approval inbox item {item.item_id} EXPIRED before it was answered "
            f"(expires_at={item.expires_at or 'unset'}); a timeout is never an approval",
            who,
        )
    if item.state == STATE_CANCELLED:
        return _deny(
            f"approval inbox item {item.item_id} was cancelled before it was answered",
            who,
        )
    return _deny(f"denied via approval inbox item {item.item_id} by {who}", who)


def make_inbox_approver(
    *,
    inbox: Optional[str] = None,
    origin: str = ORIGIN_SAG,
    timeout_seconds: Optional[float] = None,
    poll_seconds: Optional[float] = None,
    session_id: str = "",
    classification: str = "",
    deliver: Optional[Deliverer] = None,
) -> Approver:
    """Build an :data:`Approver` that asks an inbox instead of a console.

    Args:
        inbox: Destination queue name. Defaults to ``$ICDEV_APPROVAL_INBOX`` or
            ``default``. Channel routing itself is agov-inbox-03's.
        origin: Which gate is asking — ``sag`` here; ACE and ``workflow_hitl``
            route through the same store in agov-inbox-05.
        timeout_seconds: Wait budget. Also becomes the item's ``expires_at``, so
            the cross-process sweep reaches the same verdict if this process dies.
        poll_seconds: Cross-process fallback re-check interval.
        deliver: Called once with the queued item. A raising or slow deliverer is
            logged and the item stays ``pending`` — in-app is the store of
            record and a channel is a mirror, so a failed mirror must never
            resolve or drop the ask.

    Returns:
        A callable with the existing ``Approver`` signature.
    """
    def approver(request: ApprovalRequest) -> ApprovalDecision:
        actor = resolve_actor(request.actor or None)
        timeout = resolve_timeout(timeout_seconds)
        poll = resolve_poll(poll_seconds)
        # render_summary, never ApprovalRequest.summary(): the latter previews
        # the command / path / file_path VALUE, and this row gets mirrored into
        # a chat channel.
        title, body = render_summary(
            request.classification, request.tool_input, actor=actor
        )

        try:
            item = enqueue(
                tool_name=request.tool_name,
                tier=request.classification.tier,
                title=title,
                body=body,
                origin=origin,
                session_id=session_id,
                inbox=resolve_inbox(inbox),
                tool_input=request.tool_input,
                rule=request.classification.rule,
                expires_in_seconds=timeout,
                **({"classification": classification} if classification else {}),
            )
        except (ApprovalInboxUnavailable, ValueError) as exc:
            # An ask that could not be queued is an ask nobody will ever see.
            # Fail closed, exactly as deny_all_approver does.
            logger.error("inbox_approver: could not queue %s: %s", request.tool_name, exc)
            return _deny(f"approval inbox unavailable ({exc}); failing closed", actor)

        logger.info(
            "inbox_approver: %s queued as %s (inbox=%s, timeout=%.0fs) — waiting",
            request.tool_name, item.item_id, item.inbox, timeout,
        )

        if deliver is not None:
            try:
                deliver(item)
            except Exception as exc:  # noqa: BLE001 — a mirror never decides
                logger.warning(
                    "inbox_approver: delivery of %s failed (item stays pending): %s",
                    item.item_id, exc,
                )

        settled = wait_for_resolution(
            item.item_id, timeout_seconds=timeout, poll_seconds=poll
        )
        if settled is not None:
            return _decide(settled, actor)

        # Deadline. Expire it here rather than leaving it pending for a sweep
        # that may never run — the agent is about to be told "no" and the store
        # must agree.
        expired = expire(
            item.item_id,
            reason=(
                f"expired: no answer within {timeout:g}s (inbox_approver deadline); "
                "a timeout is never an approval"
            ),
        )
        if expired is not None:
            return _decide(expired, actor)

        # expire() moved nothing: somebody settled it in the race between the
        # deadline passing and the UPDATE landing. Re-read rather than assume.
        final = get(item.item_id)
        if final is not None and not final.is_pending:
            return _decide(final, actor)
        return _deny(
            f"approval inbox item {item.item_id} EXPIRED — no answer within "
            f"{timeout:g}s, and it could not be settled; a timeout is never an approval",
            actor,
        )

    return approver


def inbox_approver(request: ApprovalRequest) -> ApprovalDecision:
    """Drop-in :data:`Approver` using the env-resolved defaults.

    ``build_approval_hook(approver=inbox_approver)`` — no gate change, no new
    signature, same policy, different destination.
    """
    return make_inbox_approver()(request)
