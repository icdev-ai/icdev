---
name: icdev-query
description: "Queries the ICDEV™ operational database to surface project metrics, compliance status, audit trail entries, and task activity across all registered projects. Use when retrieving project data, checking compliance status, querying audit records, or pulling metrics for reporting and dashboard display."
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# $icdev-query

## What This Does
Provides structured query access to the ICDEV™ database:
1. **Project queries** — retrieve metadata, status, and health for one or all projects
2. **Compliance queries** — surface SSP status, POAM items, STIG findings by project
3. **Audit trail queries** — retrieve recent events, filter by activity type or date range
4. **Activity queries** — pull task tracking data, agent activity, and timeline metrics

## Example
```
$icdev-query projects --status active
$icdev-query audit --project-id abc123 --since 7d
$icdev-query compliance --project-id abc123 --format table
```

## Error Handling
- If database unavailable: report connection error and check `data/icdev.db`
- If project not found: list available project IDs
- If query returns 0 rows: suggest broader filters
