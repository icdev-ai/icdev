---
name: icdev-init
description: Initialize a new ICDEV project with compliance scaffolding, CUI markings, and NIST 800-53 control baseline
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# $icdev-init

## What This Does
1. Creates a new project with UUID in the ICDEV database
2. Scaffolds directory structure with CUI markings on all files
3. Sets up compliance directory (SSP, POAM, STIG templates)
4. Initializes test scaffolding (pytest + behave/Gherkin BDD)
5. Creates Dockerfile and .gitignore
6. Maps initial NIST 800-53 controls (SA-11, CM-3, SA-15)
7. Records project creation in audit trail

## Steps

### 1. Check for icdev.yaml (Manifest-First Path)
```bash
# Check if icdev.yaml exists in the current directory
ls icdev.yaml 2>/dev/null
```
If `icdev.yaml` exists:
1. Run `python tools/project/session_context_builder.py --init --json` to register the project in the ICDEV database from the manifest
2. If successful, run `python tools/project/session_context_builder.py --format markdown` to display the loaded context
3. Run `python tools/ci/pipeline_config_generator.py --dir . --dry-run --json` to preview CI/CD pipeline configuration
4. Skip to Step 3 (scaffolding) — project metadata comes from the manifest

If `icdev.yaml` does NOT exist, proceed with the interactive flow below.

### 2. Load Configuration
```bash
!cat args/project_defaults.yaml
!cat args/cui_markings.yaml
```

### 3. Create Project
Run the CLI command or use MCP tool `project_create` MCP tool from icdev-core:
- Name: `$ARGUMENTS` (first positional arg)
- Type: from `--type` flag or default `webapp`
- Tech stack: from `--tech-stack` flag or defaults from project_defaults.yaml

### 4. Scaffold Directory Structure
Run the CLI command or use MCP tool `scaffold` MCP tool from icdev-builder:
- Creates: src/, tests/, features/, compliance/, docs/, .gitlab-ci.yml
- All files get CUI header banners per cui_markings.yaml

### 5. Initialize Compliance Baseline
Run the CLI command or use MCP tool `control_map` MCP tool from icdev-compliance:
- Map `project.create` activity to NIST 800-53 controls
- Initialize project_controls with baseline controls for the project type

### 6. Generate Initial Test Stubs
Run the CLI command or use MCP tool `write_tests` MCP tool from icdev-builder:
- Create initial health check test (unit + BDD)
- Ensure RED phase — tests should fail until code is written

### 7. Record in Audit Trail
The project creation is automatically logged. Verify with:
```bash
python tools/audit/audit_query.py --project-id <UUID> --recent 5
```

### 8. Output Summary
Display:
- Project UUID
- Directory location
- Compliance status (controls mapped)
- Next steps: "Run /icdev-build to start TDD development"

### 9. Output Summary
Display:
- Project UUID
- Directory location
- Compliance status (controls mapped)
- Next steps: "Run /icdev-build to start TDD development"

## Example
```
$icdev-init my-webapp --type webapp --tech-stack backend=flask,frontend=react,database=postgresql
```

## Error Handling
- If project name already exists: append timestamp suffix
- If database is locked: retry up to 3 times with 1s delay
- If scaffolder module unavailable: create minimal structure manually