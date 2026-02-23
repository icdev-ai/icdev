# Phase 4 — NIST 800-53 Compliance

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 4 |
| Title | NIST 800-53 Compliance & ATO Artifact Generation |
| Status | Implemented |
| Priority | P0 |
| Dependencies | Phase 1 (GOTCHA Framework Foundation), Phase 3 (TDD/BDD Testing Framework) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

No Authority to Operate (ATO) means no deployment. Government and DoD systems cannot operate without demonstrated compliance with NIST 800-53 Rev 5 security controls. An ATO package requires multiple interrelated artifacts: a System Security Plan (SSP), Plan of Action and Milestones (POAM), STIG checklists, Software Bill of Materials (SBOM), CUI markings, and NIST control mappings. These artifacts must be accurate, current, traceable, and internally consistent -- not boilerplate.

Manually assembling ATO packages is a 3-6 month endeavor that delays every project. Artifacts become stale within days of creation. Control mappings drift from implementation. Evidence links break. Assessors find gaps that could have been caught programmatically. The compliance burden is the single largest schedule risk for government software programs.

ICDEV automates the entire ATO artifact generation pipeline: from FIPS 199 security categorization through SSP generation, POAM creation from security findings, STIG evaluation, SBOM generation in CycloneDX format, CUI marking application, and NIST 800-53 control mapping with implementation evidence. Every artifact is generated from live project data, every finding is traceable to its source, and every document carries classification markings applied at generation time (not post-processing).

---

## 2. Goals

1. Generate a complete System Security Plan (SSP) with all 17 required sections populated from project data
2. Generate a Plan of Action and Milestones (POAM) from security scan findings with severity-based timelines (CAT1: 30 days, CAT2: 90 days, CAT3: 180 days)
3. Run STIG compliance checks against applicable profiles (OS, container, database, orchestrator) with 0 CAT1 tolerance
4. Generate a Software Bill of Materials (SBOM) in CycloneDX 1.5 format with all direct and transitive dependencies
5. Apply CUI markings to all compliance artifacts at generation time using `classification_manager.py`
6. Map NIST 800-53 control families (18 families) to implementation evidence with no control marked "Implemented" without linked evidence
7. Generate a consolidated compliance status report showing ATO readiness
8. Support NIST lookup for individual control details

---

## 3. Architecture

### 3.1 Compliance Pipeline (19 Steps)

```
+------------------+    +------------------+
| FIPS 199         |--->| FIPS 200         |
| Categorize       |    | Validate         |
| (Step 0a)        |    | (Step 0b)        |
+------------------+    +--------+---------+
                                 |
                                 v
+------+  +------+  +------+  +------+  +------+  +------+  +------+  +------+
| SSP  |  | POAM |  | STIG |  | SBOM |  | CUI  |  | CTRL |  | RPT  |  | LOG  |
| Step |  | Step |  | Step |  | Step |  | Step |  | Step |  | Step |  | Step |
|  1   |  |  2   |  |  3   |  |  4   |  |  5   |  |  6   |  |  7   |  |  8   |
+------+  +------+  +------+  +------+  +------+  +------+  +------+  +------+
                                                                 |
                              +----------------------------------+
                              v
+------+  +------+  +------+  +------+  +------+  +------+
| CSSP |  | SIEM |  | IR   |  | EVID |  | CSSP |  |XACTA|
| Step |  | Step |  | Step |  | Step |  | RPT  |  | Step|
|  9   |  | 10   |  | 11   |  | 12   |  | 13   |  | 14  |
+------+  +------+  +------+  +------+  +------+  +------+
                              |
                              v
              +------+  +------+  +------+  +------+  +------+
              | SbD  |  | SbD  |  | RTM  |  | IVV  |  | IVV  |
              | Step |  | RPT  |  | Step |  | Step |  | RPT  |
              | 15   |  | 16   |  | 17   |  | 18   |  | 19   |
              +------+  +------+  +------+  +------+  +------+
```

