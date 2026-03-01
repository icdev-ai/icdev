# Phase 37 — MITRE ATLAS Integration

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 37 |
| Title | MITRE ATLAS AI Security Framework Integration |
| Status | Requirements |
| Priority | P1 |
| Dependencies | Phase 17 (Multi-Framework Compliance), Phase 23 (Universal Compliance), Phase 35 (Innovation Engine) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-21 |

---

## 1. Problem Statement

ICDEV is an AI-powered agentic development system that uses LLMs extensively for code generation, compliance assessment, requirements intake, and autonomous decision-making. Despite its comprehensive security posture (SAST, dependency audit, secret detection, container scanning, STIG hardening), ICDEV has **zero AI-specific threat defenses**.

MITRE ATLAS (Adversarial Threat Landscape for AI Systems) — the ATT&CK equivalent for AI/ML — documents 84+ techniques across 16 tactics that adversaries use to attack AI systems. As of February 2026 (v5.4.0), ATLAS includes 51 real-world case studies, several of which describe attacks against **agentic coding assistants with MCP servers** — architecturally identical to ICDEV.

Key case studies directly relevant to ICDEV:
- **AML.CS0041** — Rules File Backdoor: supply chain attack via AI config file manipulation (analogous to CLAUDE.md, goals/, args/)
- **AML.CS0045** — Data Exfiltration from MCP Server (Cursor): data stolen through MCP tool invocations (ICDEV has 14 MCP servers)
- **AML.CS0047** — Malicious AI Agent (Amazon Q VSCode): deployed malicious agent inside IDE extension
- **AML.CS0049** — Poisoned Agent Skill: trojanized skills published to registries (ICDEV Marketplace, Phase 22)
- **AML.CS0050** — 1-Click RCE via Agent: sandbox escape from agent to host system
- **AML.CS0051** — C2 via Prompt Injection: command and control through prompt injection

ICDEV processes untrusted external inputs from Jira, ServiceNow, DOORS NG, uploaded documents, code files, issue trackers, and user prompts — all documented prompt injection vectors.

---

## 2. MITRE ATLAS Overview

### 2.1 Framework Statistics (v5.4.0, February 2026)

| Metric | Count |
|--------|-------|
| Tactics | 16 |
| Techniques | 84+ |
| Sub-techniques | 56+ |
| Mitigations | 34 |
| Case Studies | 51 |

### 2.2 Complete Tactics (16)

| ID | Tactic | Description | ATLAS-Unique? |
|----|--------|-------------|---------------|
| AML.TA0001 | Reconnaissance | Model architecture probing, artifact discovery | No |
| AML.TA0002 | Resource Development | Adversarial tooling, proxy models, poisoned datasets | No |
| AML.TA0003 | Initial Access | Prompt injection, supply chain, phishing | No |
| AML.TA0004 | ML Model Access | Inference API access, model artifact access | **Yes** |
| AML.TA0005 | Execution | Command execution via AI system | No |
| AML.TA0006 | Persistence | Agent config modification, backdoors, memory poisoning | No |
| AML.TA0007 | Privilege Escalation | ML system boundary exploitation | No |
| AML.TA0008 | Defense Evasion | Adversarial perturbation, jailbreak, prompt obfuscation | No |
| AML.TA0009 | Credential Access | Agent config credential theft, RAG credential harvesting | No |
| AML.TA0010 | Discovery | Agent config enumeration, tool discovery, trigger discovery | No |
| AML.TA0011 | Collection | Data from AI services, RAG databases, agent tools | No |
| AML.TA0012 | ML Attack Staging | Training data poisoning, adversarial data crafting | **Yes** |
| AML.TA0013 | Exfiltration | Via inference API, agent tool invocation, cyber means | No |
| AML.TA0014 | Command and Control | C2 via AI service APIs (v4.9.0) | No |
| AML.TA0015 | Lateral Movement | LLM response rendering, prompt self-replication (v5.1.0) | No |
| AML.TA0016 | Impact | DoS, model integrity erosion, cost harvesting, data destruction | No |

### 2.3 Key Techniques Relevant to ICDEV

#### Prompt Injection (AML.T0051) — CRITICAL
| Sub-technique | Description | ICDEV Attack Surface |
|--------------|-------------|---------------------|
| AML.T0051.000 | Direct Prompt Injection | User prompts to Claude Code, dashboard chat |
| AML.T0051.001 | Indirect Prompt Injection | Jira tickets, DOORS exports, uploaded SOWs, code files, README files |
| AML.T0051.002 | Triggered Prompt Injection | Time-delayed injections in CI/CD artifacts |

