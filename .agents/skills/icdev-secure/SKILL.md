---
name: icdev-secure
description: "Runs the full security scanning pipeline — SAST with bandit, dependency CVE audit, secret detection, Dockerfile static analysis, and optional Trivy container scan — then evaluates security gates. Use when checking a project for vulnerabilities, scanning for hardcoded secrets, auditing dependencies before a merge, or auto-fixing detected security issues."
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# $icdev-secure

## What This Does
Runs the full security scanning pipeline:
1. **SAST** — Static Application Security Testing (bandit for Python)
2. **Dependency Audit** — Check for known CVEs in dependencies (pip-audit, npm audit)
3. **Secret Detection** — Scan for hardcoded credentials, API keys, tokens
4. **Container Scan** — Dockerfile security checks + image vulnerability scan (trivy)
5. Evaluates security gates and records findings

See [REFERENCE.md](REFERENCE.md) for detailed step procedures.

## Example
```
$icdev-secure --project-dir projects/my-webapp --scan all --fix
```

## Error Handling
- If bandit not installed: report and suggest `pip install bandit`
- If trivy not installed: skip container image scan, run static checks only
- If secrets detected: ALWAYS fail gate regardless of other results
