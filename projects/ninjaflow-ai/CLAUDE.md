# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with ninjaflow-ai.

---

## Quick Reference

### Commands
```bash
# Memory system
python tools/memory/memory_read.py --format markdown          # Load all memory
python tools/memory/memory_write.py --content "text" --type event  # Write to daily log + DB
python tools/memory/memory_write.py --content "text" --type fact --importance 7  # Store a fact
python tools/memory/memory_write.py --update-memory --content "text" --section user_preferences  # Update MEMORY.md
python tools/memory/memory_db.py --action search --query "keyword"   # Keyword search
python tools/memory/semantic_search.py --query "concept"             # Semantic search (requires OpenAI key)
python tools/memory/hybrid_search.py --query "query"                 # Best: combined keyword + semantic
python tools/memory/embed_memory.py --all                            # Generate embeddings for all entries
```

### Testing Commands
```bash
python tools/testing/health_check.py                 # Full system health check
python tools/testing/health_check.py --json           # JSON output
python tools/testing/test_orchestrator.py --project-dir /path/to/project
python tools/testing/e2e_runner.py --discover         # List available E2E test specs
python tools/testing/e2e_runner.py --run-all           # Execute all E2E tests
```

### Compliance Commands
```bash
python tools/compliance/ssp_generator.py --project-id "ninjaflow-ai"
python tools/compliance/poam_generator.py --project-id "ninjaflow-ai"
python tools/compliance/stig_checker.py --project-id "ninjaflow-ai"
python tools/compliance/sbom_generator.py --project-dir "/path/to/project"
python tools/compliance/cui_marker.py --file "/path/to/file" --marking "CUI // SP-CTI"
python tools/compliance/nist_lookup.py --control "AC-2"
python tools/compliance/control_mapper.py --activity "code.commit" --project-id "ninjaflow-ai"
python tools/compliance/crosswalk_engine.py --control AC-2
python tools/compliance/crosswalk_engine.py --project-id "ninjaflow-ai" --coverage
python tools/compliance/fedramp_assessor.py --project-id "ninjaflow-ai" --baseline moderate
python tools/compliance/cmmc_assessor.py --project-id "ninjaflow-ai" --level 2
python tools/compliance/oscal_generator.py --project-id "ninjaflow-ai" --artifact ssp
python tools/compliance/classification_manager.py --impact-level IL4
```

### Security Commands
```bash
python tools/security/sast_runner.py --project-dir "/path"
python tools/security/dependency_auditor.py --project-dir "/path"
python tools/security/secret_detector.py --project-dir "/path"
python tools/security/container_scanner.py --image "ninjaflow-ai:latest"
```

### AI Security Commands
```bash
python tools/security/prompt_injection_detector.py --text "input" --json
python tools/security/prompt_injection_detector.py --project-dir /path --gate --json
python tools/security/ai_telemetry_logger.py --summary --json
python tools/security/ai_telemetry_logger.py --anomalies --window-hours 24 --json
python tools/security/ai_bom_generator.py --project-id "ninjaflow-ai" --project-dir . --json
python tools/compliance/atlas_assessor.py --project-id "ninjaflow-ai" --json
python tools/compliance/owasp_llm_assessor.py --project-id "ninjaflow-ai" --json
python tools/compliance/owasp_agentic_assessor.py --project-id "ninjaflow-ai" --json
python tools/security/agent_trust_scorer.py --all --json
```

