# Tools Manifest

> Master list of all tools. Check here before writing a new script.

## Memory System
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Memory Read | tools/memory/memory_read.py | Load all memory (MEMORY.md + recent logs) | --format markdown | Formatted memory context |
| Memory Write | tools/memory/memory_write.py | Write to daily log + DB | --content, --type, --importance | Confirmation |
| Memory DB | tools/memory/memory_db.py | Keyword search on memory database | --action search, --query | Search results |
| Semantic Search | tools/memory/semantic_search.py | Vector similarity search (requires OpenAI key) | --query | Ranked results |
| Hybrid Search | tools/memory/hybrid_search.py | Combined keyword + semantic search | --query, --bm25-weight, --semantic-weight | Ranked results |
| Embed Memory | tools/memory/embed_memory.py | Generate embeddings for memory entries | --all | Confirmation |

## Database
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Init ICDEV DB | tools/db/init_icdev_db.py | Initialize ICDEV operational database (60 tables) | --db-path, --reset | Confirmation + table list |

## Audit Trail
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Audit Logger | tools/audit/audit_logger.py | Append-only audit trail writer (NIST AU) | --event, --actor, --action, --project | Entry ID |
| Audit Query | tools/audit/audit_query.py | Query audit trail (read-only) | --project, --type, --actor, --verify-completeness | Audit entries |
| Decision Recorder | tools/audit/decision_recorder.py | Record decisions with rationale | --project, --decision, --rationale | Entry ID |

## MCP Servers
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| MCP Base Server | tools/mcp/base_server.py | Base MCP server class (JSON-RPC 2.0 stdio) | — | — |
| MCP Core Server | tools/mcp/core_server.py | Project management MCP server | stdio | JSON-RPC responses |
| MCP Compliance Server | tools/mcp/compliance_server.py | Compliance artifact MCP server | stdio | JSON-RPC responses |
| MCP Builder Server | tools/mcp/builder_server.py | Code generation MCP server | stdio | JSON-RPC responses |
| MCP Infra Server | tools/mcp/infra_server.py | Infrastructure MCP server | stdio | JSON-RPC responses |
| MCP Knowledge Server | tools/mcp/knowledge_server.py | Knowledge base MCP server | stdio | JSON-RPC responses |
| MCP Maintenance Server | tools/mcp/maintenance_server.py | Maintenance audit MCP server (scan, check, audit, remediate) | stdio | JSON-RPC responses |
| MCP MBSE Server | tools/mcp/mbse_server.py | MBSE MCP server (import, trace, generate, sync, assess, snapshot) | stdio | JSON-RPC responses |
| MCP Modernization Server | tools/mcp/modernization_server.py | Modernization MCP server (10 tools: register, analyze, assess, plan, generate, track, migrate) | stdio | JSON-RPC responses |

## A2A Protocol
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| A2A Agent Server | tools/a2a/agent_server.py | Base A2A agent server (JSON-RPC 2.0 HTTPS) | — | — |
| A2A Client | tools/a2a/agent_client.py | Client for sending tasks to A2A agents | agent_url, skill_id, input | Task result |
| A2A Task Model | tools/a2a/task.py | Task, Artifact, StatusEvent dataclasses | — | — |
| Agent Registry | tools/a2a/agent_registry.py | Agent discovery and registration | — | Agent list |

## Project Management
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Project Create | tools/project/project_create.py | Create project with scaffolding | --name, --type, --classification | Project ID |
| Project List | tools/project/project_list.py | List all projects | --format | Project table |
| Project Status | tools/project/project_status.py | Project status report | --project, --format | Status report |
| Project Scaffold | tools/project/project_scaffold.py | Generate project directory structure | --project-id, --type | Directory tree |

## Compliance Engine
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| SSP Generator | tools/compliance/ssp_generator.py | System Security Plan generator (17 sections) | --project, --system-name | SSP document path |
| POAM Generator | tools/compliance/poam_generator.py | Plan of Action & Milestones generator | --project, --findings | POAM document path |
| STIG Checker | tools/compliance/stig_checker.py | STIG checklist auto-generation | --project, --stig-id, --target-type | Findings + checklist |
| SBOM Generator | tools/compliance/sbom_generator.py | CycloneDX SBOM generation | --project, --format | SBOM path |
| CUI Marker | tools/compliance/cui_marker.py | Apply CUI classification markings | --file, --directory | Marked file path |
| Control Mapper | tools/compliance/control_mapper.py | NIST 800-53 control mapping | --project, --control-families | Control matrix |
| NIST Lookup | tools/compliance/nist_lookup.py | NIST control reference lookup | --control-id | Control details |
| Compliance Status | tools/compliance/compliance_status.py | Compliance dashboard data (8 components incl. CSSP, SbD, IV&V) | --project | Status report |
| Classification Manager | tools/compliance/classification_manager.py | CUI/SECRET/TS markings, IL-to-baseline mapping, cross-domain controls | --impact-level, --classification, --banner, --code-header, --validate | Marking banners, baselines, validation |
| Crosswalk Engine | tools/compliance/crosswalk_engine.py | Multi-framework crosswalk query engine (FedRAMP, CMMC, 800-171, IL4/5/6) | --control, --framework, --project-id, --coverage, --gap-analysis | Crosswalk mappings + coverage |
| PI Compliance Tracker | tools/compliance/pi_compliance_tracker.py | SAFe PI compliance tracking: start/close PIs, velocity, burndown, reports | --project-id, --start-pi, --velocity, --burndown, --report | PI metrics + reports |

