# [TEMPLATE: CUI // SP-CTI]

# Goal: OWASP Agentic AI Security (Phase 45)

## Standards

- OWASP Agentic AI Threats v1.1
- OWASP Securing Agentic Applications v1.0
- OWASP MCP Security Guide v1.0
- NIST 800-53 Rev 5: RA-3 (Risk Assessment), SA-11 (Developer Testing), SI-4 (System Monitoring), AU-6 (Audit Record Review)

## Purpose

Close 8 security gaps identified from OWASP agentic AI security analysis that are not covered by Phase 37 (MITRE ATLAS Integration). Phase 37 addresses static AI threats (prompt injection, model poisoning, supply chain attacks). This goal addresses **runtime agentic behavior** -- behavioral drift, tool chain abuse, output safety, dynamic trust, per-tool authorization, behavioral red teaming, and formal threat modeling.

**Why this matters:** Agentic AI systems introduce risks beyond traditional LLM threats. Agents make autonomous decisions, chain tool calls, produce outputs that flow into downstream systems, and evolve behavior over time. OWASP's agentic AI guidance specifically targets these emergent risks: an agent that passes all Phase 37 static checks can still drift into unsafe behavior at runtime, abuse tool chains in unexpected sequences, or leak classified data in generated outputs.

---

## When to Use

- After Phase 37 (MITRE ATLAS Integration) is complete and operational
- When adding new agents or MCP servers to the ICDEV multi-agent system
- Before ATO submission for systems that include agentic AI components
- After security incidents involving unexpected agent behavior or output
- When child applications (Phase 36) report anomalous behavioral patterns
- During DevSecOps maturity assessment (Phase 24) for agentic systems
- When configuring Zero Trust policies (Phase 25) for agent-to-agent communication

---

## Prerequisites

- [ ] Phase 37 complete (prompt injection detection, AI telemetry, ATLAS assessment operational)
- [ ] ICDEV database initialized (`python tools/db/init_icdev_db.py`)
- [ ] Configuration: `args/owasp_agentic_config.yaml` (behavioral drift, tool chain, output validation, trust scoring, MCP authorization settings)
- [ ] Threat catalog: `context/compliance/owasp_agentic_threats.json` (OWASP T1-T17 threat definitions)
- [ ] Security gates configured: `args/security_gates.yaml` (owasp_agentic section)
- [ ] AI telemetry active (`ai_telemetry` table populated with baseline data)

---

## Workflow

### Step 1: Behavioral Drift Detection (Gap 1)

Monitor agent behavior against statistical baselines and alert when agents deviate from established operational patterns.

**Tool:** `tools/security/ai_telemetry_logger.py --drift`

**Config:** `args/owasp_agentic_config.yaml` -> `behavioral_drift`

**How it works:**
- Reads from `ai_telemetry` table to build per-agent behavioral baselines (tool call frequency, token volume, error rate, latency distribution)
- Computes z-score for each metric against a rolling 7-day window
- Z-score > 2.0 (configurable) triggers drift alert; z-score > 3.0 triggers ISSO notification

**CLI:**
```bash
python tools/security/ai_telemetry_logger.py --drift --project-id "proj-123" --json
python tools/security/ai_telemetry_logger.py --drift --agent-id "builder-agent" --window-days 14 --json
```

**Output:** Drift events stored in `ai_telemetry` table with `event_type: behavioral_drift` (append-only, D6).

**ADR:** D257 -- Behavioral drift uses z-score statistical detection on existing telemetry data (no additional data collection, air-gap safe, deterministic)

---

### Step 2: Tool Chain Validation (Gap 2)

Validate that agent tool call sequences follow declared rules and detect unauthorized or anomalous tool chain patterns.

**Tool:** `tools/security/tool_chain_validator.py`

**Config:** `args/owasp_agentic_config.yaml` -> `tool_chain`

**What it validates:**

