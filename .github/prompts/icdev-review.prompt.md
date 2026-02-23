---
mode: agent
description: "Enforce code review gates with security checks, compliance validation, and merge readiness assessment"
tools:
  - terminal
  - file_search
---

# icdev-review

Runs the full code review gate checklist before merge:
1. Verify all tests pass (unit + BDD)
2. Verify security scan clean (0 critical/high)
3. Verify STIG compliance (0 CAT1)
4. Verify CUI markings present on all artifacts
5. Verify SBOM is current
6. Check code quality (lint clean)
7. Record review decision in audit trail

## Steps

1. **Load Gate Configuration**
```bash
!cat args/security_gates.yaml
```

2. **Run Test Suite**
Run the equivalent CLI command for run_tests:
- Gate: All tests must pass
- Record: test count, coverage %

3. **Run Security Scan**
```bash
python tools/security/vuln_scanner.py --project-dir <path>
```

4. **Run STIG Check**
Run the equivalent CLI command for stig_check:
- Gate: 0 CAT1 findings

5. **Verify CUI Markings**
```bash
python tools/compliance/cui_marker.py --verify --directory <path>
```

6. **Verify SBOM Currency**
Run the equivalent CLI command for sbom_generate:
- Gate: SBOM must match current dependencies

7. **Run Linter**
Run the equivalent CLI command for lint:
- Gate: 0 critical lint errors (warnings acceptable)

8. **Gate Decision**
```
Tests:      PASS/FAIL
Security:   PASS/FAIL
STIG:       PASS/FAIL
CUI:        PASS/FAIL
SBOM:       PASS/FAIL
Lint:       PASS/FAIL
─────────────────────
Overall:    PASS/FAIL
```

9. **Record Decision**
Log review decision in audit trail with:
- All gate results
- Reviewer (automated)

10. **Output Summary**
Display:
- Gate results table
- Overall PASS/FAIL

11. **Output Summary**
Display:
- Gate results table
- Overall PASS/FAIL

## Example
```
#prompt:icdev-review --project-dir projects/my-webapp --project-id abc123-uuid
```