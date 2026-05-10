# SRE M04 — SRE Capstone

## Mission Brief

You've built three SRE agents: SLO monitoring, incident response, and chaos engineering. Now you're running the reliability operations center. Wire all three into a single **SRE Reliability Pipeline** that ingests real-time service metrics, fires the right SRE action (page, remediate, or validate through chaos), and returns a unified reliability report.

This is the full SRE automation loop.

## What You'll Build

**`SREPipeline.run(services)`** — For each service in the input:
1. Compute SLO health (M01 logic) → if burn rate is `"critical"` or `"fast_burn"`, `recommendation="PAGE_ONCALL"`
2. If recommendation is `"PAGE_ONCALL"`, generate an incident brief (M02 logic) with the highest-impact alert
3. If recommendation is `"MONITOR"` or `"NO_ACTION"`, design a chaos experiment for proactive validation (M03 logic)
4. Return a unified reliability report across all services

## Input Format

```python
services = [
    {
        "service": "auth-service",
        "slo_target_pct": 99.9,
        "window_hours": 1,
        "metrics": {
            "total_requests": 50000,
            "error_requests": 120,
            "latency_p99_ms": 5200,
            "error_rate_pct": 2.5,
            "user_facing": True,
        }
    },
    {
        "service": "cache-service",
        "slo_target_pct": 99.5,
        "window_hours": 24,
        "metrics": {
            "total_requests": 500000,
            "error_requests": 100,
            "latency_p99_ms": 30,
            "error_rate_pct": 0.02,
            "user_facing": False,
        }
    }
]
```

## Output Format

```python
{
    "services_evaluated": 2,
    "paged": 1,
    "monitored_with_chaos": 1,
    "results": [
        {
            "service": "auth-service",
            "slo_health": {
                "recommendation": "PAGE_ONCALL",
                "burn_rate_status": "critical"
            },
            "action": "paged",
            "incident": {
                "incident_id": "INC-auth-service-error_rate",
                "severity": "SEV2",
                "page_oncall": True
            }
        },
        {
            "service": "cache-service",
            "slo_health": {
                "recommendation": "NO_ACTION",
                "burn_rate_status": "healthy"
            },
            "action": "chaos_scheduled",
            "chaos": {
                "experiment_id": "exp-cache-service-network_delay",
                "risk_level": "medium",
                "safe_to_run": True
            }
        }
    ],
    "overall_status": "DEGRADED"   # "HEALTHY" | "DEGRADED" | "CRITICAL"
}
```

`overall_status`:
- `"CRITICAL"` if any service produced `PAGE_ONCALL`
- `"DEGRADED"` if any service produced `MONITOR`
- `"HEALTHY"` if all services are `NO_ACTION`

## Implementation Notes

You must inline all M01–M03 logic (no imports). The starter provides all constants: `SLO_THRESHOLDS` (for burn rate), `RUNBOOKS`, `FAILURE_MODES`, `SERVICE_DEPENDENCIES`.

For chaos experiments on healthy services: use `"network_delay"` as the default failure mode.

For incident generation: synthesize an alert from the service metrics dict (set `alert_type` based on which metric is worst — prefer `"error_rate"` if `error_rate_pct > 1.0`, else `"latency"`).

## Grader Contract

The grader uses `SAMPLE_SERVICES` (auth-service: critical burn, cache-service: healthy). Expected: `paged=1`, `monitored_with_chaos=1`, `overall_status="CRITICAL"`.
