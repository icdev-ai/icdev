# ICDEV™ System Architecture Reference

System architecture details for ICDEV™. See [CLAUDE.md](../../CLAUDE.md) for behavioral instructions.

---

## Multi-Agent Architecture (15 Agents, 3 Tiers)

| Tier | Agent | Port | Role |
|------|-------|------|------|
| Core | Orchestrator | 8443 | Task routing, workflow management |
| Core | Architect | 8444 | ANVIL/M-ANVIL A/T phases, system design |
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
| Domain | Gateway | 8458 | Remote command reception from messaging channels (Telegram, Slack, Teams, Mattermost), 8-gate security chain, classification filtering |
| Support | Monitor | 8450 | Log analysis, metrics, alerts, health checks |

Agents communicate via **A2A protocol** (JSON-RPC 2.0 over mutual TLS within K8s). Each publishes an Agent Card at `/.well-known/agent.json`.

---

## MCP Servers (Unified Gateway + 19 individual servers)

**Recommended: Use `icdev-unified` — single server with all 251 tools (D301).**

| Server | Config Key | Tools |
|--------|-----------|-------|
| **icdev-unified** | `.mcp.json` | **All 225 tools from 19 servers + 66 new tools** (lazy-loaded, D301) |
| icdev-core | `.mcp.json` | project_create, project_list, project_status, task_dispatch, agent_status |
| icdev-compliance | `.mcp.json` | ssp_generate, poam_generate, stig_check, sbom_generate, cui_mark, control_map, nist_lookup, cssp_assess, cssp_report, cssp_ir_plan, cssp_evidence, xacta_sync, xacta_export, sbd_assess, sbd_report, ivv_assess, ivv_report, rtm_generate, **crosswalk_query, fedramp_assess, fedramp_report, cmmc_assess, cmmc_report, oscal_generate, emass_sync, cato_monitor, pi_compliance, classification_check, fips199_categorize, fips200_validate, security_categorize, oscal_validate_deep, oscal_convert, oscal_resolve_profile, oscal_catalog_lookup, oscal_detect_tools, omb_m25_21_assess, omb_m26_04_assess, nist_ai_600_1_assess, gao_ai_assess, model_card_generate, system_card_generate, ai_transparency_audit, confabulation_check, ai_inventory_register, fairness_assess** |
| icdev-builder | `.mcp.json` | scaffold, generate_code, write_tests, run_tests, lint, format, dev_profile_create, dev_profile_get, dev_profile_resolve, dev_profile_detect |
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
| icdev-gateway | `.mcp.json` | bind_user, list_bindings, revoke_binding, send_command, gateway_status |
| icdev-innovation | `.mcp.json` | scan_web, score_signals, triage_signals, detect_trends, generate_solution, run_pipeline, get_status, introspect, competitive_scan, standards_check |
| icdev-research | `.mcp.json` | research_create_session, research_run_stage, research_run_pipeline, research_get_status, research_list_sessions, research_get_dossier, research_review_dossier, research_list_verticals, research_get_challenges, research_get_forecasts, research_trigger_fitness |
| icdev-context | `.mcp.json` | fetch_docs, list_sections, get_icdev_metadata, get_project_context, get_agent_context |
| icdev-observability | `.mcp.json` | trace_query, trace_summary, prov_lineage, prov_export, shap_analyze, xai_assess |
| icdev-lsp | `.mcp.json` | lsp_check_servers, lsp_diagnostics, lsp_hover, lsp_find_definition, lsp_verify_loop |

---

## Supported Languages (6 First-Class)

| Language | Scaffold | Lint | Format | SAST | Dep Audit | BDD Steps | Code Gen |
|----------|----------|------|--------|------|-----------|-----------|----------|
| Python | python-backend, api, cli, data-pipeline | flake8/ruff | black+isort | bandit | pip-audit | behave | Flask/FastAPI |
| Java | java-backend | checkstyle/PMD | google-java-format | SpotBugs | OWASP DC | Cucumber-JVM | Spring Boot |
| JavaScript/TS | javascript-frontend, typescript-backend | eslint+tsc | prettier | eslint-security | npm audit | cucumber-js | Express |
| Go | go-backend | golangci-lint | gofmt | gosec | govulncheck | godog | net/http/Gin |
| Rust | rust-backend | clippy | rustfmt | cargo-audit | cargo-audit | cucumber-rs | Actix-web |
| C# | csharp-backend | dotnet analyzers | dotnet format | SecurityCodeScan | dotnet list | SpecFlow | ASP.NET |

