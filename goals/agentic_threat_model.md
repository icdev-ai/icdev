# CUI // SP-CTI

# Goal: OWASP Agentic AI Threat Model

**Standards:** NIST 800-53 Rev 5 RA-3 (Risk Assessment), RA-5 (Vulnerability Monitoring and Scanning), SA-11 (Developer Testing and Evaluation)

## Purpose

Formal threat model mapping OWASP Agentic AI threats (T1-T17) and Microsoft STRIDE categories to ICDEV's 15 agents, 14 MCP servers, and A2A protocol. This document identifies existing mitigations already implemented across ICDEV's 43+ phases, quantifies residual risk per threat, and specifies gaps to be addressed in Phase 45 (Agentic Security Hardening).

**Why this matters:** ICDEV is a 15-agent, multi-tier agentic system where autonomous agents route tasks, generate code, execute compliance workflows, and self-heal. Traditional application threat models do not account for agent-specific attack surfaces: memory poisoning, tool chain manipulation, cascading hallucination amplification, inter-agent trust exploitation, and human-in-the-loop fatigue attacks. OWASP's Agentic AI threat taxonomy (T1-T17) provides the definitive enumeration; this goal operationalizes it against ICDEV's concrete architecture.

---

## When to Use

- During initial deployment security review
- When adding a new agent or MCP server to the architecture
- When modifying A2A protocol, trust boundaries, or domain authority
- When onboarding a new Remote Gateway channel
- Quarterly re-assessment (or triggered by OWASP document updates)
- When Phase 45 remediation work begins
- When conducting red team exercises against the agentic architecture

---

## Prerequisites

- [ ] ICDEV database initialized (`python tools/db/init_icdev_db.py`)
- [ ] Agent authority matrix configured: `args/agent_authority.yaml`
- [ ] Security gates configured: `args/security_gates.yaml` (atlas_ai, prompt_injection, remote_command sections)
- [ ] Prompt injection detector operational: `tools/security/prompt_injection_detector.py`
- [ ] AI telemetry enabled: `tools/security/ai_telemetry_logger.py`
- [ ] ATLAS catalogs present: `context/compliance/atlas_mitigations.json`, `context/compliance/atlas_techniques.json`
- [ ] Resilience configuration: `args/resilience_config.yaml` (circuit breaker, retry defaults)
- [ ] Remote Gateway configured (if applicable): `args/remote_gateway_config.yaml`

---

## Scope

### 3-Tier Agent Architecture

| Tier | Agent | Port | Trust Level |
|------|-------|------|-------------|
| **Core** | Orchestrator | 8443 | Highest — task routing, workflow control |
| **Core** | Architect | 8444 | High — system design decisions |
| **Domain** | Builder | 8445 | Medium — code execution via SAST-gated TDD |
| **Domain** | Compliance | 8446 | High — ATO artifact generation, hard veto |
| **Domain** | Security | 8447 | Highest — hard veto on code/deps/infra (D42) |
| **Domain** | Infrastructure | 8448 | Medium — Terraform/Ansible/K8s generation |
| **Domain** | MBSE | 8451 | Medium — model-code sync |
| **Domain** | Modernization | 8452 | Medium — legacy migration |
| **Domain** | Requirements Analyst | 8453 | Medium — intake, gap detection |
| **Domain** | Supply Chain | 8454 | Medium — dependency graph, CVE triage |
| **Domain** | Simulation | 8455 | Low — read-heavy, what-if analysis |
| **Domain** | DevSecOps & ZTA | 8457 | High — pipeline security, hard veto |
| **Domain** | Gateway | 8458 | Medium — external command reception |
| **Support** | Knowledge | 8449 | Low — pattern detection, recommendations |
| **Support** | Monitor | 8450 | Low — read-only log analysis, health checks |

### Communication Surfaces