### 3.2 Artifact Dependencies

```
FIPS 199 --> FIPS 200 --> SSP
                          |
              STIG -------+--> POAM
              SBOM -------+
                          |
                          v
                      CUI Markings --> Controls --> Status Report --> Audit
```

### 3.3 Control Crosswalk

The crosswalk engine uses a dual-hub model:
- **US Hub**: NIST 800-53 Rev 5 -- domestic frameworks map directly
- **International Hub**: ISO/IEC 27001:2022 -- international frameworks map via bridge
- Implementing AC-2 satisfies FedRAMP AC-2, 800-171 3.1.1, CMMC AC.L2-3.1.1

---

## 4. Requirements

### 4.1 System Security Plan

#### REQ-04-001: SSP Completeness
The SSP generator SHALL produce a document with all 17 required sections: System Name, Categorization, Owner/AO, Description, Boundary, Architecture, Data Flow, Ports/Protocols, Interconnections, Laws/Regulations, Control Implementation, Minimum Controls, Contingency Plan, Incident Response, Configuration Management, Continuous Monitoring, Appendices.

#### REQ-04-002: SSP Data Source
The SSP SHALL be generated from live project data in the database, not from static templates with placeholder text.

#### REQ-04-003: Classification Marking
Every compliance artifact SHALL carry appropriate classification markings (CUI banner for IL4/IL5, SECRET for IL6) applied at generation time using `classification_manager.py`.

### 4.2 POAM

#### REQ-04-004: Finding Import
The POAM generator SHALL import findings from all security scanners (SAST, dependency audit, STIG, container scan) and deduplicate by CVE/CWE ID.

#### REQ-04-005: Severity Timelines
POAM entries SHALL include severity-based remediation deadlines: CAT1 (Critical) due in 30 days, CAT2 (High) due in 90 days, CAT3 (Medium/Low) due in 180 days.

#### REQ-04-006: Remediation Plans
Every POAM entry SHALL include a finding description, affected component, remediation plan, milestone dates, responsible party, and status.

### 4.3 STIG

#### REQ-04-007: Multi-Profile Support
The STIG checker SHALL support multiple profiles: OS (RHEL8), container (Docker), orchestrator (K8s), database (PostgreSQL).

#### REQ-04-008: Zero CAT1 Gate
The system SHALL enforce 0 CAT1 (Critical) STIG findings as a blocking gate for deployment.

#### REQ-04-009: DISA Format
STIG results SHALL match the DISA STIG Viewer export format for assessor compatibility.

### 4.4 SBOM

#### REQ-04-010: CycloneDX Format
The SBOM generator SHALL produce output in CycloneDX 1.5 format with all direct and transitive dependencies.

#### REQ-04-011: License Scanning
The SBOM SHALL include license information for all components, flagging GPL and unknown licenses for review.

#### REQ-04-012: Vulnerability Correlation
The SBOM SHALL cross-reference components against known vulnerability databases.

### 4.5 Control Mapping

#### REQ-04-013: 18 Control Families
The control mapper SHALL support all 18 NIST 800-53 Rev 5 control families (AC, AT, AU, CA, CM, CP, IA, IR, MA, MP, PE, PL, PM, PS, RA, SA, SC, SI).

#### REQ-04-014: Evidence Linking
No control SHALL be marked "Implemented" without linked implementation evidence (SSP sections, STIG checks, code review records, audit trail entries, scan results).

