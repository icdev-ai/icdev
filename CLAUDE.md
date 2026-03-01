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
5. Load project context: `python tools/project/session_context_builder.py --format markdown`
   (If icdev.yaml exists or cwd is a known ICDEV project, outputs project config,
   compliance posture, dev profile, and recommended workflows)

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
- When adding an append-only/immutable DB table, ALWAYS add it to `APPEND_ONLY_TABLES` in `.claude/hooks/pre_tool_use.py`
- When adding a new dashboard page route, ALWAYS add it to the `Pages:` line in `.claude/commands/start.md`
- When taking screenshots with `browser_take_screenshot`, ALWAYS use `playwright/screenshots/<name>.png` as the filename — `--output-dir` only applies to default filenames, explicit filenames are relative to CWD
- In Jinja2 templates, NEVER use `'%%.0f'|format(value)` — it causes TypeError in `render_template_string`. Use `value|round(0)|int` instead
- In Behave step definitions, match step text to tool return signatures — read the function return dict keys before writing Then steps
- When defining SQL CHECK constraints, derive the values from a Python constant (e.g., `ENTITY_TYPES` tuple) using string formatting — never hardcode the same list in both Python and SQL
- Entity types must be added to BOTH the Python constant AND the SQL CHECK constraint (via the constant) — the `db_init_generator.py` pattern comment shows how
- When generating a child application, ALWAYS use the `child_app_generator.py` pipeline and run `gotcha_validator.py --gate` post-generation — GOTCHA compliance is mandatory, manual scaffolding is prohibited

*(Add new guardrails as mistakes happen. Keep under 15 items.)*

---

## ICDEV System — Intelligent Coding Development

ICDEV is a meta-builder that autonomously builds Gov/DoD applications using the GOTCHA framework and ATLAS workflow. It handles the full SDLC with TDD/BDD, NIST 800-53 RMF compliance, and self-healing capabilities.

### Environment Constraints
- **Classification:** CUI // SP-CTI (IL4/IL5), SECRET (IL6) — classification-aware markings via `classification_manager.py`
- **Impact Levels:** IL2 (Public), IL4 (CUI/GovCloud), IL5 (CUI/Dedicated), IL6 (SECRET/SIPR)
- **Cloud:** Multi-cloud via CSP abstraction (D225) — 6 CSPs: AWS GovCloud, Azure Government, GCP Assured Workloads, OCI Government Cloud, IBM Cloud for Government (IC4G), Local (air-gapped). Default: AWS GovCloud (us-gov-west-1). Config: `args/cloud_config.yaml`
- **LLM:** Multi-cloud via LLM router (D228) — Amazon Bedrock, Azure OpenAI, Vertex AI, OCI GenAI, IBM watsonx.ai (D238), Ollama (local). Config: `args/llm_config.yaml`
- **Access:** PyPi + cloud dedicated regions only (no public internet)
- **No local GPU** — all ML inference via cloud LLM providers or Ollama
- **CI/CD:** GitLab, **Orchestration:** K8s/OpenShift/AKS/GKE/OKE, **IaC:** Terraform + Ansible (multi-cloud generators)
- **Monitoring:** ELK + Splunk + Prometheus/Grafana + cloud-native (CloudWatch, Azure Monitor, Cloud Monitoring, OCI Monitoring)
- **Secrets:** CSP-abstracted (AWS Secrets Manager, Azure Key Vault, GCP Secret Manager, OCI Vault, Local .env)

### Multi-Agent Architecture (15 Agents, 3 Tiers)

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
| Domain | Gateway | 8458 | Remote command reception from messaging channels (Telegram, Slack, Teams, Mattermost), 8-gate security chain, classification filtering |
| Support | Monitor | 8450 | Log analysis, metrics, alerts, health checks |

Agents communicate via **A2A protocol** (JSON-RPC 2.0 over mutual TLS within K8s). Each publishes an Agent Card at `/.well-known/agent.json`.

### MCP Servers (Unified Gateway + 18 individual servers)

**Recommended: Use `icdev-unified` — single server with all 241 tools (D301).**

| Server | Config Key | Tools |
|--------|-----------|-------|
| **icdev-unified** | `.mcp.json` | **All 225 tools from 18 servers + 66 new tools** (lazy-loaded, D301) |
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
| icdev-context | `.mcp.json` | fetch_docs, list_sections, get_icdev_metadata, get_project_context, get_agent_context |
| icdev-observability | `.mcp.json` | trace_query, trace_summary, prov_lineage, prov_export, shap_analyze, xai_assess |

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
| MITRE ATLAS v5.4.0 | `atlas_mitigations.json` | `atlas_assessor.py` | `atlas_report_generator.py` |
| OWASP LLM Top 10 | `owasp_llm_top10.json` | `owasp_llm_assessor.py` | via base_assessor |
| NIST AI RMF 1.0 | `nist_ai_rmf.json` | `nist_ai_rmf_assessor.py` | via base_assessor |
| ISO/IEC 42001:2023 | `iso42001_controls.json` | `iso42001_assessor.py` | via base_assessor |
| SAFE-AI (NIST 800-53 AI) | `safeai_controls.json` | via crosswalk | AI-affected control overlay |
| OWASP Agentic AI | `owasp_agentic_threats.json` | `owasp_agentic_assessor.py` | via base_assessor |
| OWASP ASI01-ASI10 | `owasp_agentic_asi.json` | `owasp_asi_assessor.py` | via base_assessor |
| EU AI Act (Annex III) | `eu_ai_act_annex_iii.json` | `eu_ai_act_classifier.py` | via base_assessor (ISO 27001 bridge) |
| XAI (Observability) | `xai_requirements.json` | `xai_assessor.py` | via base_assessor |
| OMB M-25-21 (High-Impact AI) | `omb_m25_21_high_impact_ai.json` | `omb_m25_21_assessor.py` | via base_assessor |
| OMB M-26-04 (Unbiased AI) | `omb_m26_04_unbiased_ai.json` | `omb_m26_04_assessor.py` | via base_assessor |
| NIST AI 600-1 (GenAI Profile) | `nist_ai_600_1_genai.json` | `nist_ai_600_1_assessor.py` | via base_assessor |
| GAO-21-519SP (AI Accountability) | `gao_ai_accountability.json` | `gao_ai_assessor.py` | via base_assessor |

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

### Claude Code Skills (24 Custom Commands)

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
| `/icdev-query` | NLQ compliance query — natural language to SQL for compliance database queries (Phase 40) |
| `/icdev-worktree` | Git worktree task isolation — create, list, cleanup, status for parallel CI/CD (Phase 41) |
| `/plan_python` | Python build plan — Flask/FastAPI, pytest, behave, bandit, pip-audit, STIG Dockerfile (Phase 42) |
| `/plan_java` | Java build plan — Spring Boot, Cucumber-JVM, checkstyle, SpotBugs, OWASP DC (Phase 42) |
| `/plan_go` | Go build plan — net/http/Gin, godog, golangci-lint, gosec, govulncheck (Phase 42) |
| `/plan_rust` | Rust build plan — Actix-web, cucumber-rs, clippy, cargo-audit, rustfmt (Phase 42) |
| `/plan_csharp` | C# build plan — ASP.NET Core, SpecFlow, SecurityCodeScan, dotnet analyzers (Phase 42) |
| `/plan_typescript` | TypeScript build plan — Express, cucumber-js, eslint-security, npm audit (Phase 42) |
| `/icdev-agentic` | Generate agentic child application (mini-ICDEV clone with GOTCHA/ATLAS) |
| `/icdev-market` | Federated GOTCHA marketplace — publish, install, search, review, sync assets across tenant orgs |
| `/icdev-devsecops` | DevSecOps profile management, maturity assessment, pipeline security generation, policy-as-code (Kyverno/OPA), attestation |
| `/icdev-zta` | Zero Trust Architecture — 7-pillar maturity scoring, NIST 800-207 assessment, service mesh generation, network segmentation, PDP/PEP config, cATO posture |
| `/icdev-mosa` | DoD MOSA (10 U.S.C. §4401) — MOSA assessment, modularity analysis, ICD/TSP generation, code enforcement, intake auto-detection for DoD/IC |
| `/icdev-innovate` | Innovation Engine — autonomous self-improvement: web scanning, signal scoring, compliance triage, trend detection, solution generation, introspective analysis, competitive intel, standards monitoring |
| `/icdev-translate` | Cross-language translation — 5-phase hybrid pipeline (Extract→Type-Check→Translate→Assemble→Validate+Repair), 30 language pairs, pass@k candidates, mock-and-continue, compliance bridge |
| `/icdev-trace` | Observability & XAI — distributed tracing queries, provenance lineage, AgentSHAP tool attribution, XAI compliance assessment (Phase 46) |
| `/audit` | Production readiness audit — 38 checks across 7 categories (platform, security, compliance, integration, performance, documentation, code_quality), streaming results, consolidated report, trend tracking |
| `/remediate` | Auto-fix audit blockers — 3-tier confidence model (auto-fix >= 0.7, suggest 0.3-0.7, escalate < 0.3), verification re-runs, append-only audit trail, chains from `/audit` |
| `/icdev-transparency` | AI transparency workflow — AI inventory, model/system cards, 4 framework assessors, confabulation detection, fairness assessment, GAO evidence, cross-framework audit |
| `/icdev-accountability` | AI Accountability — oversight plans, CAIO designation, appeals, incidents, ethics reviews, reassessment scheduling, cross-framework accountability audit (Phase 49) |

### Cross-Platform Compatibility (D145)
```bash
# Platform check (run on first setup — validates OS compatibility)
python tools/testing/platform_check.py               # Human output
python tools/testing/platform_check.py --json         # JSON output

# Platform utilities (import in Python code)
from tools.compat.platform_utils import IS_WINDOWS, IS_MACOS, IS_LINUX
from tools.compat.platform_utils import get_temp_dir, get_npx_cmd, get_home_dir
from tools.compat.platform_utils import ensure_utf8_console
```

### Auto-Scaling (D141-D144)
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

