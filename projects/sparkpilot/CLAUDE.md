# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with sparkpilot.

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
python tools/compliance/ssp_generator.py --project-id "sparkpilot"
python tools/compliance/poam_generator.py --project-id "sparkpilot"
python tools/compliance/stig_checker.py --project-id "sparkpilot"
python tools/compliance/sbom_generator.py --project-dir "/path/to/project"
python tools/compliance/cui_marker.py --file "/path/to/file" --marking "CUI // SP-CTI"
python tools/compliance/nist_lookup.py --control "AC-2"
python tools/compliance/control_mapper.py --activity "code.commit" --project-id "sparkpilot"
python tools/compliance/crosswalk_engine.py --control AC-2
python tools/compliance/crosswalk_engine.py --project-id "sparkpilot" --coverage
python tools/compliance/fedramp_assessor.py --project-id "sparkpilot" --baseline moderate
python tools/compliance/cmmc_assessor.py --project-id "sparkpilot" --level 2
python tools/compliance/oscal_generator.py --project-id "sparkpilot" --artifact ssp
python tools/compliance/classification_manager.py --impact-level IL4
```

### Security Commands
```bash
python tools/security/sast_runner.py --project-dir "/path"
python tools/security/dependency_auditor.py --project-dir "/path"
python tools/security/secret_detector.py --project-dir "/path"
python tools/security/container_scanner.py --image "sparkpilot:latest"
```

### AI Security Commands
```bash
python tools/security/prompt_injection_detector.py --text "input" --json
python tools/security/prompt_injection_detector.py --project-dir /path --gate --json
python tools/security/ai_telemetry_logger.py --summary --json
python tools/security/ai_telemetry_logger.py --anomalies --window-hours 24 --json
python tools/security/ai_bom_generator.py --project-id "sparkpilot" --project-dir . --json
python tools/compliance/atlas_assessor.py --project-id "sparkpilot" --json
python tools/compliance/owasp_llm_assessor.py --project-id "sparkpilot" --json
python tools/compliance/owasp_agentic_assessor.py --project-id "sparkpilot" --json
python tools/security/agent_trust_scorer.py --all --json
```

### Requirements Intake (RICOAS) Commands
```bash
python tools/requirements/intake_engine.py --project-id "sparkpilot" --customer-name "Name" --customer-org "Org" --impact-level IL4 --json
python tools/requirements/gap_detector.py --session-id "<id>" --check-security --check-compliance --json
python tools/requirements/readiness_scorer.py --session-id "<id>" --json
python tools/requirements/decomposition_engine.py --session-id "<id>" --level story --generate-bdd --json
python tools/requirements/boundary_analyzer.py --project-id "sparkpilot" --list-assessments --json
python tools/supply_chain/dependency_graph.py --project-id "sparkpilot" --build-graph --json
python tools/supply_chain/scrm_assessor.py --project-id "sparkpilot" --aggregate --json
python tools/supply_chain/cve_triager.py --project-id "sparkpilot" --sla-check --json
python tools/simulation/simulation_engine.py --project-id "sparkpilot" --create-scenario --scenario-name "Scenario" --scenario-type what_if --json
python tools/simulation/monte_carlo.py --scenario-id "<id>" --dimension schedule --iterations 10000 --json
python tools/simulation/coa_generator.py --session-id "<id>" --generate-3-coas --simulate --json
```

### DevSecOps & ZTA Commands
```bash
python tools/devsecops/profile_manager.py --project-id "sparkpilot" --assess --json
python tools/devsecops/pipeline_security_generator.py --project-id "sparkpilot" --json
python tools/devsecops/policy_generator.py --project-id "sparkpilot" --engine kyverno --json
python tools/devsecops/zta_maturity_scorer.py --project-id "sparkpilot" --all --json
python tools/compliance/nist_800_207_assessor.py --project-id "sparkpilot" --json
python tools/devsecops/service_mesh_generator.py --project-id "sparkpilot" --mesh istio --json
```

### Observability & XAI Commands
```bash
python tools/observability/shap/agent_shap.py --project-id "sparkpilot" --last-n 10 --json
python tools/observability/provenance/prov_query.py --entity-id "<id>" --direction backward --json
python tools/observability/provenance/prov_export.py --project-id "sparkpilot" --json
python tools/compliance/xai_assessor.py --project-id "sparkpilot" --json
```

### Code Intelligence Commands
```bash
python tools/analysis/code_analyzer.py --project-dir tools/ --json
python tools/analysis/code_analyzer.py --project-dir tools/ --store --json
python tools/analysis/code_analyzer.py --project-dir tools/ --trend --json
python tools/analysis/runtime_feedback.py --health --function analyze_code --json
```

### MBSE Commands
```bash
python tools/mbse/xmi_parser.py --project-id "sparkpilot" --file /path/model.xmi --json
python tools/mbse/reqif_parser.py --project-id "sparkpilot" --file /path/reqs.reqif --json
python tools/mbse/digital_thread.py --project-id "sparkpilot" auto-link --json
python tools/mbse/digital_thread.py --project-id "sparkpilot" coverage --json
python tools/mbse/model_code_generator.py --project-id "sparkpilot" --language python --output ./src
python tools/mbse/sync_engine.py --project-id "sparkpilot" detect-drift --json
python tools/mbse/des_assessor.py --project-id "sparkpilot" --project-dir /path --json
```

### Embedded Development Commands
```bash
# Natural Language to Firmware
python tools/embedded/nl_to_firmware.py --command "Blink LED every 2 seconds" --board simulator --json
python tools/embedded/nl_to_firmware.py --command "Read temperature sensor" --board esp32-s3 --json
python tools/embedded/nl_to_firmware.py --command "Send MQTT message" --board stm32f407 --deploy --json

