# SRE M03 — Chaos Engineering Agent

## Mission Brief

Chaos engineering is how Netflix found out their services would survive an AWS AZ failure before it happened in production. You're going to build a **Chaos Engineering Agent** that designs fault injection experiments, estimates blast radius, and verifies that steady-state metrics hold after the experiment runs.

This is proactive reliability work: find the weaknesses before your users do.

## What You'll Build

1. **`design_experiment(service, failure_mode)`** — Create a chaos experiment spec for a service/failure-mode combination.

2. **`estimate_blast_radius(experiment)`** — Predict the impact scope of the experiment (which users/services are affected).

3. **`ChaosAgent.run(service, failure_mode, baseline_metrics, post_metrics)`** — Design the experiment, estimate blast radius, verify steady-state (did metrics hold?), return a chaos report.

## Failure Modes

Your agent supports 5 failure modes (defined in `FAILURE_MODES` in the starter):

| `failure_mode` | Description |
|---------------|-------------|
| `pod_failure` | Kill 50% of running pods |
| `network_delay` | Inject 300ms latency on all outbound calls |
| `disk_full` | Fill disk to 95% capacity |
| `cpu_stress` | Peg CPU at 100% for 60 seconds |
| `dependency_down` | Take a downstream service offline |

## Data Contract

### Output of `design_experiment`

```python
{
    "experiment_id": "exp-auth-service-network_delay",
    "service": "auth-service",
    "failure_mode": "network_delay",
    "description": "Inject 300ms latency on all outbound calls from auth-service",
    "duration_seconds": 60,
    "rollback": "Remove tc netem rule from service network namespace",
    "hypothesis": "auth-service maintains < 2000ms P99 latency and > 99% success rate"
}
```

`experiment_id` format: `"exp-{service}-{failure_mode}"`.

### Output of `estimate_blast_radius`

```python
{
    "experiment_id": "exp-auth-service-network_delay",
    "affected_users_pct": 100,    # 100% for user-facing services
    "affected_downstream": ["payment-service", "session-store"],
    "risk_level": "medium",       # "low" | "medium" | "high"
    "safe_to_run": True
}
```

Risk levels:
- `pod_failure` or `dependency_down` → `"high"`, `safe_to_run=False` (require approval)
- `network_delay` or `cpu_stress` → `"medium"`, `safe_to_run=True`
- `disk_full` → `"low"`, `safe_to_run=True`

### Steady-State Verification

After the experiment, compare `baseline_metrics` vs `post_metrics`. A metric fails if it degrades beyond the hypothesis tolerance:

| Metric | Tolerated degradation |
|--------|-----------------------|
| `error_rate_pct` | ≤ 1.0 percentage point increase |
| `latency_p99_ms` | ≤ 50% increase |
| `success_rate_pct` | ≤ 1.0 percentage point decrease |

### Output of `ChaosAgent.run`

```python
{
    "experiment": { ... },
    "blast_radius": { ... },
    "steady_state_passed": True,
    "metric_results": [
        {"metric": "error_rate_pct", "baseline": 0.1, "post": 0.3, "passed": True},
        {"metric": "latency_p99_ms", "baseline": 120, "post": 195, "passed": True},
    ],
    "conclusion": "PASSED"   # "PASSED" | "FAILED" | "SKIPPED"
}
```

`conclusion` is:
- `"SKIPPED"` if `blast_radius["safe_to_run"]` is False
- `"PASSED"` if all metric_results passed
- `"FAILED"` if any metric_result failed

## Grader Contract

The grader uses `SAMPLE_SERVICE="auth-service"`, `FAILURE_MODE="network_delay"`, and pre-defined baseline/post metrics where steady-state holds (conclusion should be `"PASSED"`). It also tests a high-risk mode (`pod_failure`) that should produce `"SKIPPED"`.
