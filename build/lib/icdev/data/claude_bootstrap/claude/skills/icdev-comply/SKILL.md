---
name: icdev-comply
description: "Generate ATO compliance artifacts (SSP, POAM, STIG checklist, SBOM) with CUI markings. Use when creating or updating ATO documentation for a system."
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# $icdev-comply

## What This Does
Generates Authority to Operate (ATO) package artifacts:
1. **SSP** — System Security Plan (17 sections per NIST 800-53)
2. **POAM** — Plan of Action & Milestones from security findings
3. **STIG** — STIG compliance checklist with automated checks
4. **SBOM** — Software Bill of Materials (CycloneDX format)
All artifacts include CUI // SP-CTI markings and designation indicators.

## Error Handling
- If project not found: list available projects
- If STIG has CAT1 findings: FAIL gate, list findings, suggest fixes
- If SBOM generator unavailable: generate minimal SBOM from requirements.txt
- If CSSP critical requirements not satisfied: FAIL gate, list requirements, suggest fixes
- If Xacta API unavailable: fall back to export mode, generate OSCAL + CSV files
- If SbD critical requirements not satisfied: FAIL gate, list requirements, suggest fixes
- If IV&V critical findings open: FAIL gate, list findings, suggest resolutions
- If RTM has gaps in critical requirements: warn and list untested requirements