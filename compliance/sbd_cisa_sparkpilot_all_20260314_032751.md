//////////////////////////////////////////////////////////////////
CONTROLLED UNCLASSIFIED INFORMATION (CUI) // SP-CTI
Distribution: Distribution D — Authorized DoD Personnel Only
//////////////////////////////////////////////////////////////////

# SbD Assessment Report -- CISA Secure by Design / DoDI 5000.87

**Project:** SparkPilot (sparkpilot)
**Assessment Date:** 2026-03-14 03:27 UTC
**Assessor:** ICDEV™ Compliance Engine (automated)
**Domain Scope:** all
**CISA SbD Revision:** N/A
**Classification:** CUI // SP-CTI

---

## Executive Summary

| Domain | Total | Satisfied | Partial | Not Satisfied | Not Assessed | N/A | Risk Accepted |
|--------|-------|-----------|---------|---------------|--------------|-----|---------------|
| Authentication | 2 | 1 | 0 | 1 | 0 | 0 | 0 |
| Memory Safety | 2 | 1 | 0 | 1 | 0 | 0 | 0 |
| Intrusion Evidence | 3 | 1 | 0 | 0 | 2 | 0 | 0 |
| Cryptography | 3 | 1 | 1 | 0 | 1 | 0 | 0 |
| Access Control | 2 | 1 | 0 | 0 | 1 | 0 | 0 |
| Input Handling | 3 | 3 | 0 | 0 | 0 | 0 | 0 |
| Error Handling | 2 | 0 | 1 | 0 | 1 | 0 | 0 |
| Supply Chain | 3 | 1 | 1 | 0 | 1 | 0 | 0 |
| Threat Modeling | 2 | 1 | 0 | 0 | 1 | 0 | 0 |
| Defense in Depth | 2 | 0 | 0 | 0 | 2 | 0 | 0 |
| Secure Defaults | 3 | 0 | 1 | 1 | 1 | 0 | 0 |
| CUI Compliance | 2 | 1 | 0 | 0 | 1 | 0 | 0 |
| DoD Software Assurance | 3 | 0 | 0 | 0 | 3 | 0 | 0 |
| **Total** | **32** | **11** | **4** | **3** | **14** | **0** | **0** |

---

## Detailed Findings

### Authentication

#### SBD-01: Multi-Factor Authentication

**Priority:** CRITICAL  
**Status:** Satisfied  
**Automation Level:** auto  
**NIST Controls:** IA-2(1), IA-2(2)  
**Cisa Commitment:** 1

**Evidence:** MFA/multi-factor authentication patterns found in 30 file(s).

**Details:** control_inheritance.yaml; zta_config.yaml; ato_boundary_impact.yaml; e2e_knowledge_graph.py; test_compliance_snapshots.py

---

#### SBD-02: Default Password Elimination

**Priority:** CRITICAL  
**Status:** Not Satisfied  
**Automation Level:** auto  
**NIST Controls:** IA-5  
**Cisa Commitment:** 2

**Evidence:** Default/hardcoded password patterns detected in 19 file(s).

**Details:** Files with potential default passwords: project_defaults.yaml; cisa_sbd.json; cisa_sbd_requirements.json; pci_dss_v4.json; webapp_stig.json

---

### Memory Safety

#### SBD-03: Memory-Safe Language Use

**Priority:** HIGH  
**Status:** Satisfied  
**Automation Level:** auto  
**NIST Controls:** SI-16  
**Cisa Commitment:** 3

**Evidence:** Memory-safe languages: 1154/1156 files (100%).

**Details:** Safe: 1154 | Unsafe: 2. Exceeds 90% threshold.

---

#### SBD-04: Memory Safety Tooling

**Priority:** MEDIUM  
**Status:** Not Satisfied  
**Automation Level:** auto  
**NIST Controls:** SA-11  
**Cisa Commitment:** 3

**Evidence:** Memory-unsafe code present but no memory safety tooling detected.

**Details:** Expected: AddressSanitizer, -fsanitize, Valgrind, MSAN, TSAN, or equivalent tooling in build configurations.

