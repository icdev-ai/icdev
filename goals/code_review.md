# Goal: Code Review Gates

## Purpose
Enforce automated code review gates before merge to ensure security, compliance, and quality standards are met. Implements NIST 800-53 controls SA-11 (Developer Testing), SA-15 (Development Process), and CM-3 (Configuration Change Control).

## Trigger
- Developer requests merge/review
- `/icdev-review` skill invoked
- Pre-merge hook in GitLab CI/CD

## Inputs
- Project directory path
- Project UUID
- Security gates configuration (`args/security_gates.yaml`)

## Process

### Step 1: Run Test Suite
**Tool:** `tools/builder/` (via `run_tests` MCP tool)
- Execute all unit tests (pytest)
- Execute all BDD tests (behave)
- Measure code coverage
- **Gate:** All tests must pass, coverage >= 80%

### Step 2: Run Security Scan
**Tool:** `tools/security/vuln_scanner.py`
- SAST analysis (bandit)
- Dependency audit (pip-audit / npm audit)
- Secret detection (detect-secrets + built-in patterns)
- Container scan (trivy + static Dockerfile checks)
- **Gate:** 0 critical/high SAST findings, 0 critical CVEs, 0 secrets detected

### Step 3: Run STIG Compliance Check
**Tool:** `tools/compliance/stig_checker.py`
- Evaluate against applicable STIG profile (webapp, container, database, linux, network)
- **Gate:** 0 CAT1 findings

### Step 4: Verify CUI Markings
**Tool:** `tools/compliance/cui_marker.py`
- Verify all source files have CUI header banners
- Verify all documents have CUI banners and designation indicators
- **Gate:** All files must be marked

### Step 5: Verify SBOM Currency
**Tool:** `tools/compliance/sbom_generator.py`
- Generate current SBOM
- Compare against last recorded SBOM
- **Gate:** SBOM must reflect current dependencies

### Step 6: Run Linter
**Tool:** `tools/builder/linter.py`
- Run flake8/eslint for code quality
- **Gate:** 0 critical errors (warnings acceptable)

### Step 7: Gate Decision
Evaluate all gates:
- ALL must pass for approval
- ANY failure blocks merge
- Record decision in audit trail

### Step 8: Audit Trail
**Tool:** `tools/audit/audit_logger.py`
- Record: event_type=code.review, all gate results, decision, timestamp
- **NIST Controls:** SA-11, SA-15, CM-3

## Outputs
- Gate results table (PASS/FAIL per gate)
- Overall decision (APPROVE/REJECT)
- Specific failures with remediation guidance
- Audit trail entry

## Gate Thresholds (from security_gates.yaml)
| Gate | Threshold | Severity |
|------|-----------|----------|
| Tests | 100% pass | Blocking |
| Coverage | >= 80% | Warning |
| SAST | 0 HIGH | Blocking |
| Dependencies | 0 CRITICAL | Blocking |
| Secrets | 0 detected | Blocking |
| STIG | 0 CAT1 | Blocking |
| CUI | 100% marked | Blocking |
| SBOM | Current | Warning |
| Lint | 0 critical | Warning |

## Edge Cases
- If a gate tool is unavailable: mark gate as UNKNOWN, fail overall
- If project has no tests yet: FAIL with guidance to run /icdev-build first
- If multiple failures: report ALL failures, not just first one
- Race condition on merge: lock review until complete

## Related Goals
- `tdd_workflow.md` — Test generation and execution
- `security_scan.md` — Security scanning details
- `compliance_workflow.md` — Compliance artifact generation
