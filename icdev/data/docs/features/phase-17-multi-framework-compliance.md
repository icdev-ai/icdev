# Phase 17 — Multi-Framework Compliance

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 17 |
| Title | Multi-Framework Compliance |
| Status | Implemented |
| Priority | P0 |
| Dependencies | Phase 11 (Compliance Workflow), Phase 16 (ATO Acceleration) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

Government and defense applications must comply with multiple overlapping compliance frameworks simultaneously. A single DoD system may need to satisfy NIST 800-53, FedRAMP High, CMMC Level 2, NIST 800-171, and DoD CSSP requirements -- all of which share significant control overlap but use different taxonomies, identifiers, and assessment criteria. Without a unified compliance engine, organizations implement the same security control multiple times under different names, waste assessment resources, and produce inconsistent compliance documentation.

The international dimension compounds this challenge. Organizations operating globally must also satisfy ISO/IEC 27001:2022, which uses an entirely different control structure than the NIST-based US frameworks. Manually mapping between US and international frameworks is error-prone and rarely maintained as frameworks evolve independently.

ICDEV needs a dual-hub crosswalk engine that uses NIST 800-53 Rev 5 as the US hub and ISO/IEC 27001:2022 as the international hub, connected by a bidirectional bridge. Implementing a control at either hub should automatically cascade satisfaction to all mapped frameworks, eliminating redundant work and ensuring consistent compliance posture across all regimes.

---

## 2. Goals

1. Implement a dual-hub crosswalk engine with NIST 800-53 as the US hub and ISO 27001 as the international hub, connected by a bidirectional bridge
2. Enable single-control implementation to cascade across all mapped frameworks (e.g., implementing AC-2 satisfies FedRAMP AC-2, 800-171 3.1.1, CMMC AC.L2-3.1.1)
3. Provide FedRAMP assessment and report generation for both Moderate and High baselines
4. Provide CMMC assessment and report generation for Level 2 and Level 3
5. Support classification-aware markings (CUI for IL4/IL5, SECRET for IL6) via the classification manager
6. Generate OSCAL machine-readable artifacts for automated compliance tooling
7. Integrate with eMASS and Xacta 360 for system of record synchronization
8. Enable cATO continuous monitoring with evidence freshness tracking and automated scheduling

---

## 3. Architecture

```
                    +---------------------------+
                    |    ISO/IEC 27001:2022     |
                    |   (International Hub)      |
                    +-------------+-------------+
                                  |
                    +-------------v-------------+
                    | iso27001_nist_bridge.json  |
                    | (Bidirectional Bridge)     |
                    +-------------+-------------+
                                  |
+------------------+  +-----------v-----------+  +------------------+
| FedRAMP          |  |   NIST 800-53 Rev 5   |  | CMMC             |
| (Mod/High)       |<-|     (US Hub)           |->| (Level 2/3)      |
+------------------+  +-----------+-----------+  +------------------+
                                  |
              +-------------------+-------------------+
              |                   |                   |
     +--------v------+  +--------v------+  +---------v-----+
     | NIST 800-171  |  | DoD CSSP      |  | Other US      |
     | (3.x.x)       |  | (DI 8530.01)  |  | Frameworks    |
     +---------------+  +---------------+  +---------------+
```

The crosswalk engine uses a dual-hub model (ADR D111):

- **US Hub**: NIST 800-53 Rev 5 -- all domestic frameworks (FedRAMP, CMMC, 800-171, CSSP, CJIS, HIPAA, etc.) map directly to NIST controls
- **International Hub**: ISO/IEC 27001:2022 -- international frameworks map via the ISO hub
- **Bridge**: `iso27001_nist_bridge.json` connects the two hubs bidirectionally

When a NIST 800-53 control is implemented, the crosswalk engine automatically marks the corresponding FedRAMP control, CMMC practice, 800-171 requirement, and any other mapped framework requirement as satisfied. This eliminates redundant assessment work and ensures consistency.

---

## 4. Requirements

### 4.1 Crosswalk Engine

#### REQ-17-001: Dual-Hub Crosswalk
The system SHALL implement a dual-hub crosswalk model with NIST 800-53 Rev 5 as the US hub and ISO/IEC 27001:2022 as the international hub, connected by `iso27001_nist_bridge.json`.

#### REQ-17-002: Cascade Satisfaction
When a NIST 800-53 control is marked as satisfied, the system SHALL automatically cascade that status to all mapped framework controls (FedRAMP, CMMC, 800-171, CSSP, and any additional mapped frameworks).

#### REQ-17-003: Coverage Computation
The system SHALL compute coverage percentage per framework showing satisfied, partially satisfied, and not satisfied controls.

#### REQ-17-004: Gap Analysis
The system SHALL identify gaps per target framework, showing which NIST 800-53 controls need implementation to achieve full coverage.

### 4.2 Framework Assessments

#### REQ-17-005: FedRAMP Assessor
The system SHALL assess compliance against FedRAMP Moderate and High baselines using `fedramp_moderate_baseline.json` and `fedramp_high_baseline.json` catalogs with per-control status tracking.

#### REQ-17-006: CMMC Assessor
The system SHALL assess compliance against CMMC Level 2 and Level 3 practices using `cmmc_practices.json` with per-practice status and evidence tracking.

#### REQ-17-007: Framework Report Generation
The system SHALL generate human-readable markdown assessment reports for each framework with CUI markings, finding details, and remediation guidance.