- **A2A Protocol:** JSON-RPC 2.0 over mutual TLS within K8s (inter-agent)
- **MCP Servers:** 14 stdio-transport servers for Claude Code (local process boundary)
- **Remote Gateway:** 5 channels (Telegram, Slack, Teams, Mattermost, internal chat)
- **Dashboard:** Flask web UI on port 5000 with session auth (D169-D172)
- **SaaS API Gateway:** REST + MCP Streamable HTTP on port 8443 (D58-D65)

---

## STRIDE Analysis by Tier

### Core Tier (Orchestrator, Architect)

| STRIDE Category | Threat | Existing Mitigation | Residual |
|-----------------|--------|---------------------|----------|
| **Spoofing** | Rogue agent impersonates Orchestrator to re-route tasks | mTLS within K8s cluster; Agent Card at `/.well-known/agent.json`; agent registry with heartbeat | Low |
| **Tampering** | Modify task payloads in transit between Orchestrator and Domain agents | HMAC-SHA256 event signing (D31); mTLS encryption; JSON-RPC schema validation | Low |
| **Repudiation** | Orchestrator denies issuing a destructive workflow command | Append-only audit trail (D6); AI telemetry with SHA-256 hashing (D216); correlation IDs (D149) | Low |
| **Information Disclosure** | Architect leaks CUI system design to lower-classification channel | Classification manager (D5); IL-aware response filtering (D135); per-channel max_il | Low |
| **Denial of Service** | Flood Orchestrator with decomposition requests to exhaust Bedrock quota | Rate limiting per tenant (D61); HPA auto-scaling (D141); circuit breaker (D146); token budget controls | Low |
| **Elevation of Privilege** | Architect bypasses Security agent hard veto to approve insecure design | Domain authority matrix (D42); Security agent has hard veto on code/deps/infra; veto recorded append-only | Low |

### Domain Tier (Builder, Compliance, Security, Infrastructure, MBSE, Modernization, Requirements, Supply Chain, Simulation, DevSecOps, Gateway)

| STRIDE Category | Threat | Existing Mitigation | Residual |
|-----------------|--------|---------------------|----------|
| **Spoofing** | External attacker impersonates Gateway agent to inject commands | User binding ceremony (D136); 8-gate security chain; signature verification on webhooks | Low |
| **Tampering** | Builder generates malicious code that passes superficial tests | SAST gate (bandit/gosec/SpotBugs); dependency audit; secret detection; TDD RED-GREEN-REFACTOR; Security agent hard veto | Medium |
| **Repudiation** | Builder denies generating vulnerable code pattern | Append-only audit trail (D6); agent execution JSONL logs (D35); git commit attribution | Low |
| **Information Disclosure** | Supply Chain agent leaks vendor SCRM assessment to unauthorized tenant | Per-tenant DB isolation (D60); RBAC (D172); tenant context middleware (D61) | Low |
| **Denial of Service** | Simulation agent Monte Carlo runs consume excessive compute | Resource limits on containers; read-only rootfs; K8s resource quotas; PDB (D143) | Low |
| **Elevation of Privilege** | Compliance agent approves its own ATO artifacts without ISSO review | Marketplace cross-tenant gate requires `publisher_is_reviewer` check; HITL for IL5+ | Medium |

### Support Tier (Knowledge, Monitor)

| STRIDE Category | Threat | Existing Mitigation | Residual |
|-----------------|--------|---------------------|----------|
| **Spoofing** | Attacker feeds false patterns to Knowledge agent | Self-healing confidence thresholds (>=0.7 auto, 0.3-0.7 suggest, <0.3 escalate); rate limit 5/hour | Low |
| **Tampering** | Modify self-healing recommendations to inject vulnerable fixes | Auto-heal rate limits; 10-minute cooldown; append-only pattern DB; Security agent veto | Low |
| **Repudiation** | Monitor fails to log a critical security event | Append-only audit trail; dual logging (file+console); SIEM forwarding (D31) | Low |
| **Information Disclosure** | Monitor log analysis exposes secrets in error messages | Secret detection gate; log sanitization; classification markings on logs | Low |
| **Denial of Service** | Overwhelm Monitor with false health check failures | Circuit breaker (D146); configurable check intervals; PDB | Low |
| **Elevation of Privilege** | Knowledge agent self-heals beyond its domain authority | Domain authority matrix (D42); Knowledge has no hard veto; actions are advisory only | Low |

