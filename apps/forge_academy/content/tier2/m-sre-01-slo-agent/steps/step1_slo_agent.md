---
ontology_id: icdev:mission:m-sre-01-slo-agent:step:1
step_class: icdev:Lesson
---

# SRE M01 — SLO Monitoring Agent

## Mission Brief

Your service has a 99.9% availability SLO. You have a 30-day error budget. Right now, your team checks Datadog manually to see if you're burning through it. You're replacing that check with an **SLO Monitoring Agent** that computes error budget consumption, burn rate, and tells on-call whether to page someone — automatically.

## What You'll Build

1. **`calculate_error_budget(slo_target_pct, window_hours, total_requests, error_requests)`** — Compute how much error budget has been consumed.

2. **`assess_burn_rate(budget_consumed_pct, window_hours)`** — Determine if the burn rate is sustainable given the remaining window.

3. **`SLOAgent.run(slo_config, metrics)`** — Pull it together: compute budget, assess burn rate, return an SRE action recommendation.

## Math

**Error budget**: the fraction of requests that *may* fail while still meeting the SLO.

```
budget_total_pct  = 100.0 - slo_target_pct          # e.g. 0.1% for 99.9% SLO
actual_error_pct  = (error_requests / total_requests) * 100
budget_consumed_pct = actual_error_pct / budget_total_pct * 100
```

**Burn rate**: how fast you're consuming budget relative to the window size.

```
# Normalize: if you consumed X% of budget in window_hours, and the budget window is 720h (30d)
burn_rate = (budget_consumed_pct / 100) / (window_hours / 720)
```

A burn rate of `1.0` means you'll exactly exhaust the budget at end of period. Above `2.0` = fast burn. Above `5.0` = critical.

## Data Contract

### Input to `calculate_error_budget`

```python
calculate_error_budget(99.9, 1, 50000, 120)
# slo_target=99.9%, window=1h, 50k requests, 120 errors
```

### Output of `calculate_error_budget`

```python
{
    "slo_target_pct": 99.9,
    "budget_total_pct": 0.1,
    "actual_error_pct": 0.24,
    "budget_consumed_pct": 240.0,   # 240% of budget consumed in 1h!
    "window_hours": 1
}
```

Round to 4 decimal places where needed.

### Output of `assess_burn_rate`

```python
{
    "burn_rate": 13.33,
    "status": "critical",    # "healthy" | "elevated" | "fast_burn" | "critical"
    "alert": True
}
```

Status thresholds:
- `burn_rate < 1` → `"healthy"`, `alert=False`
- `1 ≤ burn_rate < 2` → `"elevated"`, `alert=False`
- `2 ≤ burn_rate < 5` → `"fast_burn"`, `alert=True`
- `burn_rate ≥ 5` → `"critical"`, `alert=True`

### Output of `SLOAgent.run`

```python
{
    "service": "auth-service",
    "slo_target_pct": 99.9,
    "budget": { ... },      # from calculate_error_budget
    "burn_rate": { ... },   # from assess_burn_rate
    "recommendation": "PAGE_ONCALL",  # "NO_ACTION" | "MONITOR" | "PAGE_ONCALL"
}
```

Recommendation logic:
- `burn_rate.status == "healthy"` → `"NO_ACTION"`
- `burn_rate.status == "elevated"` → `"MONITOR"`
- `burn_rate.status in ("fast_burn", "critical")` → `"PAGE_ONCALL"`

## Grader Contract

The grader uses `SAMPLE_SLO_CONFIG` (99.9% SLO) and `SAMPLE_METRICS` (50k requests, 120 errors in 1 hour). Expected: `budget_consumed_pct=240.0`, `burn_rate≥5`, `recommendation="PAGE_ONCALL"`.
