# Standards Mapping — Security Infrastructure Controls
<!-- CUI // SP-CTI -->

**Requirement Source:** `features/security_infrastructure.feature`  
**Requirement ID:** SEC-INFRA-001  
**Requirement Statement:** The system enforces security controls per the accreditation boundary  
**Mapped Date:** 2026-05-16

---

## Requirement Summary (from Step 1)

| Field | Value |
|-------|-------|
| Requirement ID | SEC-INFRA-001 |
| Name | All security requirements for the system |
| Status | Active |
| Acceptance Artifacts | `args/security_gates.yaml`, `tools/security/sast_runner.py`, `tools/security/secret_detector.py`, `tools/security/classification_enforcer.py` |

---

## NIST SP 800-53 Rev 5 Control Mappings

### Primary Controls

| Control ID | Control Title | Description | Artifact |
|-----------|--------------|-------------|----------|
| **CA-7** | Continuous Monitoring | Develop a system-level continuous monitoring strategy and implement a program that includes establishing defined metrics; monitoring security and privacy controls at a defined frequency; ongoing threat and vulnerability assessments; and reporting the security and privacy posture of the system. | `args/security_gates.yaml` |
| **CA-2** | Control Assessments | Develop, document, and implement a plan to assess the controls in the system and its environment of operation. | Overall SEC-INFRA-001 |
| **SA-11** | Developer Security Testing and Evaluation | Require the developer of the system to implement a security assessment plan and produce evidence of security assessment plan execution. | `tools/security/sast_runner.py` |
| **SA-11(1)** | Developer Security Testing \| Static Code Analysis | Require the developer to employ static code analysis tools to identify common flaws and document the results of the analysis. | `tools/security/sast_runner.py` |
| **IA-5(7)** | Authenticator Management \| No Embedded Unencrypted Static Authenticators | Ensure that unencrypted static authenticators are not embedded in applications or access scripts or stored on function keys. | `tools/security/secret_detector.py` |
| **MP-3** | Media Marking | Mark system media with necessary CUI markings and distribution limitations. | `tools/security/classification_enforcer.py` |
| **AC-16** | Security and Privacy Attributes | Support and maintain the binding of security attributes to information in storage, in process, and in transmission. | `tools/security/classification_enforcer.py` |
| **RA-5** | Vulnerability Monitoring and Scanning | Monitor and scan for vulnerabilities in the system and hosted applications at a defined frequency and randomly in accordance with defined process. | `tools/security/sast_runner.py` |
| **SI-3** | Malicious Code Protection | Implement malicious code protection mechanisms at system entry and exit points to detect and eradicate malicious code. | `tools/security/sast_runner.py`, `tools/security/secret_detector.py` |

### Supporting Controls

| Control ID | Control Title | Description | Artifact |
|-----------|--------------|-------------|----------|
| **CA-3** | Information Exchange | Approve and manage the exchange of information between the system and other systems using connection agreements. | `args/security_gates.yaml` |
| **IA-5** | Authenticator Management | Manage system authenticators by verifying, as part of the initial authenticator distribution, the identity of the individual, group, role, service, or device receiving the authenticator. | `tools/security/secret_detector.py` |
| **PL-2** | System Security and Privacy Plans | Develop security and privacy plans for the system that describe the security and privacy requirements for the system and the controls in place or planned for meeting those requirements. | Overall SEC-INFRA-001 |
| **PL-8** | Security and Privacy Architectures | Develop security and privacy architectures for the system that describe the requirements and approach to be taken for protecting the confidentiality, integrity, and availability of organizational information. | `args/security_gates.yaml` |
| **SA-15** | Development Process, Standards, and Tools | Require the developer to follow a documented development process that explicitly addresses security and privacy requirements. | `tools/security/sast_runner.py` |
| **SC-28** | Protection of Information at Rest | Implement cryptographic mechanisms to prevent unauthorized disclosure and modification of information at rest. | `tools/security/secret_detector.py` |
| **SI-2** | Flaw Remediation | Identify, report, and correct information system flaws; test software and firmware updates related to flaw remediation for effectiveness and potential side effects before installation. | `tools/security/sast_runner.py` |
| **SI-12** | Information Management and Retention | Manage and retain information within the system and information output from the system in accordance with applicable laws, executive orders, directives, regulations, policies, standards, guidelines, and operational requirements. | `tools/security/classification_enforcer.py` |

---

## ISO/IEC 27001:2022 Control Mappings