---

## OWASP Agentic AI Threat Mapping (T1-T17)

| ID | Threat Name | Affected ICDEV Components | Existing Mitigations | Residual Risk | Phase 45 Gap |
|----|-------------|---------------------------|----------------------|---------------|--------------|
| T01 | Memory Poisoning | Memory system (`memory/`), Knowledge agent, `memory_write.py`, `memory.db` | HMAC signing (D31); append-only storage (D6); time-decay ranking (D168); hybrid search with BM25 (not purely embedding-based) | **Medium** | Gap 1: No behavioral drift detection on memory entries; no anomaly scoring on write patterns |
| T02 | Tool Misuse | Builder (code gen), Infrastructure (Terraform), all 14 MCP servers | `pre_tool_use.py` hook with deny patterns; Security agent hard veto (D42); SAST gates; read-only rootfs; drop ALL capabilities | **Low** | Gap 2: No multi-step tool chain validation; individual calls checked but sequences are not |
| T03 | Privilege Compromise | Orchestrator (task routing), RBAC (D172), domain authority (D42) | 5-role RBAC; domain authority matrix with hard/soft vetoes; per-agent port isolation; mTLS | **Low** | Gap 5: Static trust levels; no dynamic trust scoring based on runtime behavior |
| T04 | Resource Overload | All agents, Bedrock LLM calls, dashboard SSE | Rate limiting per tenant; HPA (D141); PDB (D143); circuit breaker (D146); token budget controls; retry with exponential backoff (D147) | **Low** | Covered |
| T05 | Cascading Hallucinations | Builder (code gen), Architect (design), Orchestrator (task decomposition) | GOTCHA framework separates LLM from business logic; structured JSON outputs (D39); deterministic tool validation; TDD gates; acceptance validation gate | **Low** | Gap 3: No output semantic validation between agent handoffs; structural checks only |
| T06 | Prompt Injection | All agents accepting external input, Gateway (5 channels), marketplace assets | 5-category prompt injection detector (D215); marketplace Gates 8-9 (D231); Gateway 8-gate security chain; confidence thresholds with block/flag/warn/allow | **Low** | Covered |
| T07 | Misaligned Behaviors | Orchestrator (workflow selection), Builder (implementation choices), child apps | Goals define expected behavior; acceptance validation gate; Security/Compliance hard vetoes; child genome versioning (D209) | **Medium** | Gap 1 + Gap 7: No continuous alignment monitoring; misalignment detected only at gate checkpoints |
| T08 | Repudiation of Actions | All agents, A2A protocol, audit trail | Append-only audit trail (D6); HMAC-SHA256 event signing (D31); AI telemetry with SHA-256 hashing (D216); correlation IDs (D149); JSONL agent execution logs (D35) | **Low** | Covered |
| T09 | Identity Spoofing | A2A protocol, Remote Gateway, SaaS API Gateway | mTLS within K8s; user binding ceremony (D136); 3-method auth (API key, OAuth, CAC/PIV); Agent Cards at `/.well-known/agent.json` | **Low** | Covered |
| T10 | HITL Overwhelming | Self-healing (Knowledge), genome propagation (Phase 36), marketplace reviews | Confidence thresholds (auto/suggest/escalate); max 5 auto-heals/hour; 10-min cooldown; HITL required for genome propagation (D214); marketplace cross-tenant human review | **Medium** | Gap 7: No HITL fatigue detection; no prioritized review queue with SLA tracking |
| T11 | Remote Code Execution | Builder (code gen), Infrastructure (Terraform), container runtime | Read-only rootfs; drop ALL capabilities; non-root UID 1000; K8s SecurityContext; SAST gates block critical findings; network policies (default deny) | **Low** | Covered |
| T12 | Communication Poisoning | A2A protocol (JSON-RPC 2.0), MCP stdio, webhook endpoints | mTLS for A2A; HMAC-SHA256 for webhooks (D31); replay window 300s; JSON-RPC schema validation; stdio process boundary for MCP | **Low** | Covered |
| T13 | Rogue Agents | Agent registry, heartbeat monitoring, A2A discovery | Agent registry in DB; heartbeat health checks; domain authority limits scope (D42); PDB prevents mass restart; agent executor JSONL audit (D35) | **Medium** | Gap 5: No runtime behavioral anomaly detection for agents; static trust model only |
| T14 | Human-Targeted Attacks | Gateway (5 channels), dashboard, SaaS portal | User binding (D136); rate limiting (30/user/min, 100/channel/min); blocked commands on remote (icdev-deploy, icdev-init); confirmation required for icdev-test/secure/build | **Medium** | Gap 6: No per-tool RBAC on MCP servers; stdio boundary is all-or-nothing |
| T15 | Human Manipulation via Agent Output | Dashboard, SaaS portal, Gateway response channel | Prompt injection detection on inputs; classification manager on outputs (D5); IL-aware response filtering (D135) | **Medium** | Gap 3: No output validation for semantic correctness; agent could present misleading summaries |
| T16 | Protocol Abuse | A2A JSON-RPC 2.0, MCP Streamable HTTP, webhook endpoints | mTLS for A2A; JSON-RPC schema validation; rate limiting; replay window; HMAC verification | **Medium** | Gap 2: No A2A message sequence validation; individual messages validated but protocol state machine not enforced |
| T17 | Supply Chain (Agent Dependencies) | All agents via requirements.txt, MCP server deps, marketplace assets | SBOM generation; dependency audit (pip-audit, npm audit, govulncheck); marketplace 7+2 gate pipeline; AI BOM (D217); Section 889 check | **Low** | Covered |

