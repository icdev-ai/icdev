# Phase 18 — MBSE Integration

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 18 |
| Title | MBSE Integration |
| Status | Implemented |
| Priority | P0 |
| Dependencies | Phase 11 (Compliance Workflow), Phase 12 (Build App / ATLAS Workflow) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

DoDI 5000.87 mandates digital engineering for all DoD acquisition programs. Without model-code traceability, programs fail audits and cannot demonstrate that delivered code implements the authoritative design. The digital thread -- the end-to-end chain from requirements through models to code, tests, and compliance controls -- is the foundation of Digital Engineering Strategy (DES) compliance and Milestone B/C review readiness.

Currently, SysML models authored in tools like Cameo Systems Modeler and requirements managed in IBM DOORS NG exist in isolation from the code that implements them. There is no automated mechanism to import model elements into the development environment, generate code scaffolding from model definitions, map model elements to NIST 800-53 security controls, detect drift between model and code, or capture point-in-time snapshots for SAFe PI boundaries.

ICDEV needs an MBSE integration layer that extends the ATLAS workflow with a Model pre-phase (M-ATLAS), establishing bidirectional traceability from DOORS requirements through SysML models to generated code, test cases, and NIST compliance controls, with continuous drift detection and DES compliance assessment.

---

## 2. Goals

1. Import SysML v1.6 models from Cameo Systems Modeler via XMI 2.5.1 format into the ICDEV database for downstream traceability and code generation
2. Import requirements from IBM DOORS NG via ReqIF 1.2 format with diff-against-previous capability
3. Build an end-to-end digital thread: Requirement -> Model Element -> Code Module -> Test Case -> NIST Control
4. Generate code scaffolding from SysML block definitions, activities, state machines, and interfaces with traceability comments and CUI markings
5. Map model elements to NIST 800-53 security controls via keyword-based and type-based matching rules
6. Detect model-code drift continuously with bidirectional sync capabilities (model-to-code and code-to-model)
7. Assess DES compliance against the 5 goals of DoDI 5000.87 for Milestone readiness
8. Capture PI model snapshots with PI-over-PI comparison for trend tracking and audit

---

## 3. Architecture

```
+-------------------+     +-------------------+     +-------------------+
|  Cameo XMI 2.5.1  |---->|  XMI Parser       |---->|  sysml_elements   |
|  (SysML Model)    |     |                   |     |  sysml_relations  |
+-------------------+     +-------------------+     +-------------------+
                                                            |
+-------------------+     +-------------------+             |
|  DOORS NG ReqIF   |---->|  ReqIF Parser     |---->doors_requirements
|  (Requirements)   |     |                   |             |
+-------------------+     +-------------------+             |
                                                            v
                                                    +-------------------+
                                                    |  Digital Thread   |
                                                    |  (auto-link)      |
                                                    +-------------------+
                                                            |
                          +----------------+----------------+----------------+
                          |                |                |                |
                          v                v                v                v
                   +-----------+   +-----------+   +-----------+   +-----------+
                   | Code Gen  |   | Control   |   | Drift     |   | DES       |
                   | (classes, |   | Mapper    |   | Detector  |   | Assessor  |
                   |  stubs)   |   | (NIST)    |   | (sync)    |   | (5000.87) |
                   +-----------+   +-----------+   +-----------+   +-----------+
                                                                        |
                                                                        v
                                                                 +-----------+
                                                                 | PI Model  |
                                                                 | Tracker   |
                                                                 +-----------+
```

M-ATLAS extends the standard ATLAS workflow by adding a Model pre-phase. If no model exists, the system gracefully falls back to standard ATLAS. The pipeline processes:

- **M (Model)** -- Import XMI/ReqIF, build digital thread, generate code scaffolding
- **A (Architect)** -- System design informed by model elements (blocks, interfaces, behaviors)
- **T (Trace)** -- Data schema, integrations, stack augmented with model traceability
- **L (Link)** -- Validate model-to-code and requirement-to-test mappings
- **A (Assemble)** -- Build with model-generated scaffolding as starting point
- **S (Stress-test)** -- Test including model-generated stubs and traceability verification

