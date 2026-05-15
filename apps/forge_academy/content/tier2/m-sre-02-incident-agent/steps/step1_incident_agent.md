---
ontology_id: icdev:mission:m-sre-02-incident-agent:step:1
step_class: icdev:Lesson
---

# SRE M02 — Incident Response Agent

## Mission Brief

An alert fires at 2 AM. Your on-call engineer is groggy, staring at a Slack message: "auth-service latency P99 > 5s". They need to know: what severity is this, who needs to be paged, and what's the first thing to do?

You're building an **Incident Response Agent** that classifies incoming alerts, selects the right runbook, and returns a structured incident brief — in under 100ms.

## What You'll Build

1. **`classify_incident(alert)`** — Assess an alert dict and assign a severity level (`SEV1`–`SEV4`) based on impact signals.

2. **`select_runbook(service, incident_type)`** — Look up the runbook for the service/incident type combination.

3. **`IncidentAgent.run(alert)`** — Classify → select runbook → return incident brief.

## Severity Matrix

| Condition | Severity |
|-----------|----------|
| User-facing impact + error rate > 10% | SEV1 |
| User-facing impact + error rate 1–10% OR latency > 2000ms | SEV2 |
| Latency 500–2000ms (any service) | SEV3 |
| Anything else (warnings, internal only, low latency) | SEV4 |

Use `alert["user_facing"]` (bool), `alert["error_rate_pct"]` (float), and `alert["latency_p99_ms"]` (int) for classification. Check conditions in order: SEV1 first, then SEV2, then SEV3 (latency 500-2000ms), then SEV4 (default).

## Data Contract

### Input alert

```python
{
    "service": "auth-service",
    "alert_type": "latency",
    "user_facing": True,
    "error_rate_pct": 2.5,
    "latency_p99_ms": 5200,
    "region": "us-east-1"
}
```

### Output of `classify_incident`

```python
{
    "severity": "SEV2",
    "user_facing": True,
    "impact": "User-facing latency P99 5200ms exceeds threshold",
    "page_oncall": True    # True for SEV1/SEV2, False for SEV3/SEV4
}
```

### Runbook catalog (`RUNBOOKS`)

| service | alert_type | first_step |
|---------|-----------|------------|
| `auth-service` | `latency` | "Check DB connection pool saturation" |
| `auth-service` | `error_rate` | "Check upstream token service health" |
| `payment-service` | `latency` | "Check Stripe API status page" |
| `payment-service` | `error_rate` | "Check fraud detection service circuit breaker" |
| `default` | any | "Check service logs and metrics dashboard" |

### Output of `select_runbook`

```python
{
    "service": "auth-service",
    "incident_type": "latency",
    "runbook_name": "Auth Service Latency Runbook",
    "steps": ["Check DB connection pool saturation", "Review slow query log", "Scale read replicas if needed"],
    "escalation_path": ["oncall-sre", "auth-team-lead"]
}
```

### Output of `IncidentAgent.run`

```python
{
    "incident_id": "INC-auth-service-latency",
    "service": "auth-service",
    "classification": { ... },   # from classify_incident
    "runbook": { ... },          # from select_runbook
    "summary": "SEV2: auth-service latency in us-east-1 — page oncall"
}
```

`incident_id` format: `"INC-{service}-{alert_type}"`.

## Implementation Tips

- For SEV classification, check conditions in order: SEV1 first (strictest), then SEV2, SEV3, SEV4.
- `page_oncall` is `True` for SEV1 and SEV2 only.
- In `select_runbook`, try `(service, incident_type)` key first; fall back to `"default"`.
- `summary` should be a single human-readable string mentioning severity, service, and region.

## Grader Contract

The grader uses `SAMPLE_ALERT` (auth-service latency, user_facing=True, error_rate=2.5%, P99=5200ms). Expected: `severity="SEV2"`, `page_oncall=True`, runbook for auth-service/latency.