# CMake and FreeRTOSConfig.h Generation
python tools/embedded/cmake_generator.py --board esp32-s3 --json
python tools/embedded/cmake_generator.py --board simulator --with-tinyml --json
python tools/embedded/cmake_generator.py --board stm32f407 --project-dir ./my-project --json

# Crash Analysis / Self-Healing
python tools/embedded/crash_analyzer.py --crash-type hardfault --device-id dev-001 --json
python tools/embedded/crash_analyzer.py --patterns --json
```

### Fleet Management Commands
```bash
# Device Registry
python tools/fleet/device_registry.py --register --name "my-esp32" --board esp32-s3 --json
python tools/fleet/device_registry.py --list --json
python tools/fleet/device_registry.py --heartbeat --device-id dev-001 --json
python tools/fleet/device_registry.py --health --json

# OTA Updates
python tools/fleet/ota_manager.py --deploy --firmware-id fw-001 --device-id dev-001 --json
python tools/fleet/ota_manager.py --canary --firmware-id fw-001 --group-id grp-001 --canary-pct 10 --json
python tools/fleet/ota_manager.py --status --json
```

### Edge AI / TinyML Commands
```bash
python tools/edge_ai/model_manager.py --templates --json                           # List model templates
python tools/edge_ai/model_manager.py --register --name "anomaly" --task anomaly_detection --json
python tools/edge_ai/model_manager.py --list --json
python tools/edge_ai/model_manager.py --deploy --model-id mdl-001 --device-id dev-001 --json
python tools/edge_ai/model_manager.py --inference-stats --device-id dev-001 --json
```

### Gamified Missions Commands
```bash
python tools/missions/mission_engine.py --seed --json            # Seed 7 default missions
python tools/missions/mission_engine.py --list --json            # List all missions
python tools/missions/mission_engine.py --start --mission 1 --user-id player1 --json
python tools/missions/mission_engine.py --complete --mission 1 --user-id player1 --json
python tools/missions/mission_engine.py --progress --user-id player1 --json
```

### Simulator Commands
```bash
python tools/simulator/sim_runner.py --seed --json               # Seed virtual peripherals
python tools/simulator/sim_runner.py --peripherals --json        # List available peripherals
python tools/simulator/sim_runner.py --create --user-id player1 --json  # Create session
python tools/simulator/sim_runner.py --list --json               # List sessions
python tools/simulator/sim_runner.py --status --session-id sim-001 --json
python tools/simulator/sim_runner.py --stop --session-id sim-001 --json
```

### CI/CD Commands
```bash
python tools/ci/triggers/webhook_server.py           # Start webhook server
python tools/ci/triggers/poll_trigger.py             # Start issue polling
python tools/ci/workflows/icdev_sdlc.py 123          # Run full SDLC pipeline
```


---

## Architecture: FORGE Framework

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

## sparkpilot System

### Classification

**Impact Level:** IL4
**Classification:** CUI // SP-CTI
All generated artifacts MUST include classification markings appropriate to impact level.

### Multi-Agent Architecture (11 Agents)

| Tier | Agent | Port | Role |
|------|-------|------|------|
| Core | Orchestrator | 9443 | Task routing, workflow management |
| Core | Architect | 9444 | ANVIL A/T phases, system design |
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

### MBSE Integration

Model-Based Systems Engineering: SysML XMI import, DOORS NG ReqIF import, digital thread traceability, model-to-code generation, drift detection, and DES compliance assessment.

- Import models: `xmi_parser.py`, `reqif_parser.py`
- Digital thread: `digital_thread.py` (auto-link, coverage, report)
- Code generation: `model_code_generator.py`
- Drift detection: `sync_engine.py`
- DES compliance: `des_assessor.py`, `des_report_generator.py`

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

---

## SparkPilot — AI Co-Pilot for Embedded Systems

SparkPilot makes embedded RTOS development accessible to anyone — from a beginner building their first blinking LED to a DoD engineer deploying AI-enabled firmware with full NIST compliance.

### Four-Tier Architecture

| Tier | Environment | Purpose |
|------|-------------|---------|
| **Tier 0** | Browser Simulator (WASM/JS) | FreeRTOS POSIX port in browser, virtual peripherals, no install needed |
| **Tier 1** | FreeRTOS MCU (Cortex-M, ESP32, RISC-V) | TinyML inference, MQTT telemetry, OTA updates, SparkPilot Device SDK (~8KB) |
| **Tier 2** | Edge Gateway (RPi, Jetson) | Local LLM (llama.cpp), multi-agent coordination, edge inference |
| **Tier 3** | Cloud/ICDEV™ (Bedrock, SageMaker) | Full LLM orchestration, compliance monitoring, self-healing |

### Key Capabilities

1. **Natural Language to Firmware** — "Blink LED every 2 seconds" → C code → cross-compile → deploy
2. **Browser Simulator** — FreeRTOS POSIX port in WASM with virtual LEDs, sensors, display
3. **Gamified Missions** — 7 progressive missions: Blink LED → Sensor → WiFi → MQTT → AI → Hardware → Fleet
4. **Edge AI Pipeline** — TFLite Micro integration, model OTA, inference scheduler as FreeRTOS task
5. **Self-Healing Firmware** — Crash dump analysis, auto-rollback via MCUboot, 72-hour stability window
6. **Fleet Management** — Device registry, canary deployments, health heartbeat, OTA pipeline
7. **Progressive Compliance** — Beginner Mode (clean, simple) vs Pro Mode (NIST, IEC 62443, DO-178C)
8. **Sim-to-Silicon Path** — Auto-adapt simulator code to real HAL drivers, board recommendations

### SparkPilot Device SDK

Thin C library (~8KB flash) with 3 FreeRTOS tasks:
- **MQTT Client Task** — bridge to cloud agents
- **Command Handler Task** — executes agent instructions (OTA_UPDATE, CONFIG_SET, REBOOT, DIAG_DUMP, MODEL_UPDATE, TASK_CONTROL)
- **Telemetry Reporter Task** — sends health/sensor data

Header: `sdk/include/sparkpilot_sdk.h`
Source: `sdk/src/sparkpilot_sdk.c`
Build: `sdk/CMakeLists.txt`

### SparkPilot-Specific Tools

| Tool | Path | Purpose |
|------|------|---------|
| NL-to-Firmware | `tools/embedded/nl_to_firmware.py` | Natural language command → FreeRTOS C code |
| CMake Generator | `tools/embedded/cmake_generator.py` | CMakeLists.txt + FreeRTOSConfig.h per board |
| Crash Analyzer | `tools/embedded/crash_analyzer.py` | Crash dump pattern matching, self-healing |
| Device Registry | `tools/fleet/device_registry.py` | Device registration, heartbeats, health |
| OTA Manager | `tools/fleet/ota_manager.py` | Firmware/model OTA with canary deployment |
| Model Manager | `tools/edge_ai/model_manager.py` | TinyML model lifecycle, inference tracking |
| Mission Engine | `tools/missions/mission_engine.py` | Gamified learning, XP, badges, progress |
| Simulator Runner | `tools/simulator/sim_runner.py` | POSIX/WASM simulator sessions, virtual peripherals |

### Supported Boards

| Board | Arch | Flash | RAM | Toolchain |
|-------|------|-------|-----|-----------|
| ESP32-S3 | Xtensa LX7 | 8MB | 512KB | xtensa-esp32s3-elf |
| STM32F407 | Cortex-M4F | 1MB | 192KB | arm-none-eabi |
| nRF52840 | Cortex-M4F | 1MB | 256KB | arm-none-eabi |
| RPi Pico | Cortex-M0+ | 2MB | 264KB | arm-none-eabi |
| Simulator | POSIX/Host | - | - | gcc |

### Embedded Compliance Frameworks (Pro Mode)

| Framework | Scope |
|-----------|-------|
| NIST 800-53 | Core federal baseline |
| IEC 62443 | Industrial cybersecurity |
| DO-178C | Avionics traceability |
| ISO 26262 | Automotive safety |
| IEC 62304 | Medical devices |
| MISRA C:2023 | Coding standard |
| FIPS 140-3 | Crypto modules |
| EU AI Act | Embedded AI |

### Database

`data/sparkpilot.db` — 32 tables covering:
- Core: projects, audit_trail, agents, agent_tasks, memory_entries
- Compliance: compliance_controls, compliance_evidence, sbom_entries
- Devices: devices, rtos_tasks, device_telemetry, device_commands
- Firmware: firmware_builds, firmware_deploy_log, ota_update_log
- Fleet: device_groups, fleet_canary_log, mqtt_messages
- Edge AI: ml_models, inference_telemetry
- Missions: missions, mission_completion_log, user_progress
- Simulator: simulator_sessions, simulator_session_log, virtual_peripherals
- Build: cmake_configs, board_support_packages
- Other: nl_commands, crash_dump_log, embedded_patterns

### Code Intelligence

AST-based code quality metrics, smell detection, deterministic maintainability scoring, and runtime feedback from test results.

- Code analyzer: `code_analyzer.py` (cyclomatic/cognitive complexity, nesting, params)
- Smell detection: 5 smell types (long function, deep nesting, high complexity, too many params, god class)
- Runtime feedback: `runtime_feedback.py` (test-to-source mapping)

### ANVIL Workflow

Build process follows the ANVIL methodology:
1. **Model** -- Import/validate SysML and DOORS models (M-ANVIL pre-phase)
1. **Model** -- model
2. **Architect** -- System design, component decomposition, interface contracts
3. **Trace** -- Requirements traceability matrix, compliance mapping
4. **Link** -- Wire components together, dependency injection, A2A registration
5. **Assemble** -- Build, test (TDD RED->GREEN->REFACTOR), integrate
6. **Stress_test** -- Load testing, security scanning, compliance gate checks

### Orchestration

- Prompt chains: Declarative YAML multi-step LLM reasoning (plan_critique_refine, scout_analyze_recommend)
- Dispatcher mode: Orchestrator restricted to delegation tools only (FORGE separation of concerns)
- Session purpose: Declared intent per session for NIST AU-3 audit traceability
```bash
python tools/agent/prompt_chain_executor.py --list --json
python tools/agent/prompt_chain_executor.py --chain plan_critique_refine --input "text" --project-id "proj-123" --json
python tools/agent/dispatcher_mode.py --status --project-id "proj-123" --json
python tools/agent/session_purpose.py --declare "task description" --project-id "proj-123" --json
```

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
| `data/sparkpilot.db` | Main operational DB: projects, agents, audit trail, compliance, MBSE, RICOAS, AI security, observability, DevSecOps/ZTA, code intelligence |
| `data/memory.db` | Memory system: entries, daily logs, access log |
| `data/activity.db` | Task tracking |

**Audit trail is append-only/immutable** -- no UPDATE/DELETE operations.  Satisfies NIST 800-53 AU controls.

---

## Existing Goals

| Goal | File | Purpose |
|------|------|---------|
| ANVIL Workflow | `goals/build_app.md` | 5-step build: Architect -> Trace -> Link -> Assemble -> Stress-test |
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
| Multi-Agent Orchestration | `goals/multi_agent_orchestration.md` | Prompt chains, dispatcher mode, session purpose, ANVIL critique |

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
- **This application CANNOT generate child applications** -- it is a generated child app of ICDEV™.  The agentic fitness assessor, app blueprint engine, and child app generator are intentionally excluded.

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
- **D4:** Statistical methods for pattern detection; Bedrock LLM for root cause analysis
- **D7:** Python stdlib xml.etree.ElementTree for XMI/ReqIF parsing (zero deps, air-gap safe)
- **D8:** Normalized DB tables for model elements (enables SQL joins across digital thread)
- **D9:** M-ANVIL adds Model pre-phase to ANVIL (backward compatible -- skips if no model)
- **D12:** N:M digital thread links (one block -> many code modules; one control -> many requirements)
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