| Rule Type | Example | Action |
|-----------|---------|--------|
| Sequence enforcement | `scaffold` must precede `generate_code` | Block if violated |
| Forbidden sequences | `sast_runner` -> `rollback` (SAST bypass) | Block + alert ISSO |
| Rate limits | Max 50 tool calls per agent per minute | Throttle + warn |
| Depth limits | Max 10 chained tool calls per request | Block at limit |

**How it works:**
- Declarative YAML rules define allowed/forbidden tool sequences
- Sliding window tracks per-agent tool call history
- Violations written to `tool_chain_events` table (append-only, D6)

**CLI:**
```bash
python tools/security/tool_chain_validator.py --validate --project-id "proj-123" --json
python tools/security/tool_chain_validator.py --rules --json   # Show configured rules
```

**ADR:** D258 -- Tool chain validation uses declarative YAML rules (D26 pattern), no LLM required, air-gap safe

---

### Step 3: Output Content Safety (Gap 3)

Validate agent-generated outputs for classification leaks, toxic content, and policy violations before delivery to users or downstream systems.

**Tool:** `tools/security/agent_output_validator.py`

**Config:** `args/owasp_agentic_config.yaml` -> `output_validation`

**What it checks:**

| Check | Description | Severity |
|-------|-------------|----------|
| Classification leak | CUI/SECRET content in unclassified output | Blocking |
| PII detection | SSN, email, phone patterns in output | Blocking |
| Prompt echo | System prompt or instructions leaked in output | Warning |
| Hallucination markers | Confidence qualifiers without evidence | Warning |
| Harmful content | Patterns matching unsafe instructions | Blocking |

**How it works:**
- Regex-based pattern matching for classification markers and PII (deterministic, air-gap safe)
- Classification leak detection compares output classification against channel max IL (extends D135 response filter)
- Violations written to `agent_output_violations` table (append-only, D6)

**CLI:**
```bash
python tools/security/agent_output_validator.py --validate --text "output text" --classification CUI --json
python tools/security/agent_output_validator.py --scan --project-id "proj-123" --json
```

**ADR:** D259 -- Output validation uses regex patterns and classification comparison (deterministic, no LLM, extends D135 response filter pattern)

---

### Step 4: Formal Threat Model (Gap 4)

Maintain a living STRIDE + OWASP agentic threat model for the ICDEV multi-agent system.

**Goal:** `goals/agentic_threat_model.md`

**What it covers:**
- STRIDE analysis (Spoofing, Tampering, Repudiation, Information Disclosure, Denial of Service, Elevation of Privilege) applied to all 15 agents
- OWASP Agentic AI Threats T1-T17 mapped to ICDEV agent architecture
- Attack surface inventory: MCP servers, A2A protocol, dashboard endpoints, remote gateway channels
- Review cadence: quarterly, or after adding new agents/MCP servers

**CLI:**
```bash
# Threat model is a goal document, not a tool -- reviewed and updated manually
# Validation of threat model freshness:
python tools/compliance/owasp_agentic_assessor.py --project-id "proj-123" --check-threat-model --json
```

**ADR:** D263 -- Threat model is a living document (not auto-generated) with quarterly review cadence; machine-checkable freshness via last-reviewed timestamp

---

### Step 5: Dynamic Trust Scoring (Gap 5)

Assign and maintain per-agent trust scores that decay on violations and recover on clean operation.

**Tool:** `tools/security/agent_trust_scorer.py`

**Config:** `args/owasp_agentic_config.yaml` -> `trust_scoring`

**Trust levels:**

| Level | Score Range | Permissions |
|-------|------------|-------------|
| Untrusted | < 0.30 | Read-only, no tool execution, ISSO alert |
| Degraded | 0.30 - 0.49 | Restricted tool set, all actions logged |
| Cautious | 0.50 - 0.69 | Normal tools, enhanced monitoring |
| Normal | >= 0.70 | Full permissions per role |

**Score dynamics:**
- Exponential decay on violation: `score = score * decay_factor` (default 0.8 per violation)
- Linear recovery on clean operation: `score = min(1.0, score + recovery_rate)` (default +0.01 per clean hour)
- Trust scores stored in `agent_trust_scores` table (append-only, D6)