## FIPS 199/200 Security Categorization
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| FIPS 199 Categorizer | tools/compliance/fips199_categorizer.py | FIPS 199 security categorization with SP 800-60 information types, high watermark, CNSSI 1253 | --project-id, --add-type, --categorize, --list-catalog, --gate, --json | Categorization + baseline |
| FIPS 200 Validator | tools/compliance/fips200_validator.py | FIPS 200 minimum security requirements validation (17 areas) | --project-id, --gate, --json | Gap report + validation |

## CSSP Compliance (DI 8530.01)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| CSSP Assessor | tools/compliance/cssp_assessor.py | CSSP assessment across 5 functional areas | --project-id, --functional-area | Assessment results + report |
| CSSP Report Generator | tools/compliance/cssp_report_generator.py | CSSP certification report generation | --project-id, --output-dir | Report path |
| Incident Response Plan | tools/compliance/incident_response_plan.py | IR plan per CSSP SOC requirements | --project-id, --output-dir | IR plan path |
| SIEM Config Generator | tools/compliance/siem_config_generator.py | Splunk + ELK forwarding configs | --project-dir, --targets | Config file paths |
| CSSP Evidence Collector | tools/compliance/cssp_evidence_collector.py | Collect and index evidence for CSSP | --project-id, --project-dir | Evidence manifest |

## Xacta 360 Integration
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Xacta Client | tools/compliance/xacta/xacta_client.py | REST API client for Xacta 360 (PKI auth) | — | — |
| Xacta Export | tools/compliance/xacta/xacta_export.py | OSCAL JSON + CSV export for Xacta import | --project-id, --format | Export file paths |
| Xacta Sync | tools/compliance/xacta/xacta_sync.py | Sync orchestrator (API/export/hybrid) | --project-id, --mode | Sync results |

## Secure by Design (CISA SbD)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| SbD Assessor | tools/compliance/sbd_assessor.py | Secure by Design assessment (14 domains, 20 auto-checks) | --project-id, --domain | Assessment results + report |
| SbD Report Generator | tools/compliance/sbd_report_generator.py | SbD certification report generation | --project-id, --output-dir | Report path |

## IV&V (IEEE 1012)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| IV&V Assessor | tools/compliance/ivv_assessor.py | Independent Verification & Validation (9 process areas, 18 auto-checks) | --project-id, --process-area | Assessment results + report |
| IV&V Report Generator | tools/compliance/ivv_report_generator.py | IV&V certification report with CERTIFY/CONDITIONAL/DENY recommendation | --project-id, --output-dir | Report path |
| Traceability Matrix | tools/compliance/traceability_matrix.py | Requirements Traceability Matrix (RTM) with gap analysis | --project-id, --project-dir | RTM document + JSON |

## Multi-Framework Compliance (Phase 17)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| FedRAMP Assessor | tools/compliance/fedramp_assessor.py | FedRAMP Moderate/High baseline assessment engine | --project-id, --baseline | Assessment results + gate |
| FedRAMP Report Generator | tools/compliance/fedramp_report_generator.py | FedRAMP assessment report with control family scores | --project-id, --baseline | Report path |
| CMMC Assessor | tools/compliance/cmmc_assessor.py | CMMC Level 2/3 assessment (14 domains) | --project-id, --level | Assessment results + gate |
| CMMC Report Generator | tools/compliance/cmmc_report_generator.py | CMMC report with domain scores and 800-171 cross-ref | --project-id, --level | Report path |
| OSCAL Generator | tools/compliance/oscal_generator.py | NIST OSCAL 1.1.2 artifact generator (SSP, POA&M, AR, CD) | --project-id, --artifact, --format | OSCAL JSON/XML path |
| cATO Monitor | tools/compliance/cato_monitor.py | Continuous ATO evidence freshness and readiness monitoring | --project-id, --check-freshness, --readiness | Evidence status |
| cATO Scheduler | tools/compliance/cato_scheduler.py | Schedule-based evidence collection manager | --project-id, --run-due, --upcoming | Collection schedule |
| PI Compliance Tracker | tools/compliance/pi_compliance_tracker.py | SAFe PI-cadenced compliance tracking and velocity | --project-id, --pi, --velocity, --burndown | PI metrics |

## eMASS Integration
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| eMASS Client | tools/compliance/emass/emass_client.py | REST API client for eMASS (PKI auth) | — | — |
| eMASS Export | tools/compliance/emass/emass_export.py | Export controls, POA&M, artifacts in eMASS format | --project-id, --type | Export file paths |
| eMASS Sync | tools/compliance/emass/emass_sync.py | Sync orchestrator (API/export/hybrid) for eMASS | --project-id, --mode | Sync results |

