# Goal: Secure by Design (SbD) + IV&V Certification Workflow

## Description

Assess applications against **CISA Secure by Design** commitments and **IEEE 1012 IV&V** process requirements. This workflow ensures that security is designed in from the start (not bolted on) and that an independent verification process confirms correctness.

**Standards:**
- CISA Secure by Design Pledge (7 commitments)
- DoDI 5000.87 (Software Acquisition Pathway)
- NIST SP 800-218 SSDF (Secure Software Development Framework)
- IEEE 1012-2016 (V&V Standard)
- NIST 800-53 Rev 5 SA-24 (Cyber Resiliency by Design)

**Why this matters:** SbD ensures security is an architectural decision, not an afterthought. IV&V provides independent assurance that systems work correctly and securely. Together they are the 7th and 8th components of the ICDEV compliance scoring system.

---

## Prerequisites

- [ ] Project initialized (`goals/init_project.md` completed)
- [ ] Project has code to assess
- [ ] Security scans completed (`goals/security_scan.md`)
- [ ] STIG checks completed (STIG findings feed SbD assessment)
- [ ] SBOM generated (freshness feeds SbD assessment)
- [ ] Tests written (test coverage feeds IV&V assessment)

---

## Process

### Step 1: Run SbD Assessment

**Tool:** `python tools/compliance/sbd_assessor.py --project-id <project_id> --domain all`

Assesses 14 domains with 20 automated checks:
- **Authentication:** MFA patterns, default password elimination
- **Memory Safety:** Language choice, safety tooling
- **Vulnerability Management:** Patch cadence, dependency scanning
- **Intrusion Evidence:** Audit logging completeness
- **Cryptography:** TLS config, encryption at rest
- **Access Control:** RBAC, least privilege
- **Input Handling:** Validation, output encoding
- **Error Handling:** Secure error handling, no stack traces
- **Supply Chain:** SBOM freshness, dependency scanning
- **Threat Modeling:** STRIDE/PASTA documentation
- **Defense in Depth:** Security headers, secure configs
- **Secure Defaults:** No default credentials, hardened baselines
- **CUI Compliance:** CUI markings present
- **DoD Software Assurance:** DoDI 5000.87 requirements

**CRITICAL GATE:** 0 critical "not_satisfied" = PASS

**Scoring:** 100 × (satisfied + partial×0.5 + risk_accepted×0.75) / assessable_count

---

### Step 2: Generate SbD Report

**Tool:** `python tools/compliance/sbd_report_generator.py --project-id <project_id>`

Generates CUI-marked report including:
- Executive Summary with overall SbD score
- CISA 7 Commitment status table
- Domain-by-domain assessment details
- Auto-check results and manual review items
- Remediation recommendations
- NIST 800-53 control mapping

---

### Step 3: Generate Requirements Traceability Matrix (RTM)

**Tool:** `python tools/compliance/traceability_matrix.py --project-id <project_id> --project-dir <path>`

Discovers and traces:
- **Requirements:** .feature files, requirements.md, user stories
- **Design:** architecture.md, system_design.md, ADR directory
- **Code:** src/ modules and their functionality
- **Tests:** tests/ directory mapped to requirements

Produces:
- Forward trace: requirement → design → code → tests
- Backward trace: test → code → design → requirement
- Gap analysis: untested requirements, orphan tests
- Coverage percentage

---

### Step 4: Run IV&V Assessment

**Tool:** `python tools/compliance/ivv_assessor.py --project-id <project_id> --process-area all`

Assesses 9 process areas with 18 automated checks:
1. **Requirements Verification:** Completeness, consistency, testability
2. **Design Verification:** Architecture docs, design patterns
3. **Code Verification:** SAST results, coding standards, complexity
4. **Test Verification:** Coverage, test plans, security tests
5. **Integration Verification:** BDD coverage, E2E tests
6. **Traceability Analysis:** RTM exists, requirements linked
7. **Security Verification:** Pipeline security, artifact integrity
8. **Build/Deploy Verification:** Config hardening, rollback capability
9. **Process Compliance:** All of the above in aggregate

**Scoring:**
- Verification (60%): Average of process areas 1-6
- Validation (40%): Average of process areas 4-5 (test-focused)
- Overall: weighted combination

**CRITICAL GATE:** 0 critical findings = PASS