#### Agentic AI Attacks (v5.0.0–v5.4.0)
| Technique | Description | ICDEV Attack Surface |
|-----------|-------------|---------------------|
| AML.T0080 | AI Agent Context Poisoning | MEMORY.md, memory.db manipulation |
| AML.T0080.001 | Memory Poisoning | Corrupting long-term memory to persist across sessions |
| AML.T0080.002 | Thread Poisoning | Injecting instructions into active conversations |
| AML.T0081 | Modify AI Agent Configuration | CLAUDE.md, goals/, args/ YAML, llm_config.yaml |
| AML.T0082 | RAG Credential Harvesting | Credentials in knowledge base documents |
| AML.T0083 | Credentials from Agent Config | .env, AWS credentials, BYOK keys |
| AML.T0084 | Discover AI Agent Configuration | Enumerate 14 MCP servers, tool definitions |
| AML.T0084.001 | Tool Definitions | Discover all 100+ available MCP tools |
| AML.T0084.002 | Activation Triggers | Find keywords that trigger agent workflows |
| AML.T0085.000 | Data from RAG Databases | Extract data from knowledge base |
| AML.T0085.001 | Data from AI Agent Tools | Invoke tools to access organizational APIs |
| AML.T0086 | Exfiltration via Agent Tool Invocation | Use write-capable tools (email, deploy, git push) for exfiltration |
| AML.T0099 | AI Agent Tool Data Poisoning | Poison data at tool invocation points |
| AML.T0100 | AI Agent Clickbait | Lure browser agents via UI manipulation |
| AML.T0101 | Data Destruction via Agent Tool Invocation | Use destructive tools (terraform_apply, rollback) as weapons |
| AML.T0104 | Publish Poisoned AI Agent Tool | Trojanized skills on ICDEV Marketplace |
| AML.T0105 | Escape to Host | Break out of agent sandbox to host system |

#### Supply Chain Attacks
| Technique | Description | ICDEV Attack Surface |
|-----------|-------------|---------------------|
| AML.T0010.001 | AI Software Compromise | Compromised Python packages in requirements.txt |
| AML.T0010.003 | Model Compromise | Backdoored models for embeddings/code generation |
| AML.T0010.004 | Container Registry Compromise | Poisoned Docker images |
| AML.T0011.002 | Poisoned AI Agent Tool | Trojanized skills from Marketplace |
| AML.T0053 | LLM Plugin Compromise | Compromised MCP server implementations |
| AML.T0058 | Publish Poisoned Models | Backdoored models on HuggingFace |
| AML.T0060 | Publish Hallucinated Entities | Fake packages matching LLM hallucinations |

#### Model Theft and Data Exfiltration
| Technique | Description |
|-----------|-------------|
| AML.T0024.000 | Infer Training Data Membership |
| AML.T0024.001 | Invert ML Model (reconstruct training data) |
| AML.T0024.002 | Extract ML Model (systematic API querying) |
| AML.T0057 | LLM Data Leakage (secrets, PII in outputs) |
| AML.T0056 | LLM Meta Prompt Extraction (system prompt theft) |

### 2.4 Complete Mitigations (34)

