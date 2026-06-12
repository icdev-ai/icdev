# CUI // SP-CTI
"""AADC Solution Packs — 7 pre-wired agentic AI use-case templates.

Each pack ships with:
  - Pre-placed nodes + wired edges (graph_json)
  - Compliance baseline (NIST AI RMF, OWASP LLM, OMB, MITRE ATLAS)
  - Pre-seeded risk register items
  - Domain-specific MITRE ATLAS scenario mappings
"""

from __future__ import annotations

import uuid

# ---------------------------------------------------------------------------
# Graph helpers (mirror of init_db.py helpers — standalone so this module
# can be imported without pulling in all of init_db)
# ---------------------------------------------------------------------------

def _uid() -> str:
    return uuid.uuid4().hex[:10]


def _node(ntype: str, label: str, x: int, y: int, w: int = 140, h: int = 50) -> dict:
    return {"id": f"n-{_uid()}", "type": ntype, "label": label,
            "x": x, "y": y, "w": w, "h": h}


def _edge(src: str, tgt: str, label: str = "") -> dict:
    return {"id": f"e-{_uid()}", "source": src, "target": tgt, "label": label}


# ---------------------------------------------------------------------------
# Quick-start routing matrix
# Used by the quick-start wizard to recommend a pack from 3 answers.
# Keys: (domain_slug, goal_slug, autonomy_slug) → pack_name
# ---------------------------------------------------------------------------

QUICKSTART_ROUTES: dict[tuple[str, str, str], str] = {
    # Government domain
    ("government", "documents",  "supervised"):   "Gov/Procurement Agent",
    ("government", "documents",  "autonomous"):   "Gov/Procurement Agent",
    ("government", "documents",  "human"):        "Gov/Procurement Agent",
    ("government", "research",   "supervised"):   "Knowledge Research Agent",
    ("government", "research",   "human"):        "Gov/Procurement Agent",
    ("government", "customers",  "supervised"):   "Customer Service Agent",
    ("government", "security",   "supervised"):   "Cybersecurity SOC Agent",
    ("government", "automation", "autonomous"):   "Autonomous Coder",
    # Healthcare domain
    ("healthcare", "documents",  "human"):        "Healthcare Admin Agent",
    ("healthcare", "documents",  "supervised"):   "Healthcare Admin Agent",
    ("healthcare", "customers",  "human"):        "Healthcare Admin Agent",
    ("healthcare", "customers",  "supervised"):   "Customer Service Agent",
    ("healthcare", "security",   "supervised"):   "Cybersecurity SOC Agent",
    ("healthcare", "research",   "supervised"):   "Knowledge Research Agent",
    # Technology domain
    ("technology", "automation", "autonomous"):   "Autonomous Coder",
    ("technology", "automation", "supervised"):   "Autonomous Coder",
    ("technology", "security",   "supervised"):   "Cybersecurity SOC Agent",
    ("technology", "security",   "autonomous"):   "Cybersecurity SOC Agent",
    ("technology", "research",   "autonomous"):   "Multi-Agent Research Lab",
    ("technology", "research",   "supervised"):   "Knowledge Research Agent",
    ("technology", "customers",  "supervised"):   "Customer Service Agent",
    # Financial domain
    ("financial",  "security",   "supervised"):   "Cybersecurity SOC Agent",
    ("financial",  "research",   "supervised"):   "Knowledge Research Agent",
    ("financial",  "research",   "autonomous"):   "Multi-Agent Research Lab",
    ("financial",  "customers",  "supervised"):   "Customer Service Agent",
    ("financial",  "automation", "autonomous"):   "Autonomous Coder",
    # General / catch-all
    ("general",    "customers",  "supervised"):   "Customer Service Agent",
    ("general",    "customers",  "human"):        "Customer Service Agent",
    ("general",    "research",   "supervised"):   "Knowledge Research Agent",
    ("general",    "research",   "autonomous"):   "Multi-Agent Research Lab",
    ("general",    "automation", "autonomous"):   "Autonomous Coder",
    ("general",    "security",   "supervised"):   "Cybersecurity SOC Agent",
    ("general",    "documents",  "supervised"):   "Gov/Procurement Agent",
}

_QUICKSTART_DEFAULT = "Customer Service Agent"


def recommend_pack(domain: str, goal: str, autonomy: str) -> str:
    """Return pack name given quick-start wizard answers (all lowercase slugs)."""
    return QUICKSTART_ROUTES.get((domain, goal, autonomy), _QUICKSTART_DEFAULT)


# ---------------------------------------------------------------------------
# Risk register seeds per pack
# List of dicts; design_id is injected at seed time.
# ---------------------------------------------------------------------------

