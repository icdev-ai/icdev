# /icdev-modernize — Application Modernization (7Rs Migration Strategy)

Modernize legacy DoD applications using the 7 Rs of Migration Strategy. Analyzes legacy code, generates documentation, recommends migration strategy, creates migration plans, generates code, and tracks progress.

## Steps

### Step 1: Register Legacy Application
Register the legacy application for analysis:
```bash
python tools/modernization/legacy_analyzer.py --register --project-id {{project_id}} --name "{{app_name}}" --source-path {{source_path}}
```
Captures: file count, LOC, primary language, source hash.

### Step 2: Analyze Legacy Code
Run full static analysis (AST for Python, regex for Java/C#):
```bash
python tools/modernization/legacy_analyzer.py --analyze --project-id {{project_id}} --app-id {{app_id}}
```
Discovers: classes, functions, imports, inheritance, API endpoints, DB schemas, framework, complexity metrics, coupling/cohesion, tech debt estimate.

### Step 3: Extract Architecture & Generate Documentation
Reverse-engineer architecture and generate missing documentation:
```bash
python tools/modernization/architecture_extractor.py --app-id {{app_id}} --extract summary --json
python tools/modernization/doc_generator.py --app-id {{app_id}} --output-dir {{output_dir}} --type all
```
Produces: API docs, data dictionary, component docs, dependency map, tech debt report — all with CUI markings.

### Step 4: Run 7R Assessment
Score all 7 migration strategies with weighted decision matrix:
```bash
python tools/modernization/seven_r_assessor.py --project-id {{project_id}} --app-id {{app_id}} --matrix
```
Outputs: scored matrix (Rehost, Replatform, Refactor, Re-architect, Repurchase, Retire, Retain), recommended strategy, cost estimate, timeline, ATO impact, risk score.

### Step 5: Create Migration Plan
Create migration plan with decomposition tasks and timeline:
```bash
python tools/modernization/monolith_decomposer.py --app-id {{app_id}} --create-plan --project-id {{project_id}} --strategy {{strategy}} --target-arch {{architecture}} --json
```
For version/framework migrations:
```bash
python tools/modernization/version_migrator.py --source {{source}} --output {{output}} --language {{language}} --from {{from_ver}} --to {{to_ver}} --validate
python tools/modernization/framework_migrator.py --source {{source}} --output {{output}} --from {{from_fw}} --to {{to_fw}} --report
```
For database migrations:
```bash
python tools/modernization/db_migration_planner.py --app-id {{app_id}} --target postgresql --output-dir {{output_dir}} --type all
```

### Step 6: Generate Migration Code
Generate adapters, facades, service scaffolding, tests, and rollback scripts:
```bash
python tools/modernization/migration_code_generator.py --plan-id {{plan_id}} --output {{output_dir}} --generate all --language {{language}} --framework {{framework}}
```

### Step 7: Track Migration Progress
Track per-PI migration velocity, burndown, and compliance gates:
```bash
python tools/modernization/migration_tracker.py --plan-id {{plan_id}} --snapshot --pi {{pi_number}} --type pi_end
python tools/modernization/migration_tracker.py --plan-id {{plan_id}} --dashboard
python tools/modernization/migration_tracker.py --plan-id {{plan_id}} --gate --pi {{pi_number}}
```

### Step 8: Validate ATO Compliance Bridge
Ensure no NIST control coverage lost during migration:
```bash
python tools/modernization/compliance_bridge.py --plan-id {{plan_id}} --validate
python tools/modernization/compliance_bridge.py --plan-id {{plan_id}} --report --output-dir {{output_dir}}
```

### Step 9: Strangler Fig Management (if incremental migration)
For strangler fig approach, manage coexistence and cutover:
```bash
python tools/modernization/strangler_fig_manager.py --plan-id {{plan_id}} --status
python tools/modernization/strangler_fig_manager.py --plan-id {{plan_id}} --cutover --component-id {{component_id}} --to modern
python tools/modernization/strangler_fig_manager.py --plan-id {{plan_id}} --health
```

### Step 10: Generate Reports
Generate CUI-marked reports for leadership and compliance:
```bash
python tools/modernization/migration_report_generator.py --app-id {{app_id}} --type assessment --output-dir {{output_dir}}
python tools/modernization/migration_report_generator.py --plan-id {{plan_id}} --type progress --pi {{pi_number}} --output-dir {{output_dir}}
```

## Supported Legacy Frameworks
- **Python:** Python 2.x, Django 1.x, Flask 0.x
- **Java:** Java 8, Struts, EJB, JSP, Spring 3.x
- **C#/.NET:** .NET Framework 4.x, WCF, WebForms

## Migration Targets
- **Python:** Python 3.11+, Django 4.x, Flask 3.x, FastAPI
- **Java:** Java 17+, Spring Boot 3.x
- **C#/.NET:** .NET 8, ASP.NET Core, Blazor, gRPC
- **Database:** PostgreSQL/Aurora (from Oracle, MSSQL, DB2, Sybase)
- **Architecture:** Microservices, Modular Monolith, Event-Driven

## Key Principles
- **Read-only analysis** — never modifies original source code
- **Air-gap safe** — Python stdlib only, no external dependencies
- **ATO-aware** — NIST control inheritance tracked through digital thread
- **CUI-marked** — all artifacts include CUI // SP-CTI banners
- **SAFe-aligned** — PI-cadenced tracking with velocity and burndown