---

## Trust Boundary Diagram

```
+------------------------------------------------------------------+
|                     EXTERNAL BOUNDARY                            |
|                                                                  |
|  [Telegram] [Slack] [Teams] [Mattermost]  [Browser/CLI Users]   |
|       |        |       |        |               |                |
|       +--------+-------+--------+               |                |
|                |                                 |                |
|     +----------v-----------+          +----------v----------+    |
|     |  REMOTE GATEWAY      |          |  DASHBOARD / PORTAL |    |
|     |  8-gate security     |          |  Session auth D169  |    |
|     |  User binding D136   |          |  RBAC D172          |    |
|     |  IL filtering D135   |          |  CUI banners D5     |    |
|     +----------+-----------+          +----------+----------+    |
|                |                                 |                |
+================|=================================|================+
|                |     AGENT TRUST BOUNDARY        |                |
|                |         (mTLS + HMAC)           |                |
|     +----------v---------------------------------v----------+     |
|     |                  CORE TIER                            |     |
|     |  [Orchestrator:8443] <---mTLS---> [Architect:8444]    |     |
|     |       |  Domain authority (D42)  |                    |     |
|     +-------+-------+-------+---------+--------------------+     |
|             |       |       |                                     |
|     +-------v-------v-------v----------------------------------+ |
|     |                  DOMAIN TIER                             | |
|     |  [Builder:8445]    [Compliance:8446]  [Security:8447]    | |
|     |  [Infra:8448]      [MBSE:8451]        [Modern:8452]     | |
|     |  [ReqAnalyst:8453] [SupplyChain:8454] [Sim:8455]        | |
|     |  [DevSecOps:8457]  [Gateway:8458]                       | |
|     |       Security agent: HARD VETO on code/deps/infra      | |
|     +-------+-------+-----------------------------------------+ |
|             |       |                                             |
|     +-------v-------v-----------+                                 |
|     |      SUPPORT TIER         |                                 |
|     |  [Knowledge:8449]         |                                 |
|     |  [Monitor:8450]           |                                 |
|     |  Read-heavy, advisory     |                                 |
|     +-------+-------------------+                                 |
|             |                                                     |
+=============|=====================================================+
|             |     MCP STDIO BOUNDARY                              |
|     +-------v--------------------------------------------+        |
|     |  14 MCP SERVERS (stdio transport, local process)   |        |
|     |  icdev-core, icdev-compliance, icdev-builder,      |        |
|     |  icdev-infra, icdev-knowledge, icdev-maintenance,  |        |
|     |  icdev-mbse, icdev-requirements, icdev-supply,     |        |
|     |  icdev-simulation, icdev-integration,              |        |
|     |  icdev-marketplace, icdev-devsecops, icdev-gateway |        |
|     |  icdev-innovation                                  |        |
|     +-------+--------------------------------------------+        |
|             |                                                     |
+=============|=====================================================+
|             |     DATABASE BOUNDARY (append-only D6)              |
|     +-------v--------------------------------------------+        |
|     |  [icdev.db]  167 tables, append-only audit         |        |
|     |  [platform.db]  SaaS tenants, users, keys          |        |
|     |  [tenants/{slug}.db]  Per-tenant isolated DBs      |        |
|     |  [memory.db]  Memory entries, embeddings            |        |
|     |  [activity.db]  Task tracking                       |        |
|     +----------------------------------------------------+        |
+-------------------------------------------------------------------+
```

