# Tools Manifest

> Master list of all tools. Check here before writing a new script.

## Memory System
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Memory Read | tools/memory/memory_read.py | Load all memory (MEMORY.md + recent logs) | --format markdown | Formatted memory context |
| Memory Write | tools/memory/memory_write.py | Write to daily log + DB | --content, --type, --importance | Confirmation |
| Memory DB | tools/memory/memory_db.py | Keyword search on memory database | --action search, --query | Search results |
| Semantic Search | tools/memory/semantic_search.py | Vector similarity search (requires OpenAI key) | --query | Ranked results |
| Hybrid Search | tools/memory/hybrid_search.py | Combined keyword + semantic search, optional --time-decay flag for recency weighting | --query, --bm25-weight, --semantic-weight, --time-decay | Ranked results |
| Embed Memory | tools/memory/embed_memory.py | Generate embeddings for memory entries | --all | Confirmation |
| Time-Decay Scoring | tools/memory/time_decay.py | Exponential time-decay scoring for memory entries: per-type half-lives, importance resistance, combined relevance+recency+importance scoring (D147) | --score --entry-id, --rank --query, --top-k, --user-id, --json | Decay factors + ranked results |
| Auto-Capture | tools/memory/auto_capture.py | Auto-capture content from hooks into memory buffer with dedup (D181) | --content, --source, --type, --tool-name, --flush, --buffer-status, --user-id, --json | Capture/flush result |
| Maintenance Cron | tools/memory/maintenance_cron.py | Orchestrate memory maintenance: flush buffer, embed, prune, backup (D179-D182) | --all, --flush-buffer, --embed-unembedded, --prune-stale, --backup, --days, --json | Maintenance results |

## Database
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Init ICDEV DB | tools/db/init_icdev_db.py | Initialize ICDEV operational database (176 tables) — detects migration system (D150) | --db-path, --reset | Confirmation + table list |
| Migration Runner | tools/db/migration_runner.py | Lightweight DB migration framework (D150) — schema versioning, checksums, dual-engine | (library) | MigrationRunner class |
| Migrate CLI | tools/db/migrate.py | CLI wrapper for migration runner | --status, --up, --down, --create, --validate, --mark-applied, --all-tenants | Status / results |
| Backup Manager | tools/db/backup_manager.py | Database backup/restore with WAL-safe sqlite3.backup() API (D152) | (library) | BackupManager class |
| Backup CLI | tools/db/backup.py | CLI wrapper for backup manager | --backup, --restore, --verify, --list, --prune, --all, --tenants | Backup metadata / results |

## Resilience (D146-D149)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Circuit Breaker | tools/resilience/circuit_breaker.py | 3-state circuit breaker with ABC + InMemory backend (D146) | (library) | CircuitBreakerBackend |
| Retry | tools/resilience/retry.py | Exponential backoff + full jitter decorator (D147) | (library) | @retry decorator |
| Errors | tools/resilience/errors.py | Structured exception hierarchy (D148) | (library) | ICDevError hierarchy |
| Correlation | tools/resilience/correlation.py | Request-scoped correlation ID middleware (D149) | (library) | Flask middleware + get_correlation_id |

## Compatibility Utilities (D145)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Platform Utils | tools/compat/platform_utils.py | OS detection, temp/home/data dirs, UTF-8 console (D145) | (library) | IS_WINDOWS, IS_LINUX, etc. |
| Datetime Utils | tools/compat/datetime_utils.py | Cross-platform datetime helpers | (library) | UTC-safe datetime funcs |
| DB Utils | tools/compat/db_utils.py | Centralized DB path resolution with env var > explicit > default fallback chain | (library) | get_icdev_db_path(), get_memory_db_path(), get_platform_db_path() |

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
| MCP DevSecOps Server | tools/mcp/devsecops_server.py | DevSecOps/ZTA MCP server (12 tools: profile, maturity, pipeline, policy, mesh, segmentation, attestation, posture) | stdio | JSON-RPC responses |
| MCP Innovation Server | tools/mcp/innovation_server.py | Innovation Engine MCP server (10 tools: scan, score, triage, trends, generate, pipeline, status, introspect, competitive, standards) | stdio | JSON-RPC responses |
| MCP Context Server | tools/mcp/context_server.py | Semantic Layer MCP server (D277): CLAUDE.md section indexer, keyword search, role-tailored context, project/agent metadata | stdio | JSON-RPC responses |
| MCP Gateway Server | tools/mcp/gateway_server.py | Remote Command Gateway MCP server (5 tools: bind_user, list_bindings, revoke, send_command, status) | stdio | JSON-RPC responses |

## Innovation Engine (Phase 35 — D199-D208)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Web Scanner | tools/innovation/web_scanner.py | Scan GitHub, NVD, Stack Overflow, HN for innovation signals | --scan, --source, --all, --list-sources, --history, --json | Signals + storage results |
| Signal Ranker | tools/innovation/signal_ranker.py | 5-dimension weighted innovation scoring (D21 pattern) | --score, --score-all, --top, --calibrate, --json | Scores + breakdowns |
| Trend Detector | tools/innovation/trend_detector.py | Cross-signal pattern detection via keyword co-occurrence (D207) | --detect, --report, --velocity, --json | Trends + velocity |
| Triage Engine | tools/innovation/triage_engine.py | 5-stage compliance-first triage pipeline (classify, GOTCHA fit, boundary, compliance, dedup/license) | --triage, --triage-all, --summary, --json | Triage outcomes |
| Solution Generator | tools/innovation/solution_generator.py | Auto-generate solution specs from approved signals (D208) | --generate, --generate-all, --list, --status, --json | Solution specs |
| Innovation Manager | tools/innovation/innovation_manager.py | Main orchestrator + daemon mode for full pipeline | --run, --discover, --score, --triage, --generate, --daemon, --status, --json | Pipeline results |
| Introspective Analyzer | tools/innovation/introspective_analyzer.py | Internal telemetry mining (D203) — gate failures, unused tools, slow pipelines, knowledge gaps | --analyze, --type, --all, --json | Analysis findings |
| Competitive Intel | tools/innovation/competitive_intel.py | Competitor feature monitoring (D205) — gap analysis against ICDEV capabilities | --scan, --gap-analysis, --report, --json | Competitive gaps |
| Standards Monitor | tools/innovation/standards_monitor.py | Standards body change tracking (D204) — NIST, CISA, DoD, FedRAMP, ISO | --check, --body, --report, --assess, --json | Standards updates |
| Innovation Config | args/innovation_config.yaml | Configuration: sources, scoring weights, triage rules, scheduling, competitive intel, standards monitoring | (data) | YAML config |

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
| Manifest Loader | tools/project/manifest_loader.py | Parse/validate icdev.yaml, apply IL defaults, env overrides (D189, D193) | --dir, --file, --validate, --json | Normalized config + errors/warnings |
| Validate Manifest | tools/project/validate_manifest.py | CLI validator for icdev.yaml (thin wrapper) | --file, --dir, --json | Valid/invalid + errors |
| Session Context Builder | tools/project/session_context_builder.py | Build session context for Claude Code — project, compliance, profile, workflows (D190) | --dir, --db, --format, --init, --json | Markdown or JSON context |

## DX Companion — Universal AI Coding Tool Support (D194-D198)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Companion CLI | tools/dx/companion.py | Single entry point: detect tools, generate instructions, MCP configs, translate skills (D194) | --setup, --detect, --list, --platforms, --write, --json | Summary + file paths |
| Tool Detector | tools/dx/tool_detector.py | Detect installed AI coding tools from env, config dirs, config files (D197) | --dir, --json | Detected tools + confidence |
| Instruction Generator | tools/dx/instruction_generator.py | Generate instruction files for 9 AI tools from Jinja2 templates (D195) | --platform, --all, --write, --json | Instruction file content + paths |
| MCP Config Generator | tools/dx/mcp_config_generator.py | Translate .mcp.json to tool-specific MCP config formats (D196) | --platform, --all, --write, --json | Config file content + paths |
| Skill Translator | tools/dx/skill_translator.py | Translate Claude Code skills to Codex/Copilot/Cursor formats (D198) | --platform, --all, --skills, --write, --json | Translated skill content + paths |
| Companion Registry | args/companion_registry.yaml | Declarative registry of 10 supported AI coding tools (D194) | (data) | Tool definitions |

## SDK
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| ICDEV Client | tools/sdk/icdev_client.py | Thin Python SDK wrapping CLI tools via subprocess (D191) | (library) | ICDEVClient class |