| ID | Mitigation | Description |
|----|-----------|-------------|
| AML.M0000 | Limit Public Release of Information | Restrict disclosure of AI system technical details |
| AML.M0001 | Limit Model Artifact Release | Control access to models, architectures, checkpoints |
| AML.M0002 | Passive ML Output Obfuscation | Reduce output fidelity to hinder extraction |
| AML.M0003 | Model Hardening | Adversarial training, distillation, robustness techniques |
| AML.M0004 | Restrict Number of ML Model Queries | Rate limiting and query quotas |
| AML.M0005 | Control Access to ML Models and Data at Rest | Access controls on model registries and data |
| AML.M0006 | Use Ensemble Methods | Multiple models for robustness against evasion |
| AML.M0007 | Sanitize Training Data | Detect, remove, remediate poisoned data |
| AML.M0008 | Validate ML Model | Test for backdoors, bias, concept drift |
| AML.M0009 | Use Multi-Modal Sensors | Multiple sensor types to prevent single-point failure |
| AML.M0010 | Input Restoration | Preprocess inputs to neutralize adversarial perturbations |
| AML.M0011 | Restrict Library Loading | Prevent untrusted code loading (pickle files) |
| AML.M0012 | Encrypt Sensitive Information | Encrypt models and sensitive data |
| AML.M0013 | Code Signing | Digital signature verification for supply chain |
| AML.M0014 | Verify ML Artifacts | Cryptographic checksum verification |
| AML.M0015 | Adversarial Input Detection | Detect and block adversarial/injection inputs |
| AML.M0016 | Vulnerability Scanning | Scan model artifacts for exploits |
| AML.M0017 | Model Distribution Methods | Prefer cloud over edge deployment |
| AML.M0018 | User Training | Educate developers on ML vulnerabilities |
| AML.M0019 | Control Access to ML Models and Data in Production | Auth and monitoring for API endpoints |
| AML.M0020 | Generative AI Guardrails | Input/output safety filters and validators |
| AML.M0021 | Generative AI Guidelines | System prompts with safety parameters |
| AML.M0022 | Generative AI Model Alignment | Fine-tuning for safety (RLHF) |
| AML.M0023 | AI Bill of Materials | Complete artifact and dataset provenance |
| AML.M0024 | AI Telemetry Logging | Log all model inputs/outputs |
| AML.M0025 | Maintain AI Dataset Provenance | Track complete dataset history |
| AML.M0026 | Privileged AI Agent Permissions Configuration | Least-privilege for agent system access |
| AML.M0027 | Single-User AI Agent Permissions Configuration | Per-user permission scoping for agents |
| AML.M0028 | AI Agent Tools Permissions Configuration | Granular tool-level permission controls |
| AML.M0029 | Human In-the-Loop for AI Agent Actions | Require human approval for critical actions |
| AML.M0030 | Restrict AI Agent Tool Invocation on Untrusted Data | Prevent tool execution on untrusted input |
| AML.M0031 | Memory Hardening | Protect agent memory from manipulation |
| AML.M0032 | Segmentation of AI Agent Components | Isolate agent components to limit blast radius |
| AML.M0033 | Input/Output Validation for AI Agent Components | Validate all agent I/O |
| AML.M0034 | Deepfake Detection | Detect synthetic media |

---

## 3. Current ICDEV Coverage Gap Analysis

### 3.1 Already Covered (~60%)

| ATLAS Mitigation | ICDEV Implementation | Status |
|-----------------|---------------------|--------|
| AML.M0005 | Per-tenant DB isolation (D60), K8s RBAC | Covered |
| AML.M0011 | STIG-hardened containers, read-only rootfs | Covered |
| AML.M0012 | AES-256 BYOK (D175), FIPS 140-2 | Covered |
| AML.M0013 | Marketplace digital signing | Covered |
| AML.M0014 | Marketplace SBOM + checksums | Covered |
| AML.M0019 | API gateway auth, mTLS for A2A | Covered |
| AML.M0026 | Command allowlists, D138 deploy blocked remotely | Covered |
| AML.M0029 | Self-healing thresholds, approval workflows | Covered |
| AML.M0030 | Remote Command Gate 8-gate chain | Covered |
| AML.M0031 | HMAC signing on agent mailbox (D41) | Covered |
| AML.M0032 | Microservice architecture, network policies | Covered |

### 3.2 Gaps (~40%)

| ATLAS Mitigation | Gap | Risk Level |
|-----------------|-----|------------|
| **AML.M0015** | No prompt injection detection on inbound requests | **CRITICAL** |
| **AML.M0020** | No input/output filtering layer on LLM calls | **CRITICAL** |
| **AML.M0033** | No structured validation of agent inputs/outputs | **HIGH** |
| **AML.M0024** | No model I/O logging (audit trail covers actions, not LLM calls) | **HIGH** |
| **AML.M0023** | Software SBOM only, no AI-specific BOM (models, datasets, frameworks) | **HIGH** |
| **AML.M0027** | No per-user permission scoping on agent tool calls | **HIGH** |
| **AML.M0028** | No granular tool-level permission controls | **HIGH** |
| **AML.M0016** | No scanning of model artifacts (pickle, ONNX, safetensors) | **HIGH** |
| **AML.M0007** | No validation of fine-tuning data or embedding inputs | **HIGH** |
| **AML.M0003** | No adversarial robustness testing for LLM interactions | **MEDIUM** |
| **AML.M0010** | No adversarial input preprocessing before LLM calls | **MEDIUM** |
| **AML.M0025** | Embedding provenance not tracked | **MEDIUM** |
| **AML.M0021** | System prompts exist but no safety parameter enforcement | **MEDIUM** |
| **AML.M0034** | No synthetic media detection for document upload | **LOW** |