---

## 4. Requirements

### 4.1 Model Import

#### REQ-18-001: SysML XMI Import
The system SHALL parse SysML v1.6 models exported as XMI 2.5.1 from Cameo Systems Modeler, extracting: Block Definition Diagrams, Activity Diagrams, Requirement Diagrams, State Machine Diagrams, Use Case Diagrams, Internal Block Diagrams, and Parametric Diagrams.

#### REQ-18-002: ReqIF Import
The system SHALL parse requirements exported as ReqIF 1.2 from IBM DOORS NG, extracting SPEC-OBJECTs, SPEC-RELATIONs, ATTRIBUTE-DEFINITIONs, and SPEC-HIERARCHY with configurable field mappings.

#### REQ-18-003: Import Diff
The system SHALL support diff-against-previous import to show new, changed, and deleted elements since the last import.

### 4.2 Digital Thread

#### REQ-18-004: End-to-End Traceability
The system SHALL establish a digital thread: Requirement (ReqIF) -> Model Element (XMI) -> Code Module (generated) -> Test Case (TDD) -> NIST Control (800-53).

#### REQ-18-005: Auto-Link
The system SHALL automatically link requirements to model elements using name/ID matching and NLP similarity, with a configurable confidence threshold (default 0.6, below which items are queued for manual review).

#### REQ-18-006: Coverage Reporting
The system SHALL report digital thread coverage percentages: requirements with model links, model elements with code links, code modules with test links, and end-to-end traced chains.

### 4.3 Code Generation

#### REQ-18-007: Model-Driven Code Generation
The system SHALL generate code scaffolding from SysML elements: Blocks become classes, Activities become functions, State Machines become state pattern classes, Flow Ports become interfaces, and Constraint Blocks become validation functions.

#### REQ-18-008: Traceability Comments
All generated code SHALL include traceability comments (`# GENERATED FROM:`, `# TRACES TO:`) and CUI // SP-CTI markings.

### 4.4 Control Mapping and Compliance

#### REQ-18-009: NIST Control Mapping
The system SHALL map model elements to NIST 800-53 controls using keyword-based and type-based matching rules with confidence scoring.

#### REQ-18-010: DES Assessment
The system SHALL assess compliance against the 5 goals of DoDI 5000.87 Digital Engineering Strategy and produce a DES compliance score with per-goal status.

### 4.5 Drift Detection and Sync

#### REQ-18-011: Drift Detection
The system SHALL detect drift between model definitions and generated code by comparing class names, method signatures, properties, state transitions, and interface implementations.

#### REQ-18-012: Bidirectional Sync
The system SHALL support model-to-code and code-to-model sync directions with conflict detection for cases where both model and code have changed.

### 4.6 PI Snapshots

#### REQ-18-013: PI Model Snapshot
The system SHALL capture point-in-time snapshots of model elements, requirements, digital thread coverage, drift status, NIST mapping, DES score, and SBOM at SAFe PI boundaries.