### 4.3 Classification and Markings

#### REQ-17-008: Classification Manager
The system SHALL apply classification markings appropriate to impact level: CUI // SP-CTI for IL4/IL5, SECRET for IL6, via `classification_manager.py`.

#### REQ-17-009: Dynamic Marking Application
Classification markings SHALL be applied at artifact generation time (inline, not post-processing) per ADR D5.

### 4.4 Artifact and Integration

#### REQ-17-010: OSCAL Generation
The system SHALL generate OSCAL-compliant artifacts (SSP, POA&M, Assessment Results) in JSON format per the NIST OSCAL specification.

#### REQ-17-011: eMASS Integration
The system SHALL synchronize controls, POA&M items, and artifacts with eMASS in hybrid mode (API when available) or export mode (file-based for air-gapped).

#### REQ-17-012: cATO Monitoring
The system SHALL continuously monitor evidence freshness for all critical controls and alert when evidence exceeds configured staleness thresholds.

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `fedramp_assessments` | FedRAMP assessment results per control: status, evidence, baseline |
| `cmmc_assessments` | CMMC practice assessment results: status, evidence, level |
| `oscal_artifacts` | OSCAL document storage: artifact_type, content_json, version, generated_at |
| `emass_sync_log` | eMASS sync records: direction, items_synced, status, errors, timestamp |
| `xacta_sync_log` | Xacta sync records: direction, items_synced, status, timestamp |
| `cato_evidence` | Continuous monitoring evidence: control_id, evidence_type, collected_at, expires_at |
| `cato_schedules` | Evidence collection schedules: control_id, frequency, last_run, next_due |
| `pi_compliance_tracking` | PI-level compliance velocity: pi_id, controls_implemented, score |
| `crosswalk_bridges` | Framework-to-framework control mappings for the dual-hub model |
| `framework_catalog_versions` | Independent versioning per framework catalog |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/compliance/crosswalk_engine.py` | Dual-hub crosswalk: query, gap analysis, coverage, cascade |
| `tools/compliance/classification_manager.py` | Impact-level-aware CUI/SECRET marking generation |
| `tools/compliance/fedramp_assessor.py` | FedRAMP Moderate/High baseline assessment |
| `tools/compliance/fedramp_report_generator.py` | FedRAMP human-readable report generation |
| `tools/compliance/cmmc_assessor.py` | CMMC Level 2/3 practice assessment |
| `tools/compliance/cmmc_report_generator.py` | CMMC human-readable report generation |
| `tools/compliance/oscal_generator.py` | OSCAL SSP, POA&M, and Assessment Results generation |
| `tools/compliance/emass/emass_sync.py` | Bidirectional eMASS synchronization |
| `tools/compliance/emass/emass_export.py` | File-based eMASS export for air-gapped environments |
| `tools/compliance/xacta/xacta_sync.py` | Bidirectional Xacta 360 synchronization |
| `tools/compliance/cato_monitor.py` | cATO evidence freshness and readiness monitoring |
| `tools/compliance/cato_scheduler.py` | Automated evidence collection scheduling |
| `tools/compliance/pi_compliance_tracker.py` | PI-level compliance velocity tracking |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D111 | Dual-hub crosswalk: NIST 800-53 (US) + ISO 27001 (international) + bridge | Implement once at either hub, cascade everywhere; eliminates redundant assessment work |
| D112 | Framework catalogs versioned independently | Update one framework JSON catalog without touching others |
| D113 | Multi-regime deduplication via crosswalk | N frameworks produce 1 unified NIST control set, not N separate assessments |
| D5 | CUI markings applied at generation time (inline) | Markings are never post-processed; classification is part of artifact creation |
| D56 | SSP baseline selection is dynamic | Query DB for FIPS 199 categorization, fall back to IL mapping |

---

## 8. Security Gate

**FedRAMP Gate:**
- 0 other_than_satisfied on high-priority controls
- Encryption FIPS 140-2 required

**CMMC Gate:**
- 0 not_met Level 2 practices
- Evidence current within 90 days

**cATO Gate:**
- 0 expired evidence on critical controls
- Readiness >= 50%

---

## 9. Commands

```bash
# Crosswalk queries
python tools/compliance/crosswalk_engine.py --control AC-2
python tools/compliance/crosswalk_engine.py --project-id "proj-123" --coverage
python tools/compliance/crosswalk_engine.py --project-id "proj-123" --target fedramp-moderate --gap-analysis

# Classification markings
python tools/compliance/classification_manager.py --impact-level IL5

# FedRAMP
python tools/compliance/fedramp_assessor.py --project-id "proj-123" --baseline moderate
python tools/compliance/fedramp_report_generator.py --project-id "proj-123"

# CMMC
python tools/compliance/cmmc_assessor.py --project-id "proj-123" --level 2
python tools/compliance/cmmc_report_generator.py --project-id "proj-123"

# OSCAL generation
python tools/compliance/oscal_generator.py --project-id "proj-123" --artifact ssp

# eMASS integration
python tools/compliance/emass/emass_sync.py --project-id "proj-123" --mode hybrid
python tools/compliance/emass/emass_export.py --project-id "proj-123" --type controls

# cATO monitoring
python tools/compliance/cato_monitor.py --project-id "proj-123" --check-freshness
python tools/compliance/cato_scheduler.py --project-id "proj-123" --run-due

# PI tracking
python tools/compliance/pi_compliance_tracker.py --project-id "proj-123" --velocity
```
