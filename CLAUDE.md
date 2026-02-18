# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Quick Reference

### Commands
```bash
# Initialize framework (first run)
/initialize                    # Custom slash command — sets up all dirs, manifests, memory, databases

# Memory system
python tools/memory/memory_read.py --format markdown          # Load all memory
python tools/memory/memory_write.py --content "text" --type event  # Write to daily log + DB
python tools/memory/memory_write.py --content "text" --type fact --importance 7  # Store a fact
python tools/memory/memory_write.py --update-memory --content "text" --section user_preferences  # Update MEMORY.md
python tools/memory/memory_db.py --action search --query "keyword"   # Keyword search
python tools/memory/semantic_search.py --query "concept"             # Semantic search (requires OpenAI key)
python tools/memory/hybrid_search.py --query "query"                 # Best: combined keyword + semantic
python tools/memory/embed_memory.py --all                            # Generate embeddings for all entries

# Agentic generation (Phase 19)
python tools/builder/agentic_fitness.py --spec "..." --json               # Assess fitness
python tools/builder/app_blueprint.py --fitness-scorecard sc.json \
  --user-decisions '{}' --app-name "my-app" --json                        # Generate blueprint
python tools/builder/child_app_generator.py --blueprint bp.json \
  --project-path /tmp --name "my-app" --json                              # Generate child app
python tools/builder/scaffolder.py --project-path /tmp --name "my-app" \
  --type api --agentic --fitness-scorecard sc.json                        # Scaffold + agentic

# LLM Provider (vendor-agnostic model routing)
python -c "from tools.llm.router import LLMRouter; r = LLMRouter(); print(r.get_provider_for_function('code_generation'))"  # Check routing
# Config: args/llm_config.yaml — providers, models, routing, embeddings
# Set OLLAMA_BASE_URL=http://localhost:11434/v1 for local model support
# Set prefer_local: true in llm_config.yaml for air-gapped environments
```

### Python Dependencies
See `requirements.txt` for full list. Key packages:
**Required:** sqlite3, pathlib, json, datetime, argparse, hashlib (all stdlib)
**Optional:** openai (embeddings + OpenAI-compat providers), anthropic (direct Anthropic API), python-dotenv (.env loading), numpy (embedding math), rank_bm25 (search, has fallback)
**ICDEV:** pyyaml, jinja2, flask, pytest, pytest-cov, behave, requests, boto3, cyclonedx-bom, bandit, pip-audit, detect-secrets

---

## Architecture: GOTCHA Framework

This is a 6-layer agentic system. The AI (you) is the orchestration layer — you read goals, call tools, apply args, reference context, and use hard prompts. You never execute work directly; you delegate to deterministic Python scripts.

**Why:** LLMs are probabilistic. Business logic must be deterministic. 90% accuracy/step = ~59% over 5 steps. Separation of concerns fixes this.

### The 6 Layers

| Layer | Directory | Role |
|-------|-----------|------|
| **Goals** | `goals/` | Process definitions — what to achieve, which tools to use, expected outputs, edge cases |
| **Orchestration** | *(you)* | Read goal → decide tool order → apply args → reference context → handle errors |
| **Tools** | `tools/` | Python scripts, one job each. Deterministic. Don't think, just execute. |
| **Args** | `args/` | YAML/JSON behavior settings (themes, modes, schedules). Change behavior without editing goals/tools |
| **Context** | `context/` | Static reference material (tone rules, writing samples, ICP descriptions, case studies) |
| **Hard Prompts** | `hardprompts/` | Reusable LLM instruction templates (outline→post, rewrite-in-voice, summarize) |

### Key Files
- `goals/manifest.md` — Index of all goal workflows. Check before starting any task.
- `tools/manifest.md` — Master list of all tools. Check before writing a new script.
- `memory/MEMORY.md` — Curated long-term facts/preferences, read at session start.
- `memory/logs/YYYY-MM-DD.md` — Daily session logs.
- `.env` — API keys and environment variables.
- `.tmp/` — Disposable scratch work. Never store important data here.

### Memory System Architecture
Dual storage: markdown files (human-readable) + SQLite databases (searchable).

**Databases:**
- `data/memory.db` — `memory_entries` (with embeddings), `daily_logs`, `memory_access_log`
- `data/activity.db` — `tasks` table for tracking

**Memory types:** fact, preference, event, insight, task, relationship

**Search ranking:** Hybrid search uses 0.7 * BM25 (keyword) + 0.3 * semantic (vector). Configurable via `--bm25-weight` and `--semantic-weight` flags.

**Embeddings:** OpenAI text-embedding-3-small (1536 dims), stored as BLOBs in SQLite.

---

## How to Operate

1. **Check goals first** — Read `goals/manifest.md` before starting a task. If a goal exists, follow it.
2. **Check tools first** — Read `tools/manifest.md` before writing new code. If you create a new tool, add it to the manifest.
3. **When tools fail** — Read the error, fix the tool, update the goal with what you learned (rate limits, batching, timing).
4. **Goals are living docs** — Update when better approaches emerge. Never modify/create goals without explicit permission.
5. **When stuck** — Explain what's missing and what you need. Don't guess or invent capabilities.

### Session Start Protocol
1. Read `memory/MEMORY.md` for long-term context
2. Read today's daily log (`memory/logs/YYYY-MM-DD.md`)
3. Read yesterday's log for continuity
4. Or run: `python tools/memory/memory_read.py --format markdown`

### First Run
If `memory/MEMORY.md` doesn't exist, this is a fresh environment. Run `/initialize` to set up all directories, manifests, memory files, and databases.

---

## Guardrails

- Always check `tools/manifest.md` before writing a new script
- Verify tool output format before chaining into another tool
- Don't assume APIs support batch operations — check first
- When a workflow fails mid-execution, preserve intermediate outputs before retrying
- Read the full goal before starting a task — don't skim
- **NEVER DELETE YOUTUBE VIDEOS** — Irreversible. MCP server blocks this intentionally. If truly needed, ask 3 times for 3 confirmations. Direct user to YouTube Studio instead.

*(Add new guardrails as mistakes happen. Keep under 15 items.)*

---

## ICDEV System — Intelligent Coding Development

ICDEV is a meta-builder that autonomously builds Gov/DoD applications using the GOTCHA framework and ATLAS workflow. It handles the full SDLC with TDD/BDD, NIST 800-53 RMF compliance, and self-healing capabilities.

### Environment Constraints
- **Classification:** CUI // SP-CTI (IL4/IL5), SECRET (IL6) — classification-aware markings via `classification_manager.py`
- **Impact Levels:** IL2 (Public), IL4 (CUI/GovCloud), IL5 (CUI/Dedicated), IL6 (SECRET/SIPR)
- **Cloud:** AWS GovCloud (us-gov-west-1), Amazon Bedrock for LLM
- **Access:** PyPi + AWS dedicated regions only (no public internet)
- **No local GPU** — all ML inference via Bedrock
- **CI/CD:** GitLab, **Orchestration:** K8s/OpenShift, **IaC:** Terraform + Ansible
- **Monitoring:** ELK + Splunk + Prometheus/Grafana
- **Secrets:** AWS Secrets Manager

### Multi-Agent Architecture (14 Agents, 3 Tiers)

| Tier | Agent | Port | Role |
|------|-------|------|------|
| Core | Orchestrator | 8443 | Task routing, workflow management |
| Core | Architect | 8444 | ATLAS/M-ATLAS A/T phases, system design |
| Domain | Builder | 8445 | TDD code gen (RED→GREEN→REFACTOR) |
| Domain | Compliance | 8446 | ATO artifacts (SSP, POAM, STIG, SBOM, FedRAMP, CMMC, OSCAL, eMASS, cATO) |
| Domain | Security | 8447 | SAST, dependency audit, secret detection, container scan |
| Domain | Infrastructure | 8448 | Terraform, Ansible, K8s, pipeline gen |
| Domain | MBSE | 8451 | SysML parsing, DOORS NG, digital thread, model-code sync, DES compliance |
| Domain | Modernization | 8452 | Legacy analysis, 7R assessment, migration planning, code generation, compliance bridge |
| Domain | Requirements Analyst | 8453 | Conversational intake, gap detection, SAFe decomposition, readiness scoring, document extraction |
| Domain | Supply Chain | 8454 | Dependency graph, SBOM aggregation, ISA lifecycle, CVE triage, SCRM assessment |
| Domain | Simulation | 8455 | Digital Program Twin — 6-dimension what-if simulation, Monte Carlo, COA generation |
| Support | Knowledge | 8449 | Self-healing patterns, ML, recommendations |
| Domain | DevSecOps & ZTA | 8457 | DevSecOps pipeline security, Zero Trust (NIST 800-207), policy-as-code, service mesh, ZTA maturity |
| Support | Monitor | 8450 | Log analysis, metrics, alerts, health checks |

Agents communicate via **A2A protocol** (JSON-RPC 2.0 over mutual TLS within K8s). Each publishes an Agent Card at `/.well-known/agent.json`.

### MCP Servers (13 stdio servers for Claude Code)

| Server | Config Key | Tools |
|--------|-----------|-------|
| icdev-core | `.mcp.json` | project_create, project_list, project_status, task_dispatch, agent_status |
| icdev-compliance | `.mcp.json` | ssp_generate, poam_generate, stig_check, sbom_generate, cui_mark, control_map, nist_lookup, cssp_assess, cssp_report, cssp_ir_plan, cssp_evidence, xacta_sync, xacta_export, sbd_assess, sbd_report, ivv_assess, ivv_report, rtm_generate, **crosswalk_query, fedramp_assess, fedramp_report, cmmc_assess, cmmc_report, oscal_generate, emass_sync, cato_monitor, pi_compliance, classification_check, fips199_categorize, fips200_validate, security_categorize** |
| icdev-builder | `.mcp.json` | scaffold, generate_code, write_tests, run_tests, lint, format |
| icdev-infra | `.mcp.json` | terraform_plan, terraform_apply, ansible_run, k8s_deploy, pipeline_generate, rollback |
| icdev-knowledge | `.mcp.json` | search_knowledge, add_pattern, get_recommendations, analyze_failure, self_heal |
| icdev-maintenance | `.mcp.json` | scan_dependencies, check_vulnerabilities, run_maintenance_audit, remediate |
| icdev-mbse | `.mcp.json` | import_xmi, import_reqif, trace_forward, trace_backward, generate_code, detect_drift, sync_model, des_assess, thread_coverage, model_snapshot |
| icdev-modernization | `.mcp.json` | register_legacy_app, analyze_legacy, extract_architecture, generate_docs, assess_seven_r, create_migration_plan, track_migration, generate_migration_code, check_compliance_bridge, migrate_version |
| icdev-requirements | `.mcp.json` | create_intake_session, resume_intake_session, get_session_status, process_intake_turn, upload_document, extract_document, detect_gaps, score_readiness, decompose_requirements, generate_bdd |
| icdev-supply-chain | `.mcp.json` | register_ato_system, assess_boundary_impact, generate_red_alternative, add_vendor, build_dependency_graph, propagate_impact, manage_isa, assess_scrm, triage_cve |
| icdev-simulation | `.mcp.json` | create_scenario, run_simulation, run_monte_carlo, generate_coas, generate_alternative_coa, compare_coas, select_coa, manage_scenarios |
| icdev-integration | `.mcp.json` | configure_jira, sync_jira, configure_servicenow, sync_servicenow, configure_gitlab, sync_gitlab, export_reqif, submit_approval, review_approval, build_traceability |
| icdev-marketplace | `.mcp.json` | publish_asset, install_asset, uninstall_asset, search_assets, list_assets, get_asset, review_asset, list_pending, check_compat, sync_status, asset_scan |
| icdev-devsecops | `.mcp.json` | devsecops_profile_create, devsecops_profile_get, devsecops_maturity_assess, zta_maturity_score, zta_assess, pipeline_security_generate, policy_generate, service_mesh_generate, network_segmentation_generate, attestation_verify, zta_posture_check, pdp_config_generate |