### Key Trust Boundaries

1. **External Boundary:** All inbound traffic passes through Gateway (8-gate chain) or Dashboard (session auth + RBAC). No direct agent access from outside.
2. **Agent Trust Boundary:** Inter-agent communication requires mTLS. HMAC-SHA256 signing for event integrity. Domain authority matrix governs cross-tier permissions.
3. **MCP Stdio Boundary:** MCP servers run as local subprocesses with stdio transport. No network exposure. All-or-nothing access per server (no per-tool RBAC -- this is Gap 6).
4. **Database Boundary:** Append-only contract (D6). No UPDATE/DELETE on audit tables. Per-tenant DB isolation (D60). WAL-safe backups (D152).

### MCP Server-Specific Threat Surface

MCP servers use stdio transport (local subprocess), which provides strong process isolation but introduces a distinct threat surface compared to network-based A2A communication.

| MCP Server | Sensitive Operations | Threat Concern | Existing Mitigation |
|------------|---------------------|----------------|---------------------|
| icdev-builder | `scaffold`, `generate_code`, `write_tests` | Code injection via generated output | SAST gates, TDD validation, Security agent veto |
| icdev-compliance | `ssp_generate`, `oscal_generate`, 30+ tools | Artifact tampering, false compliance claims | Append-only DB, classification markings (D5), ISSO review gate |
| icdev-infra | `terraform_plan`, `terraform_apply`, `k8s_deploy` | Infrastructure manipulation, privilege escalation | Region validator (D234), deployment gates, change request approval |
| icdev-gateway | `send_command`, `bind_user` | Unauthorized command injection via bound channels | User binding (D136), allowlist (D137), rate limiting, blocked commands |
| icdev-marketplace | `publish_asset`, `install_asset` | Supply chain poisoning via malicious assets | 9-gate pipeline (D231), IL compatibility check, digital signature |
| icdev-devsecops | `pipeline_security_generate`, `policy_generate` | Policy weakening, attestation bypass | DevSecOps maturity gate, policy-as-code validation |
| icdev-innovation | `run_pipeline`, `generate_solution` | Auto-generation of insecure solutions | Budget cap (10/PI), license check, compliance triage, GOTCHA fit check |

