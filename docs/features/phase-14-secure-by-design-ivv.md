# Phase 14 — Secure by Design & IV&V

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 14 |
| Title | Secure by Design & Independent Verification and Validation |
| Status | Implemented |
| Priority | P1 |
| Dependencies | Phase 6 (Compliance Workflow), Phase 7 (Security Scanning), Phase 12 (Integration Testing) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

Traditional compliance assessment treats security as a checklist applied after development. A project can pass individual STIG checks, have no critical CVEs, and maintain 80% test coverage, yet still be fundamentally insecure because security was never an architectural design decision. Default credentials might be absent not because the system enforces unique credentials at the framework level, but because a developer happened to change the defaults. Without Secure by Design (SbD) assessment, there is no distinction between accidental compliance and intentional security architecture.

Similarly, Gov/DoD acquisitions under DoDI 5000.87 (Software Acquisition Pathway) require independent verification and validation (IV&V) as evidence that systems have been independently assessed against requirements. IEEE 1012-2016 defines the V&V standard with 9 process areas covering requirements, design, code, testing, integration, traceability, security, deployment, and process compliance. Without automated IV&V assessment, programs must rely on expensive manual audits that delay ATO timelines by weeks or months.

The Requirements Traceability Matrix (RTM) bridges both standards. SbD needs evidence that security requirements trace from design to implementation to tests. IV&V needs evidence that all requirements (not just security) have forward and backward traceability. The RTM tool discovers requirements from `.feature` files and requirements documents, maps them through design artifacts to source code and tests, and identifies gaps -- untested requirements and orphan tests that trace to no requirement.

---

## 2. Goals

1. Assess applications against CISA Secure by Design commitments across 14 domains with 20 automated checks, covering authentication, memory safety, vulnerability management, cryptography, access control, input handling, supply chain, threat modeling, defense in depth, and CUI compliance
2. Generate CUI-marked SbD reports with executive summary, CISA 7 Commitment status table, domain-by-domain assessment details, remediation recommendations, and NIST 800-53 control mapping
3. Generate a Requirements Traceability Matrix (RTM) with forward trace (requirement, design, code, tests), backward trace (test, code, design, requirement), gap analysis, and coverage percentage
4. Assess applications against IEEE 1012 IV&V across 9 process areas with 18 automated checks, producing certification recommendations (CERTIFY >= 80%, CONDITIONAL >= 60%, DENY < 60%)
5. Generate CUI-marked IV&V certification reports with verification results, validation results, RTM summary, findings list, independence declaration, and NIST 800-53 control mapping
6. Assess applications against DoDI 5000.87 Digital Engineering Strategy (DES) requirements for software acquisition pathway compliance
7. Enforce blocking security gates: 0 critical SbD requirements "not_satisfied", 0 critical IV&V findings, 0 non-compliant DES requirements
8. Record all assessment events in the append-only audit trail satisfying NIST SA-11, SA-15, SA-24, and CM-3

---

## 3. Architecture

```
+-----------------------------------------------------------+
|              Assessment Pipeline                          |
|                                                           |
|  Step 1: SbD Assessment (14 domains, 20 checks)          |
|  +----------------------------------------------------+  |
|  | Authentication | Memory Safety | Vuln Mgmt | Crypto |  |
|  | Access Control | Input Handling | Error Handling    |  |
|  | Supply Chain | Threat Modeling | Defense in Depth   |  |
|  | Secure Defaults | CUI Compliance | DoD Assurance    |  |
|  +----------------------------------------------------+  |
|           |                                               |
|  Step 2: SbD Report Generation                            |
|           |                                               |
|  Step 3: RTM Generation                                   |
|  +----------------------------------------------------+  |
|  | Requirements --> Design --> Code --> Tests           |  |
|  | .feature files   arch.md    src/    tests/          |  |
|  | requirements.md  ADR dir                            |  |
|  +----------------------------------------------------+  |
|           |                                               |
|  Step 4: IV&V Assessment (9 process areas, 18 checks)    |
|  +----------------------------------------------------+  |
|  | Requirements Verification  | Design Verification    |  |
|  | Code Verification          | Test Verification      |  |
|  | Integration Verification   | Traceability Analysis  |  |
|  | Security Verification      | Build/Deploy Verify    |  |
|  | Process Compliance                                  |  |
|  +----------------------------------------------------+  |
|           |                                               |
|  Step 5: IV&V Report + Certification Recommendation       |
|           |                                               |
|  Step 6: DES Assessment (DoDI 5000.87)                    |
|           |                                               |
|  Step 7: Audit Trail                                      |
+-----------------------------------------------------------+
```

