# [TEMPLATE: CUI // SP-CTI]

# Goal: MOSA Workflow — Modular Open Systems Approach

**Authority:** 10 U.S.C. Section 4401, DoDI 5000.87 (Software Acquisition Pathway)

## Purpose

Enforce Modular Open Systems Approach (MOSA) principles across all DoD/IC software projects. This workflow auto-detects MOSA applicability during requirements intake, assesses modularity and interface openness, generates Interface Control Documents (ICDs) and Technical Standard Profiles (TSPs), enforces MOSA-compliant code structure, and feeds architecture evidence into the cATO pipeline.

**Why this matters:** 10 U.S.C. Section 4401 mandates MOSA for all major defense acquisition programs. DoDI 5000.87 reinforces this for software-intensive systems. Systems that fail to demonstrate modular design, open interfaces, and published standards risk acquisition milestone disapproval, vendor lock-in, and inability to integrate with future DoD enterprise services. ICDEV auto-enforces MOSA so compliance is continuous, not a last-minute documentation exercise.

---

## Prerequisites

- [ ] Project initialized (`goals/init_project.md` completed)
- [ ] ICDEV database initialized (`python tools/db/init_icdev_db.py`)
- [ ] `args/mosa_config.yaml` present (modularity thresholds, ICD templates, TSP standard catalogs, gate criteria)
- [ ] MOSA requirements catalog loaded (`context/compliance/mosa_requirements.json`)
- [ ] FIPS 199 categorization completed (`goals/security_categorization.md`) — baseline drives rigor
- [ ] `memory/MEMORY.md` loaded (session context)

---

## When to Use

- **DoD/IC customer indicator:** Customer org is DoD, IC, or defense contractor (detected during intake)
- **Impact level IL4+:** All IL4/IL5/IL6 projects with DoD customer trigger MOSA per D125
- **Keyword detection:** Customer mentions "modular," "open architecture," "MOSA," "interoperability," "interface control," "vendor lock-in avoidance," or "technical standard profile"
- **DoDI 5000.87 applicability:** Any software acquisition pathway program
- **Manual trigger:** Customer or ISSO explicitly requests MOSA assessment via `/icdev-mosa`

---

## Process

### Step 1: MOSA Signal Detection

**Tool:** `python tools/requirements/intake_engine.py --session-id <id> --message "<customer message>" --json`

During conversational intake (RICOAS Stage 2), the intake engine detects MOSA signals:
- DoD/IC organizational indicators (DoD, Army, Navy, USAF, USMC, USSF, DIA, NGA, NSA, NRO, etc.)
- MOSA-specific terminology (modular design, open interfaces, standard APIs, vendor-neutral)
- DoDI 5000.87 or 10 U.S.C. 4401 references
- Impact level IL4+ with defense customer context
- Existing ICD or TSP document references

**Expected output (within intake turn response):**
```
MOSA signals detected:
  - customer_org: DoD PEO (confidence: 0.95)
  - impact_level: IL5 — MOSA required
  - mosa_keywords: ["modular", "open architecture"] (confidence: 0.85)
  - existing_icds: none_detected
  - existing_tsp: none_detected
  - mosa_applicability: REQUIRED
  - trigger: dod_customer + IL4+
```

**Error handling:**
- No DoD/IC signals → MOSA not required; skip workflow, log reason
- Ambiguous customer org → probe with clarifying question before triggering
- IL2/IL3 non-DoD project → MOSA recommended but not enforced; advisory mode

**Verify:** MOSA detection results stored in DB. Trigger reason documented in audit trail.

---

### Step 2: MOSA Compliance Assessment

**Tool:** `python tools/compliance/mosa_assessor.py --project-id <id> --json`

Run full assessment against 25 MOSA requirements organized across 6 families:

| Family | Requirements | Focus |
|--------|-------------|-------|
| Modularity | 5 | Loose coupling, high cohesion, separation of concerns, encapsulation, composability |
| Open Interfaces | 5 | Published APIs, standard protocols, interface documentation, backward compatibility, versioning |
| Standards Compliance | 4 | Adherence to approved TSP standards, no proprietary lock-in, open data formats |
| Interoperability | 4 | Cross-system data exchange, standard messaging, service discovery, federation support |
| Reusability | 4 | Component reuse potential, packaging, dependency isolation, configuration externalization |
| Maintainability | 3 | Independent deployment, hot-swap capability, technology refresh readiness |

