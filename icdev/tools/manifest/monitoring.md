# Monitoring

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Monitoring
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Log Analyzer | tools/monitor/log_analyzer.py | ELK/Splunk log analysis | --project, --time-range | Anomalies |
| Metric Collector | tools/monitor/metric_collector.py | Prometheus metric collection | --project | Metrics |
| Alert Correlator | tools/monitor/alert_correlator.py | Correlate alerts across sources | --time-window | Correlated incidents |
| Health Checker | tools/monitor/health_checker.py | Application health check | --url, --retries | Health status |
| Heartbeat Daemon | tools/monitor/heartbeat_daemon.py | Proactive daemon: 7 configurable checks (cATO evidence, agent health, CVE SLA, pending intake, failing tests, expiring ISAs, memory maintenance) (D141-D142) | --once, --check, --status, --json | Check results + notifications |
| Auto-Resolver | tools/monitor/auto_resolver.py | Webhook-triggered auto-resolution: alert → normalize → analyze → fix → PR → notify (D143-D145) | --analyze, --resolve, --alert-file, --source, --dry-run, --json | Resolution log + PR URL |
| Outcome Verifier | tools/monitor/outcome_verifier.py | Track PR merge status + failure recurrence, update pattern confidence (D-EVO-6) | --check-pending, --check-recurrence, --run-all, --status, --json | Verification log |
| Push Agent | tools/monitor/push_agent.py | Lightweight sidecar: collect CPU/memory/disk per container (Docker stats or psutil), buffer to SQLite, push to dashboard on configurable interval; IL5/IL6 air-gap safe | --once, --daemon, --flush, --status, --interval, --dry-run, --json | Metrics JSON / push receipt |
| Retention Manager | tools/monitor/retention.py | SQLite retention policy for container_metrics and heartbeat_checks; configurable window (default 7d, floor 1d); daemon or one-shot purge | --purge, --status, --daemon, --retention-days, --interval, --dry-run, --json | Purge summary JSON |
| Reflex Observer | tools/monitoring/reflex_observer.py | observe() wrapper for Genesis reflexes; records to reflex_observations table | reflex_name, run_fn, *args, **kwargs | Reflex observation record |
| Reflex Health | tools/genesis/reflex_health.py | Per-reflex failure-rate + p50/p95 duration trends over 7/30-day windows from genesis_audit; opens/refreshes/auto-resolves critical-reflex alerts in the shared `alerts` table (surfaces on /monitoring) with per-reflex severity + cooldown. Wired into GenesisDaemon.on_reflex_completed | --days, --failures, --alert, --json | Health-trend / recent-failures / alert-summary JSON |
| WATCHCON Tiers | tools/monitor/watchcon.py | Three-tier alert classification: WATCHCON 4 (routine/info), WATCHCON 3 (elevated/warning), WATCHCON 2 (high/critical); insert, query, backfill, summarize | --tier, --json | Alert tier records + summary |
| Monitor Constants | tools/monitor/constants.py | WATCHCON tier constants, severity↔tier mappings | (import only) | Module-level constants |
| TimesFM Forecast Adapter | icdev/tools/forecast/timesfm_adapter.py | Self-hostable time-series forecasting microservice backed by Google TimesFM; lazy-loads model, validates payload, persists jobs + audit | JSON payload or CLI | Forecast job record / health status |

## EQO Centralized Logging (eqo-log)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Log Query | tools/logging/log_query.py | Query the append-only centralized_logs sink (RLS via get_connection); shared by /logs page, GET /api/logs, and the logs.entries IQE collection | --component, --level, --since, --contains, --limit, --json | Log rows (newest first) |
| Logs Blueprint | tools/logging/blueprint.py | /logs dashboard page + GET /api/logs + POST /logs/api/iqe-query; create_logs_blueprint() gated by ICDEV_LOGS_ENABLED | (Flask factory) | Blueprint or None |
| Logs IQE Adapter | tools/iqe/adapters/logs.py | Registers the logs.entries IQE collection over centralized_logs | (import only) | Collection registration |
| Logging Constants | tools/logging/constants.py | LOG_LEVELS, LEVEL_RANK, DEFAULT/MAX_LIMIT, LOGS_TABLE, FEATURE_FLAG | (import only) | Module-level constants |

