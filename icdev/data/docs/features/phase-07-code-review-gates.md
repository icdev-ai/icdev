# Phase 7 — Code Review Gates

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 7 |
| Title | Enforced Code Review Gates |
| Status | Implemented |
| Priority | P0 |
| Dependencies | Phase 3 (TDD/BDD Testing Framework), Phase 4 (NIST 800-53 Compliance), Phase 5 (Security Scanning) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

Code review in government software development is not optional -- it is a compliance requirement. NIST 800-53 controls SA-11 (Developer Testing and Evaluation), SA-15 (Development Process, Standards, and Tools), and CM-3 (Configuration Change Control) mandate that code changes undergo structured review before integration. Without automated enforcement, review gates are bypassed under schedule pressure, leading to security vulnerabilities, compliance gaps, and audit findings.

Traditional code review focuses on readability and logic correctness. In government environments, review must also verify security posture (no SAST findings, no secrets, no vulnerable dependencies), compliance posture (CUI markings present, STIG checks passing, SBOM current), and quality posture (all tests passing, coverage adequate, linting clean). These checks cannot rely on human diligence alone -- they must be automated, gated, and logged to an immutable audit trail.

ICDEV implements an 8-step automated code review gate that evaluates every merge request across all three dimensions: security, compliance, and quality. All gates must pass for merge approval. Any failure blocks the merge with specific remediation guidance. Every gate decision is recorded in the append-only audit trail, satisfying NIST AU controls and providing assessors with evidence of configuration change control.

---

## 2. Goals

1. Enforce automated code review gates that block merge when security, compliance, or quality standards are not met
2. Run the full test suite (pytest unit tests + behave BDD tests) with coverage verification as the first gate
3. Execute the complete security scanning pipeline (SAST, dependency audit, secret detection, container scan) as part of review
4. Verify STIG compliance with 0 CAT1 tolerance
5. Verify CUI markings are present on all source files and compliance documents
6. Verify SBOM currency (reflects current dependencies)
7. Record every gate decision in the append-only audit trail with NIST 800-53 SA-11, SA-15, and CM-3 control mappings
8. Provide specific remediation guidance for every gate failure, not just pass/fail status

---

## 3. Architecture

### 3.1 Review Gate Pipeline

```
+------------------------------------------------------------------+
|                    CODE REVIEW GATE PIPELINE                      |
|                                                                   |
|  Step 1         Step 2          Step 3          Step 4            |
|  +---------+    +-----------+   +-----------+   +-----------+     |
|  | TEST    |--->| SECURITY  |-->| STIG      |-->| CUI       |    |
|  | SUITE   |    | SCAN      |   | CHECK     |   | VERIFY    |    |
|  |         |    |           |   |           |   |           |    |
|  | pytest  |    | SAST      |   | 0 CAT1    |   | All files |    |
|  | behave  |    | deps      |   | findings  |   | marked    |    |
|  | cov>=80%|    | secrets   |   |           |   |           |    |
|  +---------+    | container |   +-----------+   +-----------+    |
|                 +-----------+                                     |
|                                                                   |
|  Step 5         Step 6          Step 7          Step 8            |
|  +---------+    +-----------+   +-----------+   +-----------+     |
|  | SBOM    |--->| LINT      |-->| GATE      |-->| AUDIT     |    |
|  | VERIFY  |    | CHECK     |   | DECISION  |   | TRAIL     |    |
|  |         |    |           |   |           |   |           |    |
|  | Current |    | 0 critical|   | ALL PASS  |   | SA-11     |    |
|  | deps    |    | errors    |   | = APPROVE |   | SA-15     |    |
|  | match   |    |           |   | ANY FAIL  |   | CM-3      |    |
|  |         |    |           |   | = REJECT  |   |           |    |
|  +---------+    +-----------+   +-----------+   +-----------+    |
+------------------------------------------------------------------+
```

### 3.2 Gate Decision Matrix

```
                    ALL GATES PASS           ANY GATE FAILS
                    +-------------+          +---------------+
                    |             |          |               |
                    v             |          v               |
              +-----------+      |    +-----------+         |
              | APPROVE   |      |    | REJECT    |         |
              | Merge     |      |    | Block     |         |
              | allowed   |      |    | merge     |         |
              +-----------+      |    +-----------+         |
                    |            |          |               |
                    v            |          v               |
              +-----------+      |    +-----------+         |
              | Audit     |      |    | Report    |         |
              | trail     |      |    | ALL fails |         |
              | entry:    |      |    | with      |         |
              | APPROVED  |      |    | remediation|        |
              +-----------+      |    | guidance   |        |
                                 |    +-----------+         |
                                 |                          |
                                 +--------------------------+
                                   Both decisions logged
                                   to audit trail
```

### 3.3 NIST Control Mapping

| Gate | NIST 800-53 Control | Requirement |
|------|---------------------|-------------|
| Test Suite | SA-11 | Developer testing and evaluation |
| Security Scan | SA-11, RA-5 | Vulnerability analysis, risk assessment |
| STIG Check | CM-6 | Configuration settings |
| CUI Markings | SC-16, MP-3 | Information classification, media marking |
| SBOM Verify | CM-8 | System component inventory |
| Lint Check | SA-15 | Development standards and tools |
| Gate Decision | CM-3 | Configuration change control |
| Audit Trail | AU-2, AU-3 | Audit events, content of audit records |