### SbD Scoring

```
Score = 100 * (satisfied + partial * 0.5 + risk_accepted * 0.75) / assessable_count
```

### IV&V Scoring

```
Verification (60%): Average of process areas 1-6
Validation   (40%): Average of process areas 4-5 (test-focused)
Overall:            Weighted combination

CERTIFY:     >= 80% overall, 0 critical findings
CONDITIONAL: >= 60% overall, 0 critical findings
DENY:        < 60% OR any critical findings
```

---

## 4. Requirements

### 4.1 Secure by Design Assessment

#### REQ-14-001: 14-Domain SbD Assessment
The system SHALL assess applications across 14 Secure by Design domains: authentication, memory safety, vulnerability management, intrusion evidence, cryptography, access control, input handling, error handling, supply chain, threat modeling, defense in depth, secure defaults, CUI compliance, and DoD software assurance.

#### REQ-14-002: CISA 7 Commitment Mapping
The system SHALL map assessment results to the CISA Secure by Design Pledge 7 commitments, producing a commitment status table in the generated report.

#### REQ-14-003: SbD Scoring
The system SHALL calculate SbD scores using the formula: 100 * (satisfied + partial * 0.5 + risk_accepted * 0.75) / assessable_count.

#### REQ-14-004: SbD Critical Gate
The system SHALL block deployment when any critical SbD requirement is "not_satisfied" (0 critical not_satisfied = PASS).

### 4.2 Requirements Traceability Matrix

#### REQ-14-005: Forward and Backward Traceability
The system SHALL generate forward traces (requirement to design to code to tests) and backward traces (test to code to design to requirement) for all discovered artifacts.

#### REQ-14-006: Gap Analysis
The RTM SHALL identify untested requirements and orphan tests (tests that trace to no requirement), calculating coverage percentage.

#### REQ-14-007: Multi-Source Discovery
The RTM tool SHALL discover requirements from `.feature` files, `requirements.md`, and user stories; design from `architecture.md`, `system_design.md`, and ADR directories; code from `src/` modules; and tests from `tests/` directories.

### 4.3 IV&V Assessment

#### REQ-14-008: 9-Process-Area IV&V Assessment
The system SHALL assess applications across 9 IEEE 1012 process areas: requirements verification, design verification, code verification, test verification, integration verification, traceability analysis, security verification, build/deploy verification, and process compliance.

#### REQ-14-009: Certification Recommendation
The system SHALL produce certification recommendations: CERTIFY (>= 80% overall, 0 critical findings), CONDITIONAL (>= 60% overall, 0 critical findings), DENY (< 60% or any critical findings).

#### REQ-14-010: Independence Declaration
IV&V reports SHALL include an independence declaration per IEEE 1012-2016, stating that automated assessment provides process verification (human-independent assessment may additionally be required for formal certification).

### 4.4 DES Compliance

#### REQ-14-011: DoDI 5000.87 DES Assessment
The system SHALL assess applications against DoDI 5000.87 Digital Engineering Strategy requirements, including digital engineering ecosystem, model-based approaches, data management, and infrastructure requirements.

#### REQ-14-012: DES Critical Gate
The system SHALL block deployment when any critical DES requirement is "non_compliant" (0 non_compliant critical = PASS).