**Gap 6 impact:** Because MCP uses stdio transport with no per-tool authorization, any client with access to an MCP server can invoke all tools on that server. The `pre_tool_use.py` hook provides deny-pattern filtering but not affirmative RBAC. Phase 45 addresses this with per-tool permission mapping aligned to dashboard roles (D172).

---

## Residual Risk Summary

All threats rated **High** or **Medium** residual risk, with remediation path.

| Threat | Residual | Root Cause | Phase 45 Remediation |
|--------|----------|------------|---------------------|
| T01 Memory Poisoning | Medium | No anomaly detection on memory write patterns; time-decay helps but does not detect adversarial insertion | Gap 1: Behavioral drift detector -- score memory entries against baseline distribution; flag statistical outliers for HITL review |
| T07 Misaligned Behaviors | Medium | Alignment checked only at discrete gate checkpoints, not continuously during execution | Gap 1 + Gap 7: Continuous alignment monitor sampling agent outputs mid-workflow; HITL fatigue-aware escalation |
| T10 HITL Overwhelming | Medium | Fixed thresholds do not adapt to reviewer workload; no queue prioritization by urgency | Gap 7: HITL fatigue detection (review velocity tracking, SLA-based prioritization, auto-defer low-priority items during surge) |
| T13 Rogue Agents | Medium | Agent trust is static (registered = trusted); no behavioral baseline or drift detection | Gap 5: Dynamic trust scoring -- establish behavioral baselines per agent, flag statistical deviations, reduce trust level on anomaly |
| T14 Human-Targeted Attacks | Medium | MCP servers lack per-tool authorization; Gateway has per-command control but MCP is all-or-nothing | Gap 6: MCP server RBAC -- per-tool permission model aligned with user roles (D172) |
| T15 Human Manipulation | Medium | Output content validated structurally (JSON schema) but not semantically (meaning, accuracy) | Gap 3: Output semantic validation -- cross-reference agent claims against DB state; flag unsupported assertions |
| T16 Protocol Abuse | Medium | Individual A2A messages validated but message sequences (state machine) are not enforced | Gap 2: A2A protocol state machine -- define valid message sequences per workflow; reject out-of-order messages |
| Domain Tier Tampering | Medium | Builder code gen checked by SAST but sophisticated logic bombs may pass static analysis | Addressed by Gap 2 (chain validation) + existing TDD + Security agent veto |
| Domain Tier EoP | Medium | Compliance agent self-approval possible in single-agent test deployments | Addressed by existing marketplace cross-tenant gate; Phase 45 enforces separation-of-duties for all ATO artifacts |

---

## Phase 45 Gap Summary

| Gap | Name | Threats Addressed | Priority | Description |
|-----|------|-------------------|----------|-------------|
| Gap 1 | Behavioral Drift Detection | T01, T07 | High | Score memory entries and agent outputs against baseline distributions; flag statistical outliers for HITL review; detect adversarial insertion patterns in `memory.db` |
| Gap 2 | Multi-Step Chain Validation | T02, T16 | High | Define valid tool call sequences per workflow type; reject out-of-order A2A messages; detect anomalous tool chain patterns (e.g., Builder calling infra tools directly) |
| Gap 3 | Output Semantic Validation | T05, T15 | Medium | Cross-reference agent claims against DB state before presenting to users; flag assertions not backed by evidence; detect hallucinated compliance status |
| Gap 5 | Dynamic Agent Trust Scoring | T03, T13 | Medium | Establish per-agent behavioral baselines (tool call frequency, error rate, veto ratio); reduce trust level on anomaly; require re-authentication at lowered trust |
| Gap 6 | MCP Server Per-Tool RBAC | T14 | Medium | Map MCP server tools to dashboard roles (D172); enforce per-tool authorization in `pre_tool_use.py` hook; admin-only for destructive operations |
| Gap 7 | HITL Fatigue Detection | T07, T10 | Medium | Track reviewer velocity and pending queue depth; auto-defer low-priority items during surge; SLA-based escalation; alert when review backlog exceeds threshold |