---

## 4. Requirements

### 4.1 ATLAS as Compliance Framework

#### REQ-37-001: ATLAS Framework in Crosswalk Engine
The system SHALL add MITRE ATLAS as a compliance framework in the dual-hub crosswalk engine (D111), functioning as an AI-specific third hub that bridges to NIST 800-53 controls.

#### REQ-37-002: ATLAS Mitigation Catalog
The system SHALL maintain a machine-readable catalog of all 34 ATLAS mitigations with IDs, descriptions, technique mappings, and NIST 800-53 control crosswalks.

#### REQ-37-003: ATLAS Technique Catalog
The system SHALL maintain a machine-readable catalog of all ATLAS techniques organized by tactic, with IDs, descriptions, sub-techniques, and applicable mitigations.

#### REQ-37-004: ATLAS Assessor
The system SHALL implement an ATLAS assessor using the BaseAssessor pattern (D116) that evaluates a project's AI security posture against all 34 mitigations.

#### REQ-37-005: ATLAS Gate
The system SHALL enforce an ATLAS security gate with the following blocking conditions:
- 0 unmitigated CRITICAL-risk techniques in the project's AI attack surface
- Prompt injection defense active (AML.M0015)
- AI telemetry logging active (AML.M0024)
- Agent permission controls active (AML.M0026, M0027, M0028)

#### REQ-37-006: ATLAS Reporting
The system SHALL generate ATLAS compliance reports showing mitigation coverage, technique exposure, gap analysis, and recommended remediation actions.

### 4.2 SAFE-AI Integration

#### REQ-37-010: SAFE-AI Control Overlay
The system SHALL flag the 100 AI-affected NIST 800-53 controls identified by MITRE SAFE-AI in the existing control catalog, with AI-specific concern narratives.

#### REQ-37-011: SSP AI System Elements
The system SHALL extend the SSP generator to include SAFE-AI's 4 system elements (Environment, AI Platform, AI Model, AI Data) when the project uses AI/ML capabilities.

#### REQ-37-012: SAFE-AI Assessment Criteria
The system SHALL integrate SAFE-AI's supplemental assessment criteria (question-and-answer sets) into the STIG checker and compliance assessment workflow.

### 4.3 Prompt Injection Defense (P0 — CRITICAL)

#### REQ-37-020: Prompt Injection Detector
The system SHALL implement a prompt injection detection engine that scans all inbound text before LLM processing, using pattern matching, heuristic analysis, and structural analysis.

#### REQ-37-021: Detection Patterns
The detector SHALL identify the following injection categories:
- Role hijacking ("ignore previous instructions", "you are now", "system: ")
- Delimiter attacks (markdown code fences, XML tags, special characters used to break prompt boundaries)
- Instruction injection ("do not follow", "override", "forget your instructions")
- Data exfiltration triggers ("send to", "email", "post to URL")
- Encoded payloads (base64, unicode escaping, homoglyph substitution)

#### REQ-37-022: Integration Points
The prompt injection detector SHALL be integrated at all external input boundaries:
- User prompts via Claude Code and dashboard chat
- Jira/ServiceNow/GitLab issue content and comments
- DOORS NG ReqIF imports
- Uploaded documents (SOW, CDD, CONOPS)
- Code files processed during build/review
- Remote Command Gateway inputs
- Marketplace asset content (skills, goals, hardprompts)

#### REQ-37-023: Air-Gap Safety
The prompt injection detector SHALL be fully air-gap safe, using regex patterns and heuristic analysis with no external API dependency (consistent with D7, D22 stdlib patterns).

#### REQ-37-024: Detection Response
When injection is detected:
- Confidence >= 0.9: Block input, log alert, notify user
- Confidence 0.7–0.89: Flag for human review, allow with warning
- Confidence 0.5–0.69: Log warning, allow with monitoring
- Confidence < 0.5: Allow, log for telemetry

### 4.4 AI Security Gates

#### REQ-37-030: AI BOM Gate
The system SHALL enforce an AI Bill of Materials gate requiring documentation of all AI/ML components: models used, model versions, embedding providers, training data sources, AI framework versions.

