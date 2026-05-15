---
ontology_id: icdev:mission:m-netops-04-capstone:step:1
step_class: icdev:Lesson
---

# NetOps M04 — NetOps Capstone

## Mission Brief

You've built three separate agents: topology mapping, anomaly detection, and auto-remediation. Now wire them into a single **NOC Pipeline** — a unified agent that accepts raw device configs, detects any anomalies, and returns a ready-to-execute remediation plan in one call.

This is the full NOC automation loop. No analyst in the middle.

## What You'll Build

**`NOCPipeline.run(raw_configs, metrics_by_device)`** — The end-to-end orchestrator:

1. Parse all device configs → build topology (using your M01 logic)
2. For each device in `metrics_by_device`, run anomaly detection (M02 logic)
3. For each device with anomalies, generate a remediation plan (M03 logic)
4. Return a unified NOC report

## Data Contract

### Input

```python
raw_configs = [...]           # list of raw config strings (M01 format)
metrics_by_device = {
    "core-sw-01": {
        "latency_ms": 620,
        "packet_loss_pct": 0.3,
        "port_util_pct": 88,
        "bgp_prefix_delta": 150,
    },
    "access-sw-01": {
        "latency_ms": 45,
        "packet_loss_pct": 0.1,
        "port_util_pct": 40,
        "bgp_prefix_delta": 10,
    }
}
```

### Output of `NOCPipeline.run`

```python
{
    "topology": {
        "node_count": 3,
        "edge_count": 2,
        "unknown_nodes": ["edge-rtr-01"]
    },
    "devices_checked": 2,
    "devices_with_anomalies": 1,
    "remediation_plans": [
        {
            "device": "core-sw-01",
            "anomaly_count": 2,
            "highest_severity": "critical",
            "plan_count": 2,
            "ready_to_execute": True
        }
    ],
    "overall_status": "ACTION_REQUIRED"   # "CLEAN" | "ACTION_REQUIRED"
}
```

`overall_status` is `"ACTION_REQUIRED"` if any device has anomalies; `"CLEAN"` if none.

## Implementation Notes

You must implement all three helper functions inline (you cannot import M01–M03 files). The starter provides `THRESHOLDS`, `PLAYBOOKS`, and `SAMPLE_CONFIGS`/`SAMPLE_METRICS` from the previous missions.

The grader specifically tests:
- `topology["node_count"]` == 3
- `devices_with_anomalies` == 1 (only core-sw-01 has anomalies)
- `overall_status` == `"ACTION_REQUIRED"`
- The single remediation plan has `device == "core-sw-01"` and `ready_to_execute == True`

## Implementation Tips

Reuse your logic from M01–M03:
- **Topology**: same `parse_device_config` + `build_topology` logic
- **Anomaly**: iterate each device's metrics dict, call classify/score logic
- **Remediation**: for each anomalous device, select + validate playbooks

You're not expected to import — inline the logic or adapt it. The goal is demonstrating you can wire a multi-stage agent pipeline.
