# MBSE Integration — Model-Based Systems Engineering Workflow

> CUI // SP-CTI

## Goal

Integrate MBSE into the ICDEV SDLC using the M-ATLAS workflow variant (Model → Architect → Trace → Link → Assemble → Stress-test). Provides end-to-end digital thread from DOORS requirements through SysML models to generated code, tests, and NIST controls. Supports DoDI 5000.87 Digital Engineering Strategy (DES) compliance and SAFe Program Increment (PI) model snapshots.

**Why this matters:** DoDI 5000.87 mandates digital engineering for all DoD acquisition programs. Without model-code traceability, programs fail audits and cannot demonstrate that delivered code implements the authoritative design. The digital thread closes the loop from requirement to deployment.

---

## Prerequisites

- [ ] Project initialized with `mbse_enabled=1` (via `/icdev-init --mbse`)
- [ ] SysML model exported as XMI 2.5.1 from Cameo Systems Modeler (v19.0+)
- [ ] Requirements exported as ReqIF 1.2 from IBM DOORS NG
- [ ] ICDEV database initialized (`python tools/db/init_icdev_db.py`)
- [ ] `memory/MEMORY.md` loaded (session context)
- [ ] NIST 800-53 control catalog available (`context/compliance/nist_800_53.json`)

---

## M-ATLAS Workflow

Extension of ATLAS adding a Model pre-phase. If no model exists, falls back to standard ATLAS.

| Step | Phase | Description |
|------|-------|-------------|
| **M** | Model | Import XMI/ReqIF, build digital thread, generate code scaffolding |
| **A** | Architect | System design informed by model elements (blocks, interfaces, behaviors) |
| **T** | Trace | Data schema, integrations, stack — augmented with model traceability |
| **L** | Link | Validate connections including model-to-code and requirement-to-test mappings |
| **A** | Assemble | Build with model-generated scaffolding as the starting point |
| **S** | Stress-test | Test including model-generated test stubs and traceability verification |

---

## Step 1: Import SysML Model (XMI)

**Purpose:** Ingest Cameo SysML v1.6 model into ICDEV database for downstream traceability and code generation.

**Tool:** `tools/mbse/xmi_parser.py`

```bash
python tools/mbse/xmi_parser.py --project-id PROJECT_ID --file /path/to/model.xmi --json
```

**What it parses:**
- Block Definition Diagrams → `sysml_elements` (type=block, with properties and ports)
- Activity Diagrams → `sysml_elements` (type=activity, with actions and flows)
- Requirement Diagrams → `sysml_elements` (type=requirement, with text and ID)
- State Machine Diagrams → `sysml_elements` (type=state_machine, with states and transitions)
- Use Case Diagrams → `sysml_elements` (type=use_case, with actors and associations)
- Internal Block Diagrams → `sysml_relationships` (type=connector, port-to-port)
- Parametric Diagrams → `sysml_elements` (type=constraint_block, with equations)

**Storage:** Writes to `sysml_elements` + `sysml_relationships` tables. Records import metadata in `model_imports`. Logs to audit trail (immutable).

**Validate before import:**
```bash
python tools/mbse/xmi_parser.py --project-id PROJECT_ID --file /path/to/model.xmi --validate-only
```

**Expected output:**
```
XMI import complete: /path/to/model.xmi
  Blocks:          42
  Activities:      18
  Requirements:    97
  State Machines:   6
  Use Cases:       12
  Relationships:  134
  Constraint Blocks: 4
  Total elements: 179
  Import ID: imp-20260216-001
  Audit entry: aud-20260216-001
```

**Error handling:**
- XMI version mismatch → warn and attempt best-effort parse, log unsupported elements
- Duplicate import → detect by file hash, prompt to overwrite or skip
- Malformed XMI → fail with line number and element context, do not partial-import
- Missing stereotypes → import as generic UML elements, flag for review

---

## Step 2: Import Requirements (ReqIF)

**Purpose:** Ingest DOORS NG requirements into ICDEV database for bidirectional traceability.

**Tool:** `tools/mbse/reqif_parser.py`

```bash
python tools/mbse/reqif_parser.py --project-id PROJECT_ID --file /path/to/reqs.reqif --json
```

