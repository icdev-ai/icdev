# CUI // SP-CTI

# obs-metric-01 — why the two `metric_snapshots` INSERT sites never fired

**Status:** resolved · **Date:** 2026-08-07 · **Task:** `obs-metric-01`

`metric_snapshots` had two INSERT sites in the tree and 0 rows in the live
PostgreSQL database. The task named three candidate causes: unreachable writers,
a flag nobody sets, or a swallowed exception. This records which one applied.

## The two sites

| Site | Only caller | External dependency |
|------|-------------|---------------------|
| `tools/monitor/metric_collector.py::store_snapshot` (line ~380) | `metric_collector.main()` under `--store` | Prometheus at `localhost:9090` |
| `tools/monitor/log_analyzer.py::_record_findings` (line ~850) | `analyze_logs()`, itself CLI-only | ELK `localhost:9200` / Splunk `localhost:8089` |

## Cause: **(a) the writers are unreachable**

Both INSERT statements are correct and both persist rows when called. Verified
empirically against a schema-matched SQLite database before changing anything:
`store_snapshot` returned 2 and 2 rows were on disk.

So the SQL was never the problem. The table was empty because **nothing in the
platform calls either function.** Neither has a reflex, daemon, scheduler, CI
job, dashboard route, or MCP handler behind it — each is reachable only from its
own `__main__`. Both additionally require a metrics backend (Prometheus / ELK)
that is not deployed in this environment, so even the CLI path would have
written nothing.

It was not a flag: there is no toggle guarding either site. It was not a
swallowed exception in the sense the sweep found elsewhere — though
`_record_findings` *would* have swallowed one had it ever run (see below).

Meanwhile four surfaces read the table: `tools/project/project_status.py`,
`tools/infra/infra_status.py`, `icdev/tools/dashboard/api/metrics.py`, and
`tools/mcp/core_server.py`. The metric is not dead, so deleting the sites and
the table was not the right call.

## Two latent defects found alongside

Both would have caused silent loss the moment a metrics backend appeared:

1. **`analyze_logs` gated the write on a SQLite file existing.** The guard read
   `if project_id and (db_path or DB_PATH.exists())`. On the PostgreSQL-primary
   stack `data/icdev.db` is absent by design, so every finding would have been
   dropped with ELK reachable and a `project_id` set. Now gated on `project_id`
   alone.
2. **`_record_findings` swallowed every failure into a `print()` and returned
   `None`.** A caller could not distinguish a successful write from a total
   no-op — the exact silent-write class as `module_budget_usage` and the govcon
   audit rows. It now returns a row count, surfaced as `metrics_recorded` on the
   analysis result. It still never raises: losing a metrics row must not discard
   the analysis.

## Fix: give the writer a caller that actually runs

`tools/genesis/reflexes/self_monitor.py` is the one monitoring path on a live
cadence (`every 30m`, enabled, and the sole author of all 10 rows in `alerts`).
It already computed the numbers below every cycle and discarded them after
thresholding into alerts. It now persists them through the **existing**
`store_snapshot` writer — no new CLI, no new surface, no dependency on
Prometheus or ELK:

`self_monitor_failing_components`, `self_monitor_alerts_firing`,
`self_monitor_alerts_opened`, `self_monitor_alerts_resolved`,
`self_monitor_failures_logged`, `self_monitor_cycle_ms`

`metric_snapshots.project_id` is `NOT NULL` (and FK-constrained to `projects(id)`
on SQLite, where `storage.py` turns `PRAGMA foreign_keys` ON), so the rows carry
`icdev-tools-rtm` — the platform's own project row, the convention already used
by `tools/genesis/reflexes/integrity_monitor.py`. Both the toggle
(`record_metrics`) and the project id (`metrics_project_id`) are config keys on
the reflex entry in `args/genesis_config.yaml`.

A metrics-write failure is logged and reported as `metrics_recorded: 0`; it
never fails the reflex, whose primary job is alerting.

## Verification

Live PostgreSQL, one real reflex cycle: `metric_snapshots` went **0 → 6 rows**,
`source='self_monitor'`, `project_id='icdev-tools-rtm'`.

`tests/test_metric_snapshots_writers.py` (8 tests, ~0.6s) holds the contract:

- `store_snapshot` persists the rows it reports, and does not count values it dropped
- `_record_findings` persists and reports its count; returns 0 when the write fails
- `analyze_logs` records findings **without** a local SQLite file — RED-checked
  against the old guard, which fails with `KeyError: 'metrics_recorded'`
- `self_monitor._record_metrics` writes real rows and survives a writer failure
- `run()` is still wired to `_record_metrics` — guards the reachability fix itself

## Out of scope

No health CLI was added, per the task. `icdev status` is already the component
toggle surface and health is already served by
`python tools/testing/health_check.py --json`.