## CI/CD Pipeline
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Pipeline Config Generator | tools/ci/pipeline_config_generator.py | Generate GitHub Actions/GitLab CI from icdev.yaml (D192) | --dir, --platform, --write, --dry-run, --json | YAML config + metadata |

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
| Crosswalk Engine | tools/compliance/crosswalk_engine.py | Dual-hub crosswalk engine (NIST+ISO 27001): FedRAMP, CMMC, 800-171, IL4/5/6, CJIS, HIPAA, HITRUST, SOC 2, PCI DSS, ISO 27001 | --control, --framework, --project-id, --coverage, --gap-analysis | Crosswalk mappings + coverage |
| PI Compliance Tracker | tools/compliance/pi_compliance_tracker.py | SAFe PI compliance tracking: start/close PIs, velocity, burndown, reports | --project-id, --start-pi, --velocity, --burndown, --report | PI metrics + reports |

## FIPS 199/200 Security Categorization
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| FIPS 199 Categorizer | tools/compliance/fips199_categorizer.py | FIPS 199 security categorization with SP 800-60 information types, high watermark, CNSSI 1253 | --project-id, --add-type, --categorize, --list-catalog, --gate, --json | Categorization + baseline |
| FIPS 200 Validator | tools/compliance/fips200_validator.py | FIPS 200 minimum security requirements validation (17 areas) | --project-id, --gate, --json | Gap report + validation |

## Universal Compliance Platform (Phase 23)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Universal Classification Mgr | tools/compliance/universal_classification_manager.py | Composable data markings for 10 categories (CUI, PHI, PCI, CJIS, etc.) | --banner, --code-header, --detect, --validate, --add-category | Composite banners, headers, validation |
| Base Assessor | tools/compliance/base_assessor.py | ABC base class for all Wave 1+ assessors (crosswalk, gate, CLI) | (imported by subclasses) | Assessment + gate results |
| CJIS Assessor | tools/compliance/cjis_assessor.py | FBI CJIS Security Policy v5.9.4 assessment | --project-id, --gate, --json | CJIS compliance + gate |
| HIPAA Assessor | tools/compliance/hipaa_assessor.py | HIPAA Security Rule (45 CFR 164) assessment | --project-id, --gate, --json | HIPAA compliance + gate |
| HITRUST Assessor | tools/compliance/hitrust_assessor.py | HITRUST CSF v11 assessment | --project-id, --gate, --json | HITRUST compliance + gate |
| SOC 2 Assessor | tools/compliance/soc2_assessor.py | SOC 2 Type II Trust Service Criteria assessment | --project-id, --gate, --json | SOC 2 compliance + gate |
| PCI DSS Assessor | tools/compliance/pci_dss_assessor.py | PCI DSS v4.0 assessment | --project-id, --gate, --json | PCI DSS compliance + gate |
| ISO 27001 Assessor | tools/compliance/iso27001_assessor.py | ISO/IEC 27001:2022 assessment (international hub) | --project-id, --gate, --json | ISO 27001 compliance + gate |
| Resolve Marking | tools/compliance/resolve_marking.py | Central classification marking resolver — determines banner, code header, grep pattern per project (ADR D132) | --project-id, --json, --banner-only, --code-header LANG, --check-required | Marking dict (marking_required, banner, code_header, grep_pattern, vision_assertion) |
| Compliance Detector | tools/compliance/compliance_detector.py | Auto-detect applicable frameworks from data categories | --project-id, --apply, --confirm, --json | Detected frameworks |
| Multi-Regime Assessor | tools/compliance/multi_regime_assessor.py | Unified multi-framework assessment + gate + minimal controls | --project-id, --gate, --minimal-controls, --json | Unified report + prioritized controls |

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
| OSCAL Generator | tools/compliance/oscal_generator.py | NIST OSCAL 1.1.2 artifact generator (SSP, POA&M, AR, CD) | --project-id, --artifact, --format, --deep-validate | OSCAL JSON/XML path |
| OSCAL Tools | tools/compliance/oscal_tools.py | OSCAL ecosystem orchestrator: deep validation, format conversion, profile resolution, catalog operations (D302-D305) | --detect, --validate, --convert, --resolve-profile, --catalog-lookup | Detection/validation/conversion results |
| OSCAL Catalog Adapter | tools/compliance/oscal_catalog_adapter.py | Unified NIST OSCAL + ICDEV catalog reader with fallback chain (D304) | --lookup, --list, --stats, --family | Control data, catalog stats |
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
| Child App Generator | tools/builder/child_app_generator.py | Generate mini-ICDEV clone child applications (16-step pipeline) | --blueprint, --output, --json | Generated app path |
| Claude MD Generator | tools/builder/claude_md_generator.py | Generate dynamic CLAUDE.md for child apps (Jinja2) | --blueprint, --output, --json | CLAUDE.md path |
| Goal Adapter | tools/builder/goal_adapter.py | Copy and adapt ICDEV goals for child applications | --source-goals, --output, --app-name, --json | Adapted goal paths |
| DB Init Generator | tools/builder/db_init_generator.py | Generate standalone DB init scripts for child apps | --blueprint, --output, --app-name, --json | DB init script path |
| Dev Profile Manager | tools/builder/dev_profile_manager.py | 5-layer cascade dev profiles (Platform→Tenant→Program→Project→User) with version immutability, role-based locks, LLM injection (D183-D188) | --scope, --scope-id, --create, --get, --update, --resolve, --lock, --inject, --diff, --rollback, --json | Profile + cascade |
| Profile Detector | tools/builder/profile_detector.py | Auto-detect dev profile from repo analysis or natural language text (D185 advisory-only) | --repo-path, --text, --json | Detected dimensions |
| Profile MD Generator | tools/builder/profile_md_generator.py | Generate PROFILE.md from resolved dev profile via Jinja2 (D186) | --scope, --scope-id, --output, --store, --json | PROFILE.md path |
| GOTCHA Validator | tools/builder/gotcha_validator.py | Validate GOTCHA framework compliance for child apps (6 layers + 4 meta checks) | --project-dir, --json, --human, --gate | Validation report |

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
| Heartbeat Daemon | tools/monitor/heartbeat_daemon.py | Proactive daemon: 7 configurable checks (cATO evidence, agent health, CVE SLA, pending intake, failing tests, expiring ISAs, memory maintenance) (D141-D142) | --once, --check, --status, --json | Check results + notifications |
| Auto-Resolver | tools/monitor/auto_resolver.py | Webhook-triggered auto-resolution: alert → normalize → analyze → fix → PR → notify (D143-D145) | --analyze, --resolve, --alert-file, --source, --dry-run, --json | Resolution log + PR URL |

## Dashboard
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Web Dashboard | tools/dashboard/app.py | Flask web dashboard with role-based views, wizard, quick paths | --port, --debug | Web UI on port 5000 |
| UX Helpers | tools/dashboard/ux_helpers.py | Jinja2 filters (friendly_time, glossary), error recovery dict, quick paths, wizard steps | register_ux_filters(app) | Template filters + globals |
| UX JavaScript | tools/dashboard/static/js/ux.js | Client-side glossary tooltips, timestamp formatting, accessibility, notifications, progress pipeline | Auto-init on DOMContentLoaded | ICDEV namespace |
| UX Stylesheet | tools/dashboard/static/css/ux.css | Tooltip, pipeline, wizard, quick path, breadcrumb, notification, accessibility styles | — | CSS |
| Charts Library | tools/dashboard/static/js/charts.js | Zero-dependency SVG chart library: sparkline, line, bar, donut, gauge with tooltips and animation | ICDEV.lineChart(), ICDEV.barChart(), ICDEV.donutChart(), ICDEV.gaugeChart() | SVG charts |
| Table Interactivity | tools/dashboard/static/js/tables.js | Table search, column sort, column filter, CSV export, row counter | Auto-init on DOMContentLoaded | Enhanced tables |
| Onboarding Tour | tools/dashboard/static/js/tour.js | Interactive overlay walkthrough for first-visit users, 6-step spotlight tour | ICDEV.startTour(), ICDEV.resetTour() | Tour overlay |
| Live Dashboard | tools/dashboard/static/js/live.js | Real-time SSE auto-refresh: connection status, smart debounced updates, event toasts | ICDEV.connectSSE(), ICDEV.disconnectSSE() | Live updates |
| Batch Operations JS | tools/dashboard/static/js/batch.js | Batch workflow UI: catalog display, execution progress, step status polling | ICDEV.batchStartBatch(id, projectId) | Batch progress UI |
| Batch Operations API | tools/dashboard/api/batch.py | Flask blueprint: batch execute/status/catalog endpoints, background subprocess runner | POST /api/batch/execute, GET /api/batch/status | JSON batch status |
| Keyboard Shortcuts | tools/dashboard/static/js/shortcuts.js | Chord-based navigation (g+key), direct shortcuts, help modal overlay | ICDEV.showShortcutsHelp() | Navigation + help modal |
| Mermaid Integration | tools/dashboard/static/js/mermaid-icdev.js | ICDEV Mermaid module: dark theme, click handlers, editor, SVG export, auto-init | ICDEV.renderMermaid(), ICDEV.initMermaidEditor(), ICDEV.exportMermaidSVG() | Rendered diagrams |
| Diagram Definitions | tools/dashboard/diagram_definitions.py | Centralized Mermaid diagram catalog: 18 diagrams across 4 categories with role filtering | get_catalog_for_role(), get_diagram() | Diagram data |
| Diagrams API | tools/dashboard/api/diagrams.py | Blueprint: list/get diagram definitions, role-filtered catalog | GET /api/diagrams/, GET /api/diagrams/<id> | JSON diagram data |