### Requirements Intake (RICOAS) Commands
```bash
python tools/requirements/intake_engine.py --project-id "ninjaflow-ai" --customer-name "Name" --customer-org "Org" --impact-level IL4 --json
python tools/requirements/gap_detector.py --session-id "<id>" --check-security --check-compliance --json
python tools/requirements/readiness_scorer.py --session-id "<id>" --json
python tools/requirements/decomposition_engine.py --session-id "<id>" --level story --generate-bdd --json
python tools/requirements/boundary_analyzer.py --project-id "ninjaflow-ai" --list-assessments --json
python tools/supply_chain/dependency_graph.py --project-id "ninjaflow-ai" --build-graph --json
python tools/supply_chain/scrm_assessor.py --project-id "ninjaflow-ai" --aggregate --json
python tools/supply_chain/cve_triager.py --project-id "ninjaflow-ai" --sla-check --json
python tools/simulation/simulation_engine.py --project-id "ninjaflow-ai" --create-scenario --scenario-name "Scenario" --scenario-type what_if --json
python tools/simulation/monte_carlo.py --scenario-id "<id>" --dimension schedule --iterations 10000 --json
python tools/simulation/coa_generator.py --session-id "<id>" --generate-3-coas --simulate --json
```

### DevSecOps & ZTA Commands
```bash
python tools/devsecops/profile_manager.py --project-id "ninjaflow-ai" --assess --json
python tools/devsecops/pipeline_security_generator.py --project-id "ninjaflow-ai" --json
python tools/devsecops/policy_generator.py --project-id "ninjaflow-ai" --engine kyverno --json
python tools/devsecops/zta_maturity_scorer.py --project-id "ninjaflow-ai" --all --json
python tools/compliance/nist_800_207_assessor.py --project-id "ninjaflow-ai" --json
python tools/devsecops/service_mesh_generator.py --project-id "ninjaflow-ai" --mesh istio --json
```

### Observability & XAI Commands
```bash
python tools/observability/shap/agent_shap.py --project-id "ninjaflow-ai" --last-n 10 --json
python tools/observability/provenance/prov_query.py --entity-id "<id>" --direction backward --json
python tools/observability/provenance/prov_export.py --project-id "ninjaflow-ai" --json
python tools/compliance/xai_assessor.py --project-id "ninjaflow-ai" --json
```

### Code Intelligence Commands
```bash
python tools/analysis/code_analyzer.py --project-dir tools/ --json
python tools/analysis/code_analyzer.py --project-dir tools/ --store --json
python tools/analysis/code_analyzer.py --project-dir tools/ --trend --json
python tools/analysis/runtime_feedback.py --health --function analyze_code --json
```

### CI/CD Commands
```bash
python tools/ci/triggers/webhook_server.py           # Start webhook server
python tools/ci/triggers/poll_trigger.py             # Start issue polling
python tools/ci/workflows/icdev_sdlc.py 123          # Run full SDLC pipeline
```

### Dashboard
```bash
python tools/dashboard/app.py                        # Start web dashboard on port 5000
```


---

## Architecture: GOTCHA Framework

This is a 6-layer agentic system.  The AI (you) is the orchestration layer -- you read goals, call tools, apply args, reference context, and use hard prompts.  You never execute work directly; you delegate to deterministic Python scripts.

**Why:** LLMs are probabilistic.  Business logic must be deterministic.  90% accuracy/step = ~59% over 5 steps.  Separation of concerns fixes this.

### The 6 Layers

| Layer | Directory | Role |
|-------|-----------|------|
| **Goals** | `goals/` | Process definitions -- what to achieve, which tools to use, expected outputs, edge cases |
| **Orchestration** | *(you)* | Read goal -> decide tool order -> apply args -> reference context -> handle errors |
| **Tools** | `tools/` | Python scripts, one job each.  Deterministic.  Don't think, just execute. |
| **Args** | `args/` | YAML/JSON behavior settings (themes, modes, schedules).  Change behavior without editing goals/tools |
| **Context** | `context/` | Static reference material (tone rules, writing samples, ICP descriptions, case studies) |
| **Hard Prompts** | `hardprompts/` | Reusable LLM instruction templates (outline->post, rewrite-in-voice, summarize) |

### Key Files

- `goals/manifest.md` -- Index of all goal workflows.  Check before starting any task.
- `tools/manifest.md` -- Master list of all tools.  Check before writing a new script.
- `memory/MEMORY.md` -- Curated long-term facts/preferences, read at session start.
- `memory/logs/YYYY-MM-DD.md` -- Daily session logs.
- `.env` -- API keys and environment variables.
- `.tmp/` -- Disposable scratch work.  Never store important data here.

