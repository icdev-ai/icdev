---
name: icdev-review
description: Enforce code review gates with security checks, compliance validation, and merge readiness assessment
context: fork
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# /icdev-review — Code Review Gates

## Usage
```
/icdev-review [--project-dir <path>] [--project-id <uuid>] [--auto-approve]
```

## What This Does
Runs the full code review gate checklist before merge:
1. Verify all tests pass (unit + BDD)
2. Verify security scan clean (0 critical/high)
3. Verify STIG compliance (0 CAT1)
4. Verify CUI markings present on all artifacts
5. Verify SBOM is current
6. Check code quality (lint clean)
7. Record review decision in audit trail

## Steps

### 1. Load Gate Configuration
```bash
!cat args/security_gates.yaml
```

### 2. Run Test Suite
Use the `run_tests` MCP tool from icdev-builder:
- Gate: All tests must pass
- Record: test count, coverage %

### 3. Run Security Scan
Run SAST + dependency audit + secret detection:
```bash
python tools/security/vuln_scanner.py --project-dir <path>
```
- Gate: 0 critical/high findings, 0 secrets

### 4. Run STIG Check
Use the `stig_check` MCP tool from icdev-compliance:
- Gate: 0 CAT1 findings

### 5. Verify CUI Markings
```bash
python tools/compliance/cui_marker.py --verify --directory <path>
```
- Gate: All source files must have CUI headers

### 6. Verify SBOM Currency
Use the `sbom_generate` MCP tool from icdev-compliance:
- Gate: SBOM must match current dependencies

### 7. Run Linter
Use the `lint` MCP tool from icdev-builder:
- Gate: 0 critical lint errors (warnings acceptable)

### 8. Gate Decision
Evaluate all gates:
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

All gates must PASS for merge approval.

### 9. Record Decision
Log review decision in audit trail with:
- All gate results
- Reviewer (automated)
- Timestamp
- Decision rationale

### 10. Output Summary
Display:
- Gate results table
- Overall PASS/FAIL
- If FAIL: specific items to fix before re-review
- If PASS: "Ready for merge. Proceed with /icdev-deploy"

## Example
```
/icdev-review --project-dir projects/my-webapp --project-id abc123-uuid
```

## Error Handling
- If any gate tool unavailable: mark that gate as UNKNOWN, fail overall
- If project-id not provided: attempt to detect from project directory
- Never auto-approve if any gate FAILS
