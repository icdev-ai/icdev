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

1. **No new ingress.** There is no route in this file. ``dispatch_envelope`` is
   called from inside the existing gateway handler, after the chain passes. An
   event that fails a gate never reaches this module, so it starts nothing —
   and the gateway's own audit records the rejection.
2. **Idempotency.** Every webhook platform retries. The unique index on
   ``studio_trigger_events.idempotency_key`` is the mechanism: the audit row is
   written *before* the run starts, so a replay loses the INSERT and returns
   without starting anything. Mirrors ``kanban_tasks.idempotency_key``.
3. **Classification.** A run inherits the event's IL — the more conservative of
   the channel ceiling and the registered source ceiling. A trigger pointing at
   a workflow rated below that IL is refused and audited, never downgraded.

Dispatch is asynchronous relative to the HTTP response: the gateway calls
``dispatch_envelope_async``, which hands the work to a daemon thread and
returns immediately.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from tools.studio.event_sources import (
    IL_ORDER,
    classification_allows,
    match_event,
    record_trigger_event,
)

logger = logging.getLogger(__name__)

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

# Payload keys that carry a stable per-event id when no header does.
_DELIVERY_PAYLOAD_KEYS = ("event_id", "delivery_id", "id", "update_id")


def extract_delivery_id(payload: dict, headers: dict | None = None) -> str | None:
    """Best-effort stable identifier for this webhook delivery.

    Returns None when the event carries nothing stable — in which case the
    event is dispatched but NOT de-duplicated, and the audit row records that.
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
    source_name = getattr(envelope, "channel", "") or ""
    envelope_id = getattr(envelope, "id", "") or ""
    event_type = _event_type(envelope, payload)
    delivery_id = extract_delivery_id(payload, headers)

    started: list[dict] = []
    refused: list[dict] = []

    try:
        triggers = match_event(source_name, event_type, payload)
    except Exception as exc:  # noqa: BLE001 - registry may not be migrated yet
        logger.warning("event dispatch: trigger lookup failed for %s: %s", source_name, exc)
        return {"matched": 0, "started": [], "refused": [], "error": str(exc)}

    if not triggers:
        record_trigger_event(
            "no_match",
            event_type=event_type,
            reason=f"no enabled trigger on source '{source_name}'",
            payload=payload,
            envelope_id=envelope_id,
        )
        return {"matched": 0, "started": [], "refused": []}

    classification = event_classification(channel_config, triggers[0].get("source"))

    for trigger in triggers:
        result = _dispatch_one(
            trigger,
            payload=payload,
            event_type=event_type,
            classification=classification,
            delivery_id=delivery_id,
            envelope_id=envelope_id,
        )
        if result.get("run_id"):
            started.append(result)
        elif result.get("outcome") == "refused_classification":
            refused.append(result)

    return {
        "matched": len(triggers),
        "started": started,
        "refused": refused,
        "classification": classification,
        "deduplicated": delivery_id is not None,
    }


def _dispatch_one(
    trigger: dict,
    *,
    payload: dict,
    event_type: str,
    classification: str,
    delivery_id: str | None,
    envelope_id: str,
) -> dict:
    """Evaluate and (if permitted) start one trigger's workflow run."""
    source = trigger.get("source") or {}
    source_id = source.get("source_id")
    trigger_id = trigger["trigger_id"]
    workflow_id = trigger["workflow_id"]

    common = {
        "source_id": source_id,
        "trigger_id": trigger_id,
        "workflow_id": workflow_id,
        "event_type": event_type,
        "classification": classification,
        "payload": payload,
        "envelope_id": envelope_id,
    }

    # Classification gate: refuse before claiming the idempotency key, so the
    # refusal is auditable and a later re-registration at the right IL can
    # still run. Refusals carry no key for that reason.
    if not classification_allows(classification, trigger.get("workflow_il", "IL6")):
        reason = (
            f"event classification {classification} exceeds workflow IL "
            f"{trigger.get('workflow_il')}"
        )
        logger.warning("event dispatch REFUSED: %s (workflow=%s)", reason, workflow_id)
        record_trigger_event("refused_classification", reason=reason, **common)
        return {"outcome": "refused_classification", "trigger_id": trigger_id, "reason": reason}

    # Idempotency claim. Per (source, delivery, trigger) so that two triggers
    # bound to the same event each get their own run, but a replay gets none.
    idem_key = None
    if delivery_id:
        idem_key = f"{source_id or trigger.get('source_id')}:{delivery_id}:{trigger_id}"

    event_row = record_trigger_event("matched", idempotency_key=idem_key, **common)
    if event_row is None:
        logger.info("event dispatch: duplicate delivery %s for trigger %s", delivery_id, trigger_id)
        return {"outcome": "duplicate", "trigger_id": trigger_id}

    try:
        from tools.studio.workflow_runner import start_run  # noqa: PLC0415

        run_id = start_run(workflow_id, trigger.get("project_id", "default"))
    except Exception as exc:  # noqa: BLE001 - a bad binding must not break ingress
        logger.error("event dispatch: start_run failed for %s: %s", workflow_id, exc)
        record_trigger_event("error", reason=str(exc)[:500], **common)
        return {"outcome": "error", "trigger_id": trigger_id, "reason": str(exc)}

    # studio_trigger_events is append-only (NIST AU-9), so the resulting run_id
    # is recorded as a *second* row referencing the claim, never by UPDATE-ing
    # the first one. The claim row proves the delivery was accepted; this one
    # proves what it started.
    record_trigger_event(
        "run_started",
        run_id=run_id,
        reason=f"claim {event_row}",
        **common,
    )
    logger.info("event dispatch: started run %s for workflow %s", run_id, workflow_id)
    return {"outcome": "started", "trigger_id": trigger_id, "workflow_id": workflow_id, "run_id": run_id}


def dispatch_envelope_async(
    envelope: Any,
    channel_config: dict | None = None,
    payload: dict | None = None,
    headers: dict | None = None,
) -> None:
    """Fire-and-forget dispatch — the webhook handler must never block on it."""
    thread = threading.Thread(
        target=dispatch_envelope,
        args=(envelope, channel_config, payload, headers),
        name=f"studio-event-dispatch-{getattr(envelope, 'id', '')[:8]}",
        daemon=True,
    )
    thread.start()