## Builder (TDD)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Code Generator | tools/builder/code_generator.py | Generate code from specifications (Python, Java, Go, Rust, C#, TypeScript) | --project, --spec, --language | Generated file paths |
| Test Writer | tools/builder/test_writer.py | Generate BDD tests — Gherkin + language-specific step defs (6 languages) | --project, --requirement, --language | Feature file paths |
| Scaffolder | tools/builder/scaffolder.py | Project scaffolding from templates (6 languages) | --project, --type | Directory tree |
| Scaffolder Extended | tools/builder/scaffolder_extended.py | Java, Go, Rust, C#, TypeScript scaffold functions | (imported by scaffolder.py) | — |
| Language Support | tools/builder/language_support.py | Unified language registry, detection, CUI headers, dep file finder | --detect, --list, --profile | Language profiles |
| Linter | tools/builder/linter.py | Multi-language linting (flake8, eslint, checkstyle, golangci-lint, clippy, dotnet) | --project, --fix | Lint report |
| Formatter | tools/builder/formatter.py | Multi-language formatting (black, prettier, gofmt, rustfmt, dotnet-format) | --project | Formatted files |
| Agentic Fitness | tools/builder/agentic_fitness.py | Assess component fitness for agentic architecture (6-dimension scoring) | --spec, --project-id, --json | Fitness scorecard |
| App Blueprint | tools/builder/app_blueprint.py | Generate deployment blueprint from fitness scorecard | --fitness-scorecard, --user-decisions, --app-name, --json | Blueprint JSON |
| Child App Generator | tools/builder/child_app_generator.py | Generate mini-ICDEV clone child applications (12-step pipeline) | --blueprint, --output, --json | Generated app path |
| Claude MD Generator | tools/builder/claude_md_generator.py | Generate dynamic CLAUDE.md for child apps (Jinja2) | --blueprint, --output, --json | CLAUDE.md path |
| Goal Adapter | tools/builder/goal_adapter.py | Copy and adapt ICDEV goals for child applications | --source-goals, --output, --app-name, --json | Adapted goal paths |
| DB Init Generator | tools/builder/db_init_generator.py | Generate standalone DB init scripts for child apps | --blueprint, --output, --app-name, --json | DB init script path |

## Security Scanning
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Vuln Scanner | tools/security/vuln_scanner.py | Vulnerability scanning orchestrator | --project | Scan results |
| SAST Runner | tools/security/sast_runner.py | Multi-language SAST (Bandit, SpotBugs, gosec, clippy, ESLint-security, SecurityCodeScan) | --report, --gate | Findings |
| Dependency Auditor | tools/security/dependency_auditor.py | Multi-language dep audit (pip-audit, npm-audit, cargo-audit, govulncheck, OWASP DC, dotnet) | --report, --gate | Vulnerabilities |
| Secret Detector | tools/security/secret_detector.py | detect-secrets wrapper | --report, --gate | Secrets found |
| Container Scanner | tools/security/container_scanner.py | trivy container scanning | --image | Vulnerabilities |

## Infrastructure
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Terraform Generator | tools/infra/terraform_generator.py | Generate Terraform for GovCloud | --project | .tf files |
| Ansible Generator | tools/infra/ansible_generator.py | Generate Ansible playbooks | --project | .yml playbooks |
| K8s Generator | tools/infra/k8s_generator.py | Generate Kubernetes manifests | --project | .yaml manifests |
| Dockerfile Generator | tools/infra/dockerfile_generator.py | STIG-hardened Dockerfiles | --project | Dockerfile |
| Pipeline Generator | tools/infra/pipeline_generator.py | Generate .gitlab-ci.yml | --project | Pipeline file |
| Rollback Manager | tools/infra/rollback.py | Deployment rollback | --project, --environment | Rollback result |
| Infra Status | tools/infra/infra_status.py | Infrastructure status report | --project | Status |

## Knowledge & Self-Healing
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Knowledge Ingest | tools/knowledge/knowledge_ingest.py | Ingest patterns and lessons | --content, --type | Pattern ID |
| Pattern Detector | tools/knowledge/pattern_detector.py | Detect patterns from logs/metrics | --source, --data | Patterns found |
| Recommendation Engine | tools/knowledge/recommendation_engine.py | Generate recommendations via Bedrock | --context | Recommendations |
| Self-Heal Analyzer | tools/knowledge/self_heal_analyzer.py | Analyze failures and auto-correct | --failure-data | Healing result |

## Monitoring
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Log Analyzer | tools/monitor/log_analyzer.py | ELK/Splunk log analysis | --project, --time-range | Anomalies |
| Metric Collector | tools/monitor/metric_collector.py | Prometheus metric collection | --project | Metrics |
| Alert Correlator | tools/monitor/alert_correlator.py | Correlate alerts across sources | --time-window | Correlated incidents |
| Health Checker | tools/monitor/health_checker.py | Application health check | --url, --retries | Health status |

## Dashboard
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Web Dashboard | tools/dashboard/app.py | Flask web dashboard for business users | -- | Web UI on port 5000 |