**CLI:**
```bash
python tools/security/agent_trust_scorer.py --score --agent-id "builder-agent" --json
python tools/security/agent_trust_scorer.py --all --json
python tools/security/agent_trust_scorer.py --history --agent-id "builder-agent" --json
```

**ADR:** D260 -- Dynamic trust uses exponential decay + linear recovery (deterministic, no LLM, consistent with self-healing confidence scoring pattern)

---

### Step 6: MCP Per-Tool Authorization (Gap 6)

Enforce role-based access control at the individual MCP tool level, not just at the server level.

**Tool:** `tools/security/mcp_tool_authorizer.py`

**Config:** `args/owasp_agentic_config.yaml` -> `mcp_authorization`

**Role-to-tool matrix (extends D172 dashboard RBAC):**

| Role | Allowed Tools | Denied Tools |
|------|--------------|--------------|
| admin | All tools | None |
| pm | project_*, task_*, search_* | terraform_*, deploy_*, rollback |
| developer | scaffold, generate_code, write_tests, run_tests, lint | terraform_apply, rollback, ssp_generate |
| isso | ssp_generate, stig_check, sbom_generate, control_map | generate_code, terraform_apply |
| co | project_status, search_knowledge | All write operations |

**How it works:**
- Authorization matrix stored in YAML (D26 pattern)
- Checked at MCP request dispatch before tool execution
- Denied calls logged to audit trail with requester identity and denied tool

**CLI:**
```bash
python tools/security/mcp_tool_authorizer.py --check --role developer --tool terraform_apply --json
python tools/security/mcp_tool_authorizer.py --matrix --json   # Show full authorization matrix
python tools/security/mcp_tool_authorizer.py --audit --json    # Show recent denials
```

**ADR:** D261 -- Per-tool authorization uses declarative YAML matrix (D26 pattern), checked at dispatch, extends existing RBAC (D172)

---

### Step 7: Behavioral Red Teaming (Gap 7)

Conduct adversarial testing targeting agentic behavior patterns (not just LLM responses). Extends Phase 37 red teaming (D219) with 6 behavioral techniques.

**Tool:** `tools/security/atlas_red_team.py --behavioral`

**Behavioral red team techniques:**

| ID | Technique | What It Tests |
|----|-----------|---------------|
| BRT-001 | Goal Hijacking | Agent pursues attacker-defined goal instead of assigned task |
| BRT-002 | Tool Chain Manipulation | Agent executes forbidden tool sequences via indirect prompting |
| BRT-003 | Privilege Escalation | Agent attempts to invoke tools above its trust/role level |
| BRT-004 | Memory Poisoning | Adversarial content injected into agent memory/context |
| BRT-005 | Cascading Failure | Single agent failure propagates through A2A communication |
| BRT-006 | Output Weaponization | Agent generates outputs designed to exploit downstream consumers |

**Safety:** Behavioral red teaming is **opt-in only** (D219 pattern). Static checks only -- no actual exploitation. Runs against test fixtures, never production.

**CLI:**
```bash
python tools/security/atlas_red_team.py --behavioral --project-id "proj-123" --json
python tools/security/atlas_red_team.py --behavioral --technique BRT-003 --project-id "proj-123" --json
```

**Output:** Findings stored in `atlas_red_team_results` table (append-only, D6) with technique, severity, evidence, remediation.

**ADR:** D262 -- Behavioral red teaming uses static analysis checks (not live exploitation), opt-in only (D219), extends existing red team infrastructure

---

### Step 8: OWASP Agentic Assessment (Gap 8)

Comprehensive assessment against OWASP Agentic AI Threats v1.1 using BaseAssessor pattern with crosswalk to NIST 800-53.

**Tool:** `tools/compliance/owasp_agentic_assessor.py`

**Catalog:** `context/compliance/owasp_agentic_threats.json` (17 threats: T1-T17)

