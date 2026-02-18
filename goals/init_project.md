# Goal: Initialize New ICDEV Project

## Description

Initialize a new project within the ICDEV framework with full compliance scaffolding, database records, CUI markings, and audit trail. This is the entry point for every new project — nothing gets built without this step completing first.

**Why this matters:** Government and DoD projects require traceability from day one. Retroactively applying compliance is 10x harder than starting with it. This workflow ensures every project is born compliant.

---

## Prerequisites

- [ ] ICDEV database initialized (`python tools/db/init_icdev_db.py`)
- [ ] `.env` file exists with required configuration
- [ ] User has provided: project name, project type, classification level
- [ ] `memory/MEMORY.md` loaded (session context)

---

## Process

### Step 1: Validate Inputs

**Action:** Confirm required parameters before creating anything.

```
Required:
  --name         Project name (lowercase, hyphens, no spaces)
  --type         Project type: webapp | api | microservice | infrastructure | library
  --classification  CUI | UNCLASSIFIED | FOUO
```

**Validation rules:**
- Name must be unique (check DB first)
- Name must match pattern: `^[a-z][a-z0-9-]{2,48}$`
- Type must be one of the allowed values
- Classification defaults to CUI if not specified (safe default)

**Error handling:**
- Duplicate name → suggest alternative with timestamp suffix
- Invalid characters → strip and confirm with user
- Missing classification → default to CUI, inform user

---

### Step 2: Create Project Record in Database

**Tool:** `python tools/project/project_create.py --name <name> --type <type> --classification <classification>`

**Expected output:**
```
Project created: <project-id>
Name: <name>
Type: <type>
Classification: <classification>
Status: initialized
Created: <timestamp>
```

**Error handling:**
- DB locked → retry 3 times with 1s backoff
- DB missing → run `python tools/db/init_icdev_db.py` first, then retry
- Creation failed → read error, do not proceed, report to user

**Verify:** Query DB to confirm record exists before proceeding.

---

### Step 3: Scaffold Project Directory

**Tool:** `python tools/project/project_scaffold.py --project-id <project-id> --type <type>`

**Expected output:**
```
Scaffolded project directory:
  projects/<name>/
  ├── src/
  ├── tests/
  │   ├── unit/
  │   ├── integration/
  │   └── features/          # Gherkin feature files
  ├── docs/
  │   ├── architecture/
  │   └── compliance/
  ├── infrastructure/
  │   ├── terraform/
  │   ├── ansible/
  │   └── k8s/
  ├── security/
  │   └── scan-results/
  ├── .gitlab-ci.yml          # Pipeline template
  ├── Dockerfile              # STIG-hardened base
  ├── requirements.txt        # or package.json
  └── README.md
```

**Error handling:**
- Directory already exists → check if it's an abandoned scaffold. If empty, remove and re-scaffold. If populated, abort and report.
- Permission error → report, do not attempt chmod

**Verify:** Confirm directory tree matches expected structure for project type.

---

### Step 4: Apply CUI Markings

**Tool:** `python tools/compliance/cui_marker.py --directory projects/<name>`

**Expected output:**
```
CUI markings applied:
  - Banner added to all source files
  - README.md marked with classification
  - .cui-manifest.json created
  Files marked: <count>
  Classification: <classification>
```

**Error handling:**
- No files to mark → warning only, proceed (empty scaffold is expected)
- Unknown file type → skip with warning, log skipped files

**Verify:** Spot-check 2-3 files for correct CUI banner format:
```
// CUI // CONTROLLED UNCLASSIFIED INFORMATION
// Authorized distribution only
```

---

### Step 5: Initialize Compliance Baseline

**Tool:** `python tools/compliance/control_mapper.py --project <name> --control-families "AC,AU,CM,IA,SC"`

**Expected output:**
```
Control mapping initialized:
  - AC (Access Control): 25 controls mapped
  - AU (Audit): 16 controls mapped
  - CM (Configuration Management): 11 controls mapped
  - IA (Identification & Authentication): 11 controls mapped
  - SC (System & Communications): 44 controls mapped
  Total: 107 controls
  Status: NOT_ASSESSED
```

**Error handling:**
- Unknown control family → warn, skip that family, continue with valid ones
- DB write failure → retry, then report

**Verify:** Query compliance status shows project with mapped controls.

---

### Step 6: Log to Audit Trail

**Tool:** `python tools/audit/audit_logger.py --event "project_initialized" --actor "orchestrator" --action "create" --project <name>`

**Expected output:**
```
Audit entry logged:
  ID: <entry-id>
  Event: project_initialized
  Actor: orchestrator
  Action: create
  Project: <name>
  Timestamp: <ISO-8601>
  Hash: <SHA-256>
```

**Error handling:**
- Audit log failure is a CRITICAL error — do not proceed silently
- If audit write fails, mark project as "init_incomplete" in DB
- Retry 3 times, then escalate to user

**Verify:** Query audit trail confirms entry exists and hash chain is intact.

---

### Step 7: Log to Memory

**Tool:** `python tools/memory/memory_write.py --content "Initialized project <name> (type: <type>, classification: <classification>, id: <project-id>)" --type event --importance 6`

**Expected output:** Confirmation written to daily log and memory DB.

---

## Success Criteria

- [ ] Project record exists in database with correct metadata
- [ ] Directory structure matches expected scaffold for project type
- [ ] CUI markings applied to all applicable files
- [ ] NIST 800-53 control families mapped (minimum: AC, AU, CM, IA, SC)
- [ ] Audit trail entry logged with valid hash
- [ ] Memory entry recorded

**All 6 checks must pass.** If any fail, the project is in an inconsistent state — investigate and fix before proceeding to any other workflow.

---

## Edge Cases & Notes

1. **Re-initialization:** If a project with the same name exists but is marked "abandoned" or "init_incomplete", offer to clean up and re-initialize rather than failing.
2. **Offline mode:** If DB is unavailable, create the directory scaffold anyway but mark it clearly as "unregistered" — the user can register it later.
3. **Classification upgrade:** If a project starts as UNCLASSIFIED and later needs CUI, re-run Step 4 with the new classification. Do NOT downgrade classifications.
4. **Batch initialization:** For multiple projects, run sequentially — do not parallelize DB writes.
5. **Project naming collisions:** The timestamp suffix approach (`my-project-20260214`) prevents collisions but creates ugly names. Prefer asking the user for an alternative.

---

## GOTCHA Layer Mapping

| Step | GOTCHA Layer | Component |
|------|-------------|-----------|
| Validate inputs | Args | User-provided parameters |
| Create DB record | Tools | project_create.py |
| Scaffold directory | Tools | project_scaffold.py |
| Apply CUI markings | Tools | cui_marker.py |
| Initialize compliance | Tools | control_mapper.py |
| Log to audit | Tools | audit_logger.py |
| Orchestrate all steps | Orchestration | AI (you) |

---

## Related Files

- **Tools:** `tools/project/project_create.py`, `tools/project/project_scaffold.py`, `tools/compliance/cui_marker.py`, `tools/compliance/control_mapper.py`, `tools/audit/audit_logger.py`
- **Database:** `data/icdev.db` (projects table)
- **Context:** `context/classification_guide.md` (CUI marking rules)

---

## Changelog

- 2026-02-14: Initial creation