**Expected output:**
```
MOSA Compliance Assessment complete.

Family Results (25 requirements):
  Modularity:          5/5 — 3 satisfied, 2 partial
  Open Interfaces:     5/5 — 4 satisfied, 1 not_satisfied
  Standards Compliance: 4/4 — 3 satisfied, 1 partial
  Interoperability:    4/4 — 2 satisfied, 1 partial, 1 not_assessed
  Reusability:         4/4 — 3 satisfied, 1 partial
  Maintainability:     3/3 — 2 satisfied, 1 partial

Overall: 17/25 satisfied, 6 partial, 1 not_satisfied, 1 not_assessed
MOSA score: 78%
Gate: PASS (with warnings)
```

**Error handling:**
- Requirements catalog missing → fail with path to expected `context/compliance/mosa_requirements.json`
- No code to assess → run in documentation-only mode, assess architecture artifacts
- Single monolith → flag modularity family as at-risk, generate decomposition recommendations

**Verify:** All 6 families assessed. No critical requirements `not_satisfied` without documented risk acceptance or POAM.

---

### Step 3: Modularity Analysis

**Tool:** `python tools/mosa/modular_design_analyzer.py --project-dir <path> --project-id <id> --store --json`

Static analysis of project code to compute modularity metrics:
- **Coupling score:** Afferent/efferent coupling per module, instability index
- **Cohesion score:** LCOM (Lack of Cohesion of Methods) per module
- **Interface coverage:** Percentage of external-facing functions with defined API specs
- **Circular dependency detection:** Module-level dependency graph cycle analysis
- **Module independence:** Ratio of internal vs external dependencies per module

**Expected output:**
```
Modularity Analysis complete.

Metrics:
  Modules analyzed: 12
  Avg coupling (Ce/Ca): 0.35 (target: <0.5 — PASS)
  Avg cohesion (LCOM): 0.72 (target: >0.6 — PASS)
  Interface coverage: 85% (target: >80% — PASS)
  Circular dependencies: 1 detected (target: 0 — FAIL)
    tools.builder → tools.compliance → tools.builder
  Module independence: 0.78 (target: >0.7 — PASS)

Overall modularity score: 74/100
Trend: +3 from last analysis (improving)
```

**Error handling:**
- Empty project (no code) → skip analysis, return advisory with recommendations
- Unsupported language → use generic import/dependency parsing, warn about reduced accuracy
- Very large codebase (>10k files) → sample top-level modules only, flag for full analysis later

**Verify:** Metrics stored in DB as time-series (D131). Circular dependencies flagged for resolution. Modularity score feeds into Step 7 gate.

---

### Step 4: ICD Generation

**Tool:** `python tools/mosa/icd_generator.py --project-id <id> --all --json`

Generate Interface Control Documents for all external-facing interfaces:
- Auto-discover interfaces from OpenAPI/Swagger specs, gRPC proto files, WSDL, and REST endpoints
- Generate ICD per interface with: protocol, data format, authentication, versioning, SLA, error handling
- Map each interface to NIST 800-53 controls (SC-7, SC-8, SA-9)
- Track ICD version history for change management

**Expected output:**
```
ICD Generation complete.

Interfaces discovered: 8
ICDs generated: 8
  - ICD-001: REST API Gateway (/api/v1/*) — OpenAPI 3.1
  - ICD-002: A2A Agent Protocol (JSON-RPC 2.0) — mTLS
  - ICD-003: Database Interface (PostgreSQL) — TLS 1.3
  - ICD-004: Message Queue (SQS/SNS) — IAM auth
  - ICD-005: S3 Artifact Store — IAM + encryption
  - ICD-006: LDAP/AD Authentication — LDAPS
  - ICD-007: SIEM Integration (Splunk HEC) — Token auth
  - ICD-008: External Partner API — OAuth 2.0

Output: projects/<id>/docs/icds/
Control mappings: SC-7, SC-8, SA-9, AC-4
```

**Error handling:**
- No OpenAPI spec found → generate skeleton ICD from code analysis, flag for manual completion
- Internal-only interfaces → exclude from ICD generation unless cross-boundary
- Undocumented interfaces detected → generate stub ICD, add to POAM for documentation

**Verify:** Every external interface has an ICD. Each ICD includes protocol, auth, versioning, and control mapping. No external interface left undocumented.

