# Maintenance Audit

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Maintenance Audit
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Dependency Scanner | tools/maintenance/dependency_scanner.py | Inventory all deps across 6 languages, check latest versions, track staleness | --project-id, --language, --offline, --json | Dependency inventory |
| Vulnerability Checker | tools/maintenance/vulnerability_checker.py | Check dependencies against advisory databases, enforce SLA compliance | --project-id, --json | Vulnerability findings + SLA status |
| Maintenance Auditor | tools/maintenance/maintenance_auditor.py | Full audit lifecycle: scan + check + score + SLA + trend + CUI report | --project-id, --output-dir, --gate, --json | Audit report + score |
| Remediation Engine | tools/maintenance/remediation_engine.py | Auto-implement dependency fixes: version bumps, branch creation, test verification | --project-id, --auto, --dry-run, --json | Remediation actions |
| Sandbox Smoke | tools/maintenance/sandbox_smoke.py | OPT-57 daily liveness probe — health_check() + tiny smoke payload through SandboxExecutor; writes audit row per run | --json, --timeout, --no-audit | Exit 0=healthy / 1=degraded / 2=smoke_failed + JSON |
| Disk Audit | tools/maintenance/disk_audit.py | OPT-29 — Walk key data dirs (genesis, scout, research, backups, .tmp, bak files), report per-dir size + file count, flag stale entries older than N days. JSON output for cron + dashboard. Exit 1 in --gate mode if stale found. | --stale-days, --json, --gate | Size/staleness report per dir + summary |
| Sandbox API | tools/dashboard/api/sandbox.py | OPT-57 `/api/sandbox/liveness`, `/api/sandbox/log`, `/sandbox` page — surfaces latest probe + execution log | HTTP GET | JSON / HTML |
| IL5 SLA Timeout Handler | tools/il5/sla_handler.py | Enforces 30-second end-to-end SLA for the IL5 ingestion-and-display pipeline; raises TimeoutError and logs NIST AU-2 failure on breach. Provides check_il5_timeout() checkpoint, IL5PipelineTimer context manager, and il5_timeout decorator. | start_time (datetime), timeout_s=30, label | elapsed_s (float) or TimeoutError |