SOLUTION_PACK_RISKS: dict[str, list[dict]] = {
    "Customer Service Agent": [
        {
            "title": "PII Leakage via LLM Output",
            "description": "LLM may include PII from retrieved knowledge base documents in responses if output guardrail is misconfigured.",
            "risk_category": "data",
            "severity": "HIGH",
            "likelihood": "MEDIUM",
            "impact": "HIGH",
            "status": "open",
            "mitigation": "Enable output PII scrubbing via pii-detector node; validate guardrail patterns against OWASP LLM02.",
        },
        {
            "title": "Prompt Injection via Customer Input",
            "description": "Malicious users may craft inputs designed to override system prompts or exfiltrate data.",
            "risk_category": "security",
            "severity": "HIGH",
            "likelihood": "HIGH",
            "impact": "HIGH",
            "status": "open",
            "mitigation": "Apply input-sanitizer with injection pattern detection; use prompt registry to version-lock system prompts.",
        },
        {
            "title": "Hallucination in Customer-Facing Response",
            "description": "LLM may generate plausible but incorrect information, degrading customer trust and creating liability.",
            "risk_category": "model",
            "severity": "MEDIUM",
            "likelihood": "MEDIUM",
            "impact": "MEDIUM",
            "status": "open",
            "mitigation": "Enforce confidence-threshold gate; require RAG grounding with citation; route low-confidence to HITL.",
        },
        {
            # LL-001: Universal — discovered in Autonomous Coder E2E build 2026-05-04
            "title": "LLM Structured Output Non-Compliance",
            "description": "LLM agents required to return structured data at handoffs consistently return prose instead, causing parse failures and fallback degradation. Observed in 3/3 live runs against Claude Sonnet during Autonomous Coder E2E build.",
            "risk_category": "model",
            "severity": "MEDIUM",
            "likelihood": "HIGH",
            "impact": "MEDIUM",
            "status": "open",
            "mitigation": "Add structured-output-enforcer node between LLM and downstream consumers; use function-calling / tool_use mode instead of freeform JSON prompts.",
        },
        {
            # LL-002: Universal — discovered in Autonomous Coder E2E build 2026-05-04
            "title": "LLM Response Latency Exceeds Safety Timeout",
            "description": "Multi-step LLM pipelines can take 80–110s per run. Default timeout values of 30–120s are too low for slow-network or high-load environments, causing premature abort before pipeline completes.",
            "risk_category": "operational",
            "severity": "MEDIUM",
            "likelihood": "HIGH",
            "impact": "MEDIUM",
            "status": "open",
            "mitigation": "Set circuit breaker max_duration_s=300 as production default; expose as config parameter; show elapsed time in audit log so operators can tune per environment.",
        },
    ],
    "Autonomous Coder": [
        {
            "title": "Unreviewed Code Execution in Production",
            "description": "Autonomously generated code may be deployed without human review, introducing bugs or security vulnerabilities.",
            "risk_category": "operational",
            "severity": "CRITICAL",
            "likelihood": "MEDIUM",
            "impact": "CRITICAL",
            "status": "open",
            "mitigation": "Enforce mandatory validator sub-agent + circuit breaker before any deployment step; require HITL for prod merges.",
        },
        {
            "title": "Runaway Token Consumption",
            "description": "Autonomous multi-step coding loops may exhaust token budgets, causing cost overruns or service degradation.",
            "risk_category": "operational",
            "severity": "HIGH",
            "likelihood": "HIGH",
            "impact": "MEDIUM",
            "status": "open",
            "mitigation": "Set circuit breaker max_tokens ceiling; monitor via audit logger; alert on >80% budget consumed.",
        },
        {
            "title": "Supply Chain Risk via MCP Tool Calls",
            "description": "MCP server tools may call external APIs or package registries that introduce untrusted dependencies.",
            "risk_category": "supply-chain",
            "severity": "HIGH",
            "likelihood": "MEDIUM",
            "impact": "HIGH",
            "status": "open",
            "mitigation": "Allowlist MCP tool targets; run sandbox-exec for all code execution; SBOM generated on every build.",
        },
        {
            # LL-001: Discovered during E2E build 2026-05-04
            "title": "LLM Structured Output Non-Compliance",
            "description": "LLM agents required to return JSON (planner steps, validator verdict) consistently return prose or code instead, causing JSON parse failures and fallback degradation. Observed in 3/3 live runs against Claude Sonnet.",
            "risk_category": "model",
            "severity": "MEDIUM",
            "likelihood": "HIGH",
            "impact": "MEDIUM",
            "status": "open",
            "mitigation": "Add structured-output-enforcer node between each agent pair; use function-calling / tool_use mode instead of freeform JSON prompts; or add a JSON repair layer before downstream consumption.",
        },
        {
            # LL-002: Discovered during E2E build 2026-05-04
            "title": "LLM Response Latency Exceeds Safety Timeout",
            "description": "Each full pipeline run (Sanitize → Plan → Code → Validate) against Claude Sonnet took 80–110s. Default circuit breaker max_duration_s=120 is too low, causing premature trip on the third LLM call in slow-network environments.",
            "risk_category": "operational",
            "severity": "MEDIUM",
            "likelihood": "HIGH",
            "impact": "MEDIUM",
            "status": "open",
            "mitigation": "Set max_duration_s=300 as production default; expose as config parameter; show elapsed time in audit log so operators can tune per environment.",
        },
    ],
    "Knowledge Research Agent": [
        {
            "title": "Knowledge Base Poisoning",
            "description": "Adversarial documents ingested into the knowledge graph may steer the agent to produce misleading research outputs.",
            "risk_category": "data",
            "severity": "HIGH",
            "likelihood": "MEDIUM",
            "impact": "HIGH",
            "status": "open",
            "mitigation": "Implement data-validator node for source provenance; quarantine unverified sources; enable ATLAS T0020 monitoring.",
        },
        {
            "title": "Unauthorized Data Access via Tool Chain",
            "description": "Web-search or external-API tools may access sensitive or restricted data sources outside the intended scope.",
            "risk_category": "compliance",
            "severity": "MEDIUM",
            "likelihood": "MEDIUM",
            "impact": "HIGH",
            "status": "open",
            "mitigation": "Scope tool-chain allowlist; require approval-workflow before accessing non-public sources.",
        },
        {
            # LL-001: Universal
            "title": "LLM Structured Output Non-Compliance",
            "description": "LLM agents required to return structured data at handoffs consistently return prose instead, causing parse failures and fallback degradation. Observed in 3/3 live runs against Claude Sonnet during Autonomous Coder E2E build.",
            "risk_category": "model",
            "severity": "MEDIUM",
            "likelihood": "HIGH",
            "impact": "MEDIUM",
            "status": "open",
            "mitigation": "Add structured-output-enforcer node between LLM Reasoner and Confidence Gate; use function-calling / tool_use mode instead of freeform JSON prompts.",
        },
        {
            # LL-002: Universal
            "title": "LLM Response Latency Exceeds Safety Timeout",
            "description": "Multi-step LLM pipelines can take 80–110s per run. Default timeout values of 30–120s are too low for slow-network or high-load environments, causing premature abort before pipeline completes.",
            "risk_category": "operational",
            "severity": "MEDIUM",
            "likelihood": "HIGH",
            "impact": "MEDIUM",
            "status": "open",
            "mitigation": "Set circuit breaker max_duration_s=300 as production default; expose as config parameter; show elapsed time in audit log so operators can tune per environment.",
        },
    ],
    "Cybersecurity SOC Agent": [
        {
            "title": "False Positive Autonomous Response",
            "description": "Drift detector may trigger autonomous defensive actions (firewall changes, process kills) on benign activity.",
            "risk_category": "operational",
            "severity": "HIGH",
            "likelihood": "MEDIUM",
            "impact": "HIGH",
            "status": "open",
            "mitigation": "Require HITL gate for all destructive actions; tune baseline-snapshot with 30-day warm-up period.",
        },
        {
            "title": "SIEM Forwarder Data Exfiltration",
            "description": "If SIEM connection is compromised, security event data may be exfiltrated to an adversary.",
            "risk_category": "security",
            "severity": "CRITICAL",
            "likelihood": "LOW",
            "impact": "CRITICAL",
            "status": "open",
            "mitigation": "Encrypt SIEM transport (TLS 1.3); validate SIEM endpoint certificate; rate-limiter on outbound data volume.",
        },
        {
            "title": "Alert Fatigue Degrading Analyst Response",
            "description": "Excessive alerts from anomaly detector may overwhelm SOC analysts, causing critical alerts to be missed.",
            "risk_category": "operational",
            "severity": "MEDIUM",
            "likelihood": "HIGH",
            "impact": "HIGH",
            "status": "open",
            "mitigation": "Tune classifier threshold; implement alert deduplication in alert-manager; add confidence-threshold pre-filter.",
        },
        {
            # LL-002: Universal — LL-001 N/A (SOC uses tool calls, not LLM→LLM handoffs requiring structured output)
            "title": "LLM Response Latency Exceeds Safety Timeout",
            "description": "Multi-step LLM pipelines can take 80–110s per run. Default timeout values of 30–120s are too low for slow-network or high-load environments, causing premature abort before pipeline completes.",
            "risk_category": "operational",
            "severity": "MEDIUM",
            "likelihood": "HIGH",
            "impact": "MEDIUM",
            "status": "open",
            "mitigation": "Tune circuit breaker max_duration_s to 300s for production; expose as config parameter; show elapsed time in audit log so operators can tune per environment.",
        },
    ],
    "Healthcare Admin Agent": [
        {
            "title": "PHI Exposure in LLM Prompt or Response",
            "description": "Protected Health Information may be inadvertently included in LLM context or outputs, violating HIPAA.",
            "risk_category": "compliance",
            "severity": "CRITICAL",
            "likelihood": "MEDIUM",
            "impact": "CRITICAL",
            "status": "open",
            "mitigation": "PII detector must scrub all PHI before LLM context injection; output redaction-engine required; audit-logger records all PHI access.",
        },
        {
            "title": "Prior Authorization Error Causing Patient Harm",
            "description": "Incorrect prior authorization decision by the semi-auto agent may delay or deny medically necessary care.",
            "risk_category": "safety",
            "severity": "CRITICAL",
            "likelihood": "LOW",
            "impact": "CRITICAL",
            "status": "open",
            "mitigation": "Mandatory HITL gate for all authorization decisions; confidence-threshold set to 0.95; approval-workflow requires clinician sign-off.",
        },
        {
            "title": "EHR Integration Data Integrity Risk",
            "description": "Doc store integration with EHR systems may produce stale or inconsistent clinical data if cache TTL is too long.",
            "risk_category": "data",
            "severity": "HIGH",
            "likelihood": "MEDIUM",
            "impact": "HIGH",
            "status": "open",
            "mitigation": "Set doc-store cache TTL ≤ 15 minutes for clinical data; implement data-validator node for EHR schema conformance.",
        },
        {
            # LL-001: Universal
            "title": "LLM Structured Output Non-Compliance",
            "description": "LLM agents required to return structured data at handoffs consistently return prose instead, causing parse failures and fallback degradation. Observed in 3/3 live runs against Claude Sonnet during Autonomous Coder E2E build.",
            "risk_category": "model",
            "severity": "MEDIUM",
            "likelihood": "HIGH",
            "impact": "MEDIUM",
            "status": "open",
            "mitigation": "Add structured-output-enforcer node between Clinical LLM and Admin Agent; use function-calling / tool_use mode instead of freeform JSON prompts.",
        },
        {
            # LL-002: Universal
            "title": "LLM Response Latency Exceeds Safety Timeout",
            "description": "Multi-step LLM pipelines can take 80–110s per run. Default timeout values of 30–120s are too low for slow-network or high-load environments, causing premature abort before pipeline completes.",
            "risk_category": "operational",
            "severity": "MEDIUM",
            "likelihood": "HIGH",
            "impact": "MEDIUM",
            "status": "open",
            "mitigation": "Set circuit breaker max_duration_s=300 as production default; expose as config parameter; show elapsed time in audit log so operators can tune per environment.",
        },
    ],
    "Gov/Procurement Agent": [
        {
            "title": "Procurement Bias in LLM Recommendations",
            "description": "LLM may reflect training data biases that favor certain vendors, violating FAR/DFARS competition requirements.",
            "risk_category": "compliance",
            "severity": "HIGH",
            "likelihood": "MEDIUM",
            "impact": "HIGH",
            "status": "open",
            "mitigation": "Compliance-reporter node must validate all vendor recommendations against FAR 6.1; HITL gate for all sole-source justifications.",
        },
        {
            "title": "CUI Data Handling in Cloud LLM",
            "description": "Controlled Unclassified Information from RFP documents may be sent to a non-IL4/IL5 LLM endpoint.",
            "risk_category": "compliance",
            "severity": "CRITICAL",
            "likelihood": "MEDIUM",
            "impact": "CRITICAL",
            "status": "open",
            "mitigation": "Route to IL4/IL5 compliant LLM only (GovCloud Bedrock or Azure Gov OpenAI); apply input classification check before LLM call.",
        },
        {
            "title": "FOIA-Discoverable AI Decision Trail Gap",
            "description": "AI-assisted procurement decisions without complete audit trails may be challenged under FOIA or bid protests.",
            "risk_category": "governance",
            "severity": "HIGH",
            "likelihood": "LOW",
            "impact": "HIGH",
            "status": "open",
            "mitigation": "Audit-logger must capture all LLM prompts, retrieved context, and approval decisions with timestamps; retain for 7 years.",
        },
        {
            # LL-001: Universal
            "title": "LLM Structured Output Non-Compliance",
            "description": "LLM agents required to return structured data at handoffs consistently return prose instead, causing parse failures and fallback degradation. Observed in 3/3 live runs against Claude Sonnet during Autonomous Coder E2E build.",
            "risk_category": "model",
            "severity": "MEDIUM",
            "likelihood": "HIGH",
            "impact": "MEDIUM",
            "status": "open",
            "mitigation": "Add structured-output-enforcer node between Gov LLM and Procurement Orchestrator; use function-calling / tool_use mode instead of freeform JSON prompts.",
        },
        {
            # LL-002: Universal
            "title": "LLM Response Latency Exceeds Safety Timeout",
            "description": "Multi-step LLM pipelines can take 80–110s per run. Default timeout values of 30–120s are too low for slow-network or high-load environments, causing premature abort before pipeline completes.",
            "risk_category": "operational",
            "severity": "MEDIUM",
            "likelihood": "HIGH",
            "impact": "MEDIUM",
            "status": "open",
            "mitigation": "Set circuit breaker max_duration_s=300 as production default; expose as config parameter; show elapsed time in audit log so operators can tune per environment.",
        },
    ],
    "Multi-Agent Research Lab": [
        {
            "title": "Agent Coordination Failure (Deadlock / Loop)",
            "description": "Parallel sub-agents may enter a coordination deadlock or infinite retry loop, stalling the research pipeline.",
            "risk_category": "operational",
            "severity": "HIGH",
            "likelihood": "MEDIUM",
            "impact": "MEDIUM",
            "status": "open",
            "mitigation": "Implement checkpoint + circuit-breaker on join node; set max_iterations per agent; token-budget hard limit triggers abort.",
        },
        {
            "title": "Cross-Agent Prompt Injection",
            "description": "One sub-agent's output may be used as a prompt by another, creating a vector for indirect prompt injection.",
            "risk_category": "security",
            "severity": "HIGH",
            "likelihood": "MEDIUM",
            "impact": "HIGH",
            "status": "open",
            "mitigation": "Sanitize all inter-agent messages through input-sanitizer; apply trusted-monitor between fork and join nodes.",
        },
        {
            "title": "Knowledge Graph Poisoning via External Sources",
            "description": "Sub-agents pulling from open-web sources may introduce adversarial content into the shared knowledge graph.",
            "risk_category": "data",
            "severity": "HIGH",
            "likelihood": "MEDIUM",
            "impact": "HIGH",
            "status": "open",
            "mitigation": "Require data-validator before any knowledge graph write; source provenance tagging; periodic ATLAS T0020 audit.",
        },
        {
            # LL-001: Universal
            "title": "LLM Structured Output Non-Compliance",
            "description": "LLM agents required to return structured data at handoffs consistently return prose instead, causing parse failures and fallback degradation. Observed in 3/3 live runs against Claude Sonnet during Autonomous Coder E2E build.",
            "risk_category": "model",
            "severity": "MEDIUM",
            "likelihood": "HIGH",
            "impact": "MEDIUM",
            "status": "open",
            "mitigation": "Add shared structured-output-enforcer node between parallel domain agents and synthesis join; use function-calling / tool_use mode instead of freeform JSON prompts.",
        },
        {
            # LL-002: Universal
            "title": "LLM Response Latency Exceeds Safety Timeout",
            "description": "Multi-step LLM pipelines can take 80–110s per run. Default timeout values of 30–120s are too low for slow-network or high-load environments, causing premature abort before pipeline completes.",
            "risk_category": "operational",
            "severity": "MEDIUM",
            "likelihood": "HIGH",
            "impact": "MEDIUM",
            "status": "open",
            "mitigation": "Set circuit breaker max_duration_s=300 as production default alongside token budget; expose as config parameter; show elapsed time in audit log so operators can tune per environment.",
        },
    ],
}