### 4.5 Reporting and Compliance

#### REQ-14-013: CUI-Marked Reports
All generated reports (SbD, IV&V, DES) SHALL include CUI markings appropriate to the project's impact level.

#### REQ-14-014: NIST 800-53 Control Mapping
Assessment results SHALL be mapped to NIST 800-53 Rev 5 controls (SA-11, SA-15, SA-24 and others) for crosswalk integration with other compliance frameworks.

#### REQ-14-015: Certification Validity Period
IV&V certifications SHALL expire after 12 months per `args/project_defaults.yaml`, requiring re-assessment after significant changes.

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `sbd_assessments` | SbD assessment results per project with domain scores, overall score, and finding details |
| `ivv_assessments` | IV&V assessment results per project with process area scores, certification recommendation |
| `des_assessments` | DES compliance assessment results per project |
| `traceability_links` | RTM forward and backward trace links between requirements, design, code, and test artifacts |
| `audit_trail` | Append-only assessment event records (NIST AU compliance) |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/compliance/sbd_assessor.py` | Secure by Design assessment across 14 domains with 20 automated checks |
| `tools/compliance/sbd_report_generator.py` | CUI-marked SbD report with CISA commitment status and remediation recommendations |
| `tools/compliance/traceability_matrix.py` | RTM generation with forward/backward trace, gap analysis, coverage percentage |
| `tools/compliance/ivv_assessor.py` | IEEE 1012 IV&V assessment across 9 process areas with 18 automated checks |
| `tools/compliance/ivv_report_generator.py` | CUI-marked IV&V certification report with independence declaration |
| `tools/mbse/des_assessor.py` | DoDI 5000.87 Digital Engineering Strategy compliance assessment |
| `tools/mbse/des_report_generator.py` | CUI-marked DES compliance report |
| `tools/audit/audit_logger.py` | Append-only audit trail recording for all assessment events |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D5 | CUI markings applied at generation time (inline, not post-processing) | All SbD/IV&V/DES reports include CUI banners from the start, not added after the fact |
| D6 | Audit trail is append-only/immutable | Assessment events satisfy NIST AU controls with tamper-evident recording |
| D111 | Dual-hub crosswalk model (NIST 800-53 US hub, ISO 27001 international hub) | SbD/IV&V findings crosswalk through NIST 800-53 to all other frameworks automatically |
| D116 | BaseAssessor ABC pattern for all compliance assessors | SbD/IV&V assessors inherit crosswalk integration, gate evaluation, and CLI from base class |

---

## 8. Security Gate

**SbD Gate:**
- 0 critical SbD requirements "not_satisfied"
- CISA 7 Commitments assessed and documented
- SbD report generated with CUI markings

**IV&V Gate:**
- 0 critical IV&V findings
- Certification recommendation not "DENY"
- Independence declaration included in report
- RTM generated with gap analysis

**DES Gate:**
- 0 non_compliant critical DoDI 5000.87 Digital Engineering requirements
- DES report generated with CUI markings

---

## 9. Commands

```bash
# Secure by Design assessment
python tools/compliance/sbd_assessor.py --project-id "proj-123" --domain all
python tools/compliance/sbd_report_generator.py --project-id "proj-123"

# Requirements Traceability Matrix
python tools/compliance/traceability_matrix.py --project-id "proj-123" --project-dir "/path"

# IV&V assessment
python tools/compliance/ivv_assessor.py --project-id "proj-123" --process-area all
python tools/compliance/ivv_report_generator.py --project-id "proj-123"

# DES assessment (DoDI 5000.87)
python tools/mbse/des_assessor.py --project-id "proj-123" --project-dir /path --json
python tools/mbse/des_report_generator.py --project-id "proj-123" --output-dir /path

# Audit trail
python tools/audit/audit_logger.py --event-type "sbd_ivv.assessed" --actor "orchestrator" --action "SbD + IV&V assessment complete" --project-id "proj-123"
```

**CUI // SP-CTI**