---

### Intrusion Evidence

#### SBD-08: Audit Logging Completeness

**Priority:** CRITICAL  
**Status:** Satisfied  
**Automation Level:** auto  
**NIST Controls:** AU-2, AU-3  
**Cisa Commitment:** 7

**Evidence:** Comprehensive audit logging detected: 6 distinct log event types across 581 file(s).

**Details:** Event types: access_logging, authentication_logging, change_logging, error_logging, security_logging, structured_logging. Files: pre_tool_use.py; default.yaml; marketplace-scan.yml; control_inheritance.yaml; db_config.yaml

---

#### SBD-09: Forensic Readiness

**Priority:** HIGH  
**Status:** Not Assessed  
**Automation Level:** semi  
**NIST Controls:** AU-7  
**Cisa Commitment:** 7

**Evidence:** Semi-automated: no automated component implemented.

**Notes:** Manual review required. Evidence needed: Forensic readiness plan documenting evidence sources and extraction procedures, evidence of test extraction completing within 1-hour SLA, and chain of custody documentation templates.

---

#### SBD-10: Log Integrity

**Priority:** HIGH  
**Status:** Not Assessed  
**Automation Level:** semi  
**NIST Controls:** AU-9  
**Cisa Commitment:** 7

**Evidence:** Semi-automated: no automated component implemented.

**Notes:** Manual review required. Evidence needed: Append-only log storage configuration, log integrity verification mechanism documentation, evidence of tamper detection or prevention controls, and log retention policy compliance records.

---

### Cryptography

#### SBD-11: TLS Configuration

**Priority:** CRITICAL  
**Status:** Partially Satisfied  
**Automation Level:** auto  
**NIST Controls:** SC-8, SC-13  
**Cisa Commitment:** N/A

**Evidence:** TLS patterns found in 247 file(s), but insecure patterns also detected in 10 file(s).

**Details:** Insecure files: bandit_baseline.json; cisa_sbd_requirements.json; dod_cssp_8530.json; nist_800_207_zta.json; fedramp_assessor.py. Remove SSLv3, TLSv1.0, TLSv1.1, and verify=False usage.

---

#### SBD-12: Encryption at Rest

**Priority:** HIGH  
**Status:** Satisfied  
**Automation Level:** auto  
**NIST Controls:** SC-28  
**Cisa Commitment:** N/A

**Evidence:** Encryption-at-rest patterns found in 128 file(s).

**Details:** cloudforge_config.yaml; container_lens_profiles.yaml; control_inheritance.yaml; databridge_config.yaml; mosa_config.yaml

---

#### SBD-13: Key Management

**Priority:** HIGH  
**Status:** Not Assessed  
**Automation Level:** manual  
**NIST Controls:** SC-12  
**Cisa Commitment:** N/A

**Evidence:** Manual assessment required.

**Notes:** This requirement must be verified manually. Evidence needed: Secrets vault configuration and access policies, key rotation policy and execution logs, secret detection scan results confirming no secrets in source code, and key inventory with rotation schedules.

---

### Access Control

#### SBD-14: Least Privilege RBAC

**Priority:** CRITICAL  
**Status:** Satisfied  
**Automation Level:** auto  
**NIST Controls:** AC-6  
**Cisa Commitment:** N/A

**Evidence:** RBAC / least-privilege patterns found in 57 file(s).

**Details:** control_inheritance.yaml; devsecops_config.yaml; endpoint_security_config.yaml; owasp_agentic_config.yaml; zta_config.yaml

---

#### SBD-15: Secure Session Management

**Priority:** HIGH  
**Status:** Not Assessed  
**Automation Level:** semi  
**NIST Controls:** AC-12, SC-23  
**Cisa Commitment:** N/A

**Evidence:** Semi-automated: no automated component implemented.

**Notes:** Manual review required. Evidence needed: Session management configuration showing cookie attributes and timeout values, session fixation test results, session invalidation test evidence, and concurrent session limit configuration.