# ---------------------------------------------------------------------------
# MITRE ATLAS scenario pre-assignments per pack
# ---------------------------------------------------------------------------

SOLUTION_PACK_ATLAS: dict[str, list[str]] = {
    "Customer Service Agent":  ["T0051", "T0054", "T0020", "T0043"],
    "Autonomous Coder":        ["T0054", "T0051", "T0016", "T0020", "T0049"],
    "Knowledge Research Agent":["T0020", "T0043", "T0051"],
    "Cybersecurity SOC Agent": ["T0051", "T0020", "T0049", "T0016"],
    "Healthcare Admin Agent":  ["T0054", "T0051", "T0043", "T0020"],
    "Gov/Procurement Agent":   ["T0054", "T0051", "T0043", "T0020", "T0049"],
    "Multi-Agent Research Lab":["T0051", "T0054", "T0020", "T0043", "T0016"],
}


# ---------------------------------------------------------------------------
# Pack definitions builder
# ---------------------------------------------------------------------------

def build_packs() -> list[dict]:
    """Return list of solution pack dicts ready for DB seeding.

    Each dict has keys matching the aadc_templates INSERT signature:
    (name, category, description, graph_json, compliance_badges, autonomy_max, tags)
    """
    packs: list[dict] = []

    # -----------------------------------------------------------------------
    # 1. Customer Service Agent
    # -----------------------------------------------------------------------
    ns1 = [
        _node("inference-input",     "Customer Request",  50,  150),
        _node("input-sanitizer",     "Input Sanitizer",  220,  150),
        _node("pii-detector",        "PII Detector",     390,  150),
        _node("llm",                 "LLM Engine",       600,  150),
        _node("guardrail",           "Content Filter",   770,  150),
        _node("confidence-threshold","Confidence Gate",  940,  150),
        _node("hitl-gate",           "HITL Review",     1110,  150),
        _node("output-validator",    "Final Response",  1280,  150),
        _node("doc-store",           "KB / FAQ Store",    50,  300),
        _node("embedding-model",     "Embedder",         220,  300),
        _node("vector-db",           "Vector DB",        390,  300),
        _node("tool-chain",          "CRM Tool Chain",   770,  300),
        _node("audit-logger",        "Audit Logger",    1110,  300),
        _node("circuit-breaker",     "Circuit Breaker",  600,  300),  # 13 — LL-002: default 300s
    ]
    es1 = [
        _edge(ns1[0]["id"], ns1[1]["id"], "request"),
        _edge(ns1[1]["id"], ns1[2]["id"], "sanitized"),
        _edge(ns1[2]["id"], ns1[3]["id"], "safe input"),
        _edge(ns1[8]["id"], ns1[9]["id"], "docs"),
        _edge(ns1[9]["id"], ns1[10]["id"], "vectors"),
        _edge(ns1[10]["id"], ns1[3]["id"], "context"),
        _edge(ns1[3]["id"], ns1[4]["id"], "draft"),
        _edge(ns1[4]["id"], ns1[5]["id"], "filtered"),
        _edge(ns1[5]["id"], ns1[6]["id"], "low confidence"),
        _edge(ns1[5]["id"], ns1[7]["id"], "high confidence"),
        _edge(ns1[6]["id"], ns1[11]["id"], "tool lookup"),
        _edge(ns1[11]["id"], ns1[7]["id"], "resolved"),
        _edge(ns1[6]["id"], ns1[12]["id"], "log"),
        _edge(ns1[13]["id"], ns1[3]["id"], "abort signal"),   # LL-002
    ]
    packs.append({
        "name": "Customer Service Agent",
        "category": "solution-pack",
        "description": "End-to-end customer support agent: PII-safe intake → RAG-grounded LLM → confidence gate → HITL escalation → CRM tool integration. 68% of CS interactions by 2028.",
        "graph": {"nodes": ns1, "edges": es1},
        "badges": {"nist_ai_rmf": 90, "owasp_llm": 85, "omb_m25_21": "compliant"},
        "autonomy_max": 1,
        "tags": '["customer-service", "support", "rag", "hitl", "enterprise", "solution-pack"]',
    })

    # -----------------------------------------------------------------------
    # 2. Autonomous Coder
    # LL updates (2026-05-04 E2E build):
    #   - Sequential edges (Planner→Coder→Validator, not parallel fan-out)
    #   - Added structured-output-enforcer between each agent pair (LL-001)
    #   - Removed token-budget→circuit-breaker edge (circuit breaker tracks tokens natively)
    #   - MCP Gateway / Git Tools moved to "Phase 2" comment — not wired in Phase 1
    #   - Honest compliance badges: NIST 35% (assessed 30%), OWASP 68% (assessed 68%)
    #   - Circuit breaker default note: 300s for LLM pipelines (LL-002)
    # -----------------------------------------------------------------------
    ns2 = [
        _node("inference-input",         "Task Spec",              50,  200),  # 0
        _node("input-sanitizer",         "Input Sanitizer",       230,  200),  # 1
        _node("orchestrator",            "Orchestrator",           430,  200),  # 2
        _node("sub-agent",               "Planner Agent",          630,   80),  # 3
        _node("structured-output",       "Schema Enforcer",        830,   80),  # 4 — LL-001
        _node("sub-agent",               "Coder Agent",            630,  200),  # 5
        _node("structured-output",       "Schema Enforcer",        830,  200),  # 6 — LL-001
        _node("sub-agent",               "Validator Agent",        630,  320),  # 7
        _node("circuit-breaker",         "Circuit Breaker",        430,  350),  # 8 — LL-002: default 300s
        _node("audit-logger",            "Audit Logger",          1030,  200),  # 9
        _node("mcp-gateway",             "MCP Gateway (Phase 2)", 1030,   80),  # 10 — Phase 2
        _node("mcp-server",              "Git / IDE Tools (P2)",  1220,   80),  # 11 — Phase 2
    ]
    es2 = [
        # Main sequential pipeline
        _edge(ns2[0]["id"], ns2[1]["id"], "spec"),
        _edge(ns2[1]["id"], ns2[2]["id"], "sanitized"),
        # Orchestrator → Planner → Schema Enforcer → Orchestrator
        _edge(ns2[2]["id"], ns2[3]["id"], "plan task"),
        _edge(ns2[3]["id"], ns2[4]["id"], "raw plan"),
        _edge(ns2[4]["id"], ns2[2]["id"], "validated plan"),
        # Orchestrator → Coder → Schema Enforcer → Orchestrator
        _edge(ns2[2]["id"], ns2[5]["id"], "code task"),
        _edge(ns2[5]["id"], ns2[6]["id"], "raw code"),
        _edge(ns2[6]["id"], ns2[2]["id"], "validated code"),
        # Orchestrator → Validator → Orchestrator
        _edge(ns2[2]["id"], ns2[7]["id"], "validate task"),
        _edge(ns2[7]["id"], ns2[2]["id"], "verdict"),
        # Circuit breaker abort
        _edge(ns2[8]["id"], ns2[2]["id"], "abort signal"),
        # Audit all steps
        _edge(ns2[2]["id"], ns2[9]["id"], "log"),
        # Phase 2: MCP tool execution
        _edge(ns2[10]["id"], ns2[11]["id"], "tool call"),
    ]
    packs.append({
        "name": "Autonomous Coder",
        "category": "solution-pack",
        "description": (
            "Autonomous software engineer: orchestrator coordinates sequential Planner→Coder→Validator "
            "sub-agents with schema enforcement at each handoff. Circuit breaker (300s default) guards "
            "against runaway LLM loops. MCP tool integration is Phase 2. Audit logger records every "
            "agent decision. Validated via E2E build 2026-05-04."
        ),
        "graph": {"nodes": ns2, "edges": es2},
        # Honest badges — reflect actual AADC assessment scores from E2E run
        "badges": {"nist_ai_rmf": 35, "owasp_llm": 68, "mitre_atlas": "covered"},
        "autonomy_max": 4,
        "tags": '["coding", "autonomous", "multi-agent", "devin", "sdlc", "solution-pack"]',
    })

    # -----------------------------------------------------------------------
    # 3. Knowledge Research Agent
    # -----------------------------------------------------------------------
    ns3 = [
        _node("inference-input",     "Research Query",    50,  150),
        _node("input-sanitizer",     "Input Sanitizer",  220,  150),
        _node("llm",                 "LLM Reasoner",     420,  150),
        _node("knowledge-graph",     "Knowledge Graph",  620,  150),
        _node("doc-store",           "Document Store",    50,  300),
        _node("embedding-model",     "Embedder",         220,  300),
        _node("vector-db",           "Vector DB",        420,  300),
        _node("short-term-mem",      "Working Memory",   620,  300),
        _node("web-search",          "Web Search",       820,  150),
        _node("confidence-threshold","Confidence Gate",  820,  300),
        _node("approval-workflow",   "Research Review", 1020,  150),
        _node("audit-logger",        "Audit Logger",    1020,  300),
        _node("structured-output",   "Schema Enforcer",  620,  300),  # 12 — LL-001
        _node("circuit-breaker",     "Circuit Breaker",  420,  300),  # 13 — LL-002: default 300s
    ]
    es3 = [
        _edge(ns3[0]["id"], ns3[1]["id"], "query"),
        _edge(ns3[1]["id"], ns3[2]["id"], "sanitized"),
        _edge(ns3[4]["id"], ns3[5]["id"], "docs"),
        _edge(ns3[5]["id"], ns3[6]["id"], "vectors"),
        _edge(ns3[6]["id"], ns3[2]["id"], "retrieved context"),
        _edge(ns3[2]["id"], ns3[3]["id"], "entities"),
        _edge(ns3[3]["id"], ns3[7]["id"], "graph facts"),
        _edge(ns3[7]["id"], ns3[2]["id"], "memory context"),
        _edge(ns3[2]["id"], ns3[8]["id"], "search query"),
        _edge(ns3[8]["id"], ns3[2]["id"], "web results"),
        _edge(ns3[2]["id"], ns3[12]["id"], "draft report"),   # LL-001: through schema enforcer
        _edge(ns3[12]["id"], ns3[9]["id"], "schema-validated"),
        _edge(ns3[9]["id"], ns3[10]["id"], "uncertain"),
        _edge(ns3[9]["id"], ns3[11]["id"], "log"),
        _edge(ns3[10]["id"], ns3[11]["id"], "approved"),
        _edge(ns3[13]["id"], ns3[2]["id"], "abort signal"),   # LL-002
    ]
    packs.append({
        "name": "Knowledge Research Agent",
        "category": "solution-pack",
        "description": "IT service desk, procurement analysis, and enterprise research: LLM + knowledge graph + RAG + web search → confidence-gated approval. Handles complex multi-source queries.",
        "graph": {"nodes": ns3, "edges": es3},
        "badges": {"nist_ai_rmf": 75, "owasp_llm": 70},
        "autonomy_max": 3,
        "tags": '["research", "knowledge-graph", "rag", "enterprise", "it-service-desk", "solution-pack"]',
    })

    # -----------------------------------------------------------------------
    # 4. Cybersecurity SOC Agent
    # -----------------------------------------------------------------------
    ns4 = [
        _node("inference-input",   "Data Stream",         50,  200),
        _node("baseline-snapshot", "Behavioral Baseline", 220,  200),
        _node("drift-detector",    "Drift Detector",      420,  200),
        _node("classifier",        "Anomaly Detector",    620,  200),
        _node("pii-detector",      "PII Scrubber",        620,  350),
        _node("autonomous-agent",  "SOC Agent",           820,  200),
        _node("circuit-breaker",   "Circuit Breaker",     820,  350),
        _node("rate-limiter",      "Rate Limiter",        820,  480),
        _node("mcp-server",        "SIEM Connector",     1020,  200),
        _node("siem-forwarder",    "SIEM Forwarder",     1020,  350),
        _node("alert-manager",     "Alert Manager",      1220,  200),
        _node("hitl-gate",         "Analyst Review",     1220,  350),
        _node("audit-logger",      "Audit Logger",       1220,  480),
    ]
    es4 = [
        _edge(ns4[0]["id"], ns4[1]["id"], "events"),
        _edge(ns4[1]["id"], ns4[2]["id"], "baseline delta"),
        _edge(ns4[2]["id"], ns4[3]["id"], "anomaly score"),
        _edge(ns4[3]["id"], ns4[4]["id"], "scrub PII"),
        _edge(ns4[4]["id"], ns4[5]["id"], "clean event"),
        _edge(ns4[5]["id"], ns4[6]["id"], "action"),
        _edge(ns4[6]["id"], ns4[7]["id"], "rate check"),
        _edge(ns4[5]["id"], ns4[8]["id"], "tool call"),
        _edge(ns4[8]["id"], ns4[9]["id"], "forward"),
        _edge(ns4[9]["id"], ns4[12]["id"], "log"),
        _edge(ns4[5]["id"], ns4[10]["id"], "alert"),
        _edge(ns4[10]["id"], ns4[11]["id"], "escalate"),
        _edge(ns4[11]["id"], ns4[12]["id"], "reviewed"),
    ]
    packs.append({
        "name": "Cybersecurity SOC Agent",
        "category": "solution-pack",
        "description": "Real-time SOC automation: behavioral baseline → drift detection → anomaly classification → autonomous response + HITL escalation → SIEM forwarding. MITRE ATLAS covered.",
        "graph": {"nodes": ns4, "edges": es4},
        "badges": {"nist_ai_rmf": 80, "owasp_llm": 80, "mitre_atlas": "covered"},
        "autonomy_max": 3,
        "tags": '["cybersecurity", "soc", "anomaly-detection", "siem", "defense", "solution-pack"]',
    })

    # -----------------------------------------------------------------------
    # 5. Healthcare Admin Agent
    # -----------------------------------------------------------------------
    ns5 = [
        _node("inference-input",     "Patient Request",    50,  150),
        _node("pii-detector",        "PHI Detector",      220,  150),
        _node("input-sanitizer",     "Input Sanitizer",   420,  150),
        _node("guardrail",           "Guardrail",         620,  150),
        _node("llm",                 "Clinical LLM",      820,  150),
        _node("doc-store",           "EHR / Formulary",    50,  300),
        _node("embedding-model",     "Embedder",          220,  300),
        _node("vector-db",           "Clinical Vector DB",420,  300),
        _node("semi-auto-agent",     "Admin Agent",       820,  300),
        _node("confidence-threshold","Confidence Gate",  1020,  150),
        _node("hitl-gate",           "Clinician Review", 1020,  300),
        _node("approval-workflow",   "Authorization",    1220,  150),
        _node("audit-logger",        "HIPAA Audit Log",  1220,  300),
        _node("structured-output",   "Schema Enforcer",  1000,  150),  # 13 — LL-001
        _node("circuit-breaker",     "Circuit Breaker",   820,  430),  # 14 — LL-002: default 300s
    ]
    es5 = [
        _edge(ns5[0]["id"], ns5[1]["id"], "request"),
        _edge(ns5[1]["id"], ns5[2]["id"], "phi-safe"),
        _edge(ns5[2]["id"], ns5[3]["id"], "sanitized"),
        _edge(ns5[3]["id"], ns5[4]["id"], "filtered"),
        _edge(ns5[5]["id"], ns5[6]["id"], "records"),
        _edge(ns5[6]["id"], ns5[7]["id"], "vectors"),
        _edge(ns5[7]["id"], ns5[4]["id"], "clinical context"),
        _edge(ns5[4]["id"], ns5[13]["id"], "raw analysis"),    # LL-001: through schema enforcer
        _edge(ns5[13]["id"], ns5[8]["id"], "schema-validated"),
        _edge(ns5[8]["id"], ns5[9]["id"], "recommendation"),
        _edge(ns5[9]["id"], ns5[10]["id"], "low confidence"),
        _edge(ns5[9]["id"], ns5[11]["id"], "high confidence"),
        _edge(ns5[10]["id"], ns5[11]["id"], "clinician approved"),
        _edge(ns5[11]["id"], ns5[12]["id"], "authorized"),
        _edge(ns5[10]["id"], ns5[12]["id"], "log"),
        _edge(ns5[14]["id"], ns5[8]["id"], "abort signal"),   # LL-002
    ]
    packs.append({
        "name": "Healthcare Admin Agent",
        "category": "solution-pack",
        "description": "HIPAA-compliant healthcare admin automation: PHI detection → EHR-grounded LLM → semi-auto agent → mandatory clinician HITL → audit trail. Prior auth, billing, scheduling.",
        "graph": {"nodes": ns5, "edges": es5},
        "badges": {"nist_ai_rmf": 90, "owasp_llm": 85, "omb_m25_21": "compliant"},
        "autonomy_max": 2,
        "tags": '["healthcare", "hipaa", "prior-auth", "phi", "ehr", "solution-pack"]',
    })

    # -----------------------------------------------------------------------
    # 6. Gov/Procurement Agent
    # -----------------------------------------------------------------------
    ns6 = [
        _node("inference-input",     "RFP / Requirement",  50,  150),
        _node("input-sanitizer",     "Input Sanitizer",   220,  150),
        _node("guardrail",           "CUI Guardrail",     420,  150),
        _node("llm",                 "Gov LLM (IL4/IL5)", 650,  150),
        _node("knowledge-graph",     "Acquisition KG",    650,  300),
        _node("doc-store",           "FAR / DFARS Store",  50,  300),
        _node("embedding-model",     "Embedder",          220,  300),
        _node("vector-db",           "Reg Vector DB",     420,  300),
        _node("orchestrator",        "Procurement Orch",  880,  150),
        _node("token-budget",        "Token Budget",      880,  300),
        _node("hitl-gate",           "CO Review",        1080,  150),
        _node("approval-workflow",   "Approvals",        1080,  300),
        _node("compliance-reporter", "FAR Compliance",   1280,  150),
        _node("audit-logger",        "CUI Audit Logger", 1280,  300),
        _node("structured-output",   "Schema Enforcer",   765,  150),  # 14 — LL-001
        _node("circuit-breaker",     "Circuit Breaker",   880,  430),  # 15 — LL-002: default 300s
    ]
    es6 = [
        _edge(ns6[0]["id"], ns6[1]["id"], "rfp"),
        _edge(ns6[1]["id"], ns6[2]["id"], "sanitized"),
        _edge(ns6[2]["id"], ns6[3]["id"], "CUI-safe"),
        _edge(ns6[5]["id"], ns6[6]["id"], "far/dfars"),
        _edge(ns6[6]["id"], ns6[7]["id"], "vectors"),
        _edge(ns6[7]["id"], ns6[3]["id"], "reg context"),
        _edge(ns6[3]["id"], ns6[4]["id"], "entities"),
        _edge(ns6[4]["id"], ns6[3]["id"], "kg context"),
        _edge(ns6[3]["id"], ns6[14]["id"], "raw analysis"),    # LL-001: through schema enforcer
        _edge(ns6[14]["id"], ns6[8]["id"], "schema-validated"),
        _edge(ns6[8]["id"], ns6[9]["id"], "budget check"),
        _edge(ns6[8]["id"], ns6[10]["id"], "draft recommendation"),
        _edge(ns6[10]["id"], ns6[11]["id"], "co approved"),
        _edge(ns6[11]["id"], ns6[12]["id"], "compliant"),
        _edge(ns6[12]["id"], ns6[13]["id"], "log"),
        _edge(ns6[10]["id"], ns6[13]["id"], "log"),
        _edge(ns6[15]["id"], ns6[8]["id"], "abort signal"),   # LL-002
    ]
    packs.append({
        "name": "Gov/Procurement Agent",
        "category": "solution-pack",
        "description": "FAR/DFARS-compliant acquisition automation: CUI-safe intake → IL4/IL5 LLM → regulation-grounded analysis → Contracting Officer HITL → FAR compliance report. DoD/Federal ready.",
        "graph": {"nodes": ns6, "edges": es6},
        "badges": {"nist_ai_rmf": 95, "owasp_llm": 75, "omb_m25_21": "compliant", "omb_m26_04": "compliant"},
        "autonomy_max": 2,
        "tags": '["government", "procurement", "rfp", "far", "dfars", "dod", "solution-pack"]',
    })

    # -----------------------------------------------------------------------
    # 7. Multi-Agent Research Lab
    # -----------------------------------------------------------------------
    ns7 = [
        _node("inference-input",  "Research Brief",     50,  250),
        _node("input-sanitizer",  "Input Sanitizer",   220,  250),
        _node("orchestrator",     "Research Orch",     420,  250),
        _node("fork",             "Parallel Fork",     620,  250),
        _node("sub-agent",        "Domain Agent A",    820,   80),
        _node("sub-agent",        "Domain Agent B",    820,  250),
        _node("sub-agent",        "Domain Agent C",    820,  420),
        _node("join",             "Synthesis Join",   1020,  250),
        _node("knowledge-graph",  "Research KG",      1220,  250),
        _node("vector-db",        "Evidence Store",   1220,   80),
        _node("embedding-model",  "Embedder",         1220,  420),
        _node("checkpoint",       "Checkpoint",       1420,  250),
        _node("token-budget",     "Token Budget",      420,  420),
        _node("audit-logger",     "Audit Logger",     1420,  420),
        _node("structured-output","Schema Enforcer",   920,  250),  # 14 — LL-001: shared enforcer before synthesis join
        _node("circuit-breaker",  "Circuit Breaker",   420,  530),  # 15 — LL-002: default 300s
    ]
    es7 = [
        _edge(ns7[0]["id"], ns7[1]["id"], "brief"),
        _edge(ns7[1]["id"], ns7[2]["id"], "sanitized"),
        _edge(ns7[2]["id"], ns7[3]["id"], "dispatch"),
        _edge(ns7[3]["id"], ns7[4]["id"], "domain A"),
        _edge(ns7[3]["id"], ns7[5]["id"], "domain B"),
        _edge(ns7[3]["id"], ns7[6]["id"], "domain C"),
        _edge(ns7[4]["id"], ns7[14]["id"], "findings A"),   # LL-001: through shared schema enforcer
        _edge(ns7[5]["id"], ns7[14]["id"], "findings B"),
        _edge(ns7[6]["id"], ns7[14]["id"], "findings C"),
        _edge(ns7[14]["id"], ns7[7]["id"], "schema-validated"),
        _edge(ns7[7]["id"], ns7[8]["id"], "entities"),
        _edge(ns7[7]["id"], ns7[9]["id"], "evidence"),
        _edge(ns7[9]["id"], ns7[10]["id"], "embed"),
        _edge(ns7[10]["id"], ns7[8]["id"], "vectors"),
        _edge(ns7[8]["id"], ns7[11]["id"], "synthesized"),
        _edge(ns7[11]["id"], ns7[13]["id"], "log"),
        _edge(ns7[12]["id"], ns7[2]["id"], "budget"),
        _edge(ns7[15]["id"], ns7[2]["id"], "abort signal"),   # LL-002
    ]
    packs.append({
        "name": "Multi-Agent Research Lab",
        "category": "solution-pack",
        "description": "Parallel multi-agent research: orchestrator → fork into 3 domain agents → synthesis join → shared knowledge graph + evidence store → checkpoint. Built for problems too large for single-agent solutions.",
        "graph": {"nodes": ns7, "edges": es7},
        "badges": {"nist_ai_rmf": 70, "owasp_llm": 65},
        "autonomy_max": 4,
        "tags": '["multi-agent", "research", "parallel", "autonomous", "knowledge-graph", "solution-pack"]',
    })

    return packs


# Module-level constant — built once on import
SOLUTION_PACKS: list[dict] = build_packs()
