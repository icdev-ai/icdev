# CUI // SP-CTI

# Goal: MITRE ATLAS Integration

## Purpose

Defend ICDEV and its child applications against AI/ML-specific adversarial threats using the MITRE ATLAS (Adversarial Threat Landscape for AI Systems) framework. This goal orchestrates prompt injection detection, AI telemetry, multi-framework AI security assessment, red teaming, and marketplace hardening.

**Why this matters:** Traditional security frameworks (NIST 800-53, STIG) address infrastructure threats but not AI-specific attack surfaces. LLM-powered systems face unique risks — prompt injection, model poisoning, data exfiltration via model responses, jailbreaking, and supply chain attacks on AI components. MITRE ATLAS provides the threat taxonomy; this goal operationalizes it through automated detection, assessment, and defense.

---

## When to Use

- When building or deploying any LLM-powered application (ICDEV or child apps)
- When assessing AI security posture for ATO/cATO
- When child applications report learned behaviors (Phase 36 cross-phase integration)
- When publishing or installing marketplace assets
- When conducting red team exercises against AI components
- When generating compliance reports that include AI security coverage
- During intake if the project uses AI/ML capabilities (auto-detected)

---

## Prerequisites

- [ ] ICDEV database initialized (`python tools/db/init_icdev_db.py`)
- [ ] ATLAS catalogs present: `context/compliance/atlas_mitigations.json`, `context/compliance/atlas_techniques.json`
- [ ] OWASP LLM Top 10 catalog: `context/compliance/owasp_llm_top10.json`
- [ ] NIST AI RMF catalog: `context/compliance/nist_ai_rmf.json`
- [ ] ISO 42001 catalog: `context/compliance/iso42001_controls.json`
- [ ] SAFE-AI catalog: `context/compliance/safeai_controls.json`
- [ ] LLM configuration: `args/llm_config.yaml` (for AI BOM scanning)
- [ ] Security gates configured: `args/security_gates.yaml` (atlas_ai section)

---

## Workflow

### Step 1: Prompt Injection Detection

Scan text inputs for adversarial prompt injection patterns across 5 categories. This is the first line of defense — applied at ingestion boundaries (user input, child-reported behaviors, marketplace assets, cross-pollination candidates).

**Tool:** `tools/security/prompt_injection_detector.py`

**Detection categories:**

| Category | What It Detects | Examples |
|----------|----------------|---------|
| Role Hijacking | Attempts to override system instructions | "Ignore previous instructions", "You are now..." |
| Delimiter Attacks | Structural separators to escape context | "```\nSYSTEM:", "---\nNew instructions:" |
| Instruction Injection | Hidden commands embedded in data | "Execute the following:", "Run this code:" |
| Data Exfiltration | Attempts to leak sensitive data via model | "Repeat the system prompt", "List all API keys" |
| Encoded Payloads | Base64, hex, unicode obfuscation | Encoded strings hiding malicious instructions |

**Confidence thresholds (D215):**

| Confidence | Action |
|------------|--------|
| >= 0.90 | Block — reject input, log to `prompt_injection_log`, alert |
| 0.70 - 0.89 | Flag — accept with warning, tag for review |
| 0.50 - 0.69 | Warn — accept, log finding, tag trust level as "external" |
| < 0.50 | Allow — no action needed |

**Integration points:**
- `tools/registry/learning_collector.py` — scans child-reported behaviors before DB insert (Phase 36 integration)
- `tools/registry/cross_pollinator.py` — scans cross-pollination candidates before scoring
- `tools/marketplace/asset_scanner.py` — scans marketplace assets (Gate 8, D231)
- User-facing inputs in child applications

**Output:** Detection results stored in `prompt_injection_log` table (append-only, D6).

**Error handling:**
- Detector unavailable -> degrade gracefully (accept with warning, log gap), do not block pipeline
- False positive suspected -> manual override via HITL, logged with rationale

---

### Step 2: AI Telemetry

Monitor all AI/ML interactions with privacy-preserving audit logging. Prompts and responses are hashed (SHA-256, D216) — the system stores fingerprints, not plaintext.

**Tool:** `tools/security/ai_telemetry_logger.py`