---

## 4. Requirements

### 4.1 Test Suite Gate

#### REQ-07-001: Full Test Execution
The review gate SHALL execute all unit tests (pytest) and all BDD tests (behave) for the project.

#### REQ-07-002: Coverage Threshold
Code coverage SHALL be measured and must meet or exceed 80%. Coverage below 80% is a blocking condition.

#### REQ-07-003: Zero Failure Tolerance
All tests MUST pass with 0 failures. Any test failure blocks the merge.

### 4.2 Security Scan Gate

#### REQ-07-004: SAST Clean
The review gate SHALL run SAST analysis and block merge on any critical or high findings.

#### REQ-07-005: Dependency Audit
The review gate SHALL run dependency audit and block merge on any critical CVE findings.

#### REQ-07-006: Secret Detection
The review gate SHALL run secret detection with zero tolerance -- any detected secret blocks the merge.

#### REQ-07-007: Container Scan
When applicable, the review gate SHALL run container scanning and block merge on critical/high findings.

### 4.3 Compliance Gate

#### REQ-07-008: STIG Compliance
The review gate SHALL run STIG compliance checks against applicable profiles and block merge on any CAT1 finding.

#### REQ-07-009: CUI Marking Verification
The review gate SHALL verify that all source files have CUI header banners and all documents have CUI banners and designation indicators.

#### REQ-07-010: SBOM Currency
The review gate SHALL generate a current SBOM and verify it reflects current dependencies. Stale SBOMs block merge.

### 4.4 Quality Gate

#### REQ-07-011: Lint Check
The review gate SHALL run language-appropriate linting and block merge on critical errors (warnings acceptable).

### 4.5 Decision and Audit

#### REQ-07-012: All-or-Nothing Decision
ALL gates MUST pass for merge approval. ANY single failure blocks the merge.

#### REQ-07-013: Complete Failure Reporting
When multiple gates fail, the system SHALL report ALL failures (not just the first one) with specific remediation guidance for each.

#### REQ-07-014: Audit Trail Entry
Every gate decision (approve or reject) SHALL be recorded in the append-only audit trail with event type `code.review`, all gate results, decision, and timestamp.

#### REQ-07-015: Unavailable Tool Handling
If a gate tool is unavailable, the gate SHALL be marked UNKNOWN and the overall decision SHALL be REJECT.

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `audit_trail` | Append-only log of code review gate decisions with per-gate results |
| `nist_controls` | NIST 800-53 control evidence from review gates (SA-11, SA-15, CM-3) |
| `security_findings` | Findings discovered during review scan |
| `projects` | Project quality metrics including review history |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/builder/linter.py` | Run language-appropriate linting (flake8/ruff, eslint, checkstyle) |
| `tools/builder/formatter.py` | Run language-appropriate formatting (black, prettier) |
| `tools/security/sast_runner.py` | Run SAST analysis as part of review |
| `tools/security/dependency_auditor.py` | Run dependency audit as part of review |
| `tools/security/secret_detector.py` | Run secret detection as part of review |
| `tools/security/container_scanner.py` | Run container scanning as part of review |
| `tools/compliance/stig_checker.py` | Run STIG compliance checks as part of review |
| `tools/compliance/cui_marker.py` | Verify CUI markings on all files |
| `tools/compliance/sbom_generator.py` | Generate and verify SBOM currency |
| `tools/audit/audit_logger.py` | Log gate decisions to append-only audit trail |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D5 | CUI markings applied at generation time | Review gate verifies markings exist; no unmarked code merges |
| D6 | Audit trail is append-only/immutable | Gate decisions cannot be retroactively modified; CM-3 evidence |
| D155 | Shared conftest.py centralizes test setup | Consistent test behavior across review runs |

---

## 8. Security Gate

**Code Review Gate (Blocking Conditions):**
- >= 1 approval required (human reviewer)
- All comments resolved
- SAST clean (0 critical/high)
- No secrets detected (0 tolerance)
- CUI markings present on all files
- Tests 100% pass
- Coverage >= 80% (warning threshold)
- STIG 0 CAT1 (blocking)
- Dependencies 0 critical (blocking)
- SBOM current (warning)
- Lint 0 critical errors (warning)

**Gate Severity Classification:**

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

---

## 9. Commands

```bash
# Code review skill (runs full gate pipeline)
/icdev-review    # Enforce code review gates with security checks

# Individual gate checks (for debugging)
# Step 1: Test suite
pytest tests/ -v --tb=short --cov=src --cov-report=term-missing

# Step 2: Security scan
python tools/security/sast_runner.py --project-dir "/path" --gate
python tools/security/dependency_auditor.py --project-dir "/path" --gate
python tools/security/secret_detector.py --project-dir "/path" --gate
python tools/security/container_scanner.py --image "my-image:latest"

# Step 3: STIG check
python tools/compliance/stig_checker.py --project-id "proj-123"

# Step 4: CUI verification
python tools/compliance/cui_marker.py --directory "/path" --verify

# Step 5: SBOM currency
python tools/compliance/sbom_generator.py --project-dir "/path"

# Step 6: Lint check
python tools/builder/linter.py --project-dir "/path"

# Audit logging
python tools/audit/audit_logger.py --event-type "code.review" \
  --actor "orchestrator" --action "Gate evaluation: APPROVED" \
  --project-id "proj-123"
```
