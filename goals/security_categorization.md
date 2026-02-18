# Goal: Security Categorization (FIPS 199 / FIPS 200)

**CUI // SP-CTI**

## Description

Perform system security categorization per FIPS 199 using NIST SP 800-60 information types, validate minimum security requirements per FIPS 200, and dynamically select the appropriate NIST 800-53 baseline. For IL6/SECRET national security systems, apply CNSSI 1253 overlays.

**Why this matters:** FIPS 199 categorization is Step 1 of the NIST Risk Management Framework (RMF). Everything downstream — SSP, control selection, baselines, FedRAMP, CMMC — depends on correct categorization. An incorrect categorization means wrong controls = failed ATO.

## Prerequisites

- Project initialized (`goals/init_project.md` completed)
- ICDEV database initialized (`python tools/db/init_icdev_db.py`)
- NIST SP 800-60 catalog exists (`context/compliance/nist_sp_800_60_types.json`)

## Process

### Step 1: Browse Information Type Catalog

**Tool:** `python tools/compliance/fips199_categorizer.py --list-catalog`

Review the NIST SP 800-60 Vol 2 information type catalog. Filter by category:
- `--category D.1` — Services Delivery (defense, health, education, law enforcement, etc.)
- `--category D.2` — Government Resource Management (admin, finance, HR, IT)
- `--category D.3` — Internal Risk Management (regulatory, public affairs, budget)

Identify which information types the system processes, stores, or transmits.

### Step 2: Assign Information Types to Project

**Tool:** `python tools/compliance/fips199_categorizer.py --project-id <id> --add-type D.x.x.x`

Add each relevant information type. Each type has provisional CIA impact levels from SP 800-60.

**Optional adjustments:** If organizational factors require deviation from provisional levels:
```bash
python tools/compliance/fips199_categorizer.py --project-id <id> --add-type D.2.3.4 \
    --adjust-c High --adjustment-justification "PII payroll data requires elevated confidentiality"
```

**Key rules:**
- Adjustments must have written justification
- Adjustments can only raise impact levels, not lower below provisional (organizational policy)
- Multiple types can be assigned — the watermark takes the highest across all types

### Step 3: Run FIPS 199 Categorization

**Tool:** `python tools/compliance/fips199_categorizer.py --project-id <id> --categorize --json`

This computes:
1. **High watermark** across all assigned information types for each CIA objective
2. **Overall categorization** = max(Confidentiality, Integrity, Availability)
3. **CNSSI 1253 overlay** automatically applied for IL6/SECRET systems
4. **Baseline selection** = overall categorization maps to NIST 800-53 baseline

**Output:** SC {system_name} = {(confidentiality, impact), (integrity, impact), (availability, impact)}

**Alternative methods:**
- `--method manual --manual-c High --manual-i High --manual-a Moderate` — Manual categorization with justification
- `--method cnssi_1253` — Force CNSSI 1253 overlay for national security systems

### Step 4: Validate FIPS 200 Minimum Security Requirements

**Tool:** `python tools/compliance/fips200_validator.py --project-id <id> --json`

Checks all 17 minimum security requirement areas against the baseline from Step 3:
- AC, AT, AU, CA, CM, CP, IA, IR, MA, MP, PE, PL, PS, RA, SA, SC, SI

For each area, verifies that required NIST 800-53 controls are mapped in `project_controls`.

**Gate evaluation:** `--gate` flag checks for 0 not_satisfied areas.

### Step 5: Generate SSP with Dynamic Baseline

**Tool:** `python tools/compliance/ssp_generator.py --project <id>`

The SSP now auto-reads the categorization from the database:
- Section 2: Actual C/I/A impact levels (not hardcoded "Moderate")
- Section 12: Correct NIST 800-53 applicability statement with baseline
- Section 13: Dynamic control baseline matching categorization
- Section 15: All 17 FIPS 200 families included

### Step 6: Review and Approve

Categorization starts as "draft". The Authorizing Official (AO) reviews and approves:
1. Verify information types are correct and complete
2. Verify impact adjustments have justification
3. Verify CNSSI 1253 overlay applied if applicable (IL6/SECRET)
4. Verify baseline is appropriate for system risk profile

## Error Handling

| Error | Resolution |
|-------|-----------|
| No information types assigned | Review SP 800-60 catalog; assign at least one type |
| All impacts are "N/A" | Invalid — at least one type must have Low/Moderate/High |
| IL6 project without CNSSI 1253 | Gate fails — must apply overlay or change method to cnssi_1253 |
| Manual categorization without justification | Warning — require `--justification` flag |
| Stale categorization (>30 days draft) | Warning — categorization should be approved |

## Verify

```bash
# Categorization stored in DB
python tools/compliance/fips199_categorizer.py --project-id <id> --json

# Projects table updated
sqlite3 data/icdev.db "SELECT fips199_overall, fips199_confidentiality, fips199_integrity, fips199_availability FROM projects WHERE id='<id>'"

# SSP reflects actual categorization
python tools/compliance/ssp_generator.py --project <id>
grep "Overall.*Categorization" projects/<id>/docs/compliance/ssp_*.md

# FIPS 200 coverage
python tools/compliance/fips200_validator.py --project-id <id> --json

# Audit trail
python tools/audit/audit_query.py --project <id> --type fips199_categorized
```

## Related Goals

- `goals/compliance_workflow.md` — SSP generation (now uses dynamic baseline)
- `goals/ato_acceleration.md` — Multi-framework ATO (categorization drives baselines)
- `goals/init_project.md` — Project initialization

## Architecture Decisions

- **D30:** FIPS 199 uses high watermark across information types; SP 800-60 provisionals are defaults, adjustable per organization
- **D31:** FIPS 200 validates all 17 minimum security areas against baseline from FIPS 199, not impact level alone
- **D32:** SSP baseline selection is dynamic: query DB for categorization first, fall back to IL mapping
- **D33:** CNSSI 1253 auto-applies for IL6/SECRET systems; elevates minimum C/I/A floor per overlay rules
