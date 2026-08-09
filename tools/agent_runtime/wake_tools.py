# CUI // SP-CTI
"""Agent-facing self-suspension tools (agov-wake-02).

Four tools that let an agent stop working and be resumed later, instead of
burning a turn — or a whole session — polling for something that has not
happened yet:

- ``sleep_for(seconds, note)``      — resume after a relative duration.
- ``sleep_until(when, note)``       — resume at a wall-clock UTC time.
- ``wake_on(job_id, note)``         — resume when that job completes.
- ``wake_on_event(event_key, note)``— resume when that event key fires.

Each writes exactly one row through the agov-wake-01 store
(:mod:`tools.agent_runtime.wake`) and returns a string telling the model to stop
and hand control back. None of them blocks, sleeps, or spins: the suspension is
a ``pending`` row, and something else in the platform — the cron reflex sweep,
``complete_job``, ``fire_event`` (agov-wake-03) — later promotes it. That is the
whole point. A tool that actually slept would hold the session open and consume
exactly the wall-clock time the agent was trying not to spend.

WHY THESE ARE ``reversible`` IN args/agent_approval_policy.yaml
---------------------------------------------------------------
``reversible`` is the strongest claim that file makes. It is not merely
"auto-allow": a tool listed there is also EXEMPT FROM CONTENT ESCALATION, which
the policy spells out as the assertion "this cannot act". These four qualify in
the literal sense the exemption was written for — they record an intention to
resume and have no other effect. The agent cannot reach anything through them:
the only free-text argument is ``note``, which is stored and shown to an
operator and never parsed, executed, or dispatched on.

The exemption is not a nicety here, it is required for the tools to work at all.
Content escalation runs the irreversible patterns against the flattened input of
every call, and the single most natural note an agent would ever write is the
one naming what it is waiting for::

    sleep_for(seconds=600, note="waiting for the git push to finish")

That matches the ``git\\s+push`` irreversible pattern and would halt for human
approval — on a tool whose entire purpose is to suspend when no human is
watching. It is the same category error ``read_file`` hit, and the same fix.

The tier is not the whole story for the BUNDLE path, and the difference is worth
knowing before wiring these anywhere. Through ``args/agent_toolsets.yaml``'s
``wake`` bundle the sag-safe-01 gate in :mod:`tools.agent_runtime.safety` runs
first, and its default ``manual`` mode asks about every non-read-only tool
whatever its tier — so a headless run gets ``blocked: operator denied`` on EOF.
The path that works unattended is the BUILT-IN starter toolset, where these are
also folded in and which is what :class:`~tools.agent_runtime.runtime.AgentRuntime`
loads by default; it dispatches handlers directly. Delivering that prompt
somewhere a human will actually see it is the INBOX epic (agov-inbox-*).

They are deliberately NOT in ``command_tools``. That list is for generic
executors, where the input IS the command; listing a non-executor there would
both re-enable escalation against ``note`` and, worse, let a *downgrade* pattern
lower some other call's tier. Neither applies to a tool that writes one row.

WHY ``is_read_only`` IS STILL FALSE
-----------------------------------
Two different flags, two different questions, and conflating them is how the
approval gate got its ``is_read_only`` warning in the first place.
``is_read_only`` in the tool SCHEMA is a factual claim about state mutation,
read by the agent loop to decide what may be dispatched in parallel — and these
write a row, so it is ``False``. The approval TIER is the operator's claim about
what a call can reach, and it lives in the operator's policy file precisely
because a schema flag is "an assertion by the caller rather than a fact". A wake
tool is honestly mutating and honestly unable to act. Both flags are correct.

BOUNDS
------
``sleep_for`` and ``sleep_until`` are capped at ``subsystems.wake.max_sleep_seconds``
(``args/agent_runtime.yaml``, env ``ICDEV_SAG_MAX_SLEEP_SECONDS``, default 24h)
and ``sleep_until`` additionally refuses a time that has already passed. The
store itself accepts both — a past ``fire_at`` there means "due on the next
sweep", which is a legitimate thing for an internal caller to want. It is not a
legitimate thing for a MODEL to ask for: a confused agent that sleeps until last
Tuesday, or for a year, has parked a session in a way nobody will notice, and
the reflex that fires wakes has no way to tell that apart from a deliberate
long timer. So the bound lives at the agent-facing edge, where the confusion is,
rather than in the store, where it would break the sweep's own semantics.

``wake_on``/``wake_on_event`` are NOT time-bounded. Their liveness belongs to
whoever fires the condition (agov-wake-03), and inventing a deadline here would
silently convert "wake me when PR #1342 is green" into "wake me in 24 hours",
which is a different and wrong answer.

Every handler matches the :data:`icdev.tools.llm.agent_loop.ToolHandler`
contract ``handler(input_dict, stop_event) -> str`` and returns an ``error: ...``
string rather than raising, so a missing store or an unmigrated database
degrades into something the model can read and adapt to.
"""
from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.agent_runtime.wake_tools")

