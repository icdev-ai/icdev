---
name: icdev-comply
description: "Generates ATO compliance artifacts including SSP, POAM, STIG checklist, SBOM, CSSP certification, SbD assessment, IV&V report, and RTM with CUI // SP-CTI markings. Use when preparing an ATO package, running a FedRAMP or CMMC review, generating compliance documentation for a project, or syncing findings to Xacta 360."
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# $icdev-comply

## What This Does
Generates Authority to Operate (ATO) package artifacts:
1. **SSP** — System Security Plan (17 sections per NIST 800-53)
2. **POAM** — Plan of Action & Milestones from security findings
3. **STIG** — STIG compliance checklist with automated checks
4. **SBOM** — Software Bill of Materials (CycloneDX format)
5. **CSSP** — CSSP certification (DI 8530.01) with functional area scores
6. **SbD** — CISA Secure by Design assessment (14 domains, 7 commitments)
7. **IV&V** — Independent Verification & Validation report (IEEE 1012)
8. **RTM** — Requirements Traceability Matrix with gap analysis
9. **Xacta 360** — Sync all findings to Xacta system of record

All artifacts include CUI // SP-CTI markings and designation indicators.

See [REFERENCE.md](REFERENCE.md) for detailed step procedures.

## Example
```
$icdev-comply abc123-uuid --artifacts all --output-dir projects/my-webapp/compliance
```

## Error Handling
- If project not found: list available projects
- If STIG has CAT1 findings: FAIL gate, list findings, suggest fixes
- If SBOM generator unavailable: generate minimal SBOM from requirements.txt
- If CSSP critical requirements not satisfied: FAIL gate, list requirements, suggest fixes
- If Xacta API unavailable: fall back to export mode, generate OSCAL + CSV files
- If SbD critical requirements not satisfied: FAIL gate, list requirements, suggest fixes
- If IV&V critical findings open: FAIL gate, list findings, suggest resolutions
- If RTM has gaps in critical requirements: warn and list untested requirements