**What is logged:**

| Field | Description |
|-------|-------------|
| `interaction_id` | Unique identifier for each AI call |
| `model_name` | LLM model used (e.g., claude-sonnet-4-5-20250514) |
| `prompt_hash` | SHA-256 hash of the prompt (not plaintext) |
| `response_hash` | SHA-256 hash of the response (not plaintext) |
| `token_count` | Input + output tokens consumed |
| `latency_ms` | Round-trip time |
| `classification` | Data classification level of the interaction |

**Methods:**
- `log_ai_interaction()` — Record a single AI interaction
- `detect_anomalies()` — Statistical anomaly detection on interaction patterns
- `get_usage_summary()` — Aggregated usage report by model, project, time period

**Integration points:**
- `tools/registry/propagation_manager.py` — logs propagation events as AI telemetry (Phase 36 integration)
- `tools/registry/telemetry_collector.py` — extracts AI metrics from child heartbeat responses
- LLM Router (`tools/llm/router.py`) — all routed LLM calls

**Output:** Telemetry records stored in `ai_telemetry` table (append-only, D6).

**Error handling:**
- Telemetry logging fails -> warn, do not block the AI call (telemetry is observational, not blocking)
- Anomaly detection finds suspicious pattern -> log alert, notify ISSO, do not auto-block

---

### Step 3: AI Security Assessment (4 Frameworks)

Assess project AI security posture across 4 complementary frameworks. All assessors use the BaseAssessor pattern (D116) with crosswalk integration through the NIST 800-53 US hub (D111).

#### 3a: MITRE ATLAS Assessment

**Tool:** `tools/compliance/atlas_assessor.py`

**Catalog:** `context/compliance/atlas_mitigations.json` (35 mitigations, AML.M0000-AML.M0034)

**Automated checks:** 6 mitigations verified programmatically:
- AML.M0015 (Adversarial Input Detection) — prompt injection detector active
- AML.M0024 (AI Supply Chain Security) — AI BOM current, dependency audit passing
- AML.M0012 (Access Control) — agent permissions configured, RBAC enforced
- AML.M0013 (Audit and Logging) — AI telemetry active
- AML.M0019 (AI Model Monitoring) — monitoring endpoints configured
- AML.M0026 (Vulnerability Scanning) — SAST + dependency audit passing

**CLI:** `python tools/compliance/atlas_assessor.py --project-id "proj-123" --json`

#### 3b: OWASP LLM Top 10 Assessment

**Tool:** `tools/compliance/owasp_llm_assessor.py`

**Catalog:** `context/compliance/owasp_llm_top10.json`

**Covers:** LLM01 (Prompt Injection), LLM02 (Insecure Output), LLM03 (Training Data Poisoning), LLM04 (Model DoS), LLM05 (Supply Chain), LLM06 (Sensitive Info), LLM07 (Insecure Plugin), LLM08 (Excessive Agency), LLM09 (Overreliance), LLM10 (Model Theft)

**Crosswalk:** Through ATLAS to NIST 800-53 US hub (D220)

**CLI:** `python tools/compliance/owasp_llm_assessor.py --project-id "proj-123" --json`

#### 3c: NIST AI RMF Assessment

**Tool:** `tools/compliance/nist_ai_rmf_assessor.py`

**Catalog:** `context/compliance/nist_ai_rmf.json`

**4 Functions, 12 subcategories (D221):**
- **Govern** — Policies and accountability for AI risk management
- **Map** — Context and risk identification
- **Measure** — AI risk analysis and tracking
- **Manage** — AI risk treatment and monitoring

**CLI:** `python tools/compliance/nist_ai_rmf_assessor.py --project-id "proj-123" --json`

#### 3d: ISO/IEC 42001 Assessment

**Tool:** `tools/compliance/iso42001_assessor.py`

**Catalog:** `context/compliance/iso42001_controls.json`

**Crosswalk:** Through ISO 27001 international hub bridge (D222, D111)

**CLI:** `python tools/compliance/iso42001_assessor.py --project-id "proj-123" --json`

#### SAFE-AI Controls

