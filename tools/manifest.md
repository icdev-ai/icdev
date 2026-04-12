# Tools Manifest

> Master list of all tools. Check here before writing a new script.

## Memory System
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Init Memory DB | tools/memory/init_memory_db.py | Initialize data/memory.db with all memory tables (memory_entries, daily_logs, memory_access_log, memory_consolidation_log, memory_buffer) | --db-path, --json | Table list + status |
| Memory Read | tools/memory/memory_read.py | Load all memory (MEMORY.md + recent logs) | --format markdown | Formatted memory context |
| Memory Write | tools/memory/memory_write.py | Write to daily log + DB | --content, --type, --importance | Confirmation |
| Memory DB | tools/memory/memory_db.py | Keyword search on memory database | --action search, --query | Search results |
| Semantic Search | tools/memory/semantic_search.py | Vector similarity search (requires OpenAI key) | --query | Ranked results |
| Hybrid Search | tools/memory/hybrid_search.py | Combined keyword + semantic search, optional --time-decay flag for recency weighting | --query, --bm25-weight, --semantic-weight, --time-decay | Ranked results |
| Embed Memory | tools/memory/embed_memory.py | Generate embeddings for memory entries | --all | Confirmation |
| Time-Decay Scoring | tools/memory/time_decay.py | Exponential time-decay scoring for memory entries: per-type half-lives, importance resistance, combined relevance+recency+importance scoring (D147) | --score --entry-id, --rank --query, --top-k, --user-id, --json | Decay factors + ranked results |
| Auto-Capture | tools/memory/auto_capture.py | Auto-capture content from hooks into memory buffer with dedup (D181) | --content, --source, --type, --tool-name, --flush, --buffer-status, --user-id, --json | Capture/flush result |
| Maintenance Cron | tools/memory/maintenance_cron.py | Orchestrate memory maintenance: flush buffer, embed, prune, backup (D179-D182) | --all, --flush-buffer, --embed-unembedded, --prune-stale, --backup, --days, --json | Maintenance results |
| Scoped Provider | tools/memory/scoped_provider.py | Per-agent isolated memory partitions with swappable backends (SQLite/InMemory) and controlled cross-agent transfer (D-MEM-10/11/12) | --agent-id, --project-id, --backend, --policy; subcommands: write, read, transfer, pull-inbox, pull-team, pull-broadcast, stats, list-partitions; --json --gate | Entry ID / entries / transfer result |

## Database
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Init ICDEV™ DB | tools/db/init_icdev_db.py | Initialize ICDEV™ operational database (176 tables) — detects migration system (D150) | --db-path, --reset | Confirmation + table list |
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
| Triage Engine | tools/innovation/triage_engine.py | 5-stage compliance-first triage pipeline (classify, FORGE fit, boundary, compliance, dedup/license) | --triage, --triage-all, --summary, --json | Triage outcomes |
| Solution Generator | tools/innovation/solution_generator.py | Auto-generate solution specs from approved signals (D208) | --generate, --generate-all, --list, --status, --json | Solution specs |
| Innovation Manager | tools/innovation/innovation_manager.py | Main orchestrator + daemon mode for full pipeline | --run, --discover, --score, --triage, --generate, --daemon, --status, --json | Pipeline results |
| Introspective Analyzer | tools/innovation/introspective_analyzer.py | Internal telemetry mining (D203) — gate failures, unused tools, slow pipelines, knowledge gaps | --analyze, --type, --all, --json | Analysis findings |
| Competitive Intel | tools/innovation/competitive_intel.py | Competitor feature monitoring (D205) — gap analysis against ICDEV™ capabilities | --scan, --gap-analysis, --report, --json | Competitive gaps |
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
| ICDEV™ Client | tools/sdk/icdev_client.py | Thin Python SDK wrapping CLI tools via subprocess (D191) | (library) | ICDEV™Client class |

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
| Complexity Compliance | tools/compliance/complexity_compliance.py | Maps cyclomatic/cognitive complexity to NIST SA-11(1/3/8) and SA-15(1/7/11) sub-controls as PDC compliance findings. Gates: SA-15(1) blocking on avg CC > 10. | --project-dir, --json, --gate, --no-trend, --control | JSON findings with control_id, severity, evidence |

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
| OSCAL Catalog Adapter | tools/compliance/oscal_catalog_adapter.py | Unified NIST OSCAL + ICDEV™ catalog reader with fallback chain (D304) | --lookup, --list, --stats, --family | Control data, catalog stats |
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
| Framework Detector | tools/builder/framework_detector.py | Zero-config language/framework detection from source — pre-populates FORGE blueprint (language, framework, deploy_template with Terraform+STIG baselines+CUI markings, capabilities) | --source-path, --blueprint, --merge, --gate, --json | Detection result + optional merged blueprint |
| Child App Generator | tools/builder/child_app_generator.py | Generate mini-ICDEV™ clone child applications (16-step pipeline); supports --source-path --auto-detect for zero-config build detection | --blueprint, --project-path, --name, --source-path, --auto-detect, --json | Generated app path |
| Claude MD Generator | tools/builder/claude_md_generator.py | Generate dynamic CLAUDE.md for child apps (Jinja2) | --blueprint, --output, --json | CLAUDE.md path |
| Goal Adapter | tools/builder/goal_adapter.py | Copy and adapt ICDEV™ goals for child applications | --source-goals, --output, --app-name, --json | Adapted goal paths |
| DB Init Generator | tools/builder/db_init_generator.py | Generate standalone DB init scripts for child apps | --blueprint, --output, --app-name, --json | DB init script path |
| Dev Profile Manager | tools/builder/dev_profile_manager.py | 5-layer cascade dev profiles (Platform→Tenant→Program→Project→User) with version immutability, role-based locks, LLM injection (D183-D188) | --scope, --scope-id, --create, --get, --update, --resolve, --lock, --inject, --diff, --rollback, --json | Profile + cascade |
| Profile Detector | tools/builder/profile_detector.py | Auto-detect dev profile from repo analysis or natural language text (D185 advisory-only) | --repo-path, --text, --json | Detected dimensions |
| Profile MD Generator | tools/builder/profile_md_generator.py | Generate PROFILE.md from resolved dev profile via Jinja2 (D186) | --scope, --scope-id, --output, --store, --json | PROFILE.md path |
| FORGE Validator | tools/builder/forge_validator.py | Validate FORGE framework compliance for child apps (6 layers + 4 meta checks) | --project-dir, --json, --human, --gate | Validation report |
| Agentic Test: A2A Callback | tools/builder/agentic_test_templates/test_a2a_callback.py | Template test for A2A callback verification in child apps | (pytest template) | Test results |
| Agentic Test: Agent Health | tools/builder/agentic_test_templates/test_agent_health.py | Template test for agent health endpoint verification in child apps | (pytest template) | Test results |

## Security Scanning
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Vuln Scanner | tools/security/vuln_scanner.py | Vulnerability scanning orchestrator | --project | Scan results |
| SAST Runner | tools/security/sast_runner.py | Multi-language SAST (Bandit, SpotBugs, gosec, clippy, ESLint-security, SecurityCodeScan) | --report, --gate | Findings |
| Dependency Auditor | tools/security/dependency_auditor.py | Multi-language dep audit (pip-audit, npm-audit, cargo-audit, govulncheck, OWASP DC, dotnet) | --report, --gate | Vulnerabilities |
| Secret Detector | tools/security/secret_detector.py | detect-secrets wrapper | --report, --gate | Secrets found |
| Container Scanner | tools/security/container_scanner.py | trivy container scanning | --image | Vulnerabilities |
| Blueprint Verifier | tools/security/blueprint_verifier.py | NemoClaw-adapted SHA-256 recursive directory digest for genome/marketplace/child integrity (D-NC-3) | --compute, --verify, --store, --lookup, --history, --json | Digest + verification |
| Credential Broker | tools/security/credential_broker.py | NemoClaw-adapted agent credential isolation: function-scoped tokens, auto-revocation (D-NC-1) | --request, --revoke, --audit, --status, --gate, --json | Token + grant log |
| Egress Policy Manager | tools/security/egress_policy_manager.py | NemoClaw-adapted per-agent network egress policies with deny-by-default (D-NC-2) | --resolve, --generate, --validate, --diff, --list-roles, --audit, --json | K8s NetworkPolicy |

## Deploy
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| BYOC Controller | tools/deploy/byoc_controller.py | BYOC control-plane: register/manage agency IL4/IL5 K8s clusters, push compliance, remote self-healing | --register, --list, --status, --heartbeat, --push-compliance, --heal, --gate, --json | Operation result JSON |

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
| Deviation Rules | tools/knowledge/deviation_rules.py | Category-based deviation rules (GSD-adapted): 5 categories layered on confidence-based healing — security/blocking auto-fix at lower threshold, architectural/compliance always escalate (D-GSD-7 through D-GSD-9) | --classify, --apply, --confidence, --stats, --list-categories, --json | Classification + decision override |

## Monitoring
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Log Analyzer | tools/monitor/log_analyzer.py | ELK/Splunk log analysis | --project, --time-range | Anomalies |
| Metric Collector | tools/monitor/metric_collector.py | Prometheus metric collection | --project | Metrics |
| Alert Correlator | tools/monitor/alert_correlator.py | Correlate alerts across sources | --time-window | Correlated incidents |
| Health Checker | tools/monitor/health_checker.py | Application health check | --url, --retries | Health status |
| Heartbeat Daemon | tools/monitor/heartbeat_daemon.py | Proactive daemon: 7 configurable checks (cATO evidence, agent health, CVE SLA, pending intake, failing tests, expiring ISAs, memory maintenance) (D141-D142) | --once, --check, --status, --json | Check results + notifications |
| Auto-Resolver | tools/monitor/auto_resolver.py | Webhook-triggered auto-resolution: alert → normalize → analyze → fix → PR → notify (D143-D145) | --analyze, --resolve, --alert-file, --source, --dry-run, --json | Resolution log + PR URL |
| Outcome Verifier | tools/monitor/outcome_verifier.py | Track PR merge status + failure recurrence, update pattern confidence (D-EVO-6) | --check-pending, --check-recurrence, --run-all, --status, --json | Verification log |
| Push Agent | tools/monitor/push_agent.py | Lightweight sidecar: collect CPU/memory/disk per container (Docker stats or psutil), buffer to SQLite, push to dashboard on configurable interval; IL5/IL6 air-gap safe | --once, --daemon, --flush, --status, --interval, --dry-run, --json | Metrics JSON / push receipt |
| Retention Manager | tools/monitor/retention.py | SQLite retention policy for container_metrics and heartbeat_checks; configurable window (default 7d, floor 1d); daemon or one-shot purge | --purge, --status, --daemon, --retention-days, --interval, --dry-run, --json | Purge summary JSON |

## Dashboard
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Web Dashboard | tools/dashboard/app.py | Flask web dashboard with role-based views, wizard, quick paths | --port, --debug | Web UI on port 5000 |
| Platform Health | tools/dashboard/platform_health.py | Aggregate health scoring across 10 ICDEV™ domains (database, agents, compliance, security, infrastructure, canvases, LLM, monitoring, CI/CD, marketplace); 60s in-process cache; bands: ≥90 healthy, ≥70 degraded, <70 critical | get_platform_health(), get_domain_health(domain), _invalidate_cache() | Composite + per-domain score/status/findings JSON |
| UX Helpers | tools/dashboard/ux_helpers.py | Jinja2 filters (friendly_time, glossary), error recovery dict, quick paths, wizard steps | register_ux_filters(app) | Template filters + globals |
| UX JavaScript | tools/dashboard/static/js/ux.js | Client-side glossary tooltips, timestamp formatting, accessibility, notifications, progress pipeline | Auto-init on DOMContentLoaded | ICDEV™ namespace |
| UX Stylesheet | tools/dashboard/static/css/ux.css | Tooltip, pipeline, wizard, quick path, breadcrumb, notification, accessibility styles | — | CSS |
| Charts Library | tools/dashboard/static/js/charts.js | Zero-dependency SVG chart library: sparkline, line, bar, donut, gauge with tooltips and animation | ICDEV™.lineChart(), ICDEV™.barChart(), ICDEV™.donutChart(), ICDEV™.gaugeChart() | SVG charts |
| Table Interactivity | tools/dashboard/static/js/tables.js | Table search, column sort, column filter, CSV export, row counter | Auto-init on DOMContentLoaded | Enhanced tables |
| Onboarding Tour | tools/dashboard/static/js/tour.js | Interactive overlay walkthrough for first-visit users, 6-step spotlight tour | ICDEV™.startTour(), ICDEV™.resetTour() | Tour overlay |
| Live Dashboard | tools/dashboard/static/js/live.js | Real-time SSE auto-refresh: connection status, smart debounced updates, event toasts | ICDEV™.connectSSE(), ICDEV™.disconnectSSE() | Live updates |
| Batch Operations JS | tools/dashboard/static/js/batch.js | Batch workflow UI: catalog display, execution progress, step status polling | ICDEV™.batchStartBatch(id, projectId) | Batch progress UI |
| Batch Operations API | tools/dashboard/api/batch.py | Flask blueprint: batch execute/status/catalog endpoints, background subprocess runner | POST /api/batch/execute, GET /api/batch/status | JSON batch status |
| Keyboard Shortcuts | tools/dashboard/static/js/shortcuts.js | Chord-based navigation (g+key), direct shortcuts, help modal overlay | ICDEV™.showShortcutsHelp() | Navigation + help modal |
| Mermaid Integration | tools/dashboard/static/js/mermaid-icdev.js | ICDEV™ Mermaid module: dark theme, click handlers, editor, SVG export, auto-init | ICDEV™.renderMermaid(), ICDEV™.initMermaidEditor(), ICDEV™.exportMermaidSVG() | Rendered diagrams |
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
| Stub Detector | tools/testing/stub_detector.py | 4-level verification & stub detection (GSD-adapted): EXISTS→SUBSTANTIVE→WIRED→FUNCTIONAL cascade, per-language stub patterns (6 languages), Python AST analysis, orphan detection, security gate (D-GSD-1 through D-GSD-3) | --file, --project-dir, --max-level, --project-id, --store, --gate, --json, --human | Verification results + gate |
| API Surface Extractor | tools/testing/api_surface_extractor.py | AST-based extraction of public API surface (functions, classes, dataclass fields, dict constants, imports, mock targets) — run BEFORE writing tests to prevent field name, return type, and mock path errors (D-API-1) | --file, --dir, --json, --human, --mock-targets, --include-private | API surface JSON or markdown |
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
| ICDEV™ Plan | tools/ci/workflows/icdev_plan.py | Planning phase: classify, branch, plan | issue-number, run-id | Plan file |
| ICDEV™ Build | tools/ci/workflows/icdev_build.py | Implementation phase: implement plan | issue-number, run-id | Committed code |
| ICDEV™ Test | tools/ci/workflows/icdev_test.py | Testing phase: pytest, ruff, bandit, gates | issue-number, run-id | Test results |
| ICDEV™ Review | tools/ci/workflows/icdev_review.py | Code review against spec | issue-number, run-id | Review results |
| ICDEV™ Document | tools/ci/workflows/icdev_document.py | Documentation generation from changes | issue-number, run-id | Doc file |
| ICDEV™ Patch | tools/ci/workflows/icdev_patch.py | Quick fix workflow from issue content | issue-number, run-id | Patched code |
| ICDEV™ SDLC | tools/ci/workflows/icdev_sdlc.py | Complete lifecycle: plan+build+test+review | issue-number, run-id | All artifacts |
| Agent Model Test | tools/testing/test_agent_models.py | Verify opus/sonnet/haiku model availability | — | Pass/fail per model |
| Base Connector | tools/ci/connectors/base_connector.py | ABC for CI/CD platform connectors (GitHub, GitLab, Mattermost, Slack) | (library) | BaseConnector ABC |
| Connector Registry | tools/ci/connectors/connector_registry.py | Registry for CI/CD platform connectors — auto-discover and load | (library) | ConnectorRegistry |
| Mattermost Connector | tools/ci/connectors/mattermost_connector.py | Mattermost integration for CI/CD notifications and triggers (air-gap safe, D140) | (library) | MattermostConnector |
| Slack Connector | tools/ci/connectors/slack_connector.py | Slack integration for CI/CD notifications and triggers | (library) | SlackConnector |
| Air Gap Detector | tools/ci/core/air_gap_detector.py | Detect air-gapped environments and disable internet-dependent features (D134/D139) | (library) | AirGapStatus |
| Comment Handler | tools/ci/core/comment_handler.py | Parse and handle CI/CD comments from issues/PRs (bot loop prevention) | (library) | ParsedComment |
| Conversation Manager | tools/ci/core/conversation_manager.py | Manage multi-turn CI/CD conversations for issue resolution | (library) | ConversationState |
| Event Router | tools/ci/core/event_router.py | Route webhook/poll events to appropriate workflow handlers | (library) | RoutedEvent |
| Failure Parser | tools/ci/core/failure_parser.py | Parse CI/CD failure logs and extract actionable error context | (library) | ParsedFailure |
| Recovery Engine | tools/ci/core/recovery_engine.py | Auto-recover from CI/CD pipeline failures (retry, workaround, escalate) | (library) | RecoveryAction |
| ICDEV™ Comply | tools/ci/workflows/icdev_comply.py | Compliance artifact generation workflow for CI/CD | issue-number, run-id | Compliance artifacts |
| ICDEV™ E2E | tools/ci/workflows/icdev_e2e.py | E2E test execution workflow for CI/CD | issue-number, run-id | E2E results |
| ICDEV™ Plan+Build | tools/ci/workflows/icdev_plan_build.py | Combined plan + build workflow | issue-number | Plan + committed code |
| ICDEV™ Plan+Build+Test | tools/ci/workflows/icdev_plan_build_test.py | Combined plan + build + test workflow | issue-number | Plan + code + test results |
| ICDEV™ Plan+Build+Test+Review | tools/ci/workflows/icdev_plan_build_test_review.py | Full SDLC pipeline (explicit variant) | issue-number | All artifacts |

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
| Context Pressure | tools/agent/context_pressure.py | Context pressure monitor & stuck detection guard (GSD-adapted): token estimation, 3-level pressure alerts (normal/warning/critical), analysis paralysis detection, duplicate loop detection, combined health check (D-GSD-4 through D-GSD-6) | --check pressure/stuck/health, --session-id, --json, --human | Pressure level + stuck status |