## CLI Output Formatting
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Output Formatter | tools/cli/output_formatter.py | Human-friendly CLI output: colored tables, banners, scores, pipelines, key-value pairs | --human flag on any tool | Formatted terminal output |

## Testing Framework (Adapted from ADW)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Test Data Types | tools/testing/data_types.py | Pydantic models: TestResult, E2ETestResult, GateResult, etc. | — | — |
| Test Utilities | tools/testing/utils.py | JSON parsing, dual logging, safe subprocess env, run ID gen | — | — |
| Health Check | tools/testing/health_check.py | System validation (env, DB, deps, tools, MCP, git, Claude, Playwright) | --json, --project-id | Health report |
| Test Orchestrator | tools/testing/test_orchestrator.py | Full test pipeline: unit + BDD + E2E + gates with retry | --project-dir, --skip-e2e | Summary + state |
| E2E Runner | tools/testing/e2e_runner.py | E2E tests via native Playwright CLI or MCP fallback | --test-file, --discover, --run-all, --mode, --validate-screenshots | E2E results |
| Screenshot Validator | tools/testing/screenshot_validator.py | Vision-based screenshot validation using LLM (Ollama LLaVA / Claude / GPT-4o) | --image, --assert, --batch-dir, --check | Pass/fail + explanation |
| Integration Smoke Test | tools/testing/smoke_test.py | Verify all CLI tools are importable and --help works after refactors | --json, --quick, --verbose | N tools tested, N passed |
| CLI Fuzz Test | tools/testing/fuzz_cli.py | Fuzz CLI tools with malformed inputs to catch crashes | --json, --tools, --discover | N tools fuzzed, 0 crashes |
| Acceptance Validator | tools/testing/acceptance_validator.py | V&V gate: validate plan acceptance criteria against test evidence + DOM content checks | --plan, --test-results, --base-url, --pages, --json | AcceptanceReport JSON |
| UI Analyzer | tools/modernization/ui_analyzer.py | Legacy UI screenshot analysis for 7R migration scoring | --image, --image-dir, --app-id, --store, --score-only | UI complexity score + analysis |
| Diagram Extractor | tools/mbse/diagram_extractor.py | Vision-based SysML diagram extraction from screenshots | --image, --diagram-type, --project-id, --store, --validate | Elements + relationships |
| Diagram Validator | tools/compliance/diagram_validator.py | Compliance diagram validation (SSP, network zone, ATO boundary) | --image, --type, --expected-components, --expected-zones | Pass/fail per check |
| Production Audit | tools/testing/production_audit.py | 30-check pre-production readiness audit across 6 categories (platform, security, compliance, integration, performance, documentation) | --json, --human, --stream, --gate, --category | AuditReport JSON + exit code |
| Production Remediate | tools/testing/production_remediate.py | Auto-fix audit blockers using 3-tier confidence model (auto-fix >= 0.7, suggest 0.3-0.7, escalate < 0.3) | --auto, --dry-run, --check-id, --category, --skip-audit, --json, --human, --stream | RemediationReport JSON + exit code |
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

## Spec-Kit Patterns (D156–D161)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Spec Quality Checker | tools/requirements/spec_quality_checker.py | "Unit tests for English" — validates spec markdown against configurable checklist (D156), annotates with [NEEDS CLARIFICATION] markers (D160) | --spec-file, --spec-dir, --annotate, --strip-markers, --json | Quality score + check results |
| Consistency Analyzer | tools/requirements/consistency_analyzer.py | Cross-artifact consistency validation — acceptance vs testing, phases vs tasks, NIST vs ATO, file existence (D157) | --spec-file, --spec-dir, --fix-suggestions, --json | Consistency score + results |
| Constitution Manager | tools/requirements/constitution_manager.py | Per-project immutable principles management with DoD defaults — add, list, remove, validate specs against principles (D158) | --project-id, --add, --list, --validate, --load-defaults, --json | Principles + validation |
| Clarification Engine | tools/requirements/clarification_engine.py | Impact × Uncertainty prioritized clarification questions for specs and intake sessions (D159) | --spec-file, --session-id, --max-questions, --json | Prioritized questions + clarity score |
| Spec Organizer | tools/requirements/spec_organizer.py | Per-feature spec directories with [P] parallel task markers — init, migrate, register, status (D160, D161) | --init, --migrate, --migrate-all, --status, --list, --register, --json | Spec directories + status |

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

## Agent Execution Framework (Phase 39)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Agent Executor | tools/agent/agent_executor.py | Subprocess-based Claude Code CLI invocation with JSONL parsing, retry, audit | --prompt, --model, --max-retries, --timeout, --json | AgentPromptResponse |
| Agent Models | tools/agent/agent_models.py | Dataclasses: AgentPromptRequest, AgentPromptResponse, RetryCode enum | — | — |
| Skill Selector | tools/agent/skill_selector.py | Selective skill injection: keyword-based category matching, file detection, context-aware skill/goal/context loading (D146) | --query, --detect, --project-dir, --resolve, --format-context, --json | Matched categories + commands + goals |

## LLM Provider Abstraction (Vendor-Agnostic)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| LLM Provider Base | tools/llm/provider.py | ABC base classes (LLMProvider, EmbeddingProvider), vendor-agnostic LLMRequest/LLMResponse, message/tool format translators | — | — |
| LLM Router | tools/llm/router.py | Config-driven function→model routing with fallback chains, reads args/llm_config.yaml | function name | (provider, model_id, config) |
| Bedrock Provider | tools/llm/bedrock_provider.py | AWS Bedrock LLMProvider: Anthropic models, thinking/effort, tools, structured output, retry/backoff | LLMRequest | LLMResponse |
| Anthropic Provider | tools/llm/anthropic_provider.py | Direct Anthropic API LLMProvider via anthropic SDK | LLMRequest | LLMResponse |
| OpenAI-Compat Provider | tools/llm/openai_provider.py | OpenAI-compatible LLMProvider: OpenAI, vLLM, Azure via configurable base_url | LLMRequest | LLMResponse |
| Ollama Native Provider | tools/llm/ollama_provider.py | Native Ollama REST API provider using /api/chat — faster than OpenAI-compat for local models, native vision support | LLMRequest | LLMResponse |
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
| Prompt Chain Executor | tools/agent/prompt_chain_executor.py | Declarative LLM-to-LLM sequential reasoning chains: YAML-driven prompt templates with $INPUT/$ORIGINAL/$STEP{x} variable substitution, per-step agent routing via LLMRouter (D-PC-1 through D-PC-3) | --chain, --input, --list, --dry-run, --history, --project-id, --json | Chain execution result |
| Skill Router | tools/agent/skill_router.py | Health-aware agent-skill routing: staleness check, least-loaded selection, dispatcher mode awareness (D-DISP-1) | --route-skill, --health, --routing-table, --project-id | Agent routing |
| Dispatcher Mode | tools/agent/dispatcher_mode.py | Dispatcher-only orchestrator mode: restricts orchestrator to delegation tools, blocks domain tool execution, per-project DB overrides, tool-to-agent redirect mapping (Phase 61, D-DISP-1) | --status, --enable, --disable, --check-tool, --project-id, --json, --human | Dispatcher status |

## Agent Collaboration (Opus 4.6 Multi-Agent — Phase C)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Collaboration | tools/agent/collaboration.py | 5 patterns: reviewer, debate, consensus, veto, escalation | --pattern, --agent-ids, --project-id, --json | Pattern result |
| Authority | tools/agent/authority.py | Domain authority matrix (YAML): check_authority, record_veto, record_override | --check, --veto, --override, --history, --json | Veto status |
| Mailbox | tools/agent/mailbox.py | HMAC-SHA256 signed inter-agent messaging: send, broadcast, receive, verify | --send, --inbox, --verify, --json | Messages |
| Agent Memory | tools/agent/agent_memory.py | Project-scoped per-agent + team memory: store, recall, inject context, prune | --store, --recall, --inject, --prune, --json | Memory entries |

