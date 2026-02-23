# Phase 20 — FIPS 199/200 Security Categorization

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 20 |
| Title | FIPS 199/200 Security Categorization |
| Status | Implemented |
| Priority | P0 |
| Dependencies | Phase 17 (Multi-Framework Compliance) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

FIPS 199 security categorization is Step 1 of the NIST Risk Management Framework (RMF). Every downstream compliance decision -- SSP control selection, NIST 800-53 baseline, FedRAMP level, CMMC maturity, and deployment architecture -- depends on correct categorization. An incorrect categorization means wrong controls, which means failed ATO. Yet most organizations perform categorization manually as a one-time paper exercise with no connection to the automated compliance pipeline.

The categorization process itself is non-trivial. Organizations must identify all information types their system processes, stores, or transmits using the NIST SP 800-60 Volume 2 catalog (hundreds of types across defense, government resource management, and internal risk management categories). Each type carries provisional Confidentiality, Integrity, and Availability (CIA) impact levels that may need organizational adjustment. The overall categorization uses a high watermark across all assigned types.

For IL6/SECRET national security systems, CNSSI 1253 overlays must be applied on top of FIPS 199, elevating minimum CIA floors per overlay rules. Additionally, FIPS 200 mandates validation across 17 minimum security requirement areas before any system can receive authorization. Without automation, these interdependent processes are error-prone, inconsistent, and disconnected from downstream artifact generation.

---

## 2. Goals

1. Provide a browsable SP 800-60 Volume 2 information type catalog with filtering by category (D.1 Services Delivery, D.2 Government Resource Management, D.3 Internal Risk Management)
2. Support assigning multiple information types to a project with per-type organizational impact adjustments and mandatory written justification
3. Compute FIPS 199 categorization using the high watermark method across all assigned types for Confidentiality, Integrity, and Availability
4. Automatically apply CNSSI 1253 overlays for IL6/SECRET national security systems, elevating minimum CIA floor per overlay rules
5. Validate all 17 FIPS 200 minimum security requirement areas (AC, AT, AU, CA, CM, CP, IA, IR, MA, MP, PE, PL, PS, RA, SA, SC, SI) against the computed baseline
6. Dynamically drive SSP baseline selection from categorization results (replacing static IL-based mapping)
7. Support manual categorization override with required justification
8. Integrate with the crosswalk engine to cascade categorization-driven baseline changes across all mapped frameworks

---

## 3. Architecture

```
+-------------------+     +-------------------+     +-------------------+
|  SP 800-60        |     |  Type Assignment  |     |  FIPS 199         |
|  Catalog Browse   |---->|  (per project)    |---->|  Categorizer      |
|  (D.1, D.2, D.3) |     |  + adjustments    |     |  (high watermark) |
+-------------------+     +-------------------+     +-------------------+
                                                            |
                                    +-----------------------+
                                    |                       |
                                    v                       v
                           +----------------+      +----------------+
                           | CNSSI 1253     |      | FIPS 200       |
                           | Overlay        |      | Validator      |
                           | (IL6/SECRET)   |      | (17 areas)     |
                           +----------------+      +----------------+
                                    |                       |
                                    v                       v
                           +----------------+      +----------------+
                           | SSP Baseline   |      | Gate            |
                           | Selection      |      | Evaluation      |
                           | (dynamic)      |      |                 |
                           +----------------+      +----------------+
```

The categorization pipeline follows 6 steps:

1. **Browse Catalog** -- Review SP 800-60 Vol 2 types filtered by category
2. **Assign Types** -- Add information types to project with optional CIA adjustments
3. **Categorize** -- Compute high watermark across all types; apply CNSSI 1253 if IL6
4. **Validate FIPS 200** -- Check all 17 minimum security areas against computed baseline
5. **Drive SSP** -- SSP generator reads categorization from DB for dynamic baseline selection
6. **Review and Approve** -- Categorization starts as draft; AO approves after verification

---

## 4. Requirements

### 4.1 Information Type Catalog

#### REQ-20-001: SP 800-60 Catalog
The system SHALL provide a browsable NIST SP 800-60 Volume 2 information type catalog stored in `context/compliance/nist_sp_800_60_types.json`, with filtering by category prefix (D.1, D.2, D.3).

#### REQ-20-002: Provisional Impact Levels
Each information type SHALL carry provisional Confidentiality, Integrity, and Availability impact levels as defined by SP 800-60.

### 4.2 Type Assignment and Adjustment

#### REQ-20-003: Multiple Type Assignment
The system SHALL support assigning multiple information types to a single project, stored in the `project_information_types` table.