### Memory System Architecture

Dual storage: markdown files (human-readable) + SQLite databases (searchable).

**Databases:**
- `data/memory.db` -- `memory_entries` (with embeddings), `daily_logs`, `memory_access_log`
- `data/activity.db` -- `tasks` table for tracking

**Memory types:** fact, preference, event, insight, task, relationship

**Search ranking:** Hybrid search uses 0.7 * BM25 (keyword) + 0.3 * semantic (vector).  Configurable via `--bm25-weight` and `--semantic-weight` flags.

**Embeddings:** OpenAI text-embedding-3-small (1536 dims), stored as BLOBs in SQLite.

---

## How to Operate

1. **Check goals first** -- Read `goals/manifest.md` before starting a task.  If a goal exists, follow it.
2. **Check tools first** -- Read `tools/manifest.md` before writing new code.  If you create a new tool, add it to the manifest.
3. **When tools fail** -- Read the error, fix the tool, update the goal with what you learned (rate limits, batching, timing).
4. **Goals are living docs** -- Update when better approaches emerge.  Never modify/create goals without explicit permission.
5. **When stuck** -- Explain what is missing and what you need.  Do not guess or invent capabilities.

### Session Start Protocol

1. Read `memory/MEMORY.md` for long-term context
2. Read today's daily log (`memory/logs/YYYY-MM-DD.md`)
3. Read yesterday's log for continuity
4. Or run: `python tools/memory/memory_read.py --format markdown`

---

## ninjaflow-ai System

### Classification

**Impact Level:** IL4
**Classification:** CUI // SP-CTI
All generated artifacts MUST include classification markings appropriate to impact level.

### Multi-Agent Architecture (11 Agents)

| Tier | Agent | Port | Role |
|------|-------|------|------|
| Core | Orchestrator | 9443 | Task routing, workflow management |
| Core | Architect | 9444 | ATLAS A/T phases, system design |
| Domain | Builder | 9445 | TDD code gen (RED->GREEN->REFACTOR) |
| Support | Knowledge | 9449 | Self-healing patterns, recommendations |
| Support | Monitor | 9450 | Log analysis, metrics, alerts, health checks |
| Domain | Compliance | 9446 | ATO artifacts, 9-framework compliance |
| Domain | Security | 9447 | SAST, dep audit, secret detection |
| Domain | Requirements_analyst | 9453 | Conversational intake, gap detection, SAFe decomposition |
| Domain | Supply_chain | 9454 | Dependency graph, SBOM aggregation, ISA lifecycle, CVE triage |
| Domain | Simulation | 9455 | Digital Program Twin, Monte Carlo, COA generation |
| Domain | Devsecops_zta | 9457 | DevSecOps pipeline security, Zero Trust, policy-as-code |

Agents communicate via **A2A protocol** (JSON-RPC 2.0 over mutual TLS within K8s).  Each publishes an Agent Card at `/.well-known/agent.json`.

### MCP Servers (11 stdio servers for Claude Code)

| Server | Tools |
|--------|-------|
| architect | design_system, decompose, interface_contract |
| builder | scaffold, generate_code, write_tests, run_tests, lint, format |
| compliance | ssp_generate, poam_generate, stig_check, sbom_generate, cui_mark, control_map, nist_lookup |
| devsecops | devsecops_profile_create, zta_maturity_score, pipeline_security_generate, policy_generate, service_mesh_generate |
| knowledge | search_knowledge, add_pattern, get_recommendations, self_heal |
| monitor | log_analyze, health_check, metrics_query, alert_manage |
| core | project_create, project_list, project_status, task_dispatch, agent_status |
| requirements | create_intake_session, process_intake_turn, detect_gaps, score_readiness, decompose_requirements |
| security | sast_scan, dep_audit, secret_detect, container_scan |
| simulation | create_scenario, run_simulation, run_monte_carlo, generate_coas, compare_coas |
| supply-chain | add_vendor, build_dependency_graph, assess_scrm, triage_cve, manage_isa |

