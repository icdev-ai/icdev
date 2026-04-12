---
mode: agent
description: ""Generate ATO compliance artifacts (SSP, POAM, STIG checklist, SBOM) with CUI markings. Use when creating or updating ATO documentation for a system.""
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
