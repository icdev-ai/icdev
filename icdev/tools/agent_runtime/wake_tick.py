# CUI // SP-CTI
"""Drain due wakes and resume the sessions waiting on them (agov-wake-03).

:mod:`tools.agent_runtime.wake` records a self-suspension and evaluates its
condition; **this** module is what actually happens when the condition is met.
It is a library with one entry point, :func:`run_due_wakes`, deliberately shaped
like :func:`tools.agent_runtime.cron.run_due_jobs` — because the caller is the
same one: :mod:`tools.genesis.reflexes.agent_cron_reflex`, ticked by the Genesis
daemon on the cadence already declared in ``args/genesis_config.yaml``.

**There is no daemon here, and that is the design.** ICDEV already runs three
long-lived processes (the Genesis daemon, the kanban scheduler, ``pr_watcher``).
A fourth would be a fourth thing that can die unnoticed — and "nobody noticed for
four days" is the exact failure this epic exists to fix. Wakes therefore ride the
cadence of a loop that is already watched.

**Claim first, deliver second.**

The order in :func:`run_due_wakes` is not interchangeable::

    if mark_fired(wake_id):      # the database picks exactly one winner
        resumer(wake)            # only the winner delivers

Two Genesis ticks can overlap (a slow resume outlasting the one-minute cadence
is enough), and both would see the same ``due`` row. ``mark_fired`` is a
conditional ``UPDATE ... WHERE state = 'due'``, so exactly one caller gets
``True`` and the loser silently skips. Delivering first and marking after would
resume the same agent twice from one suspension.

The cost of that ordering is honest and stated: if delivery fails *after* the
claim, the wake stays ``fired`` and is not retried. Resumption is **at most
once**, never twice. A failed delivery is logged at WARNING and counted in the
tick result (``failed``) rather than silently dropped — an agent that suspended
and never came back is precisely the invisible failure this epic is about, so it
has to show up in the reflex metric.

**What "resume" means.** The default resumer appends to the session's existing
mid-run message queue (:func:`tools.airgap.hook_compat.queue_message`), which is
already keyed by session/task id and already drained by a running kanban session.
It does not *start* a process — nothing in ICDEV does that for an arbitrary
session id today, and inventing a launcher here would be the daemon this task
was told not to write. ``resumer`` is injectable for exactly that reason: when
agov-wake-02 lands the agent-side tools, or an operator wants delivery through
the approval inbox, it is a one-argument swap with no change here.

Library — no CLI. Import it::

    from tools.agent_runtime.wake_tick import run_due_wakes
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Optional, Tuple

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.agent_runtime.wake_tick")

#: Delivery cap for one tick. A backlog is drained across ticks rather than in
#: one long pass, so a pile-up of due wakes cannot hold the Genesis daemon (and
#: therefore every other reflex) inside this one.
DEFAULT_MAX_PER_TICK = 50

#: ``(wake) -> (delivered, detail)``. ``detail`` is free text for the log/result.
Resumer = Callable[[Any], Tuple[bool, str]]


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def resume_message(wake: Any) -> str:
    """The text delivered to a resumed session.

    Carries the wake id, the kind, and the condition that was met, so a session
    reading its queue can tell "PR #1342 went green" from "the 30-minute timer
    elapsed" without another lookup.
    """
    condition = getattr(wake, "condition", None) or ""
    note = (getattr(wake, "note", "") or "").strip()
    text = (
        f"[wake:{getattr(wake, 'wake_id', '?')}] Resuming: "
        f"{getattr(wake, 'kind', '?')} wake fired"
    )
    if condition:
        text += f" ({condition})"
    text += "."
    if note:
        text += f" {note}"
    return text


def deliver_resume(wake: Any) -> Tuple[bool, str]:
    """Default resumer — append the resume to the session's message queue.

    Best-effort by contract: it reports failure, it never raises, because the
    caller is a reflex tick draining a batch and one undeliverable wake must not
    cost the others their delivery.
    """
    session_id = str(getattr(wake, "session_id", "") or "").strip()
    if not session_id:
        return False, "wake has no session_id"
    try:
        from tools.airgap.hook_compat import queue_message
    except Exception as exc:  # noqa: BLE001
        return False, f"message queue unavailable: {exc}"
    try:
        result = queue_message(session_id, resume_message(wake), sender="wake")
    except Exception as exc:  # noqa: BLE001
        return False, f"queue_message failed: {exc}"
    if not (result or {}).get("queued"):
        return False, str((result or {}).get("error") or "queue_message refused the message")
    return True, str((result or {}).get("path") or "queued")


def run_due_wakes(
    *,
    now: Optional[datetime] = None,
    conn=None,
    resumer: Optional[Resumer] = None,
    limit: Optional[int] = None,
) -> dict:
    """Fire every due wake once and resume its session.

    Returns ``{due, fired, resumed, failed, skipped, checked_at, wakes}`` —
    ``fired`` counts the wakes THIS call claimed, ``skipped`` the ones a
    concurrent tick claimed first, and ``resumed``/``failed`` split the claimed
    ones by whether delivery succeeded.

    Never raises. The caller is a Genesis reflex, and a raise there wedges the
    daemon for every other reflex on the cadence.
    """
    deliver = resumer or deliver_resume
    cap = int(limit if limit is not None else DEFAULT_MAX_PER_TICK)
    result = {
        "due": 0,
        "fired": 0,
        "resumed": 0,
        "failed": 0,
        "skipped": 0,
        "checked_at": _utcnow_iso(),
        "wakes": [],
    }

    try:
        from tools.agent_runtime.wake import due as due_wakes
        from tools.agent_runtime.wake import mark_fired
    except Exception as exc:  # noqa: BLE001
        logger.warning("wake_tick: wake store unavailable: %s", exc)
        result["error"] = f"wake store unavailable: {exc}"
        return result

    try:
        batch = due_wakes(now=now, conn=conn)
    except Exception as exc:  # noqa: BLE001 — due() already degrades, belt and braces
        logger.warning("wake_tick: could not read due wakes: %s", exc)
        result["error"] = str(exc)
        return result

    result["due"] = len(batch)
    for wake in batch[:cap]:
        wake_id = getattr(wake, "wake_id", "")
        try:
            claimed = mark_fired(wake_id, conn=conn)
        except Exception as exc:  # noqa: BLE001
            logger.warning("wake_tick: could not fire %s: %s", wake_id, exc)
            result["failed"] += 1
            result["wakes"].append(
                {"wake_id": wake_id, "fired": False, "resumed": False, "detail": str(exc)}
            )
            continue
        if not claimed:
            # Another tick got there first. Not an error — it is the guarantee.
            result["skipped"] += 1
            continue

        result["fired"] += 1
        try:
            delivered, detail = deliver(wake)
        except Exception as exc:  # noqa: BLE001 — an injected resumer is foreign code
            delivered, detail = False, f"resumer raised: {exc}"
        if delivered:
            result["resumed"] += 1
        else:
            result["failed"] += 1
            logger.warning(
                "wake_tick: %s fired but was NOT delivered to session %s: %s",
                wake_id, getattr(wake, "session_id", "?"), detail,
            )
        result["wakes"].append(
            {
                "wake_id": wake_id,
                "session_id": getattr(wake, "session_id", ""),
                "kind": getattr(wake, "kind", ""),
                "fired": True,
                "resumed": bool(delivered),
                "detail": detail,
            }
        )

    if result["due"] > cap:
        logger.info(
            "wake_tick: %d wakes due, delivered %d this tick (cap %d) — "
            "the rest are still 'due' and drain on the next tick",
            result["due"], cap, cap,
        )
    return result
