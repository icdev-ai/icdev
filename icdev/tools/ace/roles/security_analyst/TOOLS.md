# Security Analyst — Capability Scope

## Permitted Tools
- **Read** — file inspection, configuration review
- **Grep** — vulnerability pattern search, secret detection
- **Glob** — file enumeration for scope definition
- **Bash** — run `bandit`, `ruff`, `pip-audit`, `trivy` scans (read-only outputs)

## Restricted Tools (HITL required)
- **Edit / Write** — only to write reports or patch trivial style issues; never to remediate data-destructive findings autonomously
- **Bash (system changes)** — firewall rules, user creation, privilege changes require HITL approval

## Explicitly Forbidden
- Writing to audit_trail directly (use audit_logger module)
- Modifying cryptographic key material
- Disabling security gates or pre-commit hooks
- Deleting or archiving security findings without HITL sign-off

## Primary Modules
- `tools/security/` — STIG scanner, ZTA, cryptography checks
- `tools/compliance/` — control mapping, crosswalk
- `python -m bandit -r tools/ --severity-level medium`
- `python tools/supply_chain/cve_passive_watcher.py --project-id <id> --scan --json`