---

### Input Handling

#### SBD-16: Input Validation

**Priority:** CRITICAL  
**Status:** Satisfied  
**Automation Level:** auto  
**NIST Controls:** SI-10  
**Cisa Commitment:** N/A

**Evidence:** Input validation patterns found in 51 file(s).

**Details:** mermaid.min.js; page.js; mdast-util-to-hast.js; micromark-extension-gfm-autolink-literal.js; micromark-extension-gfm-footnote.js

---

#### SBD-17: Output Encoding

**Priority:** HIGH  
**Status:** Satisfied  
**Automation Level:** auto  
**NIST Controls:** SI-10(5)  
**Cisa Commitment:** N/A

**Evidence:** Output encoding / XSS prevention patterns found in 57 file(s).

**Details:** mermaid.min.js; devsecops-challenges-in-achieving-fedramp-authorization.html; character-entities.js; debug.js; ms.js

---

#### SBD-18: Security Headers

**Priority:** HIGH  
**Status:** Satisfied  
**Automation Level:** auto  
**NIST Controls:** SC-8  
**Cisa Commitment:** N/A

**Evidence:** 5 distinct security headers configured: CORS, CSP, HSTS, X-Content-Type-Options, X-Frame-Options.

**Details:** Files: settings.json; cloudforge_config.yaml; csp_mcp_config.yaml; llm_config.yaml; marketplace_config.yaml

---

### Error Handling

#### SBD-19: Secure Error Handling

**Priority:** HIGH  
**Status:** Partially Satisfied  
**Automation Level:** auto  
**NIST Controls:** SI-11  
**Cisa Commitment:** N/A

**Evidence:** Secure error handling in 9 file(s), but insecure patterns in 8 file(s).

**Details:** Insecure files: main-app.js; polyfills.js; deploy.py; eject_scaffolder.py; poll_trigger.py. Remove DEBUG=True and stack trace exposure in responses.

---

#### SBD-20: Fail Secure Design

**Priority:** HIGH  
**Status:** Not Assessed  
**Automation Level:** semi  
**NIST Controls:** SC-24  
**Cisa Commitment:** N/A

**Evidence:** Semi-automated: no automated component implemented.

**Notes:** Manual review required. Evidence needed: Fail-secure design documentation, test results demonstrating secure failure behavior under error conditions, evidence that security controls remain enforced during failures, and side-channel analysis results.

---

### Supply Chain

#### SBD-21: SBOM Presence and Freshness

**Priority:** HIGH  
**Status:** Partially Satisfied  
**Automation Level:** auto  
**NIST Controls:** SR-4, SR-5  
**Cisa Commitment:** N/A

**Evidence:** SBOM artifact(s) found but all are stale (>30 days old): 25 file(s).

**Details:** Stale: sbom_sparkpilot_20260309_154312.cdx.json (-1d old); sbom_sparkpilot_20260309_154405.cdx.json (-1d old); sbom_sparkpilot_20260309_154706.cdx.json (-1d old); sbom_sparkpilot_20260309_154944.cdx.json (-1d old); sbom_sparkpilot_20260309_155223.cdx.json (-1d old). Regenerate SBOM to meet freshness requirement.

---

#### SBD-22: Dependency Vulnerability Scanning

**Priority:** CRITICAL  
**Status:** Satisfied  
**Automation Level:** auto  
**NIST Controls:** RA-5  
**Cisa Commitment:** N/A

**Evidence:** Dependency vulnerability scanning tooling/results found: 35 artifact(s).

**Details:** sbom_sparkpilot_20260309_224927.cdx.json; sbom_sparkpilot_20260309_154706.cdx.json; sbom_sparkpilot_20260309_154405.cdx.json; project_defaults.yaml; sbom_sparkpilot_20260309_155711.cdx.json

---

#### SBD-23: Provenance Verification

**Priority:** MEDIUM  
**Status:** Not Assessed  
**Automation Level:** semi  
**NIST Controls:** SR-3  
**Cisa Commitment:** N/A

