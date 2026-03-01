# Phase 26 — DoD Modular Open Systems Approach (MOSA)

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 26 |
| Title | DoD Modular Open Systems Approach (MOSA) |
| Status | Implemented |
| Priority | P1 |
| Dependencies | Phase 20 (Security Categorization), Phase 23 (Universal Compliance Platform) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

10 U.S.C. Section 4401 mandates the Modular Open Systems Approach (MOSA) for all major defense acquisition programs, and DoDI 5000.87 reinforces this requirement for software-intensive systems on the Software Acquisition Pathway. Systems that fail to demonstrate modular design, open interfaces, and published standards risk acquisition milestone disapproval, vendor lock-in, and inability to integrate with future DoD enterprise services. Despite this mandate, most development teams treat MOSA as a last-minute documentation exercise rather than a continuous engineering practice, producing Interface Control Documents and Technical Standard Profiles that do not reflect the actual codebase.

Prior to this phase, ICDEV had no mechanism to assess MOSA compliance, analyze modularity metrics (coupling, cohesion, circular dependencies), auto-generate ICDs from discovered interfaces, produce TSPs from detected technology standards, or enforce modular code structure through static analysis. MOSA-relevant requirements detected during intake had no workflow to follow, and there was no way to feed architecture evidence into the cATO pipeline for continuous authorization. DoD/IC customers were manually creating MOSA artifacts disconnected from the code they described.

Phase 26 implements MOSA as a full compliance framework using the BaseAssessor pattern (D116), with 25 requirements across 6 families (Modularity, Open Interfaces, Standards Compliance, Interoperability, Reusability, Maintainability). It auto-detects MOSA applicability during RICOAS intake for all DoD/IC customers at IL4+, performs static analysis for coupling/cohesion/circular dependency metrics, auto-generates ICDs from OpenAPI/gRPC/REST endpoints, produces TSPs from the detected technology stack, enforces MOSA-compliant code structure, and optionally feeds architecture evidence into the cATO pipeline.

---

## 2. Goals

1. **Auto-detect MOSA applicability** during RICOAS intake for DoD/IC customers at IL4+, with keyword detection for MOSA-specific terminology and DoDI 5000.87 references
2. Assess **25 MOSA requirements** across 6 families (Modularity, Open Interfaces, Standards Compliance, Interoperability, Reusability, Maintainability) via the BaseAssessor pattern
3. Perform **static modularity analysis** computing coupling scores, cohesion scores (LCOM), interface coverage, circular dependency detection, and module independence ratios
4. **Auto-generate Interface Control Documents** for all external-facing interfaces discovered from OpenAPI specs, gRPC proto files, WSDL, and REST endpoints, with NIST 800-53 control mappings
5. **Auto-generate Technical Standard Profiles** from the detected technology stack, flagging proprietary or non-standard technologies for review
6. **Enforce MOSA-compliant code structure** through static analysis detecting tight coupling violations, boundary violations, missing interface specs, hardcoded dependencies, and circular imports
7. Integrate MOSA architecture evidence (SA-3, SA-8, SA-17) into the **cATO monitoring pipeline** as an optional evidence dimension
8. Store modularity metrics as **time-series data** for trend tracking and PI-over-PI improvement visualization

---

## 3. Architecture

### 3.1 MOSA Assessment Flow

```
Intake Session (RICOAS)
  |
  v
MOSA Signal Detection
  |-- DoD/IC customer + IL4+ -> MOSA REQUIRED (auto-trigger)
  |-- DoD/IC customer + IL2/IL3 -> MOSA RECOMMENDED (advisory)
  |-- Non-DoD -> MOSA NOT REQUIRED (skip)
  |
  v
MOSA Compliance Assessment (25 requirements / 6 families)
  |
  v
Modularity Analysis (coupling, cohesion, circular deps, interface coverage)
  |
  +---> ICD Generation (auto-discover from OpenAPI/gRPC/REST)
  +---> TSP Generation (auto-detect standards from tech stack)
  +---> Code Enforcement (static analysis for MOSA violations)
  |
  v
MOSA Gate Evaluation
  |-- PASS -> proceed to deployment
  |-- FAIL -> remediate blocking criteria -> re-run gate
  |
  v
cATO Evidence (optional, SA-3, SA-8, SA-17)
```

### 3.2 MOSA Requirement Families

| Family | Requirements | Focus |
|--------|-------------|-------|
| Modularity | 5 | Loose coupling, high cohesion, separation of concerns, encapsulation, composability |
| Open Interfaces | 5 | Published APIs, standard protocols, documentation, backward compatibility, versioning |
| Standards Compliance | 4 | Adherence to approved TSP standards, no proprietary lock-in, open data formats |
| Interoperability | 4 | Cross-system data exchange, standard messaging, service discovery, federation |
| Reusability | 4 | Component reuse, packaging, dependency isolation, externalized configuration |
| Maintainability | 3 | Independent deployment, hot-swap capability, technology refresh readiness |

---

## 4. Requirements

### 4.1 Detection

#### REQ-26-001: MOSA Auto-Detection
The system SHALL auto-detect MOSA applicability during RICOAS intake when the customer organization is DoD/IC and impact level is IL4+, triggering the MOSA workflow automatically.