**What it parses:**
- SPEC-OBJECT elements → `doors_requirements` (ID, text, type, priority, status)
- SPEC-RELATION elements → `doors_req_links` (parent-child, derives, satisfies)
- ATTRIBUTE-DEFINITION mappings → column-to-field mapping (configurable via `args/reqif_mappings.yaml`)
- SPEC-HIERARCHY → requirement tree structure preserved in `req_hierarchy` table

**Diff against previous import:**
```bash
python tools/mbse/reqif_parser.py --project-id PROJECT_ID --file /path/to/reqs.reqif --diff
```

**Expected output:**
```
ReqIF import complete: /path/to/reqs.reqif
  Requirements:    97
  Relations:       43
  Hierarchy depth:  4
  New (since last): 12
  Changed:           5
  Deleted:           2
  Import ID: imp-20260216-002
  Audit entry: aud-20260216-002
```

**Error handling:**
- Missing attribute mappings → use defaults, warn user to verify `args/reqif_mappings.yaml`
- Encoding issues → attempt UTF-8/UTF-16 detection, fail if unparseable
- Orphan relations (referencing missing requirements) → import but flag as broken links
- Empty requirement text → import with `[EMPTY — requires review]` placeholder

---

## Step 3: Build Digital Thread

**Purpose:** Establish end-to-end traceability: Requirement → Model Element → Code Module → Test Case → NIST Control.

**Tool:** `tools/mbse/digital_thread.py`

```bash
# Auto-link requirements to model elements (name/ID matching + NLP similarity)
python tools/mbse/digital_thread.py --project-id PROJECT_ID auto-link --json

# Auto-link model elements to NIST 800-53 controls (keyword analysis)
python tools/mbse/digital_thread.py --project-id PROJECT_ID auto-link-controls --json

# Generate coverage report
python tools/mbse/digital_thread.py --project-id PROJECT_ID coverage --json

# Manual link (for cases auto-link misses)
python tools/mbse/digital_thread.py --project-id PROJECT_ID link \
  --source "REQ-042" --target "blk-AuthService" --link-type "satisfies"
```

**Traceability chain built:**
```
DOORS Requirement → SysML Element → Code Module → Test Case → NIST Control
       (ReqIF)        (XMI)         (generated)    (TDD)      (800-53)
```

**Expected output (coverage):**
```
Digital Thread Coverage Report
  Requirements with model links:    87/97  (89.7%)
  Model elements with code links:   38/42  (90.5%)
  Code modules with test links:     35/38  (92.1%)
  Requirements with NIST links:     72/97  (74.2%)
  End-to-end traced (req→test):     32/97  (33.0%)  *** ATTENTION ***
  Orphan model elements:             4
  Orphan test cases:                 3
  Unlinked NIST controls:           14
```

**Error handling:**
- Low auto-link confidence (<0.6) → queue for manual review, do not auto-link
- Circular links detected → reject and report cycle path
- Missing code modules (not yet generated) → mark as "pending-codegen" in thread

---

## Step 4: Generate Code from Model

**Purpose:** Generate code scaffolding from SysML block and activity elements. Produces class stubs, interfaces, and method signatures that implement the model design.

**Tool:** `tools/mbse/model_codegen.py`

```bash
# Generate from all model elements
python tools/mbse/model_codegen.py --project-id PROJECT_ID --project-dir /path/to/project --json

# Generate from specific block only
python tools/mbse/model_codegen.py --project-id PROJECT_ID --project-dir /path/to/project \
  --element-id "blk-AuthService" --json

# Preview without writing files
python tools/mbse/model_codegen.py --project-id PROJECT_ID --project-dir /path/to/project --dry-run
```

**What it generates:**
- Blocks → Python classes with properties as attributes and operations as method stubs
- Activities → Function definitions with input/output parameters from activity pins
- State Machines → State pattern classes with transition methods
- Interfaces (flow ports) → Abstract base classes / Protocol classes
- Constraint Blocks → Validation functions with assertion stubs

**Expected output:**
```
Code generation complete:
  Classes generated:      42
  Interfaces generated:   11
  State machines:          6
  Validation functions:    4
  Test stubs generated:   42
  Output: /path/to/project/src/generated/
  Test output: /path/to/project/tests/generated/
  Digital thread links created: 63
```