**Evidence:** Semi-automated: no automated component implemented.

**Notes:** Manual review required. Evidence needed: Lock files with pinned versions and integrity hashes committed to repository, package registry configuration restricting to approved sources, and CI/CD configuration enforcing lock file integrity.

---

### Threat Modeling

#### SBD-24: Threat Model Artifact

**Priority:** HIGH  
**Status:** Satisfied  
**Automation Level:** auto  
**NIST Controls:** RA-3  
**Cisa Commitment:** N/A

**Evidence:** Threat model artifact(s) found: 18 item(s).

**Details:** threat_modeler.py; threat_model.md; threat_modeler.cpython-314.pyc; threat_model.md; THREAT_MODEL.md

---

#### SBD-25: Attack Surface Analysis

**Priority:** MEDIUM  
**Status:** Not Assessed  
**Automation Level:** manual  
**NIST Controls:** RA-3(3)  
**Cisa Commitment:** N/A

**Evidence:** Manual assessment required.

**Notes:** This requirement must be verified manually. Evidence needed: Attack surface analysis document listing all entry points and interfaces, mitigation mapping for each attack surface element, residual risk ratings, and evidence of updates when new interfaces are added.

---

### Defense in Depth

#### SBD-26: Multiple Security Layers

**Priority:** HIGH  
**Status:** Not Assessed  
**Automation Level:** semi  
**NIST Controls:** SC-7, PL-8  
**Cisa Commitment:** N/A

**Evidence:** Semi-automated: no automated component implemented.

**Notes:** Manual review required. Evidence needed: Defense-in-depth architecture diagram mapping security layers to critical paths, documentation showing at least 3 independent controls per critical path, and penetration test results validating layered defense effectiveness.

---

#### SBD-27: Network Segmentation

**Priority:** HIGH  
**Status:** Not Assessed  
**Automation Level:** semi  
**NIST Controls:** SC-7(5), AC-4  
**Cisa Commitment:** N/A

**Evidence:** Semi-automated: no automated component implemented.

**Notes:** Manual review required. Evidence needed: Kubernetes NetworkPolicy manifests with default-deny rules, per-service allow rules documentation, network segmentation test results, and lateral movement prevention validation.

---

### Secure Defaults

#### SBD-28: No Default Credentials in Config

**Priority:** CRITICAL  
**Status:** Not Satisfied  
**Automation Level:** auto  
**NIST Controls:** IA-5(1)  
**Cisa Commitment:** N/A

**Evidence:** Default credential patterns detected in 6 config file(s).

**Details:** Files with potential default credentials: project_defaults.yaml; security_gates.yaml; cisa_sbd.json; cisa_sbd_requirements.json; fedramp_high_baseline.json. Remove admin/admin, root/root, changeme, test/test, and other default credential patterns.

---

#### SBD-29: Secure Configuration Baselines

**Priority:** HIGH  
**Status:** Partially Satisfied  
**Automation Level:** auto  
**NIST Controls:** CM-6, CM-7  
**Cisa Commitment:** N/A

**Evidence:** Hardened Dockerfile(s) found (95), but insecure config patterns in 3 file(s).

