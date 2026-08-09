# CUI // SP-CTI
"""Event keys the platform fires at wakes (agov-wake-03).

``wake_on_event("pr:1342:ci_green")`` is worth nothing unless something in the
platform actually says ``pr:1342:ci_green`` when it happens. This module is that
vocabulary plus the one best-effort emit path, so the two producers wired in this
task — :mod:`tools.ci.pr_watcher` and the kanban state machine — each add a
single call rather than each inventing a key format.

**Key shape:** ``<subject>:<id>:<event>``. Opaque to
:mod:`tools.agent_runtime.wake`, which stores and compares the whole string; the
convention lives here, with the emitters, where it can be kept in one piece.

======================================  ====================================
``pr:<number>:ci_green``                CI passed and the PR is mergeable
``pr:<number>:ci_failed``               a required check failed
``pr:<number>:merge_conflict``          the branch no longer merges cleanly
``pr:<number>:changes_requested``       a reviewer asked for changes
``pr:<number>:merged`` / ``:closed``    the PR left the open set
``task:<id>:done`` / ``:<status>``      a kanban task reached that status
======================================  ====================================

**Re-emission is safe, so nothing here deduplicates.** ``pr_watcher`` polls the
same green PR every cycle and will emit ``pr:N:ci_green`` every time.
:func:`tools.agent_runtime.wake.fire_event` only promotes wakes that are still
``pending``, so the second and later emissions promote nothing. That is what
makes a poll loop an acceptable event source at all — the store, not the caller,
owns "has this already fired".

**Emitting never raises.** Both call sites are load-bearing loops that must not
acquire a new way to fail: a PR merge or a task transition is not allowed to
break because an agent's wake could not be promoted. Failures are logged and
returned, never propagated.

Library — no CLI. Import it::

    from tools.agent_runtime.wake_signals import emit_pr_state, emit_task_status
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.agent_runtime.wake_signals")

SUBJECT_PR = "pr"
SUBJECT_TASK = "task"

#: kanban classification (``KanbanState`` value) -> PR event name. Keyed by the
#: enum's *value* on purpose: :mod:`tools.kanban.state_machine` imports this
#: module, so importing ``KanbanState`` back would be a cycle.
_PR_EVENT_FOR_CLASSIFICATION: Dict[str, str] = {
    "done": "ci_green",
    "ci_failed": "ci_failed",
    "merge_conflict": "merge_conflict",
    "changes_requested": "changes_requested",
}

#: ``gh pr view`` state -> PR event name. ``OPEN`` yields nothing: "still open"
#: is not an event, it is the absence of one.
_PR_EVENT_FOR_STATE: Dict[str, str] = {
    "MERGED": "merged",
    "CLOSED": "closed",
}

_PR_NUMBER_RE = re.compile(r"/pull/(\d+)")


def pr_number(pr_url_or_number: Any) -> Optional[str]:
    """The PR number from a URL, a ``#1342``, or a bare number. ``None`` if absent.

    Returning ``None`` rather than guessing matters: a key built from an
    unparsed URL would be a key no agent could ever have subscribed to, and it
    would fire silently forever.
    """
    raw = str(pr_url_or_number or "").strip()
    if not raw:
        return None
    match = _PR_NUMBER_RE.search(raw)
    if match:
        return match.group(1)
    stripped = raw.lstrip("#")
    return stripped if stripped.isdigit() else None


def key(subject: str, ident: Any, event: str) -> str:
    """Build one event key. The single place the ``a:b:c`` shape is written."""
    return f"{subject}:{ident}:{event}"


def pr_event_keys(
    pr_url_or_number: Any,
    *,
    classification: Any = None,
    pr_state: Any = None,
) -> List[str]:
    """Every key implied by one observation of a PR. Pure — no DB, no network.

    ``classification`` is a :class:`~tools.kanban.state_machine.KanbanState` (or
    its value); ``pr_state`` is ``gh pr view``'s ``state`` field. Both are
    optional, and an observation that implies nothing returns ``[]``.
    """
    number = pr_number(pr_url_or_number)
    if number is None:
        return []

    keys: List[str] = []
    cls = getattr(classification, "value", classification)
    event = _PR_EVENT_FOR_CLASSIFICATION.get(str(cls or "").lower())
    if event:
        keys.append(key(SUBJECT_PR, number, event))

    state_event = _PR_EVENT_FOR_STATE.get(str(pr_state or "").upper())
    if state_event:
        keys.append(key(SUBJECT_PR, number, state_event))

    # dict.fromkeys, not set(): a caller reading the log wants a stable order.
    return list(dict.fromkeys(keys))


def task_event_keys(task_id: Any, status: Any) -> List[str]:
    """The key implied by a kanban task reaching ``status``. Pure.

    One key, ``task:<id>:<status>``, for every status rather than only ``done``:
    "wake me if this task fails" is the same waiting problem as "wake me when it
    finishes", and a status vocabulary that excluded it would push callers back
    to polling.
    """
    ident = str(task_id or "").strip()
    state = str(getattr(status, "value", status) or "").strip().lower()
    if not ident or not state:
        return []
    return [key(SUBJECT_TASK, ident, state)]


def emit(keys: Iterable[str], *, conn=None) -> Dict[str, Any]:
    """Fire every key at the wake store. Best-effort; never raises.

    Returns ``{"keys": [...], "promoted": [...], "error": str | absent}`` where
    ``promoted`` holds the wake ids THIS call moved ``pending -> due``. An empty
    ``promoted`` is the normal case — most events have no listener.
    """
    key_list = [str(k) for k in keys if str(k or "").strip()]
    out: Dict[str, Any] = {"keys": key_list, "promoted": []}
    if not key_list:
        return out

    try:
        from tools.agent_runtime.wake import fire_event
    except Exception as exc:  # noqa: BLE001 — the wake store is optional here
        logger.debug("wake_signals: wake store unavailable, dropping %s: %s", key_list, exc)
        out["error"] = f"wake store unavailable: {exc}"
        return out

    own_conn = None
    if conn is None:
        # One connection for the whole batch rather than one per key: pr_watcher
        # emits several keys per PR per poll cycle. A failure to get one is not
        # fatal — fire_event opens its own.
        try:
            from tools.db.storage import get_connection

            own_conn = get_connection()
        except Exception as exc:  # noqa: BLE001
            logger.debug("wake_signals: no shared connection (%s); per-key it is", exc)

    try:
        for event_key in key_list:
            try:
                out["promoted"].extend(fire_event(event_key, conn=conn or own_conn))
            except Exception as exc:  # noqa: BLE001
                logger.warning("wake_signals: fire_event(%r) failed: %s", event_key, exc)
                out["error"] = str(exc)
    finally:
        if own_conn is not None:
            try:
                own_conn.close()
            except Exception:  # noqa: BLE001
                pass

    if out["promoted"]:
        logger.info(
            "wake_signals: %s promoted %d wake(s) to due: %s",
            key_list, len(out["promoted"]), ", ".join(out["promoted"]),
        )
    return out


def emit_pr_state(
    pr_url_or_number: Any,
    *,
    classification: Any = None,
    pr_state: Any = None,
    conn=None,
) -> Dict[str, Any]:
    """:func:`pr_event_keys` + :func:`emit`. The pr_watcher call site."""
    return emit(
        pr_event_keys(pr_url_or_number, classification=classification, pr_state=pr_state),
        conn=conn,
    )


def emit_task_status(task_id: Any, status: Any, *, conn=None) -> Dict[str, Any]:
    """:func:`task_event_keys` + :func:`emit`. The kanban call site."""
    return emit(task_event_keys(task_id, status), conn=conn)
