---
name: icdev-modernize
description: "Analyzes an existing application and produces a 7Rs migration strategy (Retire, Retain, Rehost, Replatform, Repurchase, Refactor, Re-architect) with an IaC modernization roadmap. Use when modernizing a legacy application, planning a cloud migration, or assessing which 7R strategy applies to a given system."
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# $icdev-modernize

## What This Does
Runs the ICDEV™ Application Modernization workflow using the 7Rs framework:
1. **Assess** — analyze the existing application's architecture, dependencies, and tech debt
2. **Classify** — assign each component to the optimal 7R strategy
3. **Roadmap** — generate phased migration plan with IaC scaffolding
4. **Validate** — check security, compliance, and NIST 800-53 control gaps in the target state

## Example
```
$icdev-modernize --app-dir projects/legacy-app --target aws-govcloud
```

## Error Handling
- If no source directory found: prompt for app directory
- If target platform unsupported: list supported platforms
- If dependency scan fails: continue with partial results and warn

See [REFERENCE.md](REFERENCE.md) for detailed step procedures.