**Generated code includes:**
- `# GENERATED FROM: blk-AuthService (model v2.1)` header comment
- `# TRACES TO: REQ-042, REQ-043` traceability comment
- `# CUI // SP-CTI` marking on every file
- Method docstrings from model operation descriptions
- TODO markers for implementation body: `# TODO: Implement — see activity "Authenticate User"`

**Error handling:**
- Model element has no operations → generate empty class with TODO
- Name collision with existing code → suffix with `_gen`, warn user to reconcile
- Unsupported SysML element type → skip with warning, log to audit trail

---

## Step 5: Map Model Elements to NIST 800-53 Controls

**Purpose:** Automatically map SysML model elements to NIST 800-53 security controls based on element type, name, and description keywords.

**Tool:** `tools/mbse/control_mapper.py`

```bash
# Auto-map all model elements to NIST controls
python tools/mbse/control_mapper.py --project-id PROJECT_ID --json

# Map specific control family
python tools/mbse/control_mapper.py --project-id PROJECT_ID --family AC --json

# Generate compliance matrix (model element × control)
python tools/mbse/control_mapper.py --project-id PROJECT_ID --matrix --json
```

**Mapping rules (keyword-based + type-based):**

| Model Element Pattern | NIST Control Family | Example Controls |
|----------------------|--------------------|--------------------|
| Authentication, Login, Credential | AC (Access Control) | AC-2, AC-7, AC-8 |
| Audit, Log, Event, Trail | AU (Audit) | AU-2, AU-3, AU-6 |
| Encrypt, TLS, Certificate, PKI | SC (System/Comms) | SC-8, SC-12, SC-13 |
| Session, Timeout, Token | AC, SC | AC-11, AC-12, SC-23 |
| Input, Validate, Sanitize | SI (System Integrity) | SI-10, SI-15 |
| Backup, Recovery, Contingency | CP (Contingency) | CP-9, CP-10 |
| Monitor, Alert, Health | CA, SI | CA-7, SI-4 |

**Expected output:**
```
NIST control mapping complete:
  Model elements mapped:     156/179  (87.2%)
  Unique controls mapped:     48
  Control families covered:   12/20
  High-confidence maps:      112  (confidence >= 0.8)
  Medium-confidence maps:     44  (confidence 0.5-0.79)
  Low-confidence (review):     0  (below 0.5 excluded)
  Unmapped elements:          23  (no keyword match)
```

**Error handling:**
- Element with no description → attempt name-only matching, mark as low-confidence
- Control catalog missing → fail with instructions to verify `context/compliance/nist_800_53.json`
- Ambiguous mapping (element matches multiple families) → assign all with confidence scores

---

## Step 6: Detect Drift and Sync Model-Code

**Purpose:** Detect when code has diverged from the authoritative model and provide reconciliation actions. Drift detection runs on every build or on demand.

**Tool:** `tools/mbse/drift_detector.py`

```bash
# Full drift analysis
python tools/mbse/drift_detector.py --project-id PROJECT_ID --project-dir /path/to/project --json

# Quick check (signatures only, no deep analysis)
python tools/mbse/drift_detector.py --project-id PROJECT_ID --project-dir /path/to/project --quick

# Auto-sync model to match code changes (requires --confirm)
python tools/mbse/drift_detector.py --project-id PROJECT_ID --project-dir /path/to/project \
  --sync code-to-model --confirm

# Auto-sync code to match model changes
python tools/mbse/drift_detector.py --project-id PROJECT_ID --project-dir /path/to/project \
  --sync model-to-code --confirm
```

**What it checks:**
- Class names and hierarchy match block definitions
- Method signatures match operation definitions
- Properties/attributes match block value properties
- State machine transitions match code branching logic
- Interface implementations match flow port definitions
- Deleted model elements still have orphan code