**Catalog:** `context/compliance/safeai_controls.json` — 100 AI-affected NIST 800-53 controls with `ai_concern` narrative (D223)

Used by ATLAS assessor and multi-regime assessor for AI-specific control overlays.

**Output:** Assessment results stored in framework-specific tables (append-only, D6). Gate evaluation via `--gate` flag.

**Error handling:**
- Missing catalog file -> report gap, skip that framework, do not block other frameworks
- Crosswalk unavailable -> assess standalone without cross-framework correlation

---

### Step 4: AI Bill of Materials (AI BOM)

Catalog all AI/ML components in the project for supply chain visibility and audit.

**Tool:** `tools/security/ai_bom_generator.py`

**What is scanned:**

| Source | Components Discovered |
|--------|----------------------|
| `args/llm_config.yaml` | LLM providers, model names, versions, embedding models |
| `requirements.txt` | AI frameworks (openai, anthropic, boto3, ibm-watsonx-ai, etc.) |
| `.mcp.json` | MCP server configurations (Claude Code AI tool integrations) |

**Methods:**
- `scan_project()` — Full project scan, returns component inventory
- `store_bom()` — Persist BOM to `ai_bom` table
- `evaluate_gate()` — Check: BOM exists, not stale (90 days, `security_gates.yaml`), all components documented

**Gate conditions (blocking):**
- `ai_bom_missing` — No AI BOM generated for project
- `ai_bom_stale` — BOM older than 90 days (configurable)

**CLI:** `python tools/security/ai_bom_generator.py --project-id "proj-123" --project-dir . --json`

**Output:** BOM records stored in `ai_bom` table with component_type, component_name, version, provider, provenance, risk_level, classification.

**Error handling:**
- Config file missing -> skip that source, report partial BOM
- Unknown AI framework detected -> include with `risk_level: unknown`, flag for review

---

### Step 5: ATLAS Reporting

Generate comprehensive MITRE ATLAS compliance reports with CUI markings.

**Tool:** `tools/compliance/atlas_report_generator.py`

**Report sections (7):**
1. **Executive Summary** — Overall ATLAS coverage score, risk posture, key findings
2. **Mitigation Coverage** — 35 mitigations with status (implemented/partial/not_implemented/not_applicable)
3. **Technique Exposure Analysis** — Which ATLAS techniques the project is exposed to, mapped to mitigations
4. **OWASP LLM Cross-Reference** — How OWASP LLM findings correlate with ATLAS mitigations
5. **Gap Analysis** — Unmitigated techniques, missing controls, priority remediation items
6. **Remediation Recommendations** — Ordered by risk, with effort estimates and NIST control mappings
7. **NIST 800-53 Mapping** — All AI security controls mapped to NIST 800-53 via crosswalk engine

**CUI markings:** Applied via `classification_manager.py` based on project impact level (D5).

**CLI:** `python tools/compliance/atlas_report_generator.py --project-id "proj-123" --output-path /path/to/report --json`

**Output:** Formatted report (text or JSON) with classification banners and portion markings.

**Error handling:**
- No assessment data -> generate report with "Not Yet Assessed" sections, flag as incomplete
- Classification manager unavailable -> generate report without CUI markings, warn

---

### Step 6: ATLAS Red Teaming (Opt-In)

Conduct adversarial testing against AI components using 6 ATLAS-derived techniques. Red teaming is **opt-in only** (D219) — it is NEVER auto-executed.

**Tool:** `tools/security/atlas_red_team.py`

**Red team techniques:**

| Technique | ATLAS ID | What It Tests |
|-----------|----------|---------------|
| Prompt Injection | AML.T0051 | System prompt override, delimiter escape, instruction injection |
| Jailbreaking | AML.T0056 | Safety guardrail bypass, role-play attacks |
| Context Poisoning | AML.T0080 | Injecting adversarial context into retrieval systems |
| Data Leakage | AML.T0086 | Extracting training data, system prompts, API keys via model |
| Poisoned Agent Tool | AML.T0104 | Manipulating tool call parameters to execute unintended actions |
| Model Evasion | AML.T0015 | Adversarial inputs that cause misclassification |

**CLI:** `python tools/security/atlas_red_team.py --project-id "proj-123" --techniques all --json`

