#!/usr/bin/env python3
# CUI // SP-CTI
"""Every context injection, recorded as a ``request_context`` event (hcx-evt-03).

WHAT WAS MISSING
----------------
Nothing recorded a context injection anywhere. Three modules put text into the
system prompt at session start and none of them left a trace:

* :mod:`tools.agent_runtime.project_context` — ``CLAUDE.md`` / ``AGENTS.md`` /
  ``memory/MEMORY.md`` plus the ``session_context_builder`` project-state summary
* :mod:`tools.agent_runtime.goal_context` — the operator's active standing goals
* :mod:`tools.agent_runtime.profile_memory` — durable profile facts,
  preferences, and the top hybrid-memory hits

A tree-wide grep for ``context_injection|injected_context|prompt_snapshot|
rendered_prompt`` before this card returned three unrelated files
(``kanban/seed_irad_kanban.py``, ``zta/blueprint.py``, ``zta/constants.py``).

hcx-evt-01 gave the runtime an append-only event log whose stated invariant is
"anything that reaches a model request must be reconstructable from the log". Its
vocabulary already had ``request_context`` in it and nothing ever wrote one. An
event type that is declared and never emitted is this platform's signature defect
wearing the audit log's clothes: the log's coverage claim is only as good as its
least-covered injector, and an uncovered injector is invisible.

THIS MODULE IS THE ONE SEAM ALLOWED TO SWALLOW
----------------------------------------------
:func:`tools.agent_runtime.event_log.append` raises on a failed INSERT, on
purpose — a write path that reports success while persisting nothing is how
``module_budget_usage`` held zero rows. That rule is right for the log and wrong
here. Each injector is wrapped in best-effort ``try``/``except`` precisely so a
missing subsystem never blocks a turn, and recording must not become a new way
for context injection to fail. So :func:`record_injection` catches everything and
returns ``None``.

Swallowed is NOT unmeasured. Every outcome increments a counter in :func:`stats`
and a failure logs at WARNING with the exception attached, so a recorder that
quietly stopped working is discoverable rather than merely absent. The counters
are process-local — deliberately not persisted, because a durable failure counter
would itself be a database write on the path that must not fail. The durable
signal is the events themselves: :func:`coverage` answers "which injectors are
represented in this session" straight from the log.

THE ENVELOPE IS ALWAYS STORED; ONLY THE BODY IS POLICY-GATED
------------------------------------------------------------
``args/agent_event_log.yaml`` decides whether verbatim model input is retained at
rest, and it names ``request_context`` in its own comments as the event a
deployment might find too large or too sensitive to keep. That decision is
honoured — for the BODY.

It is not applied to the envelope. If the policy suppressed the whole payload,
the row would be a hash and an event type, and it could not say WHICH injector
produced it — which is the one thing this card requires it to say. So the payload
written is::

    {
      "source":       "project_context",     # WHICH injector — always present
      "size_tokens":  1234,                  # always present
      "size_chars":   5678,                  # always present
      "body_sha256":  "<64 hex>",            # always present
      "body_stored":  false,                 # whether "body" is below
      "detail":       {...},                 # injector accounting; always present
      "body":         "<the injected text>"  # ONLY when body_stored
    }

Nothing in the envelope is model input: a source name, two sizes, a digest and
the injector's own budget accounting. ``body_sha256`` is the stable identity of
the injected text under every setting, so a reader holding a copy of the block
can prove it is the one that was injected even in hash-only mode.

Two withheld flags, and they are NOT the same fact.
:attr:`~tools.agent_runtime.event_log.Event.payload_withheld` describes the row's
``payload_json`` (the envelope, which is always kept, so it is always ``False``
here). ``body_stored`` inside the envelope describes the injected text. Merging
them would make "the operator turned retention off" indistinguishable from "no
context was injected", which is the distinction the whole log exists to preserve.

WHICH ID IS ``session_id``
--------------------------
The ``chat_contexts.id`` the runtime calls ``context_id`` — NOT
``AgentLoopResult.session_id``.

The loop session id is empty until the first turn *completes*, and injection
happens before the first turn *starts*. Keying on it would leave turn one
unrecorded, which is the lie by omission this card exists to close. ``context_id``
exists from session creation, is stable across every turn and across a process
restart via ``/resume``, and is already what ``sessions._index_turn`` tags memory
rows with (``ctx:<id>``).

The loop session id is not discarded: callers pass it as ``correlation_id`` once
it exists, so an event can still be joined to ``agent_loop_sessions``,
``hook_events`` and ``audit_trail``. On the first turn it is legitimately empty,
and an empty correlation is recorded as empty rather than invented.

WHAT IS NOT RECORDED, AND WHY THAT IS NOT A GAP
-----------------------------------------------
An injector that produced no text injected nothing, so there is no event. A
disabled subsystem, an absent ``AGENTS.md``, an operator with no standing goals —
none of these reach the model, and writing a row saying so would be fabricating
coverage rather than measuring it. The counters distinguish these from failures:
``skipped_empty`` and ``skipped_no_session`` are separate from ``failed``.

CLI::

    python tools/agent_runtime/context_events.py --session <context_id> --json
    python tools/agent_runtime/context_events.py --session <context_id> --with-body
    python tools/agent_runtime/context_events.py --sources
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.agent_runtime.event_log import (  # noqa: E402
    Event,
    RetentionPolicy,
    append,
    load_policy,
    read_session,
)
from tools.audit.row_hash import compute_payload_hash  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("agent_runtime.context_events")

#: The event type every injection is recorded under. One of
#: ``event_log.EVENT_TYPES`` — referenced, never re-spelled, so a rename there
#: breaks the import rather than silently writing an unknown type.
EVENT_TYPE = "request_context"

#: The injector vocabulary. One entry per module that puts text in front of the
#: model, and the value is what lands in ``payload["source"]``.
#:
#: A source outside this tuple is still RECORDED — dropping an injection because
#: its name is unfamiliar is the exact failure this card is closing — but it is
#: flagged ``source_registered: false`` in the envelope and logged, so a fourth
#: injector that appears without being declared here is visible in the log rather
#: than indistinguishable from the three that were.
SOURCES = (
    "project_context",
    "goal_context",
    "profile_memory",
)

#: Fallback tokens-per-character when ``context_budget`` is unimportable (a
#: stripped runtime with no LLM config). Matches ``_CHARS_PER_TOKEN`` there; it
#: is a degraded estimate and ``size_chars`` beside it is exact either way.
_FALLBACK_CHARS_PER_TOKEN = 4


class _EnvelopePolicy(RetentionPolicy):
    """Retains the envelope unconditionally — see the module docstring.

    The deployment's retention policy still governs the BODY: it is consulted in
    :func:`record_injection` and the text is only placed in the envelope when it
    allows. What this override protects is the source name, the sizes and the
    digest, without which a ``request_context`` row cannot say which injector
    produced it.
    """

    def stores(self, event_type: str, classification: str) -> bool:  # noqa: D102
        return True


_ENVELOPE_POLICY = _EnvelopePolicy(source="context_events:envelope")


# ---------------------------------------------------------------------------
# Process-local outcome counters
# ---------------------------------------------------------------------------
#: Why these exist: :func:`record_injection` cannot raise, and an unmeasured
#: silent failure is the defect CLAUDE.md names most often. Each call lands in
#: exactly one bucket.
_STATS: dict[str, Any] = {
    "recorded": 0,
    "skipped_empty": 0,
    "skipped_no_session": 0,
    "failed": 0,
    "last_error": "",
}


def stats() -> dict[str, Any]:
    """A copy of this process's recording outcomes.

    ``recorded`` + ``skipped_empty`` + ``skipped_no_session`` + ``failed`` is the
    number of :func:`record_injection` calls. ``skipped_*`` are not failures —
    nothing was injected, or there was no session to attach the event to (a CLI
    or test invocation of an injector) — and they are counted apart from
    ``failed`` so the two are never read as one number.
    """
    return dict(_STATS)


def reset_stats() -> None:
    """Zero the counters. For tests and long-lived daemons."""
    _STATS.update(
        {
            "recorded": 0,
            "skipped_empty": 0,
            "skipped_no_session": 0,
            "failed": 0,
            "last_error": "",
        }
    )


def _estimate_tokens(text: str) -> int:
    """Token size of the injected block, via the platform's one estimator.

    ``context_budget.estimate_tokens`` is what the injectors themselves budget
    against, so the recorded size is the same quantity they were sized by. The
    fallback keeps this module usable in a runtime with no LLM config — degraded
    rather than absent, and ``size_chars`` is exact under both.
    """
    if not text:
        return 0
    try:
        from tools.llm.context_budget import estimate_tokens

        return int(estimate_tokens(text))
    except Exception as exc:  # noqa: BLE001 — LLM config is optional here
        logger.debug("context_events: token estimator unavailable: %s", exc)
        return int(math.ceil(len(text) / _FALLBACK_CHARS_PER_TOKEN))


def build_envelope(
    source: str,
    text: str,
    *,
    detail: Optional[dict[str, Any]] = None,
    store_body: bool = True,
    size_tokens: Optional[int] = None,
) -> dict[str, Any]:
    """The payload document written for one injection.

    Separated from :func:`record_injection` so a test can pin the shape without a
    database, and so a caller can see exactly what would be persisted before
    persisting it.
    """
    envelope: dict[str, Any] = {
        "source": source,
        "source_registered": source in SOURCES,
        "size_tokens": _estimate_tokens(text) if size_tokens is None else int(size_tokens),
        "size_chars": len(text or ""),
        "body_sha256": compute_payload_hash(text),
        "body_stored": bool(store_body),
        "detail": dict(detail or {}),
    }
    if store_body:
        envelope["body"] = text
    return envelope


def record_injection(
    session_id: str,
    source: str,
    text: str,
    *,
    detail: Optional[dict[str, Any]] = None,
    size_tokens: Optional[int] = None,
    correlation_id: str = "",
    classification: Optional[str] = None,
    tenant_id: Optional[str] = None,
    policy: Optional[RetentionPolicy] = None,
) -> Optional[Event]:
    """Record one context injection as a ``request_context`` event.

    Args:
        session_id: The ``chat_contexts.id`` this injection was built for. Empty
            means there is no session — a CLI or test invocation of an injector —
            and the call is skipped rather than attached to a made-up id.
        source: Which injector produced ``text``; see :data:`SOURCES`.
        text: The block that was prepended to the system prompt. Empty means
            nothing was injected, so nothing is recorded.
        detail: The injector's own accounting (budget spent, sections truncated,
            goals withheld). Stored in the envelope regardless of the retention
            policy — it is metadata about the injection, not model input.
        size_tokens: The injector's own token figure, when it already computed
            one against its budget. Omit and it is estimated here.

    Returns:
        The appended :class:`~tools.agent_runtime.event_log.Event`, or ``None``
        when the call was skipped or the write failed.

    NEVER RAISES. Every exception is caught, counted in :func:`stats` and logged.
    Each injector is deliberately best-effort so a missing subsystem cannot block
    a turn, and recording must not become a new way for injection to fail.
    """
    if not text:
        _STATS["skipped_empty"] += 1
        return None
    if not session_id:
        # Not a failure: an injector run from its own CLI, or from a test, has
        # no session. Counted apart from `failed` so the two never merge.
        _STATS["skipped_no_session"] += 1
        logger.debug(
            "context_events: %s injected %d chars with no session id — not recorded",
            source, len(text),
        )
        return None

    try:
        if source not in SOURCES:
            logger.warning(
                "context_events: %r is not a registered injector source "
                "(known: %s) — recording it anyway, flagged unregistered",
                source, ", ".join(SOURCES),
            )

        # The deployment's retention policy governs the BODY. It is asked about
        # `request_context` specifically, so a deployment that lists that type in
        # `never_store` — which args/agent_event_log.yaml names as the intended
        # use of that setting — drops the transcript and keeps the envelope.
        active = policy if policy is not None else load_policy()
        _tid, cls = _identity(tenant_id, classification)
        store_body = active.stores(EVENT_TYPE, cls)

        envelope = build_envelope(
            source, text, detail=detail, store_body=store_body, size_tokens=size_tokens
        )
        event = append(
            session_id,
            EVENT_TYPE,
            envelope,
            correlation_id=correlation_id,
            classification=classification,
            tenant_id=tenant_id,
            policy=_ENVELOPE_POLICY,
        )
    except Exception as exc:  # noqa: BLE001 — MUST NOT propagate; see docstring
        _STATS["failed"] += 1
        _STATS["last_error"] = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "context_events: could not record the %s injection for session %s: %s",
            source, session_id, exc,
        )
        return None

    _STATS["recorded"] += 1
    logger.debug(
        "context_events: recorded %s injection (%d tokens, body %s) as seq %d",
        source, envelope["size_tokens"],
        "stored" if store_body else "WITHHELD by policy", event.seq,
    )
    return event


def _identity(
    tenant_id: Optional[str], classification: Optional[str]
) -> tuple[str, str]:
    """The tenant/classification ``append`` will resolve for this row.

    Re-uses ``event_log._resolve_identity`` rather than re-deriving it, because
    the body-retention decision has to be made against the SAME classification
    the row is written with. Two independent resolutions that drifted would let a
    body be stored under a label the row does not carry.
    """
    from tools.agent_runtime.event_log import _resolve_identity

    return _resolve_identity(tenant_id, classification)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------
def injections_for_session(
    session_id: str, *, include_body: bool = False
) -> list[dict[str, Any]]:
    """Every recorded injection for ``session_id``, in ``seq`` order.

    Each entry is the envelope plus the row's ``seq``/``occurred_at``/
    ``payload_hash``. ``include_body=False`` (the default) strips the injected
    text: the common question is "what was injected, from where, how big", and a
    caller with no need for the transcript should not pull it out of the
    database.

    Returns ``[]`` when the log is unreachable — this is a reporting surface, and
    an exception here would make an absent table indistinguishable from a crash
    for every caller. :func:`coverage` reports the reachability explicitly.
    """
    try:
        events = read_session(session_id, event_types=(EVENT_TYPE,))
    except Exception as exc:  # noqa: BLE001 — reporting surface
        logger.warning(
            "context_events: cannot read injections for session %s: %s",
            session_id, exc,
        )
        return []

    out: list[dict[str, Any]] = []
    for ev in events:
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        entry = {
            "seq": ev.seq,
            "occurred_at": ev.occurred_at,
            "event_id": ev.event_id,
            "payload_hash": ev.payload_hash,
            "correlation_id": ev.correlation_id,
            "classification": ev.classification,
            # An envelope that is missing or unparseable is reported as an
            # unknown source rather than dropped: the row still proves an
            # injection happened, which is the fact this log exists to hold.
            "source": payload.get("source", "") or "(unknown)",
            "source_registered": bool(payload.get("source_registered", False)),
            "size_tokens": int(payload.get("size_tokens") or 0),
            "size_chars": int(payload.get("size_chars") or 0),
            "body_sha256": payload.get("body_sha256", ""),
            "body_stored": bool(payload.get("body_stored", False)),
            "envelope_withheld": ev.payload_withheld,
            "detail": payload.get("detail") or {},
        }
        if include_body:
            entry["body"] = payload.get("body")
        out.append(entry)
    return out


def coverage(session_id: str) -> dict[str, Any]:
    """Which injectors are represented in ``session_id``'s log.

    The card's own question, answerable from the log rather than from the code.
    Returns ``{session_id, total, sources: {name: {...}}, unregistered: [...]}``.

    A source with ``recorded: false`` is NOT necessarily a defect and is not
    reported as one — standing goals can be disabled, a checkout can have no
    ``AGENTS.md``, an operator can have no durable facts, and in each case nothing
    reached the model so nothing should have been logged. What this surfaces is
    the shape of a session, which is what makes a genuinely missing injector
    visible when the block it produces is known to be non-empty.
    """
    entries = injections_for_session(session_id)
    by_source: dict[str, dict[str, Any]] = {
        name: {"recorded": False, "count": 0, "tokens": 0, "bodies_stored": 0}
        for name in SOURCES
    }
    unregistered: list[str] = []
    for e in entries:
        name = e["source"]
        slot = by_source.get(name)
        if slot is None:
            slot = {"recorded": False, "count": 0, "tokens": 0, "bodies_stored": 0}
            by_source[name] = slot
            if name not in unregistered:
                unregistered.append(name)
        slot["recorded"] = True
        slot["count"] += 1
        slot["tokens"] += e["size_tokens"]
        slot["bodies_stored"] += 1 if e["body_stored"] else 0
    return {
        "session_id": session_id,
        "total": len(entries),
        "sources": by_source,
        "unregistered": unregistered,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
# There is deliberately no `--stats` flag. The counters in `stats()` are
# process-local, so a fresh CLI process could only ever print zeros — a flag that
# can only report "nothing happened" reads as a clean bill of health and is worse
# than no flag at all. `stats()` is for the process doing the injecting.
def _main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Show the context injections recorded for a session (hcx-evt-03)"
    )
    parser.add_argument("--session", help="Session id (the chat context_id)")
    parser.add_argument(
        "--with-body", action="store_true",
        help="Include the injected text (omitted by default — it is large and "
             "can carry verbatim model input)",
    )
    parser.add_argument(
        "--sources", action="store_true",
        help="List the registered injector sources and exit",
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    if args.sources:
        if args.json:
            print(json.dumps({"sources": list(SOURCES)}, indent=2))
        else:
            for name in SOURCES:
                print(name)
        return 0

    if not args.session:
        parser.error("--session is required (or use --sources)")

    entries = injections_for_session(args.session, include_body=args.with_body)
    report = coverage(args.session)

    if args.json:
        print(json.dumps(
            {**report, "injections": entries}, indent=2, default=str
        ))
        return 0

    if not entries:
        print(f"No context injections recorded for session {args.session}")
        return 0
    print(f"{len(entries)} injection(s) for session {args.session}")
    for e in entries:
        mark = "" if e["body_stored"] else "  [body withheld]"
        print(
            f"  {e['seq']:>4}  {e['occurred_at']}  {e['source']:<18} "
            f"{e['size_tokens']:>6} tok  {e['body_sha256'][:12]}{mark}"
        )
    print("\nCoverage:")
    for name, slot in report["sources"].items():
        state = f"{slot['count']} injection(s)" if slot["recorded"] else "not recorded"
        print(f"  {name:<18} {state}")
    if report["unregistered"]:
        print(
            "\nWARNING: unregistered source(s) in this session: "
            + ", ".join(report["unregistered"])
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
