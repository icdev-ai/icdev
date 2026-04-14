# SRE — Site Reliability Engineering

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## SRE — Site Reliability Engineering
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| SLO Manager | tools/sre/slo_manager.py | SLO manager: define SLOs, record measurements, burn rate calculation, dashboard, gate check | --define, --record, --burn-rate, --dashboard, --gate, --json | SLO status + burn rates |
| Runbook Executor | tools/sre/runbook_executor.py | Runbook executor: register runbooks, match alerts, risk-tiered execution, dry-run, rollback | --register, --match, --execute, --dry-run, --rollback, --list, --json | Execution results + rollback status |
| Incident Commander | tools/sre/incident_commander.py | Incident commander: full incident lifecycle (detected→closed), auto-escalation, MTTR tracking, postmortem | --create, --escalate, --resolve, --close, --postmortem, --mttr, --list, --json | Incident status + MTTR metrics |
| SRE Config | args/sre_config.yaml | SRE config: SLO definitions, burn rate thresholds, runbook registry, escalation policies, incident severity levels | (data) | YAML config |