#### REQ-04-015: NIST Lookup
The system SHALL support individual control lookup via `nist_lookup.py` returning control details, baseline applicability, and implementation guidance.

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `projects` | Project metadata including FIPS categorization and ATO status |
| `nist_controls` | NIST 800-53 control implementations per project |
| `stig_findings` | STIG check results by profile (CAT1/CAT2/CAT3) |
| `security_findings` | Aggregated findings from all scanners |
| `sbom_components` | SBOM component inventory with licenses and vulnerabilities |
| `audit_trail` | Append-only compliance artifact generation log |
| `fips199_categorizations` | FIPS 199 security categorization records |
| `project_information_types` | SP 800-60 information types assigned to projects |
| `fips200_assessments` | FIPS 200 minimum security area assessments |
| `cssp_assessments` | CSSP (DI 8530.01) assessment results |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/compliance/ssp_generator.py` | Generate System Security Plan with all 17 sections |
| `tools/compliance/poam_generator.py` | Generate POAM from aggregated security findings |
| `tools/compliance/stig_checker.py` | Run STIG compliance checks against applicable profiles |
| `tools/compliance/sbom_generator.py` | Generate CycloneDX 1.5 SBOM with dependency analysis |
| `tools/compliance/cui_marker.py` | Apply CUI markings to files and directories |
| `tools/compliance/control_mapper.py` | Map NIST 800-53 controls to implementation evidence |
| `tools/compliance/nist_lookup.py` | Look up individual NIST 800-53 control details |
| `tools/compliance/compliance_status.py` | Generate consolidated compliance status report |
| `tools/compliance/classification_manager.py` | Generate classification markings by impact level |
| `tools/compliance/fips199_categorizer.py` | FIPS 199 security categorization with SP 800-60 types |
| `tools/compliance/fips200_validator.py` | FIPS 200 minimum security area validation |
| `tools/compliance/crosswalk_engine.py` | Dual-hub control crosswalk (NIST + ISO) |
| `tools/audit/audit_logger.py` | Log compliance events to append-only audit trail |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D5 | CUI markings applied at generation time (inline, not post-processing) | Prevents unmarked artifacts from existing even temporarily |
| D54 | FIPS 199 uses high watermark across SP 800-60 information types | Conservative approach required for government categorization |
| D55 | FIPS 200 validates all 17 areas against baseline from FIPS 199 | Ensures minimum security requirements met at correct impact level |
| D56 | SSP baseline selection is dynamic: query DB for categorization first | Categorization drives everything; stale baselines produce incorrect SSPs |
| D111 | Dual-hub crosswalk: NIST 800-53 (US) + ISO 27001 (international) | Implement once at either hub, cascade to all mapped frameworks |
| D112 | Framework catalogs versioned independently | Update one framework without touching others |

---

## 8. Security Gate

**Compliance Gate (Merge Gate contribution):**
- 0 CAT1 STIG findings
- SBOM current (generated within 30 days)
- CUI markings present on all compliance documents
- NIST 800-53 controls mapped with implementation evidence

**FIPS 199 Gate:**
- Categorization required for all ATO projects
- IL6 projects must have CNSSI 1253 overlay applied
- Categorization must be approved before SSP generation

**FIPS 200 Gate:**
- 0 not_satisfied requirement areas
- All 17 minimum security areas assessed
- Coverage >= 80%

---

## 9. Commands

```bash
# Security categorization (foundation step)
python tools/compliance/fips199_categorizer.py --project-id "proj-123" --categorize --json
python tools/compliance/fips200_validator.py --project-id "proj-123" --json

# Core ATO artifacts
python tools/compliance/ssp_generator.py --project-id "proj-123"
python tools/compliance/poam_generator.py --project-id "proj-123"
python tools/compliance/stig_checker.py --project-id "proj-123"
python tools/compliance/sbom_generator.py --project-dir "/path/to/project"

# CUI markings
python tools/compliance/cui_marker.py --file "/path/to/file" --marking "CUI // SP-CTI"

# NIST control mapping
python tools/compliance/control_mapper.py --activity "code.commit" --project-id "proj-123"
python tools/compliance/nist_lookup.py --control "AC-2"

# Crosswalk
python tools/compliance/crosswalk_engine.py --control AC-2
python tools/compliance/crosswalk_engine.py --project-id "proj-123" --coverage

# Classification
python tools/compliance/classification_manager.py --impact-level IL5

# Compliance skill
/icdev-comply    # Generate ATO artifacts (SSP, POAM, STIG, SBOM)
```
