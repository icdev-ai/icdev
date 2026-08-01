#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV™ Studio — Gateway event → workflow run dispatch (dwo-evt-02).

The gateway already does the hard part. ``tools/gateway/gateway_agent.py``
registers per-channel webhook routes, the channel adapters normalise payloads
into a ``CommandEnvelope``, and ``tools/gateway/security_chain.py`` runs eight
gates before anything is executed. This module is the last hop: it takes an
envelope that has *already cleared* that chain and turns it into workflow runs.

Three properties are non-negotiable, and each is enforced here rather than
trusted to the caller:

1. **No new ingress.** There is no route in this file. :func:`dispatch_envelope`
   is called from inside the existing gateway handler, after the chain passes.
   An event that fails a gate never reaches this module, so it starts nothing —
   and the gateway's own audit records the rejection.
2. **Idempotency.** Every webhook platform retries. The UNIQUE index on
   ``studio_trigger_events.idempotency_key`` is the mechanism: the audit row is
   written *before* the run starts, so a replay loses the INSERT and returns
   without starting anything. Mirrors ``kanban_tasks.idempotency_key``.
3. **Classification.** A run inherits the event's IL — the more conservative of
   the channel ceiling and the registered source ceiling. A trigger pointing at
   a workflow rated below that IL is refused and audited, never downgraded.

Dispatch is asynchronous relative to the HTTP response: the gateway calls
:func:`dispatch_envelope_async`, which hands the work to a daemon thread and
returns immediately, so a slow workflow cannot hold a webhook connection open.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.logging.icdev_logger import get_logger  # noqa: E402
from tools.studio.event_sources import (  # noqa: E402
    IL_ORDER,
    classification_allows,
    list_event_sources,
    log_trigger_event,
    match_event,
    normalize_event,
)

logger = get_logger(__name__)

# Headers the supported channels use to identify a delivery. Checked in order;
# the first present wins. Without one of these (or an id in the payload) an
# event cannot be de-duplicated, and we say so in the audit trail rather than
# pretending it was.
_DELIVERY_HEADERS = (
    "X-GitHub-Delivery",
    "X-GitLab-Event-UUID",
    "X-Gitlab-Event-UUID",
    "X-Request-Id",
    "X-Slack-Request-Timestamp",
    "X-Ms-Activity-Id",
)

#: Payload keys that carry a stable per-event id when no header does.
_DELIVERY_PAYLOAD_KEYS = ("event_id", "delivery_id", "id", "update_id")


def extract_delivery_id(payload: dict, headers: dict | None = None) -> str | None:
    """Best-effort stable identifier for this webhook delivery.

    Returns None when the event carries nothing stable — in which case the
    event is dispatched but NOT de-duplicated, and the audit row records that
    by leaving ``idempotency_key`` NULL.
    """
    for name in _DELIVERY_HEADERS:
        for key, value in (headers or {}).items():
            if key.lower() == name.lower() and value:
                return str(value)

    for key in _DELIVERY_PAYLOAD_KEYS:
        value = (payload or {}).get(key)
        if value not in (None, ""):
            return str(value)
    return None


def event_classification(channel_config: dict | None, source: dict | None) -> str:
    """The IL an event from this channel/source carries.

    The more sensitive of the two ceilings wins: a source declared at IL5 on a
    channel capped at IL4 is treated as IL5, because the source is asserting
    what the payload may contain.
    """
    channel_il = (channel_config or {}).get("max_il") or "IL2"
    source_il = (source or {}).get("max_il") or "IL2"
    return channel_il if IL_ORDER.get(channel_il, 0) >= IL_ORDER.get(source_il, 0) else source_il


def _event_type(envelope: Any, payload: dict) -> str:
    """The event type used for trigger matching."""
    for key in ("event_type", "type", "action"):
        value = (payload or {}).get(key)
        if isinstance(value, str) and value:
            return value
    return getattr(envelope, "command", "") or "event"


def source_for_channel(channel: str) -> dict | None:
    """The enabled ``gateway_channel`` source bound to this gateway channel.

    A source declares the channel it listens to in ``config_json``
    (``{"channel": "github"}``); its ``name`` is accepted as a fallback so a
    source registered simply as "github" works without extra config.
    """
    if not channel:
        return None
    wanted = channel.strip().lower()
    for src in list_event_sources(enabled_only=True):
        if src.get("kind") != "gateway_channel":
            continue
        cfg = src.get("config") or {}
        declared = str(cfg.get("channel") or "").strip().lower()
        if declared == wanted or str(src.get("name", "")).strip().lower() == wanted:
            return src
    return None


