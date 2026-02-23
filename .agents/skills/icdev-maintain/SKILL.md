---
name: icdev-maintain
description: Run maintenance audit — scan dependencies, check CVEs, compute score, remediate, track SLAs
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# $icdev-maintain

## What This Does
Runs the full maintenance audit lifecycle for a project:
1. **Scan Dependencies** — Inventory all deps across all detected languages
2. **Check Vulnerabilities** — Check deps against advisory databases (pip-audit, npm audit, cargo-audit, etc.)
3. **Compute Score** — Maintenance score (0-100) based on SLA compliance + staleness
4. **Remediate** — Auto-update vulnerable dependencies (if --auto)
5. **Generate Report** — CUI-marked audit report with trend analysis

## Steps

### 1. Scan Dependencies
Run the CLI command or use MCP tool `scan_dependencies` MCP tool from icdev-maintenance:
- project_id: from arguments
- offline: from flag (for air-gapped environments)
- Inventories all deps with version, latest version, days stale

```bash
python tools/maintenance/dependency_scanner.py --project-id <id> [--offline]
```

### 2. Check Vulnerabilities
Run the CLI command or use MCP tool `check_vulnerabilities` MCP tool from icdev-maintenance:
- project_id: from arguments
- Checks all deps against CVE databases
- Maps severity to SLA deadlines: critical=48hr, high=7d, medium=30d, low=90d

```bash
python tools/maintenance/vulnerability_checker.py --project-id <id>
```

### 3. Run Maintenance Audit
Run the CLI command or use MCP tool `run_maintenance_audit` MCP tool from icdev-maintenance:
- project_id: from arguments
- Computes maintenance score, SLA compliance, trend analysis
- Generates CUI-marked markdown report

```bash
python tools/maintenance/maintenance_auditor.py --project-id <id> [--offline]
```

### 4. Remediate (if --auto or --dry-run)
Run the CLI command or use MCP tool `remediate` MCP tool from icdev-maintenance:
- project_id: from arguments
- auto: true if --auto flag set
- dry_run: true if --dry-run flag set
- Updates dependency files, creates remediation branches, runs tests

```bash
python tools/maintenance/remediation_engine.py --project-id <id> [--auto] [--dry-run]
```

### 5. Output Summary
Display:
- Languages detected and scanned
- Total dependencies (by language)
- Outdated dependencies count
- Vulnerable dependencies (by severity)
- SLA compliance percentage
- Overdue SLA items (critical/high)
- Maintenance score: XX/100
- Gate status: PASS/WARN/FAIL
- Remediation actions taken (if --auto)
- Report file path
- Next steps

### 6. Output Summary
Display:
- Languages detected and scanned
- Total dependencies (by language)
- Outdated dependencies count
- Vulnerable dependencies (by severity)
- SLA compliance percentage
- Overdue SLA items (critical/high)
- Maintenance score: XX/100
- Gate status: PASS/WARN/FAIL
- Remediation actions taken (if --auto)
- Report file path
- Next steps

## Example
```
$icdev-maintain abc123-uuid --auto
$icdev-maintain abc123-uuid --dry-run
$icdev-maintain abc123-uuid --offline
```

## Error Handling
- If project not found: list available projects
- If no dependencies detected: warn and suggest checking project directory
- If package registry unreachable: switch to offline mode automatically
- If remediation fails: rollback, report failure, continue with next
- If SLA overdue: flag as blocking in gate evaluation