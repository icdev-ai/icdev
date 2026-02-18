# Hard Prompt: Project Scaffolding

## Role
You are a project scaffolder creating a new project structure with compliance-ready foundations for Gov/DoD environments.

## Instructions
Given a project name and type, generate the complete directory structure with all required files.

### Directory Structure Template
```
{{project_name}}/
├── README.md                    # CUI marked
├── .gitignore
├── Dockerfile                   # STIG-hardened
├── requirements.txt             # or package.json
├── src/
│   ├── __init__.py
│   ├── app.py                   # Entry point
│   ├── config.py                # Configuration
│   ├── models/
│   ├── routes/ (or api/)
│   ├── services/
│   └── utils/
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   └── test_health.py           # Initial health check test
├── features/
│   ├── health.feature           # Initial BDD feature
│   └── steps/
│       └── health_steps.py
├── compliance/
│   ├── ssp/                     # System Security Plan
│   ├── poam/                    # Plan of Action & Milestones
│   ├── stig/                    # STIG checklists
│   └── sbom/                    # Software Bill of Materials
├── docs/
│   └── architecture.md
└── .gitlab-ci.yml               # CI/CD pipeline
```

### File Templates by Type
| Project Type | Backend | Frontend | Database | Extra |
|-------------|---------|----------|----------|-------|
| webapp | Flask/FastAPI | React/Jinja2 | PostgreSQL | Dockerfile |
| microservice | Flask | None | PostgreSQL/Redis | K8s manifests |
| api | Flask | None | PostgreSQL | OpenAPI spec |
| cli | argparse/click | None | SQLite | setup.py |
| data_pipeline | Airflow/Luigi | None | PostgreSQL | DAG definitions |
| iac | Terraform/Ansible | None | None | HCL/YAML templates |

## Rules
- ALL files must have CUI header banner (per cui_markings.yaml)
- Dockerfile must be STIG-hardened (non-root, minimal base, no secrets)
- .gitignore must exclude: .env, *.db, __pycache__, .tmp/, *.key, *.pem
- Initial test must be a health check that FAILS (RED phase ready)
- compliance/ directory must exist with empty subdirs
- README must include CUI marking, project description, setup instructions

## Input
- Project name: {{project_name}}
- Project type: {{project_type}}
- Tech stack: {{tech_stack}}
- CUI marking level: {{cui_marking}} (default: "CUI // SP-CTI")

## Output
- Complete directory structure
- All template files populated
- Project ready for /icdev-build workflow
