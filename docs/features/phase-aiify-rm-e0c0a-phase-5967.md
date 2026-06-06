# CUI // SP-CTI

# Metric Anomaly Detection — duplicate of phase 5970 (aiify-rm-e0c0a-phase-5967)

## Summary

AI-ify opportunity **5967** (`hardcoded_threshold -> anomaly_detection`, roadmap
`rm-e0c0aade36`, Phase 2 — Core Modernization). The scanner targeted an external
repo's `lib/cw_metrics_util.py` — a temp `aiify_git_*` clone that the engine
clones and deletes — flagging a hardcoded CloudWatch-style metric threshold for
replacement with baseline-relative anomaly detection.

This opportunity is a **duplicate of phase 5970**. Both 5967 and 5970 are the
same pattern (`hardcoded_threshold -> anomaly_detection`) on the same roadmap
(`rm-e0c0aade36`), and both external metrics-style files (`cw_metrics_util.py`
and `add_athena_partitions.py`) map to the **same analogous ICDEV internal
subsystem** under the established external-repo disposition: the Prometheus
metric collector, `tools/monitor/metric_collector.py`. The collector judged
time-series `metric_snapshots` against a hardcoded `DEFAULT_SLA` threshold map —
exactly the hardcoded-threshold smell 5967 describes.

## Resolution

No new code authored — that would create a competing copy of an existing
augmentation. The deliverable for 5967 was already fully satisfied by the work
committed under phase **5970** (commit `72dce6c66`):

- `detect_metric_anomalies()` / `_detect_value_anomaly()` in
  `tools/monitor/metric_collector.py` — baseline-relative z-score and robust
  MAD scoring against each metric's own history, complementing the fixed-line
  `check_sla()`.
- `metric_anomaly` tuning block in `args/monitoring_config.yaml` (method,
  z_threshold, mad_threshold, min_samples, direction, history_limit) with
  safe degrade-to-defaults.
- CLI surface: `--detect-anomalies` / `--anomaly-method` on the collector.
- `tests/test_metric_collector_anomaly.py` — **22 tests, all passing**.

See [`phase-aiify-rm-e0c0a-phase-5970.md`](phase-aiify-rm-e0c0a-phase-5970.md)
for the full implementation write-up.

## Status

**Done** — duplicate of 5970; deliverable verified present, committed, and
tested (22/22 green). Closed without competing implementation.