**17 automated checks:**
- T1 (Unauthorized Actions) -- agent trust scoring active, per-tool authorization enforced
- T2 (Excessive Autonomy) -- tool chain depth limits configured, human-in-the-loop gates present
- T3 (Insufficient Monitoring) -- AI telemetry active, behavioral drift detection enabled
- T4 (Prompt Injection) -- prompt injection detector operational (delegates to Phase 37)
- T5 (Insecure Output) -- output content safety validator active
- T6-T17 -- additional checks mapped to OWASP agentic threat taxonomy

**Crosswalk:** Through NIST 800-53 US hub (D111) via RA-3, SA-11, SI-4, AU-6.

**CLI:**
```bash
python tools/compliance/owasp_agentic_assessor.py --project-id "proj-123" --json
python tools/compliance/owasp_agentic_assessor.py --project-id "proj-123" --gate
python tools/compliance/owasp_agentic_assessor.py --project-id "proj-123" --check-threat-model --json
```

**Output:** Assessment results stored in `owasp_agentic_assessments` table (append-only, D6). Gate evaluation via `--gate` flag.

**ADR:** D264 -- OWASP Agentic assessor uses BaseAssessor pattern (D116) with 17 automated checks; crosswalks through NIST 800-53 US hub (D111)

---

## Security Gates

| Gate | Condition | Severity |
|------|-----------|----------|
| `behavioral_drift_unmonitored` | Behavioral drift detection not active | Blocking |
| `tool_chain_validation_disabled` | Tool chain validation rules not configured | Blocking |
| `output_safety_not_enforced` | Output content safety validator not active | Blocking |
| `agent_trust_scoring_disabled` | Dynamic trust scoring not enabled | Blocking |
| `mcp_authorization_missing` | Per-tool MCP authorization not configured | Blocking |
| `threat_model_expired` | Formal threat model not reviewed in 90 days | Blocking |
| `owasp_agentic_critical_gap` | Critical OWASP agentic threat unmitigated | Blocking |
| `behavioral_red_team_not_run` | IL5+ project without behavioral red team results | Warning |
| `trust_score_below_threshold` | Any agent trust score below 0.50 | Warning |
| `tool_chain_violations_detected` | Tool chain violations in last 24 hours | Warning |

---

## Integration Points

| Phase | Integration | How |
|-------|------------|-----|
| Phase 37 (ATLAS) | Extends prompt injection, telemetry, red teaming | Steps 1, 3, 7 build on Phase 37 tools; OWASP agentic crosswalks through same NIST US hub |
| Phase 36 (Evolutionary Intelligence) | Trust scoring for child apps | Child-reported behaviors factor into parent trust scoring; genome propagation requires trust >= 0.70 |
| Phase 24 (DevSecOps) | Pipeline security | Tool chain validation integrated into DevSecOps pipeline stages; output validation as post-generation gate |
| Phase 25 (ZTA) | Zero Trust per-tool auth | MCP per-tool authorization extends ZTA 7-pillar model (User Identity + Device Security pillars) |
| Phase 28 (Remote Gateway) | Remote command trust | Gateway commands factor into agent trust scoring; untrusted agents cannot receive remote commands |

---

## Troubleshooting

**Behavioral drift alerts firing on normal workload changes:**
```bash
# Increase z-score threshold or extend baseline window
# Edit args/owasp_agentic_config.yaml -> behavioral_drift.z_score_threshold: 3.0
# Or extend window: behavioral_drift.baseline_window_days: 14
python tools/security/ai_telemetry_logger.py --drift --agent-id "builder-agent" --window-days 14 --json
```

**Tool chain validation blocking legitimate sequences:**
```bash
# Add the sequence to allowed rules
# Edit args/owasp_agentic_config.yaml -> tool_chain.allowed_sequences
python tools/security/tool_chain_validator.py --rules --json   # Review current rules
```

**Agent trust score stuck at low value:**
```bash
# Check violation history, then verify clean operation period
python tools/security/agent_trust_scorer.py --history --agent-id "builder-agent" --json
# Manual trust reset requires ISSO approval (logged to audit trail)
python tools/security/agent_trust_scorer.py --reset --agent-id "builder-agent" --approved-by "isso@mil" --json
```

