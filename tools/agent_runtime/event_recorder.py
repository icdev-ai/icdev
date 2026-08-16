# CUI // SP-CTI
"""Wire the append-only event log to a real agent turn — hcx-evt-02.

hcx-evt-01 landed the writer (:mod:`tools.agent_runtime.event_log`) and nothing
called it. This module is the consumer: one :class:`TurnRecorder` per
:meth:`tools.agent_runtime.runtime.AgentRuntime.run_turn` call, plugged into the
four lifecycle hooks ``run_agent_loop`` already exposes. No new hook machinery
was added to the loop, and none was needed.

    turn_start          run_turn, before the loop is entered
    assistant_message   on_turn          (once per loop iteration)
    tool_call           on_pre_tool_use  (once per dispatched tool call)
    tool_result         on_post_tool_use (once per tool call, always)
    turn_end            on_stop          (all exit paths) + a finally backstop

``request_context`` is the one member of the vocabulary this module does not
emit. It belongs to the injection sites — ``project_context``, ``goal_context``,
``profile_memory`` — and lands in hcx-evt-03.

THREE THINGS THIS MODULE IS NOT ALLOWED TO DO
=============================================

**It cannot allow a tool call.** :meth:`TurnRecorder.on_pre_tool_use` returns
``None`` on every path, including the failure paths — the signature has no other
return value in it at all. ``run_agent_loop`` composes the caller's pre-tool hook
*after* the approval gate (``_compose_pre_tool_hooks`` at agent_loop.py:1395)
and the first non-empty block message wins, so a hook running second cannot
undo a denial even in principle. That ordering is deliberate and is the same
rule DSH states as "guards can only deny, never force-allow"; nothing here
changes it, and the composition order is not something this module passes,
chooses, or can reach.

**It cannot kill a turn.** Every emit is wrapped: a failed append degrades to a
warning and the turn continues. This is the one place in the hcx-evt stack where
that is correct — ``event_log.append`` itself raises by design, because a writer
that swallows its own INSERT is how ``module_budget_usage`` reported success
while holding zero rows. The distinction is *who is asking*: a caller writing an
audit row wants the failure, an agent turn already in flight does not lose its
work because the audit log is unreachable. So the raise stays where it is and
the swallow lives here, once, visibly, with a count you can read back
(:attr:`TurnRecorder.failures`) rather than scattered through the runtime.

**It does not replace ``messages_json``.** ``agent_loop_sessions`` remains the
resume path: it works, it is tested, and resume is not what this log is for.
This is the audit / fork / replay path, running alongside. Deriving the message
list *from* the log — DSH's surface projection — is deferred to hcx-evt-05.

WHICH ``session_id``
====================

The chat context id (``chat_contexts.id``), not ``AgentLoopResult.session_id``.
``run_agent_loop`` mints a fresh UUID on **every** call (agent_loop.py:1400) even
when resuming, so keying on it would make every user message its own one-turn
"session" — and hcx-evt-05 forks at a ``seq`` inside a multi-turn session, with
a boundary landing mid-turn as the case it must refuse. The context id is also
the id an operator already holds: ``icdev chat --resume <ctx-id>``,
``icdev sessions export <ctx-id>``.

The per-turn loop identity is not lost, it moves to the ``correlation_id``
column: :meth:`TurnRecorder.for_turn` mints one id per turn and the runtime
passes that same id to ``run_agent_loop(correlation_id=...)``, where it becomes
``AgentLoopResult.trace_id`` and the ``agent.turn`` OTel span. So "every event in
this turn" is a column filter and needs no ordinal — which matters because the
obvious ordinal, ``RuntimeSession.turn_count``, resets to 0 on ``--resume`` and
would silently number a resumed session's second turn as its first.
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Any, Callable

from tools.agent_runtime.event_log import EVENT_TYPES, RetentionPolicy, append
from tools.logging.icdev_logger import get_logger

logger = get_logger("agent_runtime.event_recorder")

#: Kill switch. ``0``/``false``/``no``/``off`` stands recording down for the
#: process; anything else (including unset) records. An operator disabling the
#: audit log should be visible in the process environment — the same reason
#: ``ICDEV_PRETOOLUSE_ENFORCE`` exists rather than a shell operator buried in a
#: JSON string.
RECORDING_ENV = "ICDEV_AGENT_EVENT_RECORDING"

_FALSE = {"0", "false", "no", "off"}

#: Where a ``tool_call`` event was observed. ``pre_tool_use`` is the normal path.
#: ``post_tool_use`` is the reconstruction described on
#: :meth:`TurnRecorder.on_post_tool_use` — carried in the payload so a reader can
#: tell the two apart rather than having to assume.
OBSERVED_PRE = "pre_tool_use"
OBSERVED_POST = "post_tool_use"


def recording_enabled() -> bool:
    """True unless an operator stood the log down through :data:`RECORDING_ENV`."""
    return (os.environ.get(RECORDING_ENV) or "").strip().lower() not in _FALSE


def _call_key(name: str, tool_input: Any) -> str:
    """Identity of one tool call, for pairing a pre-hook with its post-hook.

    Sorted keys because dict order is a property of how the input was built and
    never of what it means — the same argument ``compute_payload_hash`` makes one
    layer down.
    """
    try:
        rendered = json.dumps(tool_input, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001 — an unrenderable input still needs a key
        rendered = repr(tool_input)
    return f"{name}\x00{rendered}"


class TurnRecorder:
    """Records one agent turn into ``agent_session_events``.

    Construct one per turn (:meth:`for_turn`), hand its four bound hooks to
    ``run_agent_loop``, and call :meth:`turn_start` before and :meth:`turn_end`
    after. Every method is safe to call when the log is unreachable.

    Args:
        session_id: The chat context id. See the module docstring for why this
            is not ``AgentLoopResult.session_id``.
        correlation_id: The per-turn id, also passed to ``run_agent_loop`` as its
            ``correlation_id`` so the two records join.
        tenant_id / classification: Passed through to
            :func:`~tools.agent_runtime.event_log.append`, which falls back to
            the ambient :class:`SecurityContext` when they are ``None``.
        policy: A pinned :class:`RetentionPolicy`. ``None`` (the default) makes
            ``append`` load ``args/agent_event_log.yaml`` per event.
        enabled: Force recording on/off. ``None`` reads :data:`RECORDING_ENV`.
        appender: The write function, for tests. Defaults to ``event_log.append``.

    Attributes:
        recorded: Events successfully appended.
        failures: Emits that could not be written. Non-zero means the log has
            holes — which is a fact a reader needs, and the reason this is a
            counter and not a discarded exception.
    """

    def __init__(
        self,
        session_id: str,
        *,
        correlation_id: str = "",
        tenant_id: str | None = None,
        classification: str | None = None,
        policy: RetentionPolicy | None = None,
        enabled: bool | None = None,
        appender: Callable[..., Any] | None = None,
    ) -> None:
        self.session_id = session_id or ""
        self.correlation_id = correlation_id or ""
        self.tenant_id = tenant_id
        self.classification = classification
        self.policy = policy
        self.enabled = recording_enabled() if enabled is None else bool(enabled)
        self._append = appender or append
        self.recorded = 0
        self.failures = 0
        self._ended = False
        # Multiset, not a set: a turn may legitimately call one tool twice with
        # identical inputs, and pairing by presence alone would treat the second
        # post-hook as unpaired and synthesise a tool_call that already exists.
        self._awaiting_result: dict[str, int] = {}

    # -- construction ------------------------------------------------------

    @classmethod
    def for_turn(
        cls,
        session_id: str,
        *,
        tenant_id: str | None = None,
        classification: str | None = None,
        enabled: bool | None = None,
        appender: Callable[..., Any] | None = None,
    ) -> "TurnRecorder":
        """A recorder for one turn, with a freshly minted ``correlation_id``."""
        return cls(
            session_id,
            correlation_id=f"turn-{uuid.uuid4().hex[:16]}",
            tenant_id=tenant_id,
            classification=classification,
            enabled=enabled,
            appender=appender,
        )

    # -- the one write path ------------------------------------------------

    def _emit(self, event_type: str, payload: Any) -> Any:
        """Append one event. Returns the :class:`Event`, or ``None`` on failure.

        The only place this module catches. ``Exception`` and not
        ``BaseException``: a turn is interruptible by design (``stop_event``,
        Ctrl-C in the REPL), so ``KeyboardInterrupt`` and ``SystemExit`` must
        still travel — swallowing those would make the audit log the reason an
        operator cannot stop a run.

        The first failure is a WARNING with the whole reason; the rest are DEBUG.
        A missing table raises identically on every event, and 40 copies of one
        line per turn trains a reader to ignore the line. Retrying is deliberate
        too — a recorder that disabled itself on one transient error would go on
        reporting a clean turn while writing nothing for the rest of the session.
        """
        if not self.enabled or not self.session_id:
            return None
        if event_type not in EVENT_TYPES:  # pragma: no cover — guarded by callers
            raise ValueError(f"unknown event_type {event_type!r}")
        try:
            event = self._append(
                self.session_id,
                event_type,
                payload,
                correlation_id=self.correlation_id,
                tenant_id=self.tenant_id,
                classification=self.classification,
                policy=self.policy,
            )
        except Exception as exc:  # noqa: BLE001 — a turn survives an unwritable log
            self.failures += 1
            if self.failures == 1:
                logger.warning(
                    "event_recorder: could not record %s for session %s (%s) — the "
                    "turn continues and this session's event log will have holes",
                    event_type, self.session_id, exc,
                )
            else:
                logger.debug(
                    "event_recorder: could not record %s for session %s (%s); "
                    "%d failures so far",
                    event_type, self.session_id, exc, self.failures,
                )
            return None
        self.recorded += 1
        return event

    # -- turn boundaries ---------------------------------------------------

    def turn_start(self, user_input: str, **meta: Any) -> Any:
        """Record the start of a turn. Call before entering the loop.

        Not driven by ``on_turn``: that hook fires *after* a model response, so
        the earliest it could report a turn's start is once the turn is already
        one LLM call old — and the user's own input, the thing the turn exists
        to answer, would never appear in the log at all.
        """
        payload = {"user_input": user_input, "correlation_id": self.correlation_id}
        payload.update(meta)
        return self._emit("turn_start", payload)

    def turn_end(self, result: Any = None, *, reason: str = "") -> Any:
        """Record the end of a turn. Idempotent — only the first call writes.

        Idempotent because it has two callers by design: :meth:`on_stop`, which
        ``run_agent_loop`` fires on every exit path it controls, and the
        runtime's ``finally``, which covers the path it does not — an exception
        out of the loop itself, where ``on_stop`` is never reached. A turn that
        ended must leave exactly one ``turn_end``, whichever of those two ran.
        """
        if self._ended:
            return None
        self._ended = True
        payload: dict[str, Any] = {
            "correlation_id": self.correlation_id,
            # The loop's own per-run id, recorded here rather than in
            # turn_start because it does not exist until the loop returns.
            "loop_session_id": _attr(result, "session_id", ""),
            "trace_id": _attr(result, "trace_id", ""),
            "truncation_reason": reason or _attr(result, "truncation_reason", ""),
            "result_subtype": _attr(result, "result_subtype", ""),
            "done": bool(_attr(result, "done", False)),
            "truncated": bool(_attr(result, "truncated", False)),
            "turns": _attr(result, "turns", 0),
            "final_content": _attr(result, "final_content", ""),
            "input_tokens": _attr(result, "total_input_tokens", 0),
            "output_tokens": _attr(result, "total_output_tokens", 0),
            "cost_usd": _attr(result, "total_cost_usd", 0.0),
            "elapsed_seconds": _attr(result, "elapsed_seconds", 0.0),
        }
        return self._emit("turn_end", payload)

    # -- the four lifecycle hooks -----------------------------------------

    def on_turn(self, turn: int, response: Any, messages: Any = None) -> None:
        """``on_turn`` → one ``assistant_message`` per loop iteration.

        ``messages`` is the full history and is deliberately NOT stored: it is
        the blob this table exists to stop being the only artifact, and writing
        it once per iteration would make the append-only log a slower copy of
        ``messages_json`` rather than a decomposition of it. The response is the
        delta, and the delta is what a fork replays.
        """
        tool_calls = _attr(response, "tool_calls", None) or []
        self._emit(
            "assistant_message",
            {
                "iteration": turn,
                "content": _attr(response, "content", "") or "",
                "stop_reason": _attr(response, "stop_reason", "") or "",
                "model_id": _attr(response, "model_id", "") or "",
                "provider": _attr(response, "provider", "") or "",
                "input_tokens": _attr(response, "input_tokens", 0) or 0,
                "output_tokens": _attr(response, "output_tokens", 0) or 0,
                "tool_calls": [
                    {
                        "id": tc.get("id", "") if isinstance(tc, dict) else "",
                        "name": tc.get("name", "") if isinstance(tc, dict) else str(tc),
                        "input": tc.get("input", {}) if isinstance(tc, dict) else {},
                    }
                    for tc in tool_calls
                ],
            },
        )

    def on_pre_tool_use(self, name: str, tool_input: dict[str, Any]) -> None:
        """``on_pre_tool_use`` → one ``tool_call``. NEVER blocks, NEVER allows.

        Returns ``None`` unconditionally, on every path. A logging hook that
        could return a string would be a logging hook that can deny, and one
        composed ahead of a gate that returned ``None`` would be a logging hook
        that can... still not allow anything, because
        ``_compose_pre_tool_hooks`` takes the first non-empty message and this
        one is composed second regardless. Both halves of that are worth stating
        because the ordering lives in the loop, not here, and a future caller
        reading only this file should not have to infer which way it cuts.
        """
        key = _call_key(name, tool_input)
        self._awaiting_result[key] = self._awaiting_result.get(key, 0) + 1
        self._emit(
            "tool_call",
            {"name": name, "input": tool_input, "observed": OBSERVED_PRE},
        )
        return None

    def on_post_tool_use(
        self, name: str, tool_input: dict[str, Any], result_text: str, is_error: bool
    ) -> None:
        """``on_post_tool_use`` → one ``tool_result``, and a ``tool_call`` if the
        pre-hook never ran for it.

        The two hooks do not cover the same set of calls, and the asymmetry runs
        the dangerous way. ``on_post_tool_use`` fires for **every** entry in
        ``tool_calls`` (agent_loop.py:1903, inside the append-results loop);
        ``on_pre_tool_use`` is skipped for a tool with no registered handler
        (:1726) and — this is the one that matters — its return is discarded the
        moment the approval gate ahead of it returns a block message, because
        the composed chain stops at the first non-empty answer.

        So the calls with NO ``tool_call`` event would be exactly the denied ones
        and the unregistered ones: the two an audit reader most wants. Rather
        than change the gate ordering to fix that — which is the one thing this
        card forbids, and rightly — the missing event is reconstructed here from
        the post-hook's own arguments, which carry the identical ``name`` and
        ``input``. It is tagged ``observed: post_tool_use`` so it is never
        mistaken for a call the pre-hook actually saw dispatched.
        """
        key = _call_key(name, tool_input)
        outstanding = self._awaiting_result.get(key, 0)
        if outstanding > 0:
            self._awaiting_result[key] = outstanding - 1
        else:
            self._emit(
                "tool_call",
                {
                    "name": name,
                    "input": tool_input,
                    "observed": OBSERVED_POST,
                    "note": (
                        "reconstructed at post_tool_use: the pre-tool hook did not "
                        "run for this call (blocked by the approval gate, or no "
                        "handler registered)"
                    ),
                },
            )
        self._emit(
            "tool_result",
            {
                "name": name,
                "input": tool_input,
                "result": result_text,
                "is_error": bool(is_error),
            },
        )

    def on_stop(self, result: Any) -> None:
        """``on_stop`` → ``turn_end``, carrying the loop's truncation reason."""
        self.turn_end(result)

    # -- introspection -----------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """What this recorder actually wrote — for ``/usage`` and for tests."""
        return {
            "session_id": self.session_id,
            "correlation_id": self.correlation_id,
            "enabled": self.enabled,
            "recorded": self.recorded,
            "failures": self.failures,
            "ended": self._ended,
        }


def _attr(obj: Any, name: str, default: Any) -> Any:
    """``getattr`` that also reads a dict, and never raises.

    ``on_turn``/``on_stop`` are handed provider response objects and
    :class:`AgentLoopResult`s, but a caller (or a test) may pass a plain dict,
    and a hook is the wrong place to discover that difference.
    """
    if obj is None:
        return default
    if isinstance(obj, dict):
        value = obj.get(name, default)
    else:
        value = getattr(obj, name, default)
    return default if value is None else value
