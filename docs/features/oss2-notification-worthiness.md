# CUI // SP-CTI

# Notification worthiness gate (oss2-triage-01)

**Card:** OSS-02 Nine-Project Adaptation (`oss2-`)
**Spike:** [docs/spikes/oss-02-nine-project-adaptation.md](../spikes/oss-02-nine-project-adaptation.md) §4
**Upstream (pattern only):** agent-chief (MIT) — registered in `_ATTRIBUTION_REGISTRY`.

## The one genuinely new idea

Of the nine projects in the OSS-02 sweep, agent-chief was the only one with a
capability ICDEV did not already own: a scored **worthiness** stage that decides,
before routing, whether an incoming event should **interrupt** a human, **dispatch**
to an agent, or **file** for later. `tools/notifications/` already has routing,
escalation, acknowledgement and preferences — what was missing is the decision
*upstream* of `resolve_channels` about whether an event deserves attention at all.

This matters here because ICDEV generates exactly agent-chief's event-volume profile:
dozens of Genesis reflexes on schedules (awareness every 3h, foundry every 12h, OSINT
every 4h, …), an autonomous kanban board, and an awareness engine promoting
predictions into tasks.

## Scope — deliberately narrow

Per the spike (§4.3): adopt the **pattern**, not the package (agent-chief is Python
3.12+ against ICDEV's 3.9 floor), and **not** a new notification system. This is one
config-driven scored stage over the existing subsystem:

- `tools/notifications/worthiness.py` — `score_worthiness(event_type, severity, metadata)` → `WorthinessDecision(action, score, reason)`.
- `args/notification_worthiness.yaml` — severity weights, per-event-type modifiers, interrupt/dispatch thresholds.
- **Off by default** (`enabled: false`): the decision carries `enabled=False` and `action=interrupt`, so until an operator opts in, routing behaves exactly as today.

## Evaluated, not asserted

agent-chief's own posture is that every metric is backed by a test a reader can run,
and its headline is "24 events in → 1 interruption." `evaluate_stream()` reports the
distribution over a representative stream so the filter rate is a measured number.

On a representative 29-event stream (12 heartbeats, 6 routine awareness scans, 4
digests, 3 blocked-kanban, 2 CI failures, 2 critical security alerts):

| action | count | rate |
|---|---|---|
| interrupt | 4 | 13.8% |
| dispatch | 3 | 10.3% |
| **file** | **22** | **75.9%** |

**events per interrupt: 7.25** — the scheduled/routine flood is filed, the real
alerts interrupt. (More conservative than agent-chief's ~24:1; the thresholds are
tunable against a real stream, which is the point of shipping the evaluator.)

## Recommendation

**Adopt, narrowly, and keep it off until tuned on real traffic.** The pattern is
sound and the gap is real, but the thresholds should be calibrated against ICDEV's
actual notification stream (via `evaluate_stream`) before `enabled: true` — turning
it on with guessed weights would mis-file real alerts. Wiring it into `gateway.send`
in front of `resolve_channels` is a one-line opt-in guarded by `is_enabled()`; that
wiring is intentionally left for the tuning step so this task ships the *evaluated
capability*, not an un-calibrated behavior change.

## Reproduce

```bash
python tools/notifications/worthiness.py --event-type security_alert --severity critical --json
```

# CUI // SP-CTI
