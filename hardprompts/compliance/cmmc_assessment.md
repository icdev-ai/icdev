# CMMC Assessment Hard Prompt

You are running a CMMC (Cybersecurity Maturity Model Certification) assessment. Follow these steps precisely.

## Context

CMMC v2.0 has three levels. ICDEV supports Level 2 (110 practices from NIST 800-171) and Level 3 (134 practices). Most DoD contracts require Level 2 minimum.

## Steps

1. **Determine CMMC level**: Check project requirements.
   - DFARS 252.204-7012 → Level 2
   - Critical programs / IL5+ → Level 3

2. **Run CMMC assessment**:
   ```bash
   python tools/compliance/cmmc_assessor.py --project-id {{project_id}} --level {{level}} --project-dir {{project_dir}}
   ```

3. **Review domain results**: Check all 14 CMMC domains:
   - AC (Access Control), AT (Awareness & Training), AU (Audit & Accountability)
   - CM (Configuration Management), IA (Identification & Authentication)
   - IR (Incident Response), MA (Maintenance), MP (Media Protection)
   - PE (Physical Protection), PS (Personnel Security)
   - RA (Risk Assessment), RE (Recovery), SC (System & Communications Protection)
   - SI (System & Information Integrity)

4. **Generate report**:
   ```bash
   python tools/compliance/cmmc_report_generator.py --project-id {{project_id}} --level {{level}}
   ```

5. **Check NIST 800-171 alignment** — CMMC Level 2 maps 1:1 to 800-171:
   ```bash
   python tools/compliance/crosswalk_engine.py --project-id {{project_id}} --target cmmc --gap-analysis
   ```

6. **Generate evidence package** for C3PAO assessment:
   ```bash
   python tools/compliance/cato_monitor.py --project-id {{project_id}} --readiness
   ```

## Gate Evaluation

- **PASS**: 0 `not_met` practices at target level
- **CONDITIONAL**: ≤3 `not_met` with active remediation plan
- **FAIL**: >3 `not_met` or any critical domain fully unmet

## Output

- CMMC assessment report (CUI-marked markdown)
- Domain score breakdown
- NIST 800-171 cross-reference
- Gate evaluation result
- Evidence readiness summary

## Important Notes

- CMMC Level 2 = NIST 800-171 (110 requirements)
- CMMC Level 3 = 800-171 + 24 additional practices from 800-172
- Use crosswalk engine — implementing NIST 800-53 controls auto-populates CMMC practices
- C3PAO (third-party assessor) will verify Level 2; DIBCAC verifies Level 3
- All evidence must be current (within 90 days for most practices)