### Compliance Frameworks Supported

| Framework | Description |
|-----------|-------------|
| NIST 800-53 Rev 5 | Federal information systems baseline |
| FedRAMP Moderate/High | Cloud services authorization |
| NIST 800-171 | CUI protection requirements |
| CMMC Level 2/3 | Cybersecurity maturity certification |
| DoD CSSP (DI 8530.01) | Cybersecurity service provider |
| CISA Secure by Design | Secure development principles |
| IEEE 1012 IV&V | Independent verification and validation |
| DoDI 5000.87 DES | Digital engineering strategy |

**Control Crosswalk:** Implementing one NIST 800-53 control auto-populates FedRAMP, CMMC, and 800-171 status via the crosswalk engine.

### RICOAS — Requirements Intake, COA & Approval System

AI-driven conversational requirements intake with gap detection, SAFe decomposition, boundary impact assessment, supply chain intelligence, and Digital Program Twin simulation.

- Requirements intake: `intake_engine.py` (5-stage pipeline)
- Gap detection: `gap_detector.py`, `readiness_scorer.py` (7-dimension scoring)
- Decomposition: `decomposition_engine.py` (SAFe hierarchy with BDD)
- Boundary analysis: `boundary_analyzer.py` (4-tier ATO impact: GREEN/YELLOW/ORANGE/RED)
- Supply chain: `dependency_graph.py`, `scrm_assessor.py`, `cve_triager.py`
- Simulation: `simulation_engine.py`, `monte_carlo.py`, `coa_generator.py`

### DevSecOps & Zero Trust Architecture

DevSecOps pipeline security with policy-as-code (Kyverno/OPA), service mesh generation, and NIST SP 800-207 Zero Trust maturity scoring across 7 pillars.

- Profile management: `profile_manager.py` (5 maturity levels)
- Pipeline security: `pipeline_security_generator.py`
- Policy-as-code: `policy_generator.py` (Kyverno/OPA)
- ZTA maturity: `zta_maturity_scorer.py` (7-pillar DoD ZTA Strategy)
- NIST 800-207: `nist_800_207_assessor.py`
- Service mesh: `service_mesh_generator.py` (Istio/Linkerd)

### AI Security

MITRE ATLAS threat defense, OWASP LLM Top 10, prompt injection detection, AI telemetry with privacy-preserving hashing, and agentic security (behavioral drift, tool chain validation, trust scoring).

- Prompt injection: `prompt_injection_detector.py` (5 detection categories)
- AI telemetry: `ai_telemetry_logger.py` (SHA-256 hashed prompts/responses)
- ATLAS: `atlas_assessor.py`, `atlas_red_team.py`
- OWASP: `owasp_llm_assessor.py`, `owasp_agentic_assessor.py`
- Trust scoring: `agent_trust_scorer.py`, `tool_chain_validator.py`

### Observability & Explainable AI

Distributed tracing (OTel+SQLite), W3C PROV provenance, AgentSHAP tool attribution, and XAI compliance assessment.

- Tracing: Dual-mode tracer (OTel production, SQLite air-gapped)
- Provenance: `prov_query.py`, `prov_export.py` (W3C PROV-AGENT)
- Attribution: `agent_shap.py` (Monte Carlo Shapley values)
- XAI assessment: `xai_assessor.py` (10 compliance checks)

### Code Intelligence

AST-based code quality metrics, smell detection, deterministic maintainability scoring, and runtime feedback from test results.

- Code analyzer: `code_analyzer.py` (cyclomatic/cognitive complexity, nesting, params)
- Smell detection: 5 smell types (long function, deep nesting, high complexity, too many params, god class)
- Runtime feedback: `runtime_feedback.py` (test-to-source mapping)

### ATLAS Workflow

