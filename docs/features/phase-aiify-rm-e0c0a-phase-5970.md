# CUI // SP-CTI

# Metric Anomaly Detection — `metric_collector.py` (aiify-rm-e0c0a-phase-5970)

## Summary

AI-ify opportunity **5970** (`hardcoded_threshold -> anomaly_detection`,
roadmap `rm-e0c0aade36`, Phase 2 — Core Modernization). The scanner targeted an
external repo's `log_parser/add_athena_partitions.py` — a temp `aiify_git_*`
clone that the engine clones and deletes — so per the established disposition
for external-repo opportunities, the change landed in the **analogous ICDEV
internal subsystem**: `tools/monitor/metric_collector.py`.

`add_athena_partitions.py` manages time-partitioned data using fixed thresholds.
Its closest ICDEV analog is the Prometheus metric collector, which stores
time-series `metric_snapshots` and judges them with a hardcoded `DEFAULT_SLA`
threshold map. Static SLA lines answer only "is this value above a fixed line?"
— they miss a metric drifting badly while still inside its SLA, and cannot adapt
to a service whose normal baseline differs from the global default.

This is the sibling of phase **5975**, which applied the same paradigm to
`log_analyzer.py`. The two reuse the same robust statistical approach for
consistency.

## What changed

- **`args/monitoring_config.yaml`** — new `metric_anomaly` tuning block
  (`method`, `z_threshold`, `mad_threshold`, `min_samples`, `direction`,
  `history_limit`). Opt-in; a missing block/key or absent PyYAML degrades to the
  documented defaults.
- **`tools/monitor/metric_collector.py`** (mirrored to `icdev/tools/...`):
  - `_median` — median helper.
  - `_load_metric_anomaly_cfg` — defaults deep-merged with config overrides,
    never raises.
  - `_detect_value_anomaly` — pure scorer: compares a current value to its
    historical baseline via classic mean/std-dev **z-score** or robust
    **MAD** (Iglewicz-Hoaglin modified z-score, resistant to prior outliers),
    with `direction` gating (`high` / `low` / `both`) and graceful handling of
    too-little-history and degenerate zero-spread cases.
  - `_metric_history` — pulls recent values for a metric from
    `metric_snapshots`, dropping a just-stored snapshot if present.
  - `detect_metric_anomalies` — orchestrates collection + per-metric scoring;
    returns an anomaly list and the method used.
  - CLI: `--detect-anomalies` and `--anomaly-method {zscore,mad}`.

## Usage

```bash
# Score current metrics against their own historical baseline
python tools/monitor/metric_collector.py --project-id proj-1 --detect-anomalies
python tools/monitor/metric_collector.py --project-id proj-1 --detect-anomalies \
    --anomaly-method mad --json
```

The deterministic SLA check (`--check-sla`) is unchanged and remains
authoritative; anomaly detection is an additive, opt-in signal.

## Tests

`tests/test_metric_collector_anomaly.py` — 22 tests: median helper, config
load/merge/degradation, zscore + MAD scoring, direction gating, degenerate
zero-spread edges, and end-to-end `detect_metric_anomalies` with injected
history. All green; `ruff` clean.
