# Phase 23 — Universal Compliance Platform

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 23 |
| Title | Universal Compliance Platform |
| Status | Implemented |
| Priority | P1 |
| Dependencies | Phase 17 (ATO Acceleration), Phase 20 (Security Categorization) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

Government and regulated-industry software systems rarely operate under a single compliance framework. A DoD healthcare application must simultaneously satisfy NIST 800-53, FedRAMP, CMMC, HIPAA, and HITRUST. A law enforcement system requires CJIS on top of NIST 800-53 and FIPS 199/200. A financial services government contractor needs PCI DSS, SOC 2, ISO 27001, and FedRAMP. Assessing each framework independently produces redundant work: AC-2 (Account Management) in NIST 800-53 maps to nearly identical requirements in FedRAMP, CMMC, HIPAA, SOC 2, and ISO 27001. Without crosswalk deduplication, organizations implement the same control multiple times under different names.

Prior phases built individual framework assessors (NIST 800-53, FedRAMP, CMMC), but each operated in isolation. There was no mechanism to detect which frameworks apply based on the data a system processes, no composable data markings (a file containing both CUI and PHI needed two separate marking systems), and no multi-regime assessment that deduplicates effort through a crosswalk engine. The result was manual compliance mapping, missed framework applicability, and inconsistent assessment results across frameworks.

The Universal Compliance Platform introduces a dual-hub crosswalk model (NIST 800-53 as the US hub, ISO 27001 as the international hub, connected by a bidirectional bridge), composable data classification markings supporting 10 data categories, automatic framework detection based on data types, and a BaseAssessor abstract pattern that reduces new framework implementation to approximately 60 lines of code. Six Wave 1 frameworks (CJIS, HIPAA, HITRUST, SOC 2, PCI DSS, ISO 27001) join the existing NIST 800-53, FedRAMP, and CMMC assessors to deliver unified multi-regime compliance from a single assessment run.

---

## 2. Goals

1. Implement **composable data classification markings** supporting 10 data categories (CUI, PHI, PCI, CJIS, FTI, FERPA, Export Controlled, PII, Proprietary, Open Source) that can be combined on a single artifact
2. Build a **dual-hub crosswalk engine** with NIST 800-53 as the US hub and ISO 27001 as the international hub, connected by a bidirectional bridge, so implementing a control at either hub cascades to all mapped frameworks
3. Deliver **automatic framework detection** that recommends applicable compliance frameworks based on data types processed by the system, with advisory-only mode requiring ISSO confirmation (D110)
4. Implement 6 Wave 1 framework assessors via the **BaseAssessor ABC pattern** (D116): CJIS, HIPAA, HITRUST, SOC 2, PCI DSS, ISO 27001
5. Provide **multi-regime assessment with deduplication** that assesses N frameworks through a single unified NIST control set rather than N separate assessments
6. Maintain **independently versioned framework catalogs** so updating one framework does not require changes to others
7. Support a **multi-regime gate** that passes only when all applicable frameworks pass their individual gates
8. Enable framework catalogs as a **marketplace asset type** for community contribution via Phase 22

---

## 3. Architecture

### 3.1 Dual-Hub Crosswalk Model (D111)

```
                    US Hub                          International Hub
              +----------------+                +-------------------+
              | NIST 800-53    |<-- bridge -->  | ISO/IEC 27001     |
              | Rev 5          |   (D111)       | :2022             |
              +-------+--------+                +--------+----------+
                      |                                  |
        +-------------+-------------+          +---------+---------+
        |             |             |          |         |         |
   +---------+  +---------+  +---------+  +--------+ +--------+ +--------+
   |FedRAMP  |  | CMMC    |  | CJIS    |  |HITRUST | |SOC 2   | |PCI DSS |
   |Moderate |  | L2/L3   |  |Security |  |CSF v11 | |Type II | |v4.0    |
   +---------+  +---------+  |Policy   |  +--------+ +--------+ +--------+
                              +---------+
                              |HIPAA    |
                              |Security |
                              +---------+
```

### 3.2 Composable Data Classification