## Testing Framework (Adapted from ADW)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Test Data Types | tools/testing/data_types.py | Pydantic models: TestResult, E2ETestResult, GateResult, etc. | — | — |
| Test Utilities | tools/testing/utils.py | JSON parsing, dual logging, safe subprocess env, run ID gen | — | — |
| Health Check | tools/testing/health_check.py | System validation (env, DB, deps, tools, MCP, git, Claude, Playwright) | --json, --project-id | Health report |
| Test Orchestrator | tools/testing/test_orchestrator.py | Full test pipeline: unit + BDD + E2E + gates with retry | --project-dir, --skip-e2e | Summary + state |
| E2E Runner | tools/testing/e2e_runner.py | E2E tests via native Playwright CLI or MCP fallback | --test-file, --discover, --run-all, --mode | E2E results |
| Playwright Config | playwright.config.ts | Playwright test runner config (Chromium/Firefox/WebKit, video, screenshots) | — | — |
| E2E Test: Dashboard | tests/e2e/dashboard_health.spec.ts | Native Playwright test: dashboard CUI banners + navigation | npx playwright test | Pass/fail + screenshots |
| E2E Test: Compliance | tests/e2e/compliance_artifacts.spec.ts | Native Playwright test: compliance artifact display | npx playwright test | Pass/fail + screenshots |
| E2E Test: Security | tests/e2e/security_scan_results.spec.ts | Native Playwright test: security scan + audit trail display | npx playwright test | Pass/fail + screenshots |

## CI/CD Integration (GitHub + GitLab)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| VCS Abstraction | tools/ci/modules/vcs.py | Unified GitHub (gh) + GitLab (glab) interface | Auto-detects platform | VCS instance |
| Agent Executor | tools/ci/modules/agent.py | Claude Code CLI subprocess invocation | AgentTemplateRequest | AgentPromptResponse |
| State Manager | tools/ci/modules/state.py | Persistent workflow state (agents/{run_id}/icdev_state.json) | run_id | ICDevState |
| Git Ops | tools/ci/modules/git_ops.py | Branch, commit, push, PR/MR creation | branch_name, message | success/error |
| Workflow Ops | tools/ci/modules/workflow_ops.py | Issue classification, branch gen, commit, PR helpers | issue_json, run_id | Results |
| Webhook Server | tools/ci/triggers/webhook_server.py | Flask server for GitHub + GitLab webhooks | POST /gh-webhook, /gl-webhook | Workflow launch |
| Poll Trigger | tools/ci/triggers/poll_trigger.py | Cron-based issue polling (20s interval) | Auto-detects platform | Workflow launch |
| ICDEV Plan | tools/ci/workflows/icdev_plan.py | Planning phase: classify, branch, plan | issue-number, run-id | Plan file |
| ICDEV Build | tools/ci/workflows/icdev_build.py | Implementation phase: implement plan | issue-number, run-id | Committed code |
| ICDEV Test | tools/ci/workflows/icdev_test.py | Testing phase: pytest, ruff, bandit, gates | issue-number, run-id | Test results |
| ICDEV Review | tools/ci/workflows/icdev_review.py | Code review against spec | issue-number, run-id | Review results |
| ICDEV Document | tools/ci/workflows/icdev_document.py | Documentation generation from changes | issue-number, run-id | Doc file |
| ICDEV Patch | tools/ci/workflows/icdev_patch.py | Quick fix workflow from issue content | issue-number, run-id | Patched code |
| ICDEV SDLC | tools/ci/workflows/icdev_sdlc.py | Complete lifecycle: plan+build+test+review | issue-number, run-id | All artifacts |
| Agent Model Test | tools/testing/test_agent_models.py | Verify opus/sonnet/haiku model availability | — | Pass/fail per model |

## Maintenance Audit
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Dependency Scanner | tools/maintenance/dependency_scanner.py | Inventory all deps across 6 languages, check latest versions, track staleness | --project-id, --language, --offline, --json | Dependency inventory |
| Vulnerability Checker | tools/maintenance/vulnerability_checker.py | Check dependencies against advisory databases, enforce SLA compliance | --project-id, --json | Vulnerability findings + SLA status |
| Maintenance Auditor | tools/maintenance/maintenance_auditor.py | Full audit lifecycle: scan + check + score + SLA + trend + CUI report | --project-id, --output-dir, --gate, --json | Audit report + score |
| Remediation Engine | tools/maintenance/remediation_engine.py | Auto-implement dependency fixes: version bumps, branch creation, test verification | --project-id, --auto, --dry-run, --json | Remediation actions |

