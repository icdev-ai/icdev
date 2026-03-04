---
name: icdev-boundary
description: Assess ATO boundary impact and manage supply chain risk for requirements
context: fork
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# /icdev-boundary — ATO Boundary & Supply Chain Analysis

## Usage
```
/icdev-boundary <project-id> [--assess <requirement-id> --system <system-id>] [--scrm] [--isa-check] [--cve-check]
```

## What This Does
Runs RICOAS Phase 2 — ATO boundary impact and supply chain intelligence:
1. **Assess boundary impact** — evaluate how requirements affect existing ATO boundaries (GREEN/YELLOW/ORANGE/RED)
2. **Generate alternatives** — for RED-tier requirements, produce 3-5 alternative COAs within existing ATO
3. **Build dependency graph** — track vendor supply chain with upstream/downstream relationships
4. **SCRM assessment** — score vendors across 6 NIST 800-161 dimensions, check Section 889
5. **ISA/MOU lifecycle** — check expiring agreements, flag overdue reviews
6. **CVE triage** — triage vulnerabilities with blast radius propagation through dependency graph

All operations produce CUI-marked output and record audit trail entries.

## Steps

### Step 1: Validate Project
Verify the project exists:
```bash
python -c "
import sqlite3, sys
conn = sqlite3.connect('data/icdev.db')
row = conn.execute('SELECT id, name, impact_level FROM projects WHERE id=?', ('$PROJECT_ID',)).fetchone()
if not row: print('ERROR: Project not found'); sys.exit(1)
print(f'Project: {row[1]} (impact_level={row[2]})')
conn.close()
"
```

### Step 2: Register ATO System (if not already registered)
Register the target ATO system for boundary assessment:
```bash
python tools/requirements/boundary_analyzer.py --project-id $PROJECT_ID --register-system --system-id $SYSTEM_ID --json
```
- Records system boundary, current ATO status, authorization date, SSP reference
- Loads boundary impact rules from `context/requirements/boundary_impact_rules.json`
- Logs audit trail: ato_system_registered

### Step 3: Boundary Impact Assessment (if --assess)
Assess a requirement against the ATO boundary:
```bash
python tools/requirements/boundary_analyzer.py --project-id $PROJECT_ID --assess --requirement-id $REQ_ID --system-id $SYSTEM_ID --json
```
- Evaluates requirement against ATO boundary controls
- Returns tier (GREEN/YELLOW/ORANGE/RED) with score, affected controls, SSP sections
- GREEN (0-25): proceed, no ATO impact
- YELLOW (26-50): SSP addendum needed, ISSO notification
- ORANGE (51-75): SSP revision, security assessment, ISA review
- RED (76-100): **FULL STOP** — ATO-invalidating, requires alternative COAs

### Step 4: Generate RED Alternatives (if RED tier detected)
For RED-tier requirements, generate alternative approaches:
```bash
python tools/requirements/boundary_analyzer.py --project-id $PROJECT_ID --generate-alternatives --requirement-id $REQ_ID --json
```
- Generates 3-5 alternative COAs that achieve intent within existing ATO
- Each alternative includes: approach, residual risk, estimated effort, ATO impact tier
- Uses patterns from `context/requirements/red_alternative_patterns.json`

### Step 5: Supply Chain Health Check (if --scrm)
Build dependency graph and assess vendors:
```bash
python tools/supply_chain/dependency_graph.py --project-id $PROJECT_ID --build --json
python tools/supply_chain/scrm_assessor.py --project-id $PROJECT_ID --aggregate --json
python tools/supply_chain/scrm_assessor.py --project-id $PROJECT_ID --prohibited --json
```
- Builds adjacency list of all vendor dependencies
- Scores each vendor across 6 NIST 800-161 dimensions
- Checks Section 889 prohibited vendor list
- Reports aggregate risk distribution

### Step 6: ISA/MOU Lifecycle Check (if --isa-check)
Check ISA/MOU status:
```bash
python tools/supply_chain/isa_manager.py --project-id $PROJECT_ID --expiring --days 90 --json
python tools/supply_chain/isa_manager.py --project-id $PROJECT_ID --review-due --json
```
- Lists ISAs expiring within 90 days (configurable)
- Lists ISAs overdue for periodic review
- Flags ISAs with active data flows that are expired

### Step 7: CVE Triage (if --cve-check)
Triage CVEs with blast radius:
```bash
python tools/supply_chain/cve_triager.py --project-id $PROJECT_ID --sla-check --json
python tools/supply_chain/cve_triager.py --project-id $PROJECT_ID --propagate --json
```
- Checks CVE SLA compliance across all tracked vulnerabilities
- Propagates impact through dependency graph with severity decay
- Reports upstream/downstream blast radius for each CVE

### Step 8: Summary Report
Print final summary:
- Boundary assessments by tier (GREEN/YELLOW/ORANGE/RED counts)
- RED-tier requirements with alternative COAs generated
- Supply chain risk distribution (critical/high/medium/low)
- Section 889 compliance status
- ISA/MOU status (expiring, overdue, expired)
- CVE SLA compliance (in/out of compliance)
- Blocking conditions identified

## Error Handling
- If ATO system not registered: prompt to register before assessment
- If requirement not found: check intake session, report error
- If dependency graph empty: suggest adding vendors via add_vendor
- If SCRM assessment fails: check vendor records exist
- If CVE propagation finds circular dependency: break cycle, report warning

## Security Gates
- RED-tier requirement without alternative COA → **blocks**
- Critical SCRM risk unmitigated → **blocks**
- ISA expired with active data flow → **blocks**
- Critical CVE SLA overdue → **blocks**
- Section 889 prohibited vendor detected → **blocks**

## Related Skills
- `/icdev-intake` — Requirements intake (Phase 1 feeds boundary assessment)
- `/icdev-comply` — Compliance artifacts (SSP addendum for YELLOW/ORANGE tiers)
- `/icdev-maintain` — Maintenance audit (CVE triage feeds maintenance workflow)
- `/icdev-secure` — Security scanning (vulnerability data feeds CVE triage)