**Output:** Red team findings stored in audit trail with technique, severity, evidence, and remediation guidance.

**Error handling:**
- Red team test causes system error -> catch, log, continue with remaining techniques
- Finding with critical severity -> immediately halt red team, alert ISSO, do not auto-remediate

**Safety:** Red team runs in isolated environment only. Never executes against production endpoints.

---

### Step 7: Marketplace Hardening (Gates 8-9)

Apply AI-specific security scanning to marketplace assets before publication or cross-tenant sharing.

**Tool:** `tools/marketplace/asset_scanner.py` (extends existing 7-gate pipeline with Gates 8-9, D231)

**Gate 8: Prompt Injection Scan (Blocking)**
- Scans all asset files for injection patterns using `prompt_injection_detector.py`
- Any high-confidence injection -> block publication
- Applied to: skill files, goal files, hardprompt files, context files

**Gate 9: Behavioral Sandbox (Warning)**
- Scans for dangerous code patterns (file system access, network calls, subprocess execution)
- Findings generate warnings, not blocks (advisory)
- Applied to: tool scripts, args configurations

**Integration:** These gates run as part of `publish_pipeline.py` for all marketplace submissions.

**Error handling:**
- Scanner timeout -> fail open with warning, flag for manual review
- Asset contains obfuscated code -> escalate to ISSO, do not publish

---

## Outputs

- Prompt injection detection results (`prompt_injection_log` table)
- AI telemetry records (`ai_telemetry` table)
- ATLAS assessment results (`atlas_assessments` table)
- OWASP LLM assessment results (via assessor)
- NIST AI RMF assessment results (via assessor)
- ISO 42001 assessment results (via assessor)
- AI BOM inventory (`ai_bom` table)
- ATLAS compliance reports (formatted text/JSON with CUI markings)
- Red team findings (audit trail)
- Marketplace security scan results (`marketplace_scan_results` table)

---

## Error Handling

- If prompt injection detector is unavailable: degrade gracefully — accept inputs with warning, log the gap, do not block pipeline
- If AI telemetry logging fails: warn but do not block AI calls (telemetry is observational)
- If ATLAS catalog files are missing: report gap, skip ATLAS assessment, do not block other frameworks
- If AI BOM scan finds unknown components: include with `risk_level: unknown`, flag for review
- If red team causes system error: catch and log, continue with remaining techniques
- If marketplace asset contains obfuscated code: escalate to ISSO, do not auto-publish

---

## Security Gates

| Gate | Condition | Severity |
|------|-----------|----------|
| `critical_atlas_technique_unmitigated` | Critical ATLAS technique has no mitigation | Blocking |
| `prompt_injection_defense_inactive` | Prompt injection detector not operational | Blocking |
| `ai_telemetry_not_active` | AI telemetry logging not enabled | Blocking |
| `agent_permissions_not_configured` | Agent RBAC not configured | Blocking |
| `ai_bom_missing` | No AI BOM generated | Blocking |
| `atlas_coverage_below_threshold` | ATLAS mitigation coverage < 80% | Warning |
| `owasp_llm_gap_detected` | OWASP LLM assessment has gaps | Warning |
| `ai_bom_stale` | AI BOM older than 90 days | Warning |
| `red_team_scan_required_for_il5_plus` | IL5+ projects require red team scan | Warning |

---

## Phase 36 Cross-Integration

Phase 37 security is integrated into Phase 36's Evolutionary Intelligence pipeline to prevent adversarial content from entering the capability genome:

| Integration Point | Phase 36 Tool | Phase 37 Defense |
|-------------------|---------------|------------------|
| Behavior ingestion | `learning_collector.py` | Prompt injection scan before DB insert; trust_level tagging |
| Cross-pollination | `cross_pollinator.py` | Injection scan on candidate evidence before scoring |
| Capability evaluation | `capability_evaluator.py` | 7th dimension: `security_assessment` (weight 0.10) |
| Propagation | `propagation_manager.py` | AI telemetry logging for audit trail |
| Telemetry collection | `telemetry_collector.py` | AI metrics extraction from child heartbeats |

