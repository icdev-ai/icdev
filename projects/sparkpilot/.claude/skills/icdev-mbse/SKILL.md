---
name: icdev-mbse
description: MBSE integration — import SysML/DOORS, build digital thread, generate code, sync, assess DES compliance
context: fork
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# /icdev-mbse — Model-Based Systems Engineering Integration

## Usage
```
/icdev-mbse <project-id> [--import-xmi <path>] [--import-reqif <path>] [--generate-code] [--sync] [--assess] [--snapshot <pi>]
```

## What This Does
Integrates MBSE into the ICDEV SDLC workflow:
1. **Import SysML models** from Cameo Systems Modeler (XMI format)
2. **Import requirements** from IBM DOORS NG (ReqIF format)
3. **Build digital thread** — end-to-end traceability from requirements to NIST controls
4. **Generate code** from model elements (blocks → classes, activities → functions)
5. **Sync model and code** — detect drift and synchronize in either direction
6. **Assess DES compliance** — DoDI 5000.87 Digital Engineering Strategy conformance
7. **Create PI snapshots** — version model state per SAFe Program Increment

All operations produce CUI-marked output and record audit trail entries.

## Steps

### Step 1: Validate Project
Verify the project exists and has `mbse_enabled=1`:
```bash
python -c "
import sqlite3, sys
conn = sqlite3.connect('data/icdev.db')
row = conn.execute('SELECT id, name, mbse_enabled FROM projects WHERE id=?', ('$PROJECT_ID',)).fetchone()
if not row: print('ERROR: Project not found'); sys.exit(1)
print(f'Project: {row[1]} (mbse_enabled={row[2]})')
conn.close()
"
```

### Step 2: Import XMI Model (if --import-xmi provided)
Use the `import_xmi` MCP tool from icdev-mbse server:
```bash
python tools/mbse/xmi_parser.py --project-id $PROJECT_ID --file $XMI_PATH --json
```
- Parses Cameo SysML v1.6 XMI export
- Stores blocks, activities, requirements, state machines in sysml_elements table
- Stores relationships (satisfy, derive, verify, etc.) in sysml_relationships table
- Records import in model_imports table
- Logs audit trail: xmi_imported

### Step 3: Import ReqIF Requirements (if --import-reqif provided)
Use the `import_reqif` MCP tool from icdev-mbse server:
```bash
python tools/mbse/reqif_parser.py --project-id $PROJECT_ID --file $REQIF_PATH --json
```
- Parses DOORS NG ReqIF 1.2 export
- Stores requirements in doors_requirements table
- Logs audit trail: reqif_imported

### Step 4: Build Digital Thread Links
Use the `thread_coverage` MCP tool:
```bash
python tools/mbse/digital_thread.py --project-id $PROJECT_ID auto-link --json
```
- Auto-link requirements to model elements by name matching
- Auto-link model elements to code modules by name matching
- Auto-link model elements to NIST controls by keyword matching
- Reports coverage: requirements→model→code→test→control

### Step 5: Generate Code from Model (if --generate-code)
Use the `generate_code` MCP tool:
```bash
python tools/mbse/model_code_generator.py --project-id $PROJECT_ID --language python --output $PROJECT_DIR --json
```
- Generates Python classes from SysML blocks (BDD)
- Generates Python modules from SysML activities
- Generates test stubs from SysML/DOORS requirements
- Creates model_code_mappings for every generated file
- Creates digital_thread_links: model→code

### Step 6: Map Model to NIST Controls
```bash
python tools/mbse/model_control_mapper.py --project-id $PROJECT_ID --map-all --json
```
- Maps SysML elements to NIST 800-53 controls by keyword analysis
- Creates digital_thread_links: model→control
- Reports control coverage from model

### Step 7: Detect Drift and Sync (if --sync)
Use the `detect_drift` and `sync_model` MCP tools:
```bash
python tools/mbse/sync_engine.py --project-id $PROJECT_ID detect-drift --json
```
If drift detected:
```bash
python tools/mbse/sync_engine.py --project-id $PROJECT_ID sync-model-to-code --json
```

### Step 8: DES Compliance Assessment (if --assess)
Use the `des_assess` MCP tool:
```bash
python tools/mbse/des_assessor.py --project-id $PROJECT_ID --project-dir $PROJECT_DIR --json --report
```
- Runs 10 auto-checks per DoDI 5000.87
- Stores results in des_compliance table
- Generates CUI-marked DES compliance report
- Gate: 0 non_compliant on critical requirements

### Step 9: Create PI Snapshot (if --snapshot)
Use the `model_snapshot` MCP tool:
```bash
python tools/mbse/pi_model_tracker.py --project-id $PROJECT_ID --pi $PI_NUMBER --snapshot --json
```
- Snapshots current model state (element counts, hashes, coverage)
- Stores in model_snapshots table
- Logs audit trail: model_snapshot

### Step 10: Generate Summary Report
Print final summary:
- Model elements imported (blocks, activities, requirements)
- Digital thread coverage percentages
- Code files generated
- Sync status
- DES compliance score and gate status
- PI snapshot hash

## Error Handling
- If XMI parse fails: report validation errors, continue with existing model data
- If ReqIF parse fails: report validation errors, continue with existing requirements
- If no model data exists: skip code generation and sync, report warning
- If DES assessment fails: report partial results, continue with other steps

## Security Gates
- DES gate: 0 non_compliant on critical DES requirements
- Thread coverage: warn if below 60% requirement coverage
- Sync: warn if any conflicts detected

## Related Skills
- `/icdev-init` — Initialize project (set mbse_enabled=1 with --mbse flag)
- `/icdev-build` — Build code (M-ATLAS: Model→Architect→Trace→Link→Assemble→Stress-test)
- `/icdev-comply` — Generate compliance artifacts (uses digital thread for traceability)
- `/icdev-test` — Run tests (includes model-generated test stubs)
