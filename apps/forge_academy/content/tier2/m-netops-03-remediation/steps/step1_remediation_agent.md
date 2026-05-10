# NetOps M03 — Auto-Remediation Agent

## Mission Brief

Your anomaly detector fires. Now what? A senior network engineer gets paged, digs through runbooks, and manually applies fixes. That loop takes 20–45 minutes. You're eliminating the manual step with an **Auto-Remediation Agent** that maps anomaly types to remediation playbooks, validates the playbook steps, and generates an execution plan the NOC can approve in one click.

## What You'll Build

1. **`select_playbook(anomaly_type, severity)`** — Look up the remediation playbook for a given anomaly type. Return the matching playbook or a default fallback.

2. **`validate_playbook(steps)`** — Validate that a playbook's steps are correctly formed. Each step must have `action`, `target`, and `description`. Return a validation result dict.

3. **`RemediationAgent.run(anomaly_report)`** — Given the output of `AnomalyAgent.run` (from M02), select and validate a playbook for each anomaly, return a prioritized execution plan.

## Playbook Catalog

Use `PLAYBOOKS` (defined in the starter) — a dict keyed by anomaly type:

| Key | Steps |
|-----|-------|
| `latency_ms` | flush ARP cache → restart interface → open TAC case if critical |
| `packet_loss_pct` | check duplex mismatch → disable/re-enable port → capture traffic |
| `port_util_pct` | identify top talkers → apply QoS policy → consider link bundle |
| `bgp_prefix_delta` | check peer state → apply route filter → notify NOC team |
| `default` | collect diagnostics → escalate to L2 |

## Data Contract

### Output of `select_playbook`

```python
{
    "anomaly_type": "latency_ms",
    "severity": "critical",
    "playbook_name": "Latency Remediation",
    "steps": [
        {"action": "flush_arp",       "target": "device",  "description": "Flush ARP cache on affected device"},
        {"action": "restart_iface",   "target": "interface", "description": "Bounce interface to reset counters"},
        {"action": "open_tac_case",   "target": "tac",     "description": "Open TAC case — critical severity"},
    ]
}
```

The `steps` list for critical severity must include all steps; for warning severity, skip any steps marked `critical_only: true`.

### Output of `validate_playbook`

```python
{
    "valid": True,
    "step_count": 3,
    "errors": []
}
```

If any step is missing a required field:

```python
{
    "valid": False,
    "step_count": 3,
    "errors": ["Step 2 missing 'target' field"]
}
```

### Output of `RemediationAgent.run`

```python
{
    "device": "core-sw-01",
    "plan_count": 2,
    "execution_plan": [
        {
            "anomaly": "latency_ms",
            "severity": "critical",
            "playbook": { ... },
            "validation": { "valid": True, "step_count": 3, "errors": [] }
        },
        ...
    ],
    "ready_to_execute": True   # True only if ALL playbooks pass validation
}
```

## Implementation Tips

- In `select_playbook`, if `anomaly_type` is not in `PLAYBOOKS`, use the `"default"` key.
- `validate_playbook` iterates `steps` and checks that each dict has all three required keys.
- In `RemediationAgent.run`, consume `anomaly_report["anomalies"]` and `anomaly_report["device"]`.
- `ready_to_execute` is `True` only when every validation result has `"valid": True`.

## Grader Contract

The grader injects a pre-built `anomaly_report` matching M02's output format (2 anomalies: latency_ms critical, port_util_pct high). It tests `select_playbook`, `validate_playbook`, then the full `run()` method.
