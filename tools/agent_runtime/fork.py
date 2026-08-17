#!/usr/bin/env python3
# CUI // SP-CTI
"""Forking a session at a ``seq`` — ``icdev chat --fork <id> --at <n>`` (hcx-evt-05).

WHAT DID NOT EXIST
==================
ICDEV had no branching primitive. ``run_agent_loop(parent_session_id=...)``
records sub-agent LINEAGE — "this run was spawned by that run" — which is a
different relation from "this run is that run up to turn N, and then something
else". The data a fork needs was also gone: ``agent_loop_sessions.messages_json``
is UPSERT-overwritten every turn, so turn N no longer exists once turn N+1 has
been written.

hcx-evt-01/02 fixed the data half. ``agent_session_events`` is append-only and
``seq`` is monotonic per session under a UNIQUE ``(session_id, seq)`` index, so
"the session as it stood at seq 42" is a ``WHERE seq <= 42`` and not a
reconstruction. This module is the other half: the projection back to a message
list, the boundary rules, and the seeding of a new session from a prefix.

THE BOUNDARY IS RESOLVED AGAINST THE LOG, NEVER AGAINST ``messages_json``
========================================================================
``--at`` names a ``seq`` in ``agent_session_events``. A number that names no
event is REFUSED rather than clamped to the nearest one: an operator who
mistypes a boundary and silently gets a different fork has been given a wrong
answer that looks like a right one, and the log is the only place where the
number they typed means anything at all.

A BOUNDARY INSIDE AN OPEN TURN IS REFUSED
=========================================
Borrowed from DSH rather than rediscovered. A prefix that ends mid-turn is not a
shorter conversation, it is an ILLEGAL one: an assistant ``tool_use`` block with
no matching ``tool_result`` is rejected by the provider on the next call, which
``agent_loop`` itself already states at the budget check it placed before
appending the assistant message ("so the transcript does not end on a tool_use
with no matching tool_result, which providers reject on resume").

So a legal boundary is a position where, replaying the prefix:

* no turn is open (a ``turn_start`` with no ``turn_end`` after it),
* every ``tool_use`` announced by an ``assistant_message`` has been answered,
* no ``tool_result`` is left over with nothing to answer, and
* no projected event's payload is WITHHELD by the retention policy.

The refusal names the legal boundaries either side, so the correct fork is one
re-run away and never a guess.

The last of the four is the one worth stating twice. A withheld payload
(``payload_json IS NULL`` beside a NOT NULL ``payload_hash`` — see
``args/agent_event_log.yaml``) is not an empty payload. Projecting one would
put a message into the new session that the model never saw, which is a worse
outcome than refusing: the seeded history would be a fabrication carrying a
correct-looking digest. A hash-only deployment cannot fork, and should hear that
in those words rather than get a fork with holes in it.

THE EVENT ORDER IS NOT THE MESSAGE ORDER
========================================
``run_agent_loop`` fires ``on_turn`` AFTER the post-tool hooks for that
iteration (agent_loop.py:1911), so a real tool-using iteration lands in the log
as ``tool_call, tool_result, …, assistant_message`` — the assistant message
carrying the ``tool_use`` blocks arrives AFTER the results answering them. The
message list is the other way round.

:class:`_Projection` therefore buffers a result that arrives before the
assistant message that announced its call, and drains the buffer once that
message lands. That is the normal path and not an anomaly. It also means the
projection is order-tolerant: a caller driving the recorder by hand in the other
order (which is what ``tests/agent_runtime/test_event_recorder.py`` does)
projects identically.

``tool_call`` events are NOT projected. They carry no ``tool_use`` id — only
``assistant_message`` does — so the assistant message is the authoritative
source for the blocks, and the ``tool_call`` row is the audit record of the
dispatch. Pairing a result to its call is therefore by ``(name, input)``, the
same identity ``event_recorder._call_key`` uses. A result whose name matches no
outstanding call is left orphaned rather than attached to a different tool: a
mis-paired ``tool_use_id`` is a fabricated history, and the refusal above is the
correct outcome.

WHAT A FORK WRITES
==================
1. A new ``agent_loop_sessions`` row holding the projected messages, under a
   freshly minted loop session id. Written through ``save_session`` and READ
   BACK before it is trusted — that function returns ``False`` on any DB error
   and a resume id pointing at a row that was never written produces a session
   that looks continued and remembers nothing.
2. A new ``chat_contexts`` row whose ``context_config`` carries the fork
   metadata: the parent session id, the boundary seq, the seed length, and the
   digest over the seeded events' hashes.
3. One ``session_fork`` event at ``seq`` 1 of the NEW session's log, so the
   fork's own timeline opens with its provenance. It is metadata only — ids,
   counts and a digest — and quotes nothing the model saw.
4. The projected user/assistant turns replayed into ``chat_messages``, so the
   human-readable transcript matches what the model was seeded with. A session
   whose agent remembers a conversation its transcript does not show is the
   confusing half of a fork, and it costs one loop to avoid.

The prefix events themselves are NOT copied into the new session's log. A copy
would either lose the withheld payloads or need a second write verb on an
append-only module whose surface is deliberately ``append`` / ``read_session`` /
``next_seq``. The ``session_fork`` event's ``seed_digest`` — the same hashing
recipe, over the ordered ``payload_hash`` values — proves which prefix was
seeded without duplicating a byte of it.

ONE LIMITATION, INHERITED AND NOT INTRODUCED
============================================
The forked session's next turn behaves exactly as ``icdev chat --resume``'s
does, including this: ``run_agent_loop`` does NOT append the new ``user_prompt``
to a transcript loaded from ``resume_session_id`` (agent_loop.py:1414-1448,
asserted by ``tests/test_agent_loop.py::test_resume_loads_prior_messages``,
which passes ``user_prompt="ignored"``). ``AgentRuntime.run_turn`` passes both,
so on any resumed session the operator's message reaches the transcript and the
event log but not the model. That is a pre-existing defect of the resume seam,
it is the same on a fork, and fixing it belongs to the runtime rather than here
— stated in full because a fork that quietly answered the parent's last question
instead of the operator's new one would otherwise look like this module's bug.

CLI::

    python -m tools.agent_runtime.fork --session <ctx-id> --boundaries
    python -m tools.agent_runtime.fork --session <ctx-id> --at 12 --dry-run --json
    python -m tools.agent_runtime.fork --session <ctx-id> --at 12 --title "branch B"
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.agent_runtime.event_log import (  # noqa: E402
    Event,
    EventLogUnavailable,
    append,
    read_session,
)
from tools.audit.row_hash import compute_payload_hash  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("agent_runtime.fork")

#: The event appended at ``seq`` 1 of a forked session. A member of
#: :data:`~tools.agent_runtime.event_log.EVENT_TYPES`, so adding it needed no
#: migration and no CHECK-constraint edit — the payoff of validating the
#: vocabulary in Python that hcx-evt-01 documented and hcx-post-02 collected.
FORK_EVENT_TYPE = "session_fork"

#: Event types the message projection reads. A withheld payload on any of these
#: makes the prefix unprojectable; a withheld ``tool_call`` or
#: ``permission_posture`` does not, because neither is projected.
PROJECTED_TYPES = ("turn_start", "assistant_message", "tool_result")

# Refusal reasons. Machine codes, because "why was this refused" drives a
# different fix in each case and a prose message cannot be branched on.
REASON_NO_EVENTS = "no_events"
REASON_BOUNDARY_NOT_IN_LOG = "boundary_not_in_log"
REASON_OPEN_TURN = "boundary_inside_open_turn"
REASON_UNANSWERED_TOOL_CALL = "unanswered_tool_call"
REASON_ORPHAN_TOOL_RESULT = "orphan_tool_result"
REASON_PAYLOAD_WITHHELD = "payload_withheld"

_LLM_FUNCTION = "code_generation"


class ForkRefused(ValueError):
    """A fork that would produce an illegal or fabricated history.

    Carries a machine-readable :attr:`reason` and the boundaries that WOULD have
    been legal, so the caller can print something actionable instead of "no".
    """

    def __init__(self, reason: str, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.reason = reason
        self.detail = dict(detail)

    def to_dict(self) -> dict[str, Any]:
        return {
            "forked": False,
            "refused": True,
            "reason": self.reason,
            "message": str(self),
            **self.detail,
        }


# ---------------------------------------------------------------------------
# Message-block helpers — the shapes ``agent_loop`` itself builds
# ---------------------------------------------------------------------------
def _payload(event: Event) -> dict[str, Any]:
    """An event's payload as a mapping, or an empty one.

    A payload that is not a dict is not an error here: ``append`` accepts any
    JSON-able document and a hand-written row may hold a bare string. The
    projection reads named keys, so a non-mapping simply contributes none.
    """
    return event.payload if isinstance(event.payload, dict) else {}


def _user_message(event: Event) -> dict[str, Any]:
    return {"role": "user", "content": str(_payload(event).get("user_input") or "")}


def _assistant_message(event: Event) -> Optional[dict[str, Any]]:
    """The assistant message an ``assistant_message`` event projects to.

    ``None`` when the iteration produced neither text nor a tool call. Providers
    reject an assistant message with empty content, and "the model said nothing"
    is faithfully projected by there being no message rather than by an empty
    one.
    """
    payload = _payload(event)
    content: list[dict[str, Any]] = []
    text = str(payload.get("content") or "").strip()
    if text:
        content.append({"type": "text", "text": text})
    for call in _tool_calls(event):
        content.append(
            {
                "type": "tool_use",
                "id": call["id"],
                "name": call["name"],
                "input": call["input"],
            }
        )
    if not content:
        return None
    return {"role": "assistant", "content": content}


def _tool_calls(event: Event) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for call in _payload(event).get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        calls.append(
            {
                "id": str(call.get("id") or ""),
                "name": str(call.get("name") or ""),
                "input": call.get("input") if call.get("input") is not None else {},
            }
        )
    return calls


def _tool_result_message(
    tool_use_id: str, name: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """The user message carrying one ``tool_result`` block.

    Same shape as ``agent_loop._tool_result_message``, including ``name`` —
    providers that use a dedicated tool-result role (Ollama's
    ``{"role":"tool","name":…}``) read it rather than reverse-mapping the id.
    """
    block: dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "name": name,
        "content": [{"type": "text", "text": str(payload.get("result") or "")}],
    }
    if payload.get("is_error"):
        block["is_error"] = True
    return {"role": "user", "content": [block]}


def _match(pending: list[dict[str, Any]], name: str, tool_input: Any) -> Optional[int]:
    """Index of the outstanding ``tool_use`` a result answers, or ``None``.

    Exact ``(name, input)`` first, then name alone — a tool called twice with
    different inputs is paired precisely, and one called twice with the SAME
    input pairs FIFO, which is the multiset behaviour ``event_recorder`` already
    uses for the same reason.

    Never falls back to "the first outstanding call": attaching a result to a
    differently-named ``tool_use`` fabricates a history. An unmatched result is
    left orphaned, and an orphan is what makes the boundary illegal.
    """
    if not pending:
        return None
    for i, use in enumerate(pending):
        if use["name"] == name and use["input"] == tool_input:
            return i
    for i, use in enumerate(pending):
        if use["name"] == name:
            return i
    return None


# ---------------------------------------------------------------------------
# The projection, and the legality rule it defines
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _State:
    """What the projection is still waiting for at one position in the log."""

    open_turn: int = 0
    pending: tuple[str, ...] = ()
    orphans: tuple[int, ...] = ()
    withheld: tuple[int, ...] = ()

    @property
    def legal(self) -> bool:
        return not (self.open_turn or self.pending or self.orphans or self.withheld)


class _Projection:
    """Replays an ordered event prefix into a message list.

    One class, two answers, deliberately: :attr:`messages` is the surface
    projection and :meth:`state` is whether that projection is a history a
    provider will accept. A second definition of "legal" living beside the
    projection would drift from it on the first change to either.
    """

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.holes: list[dict[str, Any]] = []
        self._pending: list[dict[str, Any]] = []
        self._orphans: list[dict[str, Any]] = []
        self._withheld: list[int] = []
        self._open_turn = 0

    # -- state ------------------------------------------------------------

    def state(self) -> _State:
        return _State(
            open_turn=self._open_turn,
            pending=tuple(use["name"] for use in self._pending),
            orphans=tuple(o["seq"] for o in self._orphans),
            withheld=tuple(self._withheld),
        )

    @property
    def legal(self) -> bool:
        return self.state().legal

    # -- the walk ---------------------------------------------------------

    def feed(self, event: Event) -> None:
        etype = event.event_type
        if etype in PROJECTED_TYPES and event.payload_withheld:
            # Recorded for EVERY withheld event, not just the first: the operator
            # needs to know how much of the session is unprojectable, which is a
            # retention-policy decision and not a per-seq accident.
            self._withheld.append(event.seq)

        if etype == "turn_start":
            if self._open_turn:
                self.holes.append(
                    {
                        "seq": event.seq,
                        "kind": "turn_start_inside_open_turn",
                        "open_since": self._open_turn,
                    }
                )
            self._open_turn = event.seq
            self.messages.append(_user_message(event))
        elif etype == "assistant_message":
            message = _assistant_message(event)
            if message is not None:
                self.messages.append(message)
            for call in _tool_calls(event):
                self._pending.append(call)
            self._drain()
        elif etype == "tool_result":
            payload = _payload(event)
            index = _match(self._pending, str(payload.get("name") or ""), payload.get("input", {}))
            if index is None:
                # NORMAL, not an anomaly: the loop fires on_turn after the
                # post-tool hooks, so a result routinely precedes the assistant
                # message that announced its call. Buffered until it lands.
                self._orphans.append({"seq": event.seq, "payload": payload})
            else:
                use = self._pending.pop(index)
                self.messages.append(
                    _tool_result_message(use["id"], use["name"], payload)
                )
        elif etype == "turn_end":
            if not self._open_turn:
                self.holes.append(
                    {"seq": event.seq, "kind": "turn_end_without_turn_start"}
                )
            self._open_turn = 0
        # tool_call, request_context, permission_posture and session_fork are
        # deliberately not projected — see the module docstring.

    def _drain(self) -> None:
        """Attach buffered results to the calls the assistant message just named."""
        if not (self._orphans and self._pending):
            return
        remaining: list[dict[str, Any]] = []
        for orphan in self._orphans:
            payload = orphan["payload"]
            index = _match(
                self._pending, str(payload.get("name") or ""), payload.get("input", {})
            )
            if index is None:
                remaining.append(orphan)
                continue
            use = self._pending.pop(index)
            self.messages.append(_tool_result_message(use["id"], use["name"], payload))
        self._orphans = remaining


def project_messages(events: Iterable[Event]) -> list[dict[str, Any]]:
    """The message list an event prefix projects to. DSH's surface projection.

    Does NOT validate — :func:`plan_fork` does that, and a caller that wants to
    look at what a prefix projects to (a test, a diff, an operator) should not
    have to satisfy the fork rules to see it.
    """
    projection = _Projection()
    for event in events:
        projection.feed(event)
    return projection.messages


# ---------------------------------------------------------------------------
# Planning a fork
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ForkPlan:
    """A validated fork: what would be seeded, from where, and how it hashes."""

    parent_session_id: str
    boundary_seq: int
    seed_events: int
    messages: tuple[dict[str, Any], ...]
    seed_digest: str
    legal_boundaries: tuple[int, ...]
    holes: tuple[dict[str, Any], ...] = ()

    @property
    def user_turns(self) -> int:
        return sum(1 for m in self.messages if m["role"] == "user" and isinstance(m["content"], str))

    def summary(self) -> dict[str, Any]:
        return {
            "parent_session_id": self.parent_session_id,
            "boundary_seq": self.boundary_seq,
            "seed_events": self.seed_events,
            "seed_messages": len(self.messages),
            "user_turns": self.user_turns,
            "seed_digest": self.seed_digest,
            "legal_boundaries": list(self.legal_boundaries),
            "holes": [dict(h) for h in self.holes],
        }


def _nearest(legal: Sequence[int], at: int) -> tuple[Optional[int], Optional[int]]:
    """The legal boundaries either side of ``at`` — ``(at_or_before, after)``."""
    before = max((s for s in legal if s <= at), default=None)
    after = min((s for s in legal if s > at), default=None)
    return before, after


def _hint(legal: Sequence[int], at: int) -> str:
    before, after = _nearest(legal, at)
    if before is None and after is None:
        return "No legal fork boundary exists in this session yet."
    parts = []
    if before is not None:
        parts.append(f"--at {before}")
    if after is not None:
        parts.append(f"--at {after}")
    return "The nearest legal boundaries are: " + " or ".join(parts) + "."


def plan_fork(
    session_id: str,
    at: int,
    *,
    events: Optional[Sequence[Event]] = None,
    reader: Optional[Callable[..., list[Event]]] = None,
) -> ForkPlan:
    """Resolve ``--at`` against the log and validate it. Raises :class:`ForkRefused`.

    ``events`` (already read) or ``reader`` (a stand-in for
    :func:`~tools.agent_runtime.event_log.read_session`) are for tests and for a
    caller that has the log in hand; both default to reading it here.

    The walk continues PAST the boundary on purpose: a refusal that could not
    name the next legal boundary would leave the operator bisecting by hand.
    """
    if not session_id:
        raise ValueError("session_id is required")
    at = int(at)

    log = list(events) if events is not None else (reader or read_session)(session_id)
    if not log:
        raise ForkRefused(
            REASON_NO_EVENTS,
            f"No events are visible for session {session_id!r}. Either the session "
            "has none, or it belongs to another tenant — this cannot tell you "
            "which, and will not guess.",
            session_id=session_id,
            boundary_seq=at,
        )

    projection = _Projection()
    legal: list[int] = []
    snapshot: Optional[tuple[list[dict[str, Any]], _State]] = None
    prefix_hashes: list[str] = []

    for event in log:
        projection.feed(event)
        if event.seq <= at:
            prefix_hashes.append(event.payload_hash)
        if projection.legal:
            legal.append(event.seq)
        if event.seq == at:
            snapshot = (list(projection.messages), projection.state())

    if snapshot is None:
        raise ForkRefused(
            REASON_BOUNDARY_NOT_IN_LOG,
            f"seq {at} names no event in session {session_id}. A fork boundary is "
            "a seq in agent_session_events, and this will not round to a "
            f"neighbouring one. {_hint(legal, at)}",
            session_id=session_id,
            boundary_seq=at,
            max_seq=log[-1].seq,
            legal_boundaries=legal,
        )

    messages, state = snapshot
    if not state.legal:
        raise _refusal(session_id, at, state, legal)

    prefix = [e for e in log if e.seq <= at]
    return ForkPlan(
        parent_session_id=session_id,
        boundary_seq=at,
        seed_events=len(prefix),
        messages=tuple(messages),
        # The same hashing recipe as every payload_hash in the log, over the
        # ordered hashes themselves: it identifies the exact prefix that was
        # seeded without copying a byte of it.
        seed_digest=compute_payload_hash(prefix_hashes),
        legal_boundaries=tuple(legal),
        holes=tuple(projection.holes),
    )


def _refusal(
    session_id: str, at: int, state: _State, legal: Sequence[int]
) -> ForkRefused:
    """Turn an illegal boundary state into the refusal it calls for.

    Ordered by which fix the operator needs, not by severity: a withheld payload
    is a retention-policy decision, an open turn is a boundary choice, and an
    unanswered call or an orphaned result is a hole in the log itself.
    """
    common = {
        "session_id": session_id,
        "boundary_seq": at,
        "legal_boundaries": list(legal),
    }
    if state.withheld:
        return ForkRefused(
            REASON_PAYLOAD_WITHHELD,
            f"seq {at} cannot be forked: {len(state.withheld)} event(s) in the "
            f"prefix have a WITHHELD payload (seq {', '.join(str(s) for s in state.withheld[:8])}"
            f"{'…' if len(state.withheld) > 8 else ''}). Their hashes are recorded "
            "and their documents are not, so the seeded history would be a "
            "fabrication with a correct-looking digest. This is a retention "
            "decision — see payload_retention in args/agent_event_log.yaml.",
            withheld_seqs=list(state.withheld),
            **common,
        )
    if state.open_turn:
        return ForkRefused(
            REASON_OPEN_TURN,
            f"seq {at} lands INSIDE the turn that opened at seq "
            f"{state.open_turn}, which has no turn_end at or before the "
            "boundary. A prefix ending mid-turn is not a shorter conversation, "
            "it is an illegal one — and this will not round it to a turn "
            f"boundary. {_hint(legal, at)}",
            open_turn_seq=state.open_turn,
            **common,
        )
    if state.pending:
        return ForkRefused(
            REASON_UNANSWERED_TOOL_CALL,
            f"seq {at} cannot be forked: {len(state.pending)} tool call(s) "
            f"({', '.join(sorted(set(state.pending)))}) were announced by an "
            "assistant message and never answered by a tool_result at or before "
            "the boundary. Replaying that produces a tool_use with no "
            f"tool_result, which providers reject. {_hint(legal, at)}",
            unanswered=list(state.pending),
            **common,
        )
    return ForkRefused(
        REASON_ORPHAN_TOOL_RESULT,
        f"seq {at} cannot be forked: the prefix holds {len(state.orphans)} "
        f"tool_result(s) (seq {', '.join(str(s) for s in state.orphans)}) that "
        "answer no tool call the log records. That is a hole in the log rather "
        f"than a boundary you chose. {_hint(legal, at)}",
        orphans=list(state.orphans),
        **common,
    )


def legal_boundaries(
    session_id: str,
    *,
    events: Optional[Sequence[Event]] = None,
    reader: Optional[Callable[..., list[Event]]] = None,
) -> list[int]:
    """Every ``seq`` this session may legally be forked at, in order.

    The survey an operator runs BEFORE choosing a boundary — and what
    ``icdev chat --fork <id>`` prints when ``--at`` is omitted, rather than
    picking one for them.
    """
    return list(describe(session_id, events=events, reader=reader)["legal_boundaries"])


def describe(
    session_id: str,
    *,
    events: Optional[Sequence[Event]] = None,
    reader: Optional[Callable[..., list[Event]]] = None,
) -> dict[str, Any]:
    """A boundary survey for ``session_id``: what it holds and where it may fork."""
    log = list(events) if events is not None else (reader or read_session)(session_id)
    projection = _Projection()
    legal: list[int] = []
    for event in log:
        projection.feed(event)
        if projection.legal:
            legal.append(event.seq)
    turn_ends = [e.seq for e in log if e.event_type == "turn_end"]
    return {
        "session_id": session_id,
        "events": len(log),
        "max_seq": log[-1].seq if log else 0,
        "turns": len(turn_ends),
        "legal_boundaries": legal,
        "open_turn": projection.state().open_turn,
        "withheld": list(projection.state().withheld),
        "holes": list(projection.holes),
    }


# ---------------------------------------------------------------------------
# Performing a fork
# ---------------------------------------------------------------------------
@dataclass
class ForkResult:
    """What a fork actually created. ``warnings`` is never silently empty."""

    context_id: str
    plan: ForkPlan
    seed_loop_session_id: str = ""
    messages_seeded: int = 0
    fork_event_id: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "forked": True,
            "context_id": self.context_id,
            "seed_loop_session_id": self.seed_loop_session_id,
            "messages_seeded": self.messages_seeded,
            "fork_event_id": self.fork_event_id,
            "warnings": list(self.warnings),
            **self.plan.summary(),
        }

    def summary(self) -> str:
        lines = [
            f"Forked {self.plan.parent_session_id} at seq "
            f"{self.plan.boundary_seq} -> {self.context_id}",
            f"  seeded {self.plan.seed_events} event(s) as "
            f"{len(self.plan.messages)} message(s); digest "
            f"sha256:{self.plan.seed_digest[:12]}",
        ]
        for warning in self.warnings:
            lines.append(f"  WARNING: {warning}")
        return "\n".join(lines)


@dataclass(frozen=True)
class _SeedResult:
    """The minimum ``save_session`` reads, so a projection can be persisted.

    ``save_session`` takes an ``AgentLoopResult``; a fork has no loop run to
    produce one, and constructing a real ``AgentLoopResult`` would import the
    whole loop to fill fields nothing here means. The counters are zero because
    the fork spent nothing — the parent's usage belongs to the parent.
    """

    session_id: str
    messages: list[dict[str, Any]]
    result_subtype: str = "forked"
    turns: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fork_session(
    parent_session_id: str,
    at: int,
    *,
    title: str = "",
    user_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    actor: str = "",
    manager: Any = None,
    plan: Optional[ForkPlan] = None,
    reader: Optional[Callable[..., list[Event]]] = None,
    saver: Optional[Callable[..., bool]] = None,
    loader: Optional[Callable[[str], list]] = None,
    appender: Optional[Callable[..., Any]] = None,
) -> ForkResult:
    """Seed a NEW session from ``parent_session_id``'s prefix through ``at``.

    Raises :class:`ForkRefused` before creating anything at all: validation
    reads, and a refused fork must leave no half-built session behind.

    Order of writes, and why: the loop-session seed goes first, because a
    ``chat_contexts`` row naming a ``resume_session_id`` that was never written
    is a session that claims a memory it does not have. A seed nothing points at
    is an orphan row keyed by a UUID — harmless, and reported.
    """
    resolved = plan if plan is not None else plan_fork(parent_session_id, at, reader=reader)
    warnings: list[str] = []

    from tools.agent_runtime.sessions import _default_tenant_id, _default_user_id

    uid = _default_user_id(user_id)
    tid = _default_tenant_id(tenant_id)

    # 1. Seed the LLM-facing transcript, and READ IT BACK before trusting it.
    seed_id = _seed_loop_session(resolved, saver=saver, loader=loader, warnings=warnings)

    # 2. The new chat context, carrying the fork metadata from its first moment.
    if manager is None:
        from tools.chat.chat_manager import ChatManager

        manager = ChatManager(uid, tid)

    metadata = {
        "parent_session_id": resolved.parent_session_id,
        "boundary_seq": resolved.boundary_seq,
        "seed_events": resolved.seed_events,
        "seed_messages": len(resolved.messages),
        "seed_digest": resolved.seed_digest,
        "seed_loop_session_id": seed_id,
        "forked_at": _now(),
        "actor": actor or uid,
    }
    config: dict[str, Any] = {
        "origin": "standalone_agent_runtime",
        "fork": metadata,
    }
    if seed_id:
        config["resume_session_id"] = seed_id
    context_id = manager.create_context(
        title=title or f"Fork of {resolved.parent_session_id} @ seq {resolved.boundary_seq}",
        config=config,
    )

    # 3. Provenance at seq 1 of the new session's own log.
    event_id = _record_fork_event(
        context_id, resolved, metadata, tenant_id=tid, appender=appender, warnings=warnings
    )
    if not event_id:
        try:
            manager.update_config(context_id, {"fork": {**metadata, "fork_event_recorded": False}})
        except Exception as exc:  # noqa: BLE001 — the config already holds the facts
            logger.debug("fork: could not flag the missing fork event: %s", exc)

    # 4. The human-readable transcript, so it matches what the model was seeded with.
    seeded = _seed_transcript(manager, context_id, resolved, warnings=warnings)

    logger.info(
        "fork: %s @ seq %d -> %s (%d events, %d messages, loop seed %s)",
        resolved.parent_session_id, resolved.boundary_seq, context_id,
        resolved.seed_events, len(resolved.messages), seed_id or "NONE",
    )
    return ForkResult(
        context_id=context_id,
        plan=resolved,
        seed_loop_session_id=seed_id,
        messages_seeded=seeded,
        fork_event_id=event_id,
        warnings=warnings,
    )


def _seed_loop_session(
    plan: ForkPlan,
    *,
    saver: Optional[Callable[..., bool]],
    loader: Optional[Callable[[str], list]],
    warnings: list[str],
) -> str:
    """Persist the projected messages and confirm they are readable back.

    Returns the seeded loop session id, or ``""`` when the seed did not land —
    in which case the caller must NOT set ``resume_session_id``. ``save_session``
    reports failure by returning ``False`` and ``load_session`` returns ``[]``
    for a row that is not there, so a resume id pointing at either produces a
    session that looks continued and remembers nothing.
    """
    if not plan.messages:
        # A legal boundary with no projected messages is possible (a session
        # whose only events are postures). Nothing to seed, and no resume id to
        # invent.
        return ""
    try:
        from icdev.tools.llm.agent_loop_session import load_session, save_session
    except Exception as exc:  # noqa: BLE001 — canvas persistence unavailable
        warnings.append(
            f"the projected history could not be persisted ({exc}); the fork's "
            "transcript is seeded but its next turn starts with no model memory"
        )
        return ""

    save = saver or save_session
    load = loader or load_session
    seed_id = str(uuid.uuid4())
    stored = False
    try:
        stored = bool(save(
            _SeedResult(session_id=seed_id, messages=list(plan.messages)),
            llm_function=_LLM_FUNCTION,
        ))
    except Exception as exc:  # noqa: BLE001 — reported, never swallowed silently
        warnings.append(f"seeding the loop session raised ({exc})")
        return ""
    if not stored:
        warnings.append(
            "the projected history could not be written to agent_loop_sessions; "
            "the fork's next turn will start with no model memory"
        )
        return ""
    try:
        read_back = load(seed_id)
    except Exception as exc:  # noqa: BLE001
        read_back = []
        warnings.append(f"the seeded loop session could not be read back ({exc})")
    if len(read_back) != len(plan.messages):
        warnings.append(
            f"the seeded loop session read back {len(read_back)} message(s) of "
            f"{len(plan.messages)}; not linking it, because a resume id pointing "
            "at an incomplete seed is worse than none"
        )
        return ""
    return seed_id


def _record_fork_event(
    context_id: str,
    plan: ForkPlan,
    metadata: dict[str, Any],
    *,
    tenant_id: str,
    appender: Optional[Callable[..., Any]],
    warnings: list[str],
) -> str:
    """Append the ``session_fork`` event. Returns its id, or ``""`` on failure.

    Metadata only — ids, counts and the digest. Nothing the model saw travels in
    it, which is what lets it be read by a forensic consumer that must not touch
    ``payload_json``.

    A failure here does NOT abort the fork. The parent id, the boundary and the
    digest are already in ``context_config``, so provenance survives; what is
    lost is the fork's position in the append-only ordering, and that is a
    warning rather than a reason to refuse an operator a branch of their own
    conversation.
    """
    write = appender or append
    try:
        event = write(
            context_id,
            FORK_EVENT_TYPE,
            dict(metadata),
            correlation_id=f"fork-{plan.parent_session_id}@{plan.boundary_seq}",
            tenant_id=tenant_id or None,
        )
    except Exception as exc:  # noqa: BLE001 — a fork survives an unwritable log
        warnings.append(
            f"the session_fork event could not be appended ({exc}); the fork "
            "metadata is still on the chat context"
        )
        logger.warning("fork: could not record the session_fork event: %s", exc)
        return ""
    return str(getattr(event, "event_id", "") or "")


def _seed_transcript(
    manager: Any, context_id: str, plan: ForkPlan, *, warnings: list[str]
) -> int:
    """Replay the projected user/assistant turns into ``chat_messages``.

    Tool-result messages are NOT replayed: they are carried on the ``user`` role
    for the provider's sake, and writing them into the human transcript as user
    turns would attribute the tool's output to the operator. A ``str`` content
    is a real turn; a ``list`` is content blocks.
    """
    seeded = 0
    for message in plan.messages:
        content = message.get("content")
        if not isinstance(content, str):
            if message.get("role") == "assistant":
                content = "\n".join(
                    str(b.get("text") or "")
                    for b in (content or [])
                    if isinstance(b, dict) and b.get("type") == "text"
                ).strip()
                if not content:
                    continue
            else:
                continue
        try:
            manager.add_message(
                context_id,
                role=message["role"],
                content=content,
                metadata={
                    "seeded_from": plan.parent_session_id,
                    "boundary_seq": plan.boundary_seq,
                },
            )
            seeded += 1
        except Exception as exc:  # noqa: BLE001 — the model memory is already seeded
            warnings.append(f"a seeded transcript message could not be written ({exc})")
            logger.debug("fork: transcript seed failed: %s", exc)
            break
    return seeded


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fork an agent session at a seq in agent_session_events (hcx-evt-05)"
    )
    parser.add_argument("--session", required=True, help="Parent session (chat context) id")
    parser.add_argument("--at", type=int, help="Boundary seq, inclusive")
    parser.add_argument(
        "--boundaries", action="store_true",
        help="Survey the legal fork boundaries and exit (no --at needed)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate --at and report what would be seeded, writing nothing",
    )
    parser.add_argument("--title", default="", help="Title for the forked session")
    parser.add_argument("--actor", default="", help="Who is forking (recorded)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    try:
        if args.boundaries or args.at is None:
            report = describe(args.session)
            if args.json:
                print(json.dumps(report, indent=2, default=str))
            else:
                print(f"Session {report['session_id']}: {report['events']} event(s), "
                      f"{report['turns']} turn(s), max seq {report['max_seq']}")
                legal = report["legal_boundaries"]
                print("Legal fork boundaries: "
                      + (", ".join(str(s) for s in legal) if legal else "(none)"))
                if report["open_turn"]:
                    print(f"A turn is open from seq {report['open_turn']} — it cannot "
                          "be forked until it ends.")
                if report["withheld"]:
                    print(f"{len(report['withheld'])} event(s) have withheld payloads "
                          "— see payload_retention in args/agent_event_log.yaml.")
                if not args.boundaries:
                    print("--at is required to fork; no boundary is chosen for you.",
                          file=sys.stderr)
            # Exit 2 when a fork was asked for without a boundary: the survey ran
            # and nothing was created, which is not the same as a successful fork.
            return 0 if args.boundaries else 2

        if args.dry_run:
            plan = plan_fork(args.session, args.at)
            if args.json:
                print(json.dumps({"forked": False, "dry_run": True, **plan.summary()},
                                 indent=2, default=str))
            else:
                print(f"Would fork {plan.parent_session_id} at seq {plan.boundary_seq}: "
                      f"{plan.seed_events} event(s) -> {len(plan.messages)} message(s), "
                      f"digest sha256:{plan.seed_digest[:12]}")
            return 0

        result = fork_session(args.session, args.at, title=args.title, actor=args.actor)
        if args.json:
            print(json.dumps(result.to_dict(), indent=2, default=str))
        else:
            print(result.summary())
        return 0
    except ForkRefused as exc:
        if args.json:
            print(json.dumps(exc.to_dict(), indent=2, default=str))
        else:
            print(f"REFUSED ({exc.reason}): {exc}", file=sys.stderr)
        return 2
    except (EventLogUnavailable, ValueError) as exc:
        if args.json:
            print(json.dumps({"error": str(exc), "session_id": args.session}, indent=2))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