| Control ID | Control Title | Description | Artifact |
|-----------|--------------|-------------|----------|
| **A.8.8** | Management of technical vulnerabilities | Information about technical vulnerabilities of information systems shall be obtained in a timely fashion; the organization's exposure to such vulnerabilities shall be evaluated and appropriate measures shall be taken. | `tools/security/sast_runner.py` |
| **A.8.25** | Secure development life cycle | Rules for the secure development of software and systems shall be established and applied. | `args/security_gates.yaml`, `tools/security/sast_runner.py` |
| **A.8.26** | Application security requirements | Information security requirements shall be identified, specified and approved when developing or acquiring applications. | `args/security_gates.yaml` |
| **A.8.28** | Secure coding | Secure coding principles shall be applied to software development. | `tools/security/sast_runner.py` |
| **A.8.29** | Security testing in development and acceptance | Security testing processes shall be defined and implemented in the development life cycle. | `tools/security/sast_runner.py` |
| **A.5.12** | Classification of information | Information shall be classified according to the information security needs of the organization based on confidentiality, integrity, availability, and relevant interested party requirements. | `tools/security/classification_enforcer.py` |
| **A.5.13** | Labelling of information | An appropriate set of procedures for information labelling shall be developed and implemented in accordance with the information classification scheme adopted by the organization. | `tools/security/classification_enforcer.py` |
| **A.8.9** | Configuration management | Configurations, including security configurations, of hardware, software, services and networks shall be established, documented, implemented, monitored, and reviewed. | `args/security_gates.yaml` |

---

## CMMC 2.0 (Level 2) Mapping

| Practice ID | Practice Title | Description | Artifact |
|------------|---------------|-------------|----------|
| **SI.2.216** | Identify, report, and correct information and information system flaws in a timely manner | Flaw remediation tied to scanning and patching cadence. | `tools/security/sast_runner.py` |
| **SI.2.217** | Provide protection from malicious code at appropriate locations within organizational information systems | Malicious code scanning at ingress/egress. | `tools/security/sast_runner.py`, `tools/security/secret_detector.py` |
| **CA.2.157** | Develop, document, and periodically update system security plans | Continuous monitoring plans updated and enforced. | `args/security_gates.yaml` |
| **CA.2.158** | Periodically assess the security controls in organizational systems | Automated gate checks on every commit simulate continuous assessment. | `args/security_gates.yaml` |
| **MA.2.111** | Perform maintenance on organizational systems | Maintenance procedures enforced through gates and classification markings. | `tools/security/classification_enforcer.py` |

---

## DoD Accreditation Boundary Alignment

The requirement "system enforces security controls per the accreditation boundary" maps directly to the following RMF (Risk Management Framework) phases under DODI 8510.01:

| RMF Step | Description | Relevant Gate Behavior |
|----------|-------------|------------------------|
| **Step 4 – Implement** | Implement controls identified in the SSP | Security gates enforce runtime presence of all four artifacts |
| **Step 5 – Assess** | Assess whether controls are implemented correctly | Automated BDD validation (`features/security_infrastructure.feature`) asserts all controls are operational on every commit |
| **Step 6 – Authorize** | Authorize operation (ATO) | Merge gates block promotion if any SEC-INFRA-001 control artifact is absent or non-functional |
| **Step 7 – Monitor** | Monitor controls on an ongoing basis | Continuous CI/CD enforcement through `args/security_gates.yaml` |

---

## Traceability Matrix

| SEC-INFRA-001 Sub-Control | Artifact | NIST 800-53 | ISO 27001 | CMMC |
|--------------------------|----------|-------------|-----------|------|
| Security gates configuration | `args/security_gates.yaml` | CA-7, PL-8, CA-3 | A.8.9, A.8.25, A.8.26 | CA.2.157, CA.2.158 |
| SAST runner | `tools/security/sast_runner.py` | SA-11, SA-11(1), RA-5, SI-2, SI-3 | A.8.8, A.8.28, A.8.29 | SI.2.216, SI.2.217 |
| Secret detector | `tools/security/secret_detector.py` | IA-5, IA-5(7), SC-28, SI-3 | A.8.8 | SI.2.217 |
| Classification enforcer | `tools/security/classification_enforcer.py` | MP-3, AC-16, SI-12 | A.5.12, A.5.13 | MA.2.111 |

---

*Classification: CUI // SP-CTI*  
*Generated by ICDEV™ task-01cbd178e9-d2*
