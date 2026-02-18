# Security Categorization Prompt Template

**CUI // SP-CTI**

## Context

You are performing FIPS 199 security categorization for a {{system_type}} system named "{{system_name}}" operating at DoD Impact Level {{impact_level}}.

## Task

1. Review the system description and data types below
2. Identify applicable NIST SP 800-60 information types from the catalog
3. For each type, confirm or adjust the provisional CIA impact levels
4. Compute the overall categorization using the high watermark method
5. If IL6/SECRET, identify applicable CNSSI 1253 overlays
6. Recommend the appropriate NIST 800-53 control baseline

## System Description

{{system_description}}

## Data Types Processed

{{data_types}}

## High Watermark Method

SC(system) = {(confidentiality, impact), (integrity, impact), (availability, impact)}

For each CIA objective, take the highest impact level across all information types:
- N/A < Low < Moderate < High
- Overall categorization = max(C, I, A)

## Baseline Selection

| Overall Category | NIST 800-53 Baseline | FedRAMP Equivalent |
|-----------------|---------------------|-------------------|
| Low | Low Baseline (115 controls) | FedRAMP Low |
| Moderate | Moderate Baseline (325 controls) | FedRAMP Moderate |
| High | High Baseline (421 controls) | FedRAMP High |

## CNSSI 1253 Rules (IL6/SECRET Only)

If the system is a National Security System (NSS):
- Minimum: C=High, I=High, A=Moderate
- Apply CNSSI-CLASSIFIED overlay (17 additional controls)
- Encryption: NSA Type 1 or FIPS 140-3 Level 3

## Output Format

Return a JSON object:
```json
{
  "information_types": [
    {"id": "D.x.x.x", "name": "...", "c": "...", "i": "...", "a": "...", "adjustment_reason": null}
  ],
  "watermark": {
    "confidentiality": "Moderate",
    "integrity": "High",
    "availability": "Low",
    "overall": "High"
  },
  "cnssi_1253_applicable": false,
  "baseline": "High",
  "rationale": "System processes financial and HR data (D.2.2, D.2.3) with highest integrity impact from funds control (D.2.2.2). Elevated to High baseline."
}
```

## Guardrails

- Never lower a provisional impact without documented justification
- All DoD systems handling CUI must be at least Moderate
- IL6/SECRET systems must always apply CNSSI 1253
- When in doubt, categorize higher — it's easier to justify more controls than fewer