Language profiles stored in `context/languages/language_registry.json`. Detection via `tools/builder/language_support.py`.

---

## Claude Code Skills (24 Custom Commands)

| Skill | Purpose |
|-------|---------|
| `/icdev-init` | Initialize new project with compliance scaffolding |
| `/icdev-build` | Build code using true TDD (RED→GREEN→REFACTOR) via M-ANVIL workflow |
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
| `/icdev-query` | NLQ compliance query — natural language to SQL for compliance database queries (Phase 40) |
| `/icdev-worktree` | Git worktree task isolation — create, list, cleanup, status for parallel CI/CD (Phase 41) |
| `/plan_python` | Python build plan — Flask/FastAPI, pytest, behave, bandit, pip-audit, STIG Dockerfile (Phase 42) |
| `/plan_java` | Java build plan — Spring Boot, Cucumber-JVM, checkstyle, SpotBugs, OWASP DC (Phase 42) |
| `/plan_go` | Go build plan — net/http/Gin, godog, golangci-lint, gosec, govulncheck (Phase 42) |
| `/plan_rust` | Rust build plan — Actix-web, cucumber-rs, clippy, cargo-audit, rustfmt (Phase 42) |
| `/plan_csharp` | C# build plan — ASP.NET Core, SpecFlow, SecurityCodeScan, dotnet analyzers (Phase 42) |
| `/plan_typescript` | TypeScript build plan — Express, cucumber-js, eslint-security, npm audit (Phase 42) |
| `/icdev-agentic` | Generate agentic child application (mini-ICDEV™ clone with FORGE/ANVIL) |
| `/icdev-market` | Federated FORGE marketplace — publish, install, search, review, sync assets across tenant orgs |
| `/icdev-devsecops` | DevSecOps profile management, maturity assessment, pipeline security generation, policy-as-code (Kyverno/OPA), attestation |
| `/icdev-zta` | Zero Trust Architecture — 7-pillar maturity scoring, NIST 800-207 assessment, service mesh generation, network segmentation, PDP/PEP config, cATO posture |
| `/icdev-mosa` | DoD MOSA (10 U.S.C. §4401) — MOSA assessment, modularity analysis, ICD/TSP generation, code enforcement, intake auto-detection for DoD/IC |
| `/icdev-innovate` | Innovation Engine — autonomous self-improvement: web scanning, signal scoring, compliance triage, trend detection, solution generation, introspective analysis, competitive intel, standards monitoring |
| `/icdev-research` | Industry Research Engine — deep vertical research: 9-stage pipeline (SCOPE→FORECAST→DOSSIER), 9 source streams (incl. YouTube video), 6-dimension challenge scoring, regulatory mapping, build/buy analysis, cross-engine forecast with surprise predictions, dossier generation, HITL review, child app fitness trigger |
| `/icdev-translate` | Cross-language translation — 5-phase hybrid pipeline (Extract→Type-Check→Translate→Assemble→Validate+Repair), 30 language pairs, pass@k candidates, mock-and-continue, compliance bridge |
| `/icdev-trace` | Observability & XAI — distributed tracing queries, provenance lineage, AgentSHAP tool attribution, XAI compliance assessment (Phase 46) |
| `/audit` | Production readiness audit — 38 checks across 7 categories (platform, security, compliance, integration, performance, documentation, code_quality), streaming results, consolidated report, trend tracking |
| `/remediate` | Auto-fix audit blockers — 3-tier confidence model (auto-fix >= 0.7, suggest 0.3-0.7, escalate < 0.3), verification re-runs, append-only audit trail, chains from `/audit` |
| `/icdev-transparency` | AI transparency workflow — AI inventory, model/system cards, 4 framework assessors, confabulation detection, fairness assessment, GAO evidence, cross-framework audit |
| `/icdev-accountability` | AI Accountability — oversight plans, CAIO designation, appeals, incidents, ethics reviews, reassessment scheduling, cross-framework accountability audit (Phase 49) |