### Testing Framework (Adapted from ADW)
```bash
# ICDEV platform tests (D155 — 21 test files, ~330+ tests)
pytest tests/ -v --tb=short                          # Run all platform tests
pytest tests/test_circuit_breaker.py -v              # Circuit breaker tests
pytest tests/test_retry.py -v                        # Retry utility tests
pytest tests/test_correlation.py -v                  # Correlation ID tests
pytest tests/test_errors.py -v                       # Error hierarchy tests
pytest tests/test_migration_runner.py -v             # Migration runner tests
pytest tests/test_backup_manager.py -v               # Backup/restore tests
pytest tests/test_openapi_spec.py -v                 # OpenAPI spec tests
pytest tests/test_metrics.py -v                      # Prometheus metrics tests
pytest tests/test_rest_api.py -v                     # REST API endpoint tests
pytest tests/test_swagger_ui.py -v                   # Swagger UI tests
pytest tests/test_audit_logger.py -v                 # Audit logger tests
pytest tests/test_init_icdev_db.py -v                # DB init tests
pytest tests/test_platform_db.py -v                  # Platform DB tests
pytest tests/test_readiness_scorer.py -v             # Readiness scorer tests
pytest tests/test_dev_profile_manager.py -v          # Dev profile manager tests (33 tests)
pytest tests/test_manifest_loader.py -v              # Manifest loader tests (32 tests)
pytest tests/test_session_context_builder.py -v      # Session context builder tests (26 tests)
pytest tests/test_pipeline_config_generator.py -v    # Pipeline config generator tests (14 tests)
pytest tests/test_icdev_client.py -v                 # SDK client tests (12 tests)
pytest tests/test_tool_detector.py -v                # AI tool detector tests (10 tests)
pytest tests/test_instruction_generator.py -v        # Instruction generator tests (14 tests)
pytest tests/test_mcp_config_generator.py -v         # MCP config generator tests (8 tests)
pytest tests/test_skill_translator.py -v             # Skill translator tests (10 tests)
pytest tests/test_companion.py -v                    # Companion orchestrator tests (7 tests)
pytest tests/test_prompt_injection_detector.py -v    # Prompt injection detector tests (47 tests)
pytest tests/test_ai_telemetry.py -v                 # AI telemetry logger tests (12 tests)
pytest tests/test_cloud_providers.py -v              # Cloud provider abstraction tests (20 tests)
pytest tests/test_atlas_assessor.py -v               # ATLAS assessor tests (15 tests)
pytest tests/test_multi_cloud_llm.py -v              # Multi-cloud LLM provider tests (12 tests)
pytest tests/test_child_registry.py -v               # Child registry + telemetry tests (18 tests)
pytest tests/test_evolutionary_intelligence.py -v    # Genome, evaluation, staging, propagation tests (25 tests)
pytest tests/test_genome_evolution.py -v             # Absorption, learning, cross-pollination tests (20 tests)
pytest tests/test_atlas_red_team.py -v               # ATLAS red teaming scanner tests (10 tests)
pytest tests/test_ai_bom_generator.py -v             # AI BOM generator tests (14 tests)
pytest tests/test_phase36_phase37_integration.py -v  # Phase 36↔37 security integration tests (17 tests)
pytest tests/test_cloud_monitoring_iam.py -v         # Cloud monitoring/IAM/registry tests (15 tests)
pytest tests/test_ibm_providers.py -v                # IBM Cloud provider tests (44 tests)
pytest tests/test_region_validator.py -v             # CSP region validator tests (18 tests)
pytest tests/test_translation_manager.py -v          # Translation pipeline tests (35 tests)
pytest tests/test_dependency_mapper.py -v            # Dependency mapper tests (16 tests)
pytest tests/test_source_extractor.py -v             # Source extractor tests (22 tests)
pytest tests/test_behavioral_drift.py -v             # Behavioral drift detection tests (14 tests)
pytest tests/test_tool_chain_validator.py -v          # Tool chain validator tests (22 tests)
pytest tests/test_agent_output_validator.py -v        # Agent output validator tests (22 tests)
pytest tests/test_agent_trust_scorer.py -v            # Agent trust scorer tests (22 tests)
pytest tests/test_mcp_tool_authorizer.py -v           # MCP tool authorizer tests (28 tests)
pytest tests/test_behavioral_red_team.py -v           # Behavioral red teaming tests (13 tests)
pytest tests/test_owasp_agentic_assessor.py -v        # OWASP Agentic assessor tests (16 tests)
pytest tests/test_schemas.py -v                      # Shared schema enforcement tests (29 tests)
pytest tests/test_state_tracker.py -v                # Dirty-tracking state push tests (16 tests)
pytest tests/test_extension_manager.py -v            # Active extension hooks tests (18 tests)
pytest tests/test_chat_manager.py -v                 # Multi-stream chat + intervention tests (22 tests)
pytest tests/test_history_compressor.py -v           # 3-tier history compression tests (25 tests)
pytest tests/test_memory_consolidation.py -v         # AI-driven memory consolidation tests (22 tests)
pytest tests/test_context_server.py -v               # Semantic layer MCP tools tests (20 tests)
pytest tests/test_code_pattern_scanner.py -v         # Dangerous pattern detection tests (30 tests)
pytest tests/test_register_external_patterns.py -v   # Innovation signal registration tests (15 tests)
pytest tests/test_claude_dir_validator.py -v         # .claude directory governance validator tests (50 tests)
pytest tests/test_tracer.py -v                        # Tracer ABC + SQLiteTracer tests (43 tests)
pytest tests/test_trace_context.py -v                 # W3C traceparent + context propagation tests (30 tests)
pytest tests/test_mcp_instrumentation.py -v           # MCP auto-instrumentation tests (8 tests)
pytest tests/test_a2a_trace_propagation.py -v         # A2A distributed tracing tests (10 tests)
pytest tests/test_otel_tracer.py -v                   # OTelTracer + OTelSpan mock tests (17 tests)
pytest tests/test_prov_recorder.py -v                 # Provenance recorder tests (30 tests)
pytest tests/test_agent_shap.py -v                    # AgentSHAP Shapley value tests (20 tests)
pytest tests/test_xai_assessor.py -v                  # XAI compliance assessor tests (34 tests)
pytest tests/test_unified_server.py -v                 # Unified MCP gateway tests (42 tests)
pytest tests/test_oscal_tools.py -v                    # OSCAL ecosystem tools tests (40 tests)
pytest tests/test_omb_m25_21_assessor.py -v              # OMB M-25-21 assessor tests
pytest tests/test_omb_m26_04_assessor.py -v              # OMB M-26-04 assessor tests
pytest tests/test_nist_ai_600_1_assessor.py -v           # NIST AI 600-1 assessor tests
pytest tests/test_gao_ai_assessor.py -v                  # GAO AI assessor tests
pytest tests/test_model_card_generator.py -v             # Model card generator tests
pytest tests/test_ai_transparency.py -v                  # AI transparency integration tests
pytest tests/test_accountability_manager.py -v          # Accountability manager tests (25 tests)
pytest tests/test_ai_impact_assessor.py -v              # AI impact assessor tests (13 tests)
pytest tests/test_ai_incident_response.py -v            # AI incident response tests (19 tests)
pytest tests/test_ai_reassessment_scheduler.py -v       # AI reassessment scheduler tests (18 tests)
pytest tests/test_ai_accountability_audit.py -v         # AI accountability audit tests (20 tests)
pytest tests/test_assessor_accountability_fixes.py -v   # Assessor accountability fixes tests (24 tests)
pytest tests/test_ai_governance_intake.py -v            # AI governance intake detection tests (37 tests)
pytest tests/test_ai_governance_chat_extension.py -v    # AI governance chat extension tests (28 tests)
pytest tests/test_code_analyzer.py -v                   # Code analyzer AST self-analysis tests (29 tests)
pytest tests/test_runtime_feedback.py -v                # Runtime feedback collector tests (22 tests)

# .claude directory governance
python tools/testing/claude_dir_validator.py --json   # Validate .claude config alignment (exit 0 = pass)
python tools/testing/claude_dir_validator.py --human   # Human-readable terminal output
python tools/testing/claude_dir_validator.py --check append-only --json  # Single check

# Health check
python tools/testing/health_check.py                 # Full system health check
python tools/testing/health_check.py --json           # JSON output

# Production readiness audit (38 checks, 7 categories)
python tools/testing/production_audit.py --human --stream              # Full audit with streaming
python tools/testing/production_audit.py --json                        # JSON output
python tools/testing/production_audit.py --category security --json    # Single category
python tools/testing/production_audit.py --category security,compliance --json  # Multiple categories
python tools/testing/production_audit.py --gate --json                 # Gate evaluation (exit code 0=pass, 1=fail)
pytest tests/test_production_audit.py -v             # Production audit tests (25 tests)

# Production remediation (auto-fix audit blockers)
python tools/testing/production_remediate.py --human --stream              # Auto-fix + stream
python tools/testing/production_remediate.py --auto --json                 # Auto-fix all (JSON)
python tools/testing/production_remediate.py --dry-run --human --stream    # Preview fixes
python tools/testing/production_remediate.py --check-id SEC-002 --auto     # Single check
python tools/testing/production_remediate.py --skip-audit --auto --json    # Reuse latest audit
pytest tests/test_production_remediate.py -v          # Remediation tests (25 tests)

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

**Testing Architecture (9-step pipeline, adapted from ADW test.md):**
1. **py_compile** — Python syntax validation (catches missing colons, bad indentation before tests run)
2. **Ruff** (`ruff>=0.12`) — Ultra-fast Python linter (replaces flake8+isort+black, written in Rust)
3. **pytest** (tests/) — Unit/integration tests with coverage
4. **behave/Gherkin** (features/) — BDD scenario tests for business requirements
5. **Bandit** — SAST security scan (SQL injection, XSS, hardcoded secrets)
6. **Playwright MCP** (.claude/commands/e2e/*.md) — Browser automation E2E tests
7. **Vision validation** (optional) — LLM-based screenshot analysis (CUI banners, error detection, content verification)
8. **Acceptance validation** (V&V) — Deterministic acceptance criteria verification: maps plan criteria to test evidence, checks rendered pages for error patterns (per `acceptance_validation` gate in `security_gates.yaml`)
9. **Security + Compliance gates** — CUI markings, STIG (0 CAT1), secret detection

**Claude Code test commands** (in .claude/commands/):
- `/test` — Full application validation suite (syntax + quality + unit + BDD + security)
- `/test_e2e` — Execute E2E test via Playwright MCP with screenshots + CUI verification
- `/resolve_failed_test` — Fix a specific failing test (minimal, targeted fix)
- `/resolve_failed_e2e_test` — Fix a specific failing E2E test

**Key patterns from ADW:** parse_json (markdown-wrapped JSON), Pydantic data types (TestResult, E2ETestResult), dual logging (file+console), safe subprocess env, retry with resolution (max 4 unit / max 2 E2E), fail-fast E2E, stdin=DEVNULL for Claude Code subprocesses

### Modular Installation (Phase 33)

ICDEV supports modular deployment configured by compliance posture, platform, organizational role, and team size. Not all modules are required — pick what fits your mission.

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

### ICDEV Commands
```bash
# Database
python tools/db/init_icdev_db.py                    # Initialize ICDEV database (210 tables)

# Database Migrations (D150)
python tools/db/migrate.py --status [--json]                      # Show migration status
python tools/db/migrate.py --up [--target 005] [--dry-run]        # Apply pending migrations
python tools/db/migrate.py --down [--target 003]                  # Roll back migrations
python tools/db/migrate.py --validate [--json]                    # Validate checksums
python tools/db/migrate.py --create "add_feature_table"           # Scaffold new migration
python tools/db/migrate.py --mark-applied 001                    # Mark existing DB as migrated
python tools/db/migrate.py --up --all-tenants                    # Apply to all tenant DBs

# Database Backup/Restore (D152)
python tools/db/backup.py --backup [--db icdev] [--json]         # Backup single database
python tools/db/backup.py --backup --all [--json]                # Backup all databases
python tools/db/backup.py --backup --tenants [--slug acme]       # Backup tenant databases
python tools/db/backup.py --restore --backup-file path/to/bak    # Restore from backup
python tools/db/backup.py --verify --backup-file path/to/bak     # Verify backup integrity
python tools/db/backup.py --list [--json]                        # List available backups
python tools/db/backup.py --prune [--retention-days 30]          # Remove old backups

# Audit trail (append-only, NIST AU compliant)
python tools/audit/audit_logger.py --event-type "code.commit" --actor "builder-agent" --action "Committed module X" --project-id "proj-123"
python tools/audit/audit_query.py --project "proj-123" --format json
python tools/audit/decision_recorder.py --project-id "proj-123" --decision "Use PostgreSQL" --rationale "RDS requirement" --actor "architect-agent"

# MCP servers (stdio transport)
python tools/mcp/unified_server.py                   # Start unified MCP gateway (241 tools, recommended)
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

# Spec-Kit Patterns (D156-D161)
python tools/requirements/spec_quality_checker.py --spec-file specs/3-dashboard-kanban/spec.md --json                                                   # Quality check
python tools/requirements/spec_quality_checker.py --spec-file specs/foo.md --annotate --output specs/foo.annotated.md                                    # Annotate with [NEEDS CLARIFICATION]
python tools/requirements/spec_quality_checker.py --spec-dir specs/ --json                                                                               # Batch check all specs
python tools/requirements/consistency_analyzer.py --spec-file specs/3-dashboard-kanban/spec.md --json                                                    # Cross-artifact consistency
python tools/requirements/consistency_analyzer.py --spec-dir specs/ --json                                                                                # Batch consistency check
python tools/requirements/constitution_manager.py --project-id "proj-123" --load-defaults --json                                                         # Load DoD default principles
python tools/requirements/constitution_manager.py --project-id "proj-123" --list --json                                                                  # List project principles
python tools/requirements/constitution_manager.py --project-id "proj-123" --validate --spec-file specs/foo.md --json                                     # Validate spec vs constitution
python tools/requirements/clarification_engine.py --spec-file specs/foo.md --max-questions 5 --json                                                      # Prioritized clarification questions
python tools/requirements/clarification_engine.py --session-id "<id>" --max-questions 5 --json                                                           # Session-based clarification
python tools/requirements/spec_organizer.py --init --issue 3 --slug "dashboard-kanban" --json                                                            # Init spec directory
python tools/requirements/spec_organizer.py --migrate --spec-file specs/issue-3-foo.md --json                                                            # Migrate flat spec to directory
python tools/requirements/spec_organizer.py --migrate-all --json                                                                                          # Migrate all flat specs
python tools/requirements/spec_organizer.py --list --json                                                                                                 # List all spec directories
python tools/requirements/spec_organizer.py --register --spec-dir specs/3-dashboard-kanban/ --project-id "proj-123" --json                               # Register spec in DB
python tools/requirements/decomposition_engine.py --session-id "<id>" --annotate-parallel --json                                                          # Detect parallel task groups

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

# Observability & Agent Execution (Phase 39)
python tools/agent/agent_executor.py --prompt "echo hello" --model sonnet --json           # Execute agent via CLI
python tools/agent/agent_executor.py --prompt "fix tests" --model opus --max-retries 3     # With retry logic

# NLQ Compliance Queries (Phase 40)
# Start dashboard first: python tools/dashboard/app.py
# Navigate to /query for natural language compliance queries
# Navigate to /events for real-time event timeline (SSE)

# Git Worktree Parallel CI/CD (Phase 41)
python tools/ci/modules/worktree.py --create --task-id test-123 --target-dir src/ --json    # Create worktree
python tools/ci/modules/worktree.py --list --json                                            # List worktrees
python tools/ci/modules/worktree.py --cleanup --worktree-name icdev-test-123                # Cleanup worktree
python tools/ci/modules/worktree.py --status --worktree-name icdev-test-123                 # Worktree status

# GitLab Task Board Monitor (Phase 41)
python tools/ci/triggers/gitlab_task_monitor.py                    # Start monitor (polls every 20s)
python tools/ci/triggers/gitlab_task_monitor.py --dry-run          # Preview without spawning
python tools/ci/triggers/gitlab_task_monitor.py --once             # Single poll and exit

# Project management
python tools/project/project_create.py --name "my-app" --type microservice
python tools/project/project_list.py
python tools/project/project_status.py --project-id "proj-123"

# Three-Tier DX (D189-D193)
python tools/project/manifest_loader.py --dir /path --json                                          # Parse + validate icdev.yaml
python tools/project/manifest_loader.py --file /path/icdev.yaml --validate                          # Validate manifest
python tools/project/validate_manifest.py --file icdev.yaml --json                                  # Thin validate CLI
python tools/project/session_context_builder.py --format markdown                                   # Build session context (Tier 2)
python tools/project/session_context_builder.py --json                                              # JSON output
python tools/project/session_context_builder.py --init --json                                       # Register project from icdev.yaml
python tools/ci/pipeline_config_generator.py --dir /path --platform auto --dry-run --json           # Preview CI/CD config (Tier 1)
python tools/ci/pipeline_config_generator.py --dir /path --platform github --write                  # Generate GitHub Actions
python tools/ci/pipeline_config_generator.py --dir /path --platform gitlab --write                  # Generate GitLab CI

# SDK (Tier 3 — Python client wrapping CLI tools, D191)
# from tools.sdk.icdev_client import ICDEVClient
# client = ICDEVClient(project_id="proj-123", project_dir="/path")
# client.project_status()       # Calls project_status.py --json via subprocess
# client.generate_ssp()         # Calls ssp_generator.py --json
# client.check_stig()           # Calls stig_checker.py --json
# client.build_context()        # Calls session_context_builder.py --json
# client.generate_pipeline()    # Calls pipeline_config_generator.py --json

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
python tools/compliance/oscal_generator.py --project-id "proj-123" --deep-validate /path/to/ssp.oscal.json --json  # Deep validation (D302-D305)

# OSCAL Ecosystem Tools (D302-D306)
python tools/compliance/oscal_tools.py --detect --json                                  # Check oscal-cli, oscal-pydantic, NIST catalog availability
python tools/compliance/oscal_tools.py --validate /path/to/ssp.oscal.json --json        # 3-layer deep validation
python tools/compliance/oscal_tools.py --convert /path/to/ssp.json --output-format xml  # Format conversion (requires oscal-cli)
python tools/compliance/oscal_tools.py --resolve-profile /path/to/profile.json --json   # Profile resolution (requires oscal-cli)
python tools/compliance/oscal_tools.py --catalog-lookup AC-2 --json                     # Look up control from NIST catalog
python tools/compliance/oscal_tools.py --catalog-list --family AC --json                # List controls by family
python tools/compliance/oscal_tools.py --catalog-stats --json                           # Catalog statistics
python tools/compliance/oscal_catalog_adapter.py --lookup AC-2 --json                   # Direct catalog adapter CLI
python tools/compliance/oscal_catalog_adapter.py --stats --json                         # Catalog source info

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

