# Application Modernization (Phase 19 — 7Rs Migration)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Application Modernization (Phase 19 — 7Rs Migration)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Legacy Analyzer [DEPRECATED] | tools/modernization/legacy_analyzer.py | Static analysis engine (AST for Python, regex for Java/C#) — components, dependencies, APIs, frameworks, complexity | --register/--analyze, --project-id, --app-id, --source-path, --json | Analysis summary |
| Architecture Extractor | tools/modernization/architecture_extractor.py | Reverse-engineer architecture — call graph, component diagram, data flow, service boundaries | --app-id, --extract, --json | Architecture summary |
| Doc Generator | tools/modernization/doc_generator.py | Generate CUI-marked docs from analysis — API docs, data dictionary, component docs, dependency map | --app-id, --output-dir, --type, --json | File paths |
| 7R Assessor [DEPRECATED] | tools/modernization/seven_r_assessor.py | Score all 7 Rs with weighted decision matrix, recommend strategy | --project-id, --app-id, --matrix, --weights, --json | Scored matrix |
| Version Migrator | tools/modernization/version_migrator.py | Transform legacy code to newer versions (Python 2→3, Java 8→17, .NET FW→.NET 8) | --source, --output, --language, --from, --to, --validate | Transformation summary |
| Framework Migrator | tools/modernization/framework_migrator.py | Transform frameworks (Struts→Spring, EJB→Spring, WCF→ASP.NET Core, WebForms→Razor, Django/Flask upgrades) | --source, --output, --from, --to, --report | Transformation summary |
| Monolith Decomposer | tools/modernization/monolith_decomposer.py | Bounded context detection, service boundary suggestion, decomposition planning | --app-id, --detect-contexts, --suggest-boundaries, --create-plan, --json | Plan + tasks |
| DB Migration Planner | tools/modernization/db_migration_planner.py | Generate DDL scripts, data migration SQL, stored procedure translation (Oracle/MSSQL/DB2→PostgreSQL) | --app-id, --target, --output-dir, --type, --json | DDL + migration scripts |
| Strangler Fig Manager | tools/modernization/strangler_fig_manager.py | Incremental migration coexistence — facade routing, cutover tracking, health checks | --plan-id, --create, --status, --cutover, --routing, --health, --json | Cutover status |
| Compliance Bridge | tools/modernization/compliance_bridge.py | ATO-aware migration — control inheritance, distribution, gap analysis, coverage validation | --plan-id, --inherit, --distribute, --gaps, --validate, --json | Coverage status |
| Migration Code Generator | tools/modernization/migration_code_generator.py | Generate adapters, facades, service scaffolds, DAL, tests, rollback scripts | --plan-id, --output, --generate, --language, --framework, --json | Generated file paths |
| Migration Report Generator | tools/modernization/migration_report_generator.py | CUI-marked reports — assessment, progress, ATO impact, executive summary | --app-id, --plan-id, --pi, --output-dir, --type, --json | Report paths |
| Migration Tracker | tools/modernization/migration_tracker.py | SAFe PI-cadenced tracking — snapshots, velocity, burndown, compliance gates | --plan-id, --snapshot, --velocity, --burndown, --gate, --dashboard, --json | PI metrics |
| MCP Modernization Server | tools/mcp/modernization_server.py | MCP server for modernization tools (10 tools: register, analyze, assess, plan, generate, track, migrate) | stdio | JSON-RPC responses |
| Migration Cost API | tools/dashboard/api/migration_cost.py | Flask Blueprint: migration cost estimation — per-app breakdown, 7R strategy comparison, portfolio summary, ROI projection, custom calculator | GET /api/migration-cost/estimate, /comparison, /portfolio, /roi; POST /api/migration-cost/calculate | JSON cost/ROI data |