#### REQ-20-004: Impact Adjustment
The system SHALL support organizational adjustments to provisional impact levels with mandatory written justification. Adjustments SHALL only raise impact levels, not lower below provisional.

### 4.3 Categorization

#### REQ-20-005: High Watermark Method
The system SHALL compute the overall system categorization as the maximum impact level across all assigned information types for each CIA objective: SC = {(C, max), (I, max), (A, max)}.

#### REQ-20-006: CNSSI 1253 Overlay
For IL6/SECRET national security systems, the system SHALL automatically apply CNSSI 1253 overlays from `context/compliance/cnssi_1253_overlay.json`, elevating minimum CIA floor per overlay rules.

#### REQ-20-007: Manual Override
The system SHALL support manual categorization override via `--method manual` with `--manual-c`, `--manual-i`, `--manual-a` flags and mandatory justification.

### 4.4 FIPS 200 Validation

#### REQ-20-008: 17-Area Validation
The system SHALL validate all 17 FIPS 200 minimum security requirement areas (AC, AT, AU, CA, CM, CP, IA, IR, MA, MP, PE, PL, PS, RA, SA, SC, SI) against the baseline derived from FIPS 199 categorization.

#### REQ-20-009: FIPS 200 Gate
The system SHALL evaluate a gate requiring 0 not_satisfied requirement areas, all 17 areas assessed, and coverage >= 80%.

### 4.5 SSP Integration

#### REQ-20-010: Dynamic Baseline Selection
The SSP generator SHALL dynamically read categorization from the database to determine the correct NIST 800-53 baseline, replacing static IL-based mapping (D56).

#### REQ-20-011: SSP Section Population
The SSP SHALL include actual C/I/A impact levels in Section 2, correct baseline applicability in Section 12, dynamic control baseline in Section 13, and all 17 FIPS 200 families in Section 15.

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `fips199_categorizations` | Categorization results: project_id, method, confidentiality, integrity, availability, overall, cnssi_applied, status (draft/approved), approved_by |
| `project_information_types` | Assigned types: project_id, type_id, provisional_c/i/a, adjusted_c/i/a, adjustment_justification |
| `fips200_assessments` | FIPS 200 validation results: project_id, area (17 values), status, baseline_requirement, evidence |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/compliance/fips199_categorizer.py` | Catalog browse, type assignment, categorization, CNSSI 1253 overlay, gate evaluation |
| `tools/compliance/fips200_validator.py` | Validate 17 FIPS 200 minimum security areas against baseline, gate evaluation |
| `tools/compliance/ssp_generator.py` | SSP generation with dynamic baseline from categorization (enhanced) |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D54 | FIPS 199 uses high watermark across SP 800-60 information types | Provisional levels are defaults, adjustable per organization; watermark ensures system categorized to highest need |
| D55 | FIPS 200 validates all 17 areas against baseline from FIPS 199, not impact level alone | Baseline drives control selection, not raw impact level |
| D56 | SSP baseline selection is dynamic: query DB first, fall back to IL mapping | Ensures SSP always reflects actual categorization, not a static assumption |
| D57 | CNSSI 1253 auto-applies for IL6/SECRET; elevates minimum C/I/A floor | National security systems require overlay per CNSSI guidance; auto-detection prevents omission |

---

## 8. Security Gate

**FIPS 199 Gate:**
- Categorization required for all ATO-tracked projects
- IL6 projects must have CNSSI 1253 overlay applied
- Categorization must be in approved status (not draft) before ATO proceeds
- At least one information type must be assigned

**FIPS 200 Gate:**
- 0 not_satisfied requirement areas
- All 17 minimum security areas assessed
- Coverage >= 80%

---

## 9. Commands

```bash
# Browse SP 800-60 catalog
python tools/compliance/fips199_categorizer.py --list-catalog
python tools/compliance/fips199_categorizer.py --list-catalog --category D.1 --json

# Assign information types
python tools/compliance/fips199_categorizer.py --project-id "proj-123" --add-type "D.1.1.1"
python tools/compliance/fips199_categorizer.py --project-id "proj-123" --add-type "D.2.3.4" --adjust-c High

# List assigned types
python tools/compliance/fips199_categorizer.py --project-id "proj-123" --list-types --json

# Run categorization
python tools/compliance/fips199_categorizer.py --project-id "proj-123" --categorize --json

# Force CNSSI 1253 method
python tools/compliance/fips199_categorizer.py --project-id "proj-123" --categorize --method cnssi_1253

# Gate evaluation
python tools/compliance/fips199_categorizer.py --project-id "proj-123" --gate

# FIPS 200 validation
python tools/compliance/fips200_validator.py --project-id "proj-123" --json
python tools/compliance/fips200_validator.py --project-id "proj-123" --gate --json
```