## MBSE Integration (Phase 18)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| XMI Parser | tools/mbse/xmi_parser.py | Parse Cameo SysML v1.6 XMI exports into sysml_elements + relationships | --project-id, --file, --validate-only, --json | Import summary |
| ReqIF Parser | tools/mbse/reqif_parser.py | Parse DOORS NG ReqIF 1.2 exports into doors_requirements | --project-id, --file, --diff, --export, --json | Import summary |
| Digital Thread | tools/mbse/digital_thread.py | End-to-end traceability engine (req→model→code→test→control) | --project-id, subcommands (auto-link, coverage, orphans, gaps, report) | Coverage + trace |
| Model-to-Code Generator | tools/mbse/model_code_generator.py | Generate code scaffolding from SysML models (blocks→classes, activities→functions) | --project-id, --language, --output, --json | Generated files |
| Sync Engine | tools/mbse/sync_engine.py | Bidirectional model-code sync with SHA-256 drift detection | --project-id, detect-drift, sync-model-to-code, --json | Sync status |
| DES Assessor | tools/mbse/des_assessor.py | DoDI 5000.87 Digital Engineering Strategy compliance assessment (10 auto-checks) | --project-id, --project-dir, --json | DES score + gate |
| DES Report Generator | tools/mbse/des_report_generator.py | CUI-marked DES compliance report generation | --project-id, --output-dir | Report path |
| Model-NIST Mapper | tools/mbse/model_control_mapper.py | Map SysML elements to NIST 800-53 controls by keyword analysis | --project-id, --map-all, --json | Control mappings |
| PI Model Tracker | tools/mbse/pi_model_tracker.py | SAFe PI-cadenced model snapshots, velocity, burndown, comparison | --project-id, --pi, --snapshot, --compare, --json | PI metrics |
| MCP MBSE Server | tools/mcp/mbse_server.py | MCP server for MBSE tools (10 tools: import, trace, generate, sync, assess) | stdio | JSON-RPC responses |

## Application Modernization (Phase 19 — 7Rs Migration)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Legacy Analyzer | tools/modernization/legacy_analyzer.py | Static analysis engine (AST for Python, regex for Java/C#) — components, dependencies, APIs, frameworks, complexity | --register/--analyze, --project-id, --app-id, --source-path, --json | Analysis summary |
| Architecture Extractor | tools/modernization/architecture_extractor.py | Reverse-engineer architecture — call graph, component diagram, data flow, service boundaries | --app-id, --extract, --json | Architecture summary |
| Doc Generator | tools/modernization/doc_generator.py | Generate CUI-marked docs from analysis — API docs, data dictionary, component docs, dependency map | --app-id, --output-dir, --type, --json | File paths |
| 7R Assessor | tools/modernization/seven_r_assessor.py | Score all 7 Rs with weighted decision matrix, recommend strategy | --project-id, --app-id, --matrix, --weights, --json | Scored matrix |
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

## Requirements Intake (RICOAS Phase 1)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Intake Engine | tools/requirements/intake_engine.py | Conversational requirements intake — create/resume sessions, process turns, extract requirements | --project-id, --session-id, --message, --resume, --export, --json | Session + requirements |
| Decomposition Engine | tools/requirements/decomposition_engine.py | SAFe hierarchy decomposition (Epic > Capability > Feature > Story > Enabler) with WSJF scoring | --session-id, --level, --generate-bdd, --json | SAFe items |
| Gap Detector | tools/requirements/gap_detector.py | AI-powered gap/ambiguity detection against NIST coverage patterns | --session-id, --check-security, --check-compliance, --json | Gaps + recommendations |
| Document Extractor | tools/requirements/document_extractor.py | Upload SOW/CDD/CONOPS/SRD, extract structured requirements (shall/must/should) | --session-id, --upload, --extract, --document-id, --json | Extracted requirements |
| Readiness Scorer | tools/requirements/readiness_scorer.py | 5-dimension scoring: completeness, clarity, feasibility, compliance, testability | --session-id, --threshold, --trend, --json | Readiness score + trend |
| MCP Requirements Server | tools/mcp/requirements_server.py | MCP server for requirements tools (10 tools: intake, gaps, readiness, decompose, documents) | stdio | JSON-RPC responses |

## ATO Boundary Impact (RICOAS Phase 2)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Boundary Analyzer | tools/requirements/boundary_analyzer.py | 4-tier ATO boundary impact assessment (GREEN/YELLOW/ORANGE/RED) with RED alternative COA generation | --project-id, --system-id, --requirement-id, --generate-alternatives, --json | Impact tier + alternatives |

## Supply Chain Intelligence (RICOAS Phase 2)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Dependency Graph | tools/supply_chain/dependency_graph.py | Build/query supply chain dependency graph with upstream/downstream impact propagation | --project-id, --build-graph, --upstream, --downstream, --impact, --json | Graph + blast radius |
| ISA Manager | tools/supply_chain/isa_manager.py | ISA/MOU lifecycle tracking — create, expiring, review due, renew, revoke | --project-id, --create, --expiring, --review-due, --json | ISA status |
| SCRM Assessor | tools/supply_chain/scrm_assessor.py | NIST 800-161 supply chain risk assessment across 6 dimensions | --project-id, --vendor-id, --aggregate, --json | Risk score + tier |
| CVE Triager | tools/supply_chain/cve_triager.py | CVE triage with upstream/downstream blast radius and SLA tracking | --project-id, --triage, --sla-check, --propagate, --json | Triage + blast radius |
| MCP Supply Chain Server | tools/mcp/supply_chain_server.py | MCP server for boundary + supply chain tools (9 tools) | stdio | JSON-RPC responses |