This integration addresses AML.T0080 (Context Poisoning) and AML.T0104 (Poisoned Agent Tool) — the primary threats to capability evolution pipelines.

---

## Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D215 | 5 detection categories for prompt injection | Covers known attack vectors: role hijacking, delimiter, instruction injection, data exfiltration, encoded payloads |
| D216 | SHA-256 hashing for AI telemetry (not plaintext) | Privacy-preserving audit — stores fingerprints, not sensitive prompts/responses |
| D217 | AI BOM tracks all AI/ML components | Supply chain visibility for AI dependencies, analogous to SBOM for software |
| D218 | ATLAS assessor uses BaseAssessor pattern (D116) | Consistent with all other compliance assessors; crosswalk, gate, CLI included |
| D219 | Red teaming is opt-in only (`--atlas-red-team`) | Adversarial tests must never auto-execute; explicit human decision required |
| D220 | OWASP LLM crosswalks through ATLAS to NIST US hub | Single integration path maintains dual-hub model (D111) consistency |
| D221 | NIST AI RMF covers 4 functions, 12 subcategories | Complete coverage of NIST AI 100-1 framework |
| D222 | ISO 42001 bridges through ISO 27001 international hub | Maintains dual-hub crosswalk model (D111) |
| D223 | SAFE-AI maps 100 AI-affected NIST 800-53 controls | Identifies which existing controls have AI-specific concerns |
| D231 | Marketplace Gates 8-9 (injection + sandbox) | Prevent adversarial content from entering marketplace ecosystem |

---

## GOTCHA Layer Mapping

| Step | GOTCHA Layer | Component |
|------|-------------|-----------|
| Prompt Injection Detection | Tools | `prompt_injection_detector.py` |
| AI Telemetry | Tools | `ai_telemetry_logger.py` |
| ATLAS Assessment | Tools | `atlas_assessor.py` |
| OWASP LLM Assessment | Tools | `owasp_llm_assessor.py` |
| NIST AI RMF Assessment | Tools | `nist_ai_rmf_assessor.py` |
| ISO 42001 Assessment | Tools | `iso42001_assessor.py` |
| AI BOM Generation | Tools | `ai_bom_generator.py` |
| ATLAS Reporting | Tools | `atlas_report_generator.py` |
| Red Teaming | Tools | `atlas_red_team.py` |
| Marketplace Hardening | Tools | `asset_scanner.py` |
| Security gate thresholds | Args | `args/security_gates.yaml` (atlas_ai section) |
| ATLAS catalogs | Context | `context/compliance/atlas_mitigations.json`, `atlas_techniques.json` |
| AI framework catalogs | Context | `owasp_llm_top10.json`, `nist_ai_rmf.json`, `iso42001_controls.json`, `safeai_controls.json` |

---

## Related Files

- **Goals:** `goals/evolutionary_intelligence.md` (Phase 36 — cross-integration), `goals/marketplace.md` (Phase 22 — Gates 8-9), `goals/universal_compliance.md` (Phase 23 — multi-regime assessment)
- **Tools:** `tools/security/` (prompt_injection_detector, ai_telemetry_logger, atlas_red_team, ai_bom_generator), `tools/compliance/` (atlas_assessor, owasp_llm_assessor, nist_ai_rmf_assessor, iso42001_assessor, atlas_report_generator)
- **Args:** `args/security_gates.yaml` (atlas_ai section)
- **Context:** `context/compliance/` (atlas_mitigations.json, atlas_techniques.json, owasp_llm_top10.json, nist_ai_rmf.json, iso42001_controls.json, safeai_controls.json)
- **Tests:** `tests/test_atlas_assessor.py`, `tests/test_ai_bom_generator.py`, `tests/test_prompt_injection_detector.py`, `tests/test_ai_telemetry.py`, `tests/test_atlas_red_team.py`, `tests/test_phase36_phase37_integration.py`

---

## Changelog

- 2026-02-21: Initial creation — MITRE ATLAS Integration goal with 7-step workflow (prompt injection, AI telemetry, 4 framework assessments, AI BOM, reporting, red teaming, marketplace hardening), Phase 36 cross-integration, architecture decisions D215-D223/D231
