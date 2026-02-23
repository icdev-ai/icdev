---
mode: agent
description: "Generate ATO compliance artifacts (SSP, POAM, STIG checklist, SBOM) with CUI markings"
tools:
  - terminal
  - file_search
---

# icdev-comply

Generates Authority to Operate (ATO) package artifacts:
1. **SSP** — System Security Plan (17 sections per NIST 800-53)
2. **POAM** — Plan of Action & Milestones from security findings
3. **STIG** — STIG compliance checklist with automated checks
4. **SBOM** — Software Bill of Materials (CycloneDX format)
All artifacts include CUI // SP-CTI markings and designation indicators.

## Steps

1. **Load CUI Configuration**
```bash
!cat args/cui_markings.yaml
```

2. **Determine Artifacts to Generate**
Parse `--artifacts` flag. Default is "all".

3. **Generate SSP**
Run the equivalent CLI command for ssp_generate:
- project_id: `$ARGUMENTS` (first positional)
- output_dir: from flag or default compliance/ subdir

4. **Run STIG Checks**
Run the equivalent CLI command for stig_check:
- project_id: from arguments
- stig_profile: "webapp" (default) or detect from project type

5. **Generate POAM**
Run the equivalent CLI command for poam_generate:
- project_id: from arguments
- Generates from STIG findings + security scan results

6. **Generate SBOM**
Run the equivalent CLI command for sbom_generate:
- project_dir: project's source directory
- project_id: from arguments

7. **Apply CUI Markings**
Run the equivalent CLI command for cui_mark:
- Apply CUI // SP-CTI banners to all generated documents
- Add designation indicator blocks

8. **CSSP Assessment (DI 8530.01)**
Run the equivalent CLI command for cssp_assess:
- project_id: from arguments
- functional_area: "all" (assess all 5 areas)

9. **Generate SIEM Configurations**
```bash
python tools/compliance/siem_config_generator.py --project-dir <project_dir> --targets splunk elk
```

10. **Generate Incident Response Plan**
Run the equivalent CLI command for cssp_ir_plan:
- project_id: from arguments
- Generates project-specific IR plan with SOC coordination

11. **Collect Evidence Package**
Run the equivalent CLI command for cssp_evidence:
- project_id: from arguments
- Scans for all compliance artifacts and maps to CSSP requirements

12. **Generate CSSP Certification Report**
Run the equivalent CLI command for cssp_report:
- project_id: from arguments
- Builds comprehensive CSSP certification package document

13. **Sync to Xacta 360**
Run the equivalent CLI command for xacta_sync:
- project_id: from arguments
- mode: "hybrid" (API first, export fallback)

14. **SbD Assessment (CISA Secure by Design)**
Run the equivalent CLI command for sbd_assess:
- project_id: from arguments
- domain: "all" (assess all 14 domains)

15. **Generate SbD Report**
Run the equivalent CLI command for sbd_report:
- project_id: from arguments
- Builds SbD assessment report with CISA commitment status

16. **Generate Requirements Traceability Matrix (RTM)**
Run the equivalent CLI command for rtm_generate:
- project_id: from arguments
- project_dir: project's source directory

17. **IV&V Assessment (IEEE 1012)**
Run the equivalent CLI command for ivv_assess:
- project_id: from arguments
- process_area: "all" (assess all 9 process areas)

18. **Generate IV&V Report**
Run the equivalent CLI command for ivv_report:
- project_id: from arguments
- Certification recommendation: CERTIFY (≥80%, 0 critical), CONDITIONAL (≥60%, 0 critical), DENY (<60% or critical)

19. **Compliance Score**
Report weighted compliance score (8 components):
- SSP completeness (12%)
- POAM status (10%)

20. **Output Summary**
Display:
- Artifacts generated (file paths)
- STIG gate: PASS/FAIL (0 CAT1 required)

21. **Output Summary**
Display:
- Artifacts generated (file paths)
- STIG gate: PASS/FAIL (0 CAT1 required)

## Example
```
#prompt:icdev-comply abc123-uuid --artifacts all --output-dir projects/my-webapp/compliance
```