## Digital Program Twin Simulation (RICOAS Phase 3)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Simulation Engine | tools/simulation/simulation_engine.py | 6-dimension what-if simulation (architecture, compliance, supply chain, schedule, cost, risk) | --project-id, --create-scenario, --run, --dimensions, --json | Simulation results |
| Monte Carlo | tools/simulation/monte_carlo.py | PERT/Monte Carlo schedule/cost/risk estimation (stdlib random, no numpy) | --scenario-id, --dimension, --iterations, --json | Percentiles + histogram |
| COA Generator | tools/simulation/coa_generator.py | Generate 3 COAs (Speed/Balanced/Comprehensive) + RED alternatives | --session-id, --generate-3-coas, --simulate, --compare, --json | COAs + comparison |
| Scenario Manager | tools/simulation/scenario_manager.py | Save, fork, compare, export, archive simulation scenarios | --scenario-id, --fork, --compare, --export, --json | Scenario operations |
| MCP Simulation Server | tools/mcp/simulation_server.py | MCP server for simulation tools (8 tools) | stdio | JSON-RPC responses |

## External Integration (RICOAS Phase 4)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Jira Connector | tools/integration/jira_connector.py | Bidirectional Jira sync — SAFe items map to Jira issue types (Epic/Story/Sub-task) | --project-id, --configure, --push, --pull, --json | Sync results |
| ServiceNow Connector | tools/integration/servicenow_connector.py | Bidirectional ServiceNow sync — requirements map to ServiceNow incidents/requests/changes | --project-id, --configure, --push, --pull, --json | Sync results |
| GitLab Connector | tools/integration/gitlab_connector.py | Bidirectional GitLab sync — SAFe items map to GitLab epics/issues/merge requests | --project-id, --configure, --push, --pull, --json | Sync results |
| DOORS Exporter | tools/integration/doors_exporter.py | Export requirements as ReqIF 1.2 for DOORS NG import | --session-id, --export-reqif, --output-path, --json | ReqIF file path |
| Approval Manager | tools/integration/approval_manager.py | Approval workflows for requirements packages, COA selection, boundary acceptance | --session-id, --submit, --review, --status, --json | Approval status |
| Traceability Builder | tools/requirements/traceability_builder.py | Full RTM: requirement > SysML > code > test > control > UAT with coverage analysis | --project-id, --build-rtm, --gap-analysis, --json | RTM + coverage % |
| MCP Integration Server | tools/mcp/integration_server.py | MCP server for integration tools (10 tools: Jira, ServiceNow, GitLab, DOORS, approval, RTM) | stdio | JSON-RPC responses |

## Agent Execution Framework (TAC-8 Phase A)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Agent Executor | tools/agent/agent_executor.py | Subprocess-based Claude Code CLI invocation with JSONL parsing, retry, audit | --prompt, --model, --max-retries, --timeout, --json | AgentPromptResponse |
| Agent Models | tools/agent/agent_models.py | Dataclasses: AgentPromptRequest, AgentPromptResponse, RetryCode enum | — | — |

## LLM Provider Abstraction (Vendor-Agnostic)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| LLM Provider Base | tools/llm/provider.py | ABC base classes (LLMProvider, EmbeddingProvider), vendor-agnostic LLMRequest/LLMResponse, message/tool format translators | — | — |
| LLM Router | tools/llm/router.py | Config-driven function→model routing with fallback chains, reads args/llm_config.yaml | function name | (provider, model_id, config) |
| Bedrock Provider | tools/llm/bedrock_provider.py | AWS Bedrock LLMProvider: Anthropic models, thinking/effort, tools, structured output, retry/backoff | LLMRequest | LLMResponse |
| Anthropic Provider | tools/llm/anthropic_provider.py | Direct Anthropic API LLMProvider via anthropic SDK | LLMRequest | LLMResponse |
| OpenAI-Compat Provider | tools/llm/openai_provider.py | OpenAI-compatible LLMProvider: OpenAI, Ollama, vLLM, Azure via configurable base_url | LLMRequest | LLMResponse |
| Embedding Provider | tools/llm/embedding_provider.py | Embedding providers: OpenAI, Bedrock Titan, Ollama (nomic-embed-text) | text | float[] |
| LLM Config | args/llm_config.yaml | Master config: providers, models, per-function routing chains, embedding config, pricing | — | — |

## Bedrock Client (Opus 4.6 Multi-Agent — Phase A)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Bedrock Client | tools/agent/bedrock_client.py | Bedrock-specific wrapper: invoke, streaming, tool loops, model fallback chain (Opus→Sonnet 4.5→Sonnet 3.5), adaptive thinking, effort parameter, structured outputs. For vendor-agnostic access use tools.llm instead. | --prompt, --model, --effort, --probe, --stream, --json | BedrockResponse |
| Token Tracker | tools/agent/token_tracker.py | Token usage/cost tracking per agent/project/task with multi-provider pricing from llm_config.yaml (falls back to bedrock_models.yaml) | --action summary/cost, --project-id, --agent-id, --json | Usage summary |

## Multi-Agent Orchestration (Opus 4.6 Multi-Agent — Phase B)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Team Orchestrator | tools/agent/team_orchestrator.py | DAG-based workflow engine: LLM task decomposition, TopologicalSorter + ThreadPoolExecutor parallel execution | --decompose, --execute, --workflow-id, --json | Workflow result |
| Skill Router | tools/agent/skill_router.py | Health-aware agent-skill routing: staleness check, least-loaded selection | --route-skill, --health, --routing-table | Agent routing |