A single artifact can carry multiple data classification markings:
```
CUI // SP-CTI // HIPAA-PHI // PCI-CARDHOLDER
```
The highest-sensitivity category determines handling requirements. Classification manager generates composite banners and code headers automatically.

### 3.3 BaseAssessor Pattern (D116)

All assessors inherit from a common abstract base class providing:
- Crosswalk integration (automatic cascade to mapped frameworks)
- Gate evaluation (pass/fail with blocking conditions)
- CLI interface (--json, --human, --gate flags)
- Audit trail logging

New framework implementation requires approximately 60 lines of code versus 400+ lines without the pattern.

---

## 4. Requirements

### 4.1 Data Classification

#### REQ-23-001: Composable Markings
The system SHALL support composable data classification markings where a single artifact can carry CUI + PHI + PCI markings simultaneously, with the highest-sensitivity category determining handling.

#### REQ-23-002: 10 Data Categories
The system SHALL support 10 data categories: CUI, PHI, PCI, CJIS, FTI, FERPA, Export Controlled, PII, Proprietary, and Open Source.

#### REQ-23-003: Composite Banners
The system SHALL generate composite banners and code headers combining all applicable data categories for a given artifact.

### 4.2 Framework Detection

#### REQ-23-004: Auto-Detection
The system SHALL automatically detect applicable compliance frameworks based on data types assigned to the project (e.g., PHI triggers HIPAA, PCI triggers PCI DSS).

#### REQ-23-005: Advisory-Only Detection (D110)
Compliance auto-detection SHALL be advisory only. The system recommends frameworks; the customer ISSO must confirm before gates enforce.

#### REQ-23-006: Data Type to Framework Mapping
The system SHALL use a declarative JSON mapping (`data_type_framework_map.json`) to drive all auto-detection rules, enabling new rules without code changes.

### 4.3 Crosswalk Engine

#### REQ-23-007: Dual-Hub Model
The crosswalk engine SHALL use NIST 800-53 as the US hub and ISO 27001 as the international hub, with a bidirectional bridge connecting both.

#### REQ-23-008: Cascade Implementation
Implementing a control at either hub SHALL automatically cascade compliance status to all mapped frameworks via the crosswalk engine.

#### REQ-23-009: Multi-Regime Deduplication
Assessing N frameworks SHALL produce 1 unified NIST control set, not N separate assessments, eliminating redundant assessment work.

### 4.4 Assessment

#### REQ-23-010: Wave 1 Assessors
The system SHALL implement assessors for 6 Wave 1 frameworks: CJIS Security Policy, HIPAA Security Rule, HITRUST CSF v11, SOC 2 Type II, PCI DSS v4.0, and ISO/IEC 27001:2022.

#### REQ-23-011: BaseAssessor Pattern
All assessors SHALL inherit from the BaseAssessor ABC (D116) providing crosswalk integration, gate evaluation, and CLI interface.