### Compliance Frameworks Supported
| Framework | Catalog | Assessor | Report |
|-----------|---------|----------|--------|
| NIST 800-53 Rev 5 | `nist_800_53.json` | `control_mapper.py` | SSP, control matrix |
| FedRAMP Moderate | `fedramp_moderate_baseline.json` | `fedramp_assessor.py` | `fedramp_report_generator.py` |
| FedRAMP High | `fedramp_high_baseline.json` | `fedramp_assessor.py` | `fedramp_report_generator.py` |
| NIST 800-171 | `nist_800_171_controls.json` | via crosswalk | via crosswalk coverage |
| CMMC Level 2/3 | `cmmc_practices.json` | `cmmc_assessor.py` | `cmmc_report_generator.py` |
| DoD CSSP (DI 8530.01) | `dod_cssp_8530.json` | `cssp_assessor.py` | `cssp_report_generator.py` |
| CISA Secure by Design | `cisa_sbd_requirements.json` | `sbd_assessor.py` | `sbd_report_generator.py` |
| IEEE 1012 IV&V | `ivv_requirements.json` | `ivv_assessor.py` | `ivv_report_generator.py` |
| DoDI 5000.87 DES | `des_requirements.json` | `des_assessor.py` | `des_report_generator.py` |
| FIPS 199 | `nist_sp_800_60_types.json` | `fips199_categorizer.py` | Categorization report |
| FIPS 200 | `fips_200_areas.json` | `fips200_validator.py` | Gap report |
| CNSSI 1253 | `cnssi_1253_overlay.json` | via fips199_categorizer | Overlay application |
| CJIS Security Policy | `cjis_security_policy.json` | `cjis_assessor.py` | via base_assessor |
| HIPAA Security Rule | `hipaa_security_rule.json` | `hipaa_assessor.py` | via base_assessor |
| HITRUST CSF v11 | `hitrust_csf_v11.json` | `hitrust_assessor.py` | via base_assessor |
| SOC 2 Type II | `soc2_trust_criteria.json` | `soc2_assessor.py` | via base_assessor |
| PCI DSS v4.0 | `pci_dss_v4.json` | `pci_dss_assessor.py` | via base_assessor |
| ISO/IEC 27001:2022 | `iso27001_2022_controls.json` | `iso27001_assessor.py` | via base_assessor |
| NIST SP 800-207 (ZTA) | `nist_800_207_zta.json` | `nist_800_207_assessor.py` | via base_assessor |
| DoD MOSA (10 U.S.C. §4401) | `mosa_framework.json` | `mosa_assessor.py` | via base_assessor |

### Control Crosswalk
The crosswalk engine (`tools/compliance/crosswalk_engine.py`) uses a dual-hub model (ADR D111):
- **US Hub**: NIST 800-53 Rev 5 — domestic frameworks map directly (FedRAMP, CMMC, CJIS, HIPAA, etc.)
- **International Hub**: ISO/IEC 27001:2022 — international frameworks map via bridge
- **Bridge**: `iso27001_nist_bridge.json` connects the two hubs bidirectionally

Implementing AC-2 satisfies FedRAMP AC-2, 800-171 3.1.1, CMMC AC.L2-3.1.1, and cascades to CJIS/HIPAA/SOC 2/PCI DSS/ISO 27001/NIST 800-207 via the crosswalk engine.

### Supported Languages (6 First-Class)
| Language | Scaffold | Lint | Format | SAST | Dep Audit | BDD Steps | Code Gen |
|----------|----------|------|--------|------|-----------|-----------|----------|
| Python | python-backend, api, cli, data-pipeline | flake8/ruff | black+isort | bandit | pip-audit | behave | Flask/FastAPI |
| Java | java-backend | checkstyle/PMD | google-java-format | SpotBugs | OWASP DC | Cucumber-JVM | Spring Boot |
| JavaScript/TS | javascript-frontend, typescript-backend | eslint+tsc | prettier | eslint-security | npm audit | cucumber-js | Express |
| Go | go-backend | golangci-lint | gofmt | gosec | govulncheck | godog | net/http/Gin |
| Rust | rust-backend | clippy | rustfmt | cargo-audit | cargo-audit | cucumber-rs | Actix-web |
| C# | csharp-backend | dotnet analyzers | dotnet format | SecurityCodeScan | dotnet list | SpecFlow | ASP.NET |

Language profiles stored in `context/languages/language_registry.json`. Detection via `tools/builder/language_support.py`.

### Claude Code Skills (22 Custom Commands)

| Skill | Purpose |
|-------|---------|
| `/icdev-init` | Initialize new project with compliance scaffolding |
| `/icdev-build` | Build code using true TDD (RED→GREEN→REFACTOR) via M-ATLAS workflow |
| `/icdev-test` | Run full test suite (pytest + behave BDD) |
| `/icdev-comply` | Generate ATO artifacts (SSP, POAM, STIG, SBOM) |
| `/icdev-deploy` | Generate IaC and deploy via GitLab CI/CD |
| `/icdev-secure` | Run security scanning (SAST, deps, secrets, container) |
| `/icdev-review` | Enforce code review gates with security checks |
| `/icdev-status` | Project status dashboard |
| `/icdev-monitor` | Production monitoring + self-healing trigger |
| `/icdev-knowledge` | Query/update learning knowledge base |
| `/icdev-maintain` | Maintenance audit — scan deps, check CVEs, remediate, track SLAs |
| `/icdev-mbse` | MBSE integration — import SysML/DOORS, build digital thread, generate code, sync, DES compliance |
| `/icdev-modernize` | App modernization — legacy analysis, 7R assessment, migration planning, code gen, compliance bridge |
| `/icdev-intake` | Requirements intake — conversational AI-driven intake, gap detection, SAFe decomposition, readiness scoring, document extraction |
| `/icdev-boundary` | Boundary & supply chain — ATO boundary impact assessment, supply chain dependency graph, ISA lifecycle, SCRM, CVE triage |
| `/icdev-simulate` | Digital Program Twin — 6-dimension what-if simulation, Monte Carlo estimation, COA generation & comparison |
| `/icdev-integrate` | External integration — bidirectional Jira/ServiceNow/GitLab sync, DOORS NG ReqIF export, approval workflows, RTM traceability |
| `/icdev-query` | NLQ compliance query — natural language to SQL for compliance database queries (TAC-8) |
| `/icdev-worktree` | Git worktree task isolation — create, list, cleanup, status for parallel CI/CD (TAC-8) |
| `/plan_python` | Python build plan — Flask/FastAPI, pytest, behave, bandit, pip-audit, STIG Dockerfile (TAC-8) |
| `/plan_java` | Java build plan — Spring Boot, Cucumber-JVM, checkstyle, SpotBugs, OWASP DC (TAC-8) |
| `/plan_go` | Go build plan — net/http/Gin, godog, golangci-lint, gosec, govulncheck (TAC-8) |
| `/plan_rust` | Rust build plan — Actix-web, cucumber-rs, clippy, cargo-audit, rustfmt (TAC-8) |
| `/plan_csharp` | C# build plan — ASP.NET Core, SpecFlow, SecurityCodeScan, dotnet analyzers (TAC-8) |
| `/plan_typescript` | TypeScript build plan — Express, cucumber-js, eslint-security, npm audit (TAC-8) |
| `/icdev-agentic` | Generate agentic child application (mini-ICDEV clone with GOTCHA/ATLAS) |
| `/icdev-market` | Federated GOTCHA marketplace — publish, install, search, review, sync assets across tenant orgs |
| `/icdev-devsecops` | DevSecOps profile management, maturity assessment, pipeline security generation, policy-as-code (Kyverno/OPA), attestation |
| `/icdev-zta` | Zero Trust Architecture — 7-pillar maturity scoring, NIST 800-207 assessment, service mesh generation, network segmentation, PDP/PEP config, cATO posture |
| `/icdev-mosa` | DoD MOSA (10 U.S.C. §4401) — MOSA assessment, modularity analysis, ICD/TSP generation, code enforcement, intake auto-detection for DoD/IC |

### Testing Framework (Adapted from ADW)
```bash
# Health check
python tools/testing/health_check.py                 # Full system health check
python tools/testing/health_check.py --json           # JSON output

# Test orchestrator (full pipeline: unit + BDD + E2E + gates)
python tools/testing/test_orchestrator.py --project-dir /path/to/project
python tools/testing/test_orchestrator.py --project-dir /path --skip-e2e --project-id "proj-123"

# E2E tests (Playwright MCP)
python tools/testing/e2e_runner.py --discover         # List available E2E test specs
python tools/testing/e2e_runner.py --run-all           # Execute all E2E tests
python tools/testing/e2e_runner.py --test-file .claude/commands/e2e/dashboard_health.md
python tools/testing/e2e_runner.py --run-all --validate-screenshots    # E2E + vision validation
python tools/testing/e2e_runner.py --run-all --validate-screenshots --vision-strict  # Vision failures = test failures

# Screenshot validation (vision LLM — Ollama LLaVA / Claude / GPT-4o)
python tools/testing/screenshot_validator.py --check --json                           # Check vision model availability
python tools/testing/screenshot_validator.py --image screenshot.png --assert "CUI banner is visible" --json
python tools/testing/screenshot_validator.py --batch-dir .tmp/test_runs/screenshots/ --json
```