# Dev Profiles & Personalization (Phase 34)
python tools/builder/dev_profile_manager.py --scope tenant --scope-id "tenant-abc" --create --template dod_baseline --json       # Create from template
python tools/builder/dev_profile_manager.py --scope tenant --scope-id "tenant-abc" --create --data '{"language":{"primary":"go"}}' --created-by "admin" --json  # Create explicit
python tools/builder/dev_profile_manager.py --scope tenant --scope-id "tenant-abc" --get --json                                  # Get current profile
python tools/builder/dev_profile_manager.py --scope tenant --scope-id "tenant-abc" --get --version 2 --json                     # Get specific version
python tools/builder/dev_profile_manager.py --scope project --scope-id "proj-123" --resolve --json                               # Resolve 5-layer cascade
python tools/builder/dev_profile_manager.py --scope tenant --scope-id "tenant-abc" --update --changes '{"style":{"line_length":120}}' --change-summary "Update line length" --updated-by "admin" --json  # Update (new version)
python tools/builder/dev_profile_manager.py --scope tenant --scope-id "tenant-abc" --lock --dimension-path "security" --lock-role isso --locked-by "isso@mil" --json   # Lock dimension
python tools/builder/dev_profile_manager.py --scope tenant --scope-id "tenant-abc" --unlock --dimension-path "security" --unlocked-by "isso@mil" --role isso --json    # Unlock dimension
python tools/builder/dev_profile_manager.py --scope tenant --scope-id "tenant-abc" --diff --v1 1 --v2 3 --json                   # Diff versions
python tools/builder/dev_profile_manager.py --scope tenant --scope-id "tenant-abc" --rollback --target-version 1 --rolled-back-by "admin" --json  # Rollback (creates new version)
python tools/builder/dev_profile_manager.py --scope project --scope-id "proj-123" --inject --task-type code_generation --json    # LLM injection context
python tools/builder/dev_profile_manager.py --scope tenant --scope-id "tenant-abc" --history --json                              # Version history
python tools/builder/profile_detector.py --repo-path /path/to/repo --json                    # Auto-detect from repo
python tools/builder/profile_detector.py --text "We use Go, snake_case, 120-char lines" --json  # Detect from text
python tools/builder/profile_md_generator.py --scope project --scope-id "proj-123" --json     # Generate PROFILE.md
python tools/builder/profile_md_generator.py --scope project --scope-id "proj-123" --output /path/PROFILE.md --store  # Generate + store in DB

# Universal AI Coding Companion (D194-D198)
python tools/dx/companion.py --setup --write                              # Auto-detect tools + generate all configs
python tools/dx/companion.py --setup --all --write                        # All 10 platforms
python tools/dx/companion.py --setup --platforms codex,cursor --write     # Specific platforms
python tools/dx/companion.py --detect --json                              # Detect installed AI tools
python tools/dx/companion.py --sync --write                               # Regenerate after changes
python tools/dx/companion.py --list --json                                # List all supported platforms
python tools/dx/tool_detector.py --json                                   # Detect AI tools (env, config dirs, files)
python tools/dx/instruction_generator.py --all --write --json             # Generate instruction files
python tools/dx/mcp_config_generator.py --all --write --json              # Generate MCP configs from .mcp.json
python tools/dx/skill_translator.py --all --write --json                  # Translate skills to all platforms
python tools/dx/skill_translator.py --list                                # List available Claude Code skills

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

# AI Security (Phase 37)
python tools/security/prompt_injection_detector.py --text "ignore previous instructions" --json      # Detect prompt injection
python tools/security/prompt_injection_detector.py --file /path/to/file --json                       # Scan file for injections
python tools/security/prompt_injection_detector.py --project-dir /path --gate --json                 # Gate evaluation
python tools/security/ai_telemetry_logger.py --summary --json                                        # AI usage summary
python tools/security/ai_telemetry_logger.py --anomalies --window-hours 24 --json                   # Anomaly detection
python tools/security/atlas_red_team.py --project-id "proj-123" --json                              # Run all ATLAS red team tests (opt-in)
python tools/security/atlas_red_team.py --project-id "proj-123" --technique AML.T0051 --json        # Test specific technique
python tools/compliance/atlas_assessor.py --project-id "proj-123" --json                             # ATLAS compliance assessment
python tools/compliance/atlas_report_generator.py --project-id "proj-123" --json                     # Generate ATLAS compliance report
python tools/compliance/atlas_report_generator.py --project-id "proj-123" --output-path report.md    # Save ATLAS report to file
python tools/security/ai_bom_generator.py --project-id "proj-123" --project-dir . --json             # Generate AI Bill of Materials
python tools/security/ai_bom_generator.py --project-id "proj-123" --gate                             # AI BOM gate check
python tools/compliance/owasp_llm_assessor.py --project-id "proj-123" --json                         # OWASP LLM Top 10 assessment
python tools/compliance/nist_ai_rmf_assessor.py --project-id "proj-123" --json                       # NIST AI RMF assessment
python tools/compliance/iso42001_assessor.py --project-id "proj-123" --json                          # ISO 42001 assessment

# Evolutionary Intelligence (Phase 36)
python tools/registry/child_registry.py --register --name "ChildApp" --type microservice --json      # Register child app
python tools/registry/child_registry.py --list --json                                                 # List children
python tools/registry/genome_manager.py --get --json                                                  # Get current genome version
python tools/registry/genome_manager.py --history --json                                              # Genome version history
python tools/registry/capability_evaluator.py --evaluate --data '{}' --json                           # Evaluate capability
python tools/registry/staging_manager.py --list --json                                                # List staging environments
python tools/registry/propagation_manager.py --list --json                                            # List propagations
python tools/registry/absorption_engine.py --candidates --json                                        # Get absorption candidates
python tools/registry/learning_collector.py --unevaluated --json                                      # Get unevaluated behaviors
python tools/registry/cross_pollinator.py --candidates --json                                         # Find cross-pollination candidates

# Cloud-Agnostic Architecture (Phase 38)
# Cloud Mode Manager (D232)
python tools/cloud/cloud_mode_manager.py --status --json                                               # Current cloud mode and config
python tools/cloud/cloud_mode_manager.py --validate --json                                             # Validate mode against constraints
python tools/cloud/cloud_mode_manager.py --eligible --json                                             # List eligible modes for config
python tools/cloud/cloud_mode_manager.py --check-readiness --json                                      # Check cloud service readiness
# CSP Provider Factory
python -c "from tools.cloud.provider_factory import CSPProviderFactory; f = CSPProviderFactory(); print(f.health_check())"
# CSP Health Check
python tools/cloud/csp_health_checker.py --check --json                                               # Check all CSP services

# CSP Service Monitor (Phase 38 — D239-D241)
python tools/cloud/csp_monitor.py --scan --all --json                                                  # Scan all CSPs for service updates
python tools/cloud/csp_monitor.py --scan --csp aws --json                                              # Scan specific CSP
python tools/cloud/csp_monitor.py --diff --json                                                         # Diff registry vs recent signals (offline)
python tools/cloud/csp_monitor.py --status --json                                                       # Monitor status
python tools/cloud/csp_monitor.py --update-registry --signal-id "sig-xxx" --json                        # Apply signal to registry
python tools/cloud/csp_monitor.py --changelog --days 30 --json                                          # Quick changelog
python tools/cloud/csp_monitor.py --daemon --json                                                       # Continuous monitoring
python tools/cloud/csp_changelog.py --generate --days 30 --json                                         # Full changelog with recommendations
python tools/cloud/csp_changelog.py --generate --format markdown --output .tmp/csp_changelogs/           # Markdown report
python tools/cloud/csp_changelog.py --summary --json                                                    # Summary statistics
# Config: args/csp_monitor_config.yaml — CSP sources, signals, diff engine, scheduling
# Registry: context/cloud/csp_service_registry.json — baseline CSP service catalog (45+ services)

# Region Validation (D234)
python tools/cloud/region_validator.py validate --csp aws --region us-gov-west-1 --frameworks fedramp_high,cjis --json
python tools/cloud/region_validator.py eligible --csp azure --frameworks hipaa --json
python tools/cloud/region_validator.py deployment-check --csp aws --region us-gov-west-1 --impact-level IL5 --frameworks hipaa --json
python tools/cloud/region_validator.py list --json
python tools/cloud/region_validator.py list --csp aws --json

# Multi-Cloud Terraform (dispatches to CSP-specific generator)
python tools/infra/terraform_generator.py --project-id "proj-123" --csp azure                         # Generate Azure IaC
python tools/infra/terraform_generator.py --project-id "proj-123" --csp gcp                           # Generate GCP IaC
python tools/infra/terraform_generator.py --project-id "proj-123" --csp oci                           # Generate OCI IaC
# IBM Cloud Terraform (D237)
python tools/infra/terraform_generator_ibm.py --project-id "proj-123" --region us-south --json
# On-Premises Terraform (D236)
python tools/infra/terraform_generator_onprem.py --project-id "proj-123" --target k8s --json
python tools/infra/terraform_generator_onprem.py --project-id "proj-123" --target docker --json
# Multi-Cloud LLM Providers
# Config: args/llm_config.yaml — azure_openai, vertex_ai, oci_genai, ibm_watsonx provider entries
# Config: args/cloud_config.yaml — CSP selection, cloud_mode, per-service overrides, impact level

# Cross-Language Translation (Phase 43)
python tools/translation/translation_manager.py \
  --source-path /path/to/source --source-language python --target-language java \
  --output-dir /path/to/output --project-id "proj-123" --validate --json       # Full pipeline
python tools/translation/translation_manager.py \
  --source-path /path --source-language python --target-language java \
  --output-dir /path --project-id "proj-123" --dry-run --json                   # Dry run (no LLM)
python tools/translation/source_extractor.py \
  --source-path /path --language python --output-ir ir.json --project-id "proj-123" --json  # Extract IR only
python tools/translation/code_translator.py \
  --ir-file ir.json --source-language python --target-language go \
  --output-dir /path --candidates 3 --json                                      # Translate with pass@k
python tools/translation/dependency_mapper.py \
  --source-language python --target-language go --imports "flask,requests" --json # Dependency lookup
python tools/translation/test_translator.py \
  --source-test-dir /path/tests --source-language python --target-language java \
  --output-dir /path/output/tests --ir-file ir.json --json                      # Translate tests

# OWASP Agentic AI Security (Phase 45)
python tools/security/ai_telemetry_logger.py --drift --json                                          # Behavioral drift detection
python tools/security/ai_telemetry_logger.py --drift --agent-id "builder-agent" --json               # Drift for specific agent
python tools/security/tool_chain_validator.py --rules --json                                          # List tool chain rules
python tools/security/tool_chain_validator.py --gate --project-id "proj-123" --json                   # Tool chain gate check
python tools/security/agent_output_validator.py --text "some output" --json                           # Validate output text
python tools/security/agent_output_validator.py --gate --project-id "proj-123" --json                 # Output validation gate
python tools/security/agent_trust_scorer.py --score --agent-id "builder-agent" --json                 # Compute trust score
python tools/security/agent_trust_scorer.py --check --agent-id "builder-agent" --json                 # Check agent access
python tools/security/agent_trust_scorer.py --all --json                                              # All agent trust scores
python tools/security/agent_trust_scorer.py --gate --project-id "proj-123" --json                     # Trust scoring gate
python tools/security/mcp_tool_authorizer.py --check --role developer --tool scaffold --json          # Check tool authorization
python tools/security/mcp_tool_authorizer.py --list --role pm --json                                  # List role permissions
python tools/security/mcp_tool_authorizer.py --validate --json                                        # Validate RBAC config
python tools/security/atlas_red_team.py --behavioral --json                                           # Run behavioral red team tests
python tools/security/atlas_red_team.py --behavioral --brt-technique BRT-001 --json                   # Test specific technique
python tools/compliance/owasp_agentic_assessor.py --project-id "proj-123" --json                      # OWASP Agentic assessment
python tools/compliance/owasp_agentic_assessor.py --project-id "proj-123" --gate                      # OWASP Agentic gate

# AI Transparency & Accountability (Phase 48)
python tools/compliance/ai_inventory_manager.py --project-id "proj-123" --register --name "Claude Sonnet" --json
python tools/compliance/ai_inventory_manager.py --project-id "proj-123" --list --json
python tools/compliance/ai_inventory_manager.py --project-id "proj-123" --export --json
python tools/compliance/model_card_generator.py --project-id "proj-123" --model-name "claude-sonnet" --json
python tools/compliance/system_card_generator.py --project-id "proj-123" --json
python tools/compliance/fairness_assessor.py --project-id "proj-123" --json
python tools/compliance/fairness_assessor.py --project-id "proj-123" --gate
python tools/security/confabulation_detector.py --project-id "proj-123" --check-output "text" --json
python tools/security/confabulation_detector.py --project-id "proj-123" --summary --json
python tools/compliance/gao_evidence_builder.py --project-id "proj-123" --json
python tools/compliance/ai_transparency_audit.py --project-id "proj-123" --json
python tools/compliance/ai_transparency_audit.py --project-id "proj-123" --human
python tools/compliance/omb_m25_21_assessor.py --project-id "proj-123" --json
python tools/compliance/omb_m26_04_assessor.py --project-id "proj-123" --json
python tools/compliance/nist_ai_600_1_assessor.py --project-id "proj-123" --json
python tools/compliance/gao_ai_assessor.py --project-id "proj-123" --json

# AI Accountability (Phase 49)
python tools/compliance/accountability_manager.py --project-id "proj-123" --summary --json                    # Accountability summary
python tools/compliance/accountability_manager.py --project-id "proj-123" --register-oversight --plan-name "Human Oversight Plan" --json   # Register plan
python tools/compliance/accountability_manager.py --project-id "proj-123" --designate-caio --name "Jane Smith" --role CAIO --json           # Designate CAIO
python tools/compliance/accountability_manager.py --project-id "proj-123" --file-appeal --appellant "John Doe" --ai-system "System" --json  # File appeal
python tools/compliance/accountability_manager.py --project-id "proj-123" --submit-ethics-review --review-type bias_testing_policy --json   # Ethics review
python tools/compliance/ai_impact_assessor.py --project-id "proj-123" --ai-system "System" --json             # Impact assessment
python tools/compliance/ai_impact_assessor.py --project-id "proj-123" --summary --json                        # Impact summary
python tools/compliance/ai_incident_response.py --project-id "proj-123" --log --type bias_detected --severity high --description "Bias found" --json   # Log incident
python tools/compliance/ai_incident_response.py --project-id "proj-123" --stats --json                        # Incident stats
python tools/compliance/ai_reassessment_scheduler.py --project-id "proj-123" --create --ai-system "System" --frequency annual --json   # Schedule reassessment
python tools/compliance/ai_reassessment_scheduler.py --project-id "proj-123" --overdue --json                 # Check overdue
python tools/compliance/ai_accountability_audit.py --project-id "proj-123" --json                              # Accountability audit

