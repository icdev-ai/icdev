# SAFe Decomposition Prompt

> CUI // SP-CTI

Decompose the validated requirements into a SAFe Agile hierarchy.

## Input
- Requirements: {{requirements_json}}
- Impact Level: {{impact_level}}
- Timeline Constraint: {{timeline}}
- Team Size: {{team_size}}
- PI Cadence: {{pi_cadence_weeks}} weeks

## Decomposition Rules

1. **Epic**: Group related requirements into program-level capabilities
   - Each epic spans 2-4 PIs
   - Include lean business case
   - Map to mission objectives

2. **Capability**: Break epics into ART-level deliverables
   - Each capability fits within 1-2 PIs
   - Must be independently valuable
   - Include benefit hypothesis

3. **Feature**: Break capabilities into PI-level deliverables
   - Each feature fits within 1 PI
   - Must provide user-visible value
   - Calculate WSJF score
   - Include BDD acceptance criteria (Given/When/Then)

4. **Story**: Break features into sprint-level work
   - Max 13 story points per story
   - Must be completable in one sprint
   - Format: "As a {role}, I want to {action} so that {benefit}"
   - Include 2-4 BDD acceptance criteria each

5. **Enabler**: Identify technical enablement needs
   - Infrastructure enablers (environments, CI/CD)
   - Architecture enablers (frameworks, patterns)
   - Compliance enablers (NIST controls, STIG hardening)
   - Exploration enablers (spikes, research)

## NIST Control Mapping
For each story/enabler, identify applicable NIST 800-53 controls:
- Authentication features → IA family
- Authorization features → AC family
- Data handling → SC, SI families
- Logging features → AU family
- API endpoints → SA-9, CA-3

## ATO Boundary Impact
For each feature, assess:
- Does this add a new component? (YELLOW if within boundary)
- Does this add a new external interface? (ORANGE — requires ISA)
- Does this change data classification? (RED if upgrade)
- Does this fit within existing controls? (GREEN)

## Output Format
Return a JSON tree structure following SAFe hierarchy.