**Testing Architecture (8-step pipeline, adapted from ADW test.md):**
1. **py_compile** — Python syntax validation (catches missing colons, bad indentation before tests run)
2. **Ruff** (`ruff>=0.12`) — Ultra-fast Python linter (replaces flake8+isort+black, written in Rust)
3. **pytest** (tests/) — Unit/integration tests with coverage
4. **behave/Gherkin** (features/) — BDD scenario tests for business requirements
5. **Bandit** — SAST security scan (SQL injection, XSS, hardcoded secrets)
6. **Playwright MCP** (.claude/commands/e2e/*.md) — Browser automation E2E tests
7. **Vision validation** (optional) — LLM-based screenshot analysis (CUI banners, error detection, content verification)
8. **Security + Compliance gates** — CUI markings, STIG (0 CAT1), secret detection

**Claude Code test commands** (in .claude/commands/):
- `/test` — Full application validation suite (syntax + quality + unit + BDD + security)
- `/test_e2e` — Execute E2E test via Playwright MCP with screenshots + CUI verification
- `/resolve_failed_test` — Fix a specific failing test (minimal, targeted fix)
- `/resolve_failed_e2e_test` — Fix a specific failing E2E test

**Key patterns from ADW:** parse_json (markdown-wrapped JSON), Pydantic data types (TestResult, E2ETestResult), dual logging (file+console), safe subprocess env, retry with resolution (max 4 unit / max 2 E2E), fail-fast E2E, stdin=DEVNULL for Claude Code subprocesses

### ICDEV Commands
```bash
# Database
python tools/db/init_icdev_db.py                    # Initialize ICDEV database (95 tables)

# Audit trail (append-only, NIST AU compliant)
python tools/audit/audit_logger.py --event-type "code.commit" --actor "builder-agent" --action "Committed module X" --project-id "proj-123"
python tools/audit/audit_query.py --project "proj-123" --format json
python tools/audit/decision_recorder.py --project-id "proj-123" --decision "Use PostgreSQL" --rationale "RDS requirement" --actor "architect-agent"

# MCP servers (stdio transport)
python tools/mcp/core_server.py                     # Start core MCP server
python tools/mcp/compliance_server.py               # Start compliance MCP server
python tools/mcp/builder_server.py                  # Start builder MCP server
python tools/mcp/infra_server.py                    # Start infra MCP server
python tools/mcp/knowledge_server.py                # Start knowledge MCP server
python tools/mcp/maintenance_server.py              # Start maintenance MCP server
python tools/mcp/mbse_server.py                    # Start MBSE MCP server
python tools/mcp/requirements_server.py            # Start Requirements MCP server
python tools/mcp/supply_chain_server.py            # Start Supply Chain MCP server
python tools/mcp/simulation_server.py              # Start Simulation MCP server
python tools/mcp/integration_server.py             # Start Integration MCP server

# Requirements Intake (RICOAS)
python tools/requirements/intake_engine.py --project-id "proj-123" --customer-name "Jane Smith" --customer-org "DoD PEO" --impact-level IL5 --json  # New session
python tools/requirements/intake_engine.py --session-id "<id>" --message "We need a mission planning tool" --json                                   # Process turn
python tools/requirements/intake_engine.py --session-id "<id>" --resume --json                                                                       # Resume session
python tools/requirements/intake_engine.py --session-id "<id>" --export --json                                                                       # Export requirements
python tools/requirements/gap_detector.py --session-id "<id>" --check-security --check-compliance --json                                             # Detect gaps
python tools/requirements/readiness_scorer.py --session-id "<id>" --json                                                                             # Score readiness
python tools/requirements/decomposition_engine.py --session-id "<id>" --level story --generate-bdd --json                                            # SAFe decomposition
python tools/requirements/document_extractor.py --session-id "<id>" --upload --file-path /path/to/sow.pdf --document-type sow --json                 # Upload document
python tools/requirements/document_extractor.py --session-id "<id>" --upload --file-path /path/to/whiteboard.png --document-type attachment --json    # Upload image (auto-classified)
python tools/requirements/document_extractor.py --document-id "<id>" --extract --json                                                                 # Extract requirements
python tools/requirements/document_extractor.py --document-id "<id>" --classify --json                                                                # Classify image document

# ATO Boundary Impact (RICOAS Phase 2)
python tools/requirements/boundary_analyzer.py --project-id "proj-123" --register-system --system-name "My System" --ato-status active --classification CUI --impact-level IL5 --json
python tools/requirements/boundary_analyzer.py --project-id "proj-123" --system-id "<id>" --requirement-id "<id>" --json                              # Assess boundary impact
python tools/requirements/boundary_analyzer.py --project-id "proj-123" --generate-alternatives --assessment-id "<id>" --json                          # RED alternative COAs
python tools/requirements/boundary_analyzer.py --project-id "proj-123" --list-assessments --tier RED --json                                           # List RED items

# Supply Chain Intelligence (RICOAS Phase 2)
python tools/supply_chain/dependency_graph.py --project-id "proj-123" --add-vendor --vendor-name "Vendor X" --vendor-type software --country US --json
python tools/supply_chain/dependency_graph.py --project-id "proj-123" --build-graph --json                                                            # Build dependency graph
python tools/supply_chain/dependency_graph.py --project-id "proj-123" --impact "component-name" --impact-type vulnerability --severity critical --json # Impact propagation
python tools/supply_chain/isa_manager.py --project-id "proj-123" --expiring --days 90 --json                                                          # Expiring ISAs
python tools/supply_chain/isa_manager.py --project-id "proj-123" --review-due --json                                                                   # Review overdue ISAs
python tools/supply_chain/scrm_assessor.py --project-id "proj-123" --vendor-id "<id>" --json                                                           # SCRM vendor assessment
python tools/supply_chain/scrm_assessor.py --project-id "proj-123" --aggregate --json                                                                   # Project-wide SCRM
python tools/supply_chain/cve_triager.py --project-id "proj-123" --triage --cve-id CVE-2025-1234 --component openssl --cvss 9.8 --severity critical --json
python tools/supply_chain/cve_triager.py --project-id "proj-123" --sla-check --json                                                                     # CVE SLA compliance

# Digital Program Twin Simulation (RICOAS Phase 3)
python tools/simulation/simulation_engine.py --project-id "proj-123" --create-scenario --scenario-name "Add auth module" --scenario-type what_if --modifications '{"add_requirements": 3}' --json
python tools/simulation/simulation_engine.py --scenario-id "<id>" --run --dimensions all --json                                                       # Run 6-dimension simulation
python tools/simulation/monte_carlo.py --scenario-id "<id>" --dimension schedule --iterations 10000 --json                                            # Monte Carlo schedule
python tools/simulation/monte_carlo.py --scenario-id "<id>" --dimension cost --iterations 5000 --json                                                 # Monte Carlo cost
python tools/simulation/coa_generator.py --session-id "<id>" --generate-3-coas --simulate --json                                                      # Generate 3 COAs with simulation
python tools/simulation/coa_generator.py --session-id "<id>" --compare --json                                                                          # Compare COAs
python tools/simulation/coa_generator.py --session-id "<id>" --generate-alternative --requirement-id "<id>" --json                                     # RED alternative COAs
python tools/simulation/coa_generator.py --coa-id "<id>" --select --selected-by "Jane Smith" --rationale "Best balance" --json                        # Select COA
python tools/simulation/scenario_manager.py --scenario-id "<id>" --fork --new-name "Variant B" --json                                                  # Fork scenario
python tools/simulation/scenario_manager.py --compare --scenario-ids "<id1>,<id2>" --json                                                              # Compare scenarios

# External Integration (RICOAS Phase 4)
python tools/integration/jira_connector.py --project-id "proj-123" --configure --instance-url "https://org.atlassian.net" --json       # Configure Jira
python tools/integration/jira_connector.py --project-id "proj-123" --push --json                                                       # Push to Jira
python tools/integration/jira_connector.py --project-id "proj-123" --pull --json                                                       # Pull from Jira
python tools/integration/jira_connector.py --project-id "proj-123" --analyze-attachments --attachment-paths "img1.png,img2.jpg" --json    # Analyze Jira image attachments
python tools/integration/servicenow_connector.py --project-id "proj-123" --configure --instance-url "https://org.service-now.com" --json  # Configure ServiceNow
python tools/integration/servicenow_connector.py --project-id "proj-123" --push --json                                                    # Push to ServiceNow
python tools/integration/servicenow_connector.py --project-id "proj-123" --analyze-attachments --attachment-paths "img1.png" --json       # Analyze ServiceNow attachments
python tools/integration/gitlab_connector.py --project-id "proj-123" --configure --instance-url "https://gitlab.org.mil" --json           # Configure GitLab
python tools/integration/gitlab_connector.py --project-id "proj-123" --push --json                                                         # Push to GitLab
python tools/integration/gitlab_connector.py --project-id "proj-123" --pull --json                                                         # Pull from GitLab
python tools/integration/doors_exporter.py --session-id "<id>" --export-reqif --output-path /path/to/output.reqif --json                   # Export ReqIF
python tools/integration/approval_manager.py --session-id "<id>" --submit requirements_package --json                                       # Submit for approval
python tools/integration/approval_manager.py --workflow-id "<id>" --review --decision approved --json                                       # Review approval
python tools/requirements/traceability_builder.py --project-id "proj-123" --build-rtm --gap-analysis --json                                 # Build full RTM

# Observability & Agent Execution (TAC-8)
python tools/agent/agent_executor.py --prompt "echo hello" --model sonnet --json           # Execute agent via CLI
python tools/agent/agent_executor.py --prompt "fix tests" --model opus --max-retries 3     # With retry logic

# NLQ Compliance Queries (TAC-8)
# Start dashboard first: python tools/dashboard/app.py
# Navigate to /query for natural language compliance queries
# Navigate to /events for real-time event timeline (SSE)

# Git Worktree Parallel CI/CD (TAC-8)
python tools/ci/modules/worktree.py --create --task-id test-123 --target-dir src/ --json    # Create worktree
python tools/ci/modules/worktree.py --list --json                                            # List worktrees
python tools/ci/modules/worktree.py --cleanup --worktree-name icdev-test-123                # Cleanup worktree
python tools/ci/modules/worktree.py --status --worktree-name icdev-test-123                 # Worktree status

# GitLab Task Board Monitor (TAC-8)
python tools/ci/triggers/gitlab_task_monitor.py                    # Start monitor (polls every 20s)
python tools/ci/triggers/gitlab_task_monitor.py --dry-run          # Preview without spawning
python tools/ci/triggers/gitlab_task_monitor.py --once             # Single poll and exit

# Project management
python tools/project/project_create.py --name "my-app" --type microservice
python tools/project/project_list.py
python tools/project/project_status.py --project-id "proj-123"

# Compliance
python tools/compliance/ssp_generator.py --project-id "proj-123"
python tools/compliance/poam_generator.py --project-id "proj-123"
python tools/compliance/stig_checker.py --project-id "proj-123"
python tools/compliance/sbom_generator.py --project-dir "/path/to/project"
python tools/compliance/cui_marker.py --file "/path/to/file" --marking "CUI // SP-CTI"
python tools/compliance/nist_lookup.py --control "AC-2"
python tools/compliance/control_mapper.py --activity "code.commit" --project-id "proj-123"

# CSSP Compliance (DI 8530.01)
python tools/compliance/cssp_assessor.py --project-id "proj-123" --functional-area all
python tools/compliance/cssp_report_generator.py --project-id "proj-123"
python tools/compliance/incident_response_plan.py --project-id "proj-123"
python tools/compliance/siem_config_generator.py --project-dir "/path/to/project" --targets splunk elk
python tools/compliance/cssp_evidence_collector.py --project-id "proj-123" --project-dir "/path"

# Xacta 360 Integration
python tools/compliance/xacta/xacta_sync.py --project-id "proj-123" --mode hybrid
python tools/compliance/xacta/xacta_export.py --project-id "proj-123" --format oscal

# Secure by Design (CISA SbD + DoDI 5000.87)
python tools/compliance/sbd_assessor.py --project-id "proj-123" --domain all
python tools/compliance/sbd_report_generator.py --project-id "proj-123"

# IV&V (IEEE 1012)
python tools/compliance/ivv_assessor.py --project-id "proj-123" --process-area all
python tools/compliance/ivv_report_generator.py --project-id "proj-123"
python tools/compliance/traceability_matrix.py --project-id "proj-123" --project-dir "/path"

# Multi-Framework Compliance (Phase 17)
python tools/compliance/crosswalk_engine.py --control AC-2                             # Crosswalk query
python tools/compliance/crosswalk_engine.py --project-id "proj-123" --coverage          # Coverage across frameworks
python tools/compliance/crosswalk_engine.py --project-id "proj-123" --target fedramp-moderate --gap-analysis
python tools/compliance/classification_manager.py --impact-level IL5                    # Classification markings
python tools/compliance/fedramp_assessor.py --project-id "proj-123" --baseline moderate # FedRAMP assessment
python tools/compliance/fedramp_report_generator.py --project-id "proj-123"             # FedRAMP report
python tools/compliance/cmmc_assessor.py --project-id "proj-123" --level 2              # CMMC assessment
python tools/compliance/cmmc_report_generator.py --project-id "proj-123"                # CMMC report
python tools/compliance/oscal_generator.py --project-id "proj-123" --artifact ssp       # OSCAL generation
python tools/compliance/emass/emass_sync.py --project-id "proj-123" --mode hybrid       # eMASS sync
python tools/compliance/emass/emass_export.py --project-id "proj-123" --type controls   # eMASS export
python tools/compliance/cato_monitor.py --project-id "proj-123" --check-freshness       # cATO monitoring
python tools/compliance/cato_scheduler.py --project-id "proj-123" --run-due             # cATO scheduling
python tools/compliance/pi_compliance_tracker.py --project-id "proj-123" --velocity     # PI tracking

# FIPS 199/200 Security Categorization (Phase 20)
python tools/compliance/fips199_categorizer.py --list-catalog                                          # Browse SP 800-60 types
python tools/compliance/fips199_categorizer.py --list-catalog --category D.1 --json                    # Filter by category
python tools/compliance/fips199_categorizer.py --project-id "proj-123" --add-type "D.1.1.1"            # Add info type
python tools/compliance/fips199_categorizer.py --project-id "proj-123" --add-type "D.2.3.4" --adjust-c High  # Add with adjustment
python tools/compliance/fips199_categorizer.py --project-id "proj-123" --list-types --json             # List assigned types
python tools/compliance/fips199_categorizer.py --project-id "proj-123" --categorize --json             # Run categorization
python tools/compliance/fips199_categorizer.py --project-id "proj-123" --categorize --method cnssi_1253  # Force CNSSI 1253
python tools/compliance/fips199_categorizer.py --project-id "proj-123" --gate                          # Evaluate gate
python tools/compliance/fips200_validator.py --project-id "proj-123" --json                            # Validate 17 areas
python tools/compliance/fips200_validator.py --project-id "proj-123" --gate --json                     # Gate evaluation

# Universal Compliance Platform (Phase 23)
python tools/compliance/universal_classification_manager.py --list-categories                                   # List all data categories
python tools/compliance/universal_classification_manager.py --banner CUI PHI --json                            # Composite banner (CUI + PHI)
python tools/compliance/universal_classification_manager.py --code-header CUI PCI --language python            # Composite code header
python tools/compliance/universal_classification_manager.py --detect --project-id "proj-123" --json            # Auto-detect data categories
python tools/compliance/universal_classification_manager.py --add-category --project-id "proj-123" --category PHI  # Add data category
python tools/compliance/universal_classification_manager.py --validate --project-id "proj-123" --json          # Validate markings
python tools/compliance/compliance_detector.py --project-id "proj-123" --json                                  # Detect applicable frameworks
python tools/compliance/compliance_detector.py --project-id "proj-123" --apply --json                          # Detect + store in DB
python tools/compliance/compliance_detector.py --project-id "proj-123" --confirm --json                        # Confirm all detected
python tools/compliance/multi_regime_assessor.py --project-id "proj-123" --json                                # Assess all frameworks
python tools/compliance/multi_regime_assessor.py --project-id "proj-123" --gate                                # Multi-regime gate check
python tools/compliance/multi_regime_assessor.py --project-id "proj-123" --minimal-controls --json             # Prioritized control list
python tools/compliance/cjis_assessor.py --project-id "proj-123" --json                                        # CJIS assessment
python tools/compliance/hipaa_assessor.py --project-id "proj-123" --json                                       # HIPAA assessment
python tools/compliance/hitrust_assessor.py --project-id "proj-123" --json                                     # HITRUST assessment
python tools/compliance/soc2_assessor.py --project-id "proj-123" --json                                        # SOC 2 assessment
python tools/compliance/pci_dss_assessor.py --project-id "proj-123" --json                                     # PCI DSS assessment
python tools/compliance/iso27001_assessor.py --project-id "proj-123" --json                                    # ISO 27001 assessment
python tools/compliance/cjis_assessor.py --project-id "proj-123" --gate                                        # CJIS gate check
python tools/compliance/hipaa_assessor.py --project-id "proj-123" --gate                                       # HIPAA gate check

# MBSE Integration (Phase 18)
python tools/mbse/xmi_parser.py --project-id "proj-123" --file /path/model.xmi --json     # Import SysML XMI
python tools/mbse/reqif_parser.py --project-id "proj-123" --file /path/reqs.reqif --json   # Import DOORS ReqIF
python tools/mbse/digital_thread.py --project-id "proj-123" auto-link --json               # Auto-link thread
python tools/mbse/digital_thread.py --project-id "proj-123" coverage --json                # Thread coverage
python tools/mbse/digital_thread.py --project-id "proj-123" report --json                  # Thread report
python tools/mbse/model_code_generator.py --project-id "proj-123" --language python --output ./src  # Generate code
python tools/mbse/model_control_mapper.py --project-id "proj-123" --map-all --json         # Map to NIST controls
python tools/mbse/sync_engine.py --project-id "proj-123" detect-drift --json               # Detect model-code drift
python tools/mbse/sync_engine.py --project-id "proj-123" sync-model-to-code --json         # Sync model→code
python tools/mbse/des_assessor.py --project-id "proj-123" --project-dir /path --json       # DES assessment
python tools/mbse/des_report_generator.py --project-id "proj-123" --output-dir /path       # DES report
python tools/mbse/pi_model_tracker.py --project-id "proj-123" --pi PI-25.1 --snapshot      # PI snapshot
python tools/mbse/diagram_extractor.py --image diagram.png --diagram-type block_definition --project-id "proj-123" --json   # Extract SysML from screenshot
python tools/mbse/diagram_extractor.py --image diagram.png --validate --project-id "proj-123" --json                        # Validate against existing model
python tools/mbse/diagram_extractor.py --image diagram.png --diagram-type block_definition --store --project-id "proj-123" --json  # Extract + store in DB

# Builder (TDD workflow — 6 languages)
python tools/builder/test_writer.py --feature "user auth" --project-dir "/path" --language python
python tools/builder/code_generator.py --test-file "/path/to/test.py" --project-dir "/path" --language java
python tools/builder/scaffolder.py --type java-backend --name "my-service"
python tools/builder/language_support.py --detect "/path/to/project"    # Detect languages
python tools/builder/language_support.py --list                          # List supported languages
python tools/builder/linter.py --project-dir "/path"
python tools/builder/formatter.py --project-dir "/path"

# Maintenance Audit
python tools/maintenance/dependency_scanner.py --project-id "proj-123"           # Scan all deps
python tools/maintenance/vulnerability_checker.py --project-id "proj-123"        # Check CVEs
python tools/maintenance/maintenance_auditor.py --project-id "proj-123"          # Full audit + score
python tools/maintenance/remediation_engine.py --project-id "proj-123" --dry-run # Preview fixes
python tools/maintenance/remediation_engine.py --project-id "proj-123" --auto    # Auto-fix

# Application Modernization (7Rs Migration)
python tools/modernization/legacy_analyzer.py --project-id "proj-123" --app-id "app-1" --source-path /path/to/legacy   # Analyze legacy app
python tools/modernization/architecture_extractor.py --app-id "app-1" --json                                           # Extract architecture
python tools/modernization/doc_generator.py --app-id "app-1" --output-dir /path/to/docs                               # Generate docs
python tools/modernization/seven_r_assessor.py --project-id "proj-123" --app-id "app-1" --json                         # 7R assessment
python tools/modernization/version_migrator.py --source /path --output /path --language python --from 2.7 --to 3.11    # Version migration
python tools/modernization/framework_migrator.py --source /path --output /path --from struts --to spring-boot          # Framework migration
python tools/modernization/monolith_decomposer.py --app-id "app-1" --target microservices --json                       # Decompose monolith
python tools/modernization/db_migration_planner.py --app-id "app-1" --target postgresql --json                         # DB migration DDL
python tools/modernization/strangler_fig_manager.py --plan-id "plan-1" --status --json                                 # Strangler fig status
python tools/modernization/compliance_bridge.py --plan-id "plan-1" --validate --json                                   # ATO compliance bridge
python tools/modernization/migration_code_generator.py --plan-id "plan-1" --generate-all --output /path                # Generate migration code
python tools/modernization/migration_report_generator.py --app-id "app-1" --type assessment                            # Migration report
python tools/modernization/migration_tracker.py --plan-id "plan-1" --pi PI-25.3 --snapshot --json                      # PI migration tracker
python tools/modernization/ui_analyzer.py --image screenshot.png --json                                                  # Analyze legacy UI screenshot
python tools/modernization/ui_analyzer.py --image-dir /path/to/screenshots/ --app-id "app-1" --project-id "proj-123" --store --json  # Batch analyze + store
python tools/modernization/ui_analyzer.py --image screenshot.png --score-only                                            # Quick complexity score only

# Compliance Diagram Validation (vision-based)
python tools/compliance/diagram_validator.py --image network.png --type network_zone --project-id "proj-123" --json      # Validate network zone diagram
python tools/compliance/diagram_validator.py --image ato_boundary.png --type ato_boundary --expected-components "Web,App,DB" --json  # Validate ATO boundary
python tools/compliance/diagram_validator.py --image dataflow.png --type data_flow --classification CUI --json           # Validate data flow markings
python tools/compliance/diagram_validator.py --image arch.png --type architecture --json                                 # Validate architecture diagram

# Security
python tools/security/sast_runner.py --project-dir "/path"
python tools/security/dependency_auditor.py --project-dir "/path"
python tools/security/secret_detector.py --project-dir "/path"
python tools/security/container_scanner.py --image "my-image:latest"

# Infrastructure
python tools/infra/terraform_generator.py --project-id "proj-123"
python tools/infra/ansible_generator.py --project-id "proj-123"
python tools/infra/k8s_generator.py --project-id "proj-123"
python tools/infra/pipeline_generator.py --project-id "proj-123"
python tools/infra/rollback.py --deployment-id "deploy-123"

# Knowledge & Self-Healing
python tools/knowledge/pattern_detector.py --log-data "/path/to/logs"
python tools/knowledge/self_heal_analyzer.py --failure-id "fail-123"
python tools/knowledge/recommendation_engine.py --project-id "proj-123"

# Monitoring
python tools/monitor/log_analyzer.py --source elk --query "error"
python tools/monitor/health_checker.py --target "http://service:8080/health"

# Dashboard (Flask web UI — "GI proof" UX)
python tools/dashboard/app.py                        # Start web dashboard on port 5000
# Dashboard pages:
#   /                  — Home dashboard with auto-notifications
#   /projects          — Project listing with friendly timestamps
#   /projects/<id>     — Project detail with role-based tab visibility
#   /agents            — Agent registry with heartbeat age
#   /monitoring        — Monitoring with status icons + accessibility
#   /wizard            — Getting Started wizard (3 questions → workflow recommendation)
#   /quick-paths       — Quick Path workflow templates + error recovery reference
#   /events            — Real-time event timeline (SSE)
#   /query             — Natural language compliance queries
#   /batch             — Batch operations panel (multi-tool workflow execution)
# Role-based views:  ?role=pm | developer | isso | co
# UX features: glossary tooltips, friendly timestamps, breadcrumbs, ARIA accessibility,
#   skip-to-content, notification toasts, progress pipeline, help icons, error recovery
# Charts: SVG sparkline, line, bar, donut, gauge (charts.js — zero dependencies)
# Tables: search, sort, filter, CSV export (tables.js — auto-enhances all tables)
# Onboarding: first-visit tour with spotlight overlay (tour.js — localStorage detection)
# Live updates: SSE auto-refresh with connection status indicator (live.js)
# Batch ops: 4 built-in workflows (ATO, Security, Compliance, Build) run from UI (batch.js)
# Keyboard: g+key navigation, ? for help modal, / for search (shortcuts.js)

# DevSecOps Profile & Pipeline Security (Phase 24)
python tools/devsecops/profile_manager.py --project-id "proj-123" --create --maturity level_3_defined --json   # Create DevSecOps profile
python tools/devsecops/profile_manager.py --project-id "proj-123" --detect --json                              # Auto-detect maturity
python tools/devsecops/profile_manager.py --project-id "proj-123" --assess --json                              # Assess maturity level
python tools/devsecops/profile_manager.py --project-id "proj-123" --json                                       # Get profile
python tools/devsecops/pipeline_security_generator.py --project-id "proj-123" --json                           # Generate pipeline security stages
python tools/devsecops/policy_generator.py --project-id "proj-123" --engine kyverno --json                     # Generate Kyverno policies
python tools/devsecops/policy_generator.py --project-id "proj-123" --engine opa --json                         # Generate OPA policies
python tools/devsecops/attestation_manager.py --project-id "proj-123" --generate --json                        # Generate signing config

# Zero Trust Architecture (Phase 25)
python tools/devsecops/zta_maturity_scorer.py --project-id "proj-123" --all --json                             # Score all 7 ZTA pillars
python tools/devsecops/zta_maturity_scorer.py --project-id "proj-123" --pillar user_identity --json            # Score individual pillar
python tools/devsecops/zta_maturity_scorer.py --project-id "proj-123" --trend --json                           # Maturity trend
python tools/compliance/nist_800_207_assessor.py --project-id "proj-123" --json                                # NIST 800-207 assessment
python tools/compliance/nist_800_207_assessor.py --project-id "proj-123" --gate                                # NIST 800-207 gate
python tools/devsecops/service_mesh_generator.py --project-id "proj-123" --mesh istio --json                   # Generate Istio service mesh
python tools/devsecops/service_mesh_generator.py --project-id "proj-123" --mesh linkerd --json                 # Generate Linkerd service mesh
python tools/devsecops/network_segmentation_generator.py --project-path /path --namespaces "app,data" --json   # Namespace isolation
python tools/devsecops/network_segmentation_generator.py --project-path /path --services "api,db" --json       # Microsegmentation
python tools/devsecops/zta_terraform_generator.py --project-path /path --modules all --json                    # ZTA Terraform modules
python tools/devsecops/pdp_config_generator.py --project-id "proj-123" --pdp-type disa_icam --json             # PDP config
python tools/devsecops/pdp_config_generator.py --project-id "proj-123" --pdp-type zscaler --mesh istio --json  # PEP config

# DoD MOSA (Phase 26 — Modular Open Systems Approach)
python tools/compliance/mosa_assessor.py --project-id "proj-123" --json                                        # MOSA assessment
python tools/compliance/mosa_assessor.py --project-id "proj-123" --gate                                        # MOSA gate check
python tools/mosa/modular_design_analyzer.py --project-dir /path --project-id "proj-123" --store --json        # Modularity analysis
python tools/mosa/mosa_code_enforcer.py --project-dir /path --fix-suggestions --json                           # Code enforcement
python tools/mosa/icd_generator.py --project-id "proj-123" --all --json                                        # Generate ICDs
python tools/mosa/icd_generator.py --project-id "proj-123" --interface-id "iface-1" --json                     # Generate single ICD
python tools/mosa/tsp_generator.py --project-id "proj-123" --json                                              # Generate TSP
python tools/compliance/cato_monitor.py --project-id "proj-123" --mosa-evidence                                # MOSA cATO evidence

# CLI Output Formatting
# Any tool that supports --json also supports --human for colored terminal output:
#   python tools/compliance/stig_checker.py --project-id "proj-123" --human
#   python tools/maintenance/maintenance_auditor.py --project-id "proj-123" --human
# Programmatic usage:
#   from tools.cli.output_formatter import format_table, format_banner, format_score
#   print(format_table(["Name", "Status"], [["App1", "healthy"], ["App2", "degraded"]]))

# SaaS Multi-Tenancy (Phase 21)
python tools/saas/platform_db.py --init                                          # Initialize platform database
python tools/saas/tenant_manager.py --create --name "ACME" --il IL4 --tier professional --admin-email admin@acme.gov
python tools/saas/tenant_manager.py --list --json                                # List all tenants
python tools/saas/tenant_manager.py --provision --tenant-id "tenant-uuid"        # Provision tenant (create DB, K8s NS)
python tools/saas/tenant_manager.py --approve --tenant-id "tenant-uuid" --approver-id "admin-uuid"  # Approve IL5/IL6 tenant
python tools/saas/tenant_manager.py --add-user --tenant-id "tenant-uuid" --email dev@acme.gov --role developer
python tools/saas/api_gateway.py --port 8443 --debug                             # Start API gateway (dev mode)
gunicorn -w 4 -b 0.0.0.0:8443 tools.saas.api_gateway:app                       # Start API gateway (production)
python tools/saas/licensing/license_generator.py --generate --customer "ACME" --tier enterprise --expires-in-days 365 --private-key /path/key.pem
python tools/saas/licensing/license_validator.py --validate --json               # Validate on-prem license
python tools/saas/infra/namespace_provisioner.py --create --slug acme --il IL4 --tier professional  # Create tenant K8s NS
helm install icdev deploy/helm/ --values deploy/helm/values.yaml                 # Deploy on-prem via Helm

# CI/CD Integration (GitHub + GitLab dual support)
python tools/ci/triggers/webhook_server.py           # Start webhook server (POST /gh-webhook, /gl-webhook)
python tools/ci/triggers/poll_trigger.py             # Start issue polling (every 20s)
python tools/ci/workflows/icdev_plan.py 123          # Run planning phase for issue #123
python tools/ci/workflows/icdev_build.py 123 abc1234 # Run build phase (requires run-id)
python tools/ci/workflows/icdev_test.py 123 abc1234  # Run test phase
python tools/ci/workflows/icdev_review.py 123 abc1234 # Run review phase
python tools/ci/workflows/icdev_sdlc.py 123          # Run full SDLC pipeline
python tools/ci/workflows/icdev_sdlc.py 123 --orchestrated  # DAG-based parallel SDLC
python tools/ci/workflows/icdev_plan_build.py 123    # Run plan + build

# Multi-Agent Orchestration (Opus 4.6)
python tools/agent/bedrock_client.py --probe          # Check Bedrock model availability
python tools/agent/bedrock_client.py --prompt "text" --model opus --effort high  # Invoke Bedrock
python tools/agent/bedrock_client.py --prompt "text" --stream  # Streaming invocation
python tools/agent/token_tracker.py --action summary --project-id "proj-123"  # Token usage summary
python tools/agent/token_tracker.py --action cost --project-id "proj-123"     # Cost breakdown
python tools/agent/team_orchestrator.py --decompose "task description" --project-id "proj-123"  # Decompose task into DAG
python tools/agent/team_orchestrator.py --execute --workflow-id "wf-123"      # Execute workflow
python tools/agent/skill_router.py --route-skill "ssp_generate"              # Route skill to healthy agent
python tools/agent/skill_router.py --health                                  # Show healthy agents
python tools/agent/collaboration.py --pattern reviewer --project-id "proj-123"  # Run reviewer pattern
python tools/agent/authority.py --check security-agent code_generation       # Check domain authority
python tools/agent/mailbox.py --inbox --agent-id "builder-agent"             # Check agent inbox
python tools/agent/agent_memory.py --recall --agent-id "builder-agent" --project-id "proj-123"  # Recall memories
python tools/agent/agent_executor.py --prompt "text" --bedrock               # Execute via Bedrock API
```

### Databases

| Database | Tables | Purpose |
|----------|--------|---------|
| `data/icdev.db` | 121 tables | Main operational DB: projects, agents, A2A tasks, audit trail, compliance (NIST, FedRAMP, CMMC, CSSP, SbD, IV&V, OSCAL, FIPS 199/200), eMASS, cATO evidence, PI tracking, knowledge, deployments, metrics, alerts, maintenance audit, MBSE, Modernization, RICOAS (intake, boundary, supply chain, simulation, integration), TAC-8 (hook_events, agent_executions, nlq_queries, ci_worktrees, gitlab_task_claims), Multi-Agent Orchestration (agent_token_usage, agent_workflows, agent_subtasks, agent_mailbox, agent_vetoes, agent_memory, agent_collaboration_history), Agentic Generation (child_app_registry, agentic_fitness_assessments), Security Categorization (fips199_categorizations, project_information_types, fips200_assessments), Marketplace (marketplace_assets, marketplace_versions, marketplace_reviews, marketplace_installations, marketplace_scan_results, marketplace_ratings, marketplace_embeddings, marketplace_dependencies), Universal Compliance (data_classifications, framework_applicability, compliance_detection_log, crosswalk_bridges, framework_catalog_versions, cjis_assessments, hipaa_assessments, hitrust_assessments, soc2_assessments, pci_dss_assessments, iso27001_assessments), DevSecOps/ZTA (devsecops_profiles, zta_maturity_scores, zta_posture_evidence, nist_800_207_assessments, devsecops_pipeline_audit), MOSA (mosa_assessments, icd_documents, tsp_documents, mosa_modularity_metrics) |
| `data/platform.db` | 6 tables | SaaS platform DB: tenants, users, api_keys, subscriptions, usage_records, audit_platform |
| `data/tenants/{slug}.db` | (per-tenant) | Isolated copy of icdev.db schema per tenant — separate DB per tenant for strongest isolation |
| `data/memory.db` | 3 tables | Memory system: entries, daily logs, access log |
| `data/activity.db` | 1 table | Task tracking |

**Audit trail is append-only/immutable** — no UPDATE/DELETE operations. Satisfies NIST 800-53 AU controls.

### Args Configuration Files

| File | Purpose |
|------|---------|
| `args/project_defaults.yaml` | TDD settings, compliance baseline, security thresholds, infra defaults, CI/CD stages, monitoring, agent config |
| `args/agent_config.yaml` | 13 agent definitions with ports, TLS certs, Bedrock model config |
| `args/cui_markings.yaml` | CUI banner templates, designation indicators, portion marking rules |
| `args/security_gates.yaml` | Gate thresholds for code review, merge, deployment, FedRAMP, CMMC, cATO, RICOAS, supply chain (CAT1/CAT2, critical/high vulns) |
| `args/monitoring_config.yaml` | ELK/Splunk/Prometheus/Grafana endpoints, self-healing thresholds, SLA targets |
| `args/ricoas_config.yaml` | RICOAS settings: readiness weights/thresholds, gap detection, cost models, supply chain SLAs, integration mappings |
| `args/observability_config.yaml` | Hook settings, HMAC signing, SIEM forwarding, agent executor defaults, retention |
| `args/nlq_config.yaml` | NLQ-to-SQL settings: Bedrock model, row limits, blocked SQL patterns, SSE heartbeat |
| `args/worktree_config.yaml` | Git worktree settings: sparse checkout, cleanup policy, GitLab polling, tag-to-workflow mapping |
| `args/bedrock_models.yaml` | Bedrock model registry: model IDs, capabilities, pricing, fallback chain, probe interval, per-agent effort defaults |
| `args/agent_authority.yaml` | Domain authority matrix: Security (hard veto on code/deps/infra), Compliance (hard veto on artifacts/deploy), Architect (soft veto on design) |
| `args/marketplace_config.yaml` | Marketplace settings: scan gates, approval policies, federation sync, search weights, IL compatibility, community ratings |
| `args/classification_config.yaml` | Universal data classification: 10 data categories (CUI, PHI, PCI, CJIS, etc.), composite rules, banner templates, sensitivity order |
| `args/framework_registry.yaml` | All compliance frameworks: 20 active + planned, dual-hub model, data category triggers, bridge references |
| `args/mosa_config.yaml` | DoD MOSA settings: auto-trigger rules (DoD/IC + IL4+), modularity scoring weights, thresholds, ICD/TSP config, cATO integration flag, code enforcement, intake detection |
| `args/devsecops_config.yaml` | DevSecOps profile schema: 10 stages, 5 maturity levels, tool selections, intake detection keywords |
| `args/zta_config.yaml` | ZTA 7-pillar maturity model (DoD ZTA Strategy), service mesh options, policy engines, PDP references, posture scoring |
| `args/cli_config.yaml` | Optional CLI capabilities: 4 independent toggles (CI/CD automation, parallel agents, container execution, scripted intake), tenant ceiling, cost controls, environment detection |

### Key Architecture Decisions
- **D1:** SQLite for ICDEV internals (zero-config portability); PostgreSQL for apps ICDEV builds
- **D2:** Stdio for MCP (Claude Code); HTTPS+mTLS for A2A (K8s inter-agent)
- **D3:** Flask over FastAPI (simpler, fewer deps, auditable SSR, smaller STIG surface)
- **D4:** Statistical methods for pattern detection; Bedrock LLM for root cause analysis
- **D5:** CUI markings applied at generation time (inline, not post-processing)
- **D6:** Audit trail is append-only/immutable (no UPDATE/DELETE — NIST AU compliance)
- **D7:** Python stdlib `xml.etree.ElementTree` for XMI/ReqIF parsing (zero deps, air-gap safe)
- **D8:** Normalized DB tables for model elements (enables SQL joins across digital thread)
- **D9:** M-ATLAS adds "Model" pre-phase to ATLAS (backward compatible — skips if no model)
- **D10:** File-based sync only for Cameo (air-gapped desktop, no API — XMI export/import)
- **D11:** PI-snapshot versioning with SHA-256 content hashing for drift detection
- **D12:** N:M digital thread links (one block → many code modules; one control → many requirements)
- **D13:** Python `ast` module for Python analysis; regex-based parsing for Java/C# (air-gap safe, zero deps)
- **D14:** 7R scoring uses weighted multi-criteria decision matrix with configurable weights
- **D15:** Strangler fig tracking uses digital thread to maintain dual-system traceability (`replaces` link type)
- **D16:** Database migration generates DDL scripts, not runtime tools (reviewed by DBA, air-gap safe)
- **D17:** Framework migration patterns are declarative JSON (add new mappings without code changes)
- **D18:** Legacy analysis is read-only — never modifies source code in place (output to separate dir)
- **D19:** ATO-aware decomposition inherits control mappings from monolith via crosswalk engine
- **D20:** Agent chat stores conversation turns in SQLite, not WebSocket (air-gap compatible, auditable, save/resume)
- **D21:** Readiness scoring uses deterministic weighted average (reproducible, not probabilistic)
- **D22:** Monte Carlo uses Python stdlib `random` (zero deps, air-gap safe)
- **D23:** COA generation uses Bedrock with structured JSON output (mission intent extraction needs LLM)
- **D24:** Jira/ServiceNow/GitLab integration uses REST API with field mapping in DB (change mapping without code changes)
- **D25:** ReqIF export reuses `xml.etree.ElementTree` (consistent with D7)
- **D26:** Boundary impact rules stored as declarative JSON (add rules without code changes, D17 pattern)
- **D27:** Supply chain graph stored as SQL adjacency list (enables recursive queries, no graph DB needed)
- **D28:** Simulation chart data stored as JSON for inline rendering (works in agent chat and dashboard)
- **D29:** SSE over WebSocket for dashboard live updates (Flask-native, simpler, no additional deps, unidirectional sufficient)
- **D30:** Bedrock for NLQ→SQL (not OpenAI) — air-gap safe, consistent with D23, GovCloud available
- **D31:** HMAC-SHA256 event signing for hooks (tamper detection without PKI overhead, secret via AWS Secrets Manager)
- **D32:** Git worktrees with sparse checkout for task isolation (zero-conflict parallelism, per-task branches, classification markers)
- **D33:** GitLab tags `{{icdev: workflow}}` for task routing (mirrors TAC-8 Notion pattern, uses existing VCS abstraction)
- **D34:** Read-only SQL enforcement for NLQ (append-only audit trail must not be compromised by NLQ queries)
- **D35:** Agent executor stores JSONL output in `agents/` dir (auditable, replayable, consistent with TAC-8 pattern)
- **D36:** `boto3 invoke_model()` + `invoke_model_with_response_stream()` for Bedrock, `ThreadPoolExecutor` for parallelism — matches existing subprocess/sqlite3 patterns, no asyncio
- **D37:** Model fallback chain: Opus 4.6 → Sonnet 4.5 → Sonnet 3.5 with cached health probing (30min TTL)
- **D38:** Effort parameter mapped per agent role (Orchestrator=high, Builder=max, Monitor=low) — optimize cost/quality per agent
- **D39:** Structured outputs via `output_config.format` with JSON Schema per agent response type — enforce agent response contracts
- **D40:** `graphlib.TopologicalSorter` (stdlib Python 3.9+) for task DAG — air-gap safe, zero deps, cycle detection built-in
- **D41:** SQLite-based agent mailbox with HMAC-SHA256 signing — air-gap safe, append-only for audit, tamper-evident
- **D42:** Domain authority defined in YAML matrix, vetoes recorded append-only — configurable without code changes, auditable
- **D43:** Agent memory scoped by `(agent_id, project_id)` with team-shared via `agent_id='_team'` — prevents cross-project contamination
- **D44:** Flag-based (`--agentic`) for backward compatibility — omitting flag produces identical output
- **D45:** Copy-and-adapt over template library — ICDEV tools are the source of truth
- **D46:** Fitness scoring: weighted rule-based + optional LLM override
- **D47:** Blueprint-driven generation — single config drives all generators
- **D48:** ICDEV callback uses A2A protocol for child→parent communication
- **D49:** Agentic tests as Step 8 (conditional) in test pipeline
- **D50:** Dynamic CLAUDE.md via Jinja2 — documents only what's present
- **D51:** Minimal DB + migration — core tables first, expand as capabilities activate
- **D52:** 3-layer grandchild prevention (config flag + scaffolder strip + CLAUDE.md doc)
- **D53:** Port offset for child agents (default +1000, configurable)
- **D54:** FIPS 199 uses high watermark across SP 800-60 information types; provisionals are defaults, adjustable per org
- **D55:** FIPS 200 validates all 17 minimum security areas against baseline from FIPS 199, not impact level alone
- **D56:** SSP baseline selection is dynamic: query DB for categorization first, fall back to IL mapping
- **D57:** CNSSI 1253 auto-applies for IL6/SECRET; elevates minimum C/I/A floor per overlay rules
- **D58:** SaaS layer wraps existing tools, doesn't rewrite — preserves 20 phases of work; API gateway is additive
- **D59:** PostgreSQL for all SaaS databases — concurrent writes, MVCC, RLS capability, RDS managed (SQLite fallback for dev)
- **D60:** Separate database per tenant — strongest isolation, simplest compliance, easy backup/restore per tenant
- **D61:** API gateway as thin routing layer — auth + tenant resolution + routing; tools stay deterministic
- **D62:** MCP Streamable HTTP transport alongside REST — supports Claude Code users (MCP) and generic HTTP clients (REST)
- **D63:** Per-tenant K8s namespace (IL2-4), per-tenant AWS sub-account (IL5-6) — isolation scales with classification
- **D64:** Offline license keys with RSA-SHA256 signatures — air-gap safe, no license server needed for on-prem
- **D65:** Helm chart for on-prem deployment — standard K8s packaging, customer's own infrastructure
- **D66:** Provider abstraction pattern (ABC + implementations) — interface + adapters; vendor logic isolated per provider
- **D67:** OpenAI-compatible provider covers Ollama, vLLM, Azure — all use same API spec, one implementation with configurable base_url
- **D68:** Function-level LLM routing (not agent-level) — NLQ needs fast/cheap, code gen needs strong coder; function granularity gives best-of-breed control
- **D69:** Fallback chains per function — air-gapped deploys set `prefer_local: true`, chains end with local models; cloud deploys use cloud-first chains
- **D70:** BedrockClient preserved for Bedrock-specific callers; tools.llm provides vendor-agnostic alternative
- **D71:** llm_config.yaml is single source of truth for all LLM model routing — replaces scattered hardcoded model IDs
- **D72:** Embedding providers same pattern as LLM providers — Ollama nomic-embed-text for air-gapped, OpenAI for cloud, Bedrock Titan as middle option
- **D73:** Graceful degradation on missing SDKs — each provider handles missing `anthropic`, `openai`, or `boto3` imports
- **D74:** Marketplace is a SaaS module (reuse Phase 21 auth, RBAC, tenant isolation, API gateway)
- **D75:** Federated 3-tier catalog: tenant-local → cross-tenant review → central vetted registry
- **D76:** 7-gate automated + mandatory human review for cross-tenant sharing
- **D77:** Independent IL marking per asset with high-watermark consumption rule
- **D78:** Ollama nomic-embed-text for air-gapped marketplace semantic search (D72 pattern)
- **D79:** Full GOTCHA asset sharing: skills, goals, hardprompts, context, args, compliance extensions
- **D80:** Append-only marketplace audit trail (publish, install, review, rate) per D6 pattern
- **D81:** Asset SBOM generation required for executable assets (supply chain traceability)
- **D82:** Ollama LLaVA for air-gapped vision; vision is a message format concern (multimodal content blocks), not a provider architecture concern — all 3 providers (Bedrock, Anthropic, OpenAI-compat) support it via existing infrastructure
- **D83:** Page-by-page PDF vision fallback — pypdf text extraction first, vision LLM only for pages with no extractable text (scanned PDFs)
- **D84:** Image auto-classification via vision LLM at upload time — stored in `extracted_sections` column as JSON `{category, confidence, description}`
- **D85:** UI complexity as optional 7R scoring dimension — D44 backward-compatible flag pattern; skipped when no UI analysis exists
- **D86:** Vision diagram extraction is advisory-only — requires `--store` flag to write elements to DB (human review gate before model contamination)
- **D87:** Attachment analysis reuses `screenshot_validator.encode_image()` for single image encoding path across all vision tools
- **D88:** UX Translation Layer wraps existing tools without rewriting them — Jinja2 filters + JS modules convert technical output to business-friendly display
- **D89:** Glossary tooltip system uses `data-glossary` HTML attributes + client-side JS — no backend changes needed to add new terms
- **D90:** Role-based views via `?role=` query parameter + Flask context processor — no authentication required, progressive disclosure by persona
- **D91:** Getting Started wizard uses declarative path mapping (goal × role × classification → recommended workflow) — add new paths without code changes
- **D92:** Error recovery dictionary maps gate failure codes to plain-English fix instructions with who/what/why/fix/estimated-time — non-technical users can self-serve
- **D93:** Quick Path templates are declarative data (list of dicts in ux_helpers.py) — add new workflow shortcuts without touching templates
- **D94:** SVG chart library (charts.js) is zero-dependency, renders server data into lightweight SVG — no Chart.js/D3 needed, air-gap safe, WCAG accessible (role="img", aria-label)
- **D95:** Table interactivity (tables.js) auto-enhances all `.table-container` tables on page load — search, sort, filter, CSV export with no per-table configuration
- **D96:** CLI output formatter uses only Python stdlib (ANSI codes, os.get_terminal_size) — `--human` flag on any tool for colored tables/banners/scores instead of JSON
- **D97:** SaaS portal UX mirrors main dashboard patterns (glossary, breadcrumbs, skip-link, ARIA) via portal-specific CSS/JS — no shared dependency to avoid coupling
- **D98:** Onboarding tour uses localStorage (`icdev_tour_completed`) for first-visit detection — no server-side user tracking, air-gap safe
- **D99:** SSE live updates debounce to 3-second batches — prevents API hammering while keeping dashboard near-real-time
- **D100:** Batch operations run as sequential subprocesses in background threads — Flask request returns immediately, frontend polls status
- **D101:** Keyboard shortcuts use chord pattern (`g` + key) to avoid conflicts with browser shortcuts — 1.5s chord window, cancelled on invalid key
- **D102:** All Medium Impact UX modules inject styles via JS (no additional CSS files) — consistent with ux.js pattern, self-contained modules
- **D109:** Composable data markings — single artifact can carry CUI + PHI + PCI markings simultaneously; highest-sensitivity category determines handling
- **D110:** Compliance auto-detection is advisory only — system recommends frameworks based on data types; customer ISSO must confirm before gates enforce
- **D111:** Dual-hub crosswalk model — NIST 800-53 as US hub, ISO 27001 as international hub, bidirectional bridge connects both; implement once at either hub, cascade everywhere
- **D112:** Framework catalogs are versioned independently — each JSON catalog has its own version; update one framework without touching others
- **D113:** Multi-regime deduplication via crosswalk — assessing N frameworks produces 1 unified NIST control set, not N separate assessments
- **D114:** Compliance framework as marketplace asset type — community-contributed framework catalogs can be published, scanned, and installed via Phase 22 marketplace
- **D115:** Data type → framework mapping is declarative JSON — add new detection rules without code changes; `data_type_framework_map.json` drives all auto-detection
- **D116:** BaseAssessor ABC pattern (mirrors D66 provider pattern) — all assessors inherit from base class with crosswalk integration, gate evaluation, and CLI; ~60 LOC per new framework vs ~400+ LOC
- **D117:** New DevSecOps/ZTA Agent (port 8457) with hard veto on pipeline_configuration, zero_trust_policy, deployment_gate — hybrid approach distributes scanning to Security Agent, IaC to Infra Agent, compliance to Compliance Agent
- **D118:** NIST 800-207 maps into existing NIST 800-53 US hub (not a third hub) — ZTA is an architecture guide; requirements crosswalk to AC-2, AC-3, SA-3, SC-7, SI-4, AU-2, etc.
- **D119:** DevSecOps profile is a per-project YAML config (`devsecops_profiles` table) declaring active pipeline security stages — detected during intake, overridable post-intake
- **D120:** ZTA maturity model uses DoD 7-pillar scoring (Traditional → Advanced → Optimal) tracked per project per pillar
- **D121:** Service mesh and policy engine are profile-selectable (Istio/Linkerd, Kyverno/OPA) — both generated, customer picks in profile
- **D122:** DevSecOps/ZTA profile inherited by child apps generated via `/icdev-agentic` (extends D44 flag pattern)
- **D123:** ZTA posture score feeds into cATO monitor as additional evidence dimension (extends `cato_evidence` table)
- **D124:** PDP modeled as external reference in ZTA profile (Zscaler, Palo Alto, DISA ICAM) — ICDEV generates PEP configs but does not implement PDP itself
- **D125:** MOSA auto-triggers for all DoD/IC customers during intake (not just MDAPs) — IL4+ also triggers MOSA consideration
- **D126:** MOSA focuses on software development principles only (no FACE/VICTORY/SOSA/HOST domain-specific profiles)
- **D127:** MOSA implemented as full compliance framework via BaseAssessor pattern (D116) with gate, crosswalk, multi-regime
- **D128:** ICD/TSP are generated compliance artifacts (mirrors SSP/POAM pattern), stored in DB with CUI markings
- **D129:** MOSA code enforcement uses static analysis (coupling/cohesion/interface coverage) — deterministic, air-gap safe
- **D130:** MOSA cATO evidence is optional (config flag `cato_integration.enabled: true` in mosa_config.yaml)
- **D131:** Modularity metrics stored as time-series in `mosa_modularity_metrics` table for trend tracking
- **D132:** CLI capabilities are optional per-project toggles with tenant-level ceiling. Tenant sets maximum allowed capabilities; project enables within ceiling. Default is all-disabled — VSCode extension provides full functionality. CLI adds headless/scripted/parallel/containerized execution modes for environments that support them. Cost controls enforce token budgets. Detection auto-checks CLI availability and falls back gracefully.

### Self-Healing System
- **Confidence ≥ 0.7** + auto_healable → auto-remediate
- **Confidence 0.3–0.7** → suggest fix, require human approval
- **Confidence < 0.3** → escalate with full context
- Max 5 auto-heals/hour, 10-minute cooldown between same-pattern heals

### Security Gates (Blocking Conditions)
- **Code Review Gate:** ≥1 approval, all comments resolved, SAST clean, no secrets, CUI markings present
- **Merge Gate:** All tests pass, ≥80% coverage, 0 CAT1 STIG, 0 critical vulns, SBOM current
- **Deploy Gate:** Staging tests pass, compliance artifacts current, change request approved, rollback plan exists
- **FedRAMP Gate:** 0 other_than_satisfied on high-priority controls, encryption FIPS 140-2 required
- **CMMC Gate:** 0 not_met Level 2 practices, evidence current within 90 days
- **cATO Gate:** 0 expired evidence on critical controls, readiness ≥50%
- **DES Gate:** 0 non_compliant on critical DoDI 5000.87 Digital Engineering requirements
- **Migration Gate:** ATO coverage ≥95% maintained during modernization, compliance bridge validated per PI
- **RICOAS Gate:** Readiness score ≥0.7, 0 unresolved critical gaps, RED requirements must have alternative COAs
- **Supply Chain Gate:** 0 critical SCRM risks unmitigated, 0 expired ISAs with active data flows, 0 overdue critical CVE SLAs, 0 Section 889 prohibited vendors
- **FIPS 199 Gate:** Categorization required for ATO projects, IL6 must have CNSSI 1253, categorization approved
- **FIPS 200 Gate:** 0 not_satisfied requirement areas, all 17 areas assessed, coverage ≥80%
- **Marketplace Publish Gate:** 0 critical/high SAST findings, 0 secrets, 0 critical/high dep vulns, CUI markings present, SBOM generated, digitally signed
- **Marketplace Cross-Tenant Gate:** All publish gate requirements + human ISSO/security officer review completed + code review confirmed
- **Multi-Regime Gate:** All applicable frameworks must pass individual gates; overall pass requires 0 framework failures across all detected regimes
- **HIPAA Gate:** 0 not_satisfied on Administrative/Technical Safeguards, encryption FIPS 140-2 required for PHI
- **PCI DSS Gate:** 0 not_satisfied on Requirements 3-4 (data protection), 6 (secure development), 10 (logging)
- **CJIS Gate:** 0 not_satisfied on Policy Areas 4 (audit), 5 (access control), 6 (identification), 10 (encryption)
- **DevSecOps Gate:** 0 critical policy-as-code violations, 0 missing image attestations (when active), 0 unresolved critical SAST findings, 0 detected secrets
- **ZTA Gate:** ZTA maturity ≥ Advanced (0.34) for IL4+, mTLS enforced when service mesh active, default-deny NetworkPolicy required, no pillar at 0.0
- **MOSA Gate:** 0 external interfaces without ICD, 0 circular dependencies, modularity score ≥ 0.6, 0 direct coupling violations; warn on interface coverage < 80%, TSP expired/missing

### Docker & K8s Deployment
- `docker/Dockerfile.agent-base` — STIG-hardened base for all agents (non-root, minimal packages)
- `docker/Dockerfile.dashboard` — STIG-hardened Flask dashboard
- `docker/Dockerfile.mbse-agent` — STIG-hardened MBSE agent (port 8451)
- `docker/Dockerfile.modernization-agent` — STIG-hardened Modernization agent (port 8452)
- `docker/Dockerfile.requirements-analyst-agent` — STIG-hardened Requirements Analyst agent (port 8453)
- `docker/Dockerfile.supply-chain-agent` — STIG-hardened Supply Chain agent (port 8454)
- `docker/Dockerfile.simulation-agent` — STIG-hardened Simulation agent (port 8455)
- `docker/Dockerfile.integration-agent` — STIG-hardened Integration agent (port 8456)
- `docker/Dockerfile.devsecops-agent` — STIG-hardened DevSecOps/ZTA agent (port 8457)
- `docker/Dockerfile.api-gateway` — STIG-hardened SaaS API gateway (port 8443, gunicorn)
- `k8s/` — Full K8s manifests: namespace, configmap, secrets, network policies (default deny), ingress, 16+ deployment+service pairs
- `k8s/saas/` — SaaS-specific K8s manifests: tenant-namespace-template, api-gateway-deployment, platform-db-deployment
- `deploy/helm/` — Helm chart for on-prem deployment (Chart.yaml, values.yaml, templates/)
- `deploy/offline/` — Air-gapped installer (install.py, install.sh, README.md)
- All containers: read-only rootfs, drop ALL capabilities, non-root (UID 1000), resource limits enforced

---

## Existing Goals

| Goal | File | Purpose |
|------|------|---------|
| ATLAS/M-ATLAS Workflow | `goals/build_app.md` | 5/6-step build: [Model →] Architect → Trace → Link → Assemble → Stress-test |
| Init Project | `goals/init_project.md` | Project initialization with compliance scaffolding |
| TDD Workflow | `goals/tdd_workflow.md` | RED→GREEN→REFACTOR cycle with Cucumber/Gherkin |
| Compliance Workflow | `goals/compliance_workflow.md` | Generate SSP, POAM, STIG, SBOM, CUI markings |
| Security Scan | `goals/security_scan.md` | SAST, dependency audit, secret detection, container scan |
| Deploy Workflow | `goals/deploy_workflow.md` | IaC generation, pipeline, staging, production deploy |
| Code Review | `goals/code_review.md` | Enforced review gates with security checks |
| Self-Healing | `goals/self_healing.md` | Pattern detection, root cause analysis, auto-remediation |
| Monitoring | `goals/monitoring.md` | Log analysis, metrics, alerts, health checks |
| Dashboard | `goals/dashboard.md` | Web UI for project status, compliance, security |
| Agent Management | `goals/agent_management.md` | A2A agent lifecycle, registration, health |
| Integration Testing | `goals/integration_testing.md` | Multi-layer testing: unit, BDD, E2E (Playwright), gates |
| CI/CD Integration | `goals/cicd_integration.md` | GitHub + GitLab dual-platform webhooks, polling, workflow automation |
| SbD & IV&V Workflow | `goals/sbd_ivv_workflow.md` | Secure by Design assessment + IV&V certification (CISA, IEEE 1012, DoDI 5000.87) |
| Maintenance Audit | `goals/maintenance_audit.md` | Dependency scanning, vulnerability checking, SLA enforcement, auto-remediation |
| ATO Acceleration | `goals/ato_acceleration.md` | Multi-framework ATO: FedRAMP + CMMC + OSCAL + eMASS + cATO monitoring |
| MBSE Integration | `goals/mbse_integration.md` | Model-Based Systems Engineering: SysML, DOORS NG, digital thread, model-code sync, DES compliance |
| App Modernization | `goals/modernization_workflow.md` | Legacy app modernization: 7Rs assessment, version/framework migration, monolith decomposition, strangler fig, ATO compliance bridge |
| Requirements Intake | `goals/requirements_intake.md` | AI-driven conversational intake, gap detection, SAFe decomposition, readiness scoring, document extraction (RICOAS) |
| Boundary & Supply Chain | `goals/boundary_supply_chain.md` | ATO boundary impact (4-tier), supply chain dependency graph, ISA lifecycle, SCRM, CVE triage (RICOAS) |
| Simulation Engine | `goals/simulation_engine.md` | Digital Program Twin — 6-dimension what-if simulation, Monte Carlo, COA generation & comparison (RICOAS) |
| External Integration | `goals/external_integration.md` | Bidirectional Jira/ServiceNow/GitLab sync, DOORS NG ReqIF export, approval workflows, RTM traceability (RICOAS) |
| Observability | `goals/observability.md` | Hook-based agent monitoring: tool usage tracking, HMAC-signed events, agent execution framework, SIEM forwarding (TAC-8) |
| NLQ Compliance | `goals/nlq_compliance.md` | Natural language compliance queries via Bedrock, read-only SQL enforcement, SSE dashboard events (TAC-8) |
| Parallel CI/CD | `goals/parallel_cicd.md` | Git worktree task isolation, GitLab `{{icdev: workflow}}` tag routing, parallel workflow execution (TAC-8) |
| Framework Planning | `goals/framework_planning.md` | Language-specific build commands (Python/Java/Go/Rust/C#/TypeScript), 12 Leverage Points framework (TAC-8) |
| Multi-Agent Orchestration | `goals/multi_agent_orchestration.md` | Opus 4.6 multi-agent: Bedrock client, DAG workflows, parallel execution, collaboration patterns, domain authority vetoes, agent mailbox, agent memory |
| Agentic Generation | `goals/agentic_generation.md` | Generate mini-ICDEV clone apps with GOTCHA/ATLAS |
| Security Categorization | `goals/security_categorization.md` | FIPS 199/200 categorization with SP 800-60 types, high watermark, CNSSI 1253, dynamic baseline |
| SaaS Multi-Tenancy | `goals/saas_multi_tenancy.md` | Multi-tenant SaaS platform: API gateway (REST+MCP Streamable HTTP), per-tenant DB isolation, 3 auth methods, subscription tiers, artifact delivery, tenant portal, Helm on-prem |
| Marketplace | `goals/marketplace.md` | Federated GOTCHA asset marketplace: publish, install, search, review, sync skills/goals/hardprompts/context/args/compliance across tenant orgs with 7-gate security pipeline |
| Universal Compliance | `goals/universal_compliance.md` | Universal Compliance Platform: 10 data categories, dual-hub crosswalk (NIST+ISO), 6 Wave 1 frameworks (CJIS, HIPAA, HITRUST, SOC 2, PCI DSS, ISO 27001), auto-detection, multi-regime assessment, composable markings |
| DevSecOps Workflow | `goals/devsecops_workflow.md` | DevSecOps profile management, maturity assessment, pipeline security generation, policy-as-code (Kyverno/OPA), image signing & attestation (Phase 24) |
| Zero Trust Architecture | `goals/zero_trust_architecture.md` | ZTA 7-pillar maturity scoring (DoD ZTA Strategy), NIST SP 800-207 compliance, service mesh (Istio/Linkerd), network segmentation, PDP/PEP config, cATO posture (Phase 25) |
| MOSA Workflow | `goals/mosa_workflow.md` | DoD MOSA (10 U.S.C. §4401): MOSA assessment, modularity analysis (coupling/cohesion/circular deps), ICD/TSP generation, code enforcement, intake auto-detection for DoD/IC, optional cATO evidence (Phase 26) |
| CLI Capabilities | `goals/cli_capabilities.md` | Optional Claude CLI features: CI/CD pipeline automation, parallel agent execution, container-based execution, scripted batch intake — 4 independent toggles with tenant ceiling and cost controls (Phase 27) |

---

## RICOAS — Requirements Intake, COA & Approval System

### Overview
RICOAS transforms vague customer requirements into structured, decomposed, MBSE-traced, compliance-validated work items through AI-driven conversational intake. Three new capabilities:

1. **Requirements Analyst Agent** (port 8453) — Conversational intake, gap detection, SAFe decomposition, readiness scoring, document extraction
2. **Supply Chain Agent** (port 8454) — Dependency graph, SBOM aggregation, ISA lifecycle, CVE triage, NIST 800-161 SCRM
3. **Simulation Agent** (port 8455) — Digital Program Twin, 6-dimension what-if simulation, Monte Carlo, COA generation

### Intake Pipeline (5 Stages)
1. **Session Setup** — Create intake session with customer info, impact level, classification, ATO context
2. **Conversational Intake** — AI-guided Q&A extracting requirements, detecting ambiguities and gaps in real-time
3. **Document Upload** — Upload SOW/CDD/CONOPS, extract shall/must/should statements as structured requirements
4. **Gap Detection & Readiness** — 5-dimension scoring (completeness, clarity, feasibility, compliance, testability), NIST gap analysis
5. **SAFe Decomposition** — Epic > Capability > Feature > Story > Enabler with WSJF scoring, T-shirt sizing, BDD criteria

### ATO Boundary Impact (4 Tiers)
| Tier | Criteria | ATO Impact |
|------|----------|------------|
| GREEN | No boundary change | None |
| YELLOW | Minor adjustment — new component within boundary | SSP addendum, possible POAM |
| ORANGE | Significant change — cross-boundary data flow | SSP revision, ISSO review |
| RED | ATO-invalidating — classification change, boundary expansion | **Full stop. Alternative COAs generated.** |

### COA Generation (3 + Alternatives)
- **Speed COA**: MVP scope (P1 only), 1-2 PIs, S-M cost, higher risk
- **Balanced COA**: P1+P2 scope, 2-3 PIs, M-L cost, moderate risk (recommended)
- **Comprehensive COA**: Full scope, 3-5 PIs, L-XL cost, lowest risk
- **Alternative COAs** (for RED items): Achieve same mission intent within existing ATO boundary

### Readiness Thresholds
- **0.7** — Proceed to decomposition
- **0.8** — Proceed to COA generation
- **0.9** — Proceed to implementation

---

## CI/CD Integration (GitHub + GitLab)

### Trigger Methods
- **Webhook Server:** `python tools/ci/triggers/webhook_server.py` — receives POST events from GitHub (`/gh-webhook`) and GitLab (`/gl-webhook`)
- **Poll Trigger:** `python tools/ci/triggers/poll_trigger.py` — polls issues every 20 seconds

### Workflow Commands (in issue body or comments)
- `/icdev_plan` — Planning only
- `/icdev_build run_id:abc12345` — Build (requires prior plan run_id)
- `/icdev_test run_id:abc12345` — Test
- `/icdev_review run_id:abc12345` — Review
- `/icdev_sdlc` — Complete lifecycle: Plan → Build → Test → Review
- `/icdev_plan_build` — Plan + Build
- `/icdev_plan_build_test` — Plan + Build + Test
- `/icdev_plan_build_test_review` — Plan + Build + Test + Review

### Claude Code Slash Commands (used by workflows)
| Command | Purpose |
|---------|---------|
| `/classify_issue` | Classify issue as /chore, /bug, /feature, /patch |
| `/classify_workflow` | Extract ICDEV workflow command from text |
| `/generate_branch_name` | Generate branch: `<type>-issue-<num>-icdev-<id>-<name>` |
| `/implement` | Implement a plan with CUI markings |
| `/commit` | Generate commit: `<agent>: <type>: <message>` |
| `/pull_request` | Create PR (GitHub) or MR (GitLab) |

### Platform Auto-Detection
VCS detects GitHub vs GitLab from `git remote get-url origin`. Uses `gh` CLI for GitHub, `glab` CLI for GitLab.

### Bot Loop Prevention
All bot comments include `[ICDEV-BOT]`. Webhooks ignore comments with this identifier.

---

## 12 Leverage Points of Agentic Development

Adapted from TAC-8 for the ICDEV GOTCHA framework. These are the 12 dimensions you can tune to improve agent behavior.

### In Agent (Core Four)
1. **Context** — What agents know (CLAUDE.md, goals/, context/ files)
2. **Model** — Which LLM (Bedrock Claude Sonnet/Opus via agent_config.yaml)
3. **Prompt** — How to phrase (hardprompts/ templates)
4. **Tools** — What agents can do (tools/ deterministic scripts)

### Through Agent (Multipliers)
5. **Standard Output** — Structured JSON responses (--json flag on all CLI tools)
6. **Types** — Strong typing (dataclasses, Pydantic, DB schemas)
7. **Docs** — Clear instructions (CLAUDE.md, SKILL.md, goals/)
8. **Tests** — Validation (pytest + behave + Playwright + security/compliance gates)
9. **Architecture** — System design (GOTCHA layers, ATLAS/M-ATLAS workflow)
10. **Plans** — Implementation blueprints (specs/, plan files, goals/)
11. **Templates** — Reusable patterns (context/ JSON, hardprompts/, args/ YAML)
12. **Workflows** — Orchestration logic (goals/, CI/CD pipelines, GitLab task routing)

---

## ICDEV Guardrails

- All generated artifacts MUST include classification markings appropriate to impact level (CUI for IL4/IL5, SECRET for IL6)
- Use `classification_manager.py` for all marking generation — do NOT hard-code CUI banners
- Audit trail is append-only — NEVER add UPDATE/DELETE operations to audit tables
- Security gates block on: CAT1 STIG findings, critical/high vulnerabilities, failed tests, missing markings, SbD critical not_satisfied, IV&V critical findings, FedRAMP other_than_satisfied, CMMC not_met, cATO expired evidence
- When implementing a NIST 800-53 control, always call crosswalk engine to auto-populate FedRAMP/CMMC/800-171 status
- Self-healing auto-remediation limited to confidence ≥ 0.7 and max 5/hour
- All A2A communication uses mutual TLS within K8s cluster
- Never store secrets in code or config — use AWS Secrets Manager or K8s secrets
- SBOM must be regenerated on every build
- All containers must run as non-root with read-only root filesystem
- IL6/SECRET projects require SIPR-only network, NSA Type 1 encryption, air-gapped CI/CD

---

### Agentic Application Generation (Phase 19)

ICDEV generates mini-ICDEV clone child applications. Each child app includes:
- Full GOTCHA framework (6 layers) and ATLAS workflow (without fitness step)
- 5 core agents (Orchestrator, Architect, Builder, Knowledge, Monitor)
- Up to 2 ATO agents (Compliance, Security) when compliance/security required
- Full memory system (MEMORY.md, logs, SQLite, semantic search)
- 9 compliance frameworks (when ATO required)
- CI/CD integration (GitHub + GitLab)
- CSP MCP server integration (AWS, GCP, Azure, Oracle)

Child apps CANNOT generate their own child apps (grandchild prevention D52).

**Generation Pipeline:**
1. Fitness Assessment → 2. Blueprint → 3. Scaffold + Generate (12 steps)

**Key Tools:**
| Tool | Purpose |
|------|---------|
| `tools/builder/agentic_fitness.py` | 6-dimension fitness scoring |
| `tools/builder/app_blueprint.py` | Blueprint from scorecard + decisions |
| `tools/builder/child_app_generator.py` | 12-step child app generation |
| `tools/builder/claude_md_generator.py` | Dynamic CLAUDE.md (Jinja2) |
| `tools/builder/goal_adapter.py` | Copy/adapt goals for child apps |
| `tools/builder/db_init_generator.py` | Generate standalone DB init scripts |

**CSP MCP Integration:**
Child apps connect to cloud provider MCP servers based on target cloud:
- AWS: 66+ official MCP servers (core, IaC, Bedrock, monitoring, etc.)
- GCP: Managed endpoints (`https://{service}.googleapis.com/mcp`)
- Azure: Azure MCP server catalog
- Oracle: OCI MCP servers

Registry: `context/agentic/csp_mcp_registry.yaml`

---

## SaaS Multi-Tenancy Architecture (Phase 21)

### Overview
ICDEV is exposed as a multi-tenant SaaS platform. The SaaS layer **wraps** existing tools (D58) — it does NOT rewrite them. Each REST/MCP endpoint resolves the tenant, routes to their isolated database, calls the existing Python tool, and returns the result.

### Tenant Isolation by Impact Level
| IL | Compute | Database | Network |
|----|---------|----------|---------|
| IL2-IL4 | Dedicated K8s namespace | Dedicated PostgreSQL (or SQLite dev) | Network policy isolation |
| IL5 | Dedicated K8s namespace + node pool | Dedicated RDS instance | VPC peering |
| IL6 | Dedicated AWS sub-account (SIPR) | Isolated VPC PostgreSQL | Air-gapped |

### Subscription Tiers
| Feature | Starter | Professional | Enterprise |
|---------|---------|-------------|------------|
| Projects | 5 | 25 | Unlimited |
| Users | 3 | 15 | Unlimited |
| Impact Levels | IL2, IL4 | IL2-IL5 | IL2-IL6 |
| Auth | API key | API key + OAuth | API key + OAuth + CAC/PIV |
| Compute | Shared K8s NS | Dedicated K8s NS | Dedicated AWS account |
| Rate Limit | 60/min | 300/min | Unlimited |
| CLI Ceiling | scripted_intake only | All except container_execution | All 4 capabilities |

### Authentication (3 Methods)
1. **API Key** — `Authorization: Bearer icdev_...` → SHA-256 hash lookup in api_keys table
2. **OAuth 2.0/OIDC** — `Authorization: Bearer eyJ...` → JWT decode, JWKS verification, tenant resolution
3. **CAC/PIV** — `X-Client-Cert-CN` header → CN lookup in users table (nginx/ALB TLS termination)

### API Transport
- **REST API** — `POST/GET /api/v1/*` — standard HTTP JSON for generic clients
- **MCP Streamable HTTP** — `POST/GET/DELETE /mcp/v1/` — JSON-RPC 2.0 via Streamable HTTP (spec 2025-03-26) for Claude Code clients

### Key Components
| Component | File | Purpose |
|-----------|------|---------|
| Platform DB | `tools/saas/platform_db.py` | PostgreSQL/SQLite schema for tenants, users, keys, subscriptions |
| Tenant Manager | `tools/saas/tenant_manager.py` | Tenant CRUD, provisioning lifecycle, DB creation |
| Auth Middleware | `tools/saas/auth/middleware.py` | Extract/validate credentials, set tenant context |
| RBAC | `tools/saas/auth/rbac.py` | Role-based access control (5 roles × 9 categories) |
| API Gateway | `tools/saas/api_gateway.py` | Main Flask app: REST + MCP Streamable HTTP + auth + rate limiting |
| REST API | `tools/saas/rest_api.py` | Flask Blueprint with all v1 endpoints |
| MCP Streamable HTTP | `tools/saas/mcp_http.py` | MCP Streamable HTTP transport (spec 2025-03-26, session-based) |
| Tenant DB Adapter | `tools/saas/tenant_db_adapter.py` | Route tool DB calls to tenant's database |
| Rate Limiter | `tools/saas/rate_limiter.py` | Per-tenant rate limiting by tier |
| DB Compat | `tools/saas/db/db_compat.py` | SQLite ↔ PostgreSQL compatibility layer |
| PG Schema | `tools/saas/db/pg_schema.py` | Full ICDEV schema ported to PostgreSQL DDL |
| Artifact Delivery | `tools/saas/artifacts/delivery_engine.py` | Push artifacts to tenant S3/Git/SFTP |
| Bedrock Proxy | `tools/saas/bedrock/bedrock_proxy.py` | Route LLM calls to BYOK or shared pool |
| License Validator | `tools/saas/licensing/license_validator.py` | RSA-SHA256 offline license validation |
| Tenant Portal | `tools/saas/portal/app.py` | Web dashboard for tenant admin |
| NS Provisioner | `tools/saas/infra/namespace_provisioner.py` | Create per-tenant K8s namespace |

---

## Marketplace — Federated GOTCHA Asset Registry (Phase 22)

### Overview
Customer developer communities share skills, goals, hardprompts, context, args, and compliance extensions through a federated marketplace with mandatory security scanning, compliance validation, and governance enforcement. 100% air-gapped, integrated with Phase 21 SaaS infrastructure.

### Key Commands
```bash
# Publish a skill to tenant-local catalog
python tools/marketplace/publish_pipeline.py --asset-path /path --asset-type skill --tenant-id "tenant-abc" --publisher-user "user@mil" --json

# Search the marketplace
python tools/marketplace/search_engine.py --search "STIG checker" --json

# Check IL compatibility
python tools/marketplace/compatibility_checker.py --asset-id "asset-abc" --consumer-il IL5 --json

# Install an asset
python tools/marketplace/install_manager.py --install --asset-id "asset-abc" --tenant-id "tenant-abc" --json

# Review queue (ISSO/security officer)
python tools/marketplace/review_queue.py --pending --json
python tools/marketplace/review_queue.py --review --review-id "rev-abc" --reviewer-id "isso@mil" --decision approved --rationale "Passed review" --json

# Federation sync
python tools/marketplace/federation_sync.py --status --json
python tools/marketplace/federation_sync.py --promote --tenant-id "tenant-abc" --json
python tools/marketplace/federation_sync.py --pull --tenant-id "tenant-abc" --consumer-il IL5 --json

# Security scanning
python tools/marketplace/asset_scanner.py --asset-id "asset-abc" --version-id "ver-abc" --asset-path /path --json

# Catalog management
python tools/marketplace/catalog_manager.py --list --asset-type skill --json
python tools/marketplace/catalog_manager.py --get --slug "tenant-abc/my-skill" --json

# Provenance
python tools/marketplace/provenance_tracker.py --report --asset-id "asset-abc" --json
```

---

## Continuous Improvement

Every failure strengthens the system: identify what broke → fix the tool → test it → update the goal → next run succeeds automatically.

Be direct. Be reliable. Get shit done.
