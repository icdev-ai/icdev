# Phase 45 — OWASP Agentic AI Security

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 45 |
| Title | OWASP Agentic AI Security — Runtime Behavioral Defense |
| Status | Implemented |
| Priority | P1 |
| Dependencies | Phase 37 (MITRE ATLAS Integration), Phase 36 (Evolutionary Intelligence), Phase 24 (DevSecOps), Phase 25 (ZTA) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

Phase 37 (MITRE ATLAS Integration) addresses static AI threats — prompt injection detection, model supply chain attacks, training data poisoning, and adversarial input testing. However, agentic AI systems introduce a fundamentally different class of risk: runtime behavioral threats. An agent that passes all Phase 37 static checks can still drift into unsafe behavior over time, abuse tool chains in unexpected sequences, leak classified data in generated outputs, escalate privileges through chained tool calls, or evolve behavior that diverges from its operational baseline.

OWASP's Agentic AI Threats v1.1, Securing Agentic Applications v1.0, and MCP Security Guide v1.0 identify 17 threat categories (T1-T17) specifically targeting agentic systems. These include unauthorized actions (T1), excessive autonomy (T2), insufficient monitoring (T3), insecure output handling (T5), and 12 additional threats covering tool abuse, memory poisoning, cascading failures, and output weaponization. ICDEV's 15-agent multi-agent architecture, with 15 MCP servers and A2A protocol communication, presents a substantial attack surface for these runtime behavioral threats.

Phase 45 closes 8 specific security gaps not addressed by Phase 37: behavioral drift detection (z-score statistical monitoring), tool chain validation (declarative sequence rules), output content safety (classification leak and PII detection), formal STRIDE + T1-T17 threat modeling, dynamic per-agent trust scoring (exponential decay on violation, linear recovery on clean operation), MCP per-tool RBAC (deny-first authorization matrix), behavioral red teaming (6 techniques targeting agentic behavior), and a comprehensive OWASP Agentic assessor (17 automated checks with NIST 800-53 crosswalk).

---

## 2. Goals

1. Detect behavioral drift in agent execution patterns using z-score statistical analysis against rolling baselines
2. Validate tool call sequences against declarative rules to prevent unauthorized tool chain patterns
3. Scan agent-generated outputs for classification leaks, PII, prompt echo, and harmful content before delivery
4. Maintain a formal STRIDE + OWASP T1-T17 threat model as a living document with machine-checkable freshness
5. Assign per-agent dynamic trust scores that decay on violations and recover on clean operation, gating agent permissions by trust level
6. Enforce role-based access control at the individual MCP tool level with a deny-first authorization matrix
7. Conduct behavioral red teaming with 6 techniques targeting agentic behavior patterns (opt-in, static analysis only)
8. Provide a comprehensive OWASP Agentic assessor with 17 automated checks and NIST 800-53 crosswalk integration

---

## 3. Architecture

```
Agent Execution Flow
         │
    ┌────┴────────────────────────────────────┐
    │  MCP Tool Authorizer (Gap 6)            │
    │  deny-first RBAC per tool per role      │
    │  ──→ Denied? Log + Block                │
    └────┬────────────────────────────────────┘
         │
    ┌────┴────────────────────────────────────┐
    │  Tool Chain Validator (Gap 2)           │
    │  sliding-window sequence matching       │
    │  ──→ Forbidden sequence? Block + Alert  │
    └────┬────────────────────────────────────┘
         │
    ┌────┴────────────────────────────────────┐
    │  Agent Execution (normal)               │
    └────┬────────────────────────────────────┘
         │
    ┌────┴────────────────────────────────────┐
    │  Output Content Safety (Gap 3)          │
    │  classification leak + PII + prompt echo│
    │  ──→ Violation? Block + Log             │
    └────┬────────────────────────────────────┘
         │
    ┌────┴────────────────────────────────────┐
    │  Trust Scorer (Gap 5)                   │
    │  decay on violation, recover on clean   │
    │  ──→ Untrusted? Restrict permissions    │
    └────┬────────────────────────────────────┘
         │
    ┌────┴────────────────────────────────────┐
    │  Behavioral Drift Detector (Gap 1)      │
    │  z-score on tool freq, tokens, errors   │
    │  ──→ z>2.0? Alert  z>3.0? ISSO notify  │
    └─────────────────────────────────────────┘

Offline/Periodic:
  Behavioral Red Team (Gap 7) — opt-in, static checks
  OWASP Agentic Assessor (Gap 8) — 17 checks + gate
  Formal Threat Model (Gap 4) — living document, quarterly review
```