#### REQ-37-031: AI Telemetry Gate
The system SHALL enforce AI telemetry logging for all LLM interactions, capturing: model ID, prompt hash (not full prompt for CUI sensitivity), response hash, token count, latency, user ID, agent ID, and timestamp.

#### REQ-37-032: Agent Permission Gate
The system SHALL enforce agent permission controls: all tool invocations must follow least-privilege, per-user permission scoping must be active, and granular tool-level permissions must be configured.

#### REQ-37-033: AI Input/Output Validation Gate
The system SHALL enforce input/output validation on all LLM calls: inputs scanned for injection patterns, outputs validated against expected structure, sensitive data (PII, credentials, CUI) filtered from outputs.

#### REQ-37-034: RAG Integrity Gate
The system SHALL enforce knowledge base integrity: entries validated for injection patterns before storage, provenance tracked for all entries, integrity checksums on retrieval.

#### REQ-37-035: Model Integrity Gate
The system SHALL enforce model artifact integrity: cryptographic checksums verified for all model files, no unsigned model artifacts permitted, model provenance documented.

### 4.5 AI Telemetry and Monitoring

#### REQ-37-040: AI Telemetry Table
The system SHALL create an `ai_telemetry` table in icdev.db to capture all LLM interactions with append-only semantics (D6 pattern).

#### REQ-37-041: Anomaly Detection
The system SHALL monitor AI telemetry for anomalous patterns:
- Unusual query volume (potential model extraction — AML.T0024.002)
- Cost spikes (potential cost harvesting — AML.T0034)
- Prompt pattern anomalies (potential prompt injection campaign)
- Output pattern anomalies (potential data leakage — AML.T0057)

#### REQ-37-042: SIEM Integration
AI telemetry alerts SHALL integrate with existing SIEM forwarding (ELK/Splunk) via the observability hook system.

### 4.6 Marketplace Hardening

#### REQ-37-050: Marketplace Gate 8 — AI Content Scanning
The marketplace publish pipeline SHALL scan all asset content (skills, goals, hardprompts, context files) for prompt injection payloads, encoded instructions, and manipulation patterns.

#### REQ-37-051: Marketplace Gate 9 — Behavioral Sandbox
Executable assets (skills with tool invocations) SHALL be executed in an isolated sandbox before approval, with monitoring for: data exfiltration attempts (AML.T0086), unauthorized tool access (AML.T0085.001), and configuration manipulation (AML.T0081).

### 4.7 Memory System Hardening

#### REQ-37-060: Memory Integrity Verification
The system SHALL verify HMAC integrity on every memory read, extending the existing D41 pattern to cover MEMORY.md, daily logs, and all memory.db entries.

#### REQ-37-061: Memory Write Validation
All memory writes SHALL be scanned for injection patterns before storage. External-sourced content (child reports, integration imports) SHALL receive additional scrutiny.

#### REQ-37-062: Memory Trust Segmentation
Memory entries SHALL be tagged with trust levels:
- **system**: Generated by ICDEV core (highest trust)
- **user**: Entered by authenticated user
- **external**: Imported from external sources (lowest trust)
- **child**: Reported by child applications (medium trust)

### 4.8 ATLAS Red Teaming

#### REQ-37-070: AI Red Team Scanner
The system SHALL implement an automated AI red team capability that tests ICDEV's own defenses against ATLAS techniques.

#### REQ-37-071: Red Team Test Categories
The red team scanner SHALL test:
- Prompt injection resistance (AML.T0051 variants)
- System prompt extraction resistance (AML.T0056)
- Memory poisoning resistance (AML.T0080)
- Tool abuse resistance (AML.T0086)
- Data leakage resistance (AML.T0057)
- Cost harvesting resistance (AML.T0034)

#### REQ-37-072: ATLAS-Mapped Findings
Red team findings SHALL be mapped to specific ATLAS technique IDs and mitigations, producing an ATLAS-native security assessment report.

### 4.9 Complementary Framework Integration

#### REQ-37-080: OWASP LLM Top 10 Crosswalk
The system SHALL add OWASP Top 10 for LLMs as a lightweight compliance framework, crosswalked to both ATLAS mitigations and NIST 800-53 controls.