---

## Cross-Platform Compatibility (D145)

```bash
# Platform check (run on first setup — validates OS compatibility)
python tools/testing/platform_check.py               # Human output
python tools/testing/platform_check.py --json         # JSON output

# Platform utilities (import in Python code)
from tools.compat.platform_utils import IS_WINDOWS, IS_MACOS, IS_LINUX
from tools.compat.platform_utils import get_temp_dir, get_npx_cmd, get_home_dir
from tools.compat.platform_utils import ensure_utf8_console
```

---

## Auto-Scaling (D141-D144)

```bash
# Apply HPA + PDB (requires Metrics Server)
kubectl apply -f k8s/hpa.yaml                        # Horizontal Pod Autoscalers (18 components)
kubectl apply -f k8s/pdb.yaml                        # Pod Disruption Budgets (18 components)
kubectl apply -f k8s/node-autoscaler.yaml             # Cluster Autoscaler reference + prerequisites

# Verify scaling
kubectl get hpa -n icdev                              # Check HPA status
kubectl get pdb -n icdev                              # Check PDB status
kubectl top pods -n icdev                             # Check pod resource usage

# Helm with autoscaling enabled
helm install icdev deploy/helm/ --set autoscaling.enabled=true

# Config: args/scaling_config.yaml — profiles, topology, node autoscaler, rate limiter backend
```

---

## Docker & K8s Deployment

- `docker/Dockerfile.agent-base` — STIG-hardened base for all agents (non-root, minimal packages)
- `docker/Dockerfile.dashboard` — STIG-hardened Flask dashboard
- `docker/Dockerfile.mbse-agent` — STIG-hardened MBSE agent (port 8451)
- `docker/Dockerfile.modernization-agent` — STIG-hardened Modernization agent (port 8452)
- `docker/Dockerfile.requirements-analyst-agent` — STIG-hardened Requirements Analyst agent (port 8453)
- `docker/Dockerfile.supply-chain-agent` — STIG-hardened Supply Chain agent (port 8454)
- `docker/Dockerfile.simulation-agent` — STIG-hardened Simulation agent (port 8455)
- `docker/Dockerfile.integration-agent` — STIG-hardened Integration agent (port 8456)
- `docker/Dockerfile.devsecops-agent` — STIG-hardened DevSecOps/ZTA agent (port 8457)
- `docker/Dockerfile.gateway-agent` — STIG-hardened Remote Command Gateway agent (port 8458)
- `docker/Dockerfile.api-gateway` — STIG-hardened SaaS API gateway (port 8443, gunicorn)
- `k8s/` — Full K8s manifests: namespace, configmap, secrets, network policies (default deny), ingress, 16+ deployment+service pairs
- `k8s/hpa.yaml` — HPA manifests for all 15 agents + dashboard + API gateway (3-tier profiles: core/domain/support)
- `k8s/pdb.yaml` — Pod Disruption Budgets (minAvailable/maxUnavailable per tier)
- `k8s/node-autoscaler.yaml` — Cloud-agnostic Cluster Autoscaler reference deployment + prerequisites documentation
- `k8s/devsecops-agent.yaml` — STIG-hardened DevSecOps/ZTA agent (port 8457)
- `k8s/gateway-agent.yaml` — STIG-hardened Remote Command Gateway agent (port 8458)
- `k8s/saas/` — SaaS-specific K8s manifests: tenant-namespace-template, api-gateway-deployment, platform-db-deployment
- `deploy/helm/` — Helm chart for on-prem deployment (Chart.yaml, values.yaml, templates/)
- `deploy/helm/values-ibm.yaml` — IBM Cloud (IC4G) override
- `deploy/helm/values-on-prem.yaml` — On-premises/air-gapped override
- `deploy/helm/values-docker.yaml` — Docker Compose development override
- `deploy/offline/` — Air-gapped installer (install.py, install.sh, README.md)
- All containers: read-only rootfs, drop ALL capabilities, non-root (UID 1000), resource limits enforced

