---
mode: agent
description: "Initialize a new ICDEV project with compliance scaffolding, CUI markings, and NIST 800-53 control baseline"
tools:
  - terminal
  - file_search
---

# icdev-init

1. Creates a new project with UUID in the ICDEV database
2. Scaffolds directory structure with CUI markings on all files
3. Sets up compliance directory (SSP, POAM, STIG templates)
4. Initializes test scaffolding (pytest + behave/Gherkin BDD)
5. Creates Dockerfile and .gitignore
6. Maps initial NIST 800-53 controls (SA-11, CM-3, SA-15)
7. Records project creation in audit trail

## Steps

1. **Check for icdev.yaml (Manifest-First Path)**
```bash
# Check if icdev.yaml exists in the current directory
ls icdev.yaml 2>/dev/null
```

2. **Load Configuration**
```bash
!cat args/project_defaults.yaml
!cat args/cui_markings.yaml
```

3. **Create Project**
Run the equivalent CLI command for project_create:
- Name: `$ARGUMENTS` (first positional arg)
- Type: from `--type` flag or default `webapp`

4. **Scaffold Directory Structure**
Run the equivalent CLI command for scaffold:
- Creates: src/, tests/, features/, compliance/, docs/, .gitlab-ci.yml
- All files get CUI header banners per cui_markings.yaml

5. **Initialize Compliance Baseline**
Run the equivalent CLI command for control_map:
- Map `project.create` activity to NIST 800-53 controls
- Initialize project_controls with baseline controls for the project type

6. **Generate Initial Test Stubs**
Run the equivalent CLI command for write_tests:
- Create initial health check test (unit + BDD)
- Ensure RED phase — tests should fail until code is written

7. **Record in Audit Trail**
```bash
python tools/audit/audit_query.py --project-id <UUID> --recent 5
```

8. **Output Summary**
Display:
- Project UUID
- Directory location

9. **Output Summary**
Display:
- Project UUID
- Directory location

## Example
```
#prompt:icdev-init my-webapp --type webapp --tech-stack backend=flask,frontend=react,database=postgresql
```