## LLM Provider Abstraction (Vendor-Agnostic)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| LLM Provider Base | tools/llm/provider.py | ABC base classes (LLMProvider, EmbeddingProvider), vendor-agnostic LLMRequest/LLMResponse, message/tool format translators | — | — |
| LLM Router | tools/llm/router.py | Config-driven function→model routing with fallback chains, reads args/llm_config.yaml | function name | (provider, model_id, config) |
| RL Router | tools/llm/rl_router.py | Q-Learning RL agent that ranks model chains by learned success/latency history (epsilon-greedy, TD(0)); persists Q-table to llm_rl_qtable in icdev.db | --stats, --reset, --function, --model, --json | Q-table stats |
| Bedrock Provider | tools/llm/bedrock_provider.py | AWS Bedrock LLMProvider: Anthropic models, thinking/effort, tools, structured output, retry/backoff | LLMRequest | LLMResponse |
| Anthropic Provider | tools/llm/anthropic_provider.py | Direct Anthropic API LLMProvider via anthropic SDK | LLMRequest | LLMResponse |
| OpenAI-Compat Provider | tools/llm/openai_provider.py | OpenAI-compatible LLMProvider: OpenAI, vLLM, Azure via configurable base_url | LLMRequest | LLMResponse |
| Ollama Native Provider | tools/llm/ollama_provider.py | Native Ollama REST API provider using /api/chat — faster than OpenAI-compat for local models, native vision support | LLMRequest | LLMResponse |
| Embedding Provider | tools/llm/embedding_provider.py | Embedding providers: OpenAI, Bedrock Titan, Ollama (nomic-embed-text) | text | float[] |
| LLM Config | args/llm_config.yaml | Master config: providers, models, per-function routing chains, embedding config, pricing | — | — |
| LLM Gateway | tools/llm/gateway.py | LLM Gateway/Proxy: pre/post-invoke security checks (injection detection, PII scrubbing, rate limiting), audit trail, gate check | --invoke, --check, --rate-status, --gate, --json | Gateway response + audit |
| LLM Gateway Config | args/llm_gateway_config.yaml | Gateway config: injection rules, PII patterns, rate limits, audit settings, gate thresholds | (data) | YAML config |
| Prompt Registry | tools/llm/prompt_registry.py | Prompt version control: version, activate, rollback, diff, A/B test prompt templates with audit trail | --register, --activate, --rollback, --diff, --ab-test, --list, --json | Prompt versions + diffs |
| Cost Intelligence | tools/llm/cost_intelligence.py | Cost intelligence: anomaly detection, monthly projection, optimization recommendations, edge-vs-cloud comparison | --report, --anomalies, --project, --optimize, --edge-vs-cloud, --json | Cost analysis + recommendations |
| Model Drift Monitor | tools/llm/model_monitor.py | Model drift monitor: quality scoring, latency/token tracking, statistical drift detection (Welch's t-test), gate check | --check, --report, --drift, --gate, --model, --json | Drift analysis + gate status |
| LLM Eval Runner (OPT-64) | tools/llm/eval_runner.py | Declarative YAML→side-by-side provider comparison (promptfoo pattern, MIT). Loads args/llm_evals/*.yaml, runs prompts across logical models via LLMRouter, evaluates 6 assertion types (contains/not_contains/regex/max_length/min_length/json_schema), writes markdown+json+html reports to reports/llm_evals/ | --eval NAME, --models, --output-dir, --json, --gate | EvalReport (md/json/html) |

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
| Agent Topology | tools/agent/topology.py | Agent topology: graph-based dependency mapping of providers/models/functions/agents, SPOF detection, air-gap analysis | --map, --spof, --air-gap, --visualize, --json | Topology graph + SPOF report |

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
| PG Schema | tools/saas/db/pg_schema.py | Full ICDEV™ schema (100+ tables) ported from SQLite to PostgreSQL DDL | --init | PG schema |
| DB Compat | tools/saas/db/db_compat.py | SQLite ↔ PostgreSQL compatibility: placeholder translation, row factory | engine type | DB connection |
| Connection Pool | tools/saas/db/connection_pool.py | Per-tenant PostgreSQL connection pooling (psycopg2 ThreadedConnectionPool) | tenant_id | Pooled connection |
| Delivery Engine | tools/saas/artifacts/delivery_engine.py | Push artifacts to tenant S3/Git/SFTP with audit trail | tenant_id, artifact_path | Delivery status |
| Artifact Signer | tools/saas/artifacts/signer.py | SHA-256 hash + RSA digital signature for compliance artifacts | file_path | Hash + signature |
| Bedrock Proxy | tools/saas/bedrock/bedrock_proxy.py | Route Bedrock LLM calls: BYOK (tenant's AWS) or ICDEV™ shared pool | tenant_id, prompt | LLM response |
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
| ClawHub Connector | tools/databridge/connectors/clawhub_connector.py | DataBridge connector for ClawHub API — vector search, skill detail, zip download | --search/--get/--download/--list/--health | Skill data JSON |
| OpenClaw ScriptGen | tools/marketplace/openclaw_scriptgen.py | Generate Python companion scripts for actionable skill steps (LLM-agnostic) | --generate/--analyze | Script + analysis JSON |
| OpenClaw Enricher | tools/marketplace/openclaw_enricher.py | 3-engine skill enrichment (Innovation + Creative + Research) with merge discovery | --enrich/--discover-similar | Enrichment result JSON |
| OpenClaw Compat | tools/marketplace/openclaw_compat.py | Compatibility checker & translator for OpenClaw → ICDEV™ skills | --check/--translate/--full, --output | Compat report / translated SKILL.md |
| OpenClaw Bridge | tools/marketplace/openclaw_bridge.py | Zero-trust import/export for ClawHub (clawhub.ai) skills with 10-gate scanning, quarantine, provenance | --import/--export/--promote/--reject/--list-quarantine/--list-exports/--health/--gate | Import/export/scan JSON |
| Marketplace MCP | tools/mcp/marketplace_server.py | MCP server (17 tools, 2 resources) for marketplace | stdio | JSON-RPC 2.0 |

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
| LLM Red Team Runner (OPT-65) | tools/security/llm_red_team.py | Executable red team runner (promptfoo-inspired, MIT). Runs args/llm_red_team_catalog.yaml against a target LLM function via LLMRouter, 5 detectors (contains_string/regex/data_leak/absence_of/json_field_equals), OWASP LLM Top 10 grouped report. Complements atlas_red_team.py (static) with live adversarial probes. | --target, --catalog, --categories, --severities, --output-dir, --json, --gate | Markdown + JSON report |
| Design Twice (OPT-52) | tools/planning/design_twice.py | Parallel constraint exploration (Ousterhout, mattpocock/skills MIT). Produces 4 alternate designs for a module, each under a different constraint (minimal surface, max flexibility, common-case, stdlib-inspired). Threaded LLMRouter calls. Skeleton fallback in ICDEV_NO_LLM mode. | --module, --constraints-file, --out, --sequential | Markdown report with side-by-side comparison |
| Kanban State Machine (OPT-72) | tools/kanban/state_machine.py | Formal kanban task state machine (optio/packages/shared MIT). 11-state enum with explicit transition table, resume-cycle cap (MAX_RESUME_CYCLES=5), audit-trail integration, db_status mapping down to existing CHECK-constrained statuses. Pure Python — no schema migration required. | transition(task_id, from, to, reason, actor, db_exec, audit_exec) | TransitionResult |
| CI Error Classifier (OPT-72) | tools/ci/error_classifier.py | Deterministic PR/CI state classifier (optio/packages/shared MIT). Maps `gh pr view --json` output + raw CI log text onto KanbanState via 6 signal classifiers (ci_failed, merge_conflict, changes_requested, approved_passing, in_progress, stale). No LLM. | pr_json, ci_logs, max_age_hours | KanbanState |
| Agent Adapter Registry (OPT-71) | tools/agents/registry.py | Unified agent adapter discovery + selection (optio/packages/agent-adapters MIT). Lazy-loads adapters (claude_cli, local_llm_router, codex_cli, copilot_cli) into a module-level registry. pick_default() resolves best adapter via env var → per-task config → fallback_order → detect_available(). Reads args/agent_adapters.yaml; safe defaults if missing. See also adapter_base.py (Protocol + dataclasses). | pick_default(task_type, config), get_adapter(name), detect_available(), list_adapters(), reset() | AgentAdapter / list[str] / NotInstalledError |
| PR Watcher (OPT-70) | tools/ci/pr_watcher.py | Autonomous PR→resume feedback loop (optio MIT). Polls kanban tasks with github PR URLs, calls `gh pr view --json`, classifies via OPT-72 error_classifier, injects resume context via OPT-62 queue_message on ci_failed/merge_conflict/changes_requested, auto-merges on approved+passing (opt-in). Daemon mode with configurable interval + resume-cycle cap. | --once, --daemon, --interval, --task, --dry-run, --json, --config | WatcherReport |
| Undo Toast (OPT-68) | tools/dashboard/static/js/undo_toast.js | Vanilla-JS Snackbar-style undo toast (react-admin MIT pattern). Exposes `window.ICDEV.undoToast.show({message, undoCallback, durationMs, onExpire})`. Self-contained styles injected on first use. Opt-in per page — callers hand the reverse-action fetch closure. | show(opts) | {dismiss()} |
| Debounce Filter (OPT-68) | tools/dashboard/static/js/debounce_filter.js | Vanilla-JS filter-as-you-type helper (react-admin MIT pattern). Exposes `window.ICDEV.debounceFilter.bind(input, {delayMs, onFilter})` and `bindForm(container, opts)`. 250ms debounce by default. | bind(inputEl, opts), bindForm(formEl, opts) | {cancel, unbind, flush} |
| CRUD Resource (OPT-69) | tools/dashboard/crud_resource.py | Declarative Flask CRUD helper (react-admin <Resource> MIT pattern). `register_resource(app, name, url_prefix, columns=[ColumnSpec], sortable, filterable, allow_create/edit/delete, get_connection)` wires GET/POST/PATCH/DELETE routes with filter/sort/pagination parsing, SQL identifier safety, audit hooks. Injectable get_connection for tests. | register_resource(app, **kwargs) | Blueprint |
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
| Evolution Daemon | tools/registry/evolution_daemon.py | Autonomous 7-step capability lifecycle: discover, evaluate, stage, test, approve, verify, absorb (D-EVO-1) | --once, --status, --reflex NAME, --enable, --disable, --reset, --json | Daemon status |
| Egress Monitor | tools/registry/egress_monitor.py | NemoClaw-adapted child network egress tracking against parent policies (D-NC-6) | --collect, --evaluate, --summary, --json | Violation report |
| Propagation Verifier | tools/registry/propagation_verifier.py | Post-propagation integrity verification: digest, DB, health, CUI checks (D-NC-5) | --verify, --history, --json | Verification checklist |
| Sandbox Scorer | tools/registry/sandbox_scorer.py | 8th capability evaluation dimension: isolation posture scoring (D-NC-4) | --score, --capability-id, --source-metadata, --json | Score + breakdown |

## Bayesian Teaching Intelligence (D-BT-1 through D-BT-6)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Bayesian Teacher | tools/intelligence/bayesian_teacher.py | Information-gain scoring, optimal ordering, teaching dimension, SmartEncoding (Shafto/Goodman/Griffiths 2014, Zhu 2015, DeepFlow-inspired) | --score-pairs, --optimal-order, --teaching-dim, --smart-encode, --health, --json | Scores + ordering |

## Engineering Review Board (Phase 67, D-RB-1 through D-RB-7)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Review Board Daemon | tools/review_board/daemon.py | Multi-persona analysis daemon — 7 reflexes (SRE, QA, Security, Perf, UX, Docs, Product) on configurable schedules (D-RB-1) | --once, --status, --reflex, --enable, --disable, --reset, --json | Daemon status, findings |
| SRE Reflex | tools/review_board/reflexes/sre.py | Reliability checks — backup freshness, error rate, circuit breaker state, disk usage (D-RB-3) | config dict | Findings list |
| QA Reflex | tools/review_board/reflexes/qa.py | Coverage checks — untested modules, E2E gaps, syntax errors, test-to-code ratio (D-RB-3) | config dict | Findings list |
| Security Reflex | tools/review_board/reflexes/security.py | Red team checks — secret exposure, CVE SLA, injection patterns, dangerous code (D-RB-3) | config dict | Findings list |
| Performance Reflex | tools/review_board/reflexes/perf.py | Performance checks — DB file sizes, large tables, audit growth, temp dir size (D-RB-3) | config dict | Findings list |
| UX Reflex | tools/review_board/reflexes/ux.py | Accessibility checks — ARIA coverage, template quality, form labels (D-RB-3) | config dict | Findings list |
| Docs Reflex | tools/review_board/reflexes/docs.py | Documentation checks — stale docs, undocumented tools, broken refs, missing phases (D-RB-3) | config dict | Findings list |
| Product Reflex | tools/review_board/reflexes/product.py | Product analytics — feature usage, gate pass rates, tool distribution (D-RB-3) | config dict | Findings list |

## Workflow Discipline Engine (Phase 66, D-WF-1 through D-WF-7)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Loop Engine | tools/workflow/loop_engine.py | PLAN-APPLY-UNIFY lifecycle manager with state machine and acceptance criteria tracking (D-WF-1) | --create, --plan, --add-criteria, --start-apply, --complete-task, --verify-criterion, --start-unify, --close, --status, --list, --abandon, --json | Loop state |
| Next Action | tools/workflow/next_action.py | Single next action recommender with 5-dimension weighted priority scoring (D-WF-2) | --recommend, --project-id, --json | Prioritized action |
| Process Verifier | tools/workflow/process_verifier.py | Verify required processes were executed during APPLY phase via audit_trail queries (D-WF-3) | --verify, --loop-id, --json | Verification result |
| Tool Curator | tools/workflow/tool_curator.py | Phase-level tool curation enforcement — restrict available tools per goal phase (Agent Harness pattern) | --goal, --phase, --tool, --validate, --list-goals, --json | Allowed/blocked + reason |
| Handoff Generator | tools/workflow/handoff_generator.py | Session handoff document generation for cross-session context transfers (D-WF-4) | --generate, --loop-id, --json | Handoff markdown |
| Reconciler | tools/workflow/reconciler.py | UNIFY phase reconciliation: planned-vs-actual delta tracking with deviation classification (D-WF-5) | --reconcile, --loop-id, --json | Reconciliation record |
| Replay Engine | tools/workflow/replay_engine.py | Event-sourced ANVIL pipeline recovery — reconstructs exact workflow state from append-only audit trail, identifies resume point, skips completed steps without re-executing side effects (NIST AU extension) | --inspect/--replay/--simulate/--verify/--list-failed, --loop-id, --project-id, --gate, --json | Resume plan + idempotency tokens |

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
| FedRAMP 20x KSI Generator | tools/compliance/fedramp_ksi_generator.py | Generate Key Security Indicators (KSIs) for FedRAMP 20x authorization. Maps ICDEV™ evidence to 61 KSI schemas. | --project-id, --ksi-id, --all, --json | KSI evidence manifest |
| FedRAMP Auth Packager | tools/compliance/fedramp_authorization_packager.py | Bundle OSCAL SSP + KSI evidence into FedRAMP 20x authorization package | --project-id, --output-dir, --json | Authorization bundle |
| FedRAMP 20x API | tools/dashboard/api/fedramp_20x.py | Blueprint: stats, KSI list, generate, package | /api/fedramp-20x/* | REST endpoints |
| FedRAMP 20x Page | tools/dashboard/templates/fedramp_20x.html | Dashboard: stat-grid + KSI table + package status | (template) | HTML page |
| KSI Schemas | context/compliance/fedramp_20x_ksi_schemas.json | 61 KSI definitions (id, title, family, evidence_sources, nist_crosswalk) | (catalog) | JSON catalog |
| OWASP ASI Assessor | tools/compliance/owasp_asi_assessor.py | BaseAssessor for OWASP ASI01-ASI10 agentic AI risks. Maps 10 ASI risks to ICDEV™ controls via NIST 800-53 crosswalk. | --project-id, --json, --gate | Assessment JSON |
| OWASP ASI Catalog | context/compliance/owasp_agentic_asi.json | 10 ASI risk definitions with NIST crosswalk | (catalog) | JSON catalog |

## SWFT/SLSA + Cross-Phase Orchestration (Phase 54)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| SLSA Attestation Generator | tools/compliance/slsa_attestation_generator.py | Generate SLSA v1.0 provenance statements and VEX documents from build pipeline evidence | --project-id, --generate, --verify, --vex, --json | SLSA provenance + VEX |
| SWFT Evidence Bundler | tools/compliance/swft_evidence_bundler.py | Bundle DoD SWFT evidence package (SLSA, SBOM, VEX, scan results) | --project-id, --output-dir, --json | SWFT bundle |
| Workflow Composer | tools/orchestration/workflow_composer.py | Declarative cross-phase workflow engine using YAML templates + TopologicalSorter DAG | --template, --project-id, --dry-run, --list, --json | Workflow execution plan + results |
| ATO Workflow Template | args/workflow_templates/ato_acceleration.yaml | Workflow: categorize → assess → SSP → POAM → SBOM | (template) | YAML workflow |
| Security Workflow Template | args/workflow_templates/security_hardening.yaml | Workflow: SAST → deps → secrets → OWASP → ANVIL | (template) | YAML workflow |
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
| Evidence Chain | tools/compliance/evidence_chain.py | Continuous Compliance Evidence Chain — connects PDC/NDC/SDC audit trails into OSCAL 1.1.2-aligned evidence timeline. Stores snapshots in compliance_evidence_chain table. Gate: fails if no assessment evidence or >10 unresolved findings. | --project-id, --since (24h/7d), --json, --gate, --export-oscal, --output | Evidence chain manifest + OSCAL Assessment Results |
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
| SAM.gov Scanner | tools/govcon/sam_scanner.py | Poll SAM.gov Opportunities API v2. Extracts opportunities by NAICS, notice type. Stores in sam_gov_opportunities. Auto-backfills full descriptions. | --scan, --backfill, --naics, --list-cached, --json | Opportunity JSON |
| SAM.gov Quota Tracker | tools/govcon/quota_tracker.py | Persistent daily API call counter for SAM.gov. Proactive quota check before each request, 429 response parsing, audit trail. | --status, --check, --reset, --json | Quota status JSON |
| Requirement Extractor | tools/govcon/requirement_extractor.py | Extract shall/must/will statements from RFP descriptions. Domain-classify (9 domains). Cluster by keyword fingerprint (D364). | --extract-all, --patterns, --domain, --json | Requirements + patterns JSON |
| Capability Mapper | tools/govcon/capability_mapper.py | Map requirement patterns to ICDEV™ capability catalog. Compute coverage scores (L/M/N). | --map-all, --coverage, --gaps, --json | Compliance matrix JSON |
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
| CDRL Generator | tools/govcon/cdrl_generator.py | CDRL auto-generation by dispatching to ICDEV™ tools (SSP, SBOM, POAM, STIG, EVM, ICD, TSP). Append-only generation audit. | --generate, --generate-due, --list-generations, --tool-mapping, --json | Generation result JSON |
| SAM Contract Sync | tools/govcon/sam_contract_sync.py | SAM.gov Contract Awards API v1 adapter. Rate-limited, content hash dedup, search, link to CPMP contracts. | --sync, --list, --search, --link, --json | Award sync JSON |
| CPMP API | tools/dashboard/api/cpmp.py | Flask Blueprint with ~40 REST endpoints for CPMP. Contracts, CLINs, WBS, deliverables, EVM, CPARS, subcontractors, COR portal. | (REST API) | JSON responses |

## Industry Research Engine (Phase 63)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Research Engine | tools/research/research_engine.py | Main orchestrator: 8-stage pipeline (SCOPE→DOSSIER), session lifecycle, daemon mode | --run, --run-stage, --status, --daemon, --json | Pipeline results JSON |
| Session Manager | tools/research/session_manager.py | Session CRUD, lifecycle management, vertical loading | --create, --list, --get, --advance, --json | Session data JSON |
| Vertical Loader | tools/research/vertical_loader.py | Load/validate vertical configs from JSON, store in DB | --load, --list, --get, --validate, --json | Vertical config JSON |
| Source Scanner | tools/research/source_scanner.py | 8-stream scanning: forums, reviews, academic, regulatory, OSS, SaaS, news, patents | --scan, --list-sources, --status, --json | Signal data JSON |
| Challenge Scorer | tools/research/challenge_scorer.py | 6-dimension weighted scoring: market, regulatory, technical, competition, readiness, compliance | --cluster, --score, --score-one, --top, --json | Challenge scores JSON |
| Regulatory Mapper | tools/research/regulatory_mapper.py | Map regulations to ICDEV™ crosswalk frameworks | --map, --landscape, --json | Regulatory mapping JSON |
| Capability Mapper | tools/research/capability_mapper.py | Map challenges to ICDEV™ capability catalog via keyword overlap | --map, --map-one, --coverage, --json | Capability mapping JSON |
| Build/Buy Analyzer | tools/research/build_buy_analyzer.py | Build/buy/partner decision matrix per challenge | --analyze, --analyze-one, --matrix, --json | Decision matrix JSON |
| Trend Detector | tools/research/trend_detector.py | Cross-session trend analysis with velocity/acceleration | --detect, --trends, --report, --json | Trend data JSON |
| Dossier Generator | tools/research/dossier_generator.py | Template-based Markdown dossier generation (no LLM, air-gap safe) | --generate, --list, --get, --review, --json | Dossier Markdown |
| Research MCP Server | tools/mcp/research_server.py | MCP server handlers for 10 research tools | (MCP stdio) | JSON-RPC responses |
| Research Config | args/research_config.yaml | Engine configuration: pipeline, sources, scoring, dossier, scheduling | (data) | YAML config |

## ANVIL Critique Phase (Phase 61 — Feature 3)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| ANVIL Critique | tools/agent/anvil_critique.py | Adversarial multi-agent plan critique: parallel dispatch to security/compliance/knowledge agents, severity classification, GO/NOGO/CONDITIONAL consensus, revision loop (max 3 rounds). Append-only findings (NIST AU). | --project-id, --phase-output, --session-id, --status, --history, --max-rounds, --json | Critique session + findings JSON |
| ANVIL Critique Config | args/anvil_critique_config.yaml | Critique phase config: critic agent assignments, focus areas, consensus rules, revision prompt, max rounds | (data) | YAML config |

## Universal RAG Subsystem (Phase 64)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Vector Store Provider | tools/rag/vector_store_provider.py | ABC + dataclasses (VectorChunk, SearchResult) for pluggable vector store backends (D-RAG-1) | (import) | ABC |
| SQLite Vector Store | tools/rag/sqlite_vector_store.py | Default vector store backend using BLOB embeddings with cosine similarity (numpy + pure-Python fallback) | (import) | VectorStoreProvider |
| ChromaDB Vector Store | tools/rag/chroma_vector_store.py | Optional ChromaDB backend with persistent collections and tenant-namespaced isolation | (import) | VectorStoreProvider |
| FAISS Vector Store | tools/rag/faiss_vector_store.py | Optional FAISS backend (faiss-cpu) with IndexFlatIP for fast approximate nearest neighbor search | (import) | VectorStoreProvider |
| Vector Store Factory | tools/rag/vector_store_factory.py | Config-driven backend selection: auto-detect ChromaDB → FAISS → SQLite fallback | (import) | VectorStoreProvider instance |
| Adaptive Chunker | tools/rag/chunker.py | Adaptive chunking: <500 tok whole, >2000 tok sliding window with 10% overlap at sentence boundaries (D-RAG-4) | text, source_type | List[VectorChunk] |
| Source Registry | tools/rag/source_registry.py | Declarative SOURCE_REGISTRY mapping 20+ source types to tables, columns, priority, chunk strategy | (import) | Registry dict |
| Ingestion Manager | tools/rag/ingestion_manager.py | Real-time + batch ingestion pipeline with content hash dedup, watermarking, CLI (D-RAG-9) | --ingest, --sweep, --status, --daemon, --json | Ingestion stats JSON |
| RAG Retriever | tools/rag/retriever.py | Two-stage retrieval: vector top-50 → BM25 boost → time-decay → qwen3 re-rank → top-5 (D-RAG-3) | --query, --json | Ranked results JSON |
| Re-ranker | tools/rag/reranker.py | qwen3 re-ranking via LLM router scanner_function (D-RAG-3) | query, chunks | Ranked chunk IDs |
| Retention Manager | tools/rag/retention_manager.py | Hot/warm/cold tier migration with float16 compression (D-RAG-6) | --migrate, --status, --json | Migration stats JSON |
| RAG Ingestion Hook | tools/extensions/builtins/020_rag_ingestion.py | Extension hook at TOOL_EXECUTE_AFTER for real-time ingestion (D-RAG-9) | (hook) | Auto-ingest |
| RAG MCP Server | tools/mcp/rag_server.py | 9 MCP tool handlers: search, ingest, status, chunk_info, delete, retention, reindex, history, providers | (MCP stdio) | JSON-RPC responses |
| RAG Config | args/rag_config.yaml | All RAG settings: vector store, embedding, chunking, retrieval, rerank, injection, ingestion, retention, provenance | (data) | YAML config |
| RAG Re-rank Prompt | hardprompts/rag_rerank.md | Re-ranking prompt template for qwen3 scanner_function | (hardprompt) | Prompt template |
| Source Mappings | context/rag/source_mappings.json | Declarative source type → table/column mappings (D26 pattern) | (data) | JSON mappings |
| Knowledge Search Page | tools/dashboard/templates/rag/knowledge_search.html | Dashboard page: stat grid, NLQ search, results with scores, source distribution chart, recent searches | (template) | HTML |

## File Sync (`tools/filesync/`)

| Tool | Purpose | CLI |
|------|---------|-----|
| `sync_engine.py` | Main orchestrator — job CRUD, sync execution, daemon mode, health | `--create/--list/--run/--daemon/--health --json` |
| `providers/base.py` | `SyncTargetProvider` ABC — list_files, read_file, write_file, delete_file, get_file_info | N/A (imported) |
| `providers/local.py` | Local filesystem provider (stdlib Path/os.walk) | N/A (imported) |
| `providers/sftp.py` | SFTP provider (paramiko + subprocess ssh/scp fallback) | N/A (imported) |
| `providers/cloud.py` | Cloud provider wrapping existing `StorageProvider` ABC | N/A (imported) |
| `ignore_parser.py` | `.syncignore` parser using stdlib `fnmatch` | N/A (imported) |
| `scanner.py` | File tree scanner — SHA-256 manifests with fast-skip (mtime+size) | N/A (imported) |
| `change_detector.py` | Manifest diffing → action plans (push + bidirectional) | N/A (imported) |
| `conflict_resolver.py` | Strategy pattern: last_write_wins, rename_both, source_wins, skip | N/A (imported) |
| `transfer.py` | ThreadPoolExecutor file transfer with bandwidth throttling | N/A (imported) |
| `watcher.py` | File watcher — optional watchdog + periodic scan fallback | N/A (imported) |

## Safety Hooks
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Pre-Tool-Use Hook | .claude/hooks/pre_tool_use.py | Blocks dangerous rm, .env access, audit modifications | tool_name, tool_input | Allow/block |

## Daemon Infrastructure — Shared Base Classes

| Tool | Path | Purpose |
|------|------|---------|
| DaemonBase | `tools/daemon/base.py` | ABC for all ICDEV™ daemons: signal handling, config loading, main loop, schedule parsing, circuit breaker, audit logging, CLI (--once, --status, --reflex, --enable, --disable, --reset, --json) |
| ReflexStateBase | `tools/daemon/base.py` | Thread-safe DB-backed reflex state management parameterized by `state_table` class attribute |
| TrustKernelBase | `tools/daemon/base.py` | Risk tier enforcement (GREEN=auto, YELLOW=sandbox, ORANGE=human review) |

## Genesis v2.0 — Autonomous Research Lab

### Core Engine

| Tool | Path | Purpose |
|------|------|---------|
| daemon | `tools/genesis/daemon.py` | Always-on daemon: 14 Reflexes, Trust Kernel, circuit breakers, schedule engine (D-GEN-1). Subclass of DaemonBase |
| promoter | `tools/genesis/promoter.py` | Knowledge Bridge: GKP export/import, dedup, auto-promote, human review gateway (D-GEN-4) |
| feedback_collector | `tools/genesis/feedback_collector.py` | Pull v1.x telemetry (failures, quality, coverage, heals) for v2.0 consumption (D-GEN-11) |
| reporter | `tools/genesis/reporter.py` | Weekly autonomous markdown report: reflex activity, promotions, circuit breakers (D-GEN-12) |

### 14 Reflexes (tools/genesis/reflexes/)

| Reflex | Risk Tier | Schedule | Purpose |
|--------|-----------|----------|---------|
| research | GREEN | every 6h | Scrape NIST/CISA/OWASP feeds, export GKP research signals |
| scout | GREEN | daily 07:00 | Monitor 16 GitHub repos (autoresearch, trivy, ollama, etc.), intel briefs |
| audit | GREEN | daily 06:00 | Self-scan: code quality + SAST via existing tools |
| report | GREEN | weekly Sun 20:00 | Generate weekly status report with promotions/circuit breakers |
| comply | GREEN | daily 09:00 | cATO evidence freshness, crosswalk sync, SbD assessment |
| ingest | GREEN | every 4h | RSS feeds → innovation_signals for knowledge enrichment |
| market | GREEN | daily 10:00 | Marketplace module usage analytics, improvement suggestions |
| publish | YELLOW | daily 08:00 | Demand topic → draft → WriteGuard → staging (never production) |
| test | YELLOW | nightly 03:00 | Find untested modules → generate test stubs → run → keep passing |
| learn | YELLOW | nightly 04:00 | Generate training pairs from approved outputs for Ollama fine-tuning |
| heal | YELLOW | continuous/5min | Pattern-match audit trail errors → auto-remediation (log-only v2.0) |
| evolve | ORANGE | nightly 02:00 | Pick worst-quality file → LLM analysis → propose GKP code_patch for human review |
| docs | GREEN | daily 06:00 | Documentation drift detection → GKP report |
| experiment | ORANGE | nightly 01:00 | Bayesian Autoresearch — Karpathy-loop autonomous experiments (D-AR-9) |

## Proposal Genesis — Autonomous Proposal Intelligence

### Core Engine

| Tool | Path | Purpose |
|------|------|---------|
| daemon | `tools/proposal_genesis/daemon.py` | Autonomous proposal intelligence daemon: 14 Reflexes across 4 phases (CAPTURE, PROPOSE, DELIVER, LEARN). Subclass of DaemonBase (D-PG-1) |

### 14 Reflexes (tools/proposal_genesis/reflexes/)

| Reflex | Phase | Risk Tier | Purpose |
|--------|-------|-----------|---------|
| discover | CAPTURE | GREEN | Scan SAM.gov, internal signals for new opportunities |
| scout | CAPTURE | GREEN | Competitive intelligence and market analysis |
| shape | CAPTURE | GREEN | Win strategy, discriminators, partner fit assessment |
| engage | CAPTURE | GREEN | CRM account/contact/engagement tracking |
| extract | PROPOSE | GREEN | Extract requirements from opportunity documents |
| map | PROPOSE | GREEN | Map requirements to ICDEV™ capabilities |
| draft | PROPOSE | GREEN | Generate proposal section drafts |
| polish | PROPOSE | GREEN | Grammar, readability, tone, AI detection quality checks |
| decide | PROPOSE | YELLOW | Bid/no-bid decision with scoring |
| monitor | DELIVER | GREEN | Track awarded contract performance |
| fulfill | DELIVER | GREEN | CDRL delivery tracking |
| publish | DELIVER | GREEN | Knowledge base article generation from wins |
| analyze | LEARN | GREEN | Win/loss analysis, lesson extraction |
| train | LEARN | GREEN | Generate fine-tuning pairs from approved content |

## AppForge — Autonomous Vertical App Builder

### Core Engine

| Tool | Path | Purpose |
|------|------|---------|
| daemon | `tools/appforge/daemon.py` | Autonomous vertical discovery + app builder + Pulse writer: 5 Reflexes (discover, evaluate, architect, build, publish) on a daily cycle. Subclass of DaemonBase. Enabled via `ICDEV_APPFORGE_ENABLED`. |

### 5 Reflexes (tools/appforge/reflexes/)

| Reflex | Risk Tier | Purpose |
|--------|-----------|---------|
| discover | GREEN | Scan Innovation/Creative/Research engines to find high-value vertical challenges |
| evaluate | GREEN | Score and select the top challenge to build |
| architect | GREEN | Generate app blueprint and specification |
| build | GREEN | Create standalone child app (Flask + SQLite + professional UI) |
| publish | GREEN | Write and publish Pulse article about the build |

## Workflow Discipline Engine (Phase 66)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Loop Engine | tools/workflow/loop_engine.py | PLAN→APPLY→UNIFY lifecycle manager (D-WF-1) | --create, --plan, --start-apply, --complete-task, --start-unify, --close, --status, --list, --json | Loop state + transitions |
| Reconciler | tools/workflow/reconciler.py | Planned-vs-actual delta for UNIFY phase (D-WF-7) | --reconcile, --get, --loop-id, --json | Reconciliation record |
| Next Action | tools/workflow/next_action.py | Single next action recommender (D-WF-4) | --recommend, --project-id, --json | Priority-ranked action |
| Handoff Generator | tools/workflow/handoff_generator.py | Structured session handoff documents (D-WF-5) | --generate, --list, --get, --project-id, --json | Handoff markdown + DB record |
| Process Verifier | tools/workflow/process_verifier.py | Verify required processes were invoked (D-WF-6) | --verify, --check, --loop-id, --project-id, --json | Pass/fail per process |
| Intake Bridge | tools/workflow/intake_bridge.py | Intake→workflow loop bridge (D-WF-1 + RICOAS) | --bridge, --check, --session-id, --json | Loop with seeded AC from BDD |
| Coherence Checker | tools/workflow/coherence_checker.py | Implementation coherence validator — 12 checks + 2-tier auto-fix (D-WF-8). Checks: schema_code, config_code, signature_call, fixture_schema, manifest, append_only, import_usage, ruff_lint (OPT-49), api_wiring, route_uniqueness, attribution_claims, llm_injection_patterns. Whitelist: args/ruff_gate.yaml. Wired into: Genesis audit, GKP promotion, CI/CD, marketplace, test orchestrator, heartbeat, production audit | --all, --check, --changed-files, --fix, --json, --human, --gate | Coherence report + auto-fix results |
| Impact Analyzer | tools/workflow/impact_analyzer.py | Cross-subsystem integration gap detection (D-WF-8f) | --analyze, --graph, --changed-files, --changed-tables, --json | Impact recommendations |

## Internal Awareness Engine (Phase 1a-1g)
Reads the ICDEV filesystem and populates `kg_nodes`/`kg_edges` under graph_id `kg-icdev-self-awareness`. Provides the `/components-map` dashboard page (JointJS tree + graph + hover + detail drawer) and a post-tool hook that auto-reindexes on every Edit/Write/NotebookEdit. Enablement-aware: disabled modules are indexed but visually dimmed and excluded from probing. See docs/features/internal-awareness-engine.md for the full plan.

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Enablement Helper | tools/awareness/enablement.py | Parses .env (with .env.example fallback) for `*_ENABLED` flags, hashes the flag set for drift detection, resolves component-level enablement via the declarative mapping | (library) | load_enablement_flags, enabled_flags_signature, load_enablement_map, is_component_enabled |
| Component Indexer | tools/awareness/component_indexer.py | Deterministic filesystem walker. 6 parsers: skill, mcp_server, canvas_module, goal, tool, reflex. Writes typed nodes + edges to kg_nodes/kg_edges. Per-file idempotent upsert_file() API used by the post-tool hook. | --scan, --scan --scope, --dry-run, --stats, --json | Node/edge counts + persistence summary |
| Health Prober | tools/awareness/health_prober.py | Phase 2 — runs deterministic health probes over every component in the graph. Ships 3 probe types: http_head (HEAD against canvas routes), module_import (AST parse), coherence_status (parse coherence_checker JSON). Writes to awareness_component_health with run_id tracking in the detail JSON blob. Enablement-aware (skips disabled components). Creates awareness_run_log + awareness_learned_edges on first use. | --run-all, --probe <type>, --stats, --json | Probe counts per type + run_id + elapsed_ms |
| Drift Detector | tools/awareness/drift_detector.py | Phase 2 — reads awareness_component_health snapshots, compares each (node, probe) against its 7-day rolling baseline, emits oracle_predictions rows under lens_name='internal_awareness' for detected regressions. 3 rules: route_regression (0.85), module_import_broken (0.95), coherence_new_fail (0.80). Dedupes against predictions created within the last 24h. | --detect, --dry-run, --stable-days, --baseline-days, --stats, --json | Findings per rule + prediction IDs |
| Suggested Card Writer | tools/awareness/suggested_card_writer.py | Phase 2 — promotes oracle_predictions rows to kanban_tasks with status='suggested'. Filters by confidence threshold (default 0.7), maps severity→priority and prediction_type→task_type, links via source_prediction_id, marks predictions outcome='promoted' to prevent re-processing. Title generator handles both `regression::<probe>` (drift) and `gap::<rule>` (gaps) kinds. | --write, --dry-run, --min-confidence, --limit, --stats, --json | Created card count + task IDs |
| Gap Detector | tools/awareness/gap_detector.py | Phase 3 — 7 default-on rules + 1 default-off (stale_code) for surfacing structural gaps: route_not_listed (0.90), tool_not_in_manifest (0.95), skill_references_missing_goal (0.95), orphan_db_table (0.85), broken_test_reference (0.90), route_no_e2e (0.70), empty_mcp_server (0.85). Writes to oracle_predictions with prediction_type='gap::<rule_id>' — reuses the Phase 2 suggested_card_writer plumbing. Each rule capped to prevent flooding. | --detect, --rule <id>, --dry-run, --include-disabled, --stats, --json | Per-rule finding/written counts + sample |
| Ask-ICDEV Q&A | tools/dashboard/app.py routes `/ask-icdev`, `/api/components-map/ask`, `/api/ask-icdev/sessions/*` | Phase 4 — unified Q&A over the Internal Awareness Engine. `/ask-icdev` is the dedicated chat page (three-pane: session list, transcript, citations). `/api/components-map/ask` runs parallel RAG + GraphRAG + health + suggested-kanban lookups. Narration opt-in via `narrate=true` using `LLMRouter.invoke(function=narrative_generation)` — portable across any Scanner-tier model, graceful fallback to raw evidence. Sessions persisted in `icdev_qa_sessions`/`icdev_qa_messages` tables (created on first use). | POST `/api/components-map/ask`, POST `/api/ask-icdev/sessions/<id>/message`, GET/DELETE session CRUD | Assistant turn + citations (rag/graph/health/suggested) |
| Orphan Table Generator | tools/awareness/orphan_table_generator.py | Phase 68 — Migration 017 DDL generator. Scans all SQL string literals to find tables referenced by INSERT/SELECT/FROM with no CREATE TABLE in any migration. Infers column schemas from INSERT column lists (54 known schemas + heuristic type inference). Generates idempotent CREATE TABLE IF NOT EXISTS for all orphans in tools/db/migrations/017_orphan_tables/. Human-review gate before --apply. Reduces orphan_db_table gap findings to 0. | --generate [--dry-run], --apply, --stats, --json | Orphan count + DDL per table + column inference sources |
| Post-Tool Hook | tools/awareness/hooks.py | Phase 44 TOOL_EXECUTE_AFTER subscriber. Filters Edit/Write/NotebookEdit on tracked extensions (.py .md .html .jinja2 .j2 .yaml .yml .json). Synchronously calls component_indexer.upsert_file() so kg_nodes refresh on every code change. Registered via auto-import at module load; dispatched asynchronously by post_tool_use.py so tool calls are never blocked. | (auto-registered via import) | Background node refresh |
| Phase Promoter | tools/awareness/promote_next_phase.py | **DEPRECATED 2026-04-11 (migration 015).** Superseded by native `kanban_tasks.depends_on_task_id`. Still functional for legacy phase tasks; new sequential workflows should set `depends_on_task_id` at create time so the listener's `_get_due_tasks` gate blocks dependent rows automatically. | --after <task_id>, --phase <N>, --list | JSON promotion summary |
| Value Scorer | tools/awareness/value_scorer.py | Ranks Oracle-suggested kanban cards by computed value = `confidence × rule_weight × (1 + dedup_boost × min(dup_count-1, dedup_cap))`. Weights live in `args/awareness_config.yaml` and hot-reload on every call via mtime-keyed lru_cache. Consumed by `tools/dashboard/api/kanban.py::list_tasks` when `?sort=value` is requested, and by the dashboard Suggested lane toolbar for bulk-select presets. | (library) | compute_value, compute_dup_counts, extract_subject, annotate_tasks_with_value |
| Enablement Map | args/awareness_enablement_map.yaml | Declarative entity_type + path_glob → required flags mapping. Seed covers 11 canvases + RAG/GovCon/FINETUNE/FileSync + dashboard routes + reflexes. | (data) | 23 mapping rules |

## Code Intelligence & Verification
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Formal Verifier | tools/analysis/formal_verifier.py | Property-based security checks (LeanStral-adapted, D-VL-6) | --file, --project-dir, --gate, --generate-properties, --json | Formal check results |
| Verify Loop | tools/analysis/verify_loop.py | Compiler-in-the-loop verification (LeanStral-adapted, D-VL-1) | --file, --project-dir, --repair, --gate, --json | Verification results |
| Session Purpose | tools/agent/session_purpose.py | Session purpose declaration for NIST AU-3 (D-ORCH-5) | --declare, --active, --complete, --history, --json | Purpose records |
| CLI Harmonizer | tools/compat/cli_harmonizer.py | CLI argument normalization | (library) | Harmonized CLI args |
| CLI Formatter | tools/cli_formatter.py | ANSI terminal output formatter | (library) | Colored CLI output |

## AI Transparency & Accountability (Phase 48-49)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Accountability Manager | tools/compliance/accountability_manager.py | AI oversight plans, CAIO, appeals, ethics (D316-D321) | --summary, --register-oversight, --designate-caio, --json | Accountability records |
| AI Accountability Audit | tools/compliance/ai_accountability_audit.py | Cross-framework accountability audit (D316-D321) | --project-id, --json | Audit report |
| AI Impact Assessor | tools/compliance/ai_impact_assessor.py | Algorithmic impact assessment (D320) | --project-id, --ai-system, --json | Impact assessment |
| AI Incident Response | tools/compliance/ai_incident_response.py | AI incident logging and stats (D318) | --log, --stats, --project-id, --json | Incident records |
| AI Inventory Manager | tools/compliance/ai_inventory_manager.py | OMB M-25-21 AI system inventory (D312) | --register, --list, --export, --json | Inventory records |
| AI Reassessment Scheduler | tools/compliance/ai_reassessment_scheduler.py | Reassessment schedule manager (D316) | --create, --overdue, --json | Schedule records |
| AI Transparency Audit | tools/compliance/ai_transparency_audit.py | Cross-framework transparency audit (D307-D315) | --project-id, --json, --human | Audit report |
| Classification Resolver | tools/compliance/classification_resolver.py | Dynamic classification resolution per project | (library) | Classification level |
| Compliance Exporter | tools/compliance/compliance_exporter.py | Multi-format compliance artifact export | --project-id, --format, --json | Exported artifacts |
| Fairness Assessor | tools/compliance/fairness_assessor.py | AI fairness compliance assessment (D311) | --project-id, --gate, --json | Fairness assessment |
| GAO AI Assessor | tools/compliance/gao_ai_assessor.py | GAO-21-519SP AI accountability assessment | --project-id, --json | Assessment results |
| GAO Evidence Builder | tools/compliance/gao_evidence_builder.py | GAO evidence collection from ICDEV™ data (D313) | --project-id, --json | Evidence bundle |
| Model Card Generator | tools/compliance/model_card_generator.py | Google-format model cards (D308) | --project-id, --model-name, --json | Model card |
| Narrative Generator | tools/compliance/narrative_generator.py | Compliance narrative workflow (F4) | --project-id, --batch, --pending, --json | Narrative drafts |

## AI Compliance Assessors (Phase 48-49 — Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| NIST AI 600-1 Assessor | tools/compliance/nist_ai_600_1_assessor.py | NIST AI 600-1 GenAI Profile assessment | --project-id, --json | Assessment results |
| OMB M-25-21 Assessor | tools/compliance/omb_m25_21_assessor.py | OMB M-25-21 High-Impact AI assessment | --project-id, --json | Assessment results |
| OMB M-26-04 Assessor | tools/compliance/omb_m26_04_assessor.py | OMB M-26-04 Unbiased AI assessment | --project-id, --json | Assessment results |
| System Card Generator | tools/compliance/system_card_generator.py | ICDEV™-specific system cards (D309) | --project-id, --json | System card |

## Creative Engine (Phase 58)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Creative Engine | tools/creative/creative_engine.py | Customer-centric feature opportunity discovery (D351-D360) | --run, --discover, --scan, --extract, --score, --rank, --generate, --json | Pipeline results |
| Competitor Discoverer | tools/creative/competitor_discoverer.py | Auto-discover competitors from category pages (D353) | --discover, --list, --confirm, --json | Competitor records |
| Gap Scorer | tools/creative/gap_scorer.py | 3-dimension composite scoring (D355) | --score-all, --top, --gaps, --json | Scored gaps |
| Pain Extractor | tools/creative/pain_extractor.py | Deterministic keyword-based pain point extraction (D354) | --extract-all, --json | Pain points |
| Spec Generator | tools/creative/spec_generator.py | Template-based feature spec generation (D356) | --generate-all, --list, --json | Feature specs |
| Trend Tracker | tools/creative/trend_tracker.py | Velocity/acceleration trend detection | --detect, --report, --json | Trend data |

## DataBridge
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Connection Manager | tools/databridge/connection_manager.py | DataBridge connection lifecycle management | --create, --list, --test, --json | Connection records |
| Schema Engine | tools/databridge/schema_engine.py | DataBridge schema discovery and mapping | --discover, --map, --json | Schema maps |
| Health Base | tools/databridge/connectors/health_base.py | Base class for health-check connectors (D-CF-5) | (library) | HealthConnector ABC |
| SOAP Base | tools/databridge/connectors/soap_base.py | Base class for SOAP/XML-RPC connectors (D-CF-5) | (library) | SoapConnector ABC |
| Forge Agent | tools/databridge/forge/forge_agent.py | Generate connector from OpenAPI spec — template + optional LLM (D-CF-2) | (library) — `forge_from_spec()` | Generated connector |
| Forge Spec Parser | tools/databridge/forge/spec_parser.py | Parse OpenAPI/Swagger specs into normalized schema | (library) | Parsed spec |
| Forge Static Validator | tools/databridge/forge/static_validator.py | Validate generated connector against ABC contract | (library) | Validation results |
| Forge Base Selector | tools/databridge/forge/base_selector.py | Select appropriate connector base class from spec | (library) | Base class selection |
| Forge Integration Tester | tools/databridge/forge/integration_tester.py | Docker/subprocess sandbox testing for generated connectors (D-CF-4) | (library) | Test results |
| Forge Import Handler | tools/databridge/forge/import_handler.py | Import and register generated connectors | (library) | Registration result |
| Forge Marketplace Publisher | tools/databridge/forge/marketplace_publisher.py | Publish forge connectors to marketplace (D-CF-8) | (library) | Published asset |
| Forge Community Hub | tools/databridge/forge/community_hub.py | Browse, rate, and manage community connectors (F10) | --browse, --featured, --json | Connector listings |
| Scale Worker Pool | tools/databridge/scale/worker_pool.py | ThreadPoolExecutor wrapper for concurrent sync (D-SC-1) | (library) | WorkerPool |
| Scale Write Batcher | tools/databridge/scale/write_batcher.py | WAL + batch flush for sync log/audit writes (D-SC-3) | (library) | WriteBatcher |
| Scale Backpressure | tools/databridge/scale/backpressure.py | Backpressure monitoring with optional psutil (D-SC-6) | (library) | Pressure metrics |
| Scale Chunked Pipeline | tools/databridge/scale/chunked_pipeline.py | Chunked data pipeline for large sync operations | (library) | Pipeline results |

## File Sync (D-SYNC-1 through D-SYNC-12)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Register Competitors | tools/filesync/register_competitors.py | Register competitor sources for sync | --register, --list, --json | Competitor records |
| Service Manager | tools/filesync/service_manager.py | File sync service lifecycle | --start, --stop, --status, --json | Service status |
| Versioner | tools/filesync/versioner.py | File version tracking | --snapshot, --diff, --json | Version records |

## Fine-Tuning (Phase 64 Extension)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| A/B Evaluator | tools/finetune/ab_evaluator.py | Model A/B comparison (D-FT-15) | --model-a, --model-b, --json | Comparison results |
| Azure Provider | tools/finetune/azure_provider.py | Azure OpenAI fine-tuning provider (D-FT-20) | (library) | AzureOpenAIFineTuneProvider |
| Dataset Manager | tools/finetune/dataset_manager.py | Dataset CRUD and versioning (D-FT-9) | --create, --list, --export, --json | Dataset records |
| Doc Extractor | tools/finetune/doc_extractor.py | Document extraction for training pairs (D-FT-11) | --extract, --json | Extracted text |
| GGUF Exporter | tools/finetune/gguf_exporter.py | GGUF model export with Q4_K_M quantization (D-FT-5) | (library) | GGUF files |
| GPU Detector | tools/finetune/gpu_detector.py | GPU auto-detection for training (D-FT-8) | --json | GPU info |
| Labeler | tools/finetune/labeler.py | Dataset example labeling engine (D-FT-12) | (library) | Label results |
| Model Registry | tools/finetune/model_registry.py | Fine-tuned model version tracking (D-FT-7) | (library) | Model records |
| Pair Generator | tools/finetune/pair_generator.py | Q&A training pair generation from RAG (D-FT-10) | --generate-filtered, --dataset-id, --json | Training pairs |
| Promotion Manager | tools/finetune/promotion_manager.py | Model auto-promotion pipeline (D-FT-16) | --check, --promote, --json | Promotion results |
| Retrain Trigger | tools/finetune/retrain_trigger.py | Auto-retrain when threshold exceeded (D-FT-17) | --check, --json | Trigger status |
| Training Engine | tools/finetune/training_engine.py | Unsloth/cloud QLoRA training (D-FT-2) | --dataset-id, --provider, --json | Training job |
| Unsloth Provider | tools/finetune/unsloth_provider.py | Local Unsloth QLoRA provider (D-FT-2) | (library) | UnslothLocalProvider |
| RAG-FT Pipeline | tools/finetune/rag_ft_pipeline.py | Automated RAG-to-FT pipeline (D-KARL-5) | --run, --dry-run, --status, --json | Pipeline results |
| KG Pair Generator | tools/finetune/kg_pair_generator.py | KG community-based FT pair generation (D-KARL-6) | --graph-id, --dataset-id, --strategy, --json | Generated pairs |
| Quality Monitor | tools/finetune/quality_monitor.py | RAG eval feedback loop with retrain triggers (D-KARL-8) | --check, --status, --json | Quality status |
| HP Search | tools/finetune/hp_search.py | Hyperparameter search orchestrator for fine-tuning (grid/random search over LoRA params) | --create, --run-next, --record, --status, --list, --json | Search/trial results |
| Trajectory Capture | tools/finetune/trajectory_capture.py | Auto-capture successful agent tool-call traces as ShareGPT JSONL training data; compliance/build workflows → RL trajectories | --capture, --loop-id, --export, --stats, --gate, --workflow-type, --include-events, --json | Captured trajectories + JSONL export |

## Remote Command Gateway (Phase 28)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Command Router | tools/gateway/command_router.py | Route remote commands to ICDEV™ tools | (library) | Routed results |
| Event Envelope | tools/gateway/event_envelope.py | HMAC-signed event envelope (D31) | (library) | Signed events |
| Response Filter | tools/gateway/response_filter.py | IL-aware response classification filter (D135) | (library) | Filtered responses |
| Security Chain | tools/gateway/security_chain.py | 8-gate security chain for remote commands | (library) | Chain results |

## Genesis Launcher
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Genesis Launcher | tools/genesis/launcher.py | Genesis daemon launcher and control | (library) | Daemon lifecycle |

## Harness Engineering (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| CLI Generator | tools/harness/cli_generator.py | Generate CLI harness for child apps | (library) | CLI scaffold |

## Knowledge Graph & GraphRAG
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Graph RAG | tools/knowledge_graph/graph_rag.py | GraphRAG retrieval with scoring profiles (D-KARL-1) | --query, --profile, --json | Retrieval context |
| KG Ingester | tools/knowledge_graph/ingester.py | Knowledge graph document ingestion | --file, --project-id, --json | Ingestion result |
| Insight Generator | tools/knowledge_graph/insight_generator.py | AI insight generation from graph (scanner-tier) | --graph-id, --questions, --bridge-gaps, --json | Insights |
| Text Network | tools/knowledge_graph/text_network.py | Text-to-knowledge-graph conversion | --text, --project-id, --json | Graph data |
| KG Enricher | tools/knowledge_graph/enricher.py | Centrality + embedding computation (D-KARL-7) | --graph-id, --centrality, --embeddings, --json | Enrichment results |
| Compliance Graph | tools/knowledge_graph/compliance_graph.py | Compliance crosswalk as knowledge graph — NIST/FedRAMP/CMMC controls as nodes with crosswalk edges | --build, --crosswalk, --coverage, --target, --json | Graph/crosswalk/coverage results |
| Disambiguator | tools/knowledge_graph/disambiguator.py | Entity disambiguation — find duplicates, merge entities, add aliases, resolve ambiguous labels | --find-duplicates, --merge, --add-alias, --resolve, --json | Disambiguation results |
| Federation | tools/knowledge_graph/federation.py | Cross-project graph federation — federated search, shared entities, federated views, cross-project coverage | --search, --shared, --create-view, --coverage, --json | Federation results |
| Temporal | tools/knowledge_graph/temporal.py | Temporal reasoning — time range queries, graph evolution, recent changes, stale entities, temporal diffs | --range, --evolution, --recent, --stale, --diff, --json | Temporal results |

## LLM Providers (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Gemini Provider | tools/llm/gemini_provider.py | Google Vertex AI Gemini LLM provider | (library) | GeminiProvider |

## LLM Provider SDK
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Provider SDK | tools/llm/provider_sdk.py | LLM provider SDK utilities | (library) | Provider helpers |

## Marketplace (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Asset Installer | tools/marketplace/asset_installer.py | Install marketplace assets into project | --install, --json | Installation result |
| License Client | tools/marketplace/license_client.py | Offline license sync/verify/renew (D-MKT-S4) | (library) | License status |
| Module Runtime | tools/marketplace/module_runtime.py | Module gating runtime (D-MKT-S4) | (library) | is_module_enabled() |
| Token Store | tools/marketplace/token_store.py | Local JSON token cache (D-MKT-S4) | (library) | Token management |

## Playground
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Seed Data | tools/playground/seed_data.py | Seed demo/test data into databases | --seed, --json | Seed results |

## Proposal Genesis CRM
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| CRM CLI | tools/proposal_genesis/crm_cli.py | Lightweight CRM account/contact management | --list, --add, --json | CRM records |

## RAG Subsystem (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Auto Indexer | tools/rag/auto_indexer.py | Automatic RAG index maintenance | --index, --json | Index status |
| Corrective RAG | tools/rag/corrective_rag.py | Parallel multi-strategy retrieval (D-KARL-3) | --parallel, --query, --profile, --json | Merged results |
| PDF Provider | tools/rag/pdf_provider.py | PDF text extraction for RAG ingestion (D-FT-11) | (library) | Extracted text |
| Reranker Provider | tools/rag/reranker_provider.py | Two-stage re-ranking provider (D-RAG-3) | (library) | Reranked results |
| Secret Ref | tools/rag/secret_ref.py | Secret reference resolver for RAG | (library) | Resolved refs |
| Codebase Indexer | tools/rag/codebase_indexer.py | AST-based Python + text codebase indexer for assistant widget (D-CA-1, D-CA-2) | --scan, --scope, --json | Index status |
| CRAG Evaluator | tools/rag/crag_evaluator.py | CRAG benchmark evaluation — 8 question types, hallucination-penalizing scoring (D-RAG-23) | --benchmark-crag, --classify-question, --score, --gate, --json | Campaign results |
| Query Classifier | tools/rag/query_classifier.py | 4-label taxonomy classifier for RAG queries (D-RAG-24) | --classify --query, --classify-batch --input, --json | Label + confidence |
| Quality Feedback Loop | tools/rag/quality_feedback_loop.py | Closed-loop RAG quality → retrain pipeline (D-KARL-9) | --run, --dry-run, --status, --json | Cycle results |
| Statement Extractor | tools/finetune/statement_extractor.py | Grounded Q&A pair generation via statement extraction (D-FT-23) | --extract, --extract-from-rag, --stats, --json | Pairs + taxonomy labels |

## Codebase Assistant (Phase 69)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Assistant Config | tools/dashboard/assistant_config.py | Route-to-module mapping + security exclusions (D-CA-4, D-CA-9) | --json | Config data |
| Assistant Manager | tools/dashboard/assistant_manager.py | Codebase Q&A query handler with RAG + cache (D-CA-5 to D-CA-8) | (library) | Query results |

## Requirements (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Complexity Scorer | tools/requirements/complexity_scorer.py | Scale-adaptive complexity scoring | --session-id, --json | Complexity score |
| Elicitation Techniques | tools/requirements/elicitation_techniques.py | BMAD-inspired elicitation technique engine | --list, --activate, --json | Technique prompts |
| PRD Generator | tools/requirements/prd_generator.py | Product Requirements Document generation | --session-id, --json | PRD markdown |
| PRD Validator | tools/requirements/prd_validator.py | PRD quality validation (6 checks) | --session-id, --validate, --json | Validation results |

## Research Engine (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Forecast Generator | tools/research/forecast_generator.py | Cross-engine prediction with surprise scoring (D-RES-17) | --generate, --session-id, --json | Forecast predictions |
| YouTube Scanner | tools/research/youtube_scanner.py | YouTube video transcript scanning (D-RES-14) | --scan, --queries, --urls, --json | Video signals |

## Security (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Confabulation Detector | tools/security/confabulation_detector.py | Deterministic confabulation detection (D310) | --check-output, --summary, --json | Detection results |
| Endpoint Security Scanner | tools/security/endpoint_security_scanner.py | API endpoint security assessment | --scan, --json | Scan results |
| Sandbox Executor | tools/security/sandbox_executor.py | Container-isolated code execution with resource limits, network isolation, and audit logging (D-SEC-10) | --execute --code, --execute-file --path, --health, --gate, --language, --timeout, --memory, --json | SandboxResult JSON |

## Testing (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Claude Dir Validator | tools/testing/claude_dir_validator.py | .claude directory governance validator (9 checks) | --json, --human, --check, --all | Alignment report |

## WriteGuard — Writing Quality Analysis
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Analysis Engine | tools/writing/analysis_engine.py | Unified deterministic quality analysis (grammar, readability, tone, plagiarism, AI detection) | (library) — `analyze(text)` | Quality scores per dimension |
| Grammar Checker | tools/writing/grammar_checker.py | Thin wrapper re-exporting grammar check from analysis_engine | (library) — `check_grammar(text)` | Grammar issues list |
| Readability Scorer | tools/writing/readability_scorer.py | Thin wrapper re-exporting readability scoring from analysis_engine | (library) — `score_readability(text)` | Readability metrics (Flesch, grade level) |
| Tone Profiler | tools/writing/tone_profiler.py | Thin wrapper re-exporting tone profiling from analysis_engine (scanner-tier LLM) | (library) — `profile_tone(text)` | Tone classification + confidence |
| Plagiarism Detector | tools/writing/plagiarism_detector.py | Thin wrapper re-exporting plagiarism check from analysis_engine (RAG similarity, 0.85 threshold) | (library) — `check_plagiarism(text)` | Similarity scores |
| AI Content Detector | tools/writing/ai_content_detector.py | Deterministic AI detection — perplexity, burstiness, n-gram stats (D-WG-6, advisory-only) | (library) — `detect_ai_content(text)` | AI probability + signals |
| LLM Judge | tools/writing/llm_judge.py | Rubric-based semantic evaluation using Prometheus-2 7B (local Ollama) — 5 dimensions per content type | --text, --rubric, --json | Color ratings + scores |
| Ubiquitous Language | tools/writing/ubiquitous_language.py | DDD glossary extractor — noun-phrase extraction, synonym + ambiguity detection, optional LLM enrichment via LLMRouter. Adapted from mattpocock/skills/ubiquitous-language (MIT) | --input FILE, --out FILE, --llm-enrich, --append-to-memory, --json, --gate | UBIQUITOUS_LANGUAGE.md + JSON summary |

## Pulse AI Blog Engine
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Capability Scanner | tools/pulse/engine/capability_scanner.py | Load capability YAMLs and match to article topics via deterministic keyword scoring (D-PULSE-CAP-1) | --list, --domains, --match, --format-context, --json | Matched capabilities |
| Demand Detector | tools/pulse/engine/demand_detector.py | Identify unmet capability gaps from SAM.gov pain points, build capability graph (D-PULSE-CAP-2/3) | --detect, --aggregate, --high-demand, --suggest-articles, --graph, --json | Demand signals + suggestions |
| SAM Bridge | tools/pulse/engine/sam_bridge.py | SAM.gov to Pulse article pipeline — extract pain points from solicitations, generate articles | --run, --dry-run, --list-pending, --stats, --json | Generated articles |
| Researcher | tools/pulse/engine/researcher.py | Web research engine — scrape DuckDuckGo for developer pain points across Reddit/SO/HN/LinkedIn/DEV.to | (library) — `research(topic)` | Research cache entries |
| Topic Clusterer | tools/pulse/engine/topic_clusterer.py | Group related pain points into coherent article themes via TF-IDF keyword overlap (stdlib only) | (library) — `cluster_topics(items)` | Topic clusters |
| SEO Optimizer | tools/pulse/engine/seo_optimizer.py | SEO optimization — title/meta tuning, keyword extraction, JSON-LD schema, YAML frontmatter | (library) — `optimize(post)` | SEO metadata |
| Image Generator | tools/pulse/engine/image_generator.py | Local GPU-accelerated hero image generation via SDXL Turbo (optional, requires GPU) | (library) — `generate_image(prompt)` | Image file path |
| Video Finder | tools/pulse/engine/video_finder.py | YouTube/Vimeo video search for blog embeds — no API keys (web scraping + oEmbed) | (library) — `find_videos(query)` | Video URLs + metadata |
| Video Generator | tools/pulse/engine/video_generator.py | Local GPU-accelerated video generation via LTX-Video 2B (optional, requires GPU) | (library) — `generate_video(prompt)` | Video file path |
| WordPress Publisher | tools/pulse/engine/wordpress_publisher.py | Publish Pulse posts to WordPress (icdev.ai) via XML-RPC API | (library) — `publish(post)` | Published post URL |
| Hostinger Publisher | tools/pulse/engine/hostinger_publisher.py | Publish Pulse posts to Hostinger Website Builder via browser automation | (library) — `publish(post)` | Published post URL |

## Testing (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| GovEval Benchmark | tools/testing/goveval.py | 7-dimension Gov/DoD compliance quality benchmark (LeanStral FLTEval-adapted, D-VL-9) | --project-id, --dimension, --gate, --trend, --compare, --json | Dimension scores + gate |
| Platform Check | tools/testing/platform_check.py | OS environment compatibility validation (D145) | --json | Compatibility report |
| Claude Dir Validator | tools/testing/claude_dir_validator.py | .claude directory governance validator (9 checks) | --json, --human, --check, --all | Alignment report |

## Evaluation & Red Teaming (Phase 65)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Red Team Registry | tools/security/red_team_registry.py | YAML-driven adversarial testing framework (6 plugins, promptfoo-inspired) | --run-all, --plugin, --category, --gate, --list, --project-id, --json | Plugin results + gate evaluation |
| Convergence Gates | tools/genesis/convergence.py | Detect phantom improvements and reflex plateau (3 drift vectors + ambiguity, Ouroboros-inspired) | (library — called by daemon post-reflex hook) | Drift scores + recommendation |
| Stagnation Detector | tools/genesis/stagnation_detector.py | Detect stuck reflexes, break plateaus via 5 lateral thinking personas (Ouroboros-inspired) | (library — called by daemon when convergence flags stagnation) | Pattern detection + alternatives |
| Agent Benchmark | tools/evaluation/agent_benchmark.py | Scenario-based 2-tier evaluation of ICDEV™ agents (12 scenarios, 4 agent types, TheAgentCompany-inspired) | --run-all, --agent-type, --scenario, --trend, --gate, --list, --json | Per-agent scores + trend + gate |

## Bayesian Autoresearch (Phase 67, D-AR-1 through D-AR-10)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Experiment Engine | tools/autoresearch/experiment_engine.py | Core Karpathy Loop — create, run, evaluate, decide, autonomous loop (D-AR-1) | --create, --run, --evaluate, --decide, --loop, --status, --health, --domain, --experiment-id, --max-experiments, --overnight, --json | Experiment results + decisions |
| Bayesian Selector | tools/autoresearch/bayesian_selector.py | Bayesian info-gain experiment selection + Thompson Sampling + pgvector dedup (D-AR-5, D-AR-6) | --score, --select, --estimate, --category-order, --health, --domain, --json | Scored candidates + selection |
| Fitness Evaluator | tools/autoresearch/fitness_evaluator.py | Wraps 6 ICDEV™ tools into single-metric [0,1] scorers (D-AR-7) | --evaluate, --evaluate-all, --list-domains, --health, --project-id, --project-dir, --json | Domain metric values |
| Hypothesis Generator | tools/autoresearch/hypothesis_generator.py | Scanner-tier LLM + template fallback hypothesis creation (D-AR-1) | --domain, --max, --from-signals, --health, --json | Hypothesis candidates |
| Experiment Reflex | tools/genesis/reflexes/experiment.py | 14th Genesis reflex — Bayesian Autoresearch at ORANGE tier (D-AR-9) | config dict, trust kernel | Reflex results + GKP export |

## SRE — Site Reliability Engineering
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| SLO Manager | tools/sre/slo_manager.py | SLO manager: define SLOs, record measurements, burn rate calculation, dashboard, gate check | --define, --record, --burn-rate, --dashboard, --gate, --json | SLO status + burn rates |
| Runbook Executor | tools/sre/runbook_executor.py | Runbook executor: register runbooks, match alerts, risk-tiered execution, dry-run, rollback | --register, --match, --execute, --dry-run, --rollback, --list, --json | Execution results + rollback status |
| Incident Commander | tools/sre/incident_commander.py | Incident commander: full incident lifecycle (detected→closed), auto-escalation, MTTR tracking, postmortem | --create, --escalate, --resolve, --close, --postmortem, --mttr, --list, --json | Incident status + MTTR metrics |
| SRE Config | args/sre_config.yaml | SRE config: SLO definitions, burn rate thresholds, runbook registry, escalation policies, incident severity levels | (data) | YAML config |

## Redaction & Data Protection (Phase 70 — D-RDT-1)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Redaction Detector | tools/redaction/detector.py | Presidio + custom recognizer PII/sensitive data detection engine | --detect, --detect-file, --list-entities, --health, --json, --gate | Detection results |
| Redaction Anonymizer | tools/redaction/anonymizer.py | Anonymization engine with IL-aware operators (surrogate/redact/mask/hash) | --anonymize, --anonymize-file, --il, --session, --show-text, --health, --json, --gate | Anonymized text + metadata |
| NER Recognizer | tools/redaction/ner_recognizer.py | Ollama gemma3 NER for PERSON/ORGANIZATION + regex fallback (air-gap safe) | --extract, --no-ollama, --health, --json, --gate | Named entities |
| GovCon Recognizers | tools/redaction/govcon_recognizers.py | Custom recognizers for contract#, CAGE, pricing, program names, orgs, custom terms | --list, --json | Recognizer definitions |
| Redaction Registry | tools/redaction/registry.py | Conversation-scoped real↔surrogate mapping with SQLite persistence | --session, --list, --cleanup, --health, --json | Mapping entries |
| GovCon Sanitizer | tools/redaction/govcon_sanitizer.py | Pre-LLM hook: sanitizes proposal content before cloud LLM invocation | --sanitize, --sanitize-file, --function, --il, --local-only, --show-text, --health, --json, --gate | Sanitized text + metadata |
| Pulse Sanitizer | tools/redaction/pulse_sanitizer.py | Pulse case study de-identification (agency, program, pricing, past perf) | --sanitize-article, --title, --body, --tags, --health, --json, --gate | Sanitized article |
| DB PII Scanner | tools/redaction/db_scanner.py | Scan proposal DB tables for PII density per column | --scan, --table, --sample-size, --health, --json, --gate | PII density report |
| Redaction Config | args/redaction_config.yaml | Global redaction config: entities, thresholds, operators, IL overrides, scope, audit | (data) | YAML config |
| GovCon Redaction Config | args/redaction_govcon.yaml | GovCon-specific: program deny-list, contract patterns, pricing patterns, past perf rules, Pulse sanitization | (data) | YAML config |

## ICDEV™ Studio — Low-Code/No-Code Platform (Phase 72 — D361-D366)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Studio DB Init | tools/studio/init_db.py | Initialize 9 studio_* tables (PG + SQLite compatible, idempotent) | --json, --verbose | Table creation summary |
| Workflow Editor | tools/studio/workflow_editor.py | Workflow CRUD, tool catalog (5 categories, 22 tools), YAML validation | --json catalog, templates, list, get <id> | Tool catalog, templates, workflows |
| Studio API | tools/dashboard/api/studio.py | Flask Blueprint: workflow CRUD, tool catalog, marketplace storefront API | REST endpoints under /api/studio/ | JSON responses |
| Form Builder | tools/studio/form_builder.py | Form schema CRUD, JSON Schema output, 10 field types, 4 pre-built templates, submissions | --json field-types, templates, list, get <id> | Form schemas + submissions |
| Case Manager | tools/studio/case_manager.py | Case lifecycle engine: FSM state machine, Kanban board, SLA rules, 3 lifecycle templates | --json templates, types, cases, board <type_id> | Case data + board view |
| Dashboard Builder | tools/studio/dashboard_builder.py | Custom widget layouts: 15 widget types, 3 role defaults (PM/ISSO/Dev), save/share | --json widgets, roles, list, create-default <role> | Dashboard layouts |
| Automation Builder | tools/studio/automation_builder.py | Citizen automation: trigger/condition/action rules, 10 triggers, 8 actions, 5 templates, simulate | --json triggers, operators, actions, templates, list, runs, simulate <id> | Automation rules + run history |
| NL App Builder | tools/studio/nl_app_builder.py | NL-to-blueprint pipeline: extract capabilities from description, create session, refine, build child app | --json extract <desc>, create <desc> --name, refine <id> | Blueprint preview + build result |
| Studio CSS | tools/dashboard/static/css/studio.css | Premium design system: glass cards, gradients, animations, 8px grid | (asset) | CSS |
| Workflow Studio JS | tools/dashboard/static/js/workflow-studio.js | DAG canvas editor: drag-drop nodes, SVG edges, zoom, validate, YAML import/export | (asset) | JS |
| Marketplace JS | tools/dashboard/static/js/marketplace.js | Asset browser: search, filter, sort, detail modal, one-click install | (asset) | JS |

## A2A Protocol (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Task Lease | tools/a2a/task_lease.py | Task lease management for agent coordination | --json | Lease status |

## Autonomy Engine
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Behavior Learner | tools/autonomy/behavior_learner.py | Behavioral pattern learning from agent actions | --json | Learned patterns |
| Federation Router | tools/autonomy/federation.py | Engine federation router — auto-routes signals between Innovation/Creative/Research engines via Bayesian trust gate (D-AE-11) | --check / --route / --dry-run / --status --json | Routeable signals / routing results |
| Kill Switch | tools/autonomy/kill_switch.py | Emergency agent termination control | --json | Termination status |
| Self Evolve | tools/autonomy/self_evolve.py | Self-evolution capability engine | --json | Evolution results |
| Trust Engine | tools/autonomy/trust_engine.py | Dynamic trust scoring for autonomous actions | --json | Trust scores |

## Autoresearch (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Fitness Proposal Quality | tools/autoresearch/fitness_proposal_quality.py | Proposal quality fitness evaluator | --json | Quality scores |
| Marketplace Exporter | tools/autoresearch/marketplace_exporter.py | Export autoresearch results to marketplace | --json | Export results |

## Dashboard API (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| CI/CD API | tools/dashboard/api/cicd.py | CI/CD pipeline status API endpoints | REST endpoints | JSON responses |
| Prod Audit API | tools/dashboard/api/prod_audit.py | Production audit API endpoints | REST endpoints | JSON responses |
| Proposals API | tools/dashboard/api/proposals.py | Proposal management API endpoints | REST endpoints | JSON responses |
| Control Inheritance API | tools/dashboard/api/control_inheritance.py | Flask Blueprint: FedRAMP control inheritance visualizer — CSP profiles (AWS GovCloud, Azure Gov, GCP, OCI, IBM, on-prem), responsibility model (inherited/shared/customer) per NIST 800-53 family, gap analysis for customer-owned controls | GET /api/control-inheritance/csps, /model?csp=, /summary?csp=&project_id=, /controls?csp=&family=&responsibility=, /gap?project_id=&csp= | JSON |
| Kanban Plan API | tools/dashboard/api/kanban_plan.py | Flask Blueprint: kanban task decomposition and scheduling endpoints | GET /api/kanban/plans | JSON plan list |
| PR Intel API | tools/dashboard/api/pr_intel.py | Flask Blueprint: PR intelligence and compliance drift — aggregate stats, paginated report list/detail, compliance drift by control family, PR analysis trigger | GET /api/pr-intel/stats, /api/pr-intel/reports, /api/pr-intel/reports/<id>, /api/pr-intel/drift; POST /api/pr-intel/analyze | JSON responses |
| STIG Manager API | tools/dashboard/api/stig_manager.py | Flask Blueprint: STIG benchmark management — overall stats, benchmark list, paginated findings, finding detail, coverage heatmap by target/severity, open CAT1 blockers, and finding status assessment | GET /api/stig-manager/stats, /benchmarks, /findings, /findings/<id>, /coverage, /cat1; POST /api/stig-manager/assess | JSON responses |

## DataBridge (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Alpaca Connector | tools/databridge/connectors/alpaca_connector.py | Alpaca Markets trading API connector (equities + crypto) | --json | Connection status |
| SaaS Base Connector | tools/databridge/connectors/saas_base.py | REST/SaaS API base connector class for Connector Forge | (library) | SaaSBaseConnector class |
| Sandbox Adapter | tools/databridge/forge/sandbox_adapter.py | Sandbox environment adapter for connector testing | --json | Adapter status |
| Sandbox Manager | tools/databridge/forge/sandbox_manager.py | Sandbox lifecycle manager for generated connectors | --json | Sandbox status |

## Database (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Migrate Add Missing Columns | tools/db/migrate_add_missing_columns.py | Add missing columns migration utility | --json | Migration results |
| Migrate to Storage | tools/db/migrate_to_storage.py | Migrate to centralized storage module | --json | Migration results |
| PG Init | tools/db/pg_init.py | PostgreSQL database initialization | --json | Initialization status |
| PG Optimize All | tools/db/pg_optimize_all.py | PostgreSQL optimization for all tables | --json | Optimization results |
| PG Optimize DataBridge | tools/db/pg_optimize_databridge.py | PostgreSQL optimization for DataBridge tables | --json | Optimization results |

## Extensions (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Workflow Loop Chat Hook | tools/extensions/builtins/030_workflow_loop_chat.py | Chat hook: workflow loop status advisory | (hook) | Advisory message |
| Bayesian Learning Chat Hook | tools/extensions/builtins/040_bayesian_learning_chat.py | Chat hook: Bayesian teaching integration | (hook) | Learning context |
| RAG Context Chat Hook | tools/extensions/builtins/050_rag_context_chat.py | Chat hook: RAG context injection | (hook) | Injected context |
| Code Quality Chat Hook | tools/extensions/builtins/060_code_quality_chat.py | Chat hook: code quality advisory | (hook) | Quality advisory |
| Genesis Status Chat Hook | tools/extensions/builtins/070_genesis_status_chat.py | Chat hook: Genesis daemon status | (hook) | Status message |
| Intake Enrichment Chat Hook | tools/extensions/builtins/080_intake_enrichment_chat.py | Chat hook: intake session enrichment | (hook) | Enrichment context |

## Genesis (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Goal Template Generator | tools/genesis/goal_template_generator.py | Generate goal templates from GKP artifacts | --json | Goal templates |
| Goal Learner | tools/genesis/goal_learner.py | Detect novel problem-solving not covered by existing goals, auto-generate FORGE goal files with version history and quality scoring | --scan --json | Generated goal markdown files + DB records |
| Synthesize Reflex | tools/genesis/reflexes/synthesize.py | Synthesize reflex: tool-chain pattern detection | --json | Pattern results |

## GovCon (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| AI Clause Compliance | tools/govcon/ai_clause_compliance.py | AI-specific FAR/DFARS clause compliance checker | --json, --gate | Compliance results |
| Bayesian Bid Scorer | tools/govcon/bayesian_bid_scorer.py | Bayesian bid/no-bid scoring engine | --json | Bid scores |
| Capability Enricher | tools/govcon/capability_enricher.py | Enrich capability mappings with evidence | --json | Enriched mappings |
| Capture AI Blueprint | tools/govcon/capture_ai_blueprint.py | AI-assisted capture management blueprint | --json | Blueprint data |
| CMMC Validator | tools/govcon/cmmc_validator.py | CMMC compliance validator for proposals | --json, --gate | Validation results |
| Color Review Simulator | tools/govcon/color_review_simulator.py | Shipley color team review simulator | --json | Review results |
| Compliance Matrix Builder | tools/govcon/compliance_matrix_builder.py | L/M/N compliance matrix builder | --json | Compliance matrix |
| IDIQ Factory | tools/govcon/idiq_factory.py | IDIQ/BPA task order factory | --json | Task orders |
| LCAT Mapper | tools/govcon/lcat_mapper.py | Labor category (LCAT) mapping engine | --json | LCAT mappings |
| Opportunity Lifecycle | tools/govcon/opportunity_lifecycle.py | Opportunity lifecycle state machine | --json | Lifecycle state |
| Program Bridge | tools/govcon/program_bridge.py | Bridge proposals to program execution | --json | Bridge results |
| Proposal Quality Evaluator | tools/govcon/proposal_quality_evaluator.py | Multi-dimension proposal quality scoring | --json | Quality scores |
| Rate Benchmarker | tools/govcon/rate_benchmarker.py | Labor rate benchmarking against market data | --json | Benchmark results |
| Reflex Sandbox | tools/govcon/reflex_sandbox.py | Proposal Genesis reflex testing sandbox | --json | Sandbox results |
| Shipley Mapper | tools/govcon/shipley_mapper.py | Map proposal phases to Shipley process | --json | Phase mappings |
| Talent Intelligence | tools/govcon/talent_intelligence.py | Talent pipeline intelligence for proposals | --json | Talent data |
| Teaming Hub | tools/govcon/teaming_hub.py | Teaming partner discovery and management | --json | Partner data |
| Win Theme Manager | tools/govcon/win_theme_manager.py | Win theme and discriminator management | --json | Theme data |

## Harness (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| MCP Wrapper Generator | tools/harness/mcp_wrapper_generator.py | Generate MCP wrappers for CLI tools | --json | Generated wrappers |

## MCP Servers (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| LLM Proxy Server | tools/mcp/llm_proxy_server.py | LLM proxy MCP server for multi-provider routing | (server) | MCP endpoints |
| LSP Server | tools/mcp/lsp_server.py | Language Server Protocol MCP server | (server) | MCP endpoints |

## Notifications
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Email Adapter | tools/notifications/adapters/email_adapter.py | Email notification delivery adapter | --json | Delivery status |
| Compliance Notifier | tools/notify/adapter.py | Write-once multi-channel compliance notifier (STIG, cATO, gate blocks) | --health/--send/--json | NotifyResult JSON |
| Compliance Cards | tools/notify/cards.py | Card abstractions: ComplianceCard, STIGCard, CATOCard, SecurityGateCard | (library) | Typed card objects |
| Slack Compliance Channel | tools/notify/channels/slack.py | Slack Block Kit delivery for compliance cards | (library) | bool |
| Teams Compliance Channel | tools/notify/channels/teams.py | MS Teams Connector Card delivery for compliance cards | (library) | bool |
| Discord Compliance Channel | tools/notify/channels/discord.py | Discord webhook embed delivery for compliance cards | (library) | bool |
| Email Compliance Channel | tools/notify/channels/email.py | SMTP HTML/plain-text delivery for compliance cards | (library) | bool |

## Proposal Genesis (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Comply CMMC Reflex | tools/proposal_genesis/reflexes/comply_cmmc.py | CMMC compliance reflex | --json | Compliance findings |
| Price Reflex | tools/proposal_genesis/reflexes/price.py | Pricing strategy reflex | --json | Pricing recommendations |
| Regulate Reflex | tools/proposal_genesis/reflexes/regulate.py | Regulatory compliance reflex | --json | Regulatory findings |
| Talent Reflex | tools/proposal_genesis/reflexes/talent.py | Talent/staffing reflex | --json | Staffing recommendations |
| Vehicle Reflex | tools/proposal_genesis/reflexes/vehicle.py | Contract vehicle selection reflex | --json | Vehicle recommendations |

## RAG (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| PG Vector Store | tools/rag/pg_vector_store.py | PostgreSQL pgvector backend for RAG | (library) | VectorStore class |

## Review Board (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Docs Fixer | tools/review_board/fixers/docs_fixes.py | Auto-fix engine for documentation findings | --json | Fix results |
| Perf Fixer | tools/review_board/fixers/perf_fixes.py | Auto-fix engine for performance findings | --json | Fix results |
| QA Fixer | tools/review_board/fixers/qa_fixes.py | Auto-fix engine for QA findings | --json | Fix results |
| SRE Fixer | tools/review_board/fixers/sre_fixes.py | Auto-fix engine for SRE findings | --json | Fix results |
| Health Scorer | tools/review_board/health_scorer.py | Aggregate health scoring across reflexes | --json | Health scores |
| Notifier | tools/review_board/notifier.py | Review board notification dispatcher | --json | Notification status |

## SaaS (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| MCP SSE | tools/saas/mcp_sse.py | MCP Server-Sent Events transport for SaaS | (server) | SSE stream |
| Tenant LLM Keys | tools/saas/tenant_llm_keys.py | Per-tenant LLM API key management | --json | Key management results |

## Scout
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Config Updater | tools/scout/config_updater.py | Scout configuration auto-updater | --json | Updated config |
| Genesis Trigger | tools/scout/genesis_trigger.py | Trigger Genesis from Scout findings | --json | Trigger results |
| Install Scheduler | tools/scout/install_scheduler.py | Scout installation scheduler | --json | Schedule status |
| LLM Summarizer | tools/scout/llm_summarizer.py | LLM-powered Scout finding summarizer | --json | Summaries |
| Trending Pillar | tools/scout/pillars/trending.py | Trending topic detection pillar | --json | Trending topics |
| Preflight | tools/scout/preflight.py | Scout preflight validation | --json | Preflight results |

## AlphaDesk Trading Engine
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Fundamental Analyst | tools/trading/analysts/fundamental.py | Fundamental analysis agent (SMA, valuation, trends) | --json | Analysis results |
| Sentiment Analyst | tools/trading/analysts/sentiment.py | Keyword-based sentiment analysis agent | --json | Sentiment scores |
| Market Data | tools/trading/data/market_data.py | Alpaca market data fetch and cache layer | --json | Market data |
| Perspective Scorer | tools/trading/analysis/perspective_scorer.py | ICDEV™'s INTaaS bull/bear multiperspectivity scorer | --json | Perspective scores |
| Signal Generator | tools/trading/analysis/signal_generator.py | Weighted composite signal generator | --json | Trading signals |
| Order Manager | tools/trading/execution/order_manager.py | Alpaca order placement and tracking | --json | Order status |
| Position Tracker | tools/trading/execution/position_tracker.py | Position synchronization with Alpaca | --json | Position data |
| Risk Checker | tools/trading/execution/risk_checker.py | Pre-trade risk validation (position limits, VaR) | --json | Risk assessment |
| Pulse Article Generator | tools/trading/pulse/article_generator.py | Pulse article generator from analysis results | --json | Article draft |
| Portfolio Strategist | tools/trading/strategist/portfolio_strategist.py | Autonomous long-term investment strategy agent — 4-tier allocation (core/tactical/opportunistic/hedge) from multi-timeframe performance, macro regime, KG centrality, scenario resilience, expert consensus | --run --json | Strategy allocation |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Macro Data | tools\trading\data\macro_data.py | Auto-registered: data/macro_data.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Alert Engine | tools\trading\market_intel\alert_engine.py | Auto-registered: market_intel/alert_engine.py | --json | JSON |
| Auto Trader | tools\trading\market_intel\auto_trader.py | Auto-registered: market_intel/auto_trader.py | --json | JSON |
| Batch Scanner | tools\trading\market_intel\batch_scanner.py | Auto-registered: market_intel/batch_scanner.py | --json | JSON |
| Cascade Engine | tools\trading\market_intel\cascade_engine.py | Auto-registered: market_intel/cascade_engine.py | --json | JSON |
| Expert Agents | tools\trading\market_intel\expert_agents.py | Auto-registered: market_intel/expert_agents.py | --json | JSON |
| Forecaster | tools\trading\market_intel\forecaster.py | Auto-registered: market_intel/forecaster.py | --json | JSON |
| Kg Seeder | tools\trading\market_intel\kg_seeder.py | Auto-registered: market_intel/kg_seeder.py | --json | JSON |
| Scenario Engine | tools\trading\market_intel\scenario_engine.py | Auto-registered: market_intel/scenario_engine.py | --json | JSON |
| Universe | tools\trading\market_intel\universe.py | Auto-registered: market_intel/universe.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Cascade Bridge | tools\simulation\cascade_bridge.py | Auto-registered: simulation/cascade_bridge.py | --json | JSON |
| Query Parser | tools\simulation\query_parser.py | Auto-registered: simulation/query_parser.py | --json | JSON |
| Risk Monitor | tools\simulation\risk_monitor.py | Auto-registered: simulation/risk_monitor.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Injection Scanner | tools\security\injection_scanner.py | Auto-registered: security/injection_scanner.py | --json | JSON |
| Telegram Listener | tools\notifications\adapters\telegram_listener.py | Auto-registered: adapters/telegram_listener.py | --json | JSON |
| Telegram Connector | tools\databridge\connectors\telegram_connector.py | Auto-registered: connectors/telegram_connector.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Alpha Calculator | tools\trading\factors\alpha_calculator.py | Auto-registered: factors/alpha_calculator.py | --json | JSON |
| Cost Model | tools\trading\factors\cost_model.py | Auto-registered: factors/cost_model.py | --json | JSON |
| Factor Data | tools\trading\factors\factor_data.py | Auto-registered: factors/factor_data.py | --json | JSON |
| Factor Regression | tools\trading\factors\factor_regression.py | Auto-registered: factors/factor_regression.py | --json | JSON |
| Regime Premiums | tools\trading\factors\regime_premiums.py | Auto-registered: factors/regime_premiums.py | --json | JSON |
| Signal Validator | tools\trading\factors\signal_validator.py | Auto-registered: factors/signal_validator.py | --json | JSON |
| Skill Tracker | tools\trading\factors\skill_tracker.py | Auto-registered: factors/skill_tracker.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Govcon Scan | tools\genesis\reflexes\govcon_scan.py | Auto-registered: reflexes/govcon_scan.py | --json | JSON |


## Air-Gap Mode (OPT-51/OPT-61)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Air-Gap CLI | tools/airgap/cli.py | ICDEV air-gap orchestrator — detect environment, activate local-only routing, run health check, full report | --detect, --activate, --deactivate, --health, --full, --json | Status + report JSON |
| Detector | tools/airgap/detector.py | Probe network connectivity, cloud provider availability, and local LLM server presence to determine air-gap status | (library) | is_airgap(), detect_environment() |
| Config Patcher | tools/airgap/config_patcher.py | In-memory LLM routing patcher — disables cloud tier2, sets prefer_local, patches hook env vars for non-Claude-Code sessions | (library) | activate_airgap(), deactivate_airgap() |
| Hook Compat | tools/airgap/hook_compat.py | Claude Code hook compatibility layer for non-Claude-Code orchestrators: session ID generation, pre-tool append-only table guard, git destructive-command blocklist (OPT-51), auto-commit, mid-run message queue (OPT-62), safety-net PR, tool-error middleware (OPT-61) | (library) | get_session_id(), run_pre_tool_check(), check_message_queue(), queue_message(), safety_net_pr(), tool_error_middleware() |
| PDF Fallback | tools/airgap/pdf_fallback.py | Local-only PDF extraction — pypdf text extraction + LLaVA vision OCR fallback; registers into RAG pipeline pdf_provider chain | (library) | LocalPDFProvider, register_local_fallback() |
| Session Compat | tools/airgap/session_compat.py | Session management for non-Claude-Code environments — transcript capture, event correlation, session lifecycle without Claude Code CLI | (library) SessionManager.start(), .log_prompt(), .log_tool_use(), .end() | Session dict with session_id, duration_seconds, event_count |
| Health Check | tools/airgap/health_check.py | Air-gap-aware health check — replaces cloud-dependent checks with local equivalents, reports degraded vs. functional capabilities | --json | Health status dict |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Export Import | tools\network\export_import.py | Auto-registered: network/export_import.py | --json | JSON |
| Inventory Export | tools\network\inventory_export.py | Ansible inventory INI (hosts grouped by zone/role) and Terraform HCL skeleton (VPC, subnet, security group) derived from topology graph. Routes: POST /api/export/<topo_id>/ansible, POST /api/export/<topo_id>/terraform | graph dict | .ini / .tf text |
| Montecarlo | tools\network\montecarlo.py | Auto-registered: network/montecarlo.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Kanban Scheduler | tools\genesis\kanban_scheduler.py | Auto-registered: genesis/kanban_scheduler.py | --json | JSON |
| Add Hpc To Aiml | tools\network\add_hpc_to_aiml.py | Auto-registered: network/add_hpc_to_aiml.py | --json | JSON |
| Ato Generator | tools\network\ato_generator.py | Auto-registered: network/ato_generator.py | --json | JSON |
| Fix Template Zones | tools\network\fix_template_zones.py | Auto-registered: network/fix_template_zones.py | --json | JSON |
| Update Template Zones | tools\network\update_template_zones.py | Auto-registered: network/update_template_zones.py | --json | JSON |
| OCR Fallback | tools/network/ocr_fallback.py | OCR-based diagram extraction fallback (pytesseract + rapidocr-onnxruntime ensemble) for air-gap environments without vision LLM. Spatial proximity inference for connection detection. | --image, --check, --json, --gate | JSON topology / status |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Stig Import | tools\network\stig_import.py | Auto-registered: network/stig_import.py | --json | JSON |
| Intent Validator | tools\network\intent_validator.py | Network Canvas intent-based topology validation engine — bandwidth, redundancy, isolation, latency, encryption constraints | --json | JSON |
| Change Request | tools\network\change_request.py | Network Canvas Change Request Markup engine — add/remove/modify markup (green/red/yellow), CAB review document with before/after diffs | --json | JSON |
| NetBox Client | tools\network\netbox_client.py | NetBox REST API client — pull devices, IP allocations, VLANs, prefixes, racks, circuits; push canvas nodes back as NetBox devices. Stdlib only (no third-party deps). Blueprint routes: GET /api/netbox/status, POST /api/netbox/configure, GET/POST /api/netbox/pull/*, POST /api/netbox/import/<topo_id>, POST /api/netbox/push/<topo_id>, GET /api/netbox/sync-log | --url, --token, --pull, --site, --json, --gate | JSON / text |
| Auto-Discovery | tools\network\discovery.py | Live network auto-discovery agent — SNMP/SSH/CDP/LLDP neighbor crawl, ping sweep, JointJS graph builder, as-designed vs as-built diff. Optional deps: pysnmp, netmiko. Blueprint routes: GET /network/discovery, POST /api/discovery/scan, GET /api/discovery/scans, GET/DELETE /api/discovery/scans/<id>, POST /api/discovery/scans/<id>/import/<topo>, POST /api/discovery/diff, POST /api/discovery/ping, GET /api/discovery/capabilities | --target, --method, --community, --username, --diff, --json, --gate | JSON / text |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Intent Validator | tools\network\intent_validator.py | Auto-registered: network/intent_validator.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Topology Styler | tools\network\topology_styler.py | Auto-registered: network/topology_styler.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Visio Export | tools\network\visio_export.py | Auto-registered: network/visio_export.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Vuln Overlay | tools\network\vuln_overlay.py | Auto-registered: network/vuln_overlay.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Bandwidth Sim | tools\network\bandwidth_sim.py | Auto-registered: network/bandwidth_sim.py | --json | JSON |
| Nl Query | tools\network\nl_query.py | Auto-registered: network/nl_query.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Cloud Architecture | tools\network\cloud_architecture.py | Auto-registered: network/cloud_architecture.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Deploy Catalog | tools\pipeline\deploy_catalog.py | Auto-registered: pipeline/deploy_catalog.py | --json | JSON |
| Deploy Generator | tools\pipeline\deploy_generator.py | Auto-registered: pipeline/deploy_generator.py | --json | JSON |
| E2E Devops Canvas | tools\testing\e2e_devops_canvas.py | Auto-registered: testing/e2e_devops_canvas.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Antipattern Detector | tools\pipeline\antipattern_detector.py | Auto-registered: pipeline/antipattern_detector.py | --json | JSON |
| Iac Validator | tools\pipeline\iac_validator.py | Auto-registered: pipeline/iac_validator.py | --json | JSON |
| Seed Runbooks | tools\sre\seed_runbooks.py | Auto-registered: sre/seed_runbooks.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Security Engine | tools\security_canvas\security_engine.py | Auto-registered: security_canvas/security_engine.py | --json | JSON |
| SDC Compliance KG | tools\security_canvas\compliance_kg.py | Builds sdc-compliance-kg: STRIDE→NIST→framework traversable graph | --build / --node-info / --path-from / --stride-coverage / --sdc-ctrl-coverage --json | JSON |
| SDC NL Query | tools\security_canvas\nl_query.py | Natural language query engine for SDC compliance graph; auto-builds KG if missing | --query "..." --build --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Importers | tools\security_canvas\importers.py | Auto-registered: security_canvas/importers.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| E2E Security Canvas | tools\testing\e2e_security_canvas.py | Auto-registered: testing/e2e_security_canvas.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Boundary Engine | tools\boundary_canvas\boundary_engine.py | Pure deterministic assessment engine for the BDC Boundary Design Canvas. Functions: boundary compliance checking (NIST 800-53 controls), ISA lifecycle validation (warning 60d / critical 30d expiry), PPS matrix generation, boundary gap detection, SCIF/ATO/FedRAMP posture scoring. No Flask or LLM dependency — callable as a library from the BDC blueprint. | (library — imported by boundary_canvas/blueprint.py) | dict / JSON |
| Code Gen Agentic | tools\builder\code_gen_agentic.py | Auto-registered: builder/code_gen_agentic.py | --json | JSON |
| Code Gen Core | tools\builder\code_gen_core.py | Auto-registered: builder/code_gen_core.py | --json | JSON |
| Code Gen Multilang | tools\builder\code_gen_multilang.py | Auto-registered: builder/code_gen_multilang.py | --json | JSON |
| Code Gen Python | tools\builder\code_gen_python.py | Auto-registered: builder/code_gen_python.py | --json | JSON |
| Data Engine | tools\data_canvas\data_engine.py | Auto-registered: data_canvas/data_engine.py | --json | JSON |
| Infra Engine | tools\infra_canvas\infra_engine.py | Auto-registered: infra_canvas/infra_engine.py | --json | JSON |
| Blueprint Helpers | tools\network\blueprint_helpers.py | Auto-registered: network/blueprint_helpers.py | --json | JSON |
| Observability Engine | tools\observability_canvas\observability_engine.py | Auto-registered: observability_canvas/observability_engine.py | --json | JSON |
| E2E New Canvases | tools\testing\e2e_new_canvases.py | Auto-registered: testing/e2e_new_canvases.py | --json | JSON |
| Canvas Orchestrator | tools/canvas/orchestrator.py | Cross-Canvas Integration Engine — links all 9 design canvases (IDC/NDC/SDC/BDC/PDC/ODC/DDC/QDC/MDC) via canvas_projects entity in icdev.db; CRUD for design projects, link/unlink designs, aggregate compliance summary, compute 4-dimension readiness score (completeness/compliance/coverage/risk) | create --name / list / summary --json | JSON |
| Canvas KG Builder | tools/canvas/kg_builder.py | Incremental Knowledge Graph builder for all 9 design canvases. rebuild_canvas_kg(canvas, design_id) for targeted on-save upsert; stores nodes/edges to canvas_kg_nodes and canvas_kg_edges in icdev.db; logs every build to canvas_kg_build_log (append-only — NIST AU). | --build-all / --build-canvas idc --design-id \<id\> / --stats --json | JSON |
| Canvas Projects API | tools\dashboard\api\canvas_projects.py | REST API Blueprint for canvas_projects: GET/POST/PUT/DELETE /api/canvas-projects, link/unlink canvas designs, GET /api/canvas-projects/compliance for 7-canvas posture summary | (blueprint) | JSON |
| Canvas Export Utils | tools\canvas\export_utils.py | Unified multi-format export for all 9 design canvases. 5 functions: export_json, export_markdown, export_csv, export_drawio (mxGraphModel XML), export_svg. CUI banner included in all formats. Stdlib only — no external dependencies. | (library — called by canvas blueprints) | JSON / Markdown / CSV / DrawIO XML / SVG |


## Design Canvases (7-Canvas Suite)
All canvases share: separate SQLite DB, Flask Blueprint, YAML config in `args/`, feature flag env var, NIST 800-53 compliance assessment, and cross-canvas integration via `tools/canvas/orchestrator.py`.

| Canvas | Blueprint | Engine | Config | DB | Route | Feature Flag | Description |
|--------|-----------|--------|--------|----|-------|--------------|-------------|
| **IDC** Infrastructure | tools\infra_canvas\blueprint.py | tools\infra_canvas\infra_engine.py | args\infra_canvas_config.yaml | infra_canvas.db | /infra | ICDEV_INFRA_ENABLED | Multi-CSP infrastructure design (compute, storage, containers, serverless) with NIST/FedRAMP/CMMC assessment and CSP equivalence mapping |
| **NDC** Network | tools\network\blueprint.py | tools\network\routes\ | — | network_canvas.db | /network | ICDEV_NETWORK_ENABLED | Network topology design with intent validation, ACAS/Nessus overlay, NL query, change request markup, and NetBox sync |
| **SDC** Security | tools\security_canvas\blueprint.py | tools\security_canvas\security_engine.py | args\security_canvas_config.yaml | security_canvas.db | /security | ICDEV_SECURITY_ENABLED | STRIDE threat modeling, NIST/FedRAMP/CMMC control mapping, MITRE ATT&CK coverage, compliance KG, remediation, LLM agent, NL query |
| **BDC** Boundary | tools\boundary_canvas\blueprint.py | tools\boundary_canvas\boundary_engine.py | args\boundary_canvas_config.yaml | boundary_canvas.db | /boundary | ICDEV_BOUNDARY_ENABLED | ATO/FedRAMP/SCIF authorization boundary design, ISA lifecycle (expiry warning 60d / critical 30d), PPS matrix generation, boundary gap detection |
| **PDC** Pipeline | tools\pipeline\blueprint.py | — | — | pipeline_canvas.db | /devops | ICDEV_PIPELINE_ENABLED | DevSecOps pipeline design, CI/CD stage modeling, security gate placement, GitLab/GitHub Actions export |
| **ODC** Observability | tools\observability_canvas\blueprint.py | tools\observability_canvas\observability_engine.py | args\observability_canvas_config.yaml | observability_canvas.db | /observability | ICDEV_OBSERVABILITY_ENABLED | SIEM/SOAR/log stack design, MITRE ATT&CK detection coverage, source type weighting, NIST AU/SI control assessment, log retention policy |
| **DDC** Data | tools\data_canvas\blueprint.py | tools\data_canvas\data_engine.py | args\data_canvas_config.yaml | data_canvas.db | /data | ICDEV_DATA_CANVAS_ENABLED | Data model design with PII/PHI/CUI/SECRET classification, retention policy enforcement, Privacy Act/HIPAA/GDPR assessment, ER diagram export |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Plan Decomposer | tools\kanban\plan_decomposer.py | Auto-registered: kanban/plan_decomposer.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Base Lens | tools\oracle\base_lens.py | Abstract 3-phase pipeline (analyze → score → propose) for all Oracle lenses; exception isolation per lens | N/A (library) | OraclePrediction list |
| Oracle Reflex | tools\oracle\oracle_reflex.py | Orchestrates all 10 Oracle lenses, persists oracle_predictions, emits GKP artifacts; DaemonBase-compatible run() | run(config, trust) | {success, metric_value, details} |
| Trajectory Lens | tools\oracle\lens_trajectory.py | Lens 3: architectural trajectory forecasting — CC/maintainability regression, days-to-threshold, hotspot detection | --json / --gate | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Lens Ecosystem Gap | tools\oracle\lens_ecosystem_gap.py | Lens 1: FORGE-layer manifest gap detection, dead-table scan, coherence recidivism tracking | --json / --gate | JSON |
| Lens Workflow Patterns | tools\oracle\lens_workflow_patterns.py | Lens 2: audit_trail sequential pattern mining, tool-pair co-occurrence, self-heal detection, kanban recurrence | --json / --gate | JSON |
| Lens Regulatory Anticipation | tools\oracle\lens_regulatory.py | Lens 5: regulatory/standards signal crosswalk to ICDEV™ frameworks, effective-date extraction, compliance gap scoring | --json / --gate | JSON |
| Lens Child App Demand | tools\oracle\lens_child_app_demand.py | Lens 6: dossier + SAM.gov + marketplace demand scoring to predict top child-app verticals | --json / --gate | JSON |
| Oracle Kanban Bridge | tools\oracle\kanban_bridge.py | Convert promoted anticipation_report GKPs to suggested kanban tasks; batch-sync backfill | --sync / --gate / --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Fast Transforms | tools\builder\fast_transforms.py | Auto-registered: builder/fast_transforms.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Lens Convergence | tools\oracle\lens_convergence.py | Auto-registered: oracle/lens_convergence.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Iac Generator | tools\infra_canvas\iac_generator.py | Auto-registered: infra_canvas/iac_generator.py | --json | JSON |
| E2E Bdc Canvas | tools\testing\e2e_bdc_canvas.py | Auto-registered: testing/e2e_bdc_canvas.py | --json | JSON |
| E2E Ddc Canvas | tools\testing\e2e_ddc_canvas.py | Auto-registered: testing/e2e_ddc_canvas.py | --json | JSON |
| E2E Idc Canvas | tools\testing\e2e_idc_canvas.py | Auto-registered: testing/e2e_idc_canvas.py | --json | JSON |
| E2E Odc Canvas | tools\testing\e2e_odc_canvas.py | Auto-registered: testing/e2e_odc_canvas.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Pii Detector | tools\data_canvas\pii_detector.py | Auto-registered: data_canvas/pii_detector.py | --json | JSON |
| Cloud Import | tools\infra_canvas\cloud_import.py | Auto-registered: infra_canvas/cloud_import.py | --json | JSON |
| Sigma Generator | tools\observability_canvas\sigma_generator.py | Auto-registered: observability_canvas/sigma_generator.py | --json | JSON |
| Canvas Indexer | tools\rag\canvas_indexer.py | Auto-registered: rag/canvas_indexer.py | --json | JSON |
| Claude Cli | tools\kanban\executors\claude_cli.py | Auto-registered: executors/claude_cli.py | --json | JSON |
| Gitlab Pipeline | tools\kanban\executors\gitlab_pipeline.py | Auto-registered: executors/gitlab_pipeline.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Canvas Auto Remediate | tools\canvas\auto_remediate.py | Confidence-tiered auto-remediation for canvas assessment findings. >=0.7 auto-fix (add missing nodes), 0.3-0.7 suggest, <0.3 escalate. IDC (8 rules) and ODC (8 rules) wrappers. Max 5 auto-fixes/hour rate limiter. | (library — called by IDC/ODC blueprints on save) | JSON remediation report |
| POA&M Auto-Remediator | tools\canvas\auto_remediator.py | Cross-canvas POA&M auto-remediator (CLI). Takes finding hashes (or --all-pending / --all-approved), looks up source design, backs up canvas DB, applies vendor-neutral handler (21 rules across security/observability/boundary canvases), re-runs assessment to verify, marks finding_approvals.decision='remediated', writes audit_trail. 5 IDC rules require vendor selection (file as GitHub issues instead). | --finding-hash, --all-pending, --all-approved, --canvas, --list-handlers, --dry-run, --json, --gate | Per-finding result + summary; updates finding_approvals + audit_trail |
| Agent Toolkit | tools\agent_toolkit\ | OPT-67: unified builtin tool catalog for ICDEV agents (deepagents pattern, MIT-inspired, no upstream runtime dep). 10 primitives: read_file, write_file, edit_file, ls, glob, grep, execute_shell, write_todos, update_todo, spawn_subagent. One-line `create_agent(name, system_prompt)` factory composes the catalog with LLMRouter. Works LLM-free (primitives only) OR as a tool-calling agent loop. See tools/agent_toolkit/__init__.py for exports. | (library) — import tools.agent_toolkit; also tools.agent_toolkit.create_agent() | Agent object with .invoke(messages) -> AgentResult |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Canvas Health Scanner | tools\canvas\canvas_health_scanner.py | Auto-registered: canvas/canvas_health_scanner.py | --json | JSON |
| Proposal Generator | tools\oracle\proposal_generator.py | Auto-registered: oracle/proposal_generator.py | --json | JSON |
| Remediation Lens | tools\oracle\lenses\remediation_lens.py | Auto-registered: lenses/remediation_lens.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| BDC SOPs | tools/boundary_canvas/sops.py | CRUD and approval workflow for Boundary Design Canvas SOPs (ISA renewal, boundary change approval, cross-domain transfer, interconnection decommission). Lifecycle: draft → pending_review → approved/rejected. Functions: get_all_sops, get_sop_by_id, create_sop, update_sop, delete_sop, submit_for_review, approve_sop, reject_sop, seed_sops. NIST control tagging per SOP (CA-3, CM-3, SC-7, etc.). | (library — called by BDC blueprint) | SOP dict / list |
| Sops | tools\observability_canvas\sops.py | Auto-registered: observability_canvas/sops.py | --json | JSON |
| Sops | tools\pipeline\sops.py | Auto-registered: pipeline/sops.py | --json | JSON |
| Gate Executor | tools\qdc_canvas\gate_executor.py | Auto-registered: qdc_canvas/gate_executor.py | --json | JSON |
| Qdc Engine | tools\qdc_canvas\qdc_engine.py | Auto-registered: qdc_canvas/qdc_engine.py | --json | JSON |
| Sops | tools\security_canvas\sops.py | Auto-registered: security_canvas/sops.py | --json | JSON |
| E2E Qdc Canvas | tools\testing\e2e_qdc_canvas.py | Auto-registered: testing/e2e_qdc_canvas.py | --json | JSON |
| Lens Quality | tools\oracle\lenses\lens_quality.py | Auto-registered: lenses/lens_quality.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Migration Engine | tools\migration_canvas\migration_engine.py | Auto-registered: migration_canvas/migration_engine.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| E2E Diagram Validator | tools\testing\e2e_diagram_validator.py | Auto-registered: testing/e2e_diagram_validator.py | --json | JSON |
| Lens Migration | tools\oracle\lenses\lens_migration.py | Auto-registered: lenses/lens_migration.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Config Parser | tools\network\config_parser.py | Auto-registered: network/config_parser.py | --json | JSON |
| Device Manager | tools\network\device_manager.py | Auto-registered: network/device_manager.py | --json | JSON |
| Folder Watcher | tools\network\folder_watcher.py | Auto-registered: network/folder_watcher.py | --json | JSON |
| Ingestion Pipeline | tools\network\ingestion_pipeline.py | Auto-registered: network/ingestion_pipeline.py | --json | JSON |
| Network Ingester | tools\network\network_ingester.py | Auto-registered: network/network_ingester.py | --json | JSON |
| Network Intelligence | tools\network\network_intelligence.py | Auto-registered: network/network_intelligence.py | --json | JSON |
| Network Query Router | tools\network\network_query_router.py | Auto-registered: network/network_query_router.py | --json | JSON |
| Nms Adapter | tools\network\nms_adapter.py | Auto-registered: network/nms_adapter.py | --json | JSON |
| Librenms Adapter | tools\network\adapters\librenms_adapter.py | Auto-registered: adapters/librenms_adapter.py | --json | JSON |
| Netbox Adapter | tools\network\adapters\netbox_adapter.py | Auto-registered: adapters/netbox_adapter.py | --json | JSON |
| Solarwinds Adapter | tools\network\adapters\solarwinds_adapter.py | Auto-registered: adapters/solarwinds_adapter.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Naming Engine | tools\network\naming_engine.py | Auto-registered: network/naming_engine.py | --json | JSON |
| Topology Enricher | tools\network\topology_enricher.py | Auto-registered: network/topology_enricher.py | --json | JSON |
| Topology Validator | tools\network\topology_validator.py | Auto-registered: network/topology_validator.py | --json | JSON |


## Canvas Auto-Remediation
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Canvas Auto-Remediator | tools\canvas\auto_remediator.py | POA&M auto-remediation CLI — applies vendor-neutral design-completeness fixes to approved/pending findings across all 9 canvases (security, observability, boundary, infra, data, network, pipeline, QDC, migration). Pipeline per finding: backup canvas DB → mutate graph_json with per-rule handler → re-run assessment to verify fix → mark finding_approvals.decision='remediated' → append audit_trail row (event_type='vulnerability_resolved'). Supports --dry-run, --list-handlers, --canvas filter. | --finding-hash \<hash\>, --all-pending, --all-approved, --canvas \<name\>, --list-handlers, --dry-run, --gate, --json | JSON remediation report (status, findings processed, remediated count, skipped, errors) |
| Collaboration Manager | tools\canvas\collaboration.py | Session-based multi-user canvas collaboration (join/leave/push/poll); SQLite-backed, air-gap safe, no WebSocket required | canvas_key, db_path, collab_table | dict |


## Agent Adapters (OPT-71)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Agent Adapter Base | tools/agents/adapter_base.py | Core protocol + dataclasses for the agent adapter pattern (OPT-71). Defines `AgentAdapter` (Protocol: available, prepare_prompt, invoke, detect_completion, parse_response), `AgentSession` (task_id, prompt, working_dir, max_turns, timeout_seconds, auth), `AgentResult` (completed, exit_code, output, duration_ms, structured), and `NotInstalledError`. One layer above tools/llm/*_provider.py — wraps a full multi-turn agent session, not a single LLM call. | (library — imported by adapter implementations) | AgentAdapter Protocol, AgentSession, AgentResult dataclasses |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Promote Next Phase | tools\awareness\promote_next_phase.py | Auto-registered: awareness/promote_next_phase.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Component Indexer | tools\awareness\component_indexer.py | Auto-registered: awareness/component_indexer.py | --json | JSON |
| Enablement | tools\awareness\enablement.py | Auto-registered: awareness/enablement.py | --json | JSON |