## Agent Collaboration (Opus 4.6 Multi-Agent — Phase C)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Collaboration | tools/agent/collaboration.py | 5 patterns: reviewer, debate, consensus, veto, escalation | --pattern, --agent-ids, --project-id, --json | Pattern result |
| Authority | tools/agent/authority.py | Domain authority matrix (YAML): check_authority, record_veto, record_override | --check, --veto, --override, --history, --json | Veto status |
| Mailbox | tools/agent/mailbox.py | HMAC-SHA256 signed inter-agent messaging: send, broadcast, receive, verify | --send, --inbox, --verify, --json | Messages |
| Agent Memory | tools/agent/agent_memory.py | Project-scoped per-agent + team memory: store, recall, inject context, prune | --store, --recall, --inject, --prune, --json | Memory entries |

## Observability Hooks (TAC-8 Phase A)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Send Event | .claude/hooks/send_event.py | Shared utility: HMAC-signed event storage + SSE forwarding | session_id, hook_type, payload | Event ID |
| Post-Tool-Use Hook | .claude/hooks/post_tool_use.py | Log tool results to hook_events table (always exits 0) | tool_name, tool_input, tool_output | — |
| Notification Hook | .claude/hooks/notification.py | Log user notifications (always exits 0) | message | — |
| Stop Hook | .claude/hooks/stop.py | Capture session completion event (always exits 0) | session_id, reason | — |
| Subagent Stop Hook | .claude/hooks/subagent_stop.py | Log subagent task completion (always exits 0) | subagent_id, result | — |