**Details:** Hardened: Dockerfile.devsecops-agent: 2/4 hardening checks; dockerfile_generator.py: 2/4 hardening checks; dockerfile_generator.cpython-314.pyc: 2/4 hardening checks; dockerfile_generator.cpython-314.pyc: 2/4 hardening checks; Dockerfile: 2/4 hardening checks; Dockerfile.agent-base: 2/4 hardening checks; Dockerfile.api-gateway: 2/4 hardening checks; Dockerfile.architect: 2/4 hardening checks; Dockerfile.builder: 2/4 hardening checks; Dockerfile.compliance: 2/4 hardening checks; Dockerfile.dashboard: 2/4 hardening checks; Dockerfile.devsecops-agent: 3/4 hardening checks; Dockerfile.gateway-agent: 2/4 hardening checks; Dockerfile.infrastructure: 2/4 hardening checks; Dockerfile.integration-agent: 2/4 hardening checks; Dockerfile.mbse-agent: 2/4 hardening checks; Dockerfile.modernization-agent: 2/4 hardening checks; Dockerfile.orchestrator: 2/4 hardening checks; Dockerfile.requirements-analyst-agent: 2/4 hardening checks; Dockerfile.security: 2/4 hardening checks; Dockerfile.simulation-agent: 2/4 hardening checks; Dockerfile.supply-chain-agent: 2/4 hardening checks; dockerfile_generator.py: 2/4 hardening checks; Dockerfile.agent-base: 2/4 hardening checks; Dockerfile.api-gateway: 2/4 hardening checks; Dockerfile.architect: 2/4 hardening checks; Dockerfile.builder: 2/4 hardening checks; Dockerfile.compliance: 2/4 hardening checks; Dockerfile.dashboard: 2/4 hardening checks; Dockerfile.devsecops-agent: 3/4 hardening checks; Dockerfile.gateway-agent: 2/4 hardening checks; Dockerfile.infrastructure: 2/4 hardening checks; Dockerfile.integration-agent: 2/4 hardening checks; Dockerfile.mbse-agent: 2/4 hardening checks; Dockerfile.modernization-agent: 2/4 hardening checks; Dockerfile.orchestrator: 2/4 hardening checks; Dockerfile.requirements-analyst-agent: 2/4 hardening checks; Dockerfile.security: 2/4 hardening checks; Dockerfile.simulation-agent: 2/4 hardening checks; Dockerfile.supply-chain-agent: 2/4 hardening checks; dockerfile_generator.py: 2/4 hardening checks; Dockerfile.agent-base: 2/4 hardening checks; Dockerfile.api-gateway: 2/4 hardening checks; Dockerfile.architect: 2/4 hardening checks; Dockerfile.builder: 2/4 hardening checks; Dockerfile.compliance: 2/4 hardening checks; Dockerfile.dashboard: 2/4 hardening checks; Dockerfile.devsecops-agent: 3/4 hardening checks; Dockerfile.gateway-agent: 2/4 hardening checks; Dockerfile.infrastructure: 2/4 hardening checks; Dockerfile.integration-agent: 2/4 hardening checks; Dockerfile.mbse-agent: 2/4 hardening checks; Dockerfile.modernization-agent: 2/4 hardening checks; Dockerfile.orchestrator: 2/4 hardening checks; Dockerfile.requirements-analyst-agent: 2/4 hardening checks; Dockerfile.security: 2/4 hardening checks; Dockerfile.simulation-agent: 2/4 hardening checks; Dockerfile.supply-chain-agent: 2/4 hardening checks; dockerfile_generator.py: 2/4 hardening checks; Dockerfile.agent-base: 2/4 hardening checks; Dockerfile.api-gateway: 2/4 hardening checks; Dockerfile.architect: 2/4 hardening checks; Dockerfile.builder: 2/4 hardening checks; Dockerfile.compliance: 2/4 hardening checks; Dockerfile.dashboard: 2/4 hardening checks; Dockerfile.devsecops-agent: 3/4 hardening checks; Dockerfile.gateway-agent: 2/4 hardening checks; Dockerfile.infrastructure: 2/4 hardening checks; Dockerfile.integration-agent: 2/4 hardening checks; Dockerfile.mbse-agent: 2/4 hardening checks; Dockerfile.modernization-agent: 2/4 hardening checks; Dockerfile.orchestrator: 2/4 hardening checks; Dockerfile.requirements-analyst-agent: 2/4 hardening checks; Dockerfile.security: 2/4 hardening checks; Dockerfile.simulation-agent: 2/4 hardening checks; Dockerfile.supply-chain-agent: 2/4 hardening checks; dockerfile_generator.py: 2/4 hardening checks; Dockerfile.agent-base: 2/4 hardening checks; Dockerfile.api-gateway: 2/4 hardening checks; Dockerfile.architect: 2/4 hardening checks; Dockerfile.builder: 2/4 hardening checks; Dockerfile.compliance: 2/4 hardening checks; Dockerfile.dashboard: 2/4 hardening checks; Dockerfile.devsecops-agent: 3/4 hardening checks; Dockerfile.gateway-agent: 2/4 hardening checks; Dockerfile.infrastructure: 2/4 hardening checks; Dockerfile.integration-agent: 2/4 hardening checks; Dockerfile.mbse-agent: 2/4 hardening checks; Dockerfile.modernization-agent: 2/4 hardening checks; Dockerfile.orchestrator: 2/4 hardening checks; Dockerfile.requirements-analyst-agent: 2/4 hardening checks; Dockerfile.security: 2/4 hardening checks; Dockerfile.simulation-agent: 2/4 hardening checks; Dockerfile.supply-chain-agent: 2/4 hardening checks; dockerfile_generator.py: 2/4 hardening checks | Insecure configs: settings.local.json; eject_scaffolder.py; sbd_assessor.py

