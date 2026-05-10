# NetOps M02 — Anomaly Detection Agent

## Mission Brief

Your NOC receives a stream of network metrics every minute: latency, packet loss rate, port utilization, and BGP prefix counts. Currently, level-1 analysts manually scan dashboards and fire tickets. You're building an **Anomaly Detection Agent** that reads a metrics snapshot and surfaces the anomalies that matter — ranked by severity.

## What You'll Build

1. **`classify_anomaly(metric_name, value, threshold_dict)`** — Determine if a single metric is anomalous. Returns a classification dict or `None` if within normal range.

2. **`score_severity(anomalies)`** — Given a list of anomaly classifications, score each one (`critical` / `high` / `medium` / `low`) based on how far the value exceeds the threshold.

3. **`AnomalyAgent.run(metrics_snapshot)`** — Iterate all metrics, classify each, score results, return a ranked report.

## Thresholds

Use these thresholds (already defined as `THRESHOLDS` in the starter):

| Metric | Warning | Critical |
|--------|---------|----------|
| `latency_ms` | > 100 | > 500 |
| `packet_loss_pct` | > 1.0 | > 5.0 |
| `port_util_pct` | > 75 | > 95 |
| `bgp_prefix_delta` | > 500 | > 2000 |

## Data Contract

### Input to `classify_anomaly`

```python
classify_anomaly("latency_ms", 620, THRESHOLDS)
```

### Output of `classify_anomaly`

```python
{
    "metric": "latency_ms",
    "value": 620,
    "warning_threshold": 100,
    "critical_threshold": 500,
    "state": "critical"   # "normal" | "warning" | "critical"
}
```

Return `None` when `state` would be `"normal"` (value ≤ warning threshold).

### Input to `AnomalyAgent.run`

```python
{
    "device": "core-sw-01",
    "metrics": {
        "latency_ms": 620,
        "packet_loss_pct": 0.3,
        "port_util_pct": 88,
        "bgp_prefix_delta": 150
    }
}
```

### Output of `AnomalyAgent.run`

```python
{
    "device": "core-sw-01",
    "anomaly_count": 2,
    "anomalies": [
        {"metric": "latency_ms",    "value": 620, "state": "critical", "severity": "critical"},
        {"metric": "port_util_pct", "value": 88,  "state": "warning",  "severity": "high"},
    ],
    "highest_severity": "critical"
}
```

Anomalies are returned in descending severity order: `critical` → `high` → `medium` → `low`.

## Severity Scoring

For `score_severity`, assign each anomaly's `severity` field:

- `state == "critical"` → severity `"critical"`
- `state == "warning"` and value > 90% of the critical threshold → `"high"`
- `state == "warning"` and value > 50% of the critical threshold → `"medium"`
- Otherwise → `"low"`

## Implementation Tips

- In `classify_anomaly`, look up `metric_name` in `threshold_dict`. If not found, return `None`.
- The severity sort order (critical > high > medium > low) maps to integers; sort descending.
- `highest_severity` is the severity of the first anomaly after sorting.

## Grader Contract

The grader uses `SAMPLE_SNAPSHOT` with 3 anomalous metrics. It tests `classify_anomaly` with specific values, then `AnomalyAgent.run` for the full report. Match field names exactly.