| OWASP LLM | ATLAS Technique | ATLAS Mitigation | NIST 800-53 |
|-----------|----------------|------------------|-------------|
| LLM01: Prompt Injection | AML.T0051 | M0015, M0020, M0021 | SI-10 |
| LLM02: Sensitive Info Disclosure | AML.T0057 | M0002, M0007, M0021 | SC-28 |
| LLM03: Supply Chain | AML.T0010 | M0013, M0014, M0023 | SA-12 |
| LLM04: Data Poisoning | AML.T0020 | M0007, M0025 | SI-10 |
| LLM05: Improper Output | AML.T0067 | M0020, M0029 | SI-10 |
| LLM06: Excessive Agency | AML.T0086 | M0026, M0029, M0030 | AC-6 |
| LLM07: System Prompt Leakage | AML.T0056 | M0022, M0024 | AC-3 |
| LLM08: Vector/Embedding Weakness | AML.T0070 | M0031, M0025 | SI-10 |
| LLM09: Misinformation | AML.T0060 | M0008, M0022 | SI-10 |
| LLM10: Unbounded Consumption | AML.T0034 | M0004, M0015 | SC-5 |

#### REQ-37-081: NIST AI RMF Integration
The system SHALL add NIST AI Risk Management Framework 1.0 as a governance framework, mapped through the NIST 800-53 hub. The 4 AI RMF functions (Govern, Map, Measure, Manage) SHALL be tracked as compliance dimensions.

#### REQ-37-082: ISO/IEC 42001 Integration
The system SHALL add ISO/IEC 42001:2023 (AI Management System) as a compliance framework, bridged through ISO 27001 (international hub) with a direct crosswalk to NIST AI RMF.

---

## 5. Database Schema

### New Tables

| Table | Purpose |
|-------|---------|
| `atlas_assessments` | ATLAS assessment results per project (project_id, assessment_date, mitigation_scores_json, technique_exposure_json, overall_score) |
| `ai_telemetry` | LLM interaction log (model_id, prompt_hash, response_hash, token_count, latency_ms, user_id, agent_id, timestamp) — append-only |
| `prompt_injection_log` | Detected injection attempts (source, content_hash, confidence, category, action_taken, timestamp) — append-only |
| `ai_bom` | AI Bill of Materials (project_id, component_type, component_name, version, provider, provenance, hash) |
| `atlas_red_team_results` | Red team scan results (project_id, technique_id, result, evidence, timestamp) |

---

## 6. New Tools

| Tool | Purpose |
|------|---------|
| `tools/security/prompt_injection_detector.py` | Detect prompt injection patterns in inbound text |
| `tools/security/ai_telemetry_logger.py` | Log all LLM interactions to ai_telemetry table |
| `tools/security/ai_bom_generator.py` | Generate AI-specific Bill of Materials |
| `tools/security/atlas_red_team.py` | Automated AI red team scanner |
| `tools/compliance/atlas_assessor.py` | ATLAS compliance assessor (BaseAssessor pattern) |
| `tools/compliance/atlas_report_generator.py` | ATLAS compliance report generator |
| `tools/compliance/owasp_llm_assessor.py` | OWASP LLM Top 10 assessor |
| `tools/compliance/nist_ai_rmf_assessor.py` | NIST AI RMF assessor |
| `tools/compliance/iso42001_assessor.py` | ISO/IEC 42001 assessor |

### New Context Files

| File | Purpose |
|------|---------|
| `context/compliance/atlas_mitigations.json` | 34 mitigations with IDs, descriptions, NIST mappings |
| `context/compliance/atlas_techniques.json` | 84+ techniques organized by tactic |
| `context/compliance/safeai_controls.json` | 100 AI-affected NIST 800-53 controls |
| `context/compliance/owasp_llm_top10.json` | OWASP LLM Top 10 with crosswalks |
| `context/compliance/nist_ai_rmf.json` | NIST AI RMF functions and categories |
| `context/compliance/iso42001_controls.json` | ISO/IEC 42001 control set |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D216 | ATLAS as third hub in crosswalk engine (AI hub alongside US/International hubs) | AI threats are a distinct domain; direct NIST 800-53 bridge enables cascade to all existing frameworks |
| D217 | Prompt injection detection is regex+heuristic (no LLM) | Air-gap safe, deterministic, zero external dependency; LLM-based detection creates circular dependency |
| D218 | AI telemetry logs prompt/response hashes, not full content | CUI sensitivity — full prompts may contain classified content; hashes enable dedup and anomaly detection without content exposure |
| D219 | ATLAS red teaming is opt-in (`--atlas-red-team` flag) | Backward compatible (D44 pattern); red team tests may trigger alerts in production SIEM |
| D220 | OWASP LLM Top 10 crosswalked through ATLAS, not directly to NIST | ATLAS provides richer technique-level mapping; OWASP is developer-facing summary of ATLAS threats |
| D221 | SAFE-AI 100 controls flagged as overlay, not separate catalog | SAFE-AI uses existing NIST 800-53 controls with AI-specific narrative; overlay avoids duplication |
| D222 | Memory trust segmentation uses metadata tag, not separate storage | Consistent with existing memory.db schema; tag-based filtering simpler than separate databases |