---

### Step 5: TSP Generation

**Tool:** `python tools/mosa/tsp_generator.py --project-id <id> --json`

Generate Technical Standard Profile documenting all standards, protocols, and data formats:
- Auto-detect standards from technology stack (languages, frameworks, protocols, data formats)
- Validate against DoD-approved standard catalogs
- Flag proprietary or non-standard technologies for review
- Map standards to MOSA requirement families

**Expected output:**
```
TSP Generation complete.

Standards detected: 14
  Communication: HTTP/2, TLS 1.3, JSON-RPC 2.0, gRPC
  Data Formats: JSON (RFC 8259), XML, YAML, CycloneDX 1.5
  Authentication: OAuth 2.0, SAML 2.0, mTLS (X.509)
  Encryption: AES-256-GCM, RSA-2048, FIPS 140-2 validated
  APIs: OpenAPI 3.1, REST, A2A Protocol

Proprietary flags: 0
Non-standard flags: 1
  - YAML for IaC configs (recommend: also support JSON for tooling interop)

Output: projects/<id>/docs/tsp/technical_standard_profile.md
```

**Error handling:**
- Unknown framework/library → flag as "unclassified" in TSP, require manual categorization
- Proprietary standard detected → generate risk entry with vendor lock-in assessment
- No standards catalog available → use built-in defaults from `args/mosa_config.yaml`

**Verify:** TSP covers all technology layers. Zero proprietary standards without documented justification. TSP version-controlled for change tracking.

---

### Step 6: Code Enforcement

**Tool:** `python tools/mosa/mosa_code_enforcer.py --project-dir <path> --fix-suggestions --json`

Scan codebase for MOSA violations and generate fix suggestions:
- **Tight coupling violations:** Direct cross-module imports bypassing defined interfaces
- **Boundary violations:** Data access crossing module boundaries without API
- **Missing interface specs:** Public functions without OpenAPI/proto definitions
- **Hardcoded dependencies:** Service URLs, connection strings, or vendor-specific SDK calls without abstraction
- **Circular dependencies:** Import cycles between modules

**Expected output:**
```
MOSA Code Enforcement scan complete.

Violations: 7
  [HIGH] 2 tight coupling violations
    - tools/builder/code_generator.py imports tools.compliance.control_mapper directly
    - tools/dashboard/app.py imports tools.db.init_icdev_db directly
  [MEDIUM] 3 missing interface specs
    - tools/mcp/core_server.py:handle_request() — no OpenAPI annotation
    - tools/saas/api_gateway.py:route_request() — no OpenAPI annotation
    - tools/integration/jira_connector.py:push_issues() — no OpenAPI annotation
  [LOW] 2 hardcoded dependencies
    - tools/llm/provider.py:L45 — hardcoded "http://localhost:11434"
    - tools/infra/terraform_generator.py:L92 — hardcoded "us-gov-west-1"

Fix suggestions generated: 7
Output: projects/<id>/docs/mosa/enforcement_report.json
```

**Error handling:**
- No violations found → log clean scan, update modularity score to reflect compliance
- Too many violations (>50) → prioritize by severity, report top 20 with "and N more" summary
- Fix suggestions not applicable → mark as manual review required

**Verify:** All HIGH violations have fix suggestions. Enforcement results feed into Step 7 gate evaluation.

---

### Step 7: Security Gate Check

**Tool:** `python tools/compliance/mosa_assessor.py --project-id <id> --gate`

Evaluate MOSA gate criteria. This gate integrates with the existing ICDEV security gate framework (`args/security_gates.yaml`).

**MOSA Gate Criteria:**

| Criteria | Blocking | Threshold |
|----------|----------|-----------|
| External interface without ICD | Yes | 0 allowed |
| Circular module dependencies | Yes | 0 allowed |
| MOSA compliance score | Yes | >= 60% |
| Modularity score | Warn | >= 50% (block at < 40%) |
| Interface coverage | Warn | >= 80% |
| Proprietary standards without justification | Warn | 0 recommended |
| HIGH code enforcement violations | Warn | 0 recommended (block at > 5) |
| TSP generated | Yes | Required |