**Expected output:**
```
Drift analysis complete:
  Elements checked:    42
  In sync:             35  (83.3%)
  Drifted:              5  (11.9%)
  Code-only (orphan):   2  (4.8%)
  Model-only (missing): 0  (0.0%)

Drift details:
  blk-AuthService: method "validate_token" added in code, not in model
  blk-UserProfile: property "last_login" renamed to "last_active" in code
  blk-AuditLogger: new method "rotate_logs" in code, not in model
  blk-SessionMgr: return type changed (str→dict) in code
  blk-DataExport: parameter added ("format") in code

Recommended action: Run --sync code-to-model to update model with code changes
```

**Error handling:**
- Generated file deleted by developer → flag as orphan, do not re-generate without confirmation
- Model element renamed → detect via element ID (not name), flag as rename drift
- Sync conflict (both model and code changed) → refuse auto-sync, require manual resolution

---

## Step 7: DES Compliance Assessment (DoDI 5000.87)

**Purpose:** Assess project compliance with the DoD Digital Engineering Strategy per DoDI 5000.87. Required for Milestone B/C reviews and all ACAT I/II programs.

**Tool:** `tools/mbse/des_assessor.py`

```bash
python tools/mbse/des_assessor.py --project-id PROJECT_ID --json
```

**Assessment domains (5 DES goals):**

| Goal | Description | Checks |
|------|-------------|--------|
| Goal 1 | Formalize development/use of models | Model exists, elements complete, stereotypes applied |
| Goal 2 | Provide authoritative source of truth | Single model repo, version controlled, no shadow docs |
| Goal 3 | Incorporate tech innovation | Automated codegen, CI/CD integration, tool interop |
| Goal 4 | Establish infrastructure/environments | Model repo accessible, MBSE tools provisioned |
| Goal 5 | Transform culture and workforce | Training records, MBSE role assignments |

**Expected output:**
```
DES Compliance Assessment (DoDI 5000.87):
  Goal 1 — Formalize Models:          PASS  (4/4 checks satisfied)
  Goal 2 — Authoritative Source:      PASS  (3/3 checks satisfied)
  Goal 3 — Tech Innovation:           PARTIAL (2/3 checks — missing CI/CD model validation)
  Goal 4 — Infrastructure:            PASS  (3/3 checks satisfied)
  Goal 5 — Culture/Workforce:         NOT ASSESSED (requires manual input)

  Overall DES score: 82%
  Milestone readiness: CONDITIONAL — resolve Goal 3 gaps
  Report: projects/<name>/docs/compliance/des_assessment_<name>.md
  Audit entry: aud-20260216-003
```

**Error handling:**
- No model imported → Goal 1 auto-fails, provide instructions to run Step 1
- Missing training records → mark Goal 5 as "NOT ASSESSED", do not fail the overall assessment
- Assessment already exists for this version → prompt to overwrite or create new version

---

## Step 8: Create PI Model Snapshot (SAFe Integration)

**Purpose:** Capture a point-in-time snapshot of the model, digital thread, and compliance state aligned to a SAFe Program Increment boundary. Enables PI-over-PI comparison and audit trail.

**Tool:** `tools/mbse/pi_snapshot.py`

```bash
# Create snapshot for current PI
python tools/mbse/pi_snapshot.py --project-id PROJECT_ID --pi "PI-2026.1" --json

# Compare two PI snapshots
python tools/mbse/pi_snapshot.py --project-id PROJECT_ID \
  --compare "PI-2025.4" "PI-2026.1" --json

# List all snapshots
python tools/mbse/pi_snapshot.py --project-id PROJECT_ID --list
```

**What the snapshot captures:**
- All `sysml_elements` and `sysml_relationships` at snapshot time
- All `doors_requirements` and link states
- Digital thread coverage percentages
- Drift status (in-sync vs. drifted element count)
- NIST control mapping coverage
- DES compliance score
- Code generation hash (to detect post-snapshot code changes)
- SBOM snapshot (dependency versions at PI boundary)

**Expected output:**
```
PI Snapshot created: PI-2026.1
  Snapshot ID: snap-20260216-001
  Model elements:      179
  Requirements:         97
  Digital thread coverage: 89.7%
  Drift: 5 elements drifted
  NIST coverage: 87.2%
  DES score: 82%
  SBOM components: 134
  Stored in: pi_snapshots table
  Audit entry: aud-20260216-004
```