---

## Modular Installation (Phase 33)

ICDEV™ supports modular deployment configured by compliance posture, platform, organizational role, and team size. Not all modules are required — pick what fits your mission.

```bash
# Interactive wizard — guided setup
python tools/installer/installer.py --interactive

# Profile-based — use a pre-built bundle
python tools/installer/installer.py --profile dod_team --compliance fedramp_high,cmmc --platform k8s
python tools/installer/installer.py --profile isv_startup --platform docker
python tools/installer/installer.py --profile healthcare --compliance hipaa,hitrust

# Add features to existing installation
python tools/installer/installer.py --add-module marketplace
python tools/installer/installer.py --add-compliance hipaa
python tools/installer/installer.py --upgrade                   # Show what can be added

# Status and validation
python tools/installer/installer.py --status --json
python tools/installer/module_registry.py --validate
python tools/installer/compliance_configurator.py --list-postures

# Platform artifact generation
python tools/installer/platform_setup.py --generate docker --modules core,llm,builder,dashboard
python tools/installer/platform_setup.py --generate k8s-rbac --modules core,builder
python tools/installer/platform_setup.py --generate env --modules core,llm
python tools/installer/platform_setup.py --generate helm-values --modules core,llm,builder
```

**Deployment Profiles:**

| Profile | Modules | Compliance | Platform | CUI |
|---------|---------|------------|----------|-----|
| ISV Startup | 7 core | None | Docker | No |
| ISV Enterprise | 11 | FedRAMP Moderate | K8s | No |
| SI Consulting | 5 + RICOAS | FedRAMP + CMMC | Docker | Yes |
| SI Enterprise | 14 | FedRAMP High + CMMC + CJIS | K8s | Yes |
| DoD Team | 14 | FedRAMP High + CMMC + FIPS + cATO | K8s | Yes |
| Healthcare | 9 | HIPAA + HITRUST + SOC 2 | K8s | No |
| Financial | 9 | PCI DSS + SOC 2 + ISO 27001 | K8s | No |
| Law Enforcement | 9 | CJIS + FIPS 199/200 | K8s | Yes |
| GovCloud Full | ALL | ALL | K8s | Yes |
| Custom | 3 minimum | User choice | User choice | Configurable |

**Key Config Files:**
- `args/installation_manifest.yaml` — Module definitions, dependencies, DB table groups
- `args/deployment_profiles.yaml` — Profile bundles with platform and compliance defaults

---

## 12 Leverage Points of Agentic Development

These are the 12 dimensions you can tune to improve agent behavior.

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
9. **Architecture** — System design (FORGE layers, ANVIL/M-ANVIL workflow)
10. **Plans** — Implementation blueprints (specs/, plan files, goals/)
11. **Templates** — Reusable patterns (context/ JSON, hardprompts/, args/ YAML)
12. **Workflows** — Orchestration logic (goals/, CI/CD pipelines, GitLab task routing)

---

## Agentic Application Generation (Phase 19)

ICDEV™ generates mini-ICDEV™ clone child applications. Each child app includes:
- Full FORGE framework (6 layers) and ANVIL workflow (without fitness step)
- 5 core agents (Orchestrator, Architect, Builder, Knowledge, Monitor)
- Up to 2 ATO agents (Compliance, Security) when compliance/security required
- Full memory system (MEMORY.md, logs, SQLite, semantic search)
- 9 compliance frameworks (when ATO required)
- CI/CD integration (GitHub + GitLab)
- CSP MCP server integration (AWS, GCP, Azure, Oracle, IBM)

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
- IBM: IC4G MCP servers (IKS, watsonx.ai, Cloud Object Storage, Key Protect)

Registry: `context/agentic/csp_mcp_registry.yaml`
