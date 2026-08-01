# CUI // SP-CTI

# crx-not-01 — Notification Routing, Escalation & Per-User Preferences (Disposition ADR)

Status: Implemented
Card: crx (Component Review Remediation) — task `crx-not-01`
Related doc gaps: notification_service gaps #1 (routing), #2 (escalation), #4 (per-user preferences)

## Context

The CRX component review flagged the notification subsystem as missing (1) a
routing-rules engine, (2) escalation of unacknowledged critical alerts, and
(3) per-user channel preferences. The task brief also warned that the review
doc understated the existing surface. This ADR records what was verified,
which layer was chosen canonical, and what was built vs. dispositioned.

## Layer audit — which stack is canonical

Two notification stacks exist:

| Layer | Files | Role |
|-------|-------|------|
| **`tools/notifications/` (gateway)** | `gateway.py` (`NotificationGateway`), `adapters/*` | **CANONICAL delivery layer.** Real adapters (slack, teams, email, telegram, webhook, mattermost, ...), rate limiting, PII sanitization, append-only `notification_log` audit, and an existing `event_type -> adapters` routing map loaded from `args/notification_config.yaml`. This is the layer that actually delivers. |
| `tools/notification_service/` | `alert_service`, `digest_service`, `event_service`, `handler_service`, `render_handler_service`, `report_service` | Domain-specific `db -> render -> notify` render chains (STIG/POA&M/Kanban/Genesis/Oracle). Their transport primitives (`send`/`notify`/`publish`/`dispatch`/`emit`) are **stubs** "replaced at runtime by injected service implementations" — this is a rendering/orchestration layer, not a delivery layer. |

**Decision: `tools/notifications/` (the `NotificationGateway`) is canonical.**
The gateway already owns the `event_type -> adapters` routing dimension, adapter
registry, rate limiting, sanitization, and the proven audit sink. The new
routing/escalation/preferences modules therefore live in `tools/notifications/`
alongside it and return the same adapter channel keys the gateway understands.
No third parallel stack was created.

Corrections to the review doc, confirmed by reading the code:
- The `notification_service/*` layer is **richer** than the doc implied (six
  services), but its transports are stubs — it is not the delivery layer.
- The canvas event bus is **DB-persisted** (`audit_trail` / `notification_log`),
  not in-memory as the doc claimed.

## What was built (genuine gaps)

All in `tools/notifications/` (mirrored to `icdev/tools/notifications/`):

1. **`routing_rules.py`** — `resolve_channels(severity, component, tenant_id, default)`
   evaluates `(severity x component x tenant) -> channels` rules from
   `args/notification_routing.yaml` at **send time**. Union of all matching
   rules, order-preserving + de-duplicated, `default_channels` fallback.
   Small, stable, side-effect-free — this is the contract crx-gen-02 and DMX
   `dmx-loop-01` consume.

2. **`escalation.py`** — unacknowledged **critical** alerts re-route after a
   timeout. `register_alert()` persists a tracked alert; `acknowledge()` marks
   it acked (ack = clicking the `ack_link` which hits an API route, or an
   API/CLI call — idempotent); `process_escalations(now=...)` is a **synchronous
   sweep** meant to be called by an existing reflex/scheduler tick (no new
   always-on daemon), fully testable via injected `now`. State table
   `notification_escalations` carries `tenant_id` + `classification` (RLS).
   It is **mutable state** (pending -> acknowledged / escalated); every
   transition is additionally journaled to the immutable `audit_trail` via the
   shared `atomic_log_event` helper, so the table is intentionally **not** in
   `APPEND_ONLY_TABLES`.

3. **`preferences.py`** — per-user channels + quiet hours (midnight-wrap aware,
   IANA timezone) + digest opt-in. `resolve_user_channels()` narrows
   routing-resolved channels to what the user wants and suppresses everything
   during quiet hours **unless** the alert is critical (config-controlled
   bypass). State table `notification_preferences` keyed `(user_id, tenant_id)`
   with `classification` (RLS).

4. **`digest_service.should_deliver_digest()`** — extends (does not fork) the
   existing digest service to honour the new preferences (opt-in + quiet hours).

Backing migration: `286_notification_routing_escalation.sql`. Both tables also
self-create at runtime via `_ensure_schema()` so a checkout that has not run the
migration degrades gracefully.

## Dispositioned as already-covered (not rebuilt)

- **Adapter/channel delivery, rate limiting, PII sanitization, delivery audit** —
  already provided by `NotificationGateway` + `notification_log`. Reused, not
  duplicated.
- **Compliance/domain alert rendering** (CAT-I, STIG, POA&M, Kanban, Genesis,
  Oracle) — already in `notification_service/*`. Untouched; the new routing
  engine complements them.
- **`event_type -> adapters` routing** — the gateway already has a coarse map;
  the new engine adds the finer `severity x component x tenant` dimensions
  without replacing it.

## Rejected / deferred

- **On-call / PagerDuty integration — REJECTED for now.** No paging provider
  integration was built. The `escalation.py` timeout/re-route mechanism covers
  the "unacked critical gets re-routed" requirement using existing channels;
  a dedicated paging provider can be added later as another gateway adapter
  without changing the public routing/escalation API.

## Public API surface (kept small + stable for crx-gen-02 / DMX)

```
routing_rules.resolve_channels(severity, component=None, tenant_id=None, default=None) -> list[str]
escalation.register_alert(alert_id, severity, tenant_id, classification, channels, component=None, timeout_minutes=None, escalation_channels=None, now=None) -> dict
escalation.acknowledge(ack_token, actor="system", now=None) -> dict
escalation.process_escalations(now=None, tenant_id=None) -> list[dict]
preferences.get_preferences / set_preferences / in_quiet_hours / resolve_user_channels / wants_digest
```

## Tests

`tests/crx/test_notification_routing.py` — 21 deterministic tests (no network):
routing precedence/union/tenant-scope/default, escalation register/ack/
timeout-fires-once/acked-does-not-escalate, quiet-hours (normal + midnight wrap)
+ channel narrowing + critical bypass, and the digest opt-in gate.
