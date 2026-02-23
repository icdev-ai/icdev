---
name: icdev-comply
description: Generate ATO compliance artifacts (SSP, POAM, STIG checklist, SBOM) with CUI markings
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

## Steps

### 1. Load CUI Configuration
```bash
!cat args/cui_markings.yaml
```

### 2. Determine Artifacts to Generate
Parse `--artifacts` flag. Default is "all".

### 3. Generate SSP
Run the CLI command or use MCP tool `ssp_generate` MCP tool from icdev-compliance:
- project_id: `$ARGUMENTS` (first positional)
- output_dir: from flag or default compliance/ subdir
- Produces 17-section SSP document with CUI banners

### 4. Run STIG Checks
Run the CLI command or use MCP tool `stig_check` MCP tool from icdev-compliance:
- project_id: from arguments
- stig_profile: "webapp" (default) or detect from project type
- Returns CAT1/CAT2/CAT3 findings

### 5. Generate POAM
Run the CLI command or use MCP tool `poam_generate` MCP tool from icdev-compliance:
- project_id: from arguments
- Generates from STIG findings + security scan results
- Includes corrective actions and milestone dates

### 6. Generate SBOM
Run the CLI command or use MCP tool `sbom_generate` MCP tool from icdev-compliance:
- project_dir: project's source directory
- project_id: from arguments
- Produces CycloneDX 1.4 JSON with all dependencies
- Multi-language support: Python (requirements.txt, Pipfile, pyproject.toml), Java (pom.xml, build.gradle), JavaScript/TypeScript (package.json), Go (go.mod), Rust (Cargo.toml), C# (.csproj, packages.config)
- Auto-detects project type and parses appropriate dependency files

### 7. Apply CUI Markings
Run the CLI command or use MCP tool `cui_mark` MCP tool from icdev-compliance:
- Apply CUI // SP-CTI banners to all generated documents
- Add designation indicator blocks

### 8. CSSP Assessment (DI 8530.01)
Run the CLI command or use MCP tool `cssp_assess` MCP tool from icdev-compliance:
- project_id: from arguments
- functional_area: "all" (assess all 5 areas)
- Evaluates: Identify, Protect, Detect, Respond, Sustain
- Auto-checks where possible, flags manual review items

### 9. Generate SIEM Configurations
```bash
python tools/compliance/siem_config_generator.py --project-dir <project_dir> --targets splunk elk
```
- Generates Splunk forwarder + ELK Filebeat configs
- Validates all required log sources configured

### 10. Generate Incident Response Plan
Run the CLI command or use MCP tool `cssp_ir_plan` MCP tool from icdev-compliance:
- project_id: from arguments
- Generates project-specific IR plan with SOC coordination
- Includes reporting timelines: 1hr (critical), 24hr (high), 72hr (moderate)

### 11. Collect Evidence Package
Run the CLI command or use MCP tool `cssp_evidence` MCP tool from icdev-compliance:
- project_id: from arguments
- Scans for all compliance artifacts and maps to CSSP requirements
- Generates evidence manifest

### 12. Generate CSSP Certification Report
Run the CLI command or use MCP tool `cssp_report` MCP tool from icdev-compliance:
- project_id: from arguments
- Builds comprehensive CSSP certification package document
- Includes functional area scores, evidence summary, certification recommendation

### 13. Sync to Xacta 360
Run the CLI command or use MCP tool `xacta_sync` MCP tool from icdev-compliance:
- project_id: from arguments
- mode: "hybrid" (API first, export fallback)
- Pushes all compliance data to Xacta 360 system of record
- Pulls back certification status

### 14. SbD Assessment (CISA Secure by Design)
Run the CLI command or use MCP tool `sbd_assess` MCP tool from icdev-compliance:
- project_id: from arguments
- domain: "all" (assess all 14 domains)
- Evaluates: Authentication, Memory Safety, Vulnerability Mgmt, Intrusion Evidence, Cryptography, Access Control, Input Handling, Error Handling, Supply Chain, Threat Modeling, Defense in Depth, Secure Defaults, CUI Compliance, DoD Software Assurance
- 20 auto-checks for CISA 7 commitments + DoDI 5000.87
- Gate: 0 critical "not_satisfied" = PASS

### 15. Generate SbD Report
Run the CLI command or use MCP tool `sbd_report` MCP tool from icdev-compliance:
- project_id: from arguments
- Builds SbD assessment report with CISA commitment status
- Includes domain scores, auto-check results, remediation items

### 16. Generate Requirements Traceability Matrix (RTM)
Run the CLI command or use MCP tool `rtm_generate` MCP tool from icdev-compliance:
- project_id: from arguments
- project_dir: project's source directory
- Discovers requirements (.feature files, requirements.md)
- Maps: requirements → design → code → tests
- Gap analysis: untested requirements, orphan tests

### 17. IV&V Assessment (IEEE 1012)
Run the CLI command or use MCP tool `ivv_assess` MCP tool from icdev-compliance:
- project_id: from arguments
- process_area: "all" (assess all 9 process areas)
- Evaluates: Requirements, Design, Code, Test, Integration, Traceability, Security, Build/Deploy, Process Compliance
- 18 auto-checks, verification (60%) + validation (40%) scoring
- Gate: 0 critical findings = PASS

### 18. Generate IV&V Report
Run the CLI command or use MCP tool `ivv_report` MCP tool from icdev-compliance:
- project_id: from arguments
- Certification recommendation: CERTIFY (≥80%, 0 critical), CONDITIONAL (≥60%, 0 critical), DENY (<60% or critical)
- Independence declaration, CUI markings

### 19. Compliance Score
Report weighted compliance score (8 components):
- SSP completeness (12%)
- POAM status (10%)
- STIG findings (15%) — gate: 0 CAT1
- SBOM coverage (6%)
- Control mappings (20%)
- CSSP assessment (15%)
- SbD assessment (12%) — gate: 0 critical not_satisfied
- IV&V assessment (10%) — gate: 0 critical findings

### 20. Output Summary
Display:
- Artifacts generated (file paths)
- STIG gate: PASS/FAIL (0 CAT1 required)
- CSSP gate: PASS/FAIL (0 critical requirements not_satisfied)
- SbD gate: PASS/FAIL (0 critical not_satisfied)
- IV&V gate: PASS/FAIL (0 critical findings)
- IV&V certification: CERTIFY/CONDITIONAL/DENY
- Compliance score: XX% (8 components)
- Open POAM items count
- CSSP functional area scores
- SbD domain scores + CISA commitment status
- IV&V verification/validation scores
- RTM coverage percentage
- Xacta sync status
- Next steps for ATO/CSSP/SbD/IV&V certification

### 21. Output Summary
Display:
- Artifacts generated (file paths)
- STIG gate: PASS/FAIL (0 CAT1 required)
- CSSP gate: PASS/FAIL (0 critical requirements not_satisfied)
- SbD gate: PASS/FAIL (0 critical not_satisfied)
- IV&V gate: PASS/FAIL (0 critical findings)
- IV&V certification: CERTIFY/CONDITIONAL/DENY
- Compliance score: XX% (8 components)
- Open POAM items count
- CSSP functional area scores
- SbD domain scores + CISA commitment status
- IV&V verification/validation scores
- RTM coverage percentage
- Xacta sync status
- Next steps for ATO/CSSP/SbD/IV&V certification

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