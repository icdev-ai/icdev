---
mode: agent
description: "Run maintenance audit — scan dependencies, check CVEs, compute score, remediate, track SLAs"
tools:
  - terminal
  - file_search
---

# icdev-maintain

Runs the full maintenance audit lifecycle for a project:
1. **Scan Dependencies** — Inventory all deps across all detected languages
2. **Check Vulnerabilities** — Check deps against advisory databases (pip-audit, npm audit, cargo-audit, etc.)
3. **Compute Score** — Maintenance score (0-100) based on SLA compliance + staleness
4. **Remediate** — Auto-update vulnerable dependencies (if --auto)
5. **Generate Report** — CUI-marked audit report with trend analysis

## Steps

1. **Scan Dependencies**
```bash
python tools/maintenance/dependency_scanner.py --project-id <id> [--offline]
```

2. **Check Vulnerabilities**
```bash
python tools/maintenance/vulnerability_checker.py --project-id <id>
```

3. **Run Maintenance Audit**
```bash
python tools/maintenance/maintenance_auditor.py --project-id <id> [--offline]
```

4. **Remediate (if --auto or --dry-run)**
```bash
python tools/maintenance/remediation_engine.py --project-id <id> [--auto] [--dry-run]
```

5. **Output Summary**
Display:
- Languages detected and scanned
- Total dependencies (by language)

6. **Output Summary**
Display:
- Languages detected and scanned
- Total dependencies (by language)

## Example
```
#prompt:icdev-maintain abc123-uuid --auto
#prompt:icdev-maintain abc123-uuid --dry-run
#prompt:icdev-maintain abc123-uuid --offline
```