# Code Intelligence (Phase 52 — D331-D337)
python tools/analysis/code_analyzer.py --project-dir tools/ --json                                            # Scan directory for code quality metrics
python tools/analysis/code_analyzer.py --project-dir tools/ --store --json                                    # Scan + store metrics in DB
python tools/analysis/code_analyzer.py --file tools/analysis/code_analyzer.py --json                          # Analyze single file
python tools/analysis/code_analyzer.py --project-dir tools/ --trend --json                                    # Maintainability trend data
python tools/analysis/runtime_feedback.py --xml .tmp/results.xml --project-id proj-123 --json                 # Parse JUnit XML + store feedback
python tools/analysis/runtime_feedback.py --health --function analyze_code --json                              # Per-function health score

# Observability, Traceability & Explainable AI (Phase 46)
python -c "from tools.observability import get_tracer; print(type(get_tracer()).__name__)"           # Check active tracer
python tools/observability/shap/agent_shap.py --trace-id "<trace-id>" --iterations 1000 --json       # SHAP analysis on trace
python tools/observability/shap/agent_shap.py --project-id "proj-123" --last-n 10 --json             # SHAP last N traces
python tools/observability/provenance/prov_query.py --entity-id "<id>" --direction backward --json    # Provenance lineage
python tools/observability/provenance/prov_export.py --project-id "proj-123" --json                   # PROV-JSON export
python tools/compliance/xai_assessor.py --project-id "proj-123" --json                                # XAI assessment (10 checks)
python tools/compliance/xai_assessor.py --project-id "proj-123" --gate                                # XAI gate evaluation

# EU AI Act Risk Classifier (Phase 57, D349)
python tools/compliance/eu_ai_act_classifier.py --project-id "proj-123" --json          # Assess all 12 requirements
python tools/compliance/eu_ai_act_classifier.py --project-id "proj-123" --gate          # Gate evaluation

# Platform One / Iron Bank (Phase 57, D350)
python tools/infra/ironbank_metadata_generator.py --project-id "proj-123" --generate --json                     # Generate hardening manifest
python tools/infra/ironbank_metadata_generator.py --project-id "proj-123" --generate --output-dir .tmp/ironbank # Generate + write to dir
python tools/infra/ironbank_metadata_generator.py --project-id "proj-123" --validate --manifest-path .tmp/ironbank/hardening_manifest.yaml  # Validate
python tools/infra/ironbank_metadata_generator.py --list-base-images --json              # List Iron Bank base images

# Compliance Evidence Auto-Collection (Phase 56, D347)
python tools/compliance/evidence_collector.py --project-id "proj-123" --json             # Collect all frameworks
python tools/compliance/evidence_collector.py --project-id "proj-123" --framework fedramp --json  # Single framework
python tools/compliance/evidence_collector.py --project-id "proj-123" --freshness --max-age-hours 168 --json  # Check freshness
python tools/compliance/evidence_collector.py --list-frameworks --json                   # List supported frameworks

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

# Heartbeat Daemon (Phase 29 — Proactive Monitoring)
python tools/monitor/heartbeat_daemon.py                # Foreground daemon (7 configurable checks)
python tools/monitor/heartbeat_daemon.py --once          # Single pass of all checks
python tools/monitor/heartbeat_daemon.py --check cato_evidence  # Specific check
python tools/monitor/heartbeat_daemon.py --status --json # Show all check statuses

# Webhook-Triggered Auto-Resolution (Phase 29)
python tools/monitor/auto_resolver.py --analyze --alert-file alert.json --json   # Analyze without acting
python tools/monitor/auto_resolver.py --resolve --alert-file alert.json --json   # Full pipeline: analyze + fix + PR
python tools/monitor/auto_resolver.py --history --json                            # Resolution history

# Selective Skill Injection (Phase 29)
python tools/agent/skill_selector.py --query "fix the login tests" --json         # Keyword-based category matching
python tools/agent/skill_selector.py --detect --project-dir /path --json          # File-based detection
python tools/agent/skill_selector.py --query "deploy to staging" --format-context # Injection-ready markdown

# Time-Decay Memory Ranking (Phase 29)
python tools/memory/time_decay.py --score --entry-id 42 --json                    # Score single entry
python tools/memory/time_decay.py --rank --query "keyword" --top-k 10 --json      # Time-decay ranked search
python tools/memory/hybrid_search.py --query "test" --time-decay                   # Integrated time-decay search

# Dashboard (Flask web UI — "GI proof" UX)
python tools/dashboard/app.py                        # Start web dashboard on port 5000
# Dashboard auth management (Phase 30 — D169-D172)
python tools/dashboard/auth.py create-admin --email admin@icdev.local --name "Admin"   # Create first admin + API key
python tools/dashboard/auth.py list-users            # List all dashboard users
# Env vars: ICDEV_DASHBOARD_SECRET, ICDEV_CUI_BANNER_ENABLED, ICDEV_BYOK_ENABLED, ICDEV_BYOK_ENCRYPTION_KEY
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
#   /chat              — Unified agent chat: multi-stream + RICOAS + governance (D20, D257-D260, D322-D330, Phases 44/50/51)
#   /gateway           — Remote Command Gateway admin (bindings, command log, channels)
#   /batch             — Batch operations panel (multi-tool workflow execution)
#   /login             — API key login page (D169-D172)
#   /logout            — Clear session and redirect to login
#   /activity          — Merged activity feed (audit + hook events, WebSocket + polling)
#   /usage             — Usage tracking + cost dashboard (per-user, per-provider)
#   /profile           — User profile + BYOK LLM key management
#   /dev-profiles      — Dev profile management (create, resolve cascade, lock, version history)
#   /children          — Child application registry (health, genome version, capabilities, heartbeat)
#   /traces            — Trace explorer: stat grid, trace list, span waterfall SVG (Phase 46)
#   /provenance        — Provenance viewer: entity/activity tables, lineage query, PROV-JSON export (Phase 46)
#   /xai               — XAI dashboard: assessment runner, coverage gauge, SHAP bar chart (Phase 46)
#   /oscal             — OSCAL ecosystem: tool detection, validation log, catalog browser, artifacts (D302-D306)
#   /prod-audit        — Production readiness audit: 38 checks, 7 categories, remediation log (D291-D300)
#   /code-quality      — Code Quality Intelligence: stat grid, SVG trend chart, smell distribution, complex functions, runtime feedback (Phase 52, D331-D337)
#   /fedramp-20x       — FedRAMP 20x KSI dashboard: KSI status grid, evidence table, generate/package actions (Phase 53, D338)
#   /evidence          — Compliance evidence inventory: multi-framework collection, freshness status, collect trigger (Phase 56, D347)
#   /lineage           — Artifact lineage DAG: SVG visualization joining digital thread, provenance, audit trail, SBOM (Phase 56, D348)
#   /ai-transparency   — AI Transparency dashboard: framework scores, model/system cards, AI inventory, fairness, confabulation, GAO evidence, gap analysis (Phase 48)
#   /ai-accountability — AI Accountability: oversight plans, CAIO registry, appeals, incidents, ethics reviews, reassessment scheduling, cross-framework audit (Phase 49)
#   /proposals         — Proposal lifecycle tracker: opportunity list, stat grid, new opportunity modal
#   /proposals/<id>    — Opportunity detail: 6 tabs (Overview, Sections, Assignment Matrix, Compliance Matrix, Timeline Gantt, Reviews), countdown, donut/bar charts
#   /proposals/<id>/sections/<id> — Section detail: 14-step status pipeline, info grid, notes, compliance items, findings, dependencies, status history, advance workflow
#   /cpmp              — CPMP portfolio dashboard: stat grid (total/active/value/burn rate/overdue/at-risk), health distribution, contract table, upcoming deliverables (Phase 60)
#   /cpmp/<id>         — CPMP contract detail: 7 tabs (Overview, CLINs, WBS, Deliverables, EVM, Subcontractors, CPARS), health badge, funding gauge, Monte Carlo
#   /cpmp/<id>/deliverables/<did> — CPMP deliverable detail: 10-state status pipeline, CDRL generation, submission history
#   /cpmp/cor          — COR portal: government read-only contract list, blue accent, filtered by cor_email
#   /cpmp/cor/<id>     — COR contract detail: read-only (deliverables, EVM, CPARS only — no CLINs/WBS/internal costs)
#   /admin/users       — Admin user/key management (admin role only)
# Auth: per-user API keys (SHA-256 hashed), Flask signed sessions (D169-D171)
# RBAC: 6 roles (admin, pm, developer, isso, co, cor) — D172, Phase 60
# BYOK: bring-your-own LLM keys, Fernet AES-256 encrypted (D175-D178)
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

# Remote Command Gateway (Phase 28)
python tools/gateway/gateway_agent.py                                          # Start gateway on port 8458
python tools/gateway/user_binder.py --provision --channel mattermost --channel-user-id "user123" --icdev-user-id "admin@enclave.mil" --json  # Pre-provision binding (air-gapped)
python tools/gateway/user_binder.py --list --json                              # List all bindings
python tools/gateway/user_binder.py --revoke <binding-id>                      # Revoke a binding
# Channels: telegram (IL2-IL4), slack (IL2-IL5), teams (IL2-IL5), mattermost (IL2-IL6, air-gapped), internal_chat (IL2-IL6, always available)
# Config: args/remote_gateway_config.yaml — channels, allowlists, security, environment mode
# Air-gapped: Set environment.mode: air_gapped to auto-disable internet-dependent channels

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
# API gateway endpoints: /health, /api/v1/* (REST), /mcp/v1/ (MCP Streamable HTTP)
#   /api/v1/docs         — Swagger UI (D153)
#   /api/v1/openapi.json — OpenAPI 3.0.3 spec (D153)
#   /metrics             — Prometheus metrics (D154)
python tools/saas/openapi_spec.py [--output spec.json]                           # Generate OpenAPI spec to file
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
| `data/icdev.db` | 229 tables | Main operational DB: projects, agents, A2A tasks, audit trail, compliance (NIST, FedRAMP, CMMC, CSSP, SbD, IV&V, OSCAL, FIPS 199/200), eMASS, cATO evidence, PI tracking, knowledge, deployments, metrics, alerts, maintenance audit, MBSE, Modernization, RICOAS (intake, boundary, supply chain, simulation, integration), Operations & Automation (hook_events, agent_executions, nlq_queries, ci_worktrees, gitlab_task_claims), Multi-Agent Orchestration (agent_token_usage, agent_workflows, agent_subtasks, agent_mailbox, agent_vetoes, agent_memory, agent_collaboration_history), Agentic Generation (child_app_registry, agentic_fitness_assessments), Security Categorization (fips199_categorizations, project_information_types, fips200_assessments), Marketplace (marketplace_assets, marketplace_versions, marketplace_reviews, marketplace_installations, marketplace_scan_results, marketplace_ratings, marketplace_embeddings, marketplace_dependencies), Universal Compliance (data_classifications, framework_applicability, compliance_detection_log, crosswalk_bridges, framework_catalog_versions, cjis_assessments, hipaa_assessments, hitrust_assessments, soc2_assessments, pci_dss_assessments, iso27001_assessments), DevSecOps/ZTA (devsecops_profiles, zta_maturity_scores, zta_posture_evidence, nist_800_207_assessments, devsecops_pipeline_audit), MOSA (mosa_assessments, icd_documents, tsp_documents, mosa_modularity_metrics), Remote Gateway (remote_user_bindings, remote_command_log, remote_command_allowlist), Schema Migrations (schema_migrations — D150 version tracking), Spec-Kit (project_constitutions, spec_registry — D156-D161), Proactive Monitoring (heartbeat_checks, auto_resolution_log — D162-D166), Dashboard Auth & BYOK (dashboard_users, dashboard_api_keys, dashboard_auth_log, dashboard_user_llm_keys — D169-D178), Dev Profiles (dev_profiles, dev_profile_locks, dev_profile_detections — D183-D188), Innovation Engine (innovation_signals, innovation_triage_log, innovation_solutions, innovation_trends, innovation_competitor_scans, innovation_standards_updates, innovation_feedback — D199-D208), AI Security (prompt_injection_log, ai_telemetry, ai_bom, atlas_assessments, atlas_red_team_results, owasp_llm_assessments, nist_ai_rmf_assessments, iso42001_assessments — D209-D219), Evolutionary Intelligence (child_capabilities, child_telemetry, child_learned_behaviors, genome_versions, capability_evaluations, staging_environments, propagation_log — D209-D214), Cloud-Agnostic (cloud_provider_status, cloud_tenant_csp_config, csp_region_certifications — D225-D233), Translation (translation_jobs, translation_units, translation_dependency_mappings, translation_validations — D242-D256), Innovation Adaptation (chat_contexts, chat_messages, chat_tasks, extension_registry, extension_execution_log, memory_consolidation_log — D257-D279), OWASP Agentic Security (tool_chain_events, agent_trust_scores, agent_output_violations — Phase 45), Observability & XAI (otel_spans, prov_entities, prov_activities, prov_relations, shap_attributions, xai_assessments — D280-D289), Production Readiness (production_audits, remediation_audit_log — D291-D300), OSCAL Ecosystem (oscal_validation_log — D306), AI Transparency (omb_m25_21_assessments, omb_m26_04_assessments, nist_ai_600_1_assessments, gao_ai_assessments, model_cards, system_cards, confabulation_checks, ai_use_case_inventory, fairness_assessments — D307-D315), AI Accountability (ai_oversight_plans, ai_caio_registry, ai_appeals, ai_incident_log, ai_ethics_reviews, ai_reassessment_schedule — D316-D321), Code Intelligence (code_quality_metrics, runtime_feedback — D331-D337), Phases 53-57 (owasp_asi_assessments, eu_ai_act_assessments — D339, D349), Creative Engine (creative_competitors, creative_signals, creative_pain_points, creative_feature_gaps, creative_specs, creative_trends — D351-D360), CPMP (cpmp_contracts, cpmp_clins, cpmp_wbs, cpmp_deliverables, cpmp_status_history, cpmp_evm_periods, cpmp_subcontractors, cpmp_cpars_assessments, cpmp_negative_events, cpmp_small_business_plan, cpmp_cdrl_generations, cpmp_sam_contract_awards, cpmp_cor_access_log — Phase 60, D-CPMP-1 through D-CPMP-10) |
| `data/platform.db` | 6 tables | SaaS platform DB: tenants, users, api_keys, subscriptions, usage_records, audit_platform |
| `data/tenants/{slug}.db` | (per-tenant) | Isolated copy of icdev.db schema per tenant — separate DB per tenant for strongest isolation |
| `data/memory.db` | 3 tables | Memory system: entries, daily logs, access log |
| `data/activity.db` | 1 table | Task tracking |

**Audit trail is append-only/immutable** — no UPDATE/DELETE operations. Satisfies NIST 800-53 AU controls.

### Args Configuration Files