---

## Error Handling

- If prompt injection detector is unavailable during threat assessment: degrade gracefully, log the gap, flag as unassessed -- do not block the pipeline
- If agent heartbeat fails during trust boundary validation: mark agent as unhealthy, route tasks to healthy agents, alert Monitor
- If ATLAS catalog files are missing: skip ATLAS-specific threat mappings, assess remaining threats, report partial coverage
- If A2A mTLS certificate expires: circuit breaker opens (D146), agent communication halts until cert renewal -- fail closed, not open
- If memory anomaly detection (Phase 45) produces false positives: HITL review queue absorbs; auto-suppress after 3 consecutive false positives on same pattern
- If MCP server process crashes: stdio boundary prevents impact on other servers; restart via K8s liveness probe; log crash in audit trail

---

## Review Cadence

| Trigger | Action |
|---------|--------|
| Quarterly scheduled | Full re-assessment of all 17 threats against current architecture |
| New phase implementation | Evaluate new components against T1-T17; update mitigations table |
| New agent or MCP server added | Add to scope table; perform STRIDE analysis; update trust boundary diagram |
| OWASP Agentic AI document update | Re-map threats; adjust residual risk ratings |
| Architecture decision affecting trust boundaries | Re-evaluate affected STRIDE categories |
| Post-incident (security event involving agents) | Targeted re-assessment of affected threats; update mitigations |
| Phase 45 gap closure | Re-rate residual risk for addressed threats |

---

## GOTCHA Layer Mapping

| Component | GOTCHA Layer | File |
|-----------|-------------|------|
| Threat model (this document) | Goals | `goals/agentic_threat_model.md` |
| Prompt injection detection | Tools | `tools/security/prompt_injection_detector.py` |
| AI telemetry | Tools | `tools/security/ai_telemetry_logger.py` |
| Domain authority matrix | Args | `args/agent_authority.yaml` |
| Security gate thresholds | Args | `args/security_gates.yaml` |
| ATLAS mitigations catalog | Context | `context/compliance/atlas_mitigations.json` |
| OWASP LLM Top 10 catalog | Context | `context/compliance/owasp_llm_top10.json` |
| STRIDE analysis templates | Hard Prompts | (Phase 45: `hardprompts/threat_model/stride_per_agent.md`) |

---

## Related Files

- **Goals:** `goals/atlas_integration.md` (Phase 37 -- MITRE ATLAS), `goals/evolutionary_intelligence.md` (Phase 36 -- genome security), `goals/zero_trust_architecture.md` (Phase 25 -- ZTA), `goals/remote_command_gateway.md` (Phase 28 -- Gateway security), `goals/marketplace.md` (Phase 22 -- asset security gates)
- **Tools:** `tools/security/prompt_injection_detector.py`, `tools/security/ai_telemetry_logger.py`, `tools/security/atlas_red_team.py`, `tools/security/ai_bom_generator.py`, `tools/audit/audit_logger.py`
- **Args:** `args/security_gates.yaml` (atlas_ai, prompt_injection, remote_command sections), `args/agent_authority.yaml`, `args/resilience_config.yaml`
- **Context:** `context/compliance/atlas_mitigations.json`, `context/compliance/owasp_llm_top10.json`, `context/compliance/nist_ai_rmf.json`
- **Tests:** `tests/test_prompt_injection_detector.py` (47 tests), `tests/test_ai_telemetry.py` (12 tests), `tests/test_atlas_assessor.py` (15 tests), `tests/test_atlas_red_team.py` (10 tests), `tests/test_phase36_phase37_integration.py` (17 tests)

---

## Changelog

- 2026-02-22: Initial creation -- OWASP Agentic AI Threat Model (T1-T17) with STRIDE analysis across 3 tiers, trust boundary diagram, residual risk assessment, 6 Phase 45 gaps identified, review cadence defined

# CUI // SP-CTI