Build process follows the ATLAS methodology:
1. **Architect** -- System design, component decomposition, interface contracts
2. **Trace** -- Requirements traceability matrix, compliance mapping
3. **Link** -- Wire components together, dependency injection, A2A registration
4. **Assemble** -- Build, test (TDD RED->GREEN->REFACTOR), integrate
5. **Stress_test** -- Load testing, security scanning, compliance gate checks

### Testing Framework

**Testing Architecture (7-step pipeline):**
1. **py_compile** -- Python syntax validation
2. **Ruff** -- Ultra-fast Python linter
3. **pytest** (tests/) -- Unit/integration tests with coverage
4. **behave/Gherkin** (features/) -- BDD scenario tests
5. **Bandit** -- SAST security scan
6. **Playwright MCP** (.claude/commands/e2e/*.md) -- Browser automation E2E tests
7. **Security + Compliance gates** -- CUI markings, STIG, secret detection

### Database

| Database | Purpose |
|----------|---------|
| `data/icdev.db` | Main operational DB: projects, agents, audit trail, compliance, RICOAS, AI security, observability, DevSecOps/ZTA, code intelligence |
| `data/memory.db` | Memory system: entries, daily logs, access log |
| `data/activity.db` | Task tracking |

**Audit trail is append-only/immutable** -- no UPDATE/DELETE operations.  Satisfies NIST 800-53 AU controls.

---

## Existing Goals

| Goal | File | Purpose |
|------|------|---------|
| ATLAS Workflow | `goals/build_app.md` | 5-step build: Architect -> Trace -> Link -> Assemble -> Stress-test |
| TDD Workflow | `goals/tdd_workflow.md` | RED->GREEN->REFACTOR cycle with Cucumber/Gherkin |
| Compliance Workflow | `goals/compliance_workflow.md` | Generate SSP, POAM, STIG, SBOM, CUI markings |
| Security Scan | `goals/security_scan.md` | SAST, dependency audit, secret detection, container scan |
| Deploy Workflow | `goals/deploy_workflow.md` | IaC generation, pipeline, staging, production deploy |
| Monitoring | `goals/monitoring.md` | Log analysis, metrics, alerts, health checks |
| Self-Healing | `goals/self_healing.md` | Pattern detection, root cause analysis, auto-remediation |
| Agent Management | `goals/agent_management.md` | A2A agent lifecycle, registration, health |
| Integration Testing | `goals/integration_testing.md` | Multi-layer testing: unit, BDD, E2E (Playwright), gates |
| Maintenance Audit | `goals/maintenance_audit.md` | Dependency scanning, vulnerability checking, SLA enforcement |
| Requirements Intake (RICOAS) | `goals/requirements_intake.md` | AI-driven conversational intake, gap detection, SAFe decomposition |
| Boundary & Supply Chain | `goals/boundary_supply_chain.md` | ATO boundary impact, supply chain dependency graph, CVE triage |
| Digital Program Twin Simulation | `goals/simulation_engine.md` | 6-dimension what-if simulation, Monte Carlo, COA generation |
| DevSecOps Workflow | `goals/devsecops_workflow.md` | DevSecOps profile, pipeline security, policy-as-code |
| Zero Trust Architecture | `goals/zero_trust_architecture.md` | ZTA 7-pillar maturity, NIST 800-207, service mesh |
| MOSA Workflow | `goals/mosa_workflow.md` | DoD MOSA modularity analysis, ICD/TSP generation |
| Observability & XAI | `goals/observability_traceability_xai.md` | Distributed tracing, provenance, AgentSHAP, XAI assessment |
| AI Transparency | `goals/ai_transparency.md` | Model/system cards, AI inventory, fairness, confabulation detection |
| AI Accountability | `goals/ai_accountability.md` | Oversight plans, CAIO, appeals, incident response, ethics reviews |
| OWASP Agentic Security | `goals/owasp_agentic_security.md` | Behavioral drift, tool chain validation, trust scoring, RBAC |
| Code Intelligence | `goals/code_intelligence.md` | AST metrics, smell detection, maintainability scoring |

---

## Guardrails

- Always check `tools/manifest.md` before writing a new script
- Verify tool output format before chaining into another tool
- Do not assume APIs support batch operations -- check first
- When a workflow fails mid-execution, preserve intermediate outputs before retrying
- Read the full goal before starting a task -- do not skim
- Audit trail is append-only -- NEVER add UPDATE/DELETE operations to audit tables
- Never store secrets in code or config -- use secrets manager or K8s secrets
- All containers must run as non-root with read-only root filesystem
- All generated artifacts MUST include classification markings appropriate to impact level
- SBOM must be regenerated on every build
- When implementing a NIST 800-53 control, always call crosswalk engine to auto-populate FedRAMP/CMMC/800-171 status
- Security gates block on: CAT1 STIG findings, critical/high vulnerabilities, failed tests, missing markings
- AI Security gates block on: prompt injection defense inactive, AI telemetry disabled, AI BOM missing, ATLAS coverage < 80%
- ZTA gates block on: maturity < Advanced for IL4+, mTLS not enforced with service mesh, no default-deny NetworkPolicy
- RICOAS gates block on: readiness score < 0.7, unresolved critical gaps, RED requirements without alternative COAs
- Observability gates block on: tracing not active, provenance graph empty, XAI assessment not completed
- Code Quality gates block on: average cyclomatic complexity > 25
- **This application CANNOT generate child applications** -- it is a generated child app of ICDEV.  The agentic fitness assessor, app blueprint engine, and child app generator are intentionally excluded.

### Cloud Service Provider Integration

**Target:** AWS (us-gov-west-1)
**MCP Servers:**
- @aws/core-mcp-server
- @aws/aws-api-mcp-server
- @aws/cdk-mcp-server
- @aws/terraform-mcp-server
- @aws/cloudformation-mcp-server
- @aws/iam-mcp-server
- @aws/well-architected-security-mcp-server
- @aws/cloudwatch-mcp-server
- @aws/cloudtrail-mcp-server
- @aws/cost-explorer-mcp-server
- @aws/aws-documentation-mcp-server
- @aws/aws-knowledge-mcp-server

---

## Key Architecture Decisions

- **D1:** SQLite for internal operational data (zero-config portability)
- **D2:** Stdio for MCP (Claude Code); HTTPS+mTLS for A2A (K8s inter-agent)
- **D5:** CUI markings applied at generation time (inline, not post-processing)
- **D6:** Audit trail is append-only/immutable (no UPDATE/DELETE -- NIST AU compliance)
- **D3:** Flask over FastAPI (simpler, fewer deps, auditable SSR, smaller STIG surface)
- **D4:** Statistical methods for pattern detection; Bedrock LLM for root cause analysis
- **D21:** Readiness scoring uses deterministic weighted average (reproducible, not probabilistic)
- **D22:** Monte Carlo uses Python stdlib random (zero deps, air-gap safe)
- **D27:** Supply chain graph stored as SQL adjacency list (no graph DB needed)
- **D117:** DevSecOps/ZTA Agent with hard veto on pipeline_configuration and zero_trust_policy
- **D120:** ZTA maturity model uses DoD 7-pillar scoring (Traditional -> Advanced -> Optimal)
- **D215:** Prompt injection detector uses 5 detection categories
- **D216:** AI telemetry hashes prompts/responses with SHA-256 (privacy-preserving audit)
- **D280:** Pluggable Tracer ABC: OTelTracer (production), SQLiteTracer (air-gapped), NullTracer (fallback)
- **D287:** PROV-AGENT provenance in 3 append-only SQLite tables (W3C PROV standard)
- **D331:** Code quality metrics are read-only, advisory-only -- never modifies source files
- **D52:** This is a generated child app -- grandchild app generation is disabled by design

---

## Continuous Improvement

Every failure strengthens the system: identify what broke -> fix the tool -> test it -> update the goal -> next run succeeds automatically.

Be direct.  Be reliable.  Get it done.