#### REQ-26-002: Keyword Detection
The system SHALL detect MOSA-specific signals including terminology ("modular," "open architecture," "MOSA," "interoperability"), DoDI 5000.87 or 10 U.S.C. 4401 references, and existing ICD/TSP document references.

### 4.2 Assessment

#### REQ-26-003: 25-Requirement Assessment
The system SHALL assess 25 MOSA requirements organized across 6 families with per-requirement status (satisfied, partial, not_satisfied, not_assessed).

#### REQ-26-004: Modularity Metrics
The system SHALL compute modularity metrics via static analysis: afferent/efferent coupling per module, LCOM cohesion score, interface coverage percentage, circular dependency detection, and module independence ratio.

#### REQ-26-005: Time-Series Metrics
The system SHALL store modularity metrics as time-series data in the `mosa_modularity_metrics` table for trend tracking across PIs.

### 4.3 Artifact Generation

#### REQ-26-006: ICD Auto-Generation
The system SHALL auto-discover external-facing interfaces from OpenAPI/Swagger specs, gRPC proto files, WSDL, and REST endpoints, generating an ICD per interface with protocol, data format, authentication, versioning, SLA, error handling, and NIST control mappings (SC-7, SC-8, SA-9).

#### REQ-26-007: TSP Auto-Generation
The system SHALL auto-detect standards from the technology stack and generate a Technical Standard Profile documenting all communication protocols, data formats, authentication methods, encryption standards, and API specifications, flagging proprietary or non-standard technologies.

#### REQ-26-008: Code Enforcement
The system SHALL scan the codebase for MOSA violations including tight coupling (direct cross-module imports bypassing interfaces), boundary violations, missing interface specs, hardcoded dependencies, and circular imports, generating fix suggestions for each violation.

### 4.4 Integration

#### REQ-26-009: cATO Evidence (Optional)
When `mosa_config.yaml` has `cato_integration.enabled: true`, the system SHALL collect MOSA architecture evidence for continuous authorization covering SA-3 (SDLC), SA-8 (Security Engineering Principles), and SA-17 (Architecture and Design).

#### REQ-26-010: Non-DoD Advisory Mode
For non-DoD projects, MOSA SHALL be available on-demand via `/icdev-mosa` in advisory mode (gate does not block deployment).

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `mosa_assessments` | MOSA compliance assessment results (25 requirements, 6 families) |
| `icd_documents` | Generated Interface Control Documents per interface |
| `tsp_documents` | Generated Technical Standard Profiles |
| `mosa_modularity_metrics` | Time-series modularity metrics (coupling, cohesion, deps, coverage) |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/compliance/mosa_assessor.py` | MOSA assessment (25 reqs / 6 families) and gate evaluation |
| `tools/mosa/modular_design_analyzer.py` | Static modularity analysis (coupling, cohesion, circular deps) |
| `tools/mosa/icd_generator.py` | Auto-generate ICDs from discovered interfaces |
| `tools/mosa/tsp_generator.py` | Auto-generate TSP from detected technology stack |
| `tools/mosa/mosa_code_enforcer.py` | Static analysis for MOSA code violations with fix suggestions |
| `tools/compliance/cato_monitor.py` | cATO evidence integration (extended for MOSA) |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D125 | MOSA auto-triggers for all DoD/IC projects at IL4+ | 10 U.S.C. 4401 mandate; no opt-out for applicable programs |
| D126 | Software development principles only (no FACE/VICTORY/SOSA hardware profiles) | Hardware MOSA is out of scope for a software-focused platform |
| D127 | Full compliance framework via BaseAssessor pattern (D116) | Crosswalk integration, gate evaluation, CLI for ~60 LOC per framework |
| D128 | ICD and TSP as generated artifacts (auto-discovered from code) | Documents reflect actual codebase, not manually authored assumptions |
| D129 | Static analysis for enforcement using Python ast, import graph, regex (D13) | Air-gap safe, zero external dependencies, deterministic |
| D130 | cATO evidence is optional (config flag) | Not all projects use cATO; evidence collection should not be forced |
| D131 | Modularity metrics stored as time-series | Enables trend tracking and PI-over-PI improvement visualization |

---

## 8. Security Gate

**MOSA Gate:**
- 0 external interfaces without an ICD (blocking)
- 0 circular module dependencies (blocking)
- Modularity score >= 0.6 (blocking)
- 0 direct coupling violations (blocking at > 5)
- Interface coverage >= 80% (warning)
- TSP generated and current (blocking)
- 0 proprietary standards without documented justification (warning)

---

## 9. Commands

```bash
# MOSA assessment
python tools/compliance/mosa_assessor.py --project-id "proj-123" --json
python tools/compliance/mosa_assessor.py --project-id "proj-123" --gate

# Modularity analysis
python tools/mosa/modular_design_analyzer.py --project-dir /path \
  --project-id "proj-123" --store --json

# ICD generation
python tools/mosa/icd_generator.py --project-id "proj-123" --all --json
python tools/mosa/icd_generator.py --project-id "proj-123" \
  --interface-id "iface-1" --json

# TSP generation
python tools/mosa/tsp_generator.py --project-id "proj-123" --json

# Code enforcement
python tools/mosa/mosa_code_enforcer.py --project-dir /path \
  --fix-suggestions --json

# cATO MOSA evidence
python tools/compliance/cato_monitor.py --project-id "proj-123" --mosa-evidence
```