def dispatch_envelope(
    envelope: Any,
    channel_config: dict | None = None,
    payload: dict | None = None,
    headers: dict | None = None,
) -> dict:
    """Match a chain-cleared envelope against triggers and start the runs.

    Synchronous. The gateway calls :func:`dispatch_envelope_async` instead;
    this is the core, kept callable directly so it can be tested and so a
    non-HTTP caller (canvas bus, scheduler) can reuse it.

    Returns a summary dict — never raises into the caller, because a workflow
    binding problem must not turn a delivered webhook into an HTTP 500.
    """
    payload = payload if payload is not None else dict(getattr(envelope, "args", {}) or {})
    channel = getattr(envelope, "channel", "") or ""
    envelope_id = getattr(envelope, "id", "") or ""
    event_type = _event_type(envelope, payload)
    delivery_id = extract_delivery_id(payload, headers)

    started: list[dict] = []
    refused: list[dict] = []

    try:
        source = source_for_channel(channel)
    except Exception as exc:  # noqa: BLE001 - registry may not be migrated yet
        logger.warning("event dispatch: source lookup failed for %s: %s", channel, exc)
        return {"matched": 0, "started": [], "refused": [], "error": str(exc)}

    if not source:
        # Nothing is bound to this channel. Not an error — most gateway traffic
        # is chat commands, not workflow triggers — so this is not audited as a
        # trigger event either; there is no source row to attribute it to.
        return {"matched": 0, "started": [], "refused": [], "reason": "no source for channel"}

    source_id = source.get("source_id") or ""
    classification = event_classification(channel_config, source)

    try:
        event = normalize_event(source_id, event_type, payload)
        results = match_event(event)
    except Exception as exc:  # noqa: BLE001 - a lookup failure must not 500 the webhook
        logger.warning("event dispatch: trigger lookup failed for %s: %s", source_id, exc)
        return {"matched": 0, "started": [], "refused": [], "error": str(exc)}

    matches = [r for r in results if r.get("matched")]

    if not matches:
        reason = (
            f"no enabled trigger on source '{source.get('name', source_id)}' matched"
            if results else
            f"no enabled trigger on source '{source.get('name', source_id)}'"
        )
        log_trigger_event(
            source_id, None, event_type, payload,
            matched=False, outcome="no_match", reason=reason,
            classification=classification, envelope_id=envelope_id,
        )
        return {"matched": 0, "started": [], "refused": [], "classification": classification}

    for result in matches:
        outcome = _dispatch_one(
            result,
            source_id=source_id,
            payload=payload,
            event_type=event_type,
            classification=classification,
            delivery_id=delivery_id,
            envelope_id=envelope_id,
        )
        if outcome.get("run_id"):
            started.append(outcome)
        elif outcome.get("outcome") == "refused_classification":
            refused.append(outcome)

    return {
        "matched": len(matches),
        "started": started,
        "refused": refused,
        "classification": classification,
        "deduplicated": delivery_id is not None,
    }


def _dispatch_one(
    result: dict,
    *,
    source_id: str,
    payload: dict,
    event_type: str,
    classification: str,
    delivery_id: str | None,
    envelope_id: str,
) -> dict:
    """Evaluate and (if permitted) start one matched trigger's workflow run."""
    trigger = result.get("trigger") or {}
    trigger_id = trigger.get("trigger_id") or ""
    workflow_id = trigger.get("workflow_id") or ""
    workflow_il = trigger.get("workflow_il") or "IL6"

    common = {
        "outcome": "",
        "workflow_id": workflow_id,
        "classification": classification,
        "envelope_id": envelope_id,
    }

    # Classification gate: refuse BEFORE claiming the idempotency key, so the
    # refusal is auditable and a later re-registration at the right IL can
    # still run this delivery. Refusals therefore carry no key.
    if not classification_allows(classification, workflow_il):
        reason = (
            f"event classification {classification} exceeds workflow IL {workflow_il}"
        )
        logger.warning("event dispatch REFUSED: %s (workflow=%s)", reason, workflow_id)
        log_trigger_event(
            source_id, trigger_id, event_type, payload,
            matched=False, reason=reason,
            **{**common, "outcome": "refused_classification"},
        )
        return {"outcome": "refused_classification", "trigger_id": trigger_id, "reason": reason}

    # Idempotency claim, scoped per (source, delivery, trigger) so two triggers
    # bound to the same event each get their own run, while a replay gets none.
    idem_key = f"{source_id}:{delivery_id}:{trigger_id}" if delivery_id else None

    claim = log_trigger_event(
        source_id, trigger_id, event_type, payload,
        matched=True, reason="trigger matched", idempotency_key=idem_key,
        **{**common, "outcome": "matched"},
    )
    if claim is None:
        logger.info("event dispatch: duplicate delivery %s for trigger %s", delivery_id, trigger_id)
        return {"outcome": "duplicate", "trigger_id": trigger_id}

    try:
        from tools.studio.workflow_runner import start_run  # noqa: PLC0415

        # The mapped inputs go in through start_run (dwo-evt-04-d2) rather than
        # being seeded into run_memory afterwards — seeding after the worker
        # thread is already live races a first step that reads one.
        run_id = start_run(
            workflow_id,
            trigger.get("project_id", "default"),
            inputs=result.get("inputs") or {},
        )
    except Exception as exc:  # noqa: BLE001 - a bad binding must not break ingress
        logger.error("event dispatch: start_run failed for %s: %s", workflow_id, exc)
        log_trigger_event(
            source_id, trigger_id, event_type, payload,
            matched=False, reason=str(exc)[:500], **{**common, "outcome": "error"},
        )
        return {"outcome": "error", "trigger_id": trigger_id, "reason": str(exc)}

    # studio_trigger_events is append-only (NIST AU-9), so the resulting run_id
    # is recorded as a *second* row referencing the claim, never by UPDATE-ing
    # the first. The claim row proves the delivery was accepted; this one proves
    # what it started.
    log_trigger_event(
        source_id, trigger_id, event_type, payload,
        matched=True, run_id=run_id, reason=f"claim {claim}",
        **{**common, "outcome": "run_started"},
    )
    logger.info("event dispatch: started run %s for workflow %s", run_id, workflow_id)
    return {
        "outcome": "started",
        "trigger_id": trigger_id,
        "workflow_id": workflow_id,
        "run_id": run_id,
    }


def dispatch_envelope_async(
    envelope: Any,
    channel_config: dict | None = None,
    payload: dict | None = None,
    headers: dict | None = None,
) -> None:
    """Fire-and-forget dispatch — the webhook handler must never block on it."""
    threading.Thread(
        target=dispatch_envelope,
        args=(envelope, channel_config, payload, headers),
        name=f"studio-event-dispatch-{str(getattr(envelope, 'id', ''))[:8]}",
        daemon=True,
    ).start()