#### REQ-23-012: Independent Catalog Versioning
Each framework catalog SHALL be versioned independently, enabling updates to one framework without affecting others.

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `data_classifications` | Per-project data category assignments |
| `framework_applicability` | Which frameworks apply to which projects (detected + confirmed) |
| `compliance_detection_log` | Auto-detection audit trail |
| `crosswalk_bridges` | Inter-framework control mappings |
| `framework_catalog_versions` | Independent version tracking per framework |
| `cjis_assessments` | CJIS Security Policy assessment results |
| `hipaa_assessments` | HIPAA Security Rule assessment results |
| `hitrust_assessments` | HITRUST CSF v11 assessment results |
| `soc2_assessments` | SOC 2 Type II assessment results |
| `pci_dss_assessments` | PCI DSS v4.0 assessment results |
| `iso27001_assessments` | ISO/IEC 27001:2022 assessment results |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/compliance/universal_classification_manager.py` | Composable data markings, banners, code headers, auto-detect |
| `tools/compliance/compliance_detector.py` | Auto-detect applicable frameworks from data types |
| `tools/compliance/multi_regime_assessor.py` | Multi-framework assessment with crosswalk deduplication |
| `tools/compliance/crosswalk_engine.py` | Dual-hub crosswalk queries and coverage analysis |
| `tools/compliance/cjis_assessor.py` | CJIS Security Policy assessor |
| `tools/compliance/hipaa_assessor.py` | HIPAA Security Rule assessor |
| `tools/compliance/hitrust_assessor.py` | HITRUST CSF v11 assessor |
| `tools/compliance/soc2_assessor.py` | SOC 2 Type II assessor |
| `tools/compliance/pci_dss_assessor.py` | PCI DSS v4.0 assessor |
| `tools/compliance/iso27001_assessor.py` | ISO/IEC 27001:2022 assessor |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D109 | Composable data markings: single artifact can carry CUI + PHI + PCI simultaneously | Real-world data often has overlapping classification requirements |
| D110 | Compliance auto-detection is advisory only; ISSO must confirm before gates enforce | Prevents incorrect framework enforcement from automated heuristics |
| D111 | Dual-hub crosswalk: NIST 800-53 (US) + ISO 27001 (international) with bidirectional bridge | Implement once at either hub, cascade everywhere; covers both domestic and international frameworks |
| D112 | Framework catalogs versioned independently | Update one framework without touching others |
| D113 | Multi-regime deduplication via crosswalk: N frameworks produce 1 unified NIST control set | Eliminates redundant assessment of identical controls under different names |
| D114 | Compliance framework as marketplace asset type | Community-contributed framework catalogs can be shared via Phase 22 |
| D115 | Data type to framework mapping is declarative JSON | Add new detection rules without code changes |
| D116 | BaseAssessor ABC pattern: all assessors inherit from base class with crosswalk, gate, CLI | ~60 LOC per new framework versus ~400+ LOC without the pattern |

---

## 8. Security Gate

**Multi-Regime Gate:**
- All applicable frameworks must pass their individual gates
- Overall pass requires 0 framework failures across all detected regimes

**HIPAA Gate:**
- 0 not_satisfied on Administrative/Technical Safeguards
- Encryption FIPS 140-2 required for PHI

**PCI DSS Gate:**
- 0 not_satisfied on Requirements 3-4 (data protection), 6 (secure development), 10 (logging)

**CJIS Gate:**
- 0 not_satisfied on Policy Areas 4 (audit), 5 (access control), 6 (identification), 10 (encryption)

---

## 9. Commands

```bash
# Data classification management
python tools/compliance/universal_classification_manager.py --list-categories
python tools/compliance/universal_classification_manager.py --banner CUI PHI --json
python tools/compliance/universal_classification_manager.py --code-header CUI PCI --language python
python tools/compliance/universal_classification_manager.py --detect --project-id "proj-123" --json
python tools/compliance/universal_classification_manager.py --add-category \
  --project-id "proj-123" --category PHI
python tools/compliance/universal_classification_manager.py --validate \
  --project-id "proj-123" --json

# Framework detection
python tools/compliance/compliance_detector.py --project-id "proj-123" --json
python tools/compliance/compliance_detector.py --project-id "proj-123" --apply --json
python tools/compliance/compliance_detector.py --project-id "proj-123" --confirm --json

# Multi-regime assessment
python tools/compliance/multi_regime_assessor.py --project-id "proj-123" --json
python tools/compliance/multi_regime_assessor.py --project-id "proj-123" --gate
python tools/compliance/multi_regime_assessor.py --project-id "proj-123" \
  --minimal-controls --json

# Individual framework assessments
python tools/compliance/cjis_assessor.py --project-id "proj-123" --json
python tools/compliance/hipaa_assessor.py --project-id "proj-123" --json
python tools/compliance/hitrust_assessor.py --project-id "proj-123" --json
python tools/compliance/soc2_assessor.py --project-id "proj-123" --json
python tools/compliance/pci_dss_assessor.py --project-id "proj-123" --json
python tools/compliance/iso27001_assessor.py --project-id "proj-123" --json

# Crosswalk queries
python tools/compliance/crosswalk_engine.py --control AC-2
python tools/compliance/crosswalk_engine.py --project-id "proj-123" --coverage
python tools/compliance/crosswalk_engine.py --project-id "proj-123" \
  --target fedramp-moderate --gap-analysis
```