---

## 8. ATLAS-to-NIST 800-53 Crosswalk (Key Mappings)

| ATLAS Mitigation | NIST 800-53 Controls | Description |
|-----------------|---------------------|-------------|
| AML.M0024 (AI Telemetry) | AU-2, AU-3, AU-6 | Audit events, content, review |
| AML.M0019 (Production Access) | AC-2, AC-3, AC-6 | Account management, access enforcement, least privilege |
| AML.M0012 (Encryption) | SC-12, SC-13, SC-28 | Key management, crypto protection, data at rest |
| AML.M0013 (Code Signing) | SI-7 | Software, firmware, information integrity |
| AML.M0016 (Vuln Scanning) | RA-5 | Vulnerability monitoring and scanning |
| AML.M0007 (Data Sanitization) | SI-10 | Information input validation |
| AML.M0023 (AI BOM) | CM-8, SA-17 | System component inventory, developer architecture |
| AML.M0015 (Adversarial Input) | SI-10, SI-4 | Input validation, system monitoring |
| AML.M0020 (GenAI Guardrails) | SI-10, SC-7 | Input validation, boundary protection |
| AML.M0004 (Rate Limiting) | SC-5, AC-10 | DoS protection, concurrent session control |

---

## 9. Implementation Priority

| Priority | Component | Techniques Addressed | Risk Mitigated |
|----------|-----------|---------------------|----------------|
| **P0** | Prompt Injection Detector | AML.T0051 (all variants) | CRITICAL — most exploited AI attack vector |
| **P1** | AI Telemetry Logging | AML.T0024, T0034, T0057 | HIGH — detection requires visibility |
| **P1** | AI Security Gates | Multiple | HIGH — enforcement at CI/CD boundaries |
| **P2** | ATLAS Assessor + Reporting | All 34 mitigations | HIGH — enables compliance tracking |
| **P2** | Marketplace Hardening (Gates 8-9) | AML.T0104, T0081, T0086 | HIGH — documented real-world attack vector |
| **P3** | SAFE-AI ATO Integration | 100 AI-affected controls | MEDIUM — enhances existing ATO |
| **P3** | OWASP LLM Top 10 Crosswalk | Cross-mapped | MEDIUM — developer-facing guidance |
| **P3** | Memory Hardening | AML.T0080 | MEDIUM — extends existing HMAC |
| **P4** | ATLAS Red Teaming | All testable techniques | MEDIUM — proactive defense validation |
| **P4** | NIST AI RMF + ISO 42001 | Governance layer | LOW — governance structure |

---

## 10. Security Gate

**ATLAS AI Security Gate:**
- 0 unmitigated CRITICAL-risk ATLAS techniques
- Prompt injection defense active and passing
- AI telemetry logging active for all LLM calls
- Agent permission controls configured (M0026, M0027, M0028)
- AI BOM current and complete
- 0 detected prompt injection attempts unresolved
- RAG/knowledge base integrity verified
- Model artifact checksums verified

---

## 11. Compliance Framework Summary

After Phase 37, ICDEV will support the following AI-specific frameworks in addition to existing frameworks:

| Framework | Type | Hub | Status |
|-----------|------|-----|--------|
| MITRE ATLAS v5.4.0 | AI Threat Model | AI Hub (new) | Phase 37 |
| MITRE SAFE-AI | AI Control Overlay | US Hub (overlay) | Phase 37 |
| OWASP LLM Top 10 | Developer Guidance | Via AI Hub | Phase 37 |
| NIST AI RMF 1.0 | AI Governance | US Hub | Phase 37 |
| ISO/IEC 42001:2023 | AI Management System | International Hub | Phase 37 |

Total compliance frameworks after Phase 37: **25** (20 existing + 5 AI-specific).
