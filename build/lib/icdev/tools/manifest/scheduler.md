# Scheduler

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Scheduler

| Tool | Path | Description | Input | Output |
|------|------|-------------|-------|--------|
| Companion Sync Daemon | `tools/scheduler/companion_sync_daemon.py` | Schedule-driven companion config regeneration — runs `companion.py --sync --write` every 30 min (configurable via `args/scheduler_config.yaml`). Ensures headless and air-gap deployments stay current without a dashboard event trigger. Supports daemon mode, `--once` for cron/Task Scheduler, and `--reflex sync` for CI/CD pipeline use. | --once, --status, --reflex sync, --json | Audit trail + companion sync result |