## NLQ Compliance Queries (TAC-8 Phase B)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| NLQ Processor | tools/dashboard/nlq_processor.py | NLQ→SQL engine: schema extraction, Bedrock prompt, SQL validation, execution | query_text, actor | SQL results |
| SSE Manager | tools/dashboard/sse_manager.py | SSE connection manager: client tracking, event broadcasting, heartbeat | — | SSE stream |
| Events API | tools/dashboard/api/events.py | Blueprint: recent events, SSE stream, event ingest | GET/POST /api/events/* | Events |
| NLQ API | tools/dashboard/api/nlq.py | Blueprint: NLQ query, schema, history | POST /api/nlq/query | Query results |

## Git Worktree Parallel CI/CD (TAC-8 Phase C)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Worktree Manager | tools/ci/modules/worktree.py | Git worktree lifecycle: create (sparse checkout), list, cleanup, status | --create, --list, --cleanup, --status | WorktreeInfo |
| GitLab Task Monitor | tools/ci/triggers/gitlab_task_monitor.py | Poll GitLab issues for {{icdev: workflow}} tags, auto-trigger workflows | --interval, --dry-run, --once | Workflow launch |

## Framework Planning Commands (TAC-8 Phase D)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Plan Python | .claude/commands/plan_python.md | Python build plan: Flask/FastAPI, pytest, behave, bandit, pip-audit | $ARGUMENTS | Build plan |
| Plan Java | .claude/commands/plan_java.md | Java build plan: Spring Boot, Cucumber-JVM, checkstyle, SpotBugs | $ARGUMENTS | Build plan |
| Plan Go | .claude/commands/plan_go.md | Go build plan: net/http/Gin, godog, golangci-lint, gosec | $ARGUMENTS | Build plan |
| Plan Rust | .claude/commands/plan_rust.md | Rust build plan: Actix-web, cucumber-rs, clippy, cargo-audit | $ARGUMENTS | Build plan |
| Plan C# | .claude/commands/plan_csharp.md | C# build plan: ASP.NET Core, SpecFlow, SecurityCodeScan | $ARGUMENTS | Build plan |
| Plan TypeScript | .claude/commands/plan_typescript.md | TypeScript build plan: Express, cucumber-js, eslint-security | $ARGUMENTS | Build plan |

## SaaS Multi-Tenancy (Phase 21)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Platform DB | tools/saas/platform_db.py | Platform PostgreSQL/SQLite schema (tenants, users, api_keys, subscriptions, usage_records, audit_platform) | --init, --reset | Schema creation |
| Models | tools/saas/models.py | Pydantic models: Tenant, User, APIKey, Subscription, UsageRecord, enums, tier limits | — | — |
| Tenant Manager | tools/saas/tenant_manager.py | Tenant CRUD, provisioning lifecycle, DB creation, API key generation | --create, --list, --provision, --approve, --suspend, --delete | Tenant info |
| Auth Middleware | tools/saas/auth/middleware.py | Flask before_request middleware: credential extraction, tenant context, security headers | — | g.tenant_id, g.user_id |
| API Key Auth | tools/saas/auth/api_key_auth.py | API key validation: SHA-256 hash lookup, expiry/scope/status checks | Authorization header | Auth context |
| OAuth Auth | tools/saas/auth/oauth_auth.py | OAuth 2.0/OIDC JWT validation: decode, JWKS verify, tenant/user resolution | Authorization header | Auth context |
| CAC Auth | tools/saas/auth/cac_auth.py | CAC/PIV authentication: CN lookup from X-Client-Cert-CN header | Client cert header | Auth context |
| RBAC | tools/saas/auth/rbac.py | Role-based access control: 5 roles × 9 endpoint categories permission matrix | role, path, method | Allow/deny |
| API Gateway | tools/saas/api_gateway.py | Main Flask app: REST + MCP Streamable HTTP + auth + rate limiting + request logging | --port, --debug | Web server |
| REST API | tools/saas/rest_api.py | Flask Blueprint: tenants, users, keys, projects, compliance, security, builder, audit, usage | /api/v1/* | JSON responses |
| MCP Streamable HTTP | tools/saas/mcp_http.py | MCP Streamable HTTP transport (spec 2025-03-26): single endpoint, session-based | POST/GET/DELETE /mcp/v1/ | JSON + SSE |
| Rate Limiter | tools/saas/rate_limiter.py | Per-tenant rate limiting by subscription tier (in-memory, thread-safe) | tenant_id, tier | Allow/deny + headers |
| Request Logger | tools/saas/request_logger.py | Audit logging: every API call → usage_records + audit_platform | Flask hooks | Log entries |
| Tenant DB Adapter | tools/saas/tenant_db_adapter.py | Route existing tool DB calls to tenant's isolated database | tenant_id | DB path/connection |
| PG Schema | tools/saas/db/pg_schema.py | Full ICDEV schema (100+ tables) ported from SQLite to PostgreSQL DDL | --init | PG schema |
| DB Compat | tools/saas/db/db_compat.py | SQLite ↔ PostgreSQL compatibility: placeholder translation, row factory | engine type | DB connection |
| Connection Pool | tools/saas/db/connection_pool.py | Per-tenant PostgreSQL connection pooling (psycopg2 ThreadedConnectionPool) | tenant_id | Pooled connection |
| Delivery Engine | tools/saas/artifacts/delivery_engine.py | Push artifacts to tenant S3/Git/SFTP with audit trail | tenant_id, artifact_path | Delivery status |
| Artifact Signer | tools/saas/artifacts/signer.py | SHA-256 hash + RSA digital signature for compliance artifacts | file_path | Hash + signature |
| Bedrock Proxy | tools/saas/bedrock/bedrock_proxy.py | Route Bedrock LLM calls: BYOK (tenant's AWS) or ICDEV shared pool | tenant_id, prompt | LLM response |
| Token Metering | tools/saas/bedrock/token_metering.py | Track Bedrock token usage per tenant for billing/rate enforcement | tenant_id, tokens | Usage record |
| Tenant Portal | tools/saas/portal/app.py | Flask Blueprint: tenant admin web dashboard (login, dashboard, team, settings, keys) | /portal/* | Web UI |
| NS Provisioner | tools/saas/infra/namespace_provisioner.py | Create K8s namespace, network policies, resource quotas per tenant | --create, --slug, --il | Namespace YAML |
| Account Provisioner | tools/saas/infra/account_provisioner.py | Create AWS sub-accounts for IL5/IL6 tenants via Organizations | --provision, --slug | Account ID |
| License Validator | tools/saas/licensing/license_validator.py | Offline RSA-SHA256 license key validation (air-gap safe) | --validate, --info | License status |
| License Generator | tools/saas/licensing/license_generator.py | Admin tool: generate signed license keys for on-prem customers | --generate, --customer, --tier | License JSON |

## Marketplace (Phase 22)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Catalog Manager | tools/marketplace/catalog_manager.py | CRUD for marketplace assets and versions | --register/--list/--get/--add-version/--deprecate | Asset record JSON |
| Asset Scanner | tools/marketplace/asset_scanner.py | 7-gate security scanning pipeline (SAST, secrets, deps, CUI, SBOM, provenance, signature) | --asset-id, --version-id, --asset-path | Scan results JSON |
| Publish Pipeline | tools/marketplace/publish_pipeline.py | Orchestrate validate → scan → sign → publish/review | --asset-path, --asset-type, --tenant-id | Pipeline result JSON |
| Install Manager | tools/marketplace/install_manager.py | Install/update/uninstall assets with IL compatibility | --install/--uninstall/--update/--check-updates | Installation record |
| Search Engine | tools/marketplace/search_engine.py | Hybrid BM25 + semantic search (Ollama air-gapped) | --search query | Ranked results JSON |
| Review Queue | tools/marketplace/review_queue.py | Human review workflow for cross-tenant sharing | --submit/--review/--pending | Review record JSON |
| Provenance Tracker | tools/marketplace/provenance_tracker.py | Supply chain provenance recording and verification | --record/--get/--verify/--report | Provenance chain JSON |
| Compatibility Checker | tools/marketplace/compatibility_checker.py | IL + version + dependency compatibility checks | --asset-id, --consumer-il | Compatibility result |
| Federation Sync | tools/marketplace/federation_sync.py | Sync tenant-local ↔ central vetted registry | --promote/--pull/--status | Sync result JSON |
| Marketplace MCP | tools/mcp/marketplace_server.py | MCP server (11 tools, 2 resources) for marketplace | stdio | JSON-RPC 2.0 |

## Safety Hooks
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Pre-Tool-Use Hook | .claude/hooks/pre_tool_use.py | Blocks dangerous rm, .env access, audit modifications | tool_name, tool_input | Allow/block |