ToolHandler = Callable[[dict[str, Any], "threading.Event | None"], str]

#: Cap on a self-scheduled sleep when the config layer is unavailable. A day is
#: long enough for the overnight case the card names and short enough that a
#: mistake surfaces within one working day.
DEFAULT_MAX_SLEEP_SECONDS = 86_400

#: Config key and env var for the cap. The env var wins (hgx-cfg-01).
MAX_SLEEP_CONFIG_KEY = "subsystems.wake.max_sleep_seconds"
MAX_SLEEP_ENV = "ICDEV_SAG_MAX_SLEEP_SECONDS"

#: Appended to every successful suspension. The model has to be told, in words,
#: that the correct next action is *nothing* — otherwise it treats the wake row
#: as bookkeeping and carries straight on with the turn it just suspended.
_HANDOFF = (
    "Stop working now and end your turn without calling another tool. You will "
    "be resumed when this wake fires. Do not poll and do not re-suspend."
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Session identity
# ---------------------------------------------------------------------------
def _session_id(explicit: Optional[str] = None) -> str:
    """Whose suspension this is.

    Resolved from the environment, mirroring
    :func:`tools.agent_runtime.approval_gate._session_id`, and deliberately NOT
    exposed as a tool parameter: a wake is a claim on a session's future turn,
    so an agent that could name the session would be able to schedule a
    resumption of somebody else's.
    """
    if explicit and str(explicit).strip():
        return str(explicit).strip()
    for key in ("ICDEV_SESSION_ID", "CLAUDE_SESSION_ID"):
        val = os.environ.get(key)
        if val and val.strip():
            return val.strip()
    return "unknown"


# ---------------------------------------------------------------------------
# Bound
# ---------------------------------------------------------------------------
def max_sleep_seconds() -> int:
    """The configured ceiling on a self-scheduled sleep, in seconds.

    Env var → ``args/agent_runtime.yaml`` → :data:`DEFAULT_MAX_SLEEP_SECONDS`,
    which is the layering every other SAG knob uses. A non-positive or
    unparseable value at any layer resolves to the default rather than to
    "unbounded" — a cap that a typo can switch off is not a cap.
    """
    try:
        from tools.agent_runtime.config import load_config

        return load_config().max_sleep_seconds
    except Exception as exc:  # noqa: BLE001 — config is a layer, not a dependency
        logger.debug("wake_tools: config layer unavailable: %s", exc)
    # No config layer: read the env var directly rather than dropping the cap.
    raw = os.environ.get(MAX_SLEEP_ENV)
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_MAX_SLEEP_SECONDS
    return value if value > 0 else DEFAULT_MAX_SLEEP_SECONDS


# ---------------------------------------------------------------------------
# Store access — resolved through the module object so a checkout without the
# store degrades to an error string, and so tests can patch the store.
# ---------------------------------------------------------------------------
def _store() -> Any:
    from tools.agent_runtime import wake

    return wake


def _describe(w: Any, condition: str) -> str:
    return f"suspended: {w.wake_id} ({w.kind}) until {condition}. {_HANDOFF}"


def _note(value: Any) -> str:
    return str(value or "").strip()


def _coerce_when(value: Any) -> datetime:
    """Parse an ISO-8601 string (or pass a datetime through) as UTC.

    Deliberately local rather than reusing the store's private ``_coerce_when``:
    the two answer different questions. The store normalises a value an internal
    caller already trusts; this one has to distinguish "the model sent garbage"
    from "the store is missing" so the error the model reads names the right
    problem. It is six lines, and the store re-normalises what it is handed
    anyway.
    """
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Tool bodies
# ---------------------------------------------------------------------------
def sleep_for(seconds: Any, note: str = "", *, session_id: str = "", conn: Any = None) -> str:
    """Suspend this session for a relative duration.

    Args:
        seconds: How long to sleep. Must be > 0 and <= :func:`max_sleep_seconds`.
        note: Why — shown to an operator inspecting pending wakes.
    """
    try:
        secs = float(seconds)
    except (TypeError, ValueError):
        return f"error: 'seconds' must be a number, got {seconds!r}"
    if secs != secs or secs in (float("inf"), float("-inf")):  # NaN / infinity
        return f"error: 'seconds' must be a finite number, got {seconds!r}"
    if secs <= 0:
        return (
            f"error: 'seconds' must be greater than 0, got {secs:g}. A wake is a "
            "suspension, not a no-op — just continue working instead."
        )
    cap = max_sleep_seconds()
    if secs > cap:
        return (
            f"error: refused — a sleep of {secs:g}s exceeds the configured maximum "
            f"of {cap}s ({MAX_SLEEP_CONFIG_KEY}). Sleep for less, or ask an "
            "operator to raise the cap."
        )
    try:
        w = _store().add_timer_in(
            _session_id(session_id), secs, note=_note(note), conn=conn
        )
    except Exception as exc:  # noqa: BLE001 — never raise into the agent loop
        return f"error: could not record the wake: {exc}"
    return _describe(w, f"{w.fire_at} (in {secs:g}s)")


def sleep_until(when: Any, note: str = "", *, session_id: str = "", conn: Any = None) -> str:
    """Suspend this session until a wall-clock UTC time.

    Args:
        when: ISO-8601 timestamp. A value with no timezone is read as UTC. Must
            be in the future and within :func:`max_sleep_seconds` from now.
        note: Why — shown to an operator inspecting pending wakes.
    """
    raw = when if isinstance(when, datetime) else str(when or "").strip()
    if not raw:
        return "error: 'when' is required (an ISO-8601 UTC timestamp)"
    try:
        target = _coerce_when(raw)
    except (TypeError, ValueError) as exc:
        return f"error: 'when' is not an ISO-8601 timestamp: {exc}"

    now = _utcnow()
    if target <= now:
        return (
            f"error: refused — {target.isoformat()} is in the past (now is "
            f"{now.isoformat()}). A wake cannot be scheduled backwards; use "
            "sleep_for(seconds) if you meant a short delay."
        )
    delta = (target - now).total_seconds()
    cap = max_sleep_seconds()
    if delta > cap:
        return (
            f"error: refused — {target.isoformat()} is {delta:.0f}s away, beyond "
            f"the configured maximum of {cap}s ({MAX_SLEEP_CONFIG_KEY}). Pick a "
            "nearer time, or ask an operator to raise the cap."
        )
    try:
        w = _store().add_timer(
            _session_id(session_id), target, note=_note(note), conn=conn
        )
    except Exception as exc:  # noqa: BLE001
        return f"error: could not record the wake: {exc}"
    return _describe(w, f"{w.fire_at} (in {delta:.0f}s)")


def wake_on(job_id: Any, note: str = "", *, session_id: str = "", conn: Any = None) -> str:
    """Suspend this session until a named job completes.

    Args:
        job_id: The job to wait on. Resumption happens when something calls
            ``wake.complete_job(job_id)`` — nothing here verifies the job exists,
            so a typo waits forever.
        note: Why — shown to an operator inspecting pending wakes.
    """
    jid = str(job_id or "").strip()
    if not jid:
        return "error: 'job_id' is required"
    try:
        w = _store().add_completion(
            _session_id(session_id), jid, note=_note(note), conn=conn
        )
    except Exception as exc:  # noqa: BLE001
        return f"error: could not record the wake: {exc}"
    return _describe(w, f"job {jid!r} completes")


def wake_on_event(event_key: Any, note: str = "", *, session_id: str = "", conn: Any = None) -> str:
    """Suspend this session until an event key fires.

    Args:
        event_key: An opaque key, by convention ``pr:1342:ci_green``. Resumption
            happens when something calls ``wake.fire_event(event_key)``; the key
            is matched exactly and is not parsed, so a key nobody emits waits
            forever.
        note: Why — shown to an operator inspecting pending wakes.
    """
    key = str(event_key or "").strip()
    if not key:
        return "error: 'event_key' is required"
    try:
        w = _store().add_event(
            _session_id(session_id), key, note=_note(note), conn=conn
        )
    except Exception as exc:  # noqa: BLE001
        return f"error: could not record the wake: {exc}"
    return _describe(w, f"event {key!r} fires")


# ---------------------------------------------------------------------------
# Agent-loop handlers + schemas
# ---------------------------------------------------------------------------
def _handle_sleep_for(inp: dict[str, Any], _stop: "threading.Event | None") -> str:
    return sleep_for(inp.get("seconds"), str(inp.get("note", "")))


def _handle_sleep_until(inp: dict[str, Any], _stop: "threading.Event | None") -> str:
    return sleep_until(inp.get("when"), str(inp.get("note", "")))


def _handle_wake_on(inp: dict[str, Any], _stop: "threading.Event | None") -> str:
    return wake_on(inp.get("job_id"), str(inp.get("note", "")))


def _handle_wake_on_event(inp: dict[str, Any], _stop: "threading.Event | None") -> str:
    return wake_on_event(inp.get("event_key"), str(inp.get("note", "")))


#: The four names, for the policy/registration tests and for callers that need
#: the set without importing the schemas.
TOOL_NAMES = ("sleep_for", "sleep_until", "wake_on", "wake_on_event")

_NOTE_PARAM = {
    "type": "string",
    "description": (
        "Short reason you are suspending, e.g. 'waiting for CI on PR #1342'. "
        "Stored and shown to an operator; never executed."
    ),
}

SCHEMAS: dict[str, dict[str, Any]] = {
    "sleep_for": {
        "type": "function",
        # Writes one row: honestly not read-only. The claim that this cannot ACT
        # is the `reversible` tier in args/agent_approval_policy.yaml, which is
        # the operator's file — see the module docstring.
        "is_read_only": False,
        "function": {
            "name": "sleep_for",
            "is_read_only": False,
            "description": (
                "Suspend yourself for a number of seconds and end your turn. "
                "Records a wake and returns immediately — it does NOT block, so "
                "no wall-clock time is spent holding the session open. Use this "
                "instead of polling or re-checking in a loop. After calling it, "
                "stop and produce no further tool calls."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "seconds": {
                        "type": "number",
                        "description": (
                            "How long to sleep. Must be greater than 0 and within "
                            "the configured maximum (24 hours by default); a "
                            "longer or negative value is refused."
                        ),
                    },
                    "note": _NOTE_PARAM,
                },
                "required": ["seconds"],
            },
        },
    },
    "sleep_until": {
        "type": "function",
        "is_read_only": False,
        "function": {
            "name": "sleep_until",
            "is_read_only": False,
            "description": (
                "Suspend yourself until a specific UTC time and end your turn. "
                "Records a wake and returns immediately — it does NOT block. A "
                "time in the past, or further ahead than the configured maximum, "
                "is refused. After calling it, stop and produce no further tool "
                "calls."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "when": {
                        "type": "string",
                        "description": (
                            "ISO-8601 timestamp, e.g. '2026-08-10T06:00:00+00:00'. "
                            "A value with no timezone is read as UTC."
                        ),
                    },
                    "note": _NOTE_PARAM,
                },
                "required": ["when"],
            },
        },
    },
    "wake_on": {
        "type": "function",
        "is_read_only": False,
        "function": {
            "name": "wake_on",
            "is_read_only": False,
            "description": (
                "Suspend yourself until a named job completes, then end your "
                "turn. Records a wake and returns immediately — it does NOT "
                "block. Nothing checks that the job exists, so a wrong id waits "
                "forever. After calling it, stop and produce no further tool "
                "calls."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "Id of the job to wait for.",
                    },
                    "note": _NOTE_PARAM,
                },
                "required": ["job_id"],
            },
        },
    },
    "wake_on_event": {
        "type": "function",
        "is_read_only": False,
        "function": {
            "name": "wake_on_event",
            "is_read_only": False,
            "description": (
                "Suspend yourself until an event key fires, then end your turn. "
                "Records a wake and returns immediately — it does NOT block. The "
                "key is matched exactly (convention: 'pr:1342:ci_green'), so a "
                "key nobody emits waits forever. After calling it, stop and "
                "produce no further tool calls."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "event_key": {
                        "type": "string",
                        "description": (
                            "Exact event key to wait for, e.g. 'pr:1342:ci_green'."
                        ),
                    },
                    "note": _NOTE_PARAM,
                },
                "required": ["event_key"],
            },
        },
    },
}

HANDLERS: dict[str, ToolHandler] = {
    "sleep_for": _handle_sleep_for,
    "sleep_until": _handle_sleep_until,
    "wake_on": _handle_wake_on,
    "wake_on_event": _handle_wake_on_event,
}


def build_wake_toolset() -> "tuple[list[dict[str, Any]], dict[str, ToolHandler]]":
    """Return ``(tools, handlers)`` for the four self-suspension tools."""
    return [SCHEMAS[n] for n in HANDLERS], dict(HANDLERS)


def wake_tool_names() -> list[str]:
    """Return the sorted names of the self-suspension tools."""
    return sorted(HANDLERS)