The security controls form a layered defense: MCP authorization gates tool access, tool chain validation enforces sequence rules, output safety scans results, trust scoring adjusts permissions dynamically, and drift detection monitors for anomalous patterns. Behavioral red teaming and the OWASP assessor run periodically for comprehensive evaluation.

---

## 4. Requirements

### 4.1 Behavioral Drift Detection

#### REQ-45-001: Z-Score Drift Detection
The system SHALL compute z-scores for per-agent metrics (tool call frequency, token volume, error rate, latency) against a rolling 7-day baseline window. Z-score thresholds: >2.0 triggers alert, >3.0 triggers ISSO notification.

#### REQ-45-002: Drift Event Logging
Drift events SHALL be stored in the `ai_telemetry` table with `event_type: behavioral_drift` (append-only).

### 4.2 Tool Chain Validation

#### REQ-45-003: Declarative Sequence Rules
Tool chain rules SHALL be defined in YAML with support for: sequence enforcement (A must precede B), forbidden sequences (A followed by B blocked), rate limits (max calls per agent per minute), and depth limits (max chained calls per request).

#### REQ-45-004: Sliding Window Tracking
The system SHALL track per-agent tool call history using a sliding window for sequence matching and violation detection.

### 4.3 Output Content Safety

#### REQ-45-005: Classification Leak Detection
The system SHALL detect CUI/SECRET classification markers in outputs destined for channels below the content's classification level.

#### REQ-45-006: PII Detection
The system SHALL detect SSN patterns, email addresses, phone numbers, and credentials in agent-generated outputs using regex-based pattern matching.

### 4.4 Dynamic Trust Scoring

#### REQ-45-007: Trust Score Dynamics
Trust scores SHALL use exponential decay on violation (score * 0.8 per violation) and linear recovery on clean operation (+0.01 per clean hour). Trust levels: Untrusted (<0.30), Degraded (0.30-0.49), Cautious (0.50-0.69), Normal (>=0.70).

#### REQ-45-008: Trust-Gated Permissions
Agent permissions SHALL be gated by trust level: Untrusted agents restricted to read-only with ISSO alert, Degraded agents restricted to a limited tool set with enhanced monitoring.

### 4.5 MCP Per-Tool Authorization

#### REQ-45-009: Deny-First RBAC Matrix
MCP tool authorization SHALL follow a deny-first model with 5 roles (admin, pm, developer, isso, co) mapped to allowed/denied tool lists in YAML configuration.

#### REQ-45-010: Authorization Audit
All denied tool access attempts SHALL be logged to the audit trail with requester identity, role, requested tool, and denial reason.

### 4.6 Behavioral Red Teaming

#### REQ-45-011: 6 BRT Techniques
The system SHALL support 6 behavioral red team techniques: BRT-001 (Goal Hijacking), BRT-002 (Tool Chain Manipulation), BRT-003 (Privilege Escalation), BRT-004 (Memory Poisoning), BRT-005 (Cascading Failure), BRT-006 (Output Weaponization).

#### REQ-45-012: Opt-In Static Analysis
Behavioral red teaming SHALL be opt-in only, using static analysis checks (not live exploitation), running against test fixtures only.

### 4.7 OWASP Agentic Assessment

#### REQ-45-013: 17 Automated Checks
The OWASP Agentic assessor SHALL evaluate 17 threat categories (T1-T17) with automated checks including trust scoring active, per-tool authorization enforced, telemetry active, drift detection enabled, and output safety validator operational.

