---
ontology_id: icdev:mission:m-readiness-02-remediation:step:2
step_class: icdev:configure
---
# IL Classification Remediation

Pillar 8 (IL Classification) checks for CUI markings at the top of Python files containing sensitive data. The pattern: `# CUI // SP-CTI` or similar CUI header as the first or second line.

## CUI marking rules

- **IL4**: `# CUI // SP-CTI` — Controlled Unclassified Information
- **IL5**: `# CUI // SP-CTI // NOFORN` — NOFORN adds no foreign nationals
- **IL6**: `# SECRET // SI` — classified at SECRET level

Files that need CUI markings: any file that processes PII, security controls, authentication, keys, or classified data.

## Automated marking

ICDEV's `tools/compliance/classification_manager.py` provides `get_marking_for_il(il_level)` — use it rather than hardcoding strings.

## Your task

Write a script that identifies Python files missing CUI markings in a target directory and adds the appropriate IL4 marking. Use `classification_manager.get_marking_for_il("IL4")` for the marking string.
