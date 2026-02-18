# FedRAMP Assessment Hard Prompt

You are running a FedRAMP security assessment against a project. Follow these steps precisely.

## Context

FedRAMP (Federal Risk and Authorization Management Program) provides a standardized approach to security assessment for cloud products. ICDEV supports Moderate and High baselines aligned with NIST SP 800-53 Rev 5.

## Steps

1. **Determine baseline**: Check the project's impact level.
   - IL4 → FedRAMP Moderate
   - IL5/IL6 → FedRAMP High

2. **Run FedRAMP assessment**:
   ```bash
   python tools/compliance/fedramp_assessor.py --project-id {{project_id}} --baseline {{baseline}} --project-dir {{project_dir}}
   ```

3. **Review results**: Check for `other_than_satisfied` controls, especially in high-priority families (AC, IA, SC, AU).

4. **Generate report**:
   ```bash
   python tools/compliance/fedramp_report_generator.py --project-id {{project_id}} --baseline {{baseline}}
   ```

5. **Generate OSCAL SSP** for machine-readable submission:
   ```bash
   python tools/compliance/oscal_generator.py --project-id {{project_id}} --artifact ssp
   ```

6. **Check crosswalk coverage** — FedRAMP implementation auto-satisfies NIST 800-53 and may satisfy CMMC/800-171:
   ```bash
   python tools/compliance/crosswalk_engine.py --project-id {{project_id}} --coverage
   ```

## Gate Evaluation

- **PASS**: 0 `other_than_satisfied` on high-priority controls
- **CONDITIONAL**: ≤5 `other_than_satisfied` with active POA&M items
- **FAIL**: >5 `other_than_satisfied` or missing critical controls

## Output

- FedRAMP assessment report (CUI-marked markdown)
- OSCAL SSP artifact (JSON)
- Gate evaluation result
- Crosswalk coverage update

## Important Notes

- All artifacts must include CUI markings (or SECRET for IL6)
- FedRAMP High includes all Moderate controls plus additional enhanced controls
- Use the crosswalk engine to avoid duplicate implementation effort
- POA&M items must have realistic milestones (CAT1: 15 days, CAT2: 30 days)
