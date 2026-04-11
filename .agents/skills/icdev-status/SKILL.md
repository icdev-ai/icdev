---
name: icdev-status
description: "Displays a comprehensive project status dashboard covering compliance posture, security scan results, test coverage, deployment state, agent health, and recent audit trail entries. Use when checking overall project health, reviewing ATO readiness, or getting a quick summary before starting a development session on an ICDEV™ project."
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# $icdev-status

## What This Does
Shows a comprehensive status overview:
1. Project metadata and health
2. Compliance status (SSP, POAM, STIG, SBOM)
3. Security scan results
4. Test results and coverage
5. Deployment status
6. Agent health
7. Recent audit trail entries

See [REFERENCE.md](REFERENCE.md) for detailed step procedures.

## Example
```
$icdev-status abc123-uuid --format table
$icdev-status --all --format brief
```

## Error Handling
- If project not found: show available projects
- If database unavailable: report connection error
- If agent health check fails: show last known status