#### REQ-45-014: NIST 800-53 Crosswalk
Assessment results SHALL crosswalk through the NIST 800-53 US hub (D111) via RA-3, SA-11, SI-4, AU-6, cascading to FedRAMP/CMMC/800-171.

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `tool_chain_events` | Append-only log of tool chain validation events — agent_id, tool sequence, rule_id, violation type, timestamp |
| `agent_trust_scores` | Append-only trust score history — agent_id, score, trust_level, change_reason (violation/recovery/reset), timestamp |
| `agent_output_violations` | Append-only output safety violations — agent_id, violation_type (classification_leak/pii/prompt_echo/harmful), severity, content_hash, timestamp |
| `owasp_agentic_assessments` | Assessment results — project_id, threat_id, status (satisfied/not_satisfied/not_applicable), evidence, timestamp |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/security/ai_telemetry_logger.py` | Behavioral drift detection (--drift flag) — z-score computation on per-agent metrics |
| `tools/security/tool_chain_validator.py` | Tool chain validation — declarative sequence rules, sliding window, violation logging |
| `tools/security/agent_output_validator.py` | Output content safety — classification leak, PII, prompt echo, harmful content detection |
| `tools/security/agent_trust_scorer.py` | Dynamic trust scoring — exponential decay, linear recovery, trust-gated permissions |
| `tools/security/mcp_tool_authorizer.py` | MCP per-tool RBAC — deny-first authorization matrix, 5 roles, audit logging |
| `tools/security/atlas_red_team.py` | Behavioral red teaming (--behavioral flag) — 6 BRT techniques, opt-in, static analysis |
| `tools/compliance/owasp_agentic_assessor.py` | OWASP Agentic assessment — 17 automated checks, BaseAssessor pattern, NIST crosswalk, gate |

---

## 7. Architecture Decisions

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

## 8. Security Gate

**OWASP Agentic Gate:**
- **Blocking:** Agent trust score below untrusted threshold (0.30), critical tool chain violation detected, output classification leak detected, behavioral drift at critical level, MCP authorization not configured
- **Warning:** Behavioral red team not run for IL5+ projects, any agent trust score below 0.50, tool chain violations in last 24 hours, threat model not reviewed in 90 days
- **Thresholds:** min_trust_score=0.30, max_critical_chain_violations=0, max_critical_output_violations=0

---

## 9. Commands

```bash
# Behavioral drift detection
python tools/security/ai_telemetry_logger.py --drift --json
python tools/security/ai_telemetry_logger.py --drift --agent-id "builder-agent" --json

# Tool chain validation
python tools/security/tool_chain_validator.py --rules --json
python tools/security/tool_chain_validator.py --gate --project-id "proj-123" --json

# Output content safety
python tools/security/agent_output_validator.py --text "some output" --json
python tools/security/agent_output_validator.py --gate --project-id "proj-123" --json

# Dynamic trust scoring
python tools/security/agent_trust_scorer.py --score --agent-id "builder-agent" --json
python tools/security/agent_trust_scorer.py --check --agent-id "builder-agent" --json
python tools/security/agent_trust_scorer.py --all --json
python tools/security/agent_trust_scorer.py --gate --project-id "proj-123" --json

# MCP per-tool authorization
python tools/security/mcp_tool_authorizer.py --check --role developer --tool scaffold --json
python tools/security/mcp_tool_authorizer.py --list --role pm --json
python tools/security/mcp_tool_authorizer.py --validate --json

# Behavioral red teaming (opt-in)
python tools/security/atlas_red_team.py --behavioral --json
python tools/security/atlas_red_team.py --behavioral --brt-technique BRT-001 --json

# OWASP Agentic assessment
python tools/compliance/owasp_agentic_assessor.py --project-id "proj-123" --json
python tools/compliance/owasp_agentic_assessor.py --project-id "proj-123" --gate

# Configuration
# args/owasp_agentic_config.yaml — drift thresholds, tool chain rules, output validation,
#   trust scoring, MCP authorization matrix
# args/security_gates.yaml — owasp_agentic gate blocking/warning conditions
# context/compliance/owasp_agentic_threats.json — OWASP T1-T17 threat definitions
```