#### REQ-18-014: PI Comparison
The system SHALL support comparing two PI snapshots to show element deltas, coverage improvements, drift resolution progress, and compliance velocity.

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `sysml_elements` | Imported model elements: element_id, type (block/activity/requirement/state_machine/use_case/constraint_block), name, properties_json |
| `sysml_relationships` | Model relationships: source_id, target_id, type (connector/dependency/association), properties_json |
| `doors_requirements` | Imported requirements: req_id, text, type, priority, status, parent_id |
| `doors_req_links` | Requirement links: source_id, target_id, link_type (parent-child/derives/satisfies) |
| `model_imports` | Import metadata: import_id, file_path, file_hash, element_count, timestamp |
| `digital_thread_links` | Traceability links: source_type, source_id, target_type, target_id, link_type, confidence |
| `model_control_mappings` | Model-to-NIST mappings: element_id, control_id, confidence, mapping_rule |
| `pi_snapshots` | PI snapshots: pi_id, snapshot_data_json, coverage_pct, drift_count, des_score, timestamp |
| `des_assessments` | DES compliance: project_id, goal_scores_json, overall_score, milestone_readiness |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/mbse/xmi_parser.py` | Parse SysML XMI 2.5.1 into sysml_elements and sysml_relationships |
| `tools/mbse/reqif_parser.py` | Parse DOORS NG ReqIF 1.2 into doors_requirements and doors_req_links |
| `tools/mbse/digital_thread.py` | Auto-link, coverage, report, manual link for end-to-end traceability |
| `tools/mbse/model_code_generator.py` | Generate code scaffolding from model elements with traceability |
| `tools/mbse/model_control_mapper.py` | Map model elements to NIST 800-53 controls |
| `tools/mbse/sync_engine.py` | Detect drift and sync model-to-code or code-to-model |
| `tools/mbse/des_assessor.py` | Assess DoDI 5000.87 DES compliance (5 goals) |
| `tools/mbse/des_report_generator.py` | Generate DES compliance report |
| `tools/mbse/pi_model_tracker.py` | Create and compare PI model snapshots |
| `tools/mbse/diagram_extractor.py` | Extract SysML elements from screenshot images via vision LLM |
| `tools/mcp/mbse_server.py` | MCP server: import_xmi, import_reqif, trace_forward, trace_backward, generate_code, detect_drift, sync_model, des_assess, thread_coverage, model_snapshot |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D7 | Python stdlib `xml.etree.ElementTree` for XMI/ReqIF parsing | Zero external dependencies, air-gap safe |
| D8 | Normalized DB tables for model elements | Enables SQL joins across the digital thread |
| D9 | M-ATLAS adds "Model" pre-phase to ATLAS | Backward compatible -- skips if no model exists |
| D10 | File-based sync only for Cameo | Air-gapped desktop, no API -- XMI export/import |
| D11 | PI-snapshot versioning with SHA-256 content hashing | Drift detection between PI boundaries |
| D12 | N:M digital thread links | One block can map to many code modules; one control to many requirements |
| D86 | Vision diagram extraction is advisory-only | Requires `--store` flag to write to DB; human review gate before model contamination |

---

## 8. Security Gate

**DES Gate:**
- 0 non_compliant on critical DoDI 5000.87 Digital Engineering requirements
- DES score < 70% flags project for program management review

**Drift Gate:**
- Drift > 20% blocks deployment -- model and code must be reconciled

**Traceability Gate:**
- No code merged without at least one requirement link in the digital thread

**CUI Gate:**
- All generated reports and code files must carry CUI // SP-CTI markings

---

## 9. Commands

```bash
# Import SysML model
python tools/mbse/xmi_parser.py --project-id "proj-123" --file /path/to/model.xmi --json

# Import DOORS requirements
python tools/mbse/reqif_parser.py --project-id "proj-123" --file /path/to/reqs.reqif --json

# Build digital thread
python tools/mbse/digital_thread.py --project-id "proj-123" auto-link --json
python tools/mbse/digital_thread.py --project-id "proj-123" coverage --json

# Generate code from model
python tools/mbse/model_code_generator.py --project-id "proj-123" --language python --output ./src

# Map to NIST controls
python tools/mbse/model_control_mapper.py --project-id "proj-123" --map-all --json

# Detect drift
python tools/mbse/sync_engine.py --project-id "proj-123" detect-drift --json
python tools/mbse/sync_engine.py --project-id "proj-123" sync-model-to-code --json

# DES assessment
python tools/mbse/des_assessor.py --project-id "proj-123" --project-dir /path --json
python tools/mbse/des_report_generator.py --project-id "proj-123" --output-dir /path

# PI snapshot
python tools/mbse/pi_model_tracker.py --project-id "proj-123" --pi PI-25.1 --snapshot

# Extract diagram from screenshot
python tools/mbse/diagram_extractor.py --image diagram.png --diagram-type block_definition --project-id "proj-123" --json
```