## Observability Hooks (Phase 39)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Send Event | .claude/hooks/send_event.py | Shared utility: HMAC-signed event storage + SSE forwarding | session_id, hook_type, payload | Event ID |
| Post-Tool-Use Hook | .claude/hooks/post_tool_use.py | Log tool results to hook_events table (always exits 0) | tool_name, tool_input, tool_output | — |
| Notification Hook | .claude/hooks/notification.py | Log user notifications (always exits 0) | message | — |
| Stop Hook | .claude/hooks/stop.py | Capture session completion event (always exits 0) | session_id, reason | — |
| Subagent Stop Hook | .claude/hooks/subagent_stop.py | Log subagent task completion (always exits 0) | subagent_id, result | — |

## NLQ Compliance Queries (Phase 40)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| NLQ Processor | tools/dashboard/nlq_processor.py | NLQ→SQL engine: schema extraction, Bedrock prompt, SQL validation, execution | query_text, actor | SQL results |
| SSE Manager | tools/dashboard/sse_manager.py | SSE connection manager: client tracking, event broadcasting, heartbeat | — | SSE stream |
| Events API | tools/dashboard/api/events.py | Blueprint: recent events, SSE stream, event ingest | GET/POST /api/events/* | Events |
| NLQ API | tools/dashboard/api/nlq.py | Blueprint: NLQ query, schema, history | POST /api/nlq/query | Query results |

## Git Worktree Parallel CI/CD (Phase 41)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Worktree Manager | tools/ci/modules/worktree.py | Git worktree lifecycle: create (sparse checkout), list, cleanup, status | --create, --list, --cleanup, --status | WorktreeInfo |
| GitLab Task Monitor | tools/ci/triggers/gitlab_task_monitor.py | Poll GitLab issues for {{icdev: workflow}} tags, auto-trigger workflows | --interval, --dry-run, --once | Workflow launch |

## Framework Planning Commands (Phase 42)
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
| OpenAPI Spec | tools/saas/openapi_spec.py | OpenAPI 3.0.3 spec generator — 23 endpoints, 13 schemas (D153) | --output, --compact | OpenAPI JSON |
| Swagger UI | tools/saas/swagger_ui.py | Flask Blueprint: /api/v1/docs (Swagger UI) + /api/v1/openapi.json (D153) | /api/v1/docs | HTML + JSON |
| Metrics | tools/saas/metrics.py | Prometheus metrics collector — dual-backend: prometheus_client or stdlib fallback (D154) | (library) | MetricsCollector |
| Metrics Blueprint | tools/saas/metrics_blueprint.py | Flask Blueprint: GET /metrics — Prometheus text exposition (D154) | /metrics | text/plain |

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

## DevSecOps & Zero Trust Architecture (Phase 24-25)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| DevSecOps Profile Manager | tools/devsecops/profile_manager.py | DevSecOps profile CRUD, maturity detection, assessment | --project-id, --create, --detect, --assess, --update, --json | Profile + maturity level |
| ZTA Maturity Scorer | tools/devsecops/zta_maturity_scorer.py | 7-pillar ZTA maturity scoring (DoD ZTA Strategy) | --project-id, --pillar, --all, --trend, --json | Pillar scores + aggregate |
| Pipeline Security Generator | tools/devsecops/pipeline_security_generator.py | Profile-driven GitLab CI security stage generation | --project-id, --json | YAML security stages |
| Policy Generator | tools/devsecops/policy_generator.py | Kyverno/OPA policy-as-code generation (pod security, registry, RBAC) | --project-id, --engine kyverno\|opa, --json | Policy YAML/Rego |
| Attestation Manager | tools/devsecops/attestation_manager.py | Image signing (Cosign/Notation) + SBOM attestation (SLSA Level 3) | --project-id, --generate, --verify, --json | Signing config + attestation |
| Service Mesh Generator | tools/devsecops/service_mesh_generator.py | Istio/Linkerd service mesh config generation (mTLS, AuthzPolicy) | --project-id, --mesh istio\|linkerd, --json | Service mesh YAML |
| ZTA Terraform Generator | tools/devsecops/zta_terraform_generator.py | ZTA security modules (GuardDuty, SecurityHub, WAF, Config Rules) | --project-path, --modules, --json | .tf files |
| Network Segmentation Generator | tools/devsecops/network_segmentation_generator.py | Namespace isolation + per-pod microsegmentation NetworkPolicies | --project-path, --namespaces, --services, --json | NetworkPolicy YAML |
| PDP Config Generator | tools/devsecops/pdp_config_generator.py | PDP/PEP configuration (Zscaler, Palo Alto, DISA ICAM) | --project-id, --pdp-type, --mesh, --json | PDP/PEP config |
| NIST 800-207 Assessor | tools/compliance/nist_800_207_assessor.py | NIST SP 800-207 ZTA compliance assessment (BaseAssessor pattern) | --project-id, --gate, --json | Assessment + gate |
| MCP DevSecOps Server | tools/mcp/devsecops_server.py | MCP server for DevSecOps/ZTA tools (12 tools) | stdio | JSON-RPC responses |

## DoD MOSA — Modular Open Systems Approach (Phase 26)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| MOSA Assessor | tools/compliance/mosa_assessor.py | MOSA compliance assessment (25 requirements, 6 families, BaseAssessor pattern) | --project-id, --gate, --json | Assessment + gate |
| ICD Generator | tools/mosa/icd_generator.py | Interface Control Document generation per external interface | --project-id, --interface-id, --all, --json | ICD markdown + DB |
| TSP Generator | tools/mosa/tsp_generator.py | Technical Standard Profile generation (auto-detect standards) | --project-id, --json | TSP markdown + DB |
| Modular Design Analyzer | tools/mosa/modular_design_analyzer.py | Static analysis: coupling, cohesion, interface coverage, circular deps | --project-dir, --project-id, --store, --json | Metrics + score |
| MOSA Code Enforcer | tools/mosa/mosa_code_enforcer.py | MOSA violation scanner (coupling, boundary, missing specs) | --project-dir, --fix-suggestions, --json | Violations list |

## Dashboard Auth, Activity Feed, BYOK & Usage Tracking (Phase 30)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Dashboard Auth | tools/dashboard/auth.py | API key auth, session mgmt, RBAC (5 roles), CLI bootstrap, auth logging | API key / session | User context |
| Dashboard BYOK | tools/dashboard/byok.py | BYOK key management: Fernet AES-256 encrypt/decrypt, key resolution (user→dept→env→config) | user_id, provider, key | Encrypted storage |
| WebSocket Manager | tools/dashboard/websocket.py | Flask-SocketIO init, room-based broadcast, graceful fallback to HTTP polling | app | SocketIO instance |
| Activity Feed API | tools/dashboard/api/activity.py | Merged audit_trail + hook_events UNION ALL, filters, polling, stats | source, event_type, actor | Merged events JSON |
| Admin API | tools/dashboard/api/admin.py | User CRUD, API key gen/revoke, auth log query (admin-only) | user data, key_id | User/key records |
| Usage API | tools/dashboard/api/usage.py | Per-user token aggregation, per-provider breakdown, time-series, cost estimates | user_id, days | Usage stats JSON |
| Activity Feed JS | tools/dashboard/static/js/activity.js | WebSocket + HTTP polling client, filter state, CSV export | (browser) | Real-time UI |

## Modular Installation (Phase 33)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Installer | tools/installer/installer.py | Interactive wizard + profile-based modular deployment with compliance posture configuration | --interactive, --profile, --add-module, --add-compliance, --upgrade, --status, --json | Installation manifest |
| Module Registry | tools/installer/module_registry.py | Module definition registry: dependencies, DB table groups, validation | --validate, --list, --json | Module graph |
| Compliance Configurator | tools/installer/compliance_configurator.py | Compliance posture selection and framework activation | --list-postures, --apply, --json | Compliance config |
| Platform Setup | tools/installer/platform_setup.py | Platform artifact generation (Docker Compose, K8s RBAC, .env, Helm values) | --generate docker\|k8s-rbac\|env\|helm-values, --modules | Platform artifacts |

## AI Security (Phase 37 — MITRE ATLAS, D209-D231)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Prompt Injection Detector | tools/security/prompt_injection_detector.py | 5-category prompt injection detection (role hijacking, delimiter, instruction injection, data exfil, encoded payloads) with confidence scoring and DB logging | --text, --file, --project-dir, --gate, --json | Detection results + action |
| AI Telemetry Logger | tools/security/ai_telemetry_logger.py | Append-only AI interaction logging (SHA-256 hashes, tokens, latency), anomaly detection, behavioral drift detection (D218, D257) | --summary, --anomalies, --drift, --agent-id, --project-id, --json | Telemetry stats + drift alerts |
| ATLAS Assessor | tools/compliance/atlas_assessor.py | MITRE ATLAS v5.4.0 compliance assessment (34 mitigations, BaseAssessor pattern D116) | --project-id, --gate, --json | Assessment + gate |
| OWASP LLM Assessor | tools/compliance/owasp_llm_assessor.py | OWASP LLM Top 10 v2025 assessment (10 risk categories, BaseAssessor pattern) | --project-id, --gate, --json | Assessment + gate |
| NIST AI RMF Assessor | tools/compliance/nist_ai_rmf_assessor.py | NIST AI RMF 1.0 assessment (4 functions: Govern/Map/Measure/Manage, BaseAssessor pattern) | --project-id, --gate, --json | Assessment + gate |
| ISO 42001 Assessor | tools/compliance/iso42001_assessor.py | ISO/IEC 42001:2023 AI Management System assessment (18 requirements, international hub bridge) | --project-id, --gate, --json | Assessment + gate |
| ATLAS Red Team Scanner | tools/security/atlas_red_team.py | Opt-in adversarial testing (D219): 6 ATLAS techniques + 6 behavioral techniques (BRT-001 to BRT-006) | --project-id, --atlas-red-team, --behavioral, --brt-technique, --json | Red team results |
| AI BOM Generator | tools/security/ai_bom_generator.py | AI Bill of Materials: scan LLM providers, AI frameworks, MCP servers, store in ai_bom table with risk assessment | --project-id, --project-dir, --gate, --json | AI BOM + gate |
| ATLAS Report Generator | tools/compliance/atlas_report_generator.py | MITRE ATLAS compliance report: mitigation coverage, technique exposure, OWASP crossref, gap analysis, remediation | --project-id, --output-path, --json | ATLAS report |
| Tool Chain Validator | tools/security/tool_chain_validator.py | Sliding-window tool-call-sequence validator with fnmatch pattern matching, burst detection, append-only logging (D258) | --check, --rules, --gate, --json | Violations + gate |
| Agent Output Validator | tools/security/agent_output_validator.py | Post-tool output content safety checker — classification leaks, sensitive data, oversized responses (D259) | --text, --file, --gate, --json | Violations + action |
| Agent Trust Scorer | tools/security/agent_trust_scorer.py | Dynamic inter-agent trust scoring with decay/recovery from 5 signal sources (D260) | --score, --check, --history, --all, --gate, --json | Trust level + gate |
| MCP Tool Authorizer | tools/security/mcp_tool_authorizer.py | Per-tool RBAC for MCP servers — deny-first with fnmatch wildcards, 5 roles (D261) | --check --role --tool, --list --role, --validate, --json | Allow/deny + validation |
| OWASP Agentic Assessor | tools/compliance/owasp_agentic_assessor.py | OWASP Agentic AI security assessment (17 checks across 8 gaps, BaseAssessor pattern D264) | --project-id, --gate, --json | Assessment + gate |
| OWASP Agentic Threats Catalog | context/compliance/owasp_agentic_threats.json | T01-T17 agentic AI threat catalog with NIST 800-53 crosswalk and ATLAS technique mappings | (data) | JSON catalog |
| ATLAS Mitigations Catalog | context/compliance/atlas_mitigations.json | 34 MITRE ATLAS mitigations with NIST 800-53 crosswalk and technique mappings | (data) | JSON catalog |
| ATLAS Techniques Catalog | context/compliance/atlas_techniques.json | 84+ ATLAS techniques by tactic with sub-techniques and mitigations | (data) | JSON catalog |
| OWASP LLM Top 10 Catalog | context/compliance/owasp_llm_top10.json | 10 OWASP LLM risk categories with NIST crosswalk and ATLAS technique refs | (data) | JSON catalog |
| SAFE-AI Controls Catalog | context/compliance/safeai_controls.json | 50 AI-affected NIST 800-53 controls across 13 families with AI concern narratives | (data) | JSON catalog |
| NIST AI RMF Catalog | context/compliance/nist_ai_rmf.json | 12 NIST AI RMF requirements across 4 functions with NIST 800-53 crosswalk | (data) | JSON catalog |
| ISO 42001 Catalog | context/compliance/iso42001_controls.json | 18 ISO 42001 requirements (10 clauses + 8 Annex A) with dual hub crosswalk | (data) | JSON catalog |

## Evolutionary Intelligence (Phase 36 — D209-D214)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Child Registry | tools/registry/child_registry.py | Enhanced child app registry with capabilities CRUD, status tracking | --register, --list, --get, --add-capability, --json | Child record |
| Telemetry Collector | tools/registry/telemetry_collector.py | Pull-based health telemetry from child heartbeat endpoints (D210) | --collect, --child-id, --summary, --json | Health data |
| Genome Manager | tools/registry/genome_manager.py | Versioned capability genome with semver + SHA-256 content hash (D209) | --get, --create, --diff, --rollback, --history, --verify, --json | Genome version |
| Capability Evaluator | tools/registry/capability_evaluator.py | 7-dimension scoring: universality, compliance_safety, risk, evidence, novelty, cost, security_assessment (REQ-36-020 + Phase 37) | --evaluate, --capability-data, --json | Score + outcome |
| Staging Manager | tools/registry/staging_manager.py | Git worktree isolation for testing capabilities (D211, 72-hour expiry) | --create, --test, --check-compliance, --destroy, --list, --json | Staging env |
| Propagation Manager | tools/registry/propagation_manager.py | Deploy capabilities to children with HITL approval (REQ-36-040, D214) | --prepare, --approve, --execute, --rollback, --status, --list, --json | Propagation log |
| Absorption Engine | tools/registry/absorption_engine.py | 72-hour stability window before genome absorption (D212) | --check, --absorb, --candidates, --json | Absorption result |
| Learning Collector | tools/registry/learning_collector.py | Process child-reported learned behaviors (D213) | --ingest, --evaluate, --unevaluated, --json | Behavior records |
| Cross-Pollinator | tools/registry/cross_pollinator.py | Broker capabilities between children via parent (HITL required) | --find, --propose, --execute, --json | Pollination result |

## Cloud-Agnostic Architecture (Phase 38 — D223-D231)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Cloud Mode Manager | tools/cloud/cloud_mode_manager.py | Cloud mode orchestrator — status, validation, readiness checks for commercial/government/on_prem/air_gapped (D232) | --status, --validate, --eligible, --check-readiness, --json | Mode validation |
| CSP Provider Factory | tools/cloud/provider_factory.py | Config-driven CSP factory from cloud_config.yaml — lazy instantiation, per-service override | service name | Provider instance |
| Secrets Provider | tools/cloud/secrets_provider.py | ABC + 5 implementations (AWS, Azure, GCP, OCI, Local) for secret management | get/put/list/delete | Secret data |
| Storage Provider | tools/cloud/storage_provider.py | ABC + 5 implementations (S3, Blob, GCS, OCI Object, Local) for object storage | upload/download/list/delete | Storage data |
| KMS Provider | tools/cloud/kms_provider.py | ABC + 5 implementations (AWS KMS, Azure KV, GCP Cloud KMS, OCI Vault, Local Fernet) for encryption | encrypt/decrypt/generate_key | Encrypted data |
| Monitoring Provider | tools/cloud/monitoring_provider.py | ABC + 5 implementations (CloudWatch, Azure Monitor, Cloud Monitoring, OCI, Local) for metrics/logs | send_metric/send_log | Metrics/logs |
| IAM Provider | tools/cloud/iam_provider.py | ABC + 5 implementations (AWS IAM, Entra ID, Cloud IAM, OCI, Local) for identity | create_role/check_permission | IAM data |
| Registry Provider | tools/cloud/registry_provider.py | ABC + 5 implementations (ECR, ACR, Artifact Registry, OCIR, Local) for container images | list/push/pull | Image data |
| CSP Health Checker | tools/cloud/csp_health_checker.py | Health check all CSP services, integrates with heartbeat daemon (D230) | --check-all, --json | Service statuses |
| CSP Region Validator | tools/cloud/region_validator.py | CSP Region Validator — compliance-driven deployment validation (D234). Validates CSP regions hold required certifications before deployment. | validate/eligible/deployment-check/list, --csp, --region, --frameworks, --impact-level, --json | Validation results |
| CSP Monitor | tools/cloud/csp_monitor.py | Autonomous CSP service monitor — scans feeds, diffs registry, generates innovation signals (D239) | --scan --all, --diff, --status, --daemon, --json | Signals + changes |
| CSP Changelog | tools/cloud/csp_changelog.py | Human-readable changelog with per-change-type recommendations (D241) | --generate, --summary, --days, --format, --json | Changelog report |
| Cloud Config | args/cloud_config.yaml | Master config: provider, region, IL, per-service CSP overrides (D225) | (data) | YAML config |
| CSP Monitor Config | args/csp_monitor_config.yaml | CSP monitoring config: sources, signals, diff engine, scheduling (D239) | (data) | YAML config |
| CSP Service Registry | context/cloud/csp_service_registry.json | Baseline CSP service catalog: 45+ services, compliance programs, regions (D240) | (data) | JSON registry |
| Azure OpenAI Provider | tools/llm/azure_openai_provider.py | Azure OpenAI Service LLM provider with government endpoints | LLMRequest | LLMResponse |
| Vertex AI Provider | tools/llm/vertex_ai_provider.py | Google Vertex AI LLM provider with Assured Workloads | LLMRequest | LLMResponse |
| OCI GenAI Provider | tools/llm/oci_genai_provider.py | Oracle OCI Generative AI LLM provider | LLMRequest | LLMResponse |
| IBM watsonx.ai Provider | tools/llm/ibm_watsonx_provider.py | IBM watsonx.ai LLM provider — Granite, Llama models via watsonx.ai SDK (D238). | LLMRequest | LLMResponse |
| Terraform Generator Azure | tools/infra/terraform_generator_azure.py | Azure Government Terraform (VNet, AKS, Azure PG, Blob, Key Vault) | --project-path, --json | .tf files |
| Terraform Generator GCP | tools/infra/terraform_generator_gcp.py | GCP Government Terraform (VPC, GKE, Cloud SQL, GCS, Secret Manager) | --project-path, --json | .tf files |
| Terraform Generator OCI | tools/infra/terraform_generator_oci.py | OCI Government Terraform (VCN, OKE, Autonomous DB, Object Storage, Vault) | --project-path, --json | .tf files |
| Terraform Generator IBM | tools/infra/terraform_generator_ibm.py | IBM Cloud Terraform generator — VPC, IKS, PostgreSQL, COS, Key Protect with CUI headers. | --project-id, --region, --json | .tf files |
| Terraform Generator On-Prem | tools/infra/terraform_generator_onprem.py | On-premises Terraform generator — self-managed K8s, Docker Compose, local PostgreSQL. | --project-id, --target k8s\|docker, --json | .tf / docker-compose files |

## Cross-Language Translation (Phase 43 — D242-D256)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Source Extractor | tools/translation/source_extractor.py | Phase 1: AST/regex → language-agnostic IR (JSON). Per-language extractors (Python AST, Java/Go/Rust/C#/TS regex). Detects concurrency, error handling, idioms, framework annotations | --source-path, --language, --output-ir, --project-id, --json | IR JSON |
| Type Checker | tools/translation/type_checker.py | Phase 2: Validate type-compatibility of function signatures between source/target type systems (D253, Amazon Oxidizer) | --ir-file, --source-language, --target-language, --json | Compatibility report |
| Code Translator | tools/translation/code_translator.py | Phase 3: LLM-assisted chunk translation with feature mapping rules (D247), pass@k candidates (D254). Post-order dependency traversal (D244). Mock-and-continue on failure (D256) | --ir-file, --source-language, --target-language, --output-dir, --candidates, --json | Translated units JSON |
| Project Assembler | tools/translation/project_assembler.py | Phase 4: Scaffold target project (pom.xml/go.mod/Cargo.toml/etc.), write translated files, apply CUI headers, generate build file | --translated-file, --source-language, --target-language, --output-dir, --json | Project files |
| Translation Validator | tools/translation/translation_validator.py | Phase 5: 8-check validation (syntax, lint, round-trip IR, API surface, type coverage, complexity, compliance, feature mapping). Compiler-feedback repair loop (D255) | --ir-file, --translated-file, --source-language, --target-language, --json | Validation report |
| Translation Manager | tools/translation/translation_manager.py | Full pipeline orchestrator. Supports --extract-only, --translate-only, --validate-only, --dry-run, --compliance-bridge, --candidates k | --source-path, --source-language, --target-language, --output-dir, --project-id, --json | Pipeline result |
| Test Translator | tools/translation/test_translator.py | Translate test files between frameworks (pytest↔JUnit↔testing↔cargo_test↔xUnit↔Jest). BDD .feature files preserved; step definitions translated (D250) | --source-test-dir, --source-language, --target-language, --output-dir, --ir-file, --json | Translated tests |
| Dependency Mapper | tools/translation/dependency_mapper.py | Map cross-language package equivalents from declarative JSON table (D246). LLM suggestion for unknowns (advisory only) | --source-language, --target-language, --imports, --json | Mapped dependencies |
| Feature Map Loader | tools/translation/feature_map.py | Load and apply 3-part feature mapping rules (D247): syntactic pattern → NL description → static validation | (library) | Feature rules |

## Remote Command Gateway (Phase 28 — D133-D140)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Gateway Agent | tools/gateway/gateway_agent.py | Remote command reception from 5 channels (Telegram, Slack, Teams, Mattermost, internal chat), 8-gate security chain, IL-aware response filtering | --port 8458 | Flask server |
| User Binder | tools/gateway/user_binder.py | Pre-provision user bindings (air-gapped mode), binding ceremony, revocation | --provision, --list, --revoke, --json | Binding records |

## Innovation Adaptation (Phase 44 — D257-D279)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Chat Manager | tools/dashboard/chat_manager.py | Multi-stream parallel chat: thread-per-context, max 5/user, message queue, mid-stream intervention (D257-D260, D265-D267) | (library) | ChatManager class |
| Chat API | tools/dashboard/api/chat.py | Flask Blueprint: create/list/send/intervene/resume/delete chat contexts | /api/chat/* | JSON chat data |
| Chat JS | tools/dashboard/static/js/chat.js | Unified multi-stream + RICOAS chat UI with intervention controls and real-time updates | (browser) | Chat UI |
| State Tracker | tools/dashboard/state_tracker.py | Dirty-tracking state push: per-client version counters, debounced SSE, incremental updates (D268-D270) | (library) | StateTracker class |
| Phase Loader | tools/dashboard/phase_loader.py | Load and render phase registry data for dashboard phases page | (library) | Phase data |
| Extension Manager | tools/extensions/extension_manager.py | Active extension hook system: 10 hook points, behavioral/observational tiers, layered override (project > tenant > default) (D261-D264) | (library) | ExtensionManager class |
| History Compressor | tools/memory/history_compressor.py | 3-tier history compression: current topic 50%, historical 30%, bulk 20%, topic boundary detection, LLM/truncation fallback (D271-D274) | --context-id, --budget, --json | Compressed history |
| Memory Consolidation | tools/memory/memory_consolidation.py | AI-driven memory consolidation: hybrid search → LLM decision (MERGE/REPLACE/KEEP_SEPARATE/UPDATE/SKIP), Jaccard fallback (D276) | --consolidate, --dry-run, --json | Consolidation log |
| Code Pattern Scanner | tools/security/code_pattern_scanner.py | Dangerous pattern detection across 6 languages (Python, Java, Go, Rust, C#, TypeScript), declarative YAML patterns (D278) | --scan, --project-dir, --language, --gate, --json | Pattern findings + gate |
| Register External Patterns | tools/innovation/register_external_patterns.py | Register Agent Zero + InsForge patterns as innovation signals with 5-dimension scoring (D279) | --register-all, --status, --score-all, --json | Registration results |
| Shared Schemas | tools/schemas/ | stdlib dataclass models (ProjectStatus, AgentHealth, AuditEvent, etc.) with validate_output() and wrap_mcp_response() (D275) | (library) | Schema classes |
| Context Indexer | tools/mcp/context_indexer.py | CLAUDE.md section indexer by ## headers for semantic layer MCP delivery (D277) | (library) | Section index |

## Observability, Traceability & Explainable AI (Phase 46)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Tracer ABC | tools/observability/tracer.py | Span/Tracer ABCs, NullTracer, NullSpan, ProxyTracer, set_content_tag() (D280) | (library) | Tracer classes |
| SQLite Tracer | tools/observability/sqlite_tracer.py | Writes spans to otel_spans table — air-gapped default backend (D280) | (library) | SQLiteTracer class |
| OTel Tracer | tools/observability/otel_tracer.py | Wraps opentelemetry-api/sdk with OTLP exporter — production backend (D280) | (library) | OTelTracer class |
| Trace Context | tools/observability/trace_context.py | W3C traceparent parse/generate, contextvars propagation (D281) | (library) | TraceparentContext class |
| GenAI Attributes | tools/observability/genai_attributes.py | OTel GenAI semantic convention constants for LLM spans (D286) | (library) | Attribute constants |
| Instrumentation | tools/observability/instrumentation.py | @traced() decorator for auto-span creation on functions (D284) | (library) | Decorator |
| MLflow Exporter | tools/observability/mlflow_exporter.py | Batch export SQLite spans to MLflow REST API (D283) | --export, --status, --json | Export results |
| Prov Recorder | tools/observability/provenance/prov_recorder.py | W3C PROV entity/activity/relation recording, span callbacks (D287) | (library) | ProvRecorder class |
| Prov Query | tools/observability/provenance/prov_query.py | Lineage queries — backward ("what produced this?") and forward (D287) | --entity-id, --direction, --json | Lineage graph |
| Prov Export | tools/observability/provenance/prov_export.py | Export provenance graph as W3C PROV-JSON for interoperability (D287) | --project-id, --json | PROV-JSON |
| AgentSHAP | tools/observability/shap/agent_shap.py | Monte Carlo Shapley value tool attribution analysis (D288) | --trace-id, --iterations, --json | Shapley values |
| SHAP Reporter | tools/observability/shap/shap_reporter.py | JSON/markdown/dashboard report generation for SHAP results (D288) | (library) | Reports |
| XAI Assessor | tools/compliance/xai_assessor.py | Explainable AI compliance assessor — 10 automated checks (D289) | --project-id, --gate, --json | Assessment results |
| XAI Requirements | context/compliance/xai_requirements.json | XAI requirements catalog (NIST AI RMF + DoD RAI + ISO 42001) | (data) | Requirements JSON |
| Observability Config | args/observability_tracing_config.yaml | Tracer backend, sampling, retention, content policy, PROV/SHAP settings (D290) | (config) | YAML config |
| Observability MCP | tools/mcp/observability_server.py | MCP server: trace_query, trace_summary, prov_lineage, prov_export, shap_analyze, xai_assess | (server) | 6 tools, 2 resources |
| Unified MCP Gateway | tools/mcp/unified_server.py | Unified MCP gateway (D301): aggregates all 225 tools from 18 servers + 55 new tools into one process with lazy module loading | (server) | 225 tools, 6 resources |
| Tool Registry | tools/mcp/tool_registry.py | Declarative registry mapping tool name to (module, handler, schema) for unified gateway | (data) | Python dict |
| Gap Handlers | tools/mcp/gap_handlers.py | 55 handler functions for CLI tools not previously exposed via MCP (translation, dx, cloud, registry, security, testing, installer) | (handlers) | Python functions |
| Registry Generator | tools/mcp/generate_registry.py | Utility to auto-generate tool_registry.py by introspecting all 18 MCP server modules | (utility) | Python script |
| Traces API | tools/dashboard/api/traces.py | Flask API Blueprint for trace, provenance, and XAI endpoints | (api) | REST endpoints |
| Traces Page | tools/dashboard/templates/traces.html | Trace explorer: stat grid, trace list, span waterfall SVG | (template) | HTML page |
| Provenance Page | tools/dashboard/templates/provenance.html | Provenance viewer: entity/activity tables, lineage query | (template) | HTML page |
| XAI Page | tools/dashboard/templates/xai.html | XAI dashboard: assessment runner, coverage gauge, SHAP chart | (template) | HTML page |

## Code Intelligence (Phase 52 — D331-D337)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Code Analyzer | tools/analysis/code_analyzer.py | AST self-analysis: per-function cyclomatic/cognitive complexity, nesting, params, LOC, smell detection, maintainability scoring (D331, D333, D337) | --project-dir, --file, --project-id, --store, --trend, --json, --human | Metrics JSON |
| Runtime Feedback | tools/analysis/runtime_feedback.py | Test-to-source correlation: JUnit XML parsing, stdout fallback, per-function health scoring (D332, D334) | --xml, --stdout, --project-id, --health, --function, --json | Feedback JSON |
| Code Quality API | tools/dashboard/api/code_quality.py | Flask Blueprint: summary stats, top-complex functions, smell distribution, trend data, runtime feedback, scan trigger | /api/code-quality/* | REST endpoints |
| Code Quality Page | tools/dashboard/templates/code_quality.html | Dashboard: stat grid (7 metrics), SVG trend chart, smell bar chart, complex functions table, runtime feedback table | (template) | HTML page |
| Code Quality Config | args/code_quality_config.yaml | Smell thresholds, maintainability weights (D337), audit thresholds, scan exclusion dirs | (config) | YAML config |

## AI Governance Integration (Phase 50)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| AI Governance Scorer | tools/requirements/ai_governance_scorer.py | Score AI governance readiness (6 components) for 7th readiness dimension | project_id, conn/db_path | JSON score + gaps |
| AI Governance Chat Extension | tools/extensions/builtins/010_ai_governance_chat.py | Chat hook: detect AI keywords, check governance gaps, inject advisory messages | chat context dict | context + governance_advisory |
| AI Governance Config | args/ai_governance_config.yaml | Intake detection keywords, chat governance, readiness weights, auto-trigger rules | (config) | YAML config |

## FedRAMP 20x KSI + OWASP ASI (Phase 53)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| FedRAMP 20x KSI Generator | tools/compliance/fedramp_ksi_generator.py | Generate Key Security Indicators (KSIs) for FedRAMP 20x authorization. Maps ICDEV evidence to 61 KSI schemas. | --project-id, --ksi-id, --all, --json | KSI evidence manifest |
| FedRAMP Auth Packager | tools/compliance/fedramp_authorization_packager.py | Bundle OSCAL SSP + KSI evidence into FedRAMP 20x authorization package | --project-id, --output-dir, --json | Authorization bundle |
| FedRAMP 20x API | tools/dashboard/api/fedramp_20x.py | Blueprint: stats, KSI list, generate, package | /api/fedramp-20x/* | REST endpoints |
| FedRAMP 20x Page | tools/dashboard/templates/fedramp_20x.html | Dashboard: stat-grid + KSI table + package status | (template) | HTML page |
| KSI Schemas | context/compliance/fedramp_20x_ksi_schemas.json | 61 KSI definitions (id, title, family, evidence_sources, nist_crosswalk) | (catalog) | JSON catalog |
| OWASP ASI Assessor | tools/compliance/owasp_asi_assessor.py | BaseAssessor for OWASP ASI01-ASI10 agentic AI risks. Maps 10 ASI risks to ICDEV controls via NIST 800-53 crosswalk. | --project-id, --json, --gate | Assessment JSON |
| OWASP ASI Catalog | context/compliance/owasp_agentic_asi.json | 10 ASI risk definitions with NIST crosswalk | (catalog) | JSON catalog |

## SWFT/SLSA + Cross-Phase Orchestration (Phase 54)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| SLSA Attestation Generator | tools/compliance/slsa_attestation_generator.py | Generate SLSA v1.0 provenance statements and VEX documents from build pipeline evidence | --project-id, --generate, --verify, --vex, --json | SLSA provenance + VEX |
| SWFT Evidence Bundler | tools/compliance/swft_evidence_bundler.py | Bundle DoD SWFT evidence package (SLSA, SBOM, VEX, scan results) | --project-id, --output-dir, --json | SWFT bundle |
| Workflow Composer | tools/orchestration/workflow_composer.py | Declarative cross-phase workflow engine using YAML templates + TopologicalSorter DAG | --template, --project-id, --dry-run, --list, --json | Workflow execution plan + results |
| ATO Workflow Template | args/workflow_templates/ato_acceleration.yaml | Workflow: categorize → assess → SSP → POAM → SBOM | (template) | YAML workflow |
| Security Workflow Template | args/workflow_templates/security_hardening.yaml | Workflow: SAST → deps → secrets → OWASP → ATLAS | (template) | YAML workflow |
| Compliance Workflow Template | args/workflow_templates/full_compliance.yaml | Workflow: detect → multi-regime assess → crosswalk | (template) | YAML workflow |
| Build Workflow Template | args/workflow_templates/build_deploy.yaml | Workflow: scaffold → test → build → lint → deploy | (template) | YAML workflow |

## A2A v0.3 + MCP OAuth (Phase 55)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| A2A Agent Card Generator | tools/agent/a2a_agent_card_generator.py | Generate v0.3 Agent Cards with capabilities, protocolVersion, tasks/sendSubscribe | --all, --agent-id, --json | Agent Cards JSON |
| A2A Discovery Server | tools/agent/a2a_discovery_server.py | Agent discovery endpoint serving /.well-known/agent.json for all 15 agents | (server) | JSON-RPC discovery |
| MCP OAuth | tools/saas/mcp_oauth.py | OAuth 2.1 + HMAC offline + JWT token verification for MCP transport. Elicitation handler. Task manager. | MCPOAuthVerifier, MCPElicitationHandler, MCPTaskManager | Token verification, elicitation, tasks |

## Compliance Evidence Auto-Collection + Lineage (Phase 56)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Evidence Collector | tools/compliance/evidence_collector.py | Universal evidence auto-collection across 14 compliance frameworks. DB query + file scan. | --project-id, --project-dir, --framework, --freshness, --list-frameworks, --json | Evidence manifest |
| Evidence API | tools/dashboard/api/evidence.py | Blueprint: evidence stats, collect, freshness check, framework list | /api/evidence/* | REST endpoints |
| Evidence Page | tools/dashboard/templates/evidence.html | Dashboard: evidence inventory, freshness status, collect trigger | (template) | HTML page |
| Lineage API | tools/dashboard/api/lineage.py | Blueprint: artifact lineage DAG (digital thread + provenance + audit trail + SBOM), stats | /api/lineage/* | REST endpoints |
| Lineage Page | tools/dashboard/templates/lineage.html | Dashboard: SVG DAG artifact visualization, color-coded by source | (template) | HTML page |

## EU AI Act + Platform One (Phase 57)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| EU AI Act Classifier | tools/compliance/eu_ai_act_classifier.py | BaseAssessor for EU AI Act (Regulation 2024/1689) risk classification. 12 requirements via ISO 27001 bridge. | --project-id, --json, --gate | Classification JSON |
| EU AI Act Catalog | context/compliance/eu_ai_act_annex_iii.json | 12 high-risk requirements, 8 Annex III categories, 4 risk levels with NIST crosswalk | (catalog) | JSON catalog |
| Iron Bank Generator | tools/infra/ironbank_metadata_generator.py | Generate Platform One / Iron Bank hardening_manifest.yaml and container_approval.json for DoD Iron Bank submission. Language auto-detection. | --project-id, --project-dir, --output-dir, --generate, --validate, --json | Hardening manifest + approval record |

## GovCon Intelligence (Phase 59)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| SAM.gov Scanner | tools/govcon/sam_scanner.py | Poll SAM.gov Opportunities API v2. Extracts opportunities by NAICS, notice type. Stores in sam_gov_opportunities. | --scan, --naics, --list-cached, --json | Opportunity JSON |
| Requirement Extractor | tools/govcon/requirement_extractor.py | Extract shall/must/will statements from RFP descriptions. Domain-classify (9 domains). Cluster by keyword fingerprint (D364). | --extract-all, --patterns, --domain, --json | Requirements + patterns JSON |
| Capability Mapper | tools/govcon/capability_mapper.py | Map requirement patterns to ICDEV capability catalog. Compute coverage scores (L/M/N). | --map-all, --coverage, --gaps, --json | Compliance matrix JSON |
| Gap Analyzer | tools/govcon/gap_analyzer.py | Identify unmet requirements (coverage < 0.40). Generate enhancement recommendations. Cross-register to Innovation Engine. | --analyze, --recommendations, --json | Gap analysis JSON |
| Response Drafter | tools/govcon/response_drafter.py | Two-tier LLM drafting (D365). qwen3 drafts compact response, Claude reviews. Stores in proposal_section_drafts. | --draft, --opp-id, --json | Draft response JSON |
| Compliance Populator | tools/govcon/compliance_populator.py | Auto-populate L/M/N compliance matrix from capability coverage scores. Bid/no-bid recommendation. | --populate, --summary, --export-matrix, --opp-id, --json | Compliance matrix JSON |
| Knowledge Base | tools/govcon/knowledge_base.py | CRUD for reusable content blocks. 11 categories including product_overview, integrated_solution, customer_value. Organized by category, domain, NAICS. Seeds from capability catalog (products + capabilities). Keyword search. Usage tracking. | --search, --add, --seed, --json | KB entries JSON |
| Question Generator | tools/govcon/question_generator.py | Auto-generate strategic questions from RFP analysis (D-QTG-1). Deterministic regex/keyword extraction. Categories, priority scoring, dedup. | --generate, --list, --stats, --opp-id, --json | Questions JSON |
| Amendment Tracker | tools/govcon/amendment_tracker.py | RFP amendment version tracking, difflib unified diff (D-QTG-3), government Q&A response capture. | --upload, --upload-text, --diff, --list, --record-response, --json | Amendment/diff JSON |
| Question Exporter | tools/govcon/question_exporter.py | Export questions to formatted HTML for government Q&A submission (D-QTG-4). CUI banner, print-friendly. | --export, --opp-id, --status, --output, --json | HTML document |
| Award Tracker | tools/govcon/award_tracker.py | Poll SAM.gov for award notices. Extract vendor, value, NAICS. Cross-ref with creative_competitors. | --scan, --list, --vendor, --json | Award data JSON |
| Competitor Profiler | tools/govcon/competitor_profiler.py | Aggregate competitor intelligence: total awards, contract value, agencies/NAICS diversity, leaderboard. | --profile, --leaderboard, --compare, --vendor, --json | Competitor profile JSON |
| GovCon Engine | tools/govcon/govcon_engine.py | Pipeline orchestrator: DISCOVER → EXTRACT → MAP → DRAFT. Daemon mode with quiet hours. Status and reporting. | --run, --stage, --status, --pipeline-report, --daemon, --json | Pipeline results JSON |
| GovCon API | tools/dashboard/api/govcon.py | Flask Blueprint with 20+ REST endpoints for GovCon Intelligence. Bridges govcon tools into proposal lifecycle. | (REST API) | JSON responses |
| Contract Manager | tools/govcon/contract_manager.py | CRUD for CPMP contracts, CLINs, WBS, deliverables. State machine transition enforcement. Status history tracking. | --create-contract, --list-contracts, --create-deliverable, --transition, --json | Contract/CLIN/WBS/deliverable JSON |
| Portfolio Manager | tools/govcon/portfolio_manager.py | Portfolio dashboard summary, 5-dimension health scoring (EVM/deliverables/CPARS/events/funding), proposal→contract transition bridge. | --portfolio, --health, --transition, --json | Portfolio/health JSON |
| EVM Engine | tools/govcon/evm_engine.py | ANSI/EIA-748 EVM calculations (CPI/SPI/EAC/ETC/VAC/TCPI), Monte Carlo forecast (PERT), S-curve data, IPMDAR export. | --record, --aggregate, --forecast, --scurve, --ipmdar, --json | EVM indicators JSON |
| CPARS Predictor | tools/govcon/cpars_predictor.py | Deterministic weighted CPARS prediction (5 dimensions), NDAA penalty table, corrective action discount, rating thresholds. | --predict, --create, --update, --trend, --json | CPARS prediction JSON |
| Subcontractor Tracker | tools/govcon/subcontractor_tracker.py | FAR 52.219-9 small business compliance, flow-down/cybersecurity checks, ISR/SSR generation, noncompliance detection. | --create, --list, --sb-compliance, --detect-noncompliance, --json | Subcontractor/SB JSON |
| Negative Event Tracker | tools/govcon/negative_event_tracker.py | FY2026 NDAA negative-event recording, 4 auto-detection rules, CPARS impact calculation, corrective action tracking. | --record, --auto-detect, --impact, --ndaa-thresholds, --json | Event/impact JSON |
| CDRL Generator | tools/govcon/cdrl_generator.py | CDRL auto-generation by dispatching to ICDEV tools (SSP, SBOM, POAM, STIG, EVM, ICD, TSP). Append-only generation audit. | --generate, --generate-due, --list-generations, --tool-mapping, --json | Generation result JSON |
| SAM Contract Sync | tools/govcon/sam_contract_sync.py | SAM.gov Contract Awards API v1 adapter. Rate-limited, content hash dedup, search, link to CPMP contracts. | --sync, --list, --search, --link, --json | Award sync JSON |
| CPMP API | tools/dashboard/api/cpmp.py | Flask Blueprint with ~40 REST endpoints for CPMP. Contracts, CLINs, WBS, deliverables, EVM, CPARS, subcontractors, COR portal. | (REST API) | JSON responses |

## ATLAS Critique Phase (Phase 61 — Feature 3)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| ATLAS Critique | tools/agent/atlas_critique.py | Adversarial multi-agent plan critique: parallel dispatch to security/compliance/knowledge agents, severity classification, GO/NOGO/CONDITIONAL consensus, revision loop (max 3 rounds). Append-only findings (NIST AU). | --project-id, --phase-output, --session-id, --status, --history, --max-rounds, --json | Critique session + findings JSON |
| ATLAS Critique Config | args/atlas_critique_config.yaml | Critique phase config: critic agent assignments, focus areas, consensus rules, revision prompt, max rounds | (data) | YAML config |

## Safety Hooks
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Pre-Tool-Use Hook | .claude/hooks/pre_tool_use.py | Blocks dangerous rm, .env access, audit modifications | tool_name, tool_input | Allow/block |