**Expected output:**
```
MOSA Gate Evaluation:

  External interfaces without ICD:     0 — PASS
  Circular dependencies:               1 — FAIL (blocking)
  MOSA compliance score:               78% — PASS (threshold: 60%)
  Modularity score:                    74/100 — PASS
  Interface coverage:                  85% — PASS
  Proprietary standards:               0 — PASS
  HIGH enforcement violations:         2 — WARN
  TSP generated:                       Yes — PASS

  Overall: FAIL (1 blocking criteria)
  Action required: Resolve circular dependency in tools.builder ↔ tools.compliance
```

**Error handling:**
- Assessment data missing → run Steps 2-6 first, do not evaluate partial data
- Gate fails → generate remediation plan with specific actions per failing criteria
- Customer requests gate waiver → require ISSO approval, record in audit trail with expiration

**Verify:** Gate result stored in DB. Blocking failures prevent deployment. Warnings logged but do not block.

---

### Step 8: cATO Evidence Collection (Optional)

**Condition:** Only if `mosa_config.yaml` has `cato_integration.enabled: true`

**Tool:** `python tools/compliance/cato_monitor.py --project-id <id> --add-evidence --evidence-type mosa_architecture --json`

Collect MOSA architecture evidence for continuous authorization:
- **SA-3 (System Development Life Cycle):** MOSA design principles applied during development
- **SA-8 (Security and Privacy Engineering Principles):** Modular, open, standards-based architecture
- **SA-17 (Developer Security and Privacy Architecture and Design):** Interface documentation, separation of concerns

**Expected output:**
```
cATO evidence updated.

Evidence added:
  Type: mosa_architecture
  MOSA score: 78%
  Modularity score: 74/100
  ICD count: 8 (all interfaces documented)
  TSP standards: 14 (0 proprietary)
  Controls covered: SA-3, SA-8, SA-17
  Timestamp: 2026-02-18T15:00:00Z
  Freshness: current (< 24 hours)

cATO readiness (with MOSA):
  Traditional evidence: 85%
  MOSA architecture: 78%
  Combined readiness: 83%
```

**Error handling:**
- cATO not enabled → skip, log advisory
- MOSA assessment not yet run → run Steps 2-6 first
- Evidence already exists for today → update existing record, do not duplicate

**Verify:** Evidence stored in `cato_evidence` table. SA-3/SA-8/SA-17 controls mapped. Audit trail logged.

---

### Step 9: Log to Audit Trail

**Tool:** `python tools/audit/audit_logger.py --event-type "mosa.assessment" --actor "orchestrator" --action "MOSA assessment and gate evaluation completed" --project-id <id>`

**Tool:** `python tools/memory/memory_write.py --content "MOSA assessment for <id>. Score: <pct>%. Modularity: <score>/100. ICDs: <count>. Gate: <PASS|FAIL>" --type event --importance 7`

---

## Decision Flow

```
Intake begins
  ├── DoD/IC customer detected?
  │     ├── Yes + IL4+ → MOSA REQUIRED (auto-trigger)
  │     ├── Yes + IL2/IL3 → MOSA RECOMMENDED (advisory)
  │     └── No → MOSA NOT REQUIRED (skip)
  │
  MOSA triggered →
  ├── Step 2: Assess 25 requirements / 6 families
  ├── Step 3: Static modularity analysis
  ├── Step 4: Generate ICDs for all external interfaces
  ├── Step 5: Generate TSP from tech stack
  ├── Step 6: Enforce code-level MOSA compliance
  ├── Step 7: Gate check
  │     ├── PASS → proceed to deployment
  │     └── FAIL → remediate blocking criteria → re-run gate
  └── Step 8 (optional): Feed evidence to cATO
```

---

## Architecture Decisions

- **D125:** MOSA auto-triggers for all DoD/IC projects at IL4+ — no opt-out for mandatory programs
- **D126:** Software development principles only — hardware MOSA (MIL-STD-1760, VICTORY) is out of scope
- **D127:** Full compliance framework via BaseAssessor (D116 pattern) — crosswalk integration, gate evaluation, CLI for ~60 LOC per framework
- **D128:** ICD and TSP as generated artifacts — auto-discovered from code, not manually authored
- **D129:** Static analysis for enforcement — Python `ast`, import graph, and regex-based detection (air-gap safe, zero deps per D13)
- **D130:** cATO evidence is optional — enabled via `mosa_config.yaml` flag, not all projects use cATO
- **D131:** Modularity metrics stored as time-series — enables trend tracking and PI-over-PI improvement visualization

---

## Success Criteria