**Expected output (comparison):**
```
PI Comparison: PI-2025.4 → PI-2026.1
  Model elements: 163 → 179 (+16)
  Requirements:    91 → 97  (+6)
  Thread coverage: 78.2% → 89.7% (+11.5%)
  Drift: 12 → 5 (-7 resolved)
  NIST coverage: 71.0% → 87.2% (+16.2%)
  DES score: 65% → 82% (+17%)
  New controls mapped: AC-11, SC-23, SI-15
  Velocity: 16 elements/PI, 6 reqs/PI
```

**Error handling:**
- PI already has a snapshot → prompt to overwrite or version (e.g., PI-2026.1-v2)
- Missing data for snapshot fields → capture what exists, mark missing fields as null with warnings
- Comparison with nonexistent PI → fail with available snapshot list

---

## Step 9: Generate Reports

**Purpose:** Produce human-readable and machine-parseable reports for stakeholders, assessors, and audit boards.

**Tool:** `tools/mbse/report_generator.py`

```bash
# Full MBSE status report
python tools/mbse/report_generator.py --project-id PROJECT_ID --type status --json

# Digital thread traceability report
python tools/mbse/report_generator.py --project-id PROJECT_ID --type traceability --json

# DES compliance report (for milestone review)
python tools/mbse/report_generator.py --project-id PROJECT_ID --type des --json

# PI delta report
python tools/mbse/report_generator.py --project-id PROJECT_ID --type pi-delta \
  --pi-from "PI-2025.4" --pi-to "PI-2026.1" --json
```

**All reports include:** CUI // SP-CTI banners, generation timestamp, project metadata, and audit trail entry.

---

## Error Handling

| Failure | Action |
|---------|--------|
| XMI parse failure | Log line number + element, abort import, preserve no partial data |
| ReqIF encoding error | Attempt UTF-8/UTF-16 auto-detect, fail if unresolvable |
| Auto-link confidence < 0.6 | Queue for manual review, do not create link |
| Circular traceability link | Reject link, report cycle path to user |
| Drift sync conflict | Refuse auto-sync, require manual resolution |
| DES assessment data missing | Score available domains, mark others NOT ASSESSED |
| PI snapshot collision | Prompt overwrite or version increment |
| Database locked | Retry 3x with 2s backoff, fail with lock holder info |
| Tool script missing | Check `tools/manifest.md`, report which tool needs creation |

---

## Security Gates

MBSE artifacts are subject to the same security gates as all ICDEV outputs:

- **Model Import Gate:** XMI/ReqIF files must pass schema validation before import
- **Code Generation Gate:** Generated code inherits all TDD requirements — must have tests before merge
- **Traceability Gate:** No code merged without at least one requirement link in the digital thread
- **Drift Gate:** Drift > 20% blocks deployment — model and code must be reconciled
- **DES Gate:** DES score < 70% flags project for program management review
- **CUI Gate:** All generated reports and code files must carry CUI // SP-CTI markings

---

## Related Goals

- **Feeds from:** `goals/init_project.md` (project setup with `--mbse` flag)
- **Feeds into:** `goals/build_app.md` (ATLAS workflow, M-phase prepends), `goals/tdd_workflow.md` (model-generated test stubs), `goals/compliance_workflow.md` (NIST control mappings), `goals/deploy_workflow.md` (DES gate check)
- **Tools:** `tools/mbse/xmi_parser.py`, `tools/mbse/reqif_parser.py`, `tools/mbse/digital_thread.py`, `tools/mbse/model_codegen.py`, `tools/mbse/control_mapper.py`, `tools/mbse/drift_detector.py`, `tools/mbse/des_assessor.py`, `tools/mbse/pi_snapshot.py`, `tools/mbse/report_generator.py`
- **Context:** `context/compliance/nist_800_53.json`, `context/mbse/sysml_stereotypes.json`, `context/mbse/des_5000_87_goals.json`
- **Args:** `args/reqif_mappings.yaml`, `args/mbse_defaults.yaml`

---

## Changelog

- 2026-02-16: Initial creation — M-ATLAS workflow with 9 steps covering XMI/ReqIF import, digital thread, codegen, NIST mapping, drift detection, DES compliance, PI snapshots, and reporting