**OWASP agentic assessment showing gaps after Phase 37 is complete:**
```bash
# Phase 45 covers runtime gaps not addressed by Phase 37 static checks
# Run the full assessment to see which specific gaps remain
python tools/compliance/owasp_agentic_assessor.py --project-id "proj-123" --json
```

---

## Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D257 | Z-score behavioral drift on existing telemetry | No additional data collection; air-gap safe; deterministic statistical detection |
| D258 | Declarative YAML tool chain rules (D26 pattern) | Add/remove rules without code changes; auditable; air-gap safe |
| D259 | Regex-based output validation (extends D135) | Deterministic; no LLM required; consistent with response filter pattern |
| D260 | Exponential decay + linear recovery for trust | Penalizes violations quickly, rewards clean operation gradually; consistent with self-healing confidence |
| D261 | Per-tool YAML authorization matrix (D26 + D172) | Extends existing RBAC; declarative; no code changes to add roles/tools |
| D262 | Static behavioral red team checks (D219 pattern) | Opt-in only; no live exploitation; extends existing red team infrastructure |
| D263 | Living threat model with machine-checkable freshness | Human-authored for accuracy; automated staleness check for enforcement |
| D264 | OWASP Agentic assessor via BaseAssessor (D116) | Consistent pattern; crosswalk integration; gate evaluation; ~60 LOC per framework |

---

## GOTCHA Layer Mapping

| Step | GOTCHA Layer | Component |
|------|-------------|-----------|
| Behavioral Drift Detection | Tools | `ai_telemetry_logger.py --drift` |
| Tool Chain Validation | Tools | `tool_chain_validator.py` |
| Output Content Safety | Tools | `agent_output_validator.py` |
| Formal Threat Model | Goals | `agentic_threat_model.md` |
| Dynamic Trust Scoring | Tools | `agent_trust_scorer.py` |
| MCP Per-Tool Authorization | Tools | `mcp_tool_authorizer.py` |
| Behavioral Red Teaming | Tools | `atlas_red_team.py --behavioral` |
| OWASP Agentic Assessment | Tools | `owasp_agentic_assessor.py` |
| Agentic security config | Args | `args/owasp_agentic_config.yaml` |
| Threat catalog | Context | `context/compliance/owasp_agentic_threats.json` |
| Gate thresholds | Args | `args/security_gates.yaml` (owasp_agentic section) |

---

## Related Files

- **Goals:** `goals/atlas_integration.md` (Phase 37 -- prerequisite), `goals/evolutionary_intelligence.md` (Phase 36 -- trust integration), `goals/devsecops_workflow.md` (Phase 24 -- pipeline integration), `goals/zero_trust_architecture.md` (Phase 25 -- ZTA integration), `goals/agentic_threat_model.md` (threat model document)
- **Tools:** `tools/security/` (ai_telemetry_logger, tool_chain_validator, agent_output_validator, agent_trust_scorer, mcp_tool_authorizer, atlas_red_team), `tools/compliance/` (owasp_agentic_assessor)
- **Args:** `args/owasp_agentic_config.yaml`, `args/security_gates.yaml` (owasp_agentic section)
- **Context:** `context/compliance/owasp_agentic_threats.json`
- **Tests:** `tests/test_tool_chain_validator.py`, `tests/test_agent_output_validator.py`, `tests/test_agent_trust_scorer.py`, `tests/test_mcp_tool_authorizer.py`, `tests/test_owasp_agentic_assessor.py`

---

## Changelog

- 2026-02-22: Initial creation -- OWASP Agentic AI Security goal (Phase 45) with 8-step workflow closing gaps identified from OWASP agentic AI analysis: behavioral drift detection (D257), tool chain validation (D258), output content safety (D259), formal threat model (D263), dynamic trust scoring (D260), MCP per-tool authorization (D261), behavioral red teaming (D262), OWASP agentic assessment (D264). Cross-integrates with Phases 24, 25, 36, 37.