| File | Purpose |
|------|---------|
| `args/project_defaults.yaml` | TDD settings, compliance baseline, security thresholds, infra defaults, CI/CD stages, monitoring, agent config |
| `args/agent_config.yaml` | 15 agent definitions with ports, TLS certs, Bedrock model config |
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
| `args/remote_gateway_config.yaml` | Remote Command Gateway: environment mode (connected/air_gapped), 5 channel definitions (telegram, slack, teams, mattermost, internal_chat), security settings (binding TTL, signatures, rate limits), command allowlist with per-channel restrictions |
| `args/scaling_config.yaml` | Auto-scaling: HPA profiles (core/domain/support/dashboard/api_gateway), PDB config, topology spread, node autoscaler type (cluster-autoscaler/karpenter/none), custom metrics (Phase 2), rate limiter backend (in_memory/redis) |
| `args/resilience_config.yaml` | Circuit breaker defaults + per-service overrides (bedrock, redis, jira, servicenow, gitlab); retry defaults (max_retries, base_delay, max_delay) |
| `args/db_config.yaml` | Database migration settings (auto_migrate, checksum validation, lock timeout); backup settings (retention, encryption, per-database schedules); tenant backup policies |
| `args/spec_config.yaml` | Spec-kit pattern configuration (D156-D161): quality checklist, constitution, clarification (max questions, impact/uncertainty levels), spec directory structure, parallel markers |
| `args/skill_injection_config.yaml` | Selective skill injection (D167): 9 category definitions with keywords→commands/goals/context_dirs, file_extension_map, path_pattern_map, always_include, confidence_threshold |
| `args/memory_config.yaml` | Time-decay memory ranking (D168): per-type half-lives (fact=90d, preference=180d, event=7d, insight=30d, task=14d, relationship=120d), scoring weights (relevance=0.60, recency=0.25, importance=0.15), importance resistance threshold |
| `args/dev_profile_config.yaml` | Dev profile dimensions (D184): 10 dimension categories (language, style, testing, architecture, security, compliance, operations, documentation, git, ai), cascade rules, detection keywords, intake signals, task-dimension mapping |
| `args/companion_registry.yaml` | Universal AI Companion (D194): 10 tool definitions (Claude Code, Codex, Gemini, Copilot, Cursor, Windsurf, Amazon Q, JetBrains/Junie, Cline, Aider), instruction file paths, MCP support flags, skill formats, env detection signals, capabilities |
| `args/innovation_config.yaml` | Innovation Engine (D199): web sources (GitHub, NVD, SO, HN, package registries, compliance feeds), signal categories, 5-dimension scoring weights/thresholds, 5-stage triage rules, solution generation config, introspective analysis, competitive intel, standards monitoring, feedback calibration, scheduling (daemon mode, quiet hours) |
| `args/cloud_config.yaml` | Cloud-Agnostic Architecture (D225, D232): CSP selection (aws/azure/gcp/oci/ibm/local), cloud_mode (commercial/government/on_prem/air_gapped), region, impact level, per-CSP settings (GovCloud, Azure Government, Assured Workloads, OCI Government, IBM IC4G), per-service CSP overrides for secrets/storage/kms, region certification validation (D234) |
| `args/csp_monitor_config.yaml` | CSP Service Monitor (D239): 5 CSP sources (AWS, Azure, GCP, OCI, IBM) with RSS/API/HTML endpoints, signal generation (8 change types with category/score/urgency mapping), diff engine, notification/escalation, changelog generation, Innovation Engine integration, scheduling (daemon mode, quiet hours) |
| `args/security_gates.yaml` | (updated) Added `atlas_ai` gate with blocking conditions: critical_atlas_technique_unmitigated, prompt_injection_defense_inactive, ai_telemetry_not_active, agent_permissions_not_configured, ai_bom_missing; thresholds: min_atlas_coverage_pct=80, ai_telemetry_required=true |
| `args/translation_config.yaml` | Cross-Language Translation (D242-D256): 30 language pairs, extraction parsers, translation settings (max_chunk_lines=500, temperature=0.2, candidates=3), repair (max_attempts=3, compiler_feedback), type_checking, assembly (per-language project conventions), validation thresholds (api_surface≥0.90, type_coverage≥0.85, round_trip≥0.80, complexity≤30%), test translation framework mappings, compliance (95% control coverage) |
| `args/extension_config.yaml` | Active Extension Hooks (D261-D264): hook point configs (10 extension points), layered override rules (project > tenant > default), safety limits (max 30s total handler time, exception isolation), behavioral vs observational tiers |
| `args/context_config.yaml` | Semantic Layer MCP Tools (D277): CLAUDE.md indexing, cache TTL, agent-role→section mapping (10 agent roles), section refresh on mtime change |
| `args/code_pattern_config.yaml` | Dangerous Pattern Detection (D278): per-language patterns (Python, Java, Go, Rust, C#, TypeScript + universal), scan settings (skip_dirs, file_extensions, max_file_size), severity classification (critical/high/medium/low) |
| `args/security_gates.yaml` | (updated) Added `code_patterns` gate with max_critical=0, max_high=0, max_medium=10 |
| `args/owasp_agentic_config.yaml` | OWASP Agentic AI Security (Phase 45): behavioral drift thresholds (z-score, 7-day baseline), tool chain rules (4 default: secrets→external, read→exfil, privesc→deploy, rapid burst), output validation (classification leak, SSN, credentials, private keys), trust scoring (decay/recovery factors, 3 trust levels), MCP per-tool RBAC (5 roles: admin, pm, developer, isso, co) |
| `args/security_gates.yaml` | (updated) Added `owasp_agentic` gate with blocking: agent_trust_below_untrusted, tool_chain_critical_violation, output_classification_leak, behavioral_drift_critical, mcp_authorization_not_configured; thresholds: min_trust_score=0.30, max_critical_chain_violations=0, max_critical_output_violations=0 |
| `args/observability_tracing_config.yaml` | Observability & XAI (Phase 46, D290): dual-mode tracer config (otel/sqlite auto-detect via ICDEV_MLFLOW_TRACKING_URI), sampling rate, retention (sqlite_retention_days, mlflow_retention_days), content tracing policy (hash-only vs plaintext, ICDEV_CONTENT_TRACING_ENABLED), PROV-AGENT settings, AgentSHAP defaults (iterations, seed), XAI assessment thresholds |
| `args/security_gates.yaml` | (updated) Added `observability_xai` gate with blocking: tracing_not_active, provenance_graph_empty, xai_assessment_not_completed, content_tracing_active_in_cui_without_approval; thresholds: tracing_required=true, provenance_required=true, shap_max_age_days=30, min_xai_coverage_pct=80 |
| `args/oscal_tools_config.yaml` | OSCAL Ecosystem Tools (D302-D306): oscal-cli paths/timeout/JVM args, oscal-pydantic validation toggles, catalog source priority (official NIST → ICDEV fallback), validation pipeline order (structural → pydantic → Metaschema), max errors per validator |
| `args/security_gates.yaml` | (updated) Added `ai_transparency` gate with blocking: high_impact_ai_not_classified, model_cards_missing_for_deployed_models, ai_inventory_incomplete, gao_evidence_gaps_on_critical_practices, confabulation_detection_not_active; thresholds: min_gao_evidence_coverage_pct=80 |
| `args/ai_governance_config.yaml` | AI Governance Integration (Phase 50, D322-D330): intake detection keywords by 6 pillars, auto-trigger rules (federal agencies, impact level), chat governance (advisory cooldown, AI keyword list, priority order), readiness dimension component weights, probe questions for missing pillars |
| `args/security_gates.yaml` | (updated) Added `ai_governance` gate with blocking: caio_not_designated_for_rights_impacting_ai, oversight_plan_missing_for_high_impact_ai, impact_assessment_not_completed; warning: model_card_missing, fairness_assessment_stale, reassessment_overdue, ai_inventory_incomplete; thresholds: caio_required_for_rights_impacting=true, oversight_plan_required=true, impact_assessment_required=true |
| `args/code_quality_config.yaml` | Code Intelligence (Phase 52, D331-D337): smell thresholds (long_function, deep_nesting, high_complexity, too_many_params, god_class), maintainability weights (complexity 0.30, smell_density 0.20, test_health 0.20, coupling 0.15, coverage 0.15), audit thresholds, scan exclusion dirs, Innovation Engine integration |
| `args/security_gates.yaml` | (updated) Added `code_quality` gate with blocking: avg_cyclomatic_complexity_exceeds_critical; warning: maintainability_score_declining, high_smell_density, dead_code_exceeds_threshold; thresholds: max_avg_complexity=25, min_maintainability_score=0.40, max_smell_density_per_kloc=20, max_dead_code_pct=10 |
| `args/creative_config.yaml` | Creative Engine (Phase 58, D351-D360): domain, sources (review_sites, community_forums, github_issues, producthunt), competitor_discovery (refresh interval, auto_confirm=false), extraction (negative/feature-request keywords, 15 categories, clustering), scoring weights (pain_frequency 0.40, gap_uniqueness 0.35, effort_to_impact 0.25), thresholds (auto_spec 0.75, suggest 0.50), spec_generation, innovation_bridge (min_score 0.60), trends, scheduling (daemon interval, quiet hours) |

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
- **D33:** GitLab tags `{{icdev: workflow}}` for task routing (mirrors Phase 41 Notion pattern, uses existing VCS abstraction)
- **D34:** Read-only SQL enforcement for NLQ (append-only audit trail must not be compromised by NLQ queries)
- **D35:** Agent executor stores JSONL output in `agents/` dir (auditable, replayable, consistent with Phase 39 observability pattern)
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
- **D133:** Channel adapters are ABC + implementations (D66 pattern) — add new channels without modifying gateway core
- **D134:** Air-gapped environments use internal chat + optional Mattermost, never internet channels — IL6/SIPR cannot reach Telegram/Slack/Teams APIs
- **D135:** Response filter strips content above channel max_il, never upgrades — prevents CUI/SECRET leaking to unauthorized channels
- **D136:** User binding is mandatory before any command execution — no anonymous remote commands, full identity chain
- **D137:** Command allowlist is YAML-driven with per-channel overrides — add/remove commands without code changes (D26 pattern)
- **D138:** Deploy commands disabled by default on all remote channels — destructive operations require dashboard/CLI access
- **D139:** `environment.mode: air_gapped` auto-disables internet-dependent channels — single config toggle, no per-channel manual disable needed
- **D140:** Mattermost adapter uses REST API (no WebSocket) — consistent with D20 (no WebSocket), simpler, works behind proxies
- **D141:** HPA with CPU/memory metrics as baseline; custom metrics (queue depth, Bedrock token rate) via Prometheus adapter as Phase 2. All HPA manifests use `autoscaling/v2` API. Cloud-agnostic — works on EKS, GKE, AKS, OpenShift, bare-metal K8s
- **D142:** Cluster Autoscaler as cloud-agnostic baseline for node auto-scaling; vendor-specific optimizations (Karpenter for EKS, GKE Autopilot, AKS cluster-autoscaler) as optional overlays
- **D143:** PDB with `minAvailable=1` for core agents + dashboard + gateway; `maxUnavailable=1` for domain + support agents
- **D144:** Cross-AZ topology spread with `whenUnsatisfiable: ScheduleAnyway` (availability over strict spread) for all scaled components
- **D145:** Platform compatibility module (`tools/compat/`) centralizes OS detection — single source of truth for platform-specific behavior; stdlib only, air-gap safe
- **D146:** Application-level circuit breaker using ABC + in-memory state (stdlib only); 3-state machine: CLOSED → OPEN → HALF_OPEN. D66 provider pattern for pluggable backends
- **D147:** Reusable retry utility extracted from bedrock_client.py; exponential backoff + full jitter; decorator pattern with configurable exceptions and on_retry callback
- **D148:** Structured error hierarchy for new code only (ICDevError → Transient/Permanent → ServiceUnavailable/RateLimited/Configuration); existing 450 bare exceptions left untouched to avoid mass-refactor risk
- **D149:** Request-scoped correlation ID via Flask before_request middleware; propagated through A2A JSON-RPC metadata and audit trail session_id; 12-char UUID prefix
- **D150:** Lightweight migration runner (stdlib only, no Alembic); `schema_migrations` table for version tracking; .sql/.py files with `@sqlite-only`/`@pg-only` directives; checksum validation
- **D151:** Baseline migration (v001) delegates to init_icdev_db.py rather than duplicating 2860-line SQL; init_icdev_db.py preserved for backward compat, detects migration system
- **D152:** Backup tool uses `sqlite3.backup()` API for SQLite (WAL-safe online backup), `pg_dump` for PostgreSQL; optional AES-256-CBC encryption via `cryptography` package (PBKDF2, 600K iterations)
- **D153:** OpenAPI 3.0.3 spec generated programmatically from declarative schema dicts; no flask-restx dependency; Swagger UI loaded from CDN (bundleable for air-gap); 23 endpoints documented
- **D154:** Prometheus metrics use optional `prometheus_client` with stdlib text-format fallback (D66 dual-backend pattern); /metrics exempt from auth; 8 metrics covering HTTP, errors, rate limits, circuit breakers, uptime, tenants
- **D155:** Project-root conftest.py with shared fixtures (icdev_db, platform_db, api_gateway_app, dashboard_app, auth_headers) centralizes test DB setup; test strategy prioritizes security-critical paths first
- **D156:** Spec quality checklist is declarative JSON — add/remove checks without code changes (consistent with D26 pattern)
- **D157:** Cross-artifact consistency uses markdown section-header parsing (`## Header`) — simple, reliable, stdlib-only, air-gap safe
- **D158:** Constitutions stored in DB per-project with defaults from JSON — allows per-project customization while maintaining DoD defaults
- **D159:** Clarification prioritization uses deterministic Impact × Uncertainty 2D matrix — consistent with D21 readiness scoring approach
- **D160:** Per-feature spec directories are optional (additive) — existing flat spec files continue to work unchanged
- **D161:** Parallel markers use `parallel_group` field in safe_decomposition — reuses existing DAG infrastructure (D40) for concurrency annotation
- **D162:** Heartbeat daemon uses configurable check registry with per-check intervals in YAML — each check type has its own cadence (D26 pattern)
- **D163:** Heartbeat notifications fan out to 3 sinks: audit trail (always), SSE (if dashboard running), gateway channels (if configured)
- **D164:** Auto-resolver extends existing webhook_server.py with `/alert-webhook` endpoint — avoids second Flask app, reuses HMAC verification
- **D165:** Auto-resolver reuses existing 3-tier self-healing decision engine (≥0.7 auto, 0.3–0.7 suggest, <0.3 escalate) and rate limits (5/hour)
- **D166:** Auto-resolver creates fix branches/PRs via existing VCS abstraction (`tools/ci/modules/vcs.py`)
- **D167:** Selective skill injection via deterministic keyword-based category matching — no LLM required, declarative YAML config (D26 pattern)
- **D168:** Time-decay uses exponential formula `2^(-(age/half_life))` with per-memory-type half-lives, opt-in via `--time-decay` flag (backward compatible)
- **D169:** Dashboard auth is self-contained against `icdev.db` (not imported from SaaS layer) — keeps dashboard independently deployable
- **D170:** WebSocket via Flask-SocketIO is additive — HTTP polling (D103) remains for backward compat. Falls back automatically when SocketIO unavailable
- **D171:** Session cookies use Flask's built-in signed sessions. `app.secret_key` from `ICDEV_DASHBOARD_SECRET` env var or auto-generated
- **D172:** Dashboard RBAC: 5 roles (admin, pm, developer, isso, co). Admin manages users/keys. Others map to existing `ROLE_VIEWS` for page visibility
- **D173:** CUI banner toggle via `ICDEV_CUI_BANNER_ENABLED` env var (default `true`). Existing `CUI_BANNER_TOP/BOTTOM` env vars preserved
- **D174:** Activity feed merges `audit_trail` + `hook_events` via UNION ALL query — read-only, preserves append-only contract (D6)
- **D175:** BYOK keys stored AES-256 encrypted in `dashboard_user_llm_keys` table (Fernet symmetric encryption, key from env var). Per-user keys override per-department env vars, which override system config
- **D176:** BYOK injection via `api_key_override` field on `LLMRequest` — router passes override to provider, provider uses it before config/env fallback
- **D177:** Usage tracking extends `agent_token_usage` table with `user_id` column (nullable for backward compat). Cost dashboard aggregates by user and provider
- **D178:** BYOK disabled by default (`ICDEV_BYOK_ENABLED=false`). When enabled, users see an "LLM Keys" section in their profile. Admin can enable/disable per-tenant
- **D183:** Version-based immutability — no UPDATE on `dev_profiles`, insert new version (consistent with D6 append-only)
- **D184:** 5-layer deterministic cascade (Platform → Tenant → Program → Project → User) — locked dimensions skip-propagate (child cannot override locked parent)
- **D185:** Auto-detection is advisory only — detected profile dimensions require human acceptance (consistent with D110 compliance auto-detection)
- **D186:** PROFILE.md generated from dev_profile via Jinja2 (consistent with D50 dynamic CLAUDE.md) — read-only narrative, not separately editable
- **D187:** LLM injection uses selective dimension extraction per task context (consistent with D167 skill injection) — code gen gets language+style, review gets testing+security
- **D188:** Starter templates in `context/profiles/*.yaml` (consistent with `context/requirements/default_constitutions.json`) — 6 sector-specific templates (DoD, FedRAMP, Healthcare, Financial, Law Enforcement, Startup)
- **D189:** `icdev.yaml` is advisory — declares intent but DB remains source of truth; explicit `--init` required to sync manifest to DB
- **D190:** Session context outputs as stdout markdown (like `memory_read.py`) — not dynamic CLAUDE.md injection; consumed by Claude at session start
- **D191:** SDK wraps CLI subprocess calls (not REST API) — works offline, air-gap safe (D134), no server dependency; `stdin=DEVNULL`, timeout, safe env filtering
- **D192:** Pipeline config generator uses declarative CHECK_REGISTRY (D26 pattern) — add new checks without code changes; generates GitHub Actions or GitLab CI from `icdev.yaml`
- **D193:** Env var overrides use `ICDEV_` prefix; 3-level precedence: env > yaml > defaults; integer auto-parsing for gate thresholds
- **D194:** Companion registry (`args/companion_registry.yaml`) is declarative YAML — add new AI tools without code changes (D26 pattern); 10 tools: Claude Code, Codex, Gemini, Copilot, Cursor, Windsurf, Amazon Q, JetBrains/Junie, Cline, Aider
- **D195:** Instruction files generated from Jinja2 string constant templates (D186 pattern) — one template per tool format, each tailored to the tool's conventions
- **D196:** MCP is the primary integration protocol — 9/10 supported tools have MCP support; `.mcp.json` (Claude Code format) is source of truth, translated to per-tool config formats
- **D197:** Tool detection is advisory only (D110/D185 pattern) — auto-detect from env vars, config dirs, config files; explicit `--platform` override always available
- **D198:** Skill translation preserves semantic intent — each tool gets equivalent capability in its native format, not a literal copy
- **D209:** Capability genome uses semver + SHA-256 content hash for versioned, tamper-evident genome tracking (Phase 36)
- **D210:** Telemetry collector uses pull-based model — parent polls child heartbeat endpoints (no child→parent push required)
- **D211:** Staging manager uses git worktree isolation for capability testing before genome absorption
- **D212:** 72-hour stability window before genome absorption — capability must demonstrate stability in staging for ≥72 hours
- **D213:** Bidirectional learning: children report learned behaviors to parent via LearningCollector; parent evaluates and optionally absorbs into genome
- **D214:** Cross-pollination requires HITL approval — no auto-execute for capability sharing between children (append-only propagation_log)
- **D215:** Prompt injection detector uses 5 detection categories: role hijacking, delimiter attacks, instruction injection, data exfiltration, encoded payloads
- **D216:** AI telemetry logger hashes prompts/responses with SHA-256 — stores hashes not plaintext (privacy-preserving audit)
- **D217:** AI BOM (AI Bill of Materials) tracks all AI/ML components, models, training data lineage
- **D218:** ATLAS assessor maps MITRE ATLAS mitigations to automated checks via BaseAssessor pattern (D116)
- **D219:** ATLAS red teaming is opt-in only (`--atlas-red-team` flag) — never auto-executes adversarial tests
- **D220:** OWASP LLM Top 10 assessor crosswalks through ATLAS to NIST 800-53 US hub
- **D221:** NIST AI RMF assessor covers 4 functions (Govern, Map, Measure, Manage) with 12 subcategories
- **D222:** ISO 42001 assessor bridges through ISO 27001 international hub for crosswalk integration
- **D223:** SAFE-AI catalog maps 100 AI-affected NIST 800-53 controls with `ai_concern` narrative per control
- **D224:** Capability evaluator uses 6-dimension weighted scoring: universality(0.25), compliance_safety(0.25), risk(0.20), evidence(0.15), novelty(0.10), cost(0.05)
- **D225:** CSP abstraction uses ABC + 6 implementations (AWS, Azure, GCP, OCI, IBM, Local) per service — Secrets, Storage, KMS, Monitoring, IAM, Registry
- **D226:** Multi-cloud Terraform generators produce CSP-specific IaC (Azure Gov VNet/AKS, GCP Assured Workloads VPC/GKE, OCI Gov VCN/OKE, IBM IC4G VPC/IKS, on-prem K8s/Docker)
- **D227:** Terraform dispatcher auto-detects CSP from `cloud_config.yaml` or `ICDEV_CLOUD_PROVIDER` env var, delegates to CSP-specific generator
- **D228:** LLM multi-cloud: Azure OpenAI (*.openai.azure.us), Vertex AI (Gemini + Claude-via-Vertex), OCI GenAI (Cohere + Llama), IBM watsonx.ai (Granite + Llama) — all via LLMProvider ABC
- **D229:** Helm value overlays per CSP (`values-aws.yaml`, `values-azure.yaml`, `values-gcp.yaml`, `values-oci.yaml`, `values-ibm.yaml`, `values-on-prem.yaml`, `values-docker.yaml`) for CSP-specific K8s config
- **D230:** CSP health checker probes all configured cloud services and stores status in `cloud_provider_status` table
- **D231:** Marketplace Gates 8-9: prompt injection scan (blocking) + behavioral sandbox (warning) — scans all asset files for injection patterns and dangerous code patterns
- **D232:** `cloud_mode` (commercial/government/on_prem/air_gapped) controls endpoint selection and feature availability per CSP — single config field, providers adapt behavior
- **D233:** CSP region certifications stored as declarative JSON (`csp_certifications.json`); human-maintained, machine-validated at deployment time
- **D234:** Region validator blocks deployment to regions lacking required compliance certifications before Terraform/Helm generation (REQ-38-080-082)
- **D236:** On-prem Terraform targets Docker Compose and self-managed K8s; no cloud provider block required
- **D237:** IBM Cloud providers follow D66 ABC pattern with `ibm-cloud-sdk-core` + `ibm-platform-services` SDKs; IBM COS uses S3-compatible `ibm_boto3`
- **D238:** IBM watsonx.ai LLM provider uses `ibm-watsonx-ai` SDK; Granite and Llama model families; embedding via Slate model

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
- **Acceptance Validation Gate:** 0 failed acceptance criteria, 0 pages with error patterns (500, tracebacks, JS errors), plan must contain `## Acceptance Criteria` section
- **Remote Command Gate:** User binding required, signature verification on webhooks, replay window 300s, rate limit 30/user/min + 100/channel/min, icdev-deploy + icdev-init blocked on all remote channels, icdev-test/icdev-secure/icdev-build require confirmation
- **AI Security Gate:** Prompt injection defense active, AI telemetry enabled, AI BOM present, ≥80% ATLAS coverage, agent permissions configured
- **Genome Propagation Gate:** 72-hour stability window passed, capability evaluation score ≥0.65, HITL approval required for execution, compliance preservation verified in staging
- **Marketplace Prompt Injection Gate (Gate 8):** 0 high-confidence prompt injection patterns in asset files — blocking gate
- **Marketplace Behavioral Sandbox Gate (Gate 9):** 0 critical dangerous code patterns (eval, exec, os.system) — warning gate
- **Translation Gate:** Blocking: syntax errors in output, API surface < 90%, compliance coverage < 95%, secrets detected, CUI markings missing. Warning: round-trip similarity < 80%, type coverage < 85%, complexity increase > 30%, unmapped deps, stub functions, lint issues
- **Claude Config Alignment Gate:** Blocking: append-only table unprotected in pre_tool_use.py, hook syntax error, hook reference missing. Warning: dashboard route undocumented, E2E coverage gap, settings deny rule missing
- **AI Accountability Gate:** CAIO designated for high-impact AI, oversight plan exists, 0 unresolved critical AI incidents, no reassessments overdue >90 days; warn on appeal process not defined, ethics review not conducted, impact assessment missing, fairness gate not passing
- **AI Governance Gate:** CAIO designated for rights-impacting AI, oversight plan for high-impact AI, impact assessment completed; warn on model card missing, fairness assessment stale, reassessment overdue, AI inventory incomplete
- **Code Quality Gate:** Avg cyclomatic complexity ≤ 25 (blocking), maintainability score not declining, smell density ≤ 20/KLOC, dead code ≤ 10%

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
| Observability | `goals/observability.md` | Hook-based agent monitoring: tool usage tracking, HMAC-signed events, agent execution framework, SIEM forwarding (Phase 39) |
| NLQ Compliance | `goals/nlq_compliance.md` | Natural language compliance queries via Bedrock, read-only SQL enforcement, SSE dashboard events (Phase 40) |
| Parallel CI/CD | `goals/parallel_cicd.md` | Git worktree task isolation, GitLab `{{icdev: workflow}}` tag routing, parallel workflow execution (Phase 41) |
| Framework Planning | `goals/framework_planning.md` | Language-specific build commands (Python/Java/Go/Rust/C#/TypeScript), 12 Leverage Points framework (Phase 42) |
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
| Remote Command Gateway | `goals/remote_command_gateway.md` | Remote Command Gateway: messaging channel integration (Telegram, Slack, Teams, Mattermost, internal chat), 8-gate security chain, IL-aware response filtering, user binding ceremony, air-gapped/connected mode, command allowlist (Phase 28) |
| Modular Installation | `goals/modular_installation.md` | Modular installer: interactive wizard, profile-based deployment (10 profiles), compliance posture configuration, platform artifact generation (Docker/K8s/Helm), module dependency resolution, add/upgrade existing installations (Phase 33) |
| Dev Profiles | `goals/dev_profiles.md` | Tenant dev profiles & personalization: 5-layer cascade (Platform→Tenant→Program→Project→User), role-based lock governance, auto-detection from codebases, PROFILE.md generation, LLM prompt injection, 6 starter templates, version history with diff/rollback (Phase 34) |
| Three-Tier DX | `docs/dx/README.md` | Developer experience: Tier 1 (icdev.yaml → CI/CD auto-generation), Tier 2 (session context auto-load for Claude Code), Tier 3 (Python SDK wrapping CLI tools). Manifest loader (D189), session context builder (D190), SDK client (D191), pipeline config generator (D192), env var overrides (D193) |
| Universal AI Companion | `docs/dx/companion-guide.md` | Universal AI coding companion: 10 tools (Claude Code, Codex, Gemini, Copilot, Cursor, Windsurf, Amazon Q, JetBrains/Junie, Cline, Aider), instruction file generation (D195), MCP config translation (D196), skill translation (D198), auto-detection (D197), companion registry (D194) |
| Innovation Engine | `goals/innovation_engine.md` | Autonomous self-improvement: web intelligence scanning (GitHub, NVD, SO, HN), 5-dimension scoring, 5-stage compliance triage, trend detection, solution generation, introspective analysis, competitive intel, standards monitoring, feedback calibration (Phase 35, D199-D208) |
| Evolutionary Intelligence | `goals/evolutionary_intelligence.md` | Parent-child lifecycle: capability genome (semver+SHA-256), 7-dimension evaluation (incl. security_assessment), staging isolation, HITL propagation, 72-hour absorption window, bidirectional learning, cross-pollination, Phase 37 injection scanning (Phase 36, D209-D215) |
| MITRE ATLAS Integration | `goals/atlas_integration.md` | AI-specific threat defense: prompt injection detection (5 categories), AI telemetry (SHA-256 hashing), ATLAS assessor, OWASP LLM Top 10, NIST AI RMF, ISO 42001, ATLAS red teaming (6 techniques), marketplace hardening (Gates 8-9) (Phase 37, D215-D223, D231) |
| Cloud-Agnostic Architecture | `goals\cloud_agnostic.md` | Multi-cloud abstraction: CSP provider ABCs (Secrets, Storage, KMS, Monitoring, IAM, Registry × 6 clouds incl. IBM), Terraform generators (Azure/GCP/OCI/IBM Gov + on-prem), LLM multi-cloud (Azure OpenAI, Vertex AI, OCI GenAI, IBM watsonx.ai), Helm overlays, CSP health checker, region validator (Phase 38, D225-D238) |
| Cross-Language Translation | `goals/cross_language_translation.md` | LLM-assisted cross-language code translation: 5-phase hybrid pipeline (Extract→Type-Check→Translate→Assemble→Validate+Repair), 30 directional pairs, pass@k candidates, mock-and-continue, feature mapping rules, type-compatibility pre-check, compiler-feedback repair, compliance bridge, Dashboard+Portal visibility (Phase 43, D242-D256) |
| .claude Directory Maintenance | `goals/claude_dir_maintenance.md` | Per-phase governance checklist: append-only table protection, route documentation, E2E spec coverage, hook integrity, deny rules. Automated 6-check validator (`tools/testing/claude_dir_validator.py`), `claude_config_alignment` security gate (NIST AU-2, CM-3, SA-11) |
| Innovation Adaptation | *(Phase 44 plan)* | Agent Zero + InsForge pattern adaptation: multi-stream parallel chat (thread-per-context, max 5/user), active extension hooks (10 hook points, behavioral/observational tiers), mid-stream intervention (atomic 3-checkpoint system), dirty-tracking state push (debounced SSE), 3-tier history compression (50/30/20 budget), semantic layer MCP tools (CLAUDE.md indexer, role-based context), shared schema enforcement (dataclasses), AI-driven memory consolidation (Jaccard+LLM), dangerous pattern detection (6 languages), innovation signal registration (Phase 44, D257-D279) |
| OWASP Agentic Security | `goals/owasp_agentic_security.md` | OWASP Agentic AI 8-gap implementation: behavioral drift detection (z-score baseline), tool chain validation (sliding-window sequence matching), output content safety (classification leak + PII), formal agentic threat model (STRIDE + T1-T17), dynamic trust scoring (decay/recovery), MCP per-tool RBAC (deny-first, 5 roles), behavioral red teaming (6 BRT techniques), OWASP agentic assessor (17 automated checks). Config: `args/owasp_agentic_config.yaml` (Phase 45) |
| Agentic Threat Model | `goals/agentic_threat_model.md` | Formal STRIDE + OWASP T1-T17 agentic threat model: trust boundaries, MCP server threat surface, 17 threat entries with NIST 800-53 crosswalk, residual risk analysis (Phase 45) |
| Observability & XAI | `goals/observability_traceability_xai.md` | Distributed tracing (OTel+SQLite), W3C PROV-AGENT provenance, AgentSHAP tool attribution, XAI compliance assessor (10 checks), dashboard pages (/traces, /provenance, /xai), MCP server (Phase 46, D280-D290) |
| AI Transparency | `goals/ai_transparency.md` | AI Transparency & Accountability: OMB M-25-21/M-26-04, NIST AI 600-1, GAO-21-519SP — model cards, system cards, AI inventory, confabulation detection, fairness assessment, GAO evidence, cross-framework audit (Phase 48, D307-D315) |
| AI Accountability | `goals/ai_accountability.md` | AI Accountability: oversight plans, CAIO designation, appeals, incident response, ethics reviews, reassessment scheduling, cross-framework audit, assessor fixes (Phase 49, D316-D321) |
| AI Governance Intake | `goals/ai_governance_intake.md` | AI governance integration: RICOAS intake detection (6 pillars), 7th readiness dimension, chat extension advisory, governance sidebar, auto-trigger for federal agencies (Phase 50, D322-D330) |
| Code Intelligence | `goals/code_intelligence.md` | Code quality self-analysis: AST metrics (cyclomatic/cognitive complexity, nesting, params), 5 smell detectors, deterministic maintainability scoring, runtime feedback (test-to-source), production audit (5 CODE checks), Innovation Engine integration, pattern-based TDD learning (Phase 52, D331-D337) |
| FedRAMP 20x | `goals/fedramp_20x.md` | FedRAMP 20x KSI evidence generation (61 KSIs), authorization packaging (OSCAL SSP + KSI bundle), dashboard visualization (Phase 53, D338, D340) |
| Cross-Phase Orchestration | `goals/cross_phase_orchestration.md` | Declarative workflow engine: 4 YAML templates (ato_acceleration, security_hardening, full_compliance, build_deploy), TopologicalSorter DAG, dry-run mode (Phase 54, D343) |
| A2A v0.3 | `goals/a2a_v03.md` | A2A Protocol v0.3: capabilities in Agent Cards, tasks/sendSubscribe streaming, backward-compatible protocolVersion field, discovery server (Phase 55, D344) |
| Evidence Collection | `goals/evidence_collection.md` | Universal compliance evidence auto-collection across 14 frameworks, freshness checking, heartbeat integration (Phase 56, D347) |
| EU AI Act | `goals/eu_ai_act.md` | EU AI Act (Regulation 2024/1689) risk classification: 12 Annex III requirements, 4 risk levels, ISO 27001 bridge crosswalk (Phase 57, D349) |
| Creative Engine | `goals/creative_engine.md` | Customer-centric feature opportunity discovery: auto-discover competitors, scan review sites/forums/GitHub, extract pain points (deterministic keyword), 3-dimension scoring (pain_frequency 0.40 + gap_uniqueness 0.35 + effort_to_impact 0.25), trend detection, template-based spec generation, Innovation Engine bridge (Phase 58, D351-D360) |
| CPMP Workflow | `goals/cpmp_workflow.md` | Contract Performance Management Portal: post-award lifecycle management — EVM (ANSI/EIA-748), CPARS prediction (NDAA), subcontractor FAR 52.219-9, CDRL auto-generation, COR portal, SAM.gov awards, portfolio health scoring (Phase 60, D-CPMP-1 through D-CPMP-10) |

---

## Innovation Engine — Autonomous Self-Improvement (Phase 35)

### Overview
ICDEV continuously improves itself by discovering developer pain points, CVEs, compliance changes, and competitive gaps from the web and internal telemetry — then generating solutions through the existing ATLAS build pipeline with full compliance triage.

### Pipeline
```
DISCOVER (web + introspective + competitive + standards)
    → SCORE (5-dimension weighted average)
        → TRIAGE (5-stage compliance gate)
            → GENERATE (template-based spec)
                → BUILD (ATLAS/M-ATLAS TDD)
                    → PUBLISH (marketplace 7-gate)
                        → MEASURE → CALIBRATE
```

### Commands
```bash
# Full pipeline (one-shot)
python tools/innovation/innovation_manager.py --run --json

# Individual stages
python tools/innovation/web_scanner.py --scan --all --json
python tools/innovation/signal_ranker.py --score-all --json
python tools/innovation/triage_engine.py --triage-all --json
python tools/innovation/trend_detector.py --detect --json
python tools/innovation/solution_generator.py --generate-all --json

# Introspective analysis (air-gap safe)
python tools/innovation/introspective_analyzer.py --analyze --all --json

# Competitive intelligence
python tools/innovation/competitive_intel.py --scan --all --json
python tools/innovation/competitive_intel.py --gap-analysis --json

# Standards body monitoring
python tools/innovation/standards_monitor.py --check --all --json

# Status and reporting
python tools/innovation/innovation_manager.py --status --json
python tools/innovation/innovation_manager.py --pipeline-report --json

# Continuous daemon mode
python tools/innovation/innovation_manager.py --daemon --json

# Feedback calibration
python tools/innovation/signal_ranker.py --calibrate --json
```

### Architecture Decisions
- **D199:** Scan frequency configurable per source in `args/innovation_config.yaml`
- **D200:** Human-in-the-loop: score >= 0.80 auto-queues, 0.50-0.79 suggests, < 0.50 logs only
- **D201:** Budget: max 10 auto-generated solutions per PI
- **D202:** IP/license scanning blocks GPL/AGPL/SSPL (copyleft risk for Gov/DoD)
- **D203:** Introspective analysis is air-gap safe (reads internal DB only)
- **D204:** Standards monitoring degrades gracefully when offline
- **D205:** Competitive intel for GitHub-based competitors (website scraping requires additional setup)
- **D206:** All signals append-only (D6 pattern), triage decisions audited
- **D207:** Trend detection uses deterministic keyword co-occurrence (no LLM, air-gap safe)
- **D208:** Solution specs are template-based (not LLM-generated)
- **D239:** CSP monitoring integrated as Innovation Engine source (Phase 35) — reuses existing signal scoring, triage, and solution generation pipeline; CSP changes treated as innovation signals with category mapping and government/compliance boosts
- **D240:** Declarative CSP service registry as JSON catalog (extends D26 pattern) — baseline of all CSP services, compliance programs, regions, and FIPS status; monitor diffs live data against registry to detect changes; human review required before registry updates
- **D241:** CSP changelog generates actionable recommendations per change type — each change type (deprecation, compliance scope change, breaking API change, etc.) maps to specific files and actions
- **D242:** Hybrid 5-phase translation pipeline — deterministic extraction + type-checking + LLM translation + deterministic assembly + validate-repair loop. Consistent with GOTCHA principle (LLMs probabilistic, business logic deterministic)
- **D243:** IR pivot — source code extracted into language-agnostic JSON IR before translation. Enables chunk-based translation, round-trip validation, progress tracking per unit
- **D244:** Post-order dependency graph traversal at function/class granularity — translate leaf nodes first, then dependents (Amazon Oxidizer)
- **D245:** Non-destructive output (extends D18) — translation output to separate directory, source never modified
- **D246:** Declarative dependency mapping tables (D26 pattern) — cross-language package equivalents in `context/translation/dependency_mappings.json`
- **D247:** 3-part feature mapping rules (Amazon Oxidizer) — syntactic pattern + NL description + static validation check per language pair
- **D248:** Round-trip IR consistency check — re-parse translated output into IR, compare structurally to source IR
- **D249:** Translation compliance bridge — reuses `compliance_bridge.py` for NIST 800-53 control inheritance (95% threshold)
- **D250:** Test translation as separate tool — `test_translator.py` with framework-specific assertion mapping; BDD `.feature` files preserved
- **D251:** Translation DB tables follow existing `migration_plans`/`migration_tasks` pattern for traceability
- **D252:** Dashboard/Portal translation pages follow existing page patterns (stat-grid, table-container, charts.js)
- **D253:** Type-compatibility pre-check (Amazon Oxidizer) — validate function signatures map correctly between source/target type systems BEFORE LLM translation
- **D254:** Pass@k candidate generation (Google) — generate k translation candidates with varied prompts, select best. Default k=3 cloud, k=1 air-gapped
- **D255:** Compiler-feedback repair loop (Google/CoTran) — on validation failure, feed compiler errors back to LLM for targeted repair (max 3 attempts)
- **D256:** Mock-and-continue (Amazon Oxidizer) — on persistent failure, generate type-compatible mock/stub and continue translating dependent units
- **D257-D260:** Multi-Stream Parallel Chat — thread-per-context execution, contexts scoped to `(user_id, tenant_id)`, max 5 concurrent per user, message queue via `collections.deque`, independent of intake sessions
- **D261-D264:** Active Extension Hooks — extensions loaded from numbered Python files (Agent Zero pattern), two tiers: behavioral (modify data) and observational (log only), layered override (project > tenant > default), exception isolation
- **D265-D267:** Mid-Stream Intervention — atomic intervention field on ChatContext, checked at 3 points per loop iteration (pre-LLM, post-LLM, pre-queue-pop), checkpoint preservation, intervention messages stored as `role='intervention'`
- **D268-D270:** Dirty-Tracking State Push — per-client dirty/pushed version counters, SSE debounced at 25ms, HTTP polling at 3s, clients send `?since_version=N` for incremental updates
- **D271-D274:** 3-Tier History Compression — opt-in per context, budget: current topic 50%, historical 30%, bulk 20%, topic boundary: time gap >30min OR keyword shift >60%, LLM/truncation fallback
- **D275:** Shared Schema Enforcement — stdlib `dataclasses` (air-gap safe), optional Pydantic, backward compatible via `to_dict()`, `validate_output()` with strict/non-strict modes
- **D276:** AI-Driven Memory Consolidation — optional `--consolidate` flag, hybrid search finds similar entries, LLM decides MERGE/REPLACE/KEEP_SEPARATE/UPDATE/SKIP, Jaccard keyword fallback, append-only consolidation log
- **D277:** Semantic Layer MCP Tools — CLAUDE.md section indexing via `##` header parsing, metadata from DB with cache TTL, agent-role→section mapping, air-gap safe (stdlib only)
- **D278:** Dangerous Pattern Detection — unified scanner across 6 languages (Python, Java, Go, Rust, C#, TypeScript), callable from marketplace/translation/child app generation/security scanning, declarative YAML patterns
- **D279:** Innovation Signal Registration — external patterns registered as innovation signals, source type `external_framework_analysis`, 5-dimension weighted scoring (novelty, feasibility, compliance_alignment, user_impact, effort)
- **D280:** Pluggable Tracer ABC: `OTelTracer` (production), `SQLiteTracer` (air-gapped), `NullTracer` (fallback). Haystack ProxyTracer pattern. `opentelemetry-sdk` stays optional
- **D281:** Extend correlation ID (D149) to W3C `traceparent` format — additive, backward compatible
- **D282:** Content tracing opt-in via `ICDEV_CONTENT_TRACING_ENABLED` env var — CUI environments must never leak content to telemetry; SHA-256 hashes always recorded, plaintext only when opted in
- **D283:** MLflow as unified trace backend (Apache 2.0, self-hosted) — DoD-safe license, accepts OTLP natively (3.6+), built-in trace UI, SQLite/PG backend matches D1
- **D284:** MCP auto-instrumentation at `base_server.py._handle_tools_call()` — single change instruments all 15 MCP servers
- **D285:** A2A distributed tracing via traceparent in JSON-RPC metadata — 3-line additions to `agent_client.py` and `agent_server.py`
- **D286:** LLM instrumentation at router level with GenAI semantic conventions — `gen_ai.request.model`, `gen_ai.usage.*`, `gen_ai.response.*`
- **D287:** PROV-AGENT provenance in 3 append-only SQLite tables — W3C PROV standard (DOE-funded), Entity/Activity/Relation model
- **D288:** AgentSHAP post-hoc tool attribution via Monte Carlo Shapley values — 0.945 consistency (arXiv:2512.12597), stdlib `random` for sampling (D22 air-gap safe)
- **D289:** XAI assessor via BaseAssessor pattern (D116) — ~200 LOC, crosswalk to NIST 800-53 US hub cascades to FedRAMP/CMMC
- **D290:** Dual-mode config in `args/observability_tracing_config.yaml` — auto-detect: `ICDEV_MLFLOW_TRACKING_URI` set → `otel` mode, else → `sqlite` mode
- **D301:** Unified MCP gateway (`unified_server.py`) uses declarative tool registry (`tool_registry.py`) with lazy module loading. Existing 18 servers remain independently runnable (backward compat). Registry maps tool name → (module, handler, schema). Handlers imported via `importlib.import_module()` on first call, cached thereafter. 55 new tools for CLI gaps use direct Python import with subprocess fallback (`gap_handlers.py`). All 238 tools inherit D284 auto-instrumentation from `base_server.py`. Reduces `.mcp.json` from 18 entries to 1.
- **D302:** oscal-cli invoked via subprocess (`_run_cli()` pattern). Java detected at load time, cached. Degrades to built-in validation when absent. Config: `args/oscal_tools_config.yaml`
- **D303:** oscal-pydantic is a post-generation validation layer. Does NOT replace dict construction. Skipped via `ImportError` when not installed. MIT license.
- **D304:** Official NIST OSCAL catalog stored in `context/oscal/` (downloaded, not committed — 14MB). ICDEV custom catalog (`context/compliance/nist_800_53.json`) preserved as fallback. `OscalCatalogAdapter` normalizes both formats. Priority: official → ICDEV.
- **D305:** Single orchestrator module (`oscal_tools.py`) composes all three integrations. Each independently optional. 3-layer validation pipeline: structural → pydantic → Metaschema.
- **D306:** `oscal_validation_log` append-only table records every validation attempt (D6 pattern). Validator name, pass/fail, error count, duration tracked per layer.
- **D307:** All 4 AI transparency assessors use BaseAssessor ABC (D116) — ~150-200 LOC each, automatic gate/CLI/crosswalk
- **D308:** Model cards follow Google Model Cards format (open standard, widely adopted in Gov AI community)
- **D309:** System cards are ICDEV-specific (broader than model cards — cover full agentic system, not just individual models)
- **D310:** Confabulation detector uses deterministic methods only (consistency checks, citation verification) — no LLM-based detection (air-gap safe)
- **D311:** Fairness assessor focuses on compliance documentation evidence, not statistical bias testing (ICDEV doesn't train models — it uses them)
- **D312:** AI inventory follows OMB M-25-21 schema for direct government reporting compatibility
- **D313:** GAO evidence builder reuses existing ICDEV data (audit_trail, ai_telemetry, XAI, SHAP, provenance) — no new data collection needed
- **D314:** New `AI` data category trigger auto-activates all 4 frameworks + existing NIST AI RMF + ISO 42001 when AI components detected
- **D315:** COSAiS overlay mapping deferred until NIST publishes final specification (anticipated late 2026) — catalog stub in framework_registry.yaml with status: planned
- **D316:** Accountability tables are append-only (D6) except `ai_caio_registry` and `ai_reassessment_schedule` which allow UPDATE (officials change, schedules shift)
- **D317:** Accountability manager is a single coordinator tool (not 13 separate tools) — consolidates gaps into one import with focused functions
- **D318:** AI incident log is separate from `audit_trail` — incidents are AI-specific events requiring corrective action, not generic audit events
- **D319:** Ethics reviews store boolean flags (`opt_out_policy`, `legal_compliance_matrix`, `pre_deployment_review`) for fast assessor checks rather than free-text scanning
- **D320:** Impact assessment stored in `ai_ethics_reviews` with `review_type='impact_assessment'` rather than a separate table — avoids table proliferation
- **D321:** Fairness gate lowered to 25% to be achievable with DB-only checks (no `project_dir` required) — 2 existing + 4 new DB checks = 6/8 = 75% maximum possible
- **D322:** AI governance keyword detection reuses existing `_detect_*_signals()` intake pattern (D119, D125) — deterministic keyword matching from YAML config, no LLM needed
- **D323:** AI governance readiness is the 7th readiness dimension (extends D21 weighted average) — checks 6 governance components against existing Phase 48/49 DB tables
- **D324:** Extension builtins stored in `tools/extensions/builtins/` with numbered Python files (Agent Zero pattern) — auto-loaded by ExtensionManager on init
- **D325:** `chat_message_after` hook activated for governance advisory injection — observational tier, does not block message delivery
- **D326:** Governance sidebar fetches from existing transparency/accountability APIs (no new endpoints) — reuses `/api/ai-transparency/stats` and `/api/ai-accountability/stats`
- **D327:** Advisory messages are non-blocking system messages (advisory-only, not enforcing) — cooldown prevents spamming (default 5 turns)
- **D328:** Single config file (`args/ai_governance_config.yaml`) for all governance integration settings — intake, chat, readiness, auto-trigger
- **D329:** No new database tables — reuses Phase 48/49 tables (`ai_use_case_inventory`, `ai_model_cards`, `ai_oversight_plans`, `ai_ethics_reviews`, `ai_caio_registry`, `ai_reassessment_schedule`) for all governance checks
- **D330:** `ai_governance` security gate is separate from `ai_transparency` and `ai_accountability` gates — governance focuses on cross-cutting intake/chat integration requirements
- **D331:** Code quality metrics are read-only, advisory-only (D110 pattern). Never modifies source files.
- **D332:** `code_quality_metrics` and `runtime_feedback` tables are append-only time-series (D6, D131 pattern).
- **D333:** Python uses `ast.NodeVisitor` (D13); other languages use regex branch-counting (same dispatch as `modular_design_analyzer.py`).
- **D334:** Runtime feedback maps test→source via naming convention. Advisory correlation only.
- **D335:** Code quality signals feed into existing Innovation Engine pipeline (D199-D208). No new pipeline. No autonomous modification.
- **D336:** Pattern learning uses existing +0.1/-0.2 model from `pattern_detector.py`.
- **D337:** Maintainability score = deterministic weighted average: complexity(0.30) + smell_density(0.20) + test_health(0.20) + coupling(0.15) + coverage(0.15).
- **D338:** KSI generator maps ICDEV evidence to FedRAMP 20x KSI schemas. Not a BaseAssessor — KSIs are evidence artifacts, not assessment checks. Follows `cssp_evidence_collector.py` pattern.
- **D339:** OWASP ASI assessor uses BaseAssessor ABC (D116). 10 ASI risks map to NIST 800-53 via crosswalk.
- **D340:** FedRAMP authorization packager bundles OSCAL SSP + KSI evidence. Extends `oscal_generator.py`.
- **D341:** SLSA attestation generator extends existing `attestation_manager.py`. Produces SLSA v1.0 provenance from build pipeline evidence.
- **D342:** CycloneDX version upgrade is backward-compatible with `--spec-version` flag (default 1.7, allow 1.4).
- **D343:** Workflow composer uses declarative YAML templates (D26) + `graphlib.TopologicalSorter` (D40).
- **D344:** A2A v0.3 adds `capabilities` to Agent Card and `tasks/sendSubscribe` for streaming. Backward compatible — checks `protocolVersion` field.
- **D345:** MCP OAuth 2.1 reuses existing SaaS auth middleware. Supports offline HMAC token verification for air-gap.
- **D346:** MCP Elicitation allows tools to request user input mid-execution. MCP Tasks wraps long-running tools with create/progress/complete lifecycle.
- **D347:** Evidence collector extends `cssp_evidence_collector.py` pattern to all 14 frameworks. Uses crosswalk engine for multi-framework mapping.
- **D348:** Lineage dashboard joins digital thread + provenance + audit trail + SBOM into unified DAG visualization. Read-only SVG rendering.
- **D349:** EU AI Act classifier uses BaseAssessor ABC. Bridges through ISO 27001 international hub (D111). Optional — triggered only when `eu_market: true`.
- **D350:** Iron Bank metadata generator follows `terraform_generator.py` pattern. Produces `hardening_manifest.yaml` for Platform One Big Bang. Language auto-detection from project directory.
- **D351:** Creative Engine is separate from Innovation Engine — different domain (customer voice vs. technical signals), different scoring (3-dimension vs. 5-dimension), different sources (review sites/forums vs. CVE/package/standards feeds)
- **D352:** Source adapters via function registry dict (D66/web_scanner `SOURCE_SCANNERS` pattern) — add new sources without code changes
- **D353:** Competitor auto-discovery is advisory-only — stores as `status='discovered'`; human must confirm before tracking activates
- **D354:** Pain extraction is deterministic keyword/regex — no LLM needed, air-gap safe, reproducible
- **D355:** 3-dimension scoring: pain_frequency(0.40) + gap_uniqueness(0.35) + effort_to_impact(0.25) — user-specified weights, deterministic weighted average (D21)
- **D356:** Feature specs are template-based — follows `solution_generator.py` pattern, no LLM, reproducible
- **D357:** All Creative Engine tables append-only except `creative_competitors` (allows UPDATE for status transitions discovered→confirmed→archived)
- **D358:** Reuses `_safe_get()`, `_get_db()`, `_now()`, `_audit()` helpers — copy-adapted from `web_scanner.py`
- **D359:** Daemon mode respects quiet hours from config — consistent with `innovation_manager.py`
- **D360:** High-scoring creative signals cross-register to `innovation_signals` — enables Innovation Engine trend detection on creative discoveries
- **D-CPMP-1:** All CPMP tables prefixed `cpmp_` — namespace isolation from existing govcon/proposal tables
- **D-CPMP-2:** EVM uses deterministic formulas, Monte Carlo via stdlib `random` (D22) — air-gap safe, no numpy/scipy
- **D-CPMP-3:** CPARS prediction uses deterministic weighted average (D21) — reproducible, not probabilistic; ML upgrade path later
- **D-CPMP-4:** COR portal is read-only routes on same Flask app — reuses existing auth; role-based access sufficient
- **D-CPMP-5:** CDRL generator dispatches to existing ICDEV tools — reuse ssp_generator, sbom_generator, stig_checker, etc.
- **D-CPMP-6:** SAM.gov Contract Awards follows sam_scanner.py pattern (D366) — consistent rate limiting, content hash dedup
- **D-CPMP-7:** Negative events append-only (D6) — NIST AU-2; corrective action status tracked on record
- **D-CPMP-8:** Contract health is deterministic weighted average (D21) — configurable weights in YAML (D26)
- **D-CPMP-9:** Transition bridge is explicit API call, not automatic — human confirms contract creation from won proposal
- **D-CPMP-10:** `idiq_contract_id` self-reference for IDIQ/TO hierarchy — task orders under IDIQ vehicles without separate table

### Innovation Security Gates
| Gate | Condition |
|------|-----------|
| License Check | No GPL/AGPL/SSPL (copyleft risk) |
| Boundary Impact | RED items blocked from auto-generation |
| Compliance Alignment | Must not weaken existing compliance posture |
| GOTCHA Fit | Must map to Goal/Tool/Arg/Context/HardPrompt |
| Duplicate Detection | Content hash dedup (similarity > 0.85) |
| Budget Cap | Max 10 auto-solutions per PI |
| Build Gates | All existing security gates (SAST, deps, secrets, CUI) |
| Marketplace Publish | 7-gate marketplace pipeline |

---

## Creative Engine — Customer-Centric Feature Discovery (Phase 58)

### Overview
Automates competitor gap analysis, customer pain point discovery, and feature opportunity scouting from public review sites, community forums, and GitHub issues. Outputs ranked feature specs with justification. Separate from Innovation Engine — different domain (customer voice vs. technical signals), different scoring, different sources (D351).

### Pipeline
```
DISCOVER → EXTRACT → SCORE → RANK → GENERATE
```

1. **DISCOVER** — Auto-discover competitors from category pages; scan review sites, forums, GitHub issues
2. **EXTRACT** — Extract pain points from raw signals via deterministic keyword matching + sentiment detection (D354)
3. **SCORE** — 3-dimension composite score: pain_frequency(0.40) + gap_uniqueness(0.35) + effort_to_impact(0.25)
4. **RANK** — Deduplicate, cluster, prioritize by composite score; detect trends (velocity/acceleration)
5. **GENERATE** — Template-based feature specs with justification, competitive analysis, user quotes (D356)

### Commands
```bash
# Full pipeline
python tools/creative/creative_engine.py --run --json
python tools/creative/creative_engine.py --run --domain "proposal management" --json

# Individual stages
python tools/creative/creative_engine.py --discover --domain "proposal management" --json
python tools/creative/creative_engine.py --scan --all --json
python tools/creative/creative_engine.py --extract --json
python tools/creative/creative_engine.py --score --json
python tools/creative/creative_engine.py --rank --top-k 20 --json
python tools/creative/creative_engine.py --generate --json

# Status
python tools/creative/creative_engine.py --status --json
python tools/creative/creative_engine.py --pipeline-report --json
python tools/creative/creative_engine.py --competitors --json
python tools/creative/creative_engine.py --trends --json
python tools/creative/creative_engine.py --specs --json

# Sub-tools
python tools/creative/source_scanner.py --scan --all --json
python tools/creative/source_scanner.py --list-sources --json
python tools/creative/competitor_discoverer.py --discover --domain "proposal management" --json
python tools/creative/competitor_discoverer.py --list --json
python tools/creative/competitor_discoverer.py --confirm --competitor-id <id> --json
python tools/creative/pain_extractor.py --extract-all --json
python tools/creative/gap_scorer.py --score-all --json
python tools/creative/gap_scorer.py --top --limit 20 --json
python tools/creative/gap_scorer.py --gaps --json
python tools/creative/trend_tracker.py --detect --json
python tools/creative/trend_tracker.py --report --json
python tools/creative/spec_generator.py --generate-all --json
python tools/creative/spec_generator.py --list --json

# Daemon mode
python tools/creative/creative_engine.py --daemon --json
```

### Architecture Decisions
- **D351:** Creative Engine is separate from Innovation Engine (different domain, scoring, sources)
- **D352:** Source adapters via function registry dict (web_scanner pattern)
- **D353:** Competitor auto-discovery is advisory-only (human must confirm)
- **D354:** Pain extraction is deterministic keyword/regex (air-gap safe)
- **D355:** 3-dimension scoring: pain_frequency(0.40) + gap_uniqueness(0.35) + effort_to_impact(0.25)
- **D356:** Feature specs are template-based (no LLM, reproducible)
- **D357:** All tables append-only except creative_competitors (UPDATE for status transitions)
- **D358:** Reuses _safe_get(), _get_db(), _now(), _audit() helpers
- **D359:** Daemon mode respects quiet hours from config
- **D360:** High-scoring signals cross-register to innovation_signals

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
4. **Gap Detection & Readiness** — 7-dimension scoring (completeness, clarity, feasibility, compliance, testability, devsecops_readiness, ai_governance_readiness), NIST gap analysis
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
- **V&V before handoff** — NEVER declare a fix/feature complete based solely on API or CLI validation. If the change affects UI/dashboard, open the browser with Playwright MCP, interact with the feature as the user would (click buttons, submit forms, watch real-time updates), take a screenshot, and confirm it works from the user's perspective BEFORE reporting completion. API passing ≠ user experience working.

---

### Agentic Application Generation (Phase 19)

ICDEV generates mini-ICDEV clone child applications. Each child app includes:
- Full GOTCHA framework (6 layers) and ATLAS workflow (without fitness step)
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
| Tenant Portal | `tools/saas/portal/app.py` | Web dashboard for tenant admin (pages: dashboard, projects, compliance, team, settings, profile, api_keys, usage, cmmc, phases, translations, oscal, prod-audit, ai-transparency, ai-accountability, code-quality, chat, audit) |
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