---

#### SBD-30: Principle of Least Functionality

**Priority:** MEDIUM  
**Status:** Not Assessed  
**Automation Level:** semi  
**NIST Controls:** CM-7  
**Cisa Commitment:** N/A

**Evidence:** Semi-automated: no automated component implemented.

**Notes:** Manual review required. Evidence needed: Minimal base image documentation, port and service inventory confirming only required services enabled, container image layer analysis showing no unnecessary packages, and configuration review confirming disabled non-essential features.

---

### CUI Compliance

#### SBD-31: CUI Markings on All Artifacts

**Priority:** CRITICAL  
**Status:** Satisfied  
**Automation Level:** auto  
**NIST Controls:** MP-3, AC-16  
**Cisa Commitment:** N/A

**Evidence:** CUI markings found in 1301/1498 files (87%).

**Details:** Threshold: >80%. Files scanned: 1498.

---

#### SBD-32: Data Flow Classification

**Priority:** MEDIUM  
**Status:** Not Assessed  
**Automation Level:** semi  
**NIST Controls:** RA-2  
**Cisa Commitment:** N/A

**Evidence:** Semi-automated: no automated component implemented.

**Notes:** Manual review required. Evidence needed: Data flow diagram with classification annotations at each node, data classification matrix mapping data types to sensitivity levels, and control validation records confirming appropriate protections at each classification level.

---

### DoD Software Assurance

#### SBD-33: SA-24 Cyber Resiliency

**Priority:** HIGH  
**Status:** Not Assessed  
**Automation Level:** semi  
**NIST Controls:** SA-24, CP-2  
**Cisa Commitment:** N/A

**Evidence:** Semi-automated: no automated component implemented.

**Notes:** Manual review required. Evidence needed: Cyber resiliency architecture documentation, redundancy and failover configuration, automated recovery mechanism implementation, recovery time objective test results, and graceful degradation test evidence.

---

#### SBD-34: CERT Coding Standards

**Priority:** MEDIUM  
**Status:** Not Assessed  
**Automation Level:** manual  
**NIST Controls:** SA-15  
**Cisa Commitment:** N/A

**Evidence:** Manual assessment required.

**Notes:** This requirement must be verified manually. Evidence needed: Static analysis tool configuration with CERT rule sets enabled, CERT compliance scan results, violation triage and remediation records, and risk acceptance documentation for any unresolved findings.

---

#### SBD-35: SSDF Lifecycle Practices

**Priority:** MEDIUM  
**Status:** Not Assessed  
**Automation Level:** manual  
**NIST Controls:** SA-3, SA-11  
**Cisa Commitment:** N/A

**Evidence:** Manual assessment required.

**Notes:** This requirement must be verified manually. Evidence needed: SSDF practice mapping document linking PO, PS, PW, and RV practices to SDLC phases, evidence of practice implementation for each SSDF task, and periodic SSDF compliance assessment results.

---

//////////////////////////////////////////////////////////////////
CUI // SP-CTI | Department of Defense
//////////////////////////////////////////////////////////////////