- [ ] MOSA signals detected during intake with trigger reason documented
- [ ] 25 MOSA requirements assessed across all 6 families
- [ ] Modularity analysis completed with coupling, cohesion, and dependency metrics
- [ ] ICDs generated for all external-facing interfaces with control mappings
- [ ] TSP generated documenting all standards with zero unjustified proprietary entries
- [ ] Code enforcement scan completed with violation report and fix suggestions
- [ ] MOSA gate evaluated with clear PASS/FAIL and remediation plan for failures
- [ ] cATO evidence collected for SA-3, SA-8, SA-17 (if cATO enabled)
- [ ] Audit trail entry logged for all assessment and gate events

---

## Edge Cases

1. **Non-DoD project:** MOSA is not required. Workflow skips entirely. Modularity analysis still available on-demand via `/icdev-mosa` for any project wanting to improve architecture quality.
2. **Monolithic legacy application:** Modularity score will be low. Generate decomposition recommendations aligned with `goals/modernization_workflow.md`. Do not fail gate for legacy-in-analysis — use advisory mode until migration begins.
3. **Air-gapped environment:** All analysis uses Python stdlib (`ast`, `importlib`, `xml.etree`). No external dependencies required. ICD/TSP generation works offline.
4. **No external interfaces:** Skip Step 4 (ICD generation). Internal modularity and standards compliance still assessed. Gate adjusts — ICD criterion becomes N/A.
5. **Microservice architecture:** Modularity score should be high by default. Focus shifts to interface coverage and standards compliance. Each microservice gets its own ICD.
6. **Mixed language project:** Run modularity analysis per language using `context/languages/language_registry.json`. Aggregate scores with weighted average by LOC proportion.
7. **Vendor-provided COTS components:** Treat as black-box modules. Require ICD for integration points. TSP must document vendor-specific protocols with lock-in risk assessment.
8. **MOSA gate waiver requested:** Require ISSO written approval. Record waiver with justification, scope, and 90-day expiration in audit trail. Waiver does not suppress warnings.

---

## GOTCHA Layer Mapping

| Step | GOTCHA Layer | Component |
|------|-------------|-----------|
| MOSA signal detection | Orchestration | AI detects MOSA signals during intake |
| Compliance assessment | Tools | mosa_assessor.py |
| Modularity analysis | Tools | modular_design_analyzer.py |
| ICD generation | Tools | icd_generator.py |
| TSP generation | Tools | tsp_generator.py |
| Code enforcement | Tools | mosa_code_enforcer.py |
| Gate evaluation | Tools + Args | mosa_assessor.py + mosa_config.yaml |
| cATO evidence | Tools | cato_monitor.py |
| Workflow sequencing | Orchestration | AI (you) |
| MOSA requirements | Context | mosa_requirements.json |
| Thresholds / templates | Args | mosa_config.yaml |

---

## Related Goals

- `goals/requirements_intake.md` — MOSA signals detected during RICOAS intake
- `goals/universal_compliance.md` — MOSA integrates with multi-regime compliance
- `goals/zero_trust_architecture.md` — ZTA and MOSA share SA-8 architecture controls
- `goals/devsecops_workflow.md` — DevSecOps profile drives pipeline; MOSA drives architecture
- `goals/compliance_workflow.md` — MOSA assessment feeds into overall compliance scoring
- `goals/modernization_workflow.md` — Legacy decomposition aligns with MOSA modularity targets
- `goals/ato_acceleration.md` — MOSA evidence feeds cATO readiness

## Related Commands

- `/icdev-mosa` — Run full MOSA workflow (Steps 1-8)
- `/icdev-comply` — Includes MOSA in multi-regime assessment when applicable
- `/icdev-intake` — Detects MOSA signals during conversational intake

## Related Files

- **Tools:** `tools/compliance/mosa_assessor.py`, `tools/mosa/modular_design_analyzer.py`, `tools/mosa/icd_generator.py`, `tools/mosa/tsp_generator.py`, `tools/mosa/mosa_code_enforcer.py`, `tools/compliance/cato_monitor.py`
- **Args:** `args/mosa_config.yaml`
- **Context:** `context/compliance/mosa_requirements.json`
- **Database:** `data/icdev.db` (mosa_assessments, mosa_modularity_metrics, mosa_icds, mosa_tsp tables)

---

## Changelog

- 2026-02-18: Initial creation
