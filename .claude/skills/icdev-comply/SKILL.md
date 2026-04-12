---
name: icdev-comply
description: "Generate ATO compliance artifacts (SSP, POAM, STIG checklist, SBOM) with CUI markings. Use when creating or updating ATO documentation for a system."
context: fork
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# /icdev-comply — Generate Compliance Artifacts

## Usage
```
/icdev-comply <project-id> [--artifacts ssp,poam,stig,sbom,all] [--output-dir <path>]
```

## What This Does
Generates Authority to Operate (ATO) package artifacts:
1. **SSP** — System Security Plan (17 sections per NIST 800-53)
2. **POAM** — Plan of Action & Milestones from security findings
3. **STIG** — STIG compliance checklist with automated checks
4. **SBOM** — Software Bill of Materials (CycloneDX format)
All artifacts include CUI // SP-CTI markings and designation indicators.

## Steps
> Full step-by-step MCP tool details are in [REFERENCE.md](REFERENCE.md).

1. Load CUI config from `args/cui_markings.yaml`
2. Parse `--artifacts` flag (default: "all")
3. Generate SSP (`ssp_generate`) — 17-section NIST 800-53 document with CUI banners
4. Run STIG checks (`stig_check`) — gate: 0 CAT1 findings
5. Generate POAM (`poam_generate`) — from STIG findings + security scan results
6. Generate SBOM (`sbom_generate`) — CycloneDX 1.4 JSON, multi-language support
7. Apply CUI markings (`cui_mark`) — CUI // SP-CTI banners on all artifacts
8. CSSP assessment (`cssp_assess`) — 5 functional areas per DI 8530.01
9. Generate SIEM configs — Splunk + ELK Filebeat (`siem_config_generator.py`)
10. Generate IR plan (`cssp_ir_plan`) — SOC coordination, tiered timelines
11. Collect evidence package (`cssp_evidence`) — manifest mapped to CSSP requirements
12. Generate CSSP certification report (`cssp_report`)
13. Sync to Xacta 360 (`xacta_sync`) — hybrid API/export mode
14. SbD assessment (`sbd_assess`) — 14 domains, 20 CISA commitment auto-checks
15. Generate SbD report (`sbd_report`)
16. Generate RTM (`rtm_generate`) — requirements → design → code → tests
17. IV&V assessment (`ivv_assess`) — 9 process areas, gate: 0 critical findings
18. Generate IV&V report (`ivv_report`) — CERTIFY/CONDITIONAL/DENY recommendation
19. Report weighted compliance score (SSP 12%, POAM 10%, STIG 15%, SBOM 6%, Controls 20%, CSSP 15%, SbD 12%, IV&V 10%)
20. Display output summary with gate statuses and next steps

## Error Handling
- If project not found: list available projects
- If STIG has CAT1 findings: FAIL gate, list findings, suggest fixes
- If SBOM generator unavailable: generate minimal SBOM from requirements.txt
- If CSSP critical requirements not satisfied: FAIL gate, list requirements, suggest fixes
- If Xacta API unavailable: fall back to export mode, generate OSCAL + CSV files
- If SbD critical requirements not satisfied: FAIL gate, list requirements, suggest fixes
- If IV&V critical findings open: FAIL gate, list findings, suggest resolutions
- If RTM has gaps in critical requirements: warn and list untested requirements
