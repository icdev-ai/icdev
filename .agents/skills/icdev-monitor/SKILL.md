---
name: icdev-monitor
description: "Monitors deployed services by running health checks, analyzing logs for error patterns, collecting metrics, correlating alerts, and optionally triggering self-healing for known patterns. Use when checking production health, investigating an active alert, analyzing recent logs, or triggering the self-healing engine for a running ICDEV™ project."
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# $icdev-monitor

## What This Does
1. Runs health checks on deployed services
2. Analyzes logs for error patterns
3. Collects and reviews metrics
4. Checks active alerts and correlates
5. Triggers self-healing for known patterns (if --self-heal)

See [REFERENCE.md](REFERENCE.md) for detailed step procedures.

## Example
```
$icdev-monitor abc123-uuid --check all --self-heal --since 24h
```

## Error Handling
- If monitoring endpoints unavailable: report and use cached data
- If self-heal rate limited: queue action and report
- If pattern confidence too low: always escalate, never auto-fix