**Certification recommendation:**
- **CERTIFY:** ≥80% overall, 0 critical findings
- **CONDITIONAL:** ≥60% overall, 0 critical findings
- **DENY:** <60% or any critical findings

---

### Step 5: Generate IV&V Report

**Tool:** `python tools/compliance/ivv_report_generator.py --project-id <project_id>`

Generates CUI-marked certification report including:
- Executive Summary with certification recommendation
- Verification results by process area
- Validation results (testing focus)
- RTM summary and coverage
- Findings list with severity and status
- Independence declaration
- NIST 800-53 control mapping

---

### Step 6: Log to Audit Trail

**Tool:** `python tools/audit/audit_logger.py --event "sbd_ivv_assessed" --actor "orchestrator" --action "SbD + IV&V assessment complete" --project <name>`

**Tool:** `python tools/memory/memory_write.py --content "SbD + IV&V assessed for <name>. SbD score: <pct>%, IV&V cert: <CERTIFY|CONDITIONAL|DENY>" --type event --importance 7`

---

## Success Criteria

- [ ] SbD assessment completed across all 14 domains
- [ ] SbD report generated with CISA commitment status and CUI markings
- [ ] RTM generated with forward and backward trace, gap analysis
- [ ] IV&V assessment completed across all 9 process areas
- [ ] IV&V certification recommendation generated (CERTIFY/CONDITIONAL/DENY)
- [ ] IV&V report generated with independence declaration and CUI markings
- [ ] Audit trail entries logged for all assessments
- [ ] No critical SbD requirements "not_satisfied"
- [ ] No critical IV&V findings open

---

## Edge Cases & Notes

1. **SbD is design-level:** SbD checks patterns and architecture, not individual vulnerabilities. A project can pass STIG checks but fail SbD if security wasn't designed in.
2. **IV&V independence:** Automated IV&V provides process verification. For formal certification, human-independent assessment may also be required per IEEE 1012.
3. **RTM gaps:** Untested requirements should be flagged as risks, not failures. Some requirements may be non-testable (policy, process).
4. **Memory safety:** Projects in memory-unsafe languages (C/C++) should have safety tooling (AddressSanitizer, Valgrind). This doesn't block the assessment but generates warnings.
5. **CISA commitments:** The 7 CISA commitments are aspirational targets. Partial satisfaction is expected for new projects. Track progress over time.
6. **Certification validity:** IV&V certifications expire after 12 months per `args/project_defaults.yaml`. Re-assessment required after significant changes.

---

## GOTCHA Layer Mapping

| Step | GOTCHA Layer | Component |
|------|-------------|-----------|
| SbD assessment | Tools | sbd_assessor.py |
| SbD report | Tools | sbd_report_generator.py |
| RTM generation | Tools | traceability_matrix.py |
| IV&V assessment | Tools | ivv_assessor.py |
| IV&V report | Tools | ivv_report_generator.py |
| Sequence decisions | Orchestration | AI (you) |
| SbD requirements | Context | cisa_sbd_requirements.json |
| IV&V requirements | Context | ivv_requirements.json |
| Gate thresholds | Args | security_gates.yaml |

---

## Related Files

- **Tools:** `tools/compliance/sbd_assessor.py`, `tools/compliance/sbd_report_generator.py`, `tools/compliance/traceability_matrix.py`, `tools/compliance/ivv_assessor.py`, `tools/compliance/ivv_report_generator.py`
- **Context:** `context/compliance/cisa_sbd_requirements.json`, `context/compliance/ivv_requirements.json`, `context/compliance/sbd_report_template.md`, `context/compliance/ivv_report_template.md`, `context/compliance/ivv_checklist_template.md`
- **Args:** `args/project_defaults.yaml` (sbd + ivv sections), `args/security_gates.yaml` (sbd + ivv thresholds)
- **Hard Prompts:** `hardprompts/compliance/sbd_assessment.md`, `hardprompts/compliance/ivv_assessment.md`
- **Feeds from:** `goals/security_scan.md` (SAST findings), `goals/tdd_workflow.md` (test coverage)
- **Feeds into:** `goals/compliance_workflow.md` (compliance scoring), `goals/deploy_workflow.md` (deployment gate)

---

## Changelog

- 2026-02-15: Initial creation (Phase 15)
