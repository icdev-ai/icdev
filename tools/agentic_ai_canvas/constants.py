# CUI // SP-CTI
"""Agentic AI Design Canvas — Constants, node palette, compliance rule maps."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Node type sets — used by agentic_engine.py for rule evaluation
# ---------------------------------------------------------------------------

MODEL_NODES = {"llm", "llm-local", "embedding-model", "fine-tuned-adapter",
               "classifier", "reranker", "multimodal"}

MEMORY_NODES = {"vector-db", "doc-store", "short-term-mem", "long-term-mem",
                "episodic-buffer", "knowledge-graph", "embedding-cache",
                # Phase 4 — LangGraph/LlamaIndex-inspired memory enrichment
                "working-memory", "semantic-cache", "conversation-history"}

AGENT_NODES = {"autonomous-agent", "semi-auto-agent", "orchestrator",
               "sub-agent", "researcher-agent", "writer-agent",
               "analyst-agent", "reviewer-agent",
               # Phase 5 — Swarm/parallel patterns (LangGraph fan-out)
               "swarm-agent", "fan-out-coordinator", "fan-in-aggregator"}

TOOL_MCP_NODES = {"mcp-server", "mcp-gateway", "tool-chain",
                  "function-caller", "output-validator", "external-api",
                  "code-executor", "web-search",
                  # Phase 4 — A2A bridge + sandboxed execution
                  "a2a-bridge", "sandbox-exec",
                  # Phase 5 — Agent isolation / trust zones
                  "agent-isolation-boundary"}

DATA_NODES = {"training-data", "inference-input", "feedback-collector",
              "rlhf-pipeline", "data-lake", "chunker", "data-validator"}

SAFETY_NODES = {"guardrail", "pii-detector", "toxicity-filter",
                "confidence-threshold", "circuit-breaker", "rate-limiter",
                "input-sanitizer", "redaction-engine",
                # Phase 4 — trusted monitor + field-level PII
                "trusted-monitor", "pii-field-detector"}

GOVERNANCE_NODES = {"hitl-gate", "audit-logger", "approval-workflow",
                    "caio-override", "compliance-reporter", "alert-manager",
                    "prompt-registry"}

INFRA_NODES = {"gpu-cluster", "model-registry", "token-budget",
               "vector-index", "siem-forwarder", "baseline-snapshot",
               "drift-detector"}

# Phase 4 — execution control (checkpoint/fork/join from LangGraph pattern)
EXECUTION_NODES = {"checkpoint", "fork", "join", "parallel-group"}

# Phase 4 — observability (Haystack ProxyTracer / OpenTelemetry pattern)
OBSERVABILITY_NODES = {"trace-collector", "span-recorder", "metrics-emitter"}

ALL_NODE_TYPES = (MODEL_NODES | MEMORY_NODES | AGENT_NODES | TOOL_MCP_NODES
                  | DATA_NODES | SAFETY_NODES | GOVERNANCE_NODES | INFRA_NODES
                  | EXECUTION_NODES | OBSERVABILITY_NODES)

# Nodes considered "output-producing" for HITL path tracing
OUTPUT_NODES = {"output-validator", "compliance-reporter", "external-api",
                "siem-forwarder", "approval-workflow"}

# ---------------------------------------------------------------------------
# Autonomy level thresholds
# ---------------------------------------------------------------------------

AUTONOMY_LEVELS = {
    0: "Human-Operated",
    1: "Human-Delegated",
    2: "Human-Supervised",
    3: "Human-Initiated",
    4: "Fully Autonomous",
    5: "Unconstrained (CRITICAL)",
}

# ---------------------------------------------------------------------------
# NIST AI RMF — function / category / check map
# ---------------------------------------------------------------------------

NIST_AI_RMF_CHECKS: list[dict] = [
    # GOVERN
    {"id": "gov-1", "function": "GOVERN", "category": "GOV-1",
     "title": "Oversight plan present",
     "description": "Design must include an approval-workflow or hitl-gate node.",
     "required_any": ["approval-workflow", "hitl-gate"],
     "weight": 15},
    {"id": "gov-2", "function": "GOVERN", "category": "GOV-2",
     "title": "AI use case classified",
     "description": "Design metadata must specify impact classification.",
     "meta_field": "classification",
     "weight": 10},
    # MAP
    {"id": "map-1", "function": "MAP", "category": "MAP-1",
     "title": "System boundary defined",
     "description": "Design must have at least one inference-input and one output node.",
     "required_any": ["inference-input"],
     "required_output": True,
     "weight": 10},
    {"id": "map-2", "function": "MAP", "category": "MAP-2",
     "title": "Risk documented via assessment",
     "description": "At least one assessment run must exist for this design.",
     "check": "has_prior_assessment",
     "weight": 10},
    # MEASURE
    {"id": "mea-1", "function": "MEASURE", "category": "MEA-1",
     "title": "Hallucination bounded",
     "description": "A confidence-threshold node must be present.",
     "required_any": ["confidence-threshold"],
     "weight": 15},
    {"id": "mea-2", "function": "MEASURE", "category": "MEA-2",
     "title": "Drift monitoring enabled",
     "description": "A drift-detector or baseline-snapshot node must be present.",
     "required_any": ["drift-detector", "baseline-snapshot"],
     "weight": 10},
    # MANAGE
    {"id": "mng-1", "function": "MANAGE", "category": "MNG-1",
     "title": "Incident response path",
     "description": "An alert-manager connected to a hitl-gate must exist.",
     "required_any": ["alert-manager"],
     "weight": 15},
    {"id": "mng-2", "function": "MANAGE", "category": "MNG-2",
     "title": "Circuit breaker in agent chain",
     "description": "Any autonomous agent must have a circuit-breaker downstream.",
     "check": "agent_has_circuit_breaker",
     "weight": 15},
    # NIST AI RMF 2.0 — GOVERN-1.7: AI use case inventory registration
    {"id": "gov-3", "function": "GOVERN", "category": "GOV-1.7",
     "title": "AI use case registered in inventory",
     "description": "NIST AI RMF 2.0 GOVERN-1.7 — Design must include a system-card or AI-BOM node, "
                    "or declare a use_case_id in metadata, documenting the AI use case for inventory.",
     "check": "ai_use_case_registered",
     "weight": 10},
]

# ---------------------------------------------------------------------------
# OWASP LLM Top 10 checks
# ---------------------------------------------------------------------------

OWASP_LLM_CHECKS: list[dict] = [
    {"id": "llm01", "title": "Prompt Injection",
     "description": "An input-sanitizer must be upstream of every LLM node.",
     "check": "llm_has_input_sanitizer", "weight": 12},
    {"id": "llm02", "title": "Insecure Output Handling",
     "description": "An output-validator must be downstream of every LLM node.",
     "check": "llm_has_output_validator", "weight": 12},
    {"id": "llm03", "title": "Training Data Poisoning",
     "description": "Training-data nodes must link to an audit-logger.",
     "check": "training_data_audited", "weight": 8},
    {"id": "llm04", "title": "Model Denial of Service",
     "description": "A token-budget or rate-limiter must be present.",
     "required_any": ["token-budget", "rate-limiter"], "weight": 8},
    {"id": "llm05", "title": "Supply Chain Vulnerabilities",
     "description": "Each LLM node should link to a model-registry.",
     "check": "llm_has_model_registry", "weight": 8},
    {"id": "llm06", "title": "Sensitive Information Disclosure",
     "description": "PII detector and redaction engine must both be present.",
     "required_all": ["pii-detector", "redaction-engine"], "weight": 12},
    {"id": "llm07", "title": "Insecure Plugin Design",
     "description": "Every MCP server must be downstream of an mcp-gateway.",
     "check": "mcp_server_has_gateway", "weight": 10},
    {"id": "llm08", "title": "Excessive Agency",
     "description": "Autonomous agents must have a circuit-breaker.",
     "check": "agent_has_circuit_breaker", "weight": 12},
    {"id": "llm09", "title": "Overreliance",
     "description": "A confidence-threshold and hitl-gate must exist for low-confidence paths.",
     "required_any": ["confidence-threshold"], "weight": 8},
    {"id": "llm10", "title": "Model Theft",
     "description": "Inference calls must be logged (audit-logger present).",
     "required_any": ["audit-logger"], "weight": 10},
]

# ---------------------------------------------------------------------------
# MITRE ATLAS — node type → adversarial technique mapping
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Phase 4 — NIST AI RMF observability checks
# ---------------------------------------------------------------------------

P4_OBSERVABILITY_CHECKS: list[dict] = [
    {"id": "obs-1", "function": "MEASURE", "category": "MEA-3",
     "title": "Execution trace coverage",
     "description": "At least one trace-collector or span-recorder node must be present for observability.",
     "required_any": ["trace-collector", "span-recorder"],
     "weight": 10},
    {"id": "obs-2", "function": "MEASURE", "category": "MEA-4",
     "title": "Metrics emission enabled",
     "description": "A metrics-emitter node must be present to surface runtime telemetry.",
     "required_any": ["metrics-emitter"],
     "weight": 5},
]

# Phase 4 — safety extension checks
P4_SAFETY_EXT_CHECKS: list[dict] = [
    {"id": "p4-trusted-monitor", "function": "MANAGE", "category": "MNG-3",
     "title": "Trusted monitor present for autonomous agents",
     "description": "If any autonomous-agent is present, a trusted-monitor node must exist downstream.",
     "check": "autonomous_agent_has_trusted_monitor",
     "weight": 12},
    {"id": "p4-pii-field", "function": "MANAGE", "category": "MNG-4",
     "title": "Field-level PII detection enabled",
     "description": "Designs with a pii-field-detector must wire it upstream of redaction-engine.",
     "check": "pii_field_wired_to_redaction",
     "weight": 8},
]

# Phase 4 — A2A / sandbox checks
P4_A2A_CHECKS: list[dict] = [
    {"id": "p4-a2a-audit", "function": "GOVERN", "category": "GOV-3",
     "title": "A2A bridge audited",
     "description": "Every a2a-bridge node must have an audit-logger downstream.",
     "check": "a2a_bridge_has_audit_logger",
     "weight": 10},
    {"id": "p4-sandbox-guard", "function": "GOVERN", "category": "GOV-4",
     "title": "Sandbox execution guarded",
     "description": "Every sandbox-exec node must have a guardrail or input-sanitizer upstream.",
     "check": "sandbox_exec_has_guardrail",
     "weight": 10},
]

# Phase 4 — checkpoint coverage check
P4_EXECUTION_CHECKS: list[dict] = [
    {"id": "p4-checkpoint", "function": "MANAGE", "category": "MNG-5",
     "title": "Execution checkpoints present",
     "description": "Multi-agent designs benefit from at least one checkpoint node for fault recovery.",
     "check": "has_checkpoint_node",
     "weight": 5},
]

# ---------------------------------------------------------------------------
# MITRE ATLAS — node type → adversarial technique mapping
# ---------------------------------------------------------------------------

ATLAS_THREAT_MAP: dict[str, list[str]] = {
    "llm":              ["AML.T0051", "AML.T0048", "AML.T0040"],  # Prompt inject, exfil, model inversion
    "llm-local":        ["AML.T0048", "AML.T0012"],               # Model exfil, backdoor
    "training-data":    ["AML.T0020", "AML.T0019"],               # Data poisoning, corruption
    "vector-db":        ["AML.T0048", "AML.T0020"],               # RAG poisoning, exfil
    "mcp-server":       ["AML.T0051", "AML.T0043"],               # Plugin exploit, resource exhaustion
    "external-api":     ["AML.T0043", "AML.T0044"],               # Resource exhaustion, supply chain
    "fine-tuned-adapter": ["AML.T0012", "AML.T0019"],            # Backdoor, data corruption
    "autonomous-agent": ["AML.T0051", "AML.T0052"],               # Prompt inject, agent takeover
    "rlhf-pipeline":    ["AML.T0020"],                            # Preference poisoning
    "embedding-model":  ["AML.T0048"],                            # Embedding inversion
}

# ---------------------------------------------------------------------------
# OMB M-25-21 — domain → impact classification
# ---------------------------------------------------------------------------

RIGHTS_IMPACTING_DOMAINS = {
    "benefits", "credit", "employment", "housing",
    "education", "criminal-justice", "immigration",
}

SAFETY_IMPACTING_DOMAINS = {
    "critical-infrastructure", "safety-systems",
    "medical", "autonomous-vehicle", "defense",
}

# ---------------------------------------------------------------------------
# Visual palette — drag-and-drop categories and node definitions
# ---------------------------------------------------------------------------

AADC_OBJECTS: dict = {
    "categories": {
        "models": [
            {"id": "llm",               "label": "LLM",              "type": "llm",               "icon": "🧠", "color": "#6366f1"},
            {"id": "llm-local",         "label": "Local LLM",        "type": "llm-local",         "icon": "🧠", "color": "#8b5cf6"},
            {"id": "embedding-model",   "label": "Embedding Model",  "type": "embedding-model",   "icon": "🔢", "color": "#6366f1"},
            {"id": "fine-tuned-adapter","label": "Fine-Tuned",       "type": "fine-tuned-adapter","icon": "🎯", "color": "#7c3aed"},
            {"id": "classifier",        "label": "Classifier",       "type": "classifier",        "icon": "🏷️", "color": "#5b21b6"},
            {"id": "reranker",          "label": "Re-Ranker",        "type": "reranker",          "icon": "📊", "color": "#6366f1"},
            {"id": "multimodal",        "label": "Multimodal",       "type": "multimodal",        "icon": "🎨", "color": "#7c3aed"},
        ],
        "memory": [
            {"id": "vector-db",         "label": "Vector DB",        "type": "vector-db",         "icon": "🗄️", "color": "#0ea5e9"},
            {"id": "doc-store",         "label": "Document Store",   "type": "doc-store",         "icon": "📁", "color": "#0284c7"},
            {"id": "short-term-mem",    "label": "Short-Term Mem",   "type": "short-term-mem",    "icon": "💭", "color": "#0ea5e9"},
            {"id": "long-term-mem",     "label": "Long-Term Mem",    "type": "long-term-mem",     "icon": "🧩", "color": "#0284c7"},
            {"id": "episodic-buffer",   "label": "Episodic Buffer",  "type": "episodic-buffer",   "icon": "📼", "color": "#0369a1"},
            {"id": "knowledge-graph",   "label": "Knowledge Graph",  "type": "knowledge-graph",   "icon": "🕸️", "color": "#0ea5e9"},
            {"id": "embedding-cache",   "label": "Embed Cache",      "type": "embedding-cache",   "icon": "⚡", "color": "#0284c7"},
        ],
        "agents": [
            {"id": "autonomous-agent",  "label": "Autonomous Agent", "type": "autonomous-agent",  "icon": "🤖", "color": "#f59e0b"},
            {"id": "semi-auto-agent",   "label": "Semi-Auto Agent",  "type": "semi-auto-agent",   "icon": "🤝", "color": "#d97706"},
            {"id": "orchestrator",      "label": "Orchestrator",     "type": "orchestrator",      "icon": "🎭", "color": "#b45309"},
            {"id": "sub-agent",         "label": "Sub-Agent",        "type": "sub-agent",         "icon": "🔧", "color": "#f59e0b"},
            {"id": "researcher-agent",  "label": "Research Agent",   "type": "researcher-agent",  "icon": "🔍", "color": "#d97706"},
            {"id": "writer-agent",      "label": "Writer Agent",     "type": "writer-agent",      "icon": "✍️", "color": "#f59e0b"},
            {"id": "analyst-agent",     "label": "Analyst Agent",    "type": "analyst-agent",     "icon": "📈", "color": "#b45309"},
            {"id": "reviewer-agent",    "label": "Reviewer Agent",   "type": "reviewer-agent",    "icon": "👁️", "color": "#d97706"},
        ],
        "tools_mcp": [
            {"id": "mcp-server",        "label": "MCP Server",       "type": "mcp-server",        "icon": "🔌", "color": "#10b981"},
            {"id": "mcp-gateway",       "label": "MCP Gateway",      "type": "mcp-gateway",       "icon": "🛡️", "color": "#059669"},
            {"id": "tool-chain",        "label": "Tool Chain",       "type": "tool-chain",        "icon": "⛓️", "color": "#10b981"},
            {"id": "function-caller",   "label": "Function Caller",  "type": "function-caller",   "icon": "📞", "color": "#047857"},
            {"id": "output-validator",  "label": "Output Validator", "type": "output-validator",  "icon": "✅", "color": "#10b981"},
            {"id": "external-api",      "label": "External API",     "type": "external-api",      "icon": "🌐", "color": "#059669"},
            {"id": "code-executor",     "label": "Code Executor",    "type": "code-executor",     "icon": "⚙️", "color": "#047857"},
            {"id": "web-search",        "label": "Web Search",       "type": "web-search",        "icon": "🔎", "color": "#10b981"},
        ],
        "data": [
            {"id": "training-data",     "label": "Training Data",    "type": "training-data",     "icon": "📦", "color": "#64748b"},
            {"id": "inference-input",   "label": "Inference Input",  "type": "inference-input",   "icon": "➡️", "color": "#475569"},
            {"id": "feedback-collector","label": "Feedback",         "type": "feedback-collector","icon": "💬", "color": "#64748b"},
            {"id": "rlhf-pipeline",     "label": "RLHF Pipeline",    "type": "rlhf-pipeline",     "icon": "🔄", "color": "#475569"},
            {"id": "data-lake",         "label": "Data Lake",        "type": "data-lake",         "icon": "🌊", "color": "#334155"},
            {"id": "chunker",           "label": "Chunker",          "type": "chunker",           "icon": "✂️", "color": "#64748b"},
            {"id": "data-validator",    "label": "Data Validator",   "type": "data-validator",    "icon": "🔍", "color": "#475569"},
        ],
        "safety": [
            {"id": "guardrail",         "label": "Guardrail",        "type": "guardrail",         "icon": "🚧", "color": "#ef4444"},
            {"id": "pii-detector",      "label": "PII Detector",     "type": "pii-detector",      "icon": "🔒", "color": "#dc2626"},
            {"id": "toxicity-filter",   "label": "Toxicity Filter",  "type": "toxicity-filter",   "icon": "🚫", "color": "#b91c1c"},
            {"id": "confidence-threshold","label": "Confidence Gate","type": "confidence-threshold","icon": "📉","color": "#ef4444"},
            {"id": "circuit-breaker",   "label": "Circuit Breaker",  "type": "circuit-breaker",   "icon": "⚡", "color": "#dc2626"},
            {"id": "rate-limiter",      "label": "Rate Limiter",     "type": "rate-limiter",      "icon": "⏱️", "color": "#b91c1c"},
            {"id": "input-sanitizer",   "label": "Input Sanitizer",  "type": "input-sanitizer",   "icon": "🧹", "color": "#ef4444"},
            {"id": "redaction-engine",  "label": "Redaction",        "type": "redaction-engine",  "icon": "📵", "color": "#dc2626"},
        ],
        "governance": [
            {"id": "hitl-gate",         "label": "HITL Gate",        "type": "hitl-gate",         "icon": "👤", "color": "#8b5cf6"},
            {"id": "audit-logger",      "label": "Audit Logger",     "type": "audit-logger",      "icon": "📋", "color": "#7c3aed"},
            {"id": "approval-workflow", "label": "Approval Flow",    "type": "approval-workflow", "icon": "✔️", "color": "#6d28d9"},
            {"id": "caio-override",     "label": "CAIO Override",    "type": "caio-override",     "icon": "🏛️", "color": "#8b5cf6"},
            {"id": "compliance-reporter","label": "Compliance Rpt",  "type": "compliance-reporter","icon": "📊","color": "#7c3aed"},
            {"id": "alert-manager",     "label": "Alert Manager",    "type": "alert-manager",     "icon": "🔔", "color": "#6d28d9"},
            {"id": "prompt-registry",   "label": "Prompt Registry",  "type": "prompt-registry",   "icon": "📝", "color": "#8b5cf6"},
        ],
        "infrastructure": [
            {"id": "gpu-cluster",       "label": "GPU Cluster",      "type": "gpu-cluster",       "icon": "💻", "color": "#6b7280"},
            {"id": "model-registry",    "label": "Model Registry",   "type": "model-registry",    "icon": "🗂️", "color": "#4b5563"},
            {"id": "token-budget",      "label": "Token Budget",     "type": "token-budget",      "icon": "💰", "color": "#374151"},
            {"id": "vector-index",      "label": "Vector Index",     "type": "vector-index",      "icon": "📐", "color": "#6b7280"},
            {"id": "siem-forwarder",    "label": "SIEM Forwarder",   "type": "siem-forwarder",    "icon": "📡", "color": "#4b5563"},
            {"id": "baseline-snapshot", "label": "Baseline Snapshot","type": "baseline-snapshot", "icon": "📸", "color": "#374151"},
            {"id": "drift-detector",    "label": "Drift Detector",   "type": "drift-detector",    "icon": "📡", "color": "#6b7280"},
        ],
        # ── Phase 4: Execution Control (LangGraph checkpoint/fork pattern) ──
        "execution": [
            {"id": "checkpoint",        "label": "Checkpoint",       "type": "checkpoint",        "icon": "💾", "color": "#a855f7"},
            {"id": "fork",              "label": "Fork",             "type": "fork",              "icon": "⑂",  "color": "#c084fc"},
            {"id": "join",              "label": "Join",             "type": "join",              "icon": "⊕",  "color": "#a855f7"},
            {"id": "parallel-group",    "label": "Parallel Group",   "type": "parallel-group",    "icon": "⊞",  "color": "#7e22ce"},
        ],
        # ── Phase 4: Observability (Haystack/OTel trace pattern) ──
        "observability": [
            {"id": "trace-collector",   "label": "Trace Collector",  "type": "trace-collector",  "icon": "🔭", "color": "#06b6d4"},
            {"id": "span-recorder",     "label": "Span Recorder",    "type": "span-recorder",    "icon": "📏", "color": "#0891b2"},
            {"id": "metrics-emitter",   "label": "Metrics Emitter",  "type": "metrics-emitter",  "icon": "📈", "color": "#0e7490"},
        ],
    }
}

# Palette extension for Phase 4 additions to existing categories
# (injected via _extend_palette() called at module load time)
_P4_MEMORY_NODES = [
    {"id": "working-memory",       "label": "Working Memory",    "type": "working-memory",       "icon": "🧠", "color": "#38bdf8"},
    {"id": "semantic-cache",       "label": "Semantic Cache",    "type": "semantic-cache",       "icon": "⚡", "color": "#0ea5e9"},
    {"id": "conversation-history", "label": "Conv. History",     "type": "conversation-history", "icon": "💬", "color": "#0284c7"},
]
_P4_SAFETY_NODES = [
    {"id": "trusted-monitor",      "label": "Trusted Monitor",   "type": "trusted-monitor",      "icon": "🛡️", "color": "#f43f5e"},
    {"id": "pii-field-detector",   "label": "PII Field Detect",  "type": "pii-field-detector",   "icon": "🔐", "color": "#e11d48"},
]
_P4_TOOL_NODES = [
    {"id": "a2a-bridge",           "label": "A2A Bridge",        "type": "a2a-bridge",           "icon": "🤝", "color": "#34d399"},
    {"id": "sandbox-exec",         "label": "Sandbox Exec",      "type": "sandbox-exec",         "icon": "📦", "color": "#059669"},
]

AADC_OBJECTS["categories"]["memory"].extend(_P4_MEMORY_NODES)
AADC_OBJECTS["categories"]["safety"].extend(_P4_SAFETY_NODES)
AADC_OBJECTS["categories"]["tools_mcp"].extend(_P4_TOOL_NODES)

# ---------------------------------------------------------------------------
# Memory Layer compliance checks — vector-db / research pipeline rules
# ---------------------------------------------------------------------------

MEMORY_LAYER_CHECKS: list[dict] = [
    {
        "id": "mem-1",
        "function": "MAP",
        "category": "MAP-3",
        "title": "Vector DB requires embedding model",
        "description": "A vector-db node must be paired with an embedding-model node "
                       "(documents must be vectorized before storage).",
        "check": "vector_db_has_embedding_model",
        "weight": 15,
    },
    {
        "id": "mem-2",
        "function": "MAP",
        "category": "MAP-4",
        "title": "Vector DB requires chunker upstream",
        "description": "A vector-db node must be preceded by a chunker node "
                       "(raw documents must be chunked before embedding/indexing).",
        "check": "vector_db_has_chunker",
        "weight": 12,
    },
    {
        "id": "mem-3",
        "function": "GOVERN",
        "category": "GOV-5",
        "title": "Persistent memory must be audited",
        "description": "long-term-mem and vector-db nodes must have an audit-logger "
                       "downstream to maintain an immutable retrieval audit trail.",
        "check": "persistent_memory_has_audit",
        "weight": 15,
    },
    {
        "id": "mem-4",
        "function": "MEASURE",
        "category": "MEA-5",
        "title": "Vector DB ingestion requires data validation",
        "description": "A data-validator node must be upstream of any vector-db to prevent "
                       "RAG poisoning (MITRE ATLAS AML.T0020).",
        "check": "vector_db_has_data_validator",
        "weight": 12,
    },
]

# ---------------------------------------------------------------------------
# Compliance rule display labels
# ---------------------------------------------------------------------------

FRAMEWORK_LABELS = {
    "nist_ai_rmf":  "NIST AI RMF",
    "owasp_llm":    "OWASP LLM Top 10",
    "omb_m25_21":   "OMB M-25-21",
    "nist_ai_600":  "NIST AI 600-1",
    "mitre_atlas":  "MITRE ATLAS",
    "dod_ai_ethics":"DoD AI Ethics",
}

NODE_DESCRIPTIONS: dict[str, str] = {
    "llm":               "Large Language Model — core reasoning/generation component",
    "llm-local":         "Air-gap / Ollama-hosted LLM — no cloud dependency",
    "embedding-model":   "Converts text to dense vectors for semantic search",
    "fine-tuned-adapter":"LoRA/PEFT adapter — domain-specific model behavior",
    "classifier":        "ML classifier — intent detection, toxicity, PII, routing",
    "reranker":          "Cross-encoder — reranks retrieval results by relevance",
    "multimodal":        "Vision-language model — processes images + text",
    "vector-db":         "Stores and queries dense vector embeddings",
    "doc-store":         "Raw document repository — source for RAG pipeline",
    "short-term-mem":    "In-context or session-scoped working memory",
    "long-term-mem":     "Persistent cross-session memory store",
    "episodic-buffer":   "Ordered event/interaction history for agents",
    "knowledge-graph":   "Structured entity-relationship knowledge base",
    "embedding-cache":   "Caches computed embeddings to reduce latency/cost",
    "autonomous-agent":  "Fully autonomous agent — takes actions without approval",
    "semi-auto-agent":   "Human-supervised agent — escalates consequential decisions",
    "orchestrator":      "Multi-agent dispatcher — decomposes tasks, routes to sub-agents",
    "sub-agent":         "Specialized child agent — executes a narrow task",
    "researcher-agent":  "Web/doc research specialist agent",
    "writer-agent":      "Content generation specialist agent",
    "analyst-agent":     "Data analysis and insight generation agent",
    "reviewer-agent":    "Output review and critique agent",
    "mcp-server":        "Model Context Protocol server — exposes tools/resources/prompts",
    "mcp-gateway":       "Auth + rate-limit gateway in front of MCP servers",
    "tool-chain":        "Ordered sequence of function invocations",
    "function-caller":   "Single function/tool invocation",
    "output-validator":  "Enforces structured output schema",
    "external-api":      "Third-party REST/GraphQL API endpoint",
    "code-executor":     "Sandboxed code execution environment",
    "web-search":        "Web search tool — returns live web results",
    "training-data":     "Dataset used for fine-tuning or RLHF",
    "inference-input":   "User query or prompt — entry point to the system",
    "feedback-collector":"Collects user preference/rating signals",
    "rlhf-pipeline":     "Reinforcement learning from human feedback training loop",
    "data-lake":         "Bulk data source for ingestion and preprocessing",
    "chunker":           "Splits documents into retrievable chunks",
    "data-validator":    "Validates input schema and data types",
    "guardrail":         "General-purpose content policy enforcer",
    "pii-detector":      "Detects personally identifiable information",
    "toxicity-filter":   "Filters harmful, biased, or inappropriate content",
    "confidence-threshold":"Blocks output below a minimum confidence score",
    "circuit-breaker":   "Halts runaway agent chains after N failures",
    "rate-limiter":      "Enforces token/request rate limits",
    "input-sanitizer":   "Mitigates prompt injection — sanitizes user inputs",
    "redaction-engine":  "Redacts PII/CUI before output reaches end users",
    "hitl-gate":         "Human-in-the-loop approval gate",
    "audit-logger":      "Append-only event log for compliance audit trail",
    "approval-workflow": "Multi-stage approval chain (e.g. HITL engine)",
    "caio-override":     "Chief AI Officer manual override capability",
    "compliance-reporter":"Generates framework-aligned compliance reports",
    "alert-manager":     "Threshold-based alert dispatcher",
    "prompt-registry":   "Versioned prompt template store",
    "gpu-cluster":       "Model inference compute cluster",
    "model-registry":    "Tracks model versions, lineage, and provenance",
    "token-budget":      "Per-request token ceiling enforcer",
    "vector-index":      "ANN index (HNSW, IVF) for fast similarity search",
    "siem-forwarder":    "Forwards security events to SIEM (Splunk, Sentinel)",
    "baseline-snapshot": "Captures behavioral baseline for drift comparison",
    "drift-detector":    "Compares production behavior against baseline",
    # Phase 4 — execution control
    "checkpoint":           "Save/restore execution state — enables fault recovery and timeline branching",
    "fork":                 "Splits execution into parallel branches — each branch receives the same state",
    "join":                 "Convergence point — waits for all upstream parallel branches to complete",
    "parallel-group":       "Visual container grouping parallel execution nodes into a named swim-lane",
    # Phase 4 — observability
    "trace-collector":      "Collects distributed traces from agent executions (OTel-compatible)",
    "span-recorder":        "Records individual LLM call spans with latency, tokens, and metadata",
    "metrics-emitter":      "Emits runtime metrics (token rates, latency, error rates) to monitoring",
    # Phase 4 — memory enrichment
    "working-memory":       "Transient in-flight memory scoped to a single agent turn",
    "semantic-cache":       "Caches semantically similar query responses to reduce LLM calls",
    "conversation-history": "Stores ordered conversation turns for multi-turn context injection",
    # Phase 4 — safety extensions
    "trusted-monitor":      "Out-of-band monitor that audits all autonomous agent outputs independently",
    "pii-field-detector":   "Field-level PII scanner — identifies and tags specific sensitive fields",
    # Phase 4 — A2A / sandbox
    "a2a-bridge":           "Agent-to-Agent communication bridge — zero-trust inter-agent messaging",
    "sandbox-exec":         "Isolated sandboxed execution environment for untrusted code or tools",
}

# ---------------------------------------------------------------------------
# Enhancement constants — cost estimation, autonomy colors, IaC mapping
# ---------------------------------------------------------------------------

AUTONOMY_COLORS: dict[int, str] = {
    0: "#27ae60",   # L0 Human-Operated — green
    1: "#2ecc71",   # L1 Human-Delegated — light green
    2: "#f39c12",   # L2 Human-Supervised — amber
    3: "#e67e22",   # L3 Human-Initiated — orange
    4: "#e74c3c",   # L4 Fully Autonomous — red
    5: "#8e44ad",   # L5 Unconstrained — purple (CRITICAL)
}

AADC_MODEL_COSTS: dict[str, dict] = {
    # model_id: {input: $/1k-tokens, output: $/1k-tokens, avg_in: tokens, avg_out: tokens}
    "claude-opus-4":      {"input": 0.015,    "output": 0.075,   "avg_in": 800, "avg_out": 400},
    "claude-sonnet-4":    {"input": 0.003,    "output": 0.015,   "avg_in": 600, "avg_out": 300},
    "claude-haiku-4":     {"input": 0.00025,  "output": 0.00125, "avg_in": 500, "avg_out": 250},
    "gpt-4o":             {"input": 0.005,    "output": 0.015,   "avg_in": 700, "avg_out": 350},
    "gpt-4o-mini":        {"input": 0.00015,  "output": 0.0006,  "avg_in": 500, "avg_out": 250},
    "llama-3.3-70b":      {"input": 0.00059,  "output": 0.00079, "avg_in": 600, "avg_out": 300},
    "llama-3.1-8b":       {"input": 0.00018,  "output": 0.00018, "avg_in": 500, "avg_out": 250},
    "mistral-large":      {"input": 0.002,    "output": 0.006,   "avg_in": 600, "avg_out": 300},
    "gemini-1.5-pro":     {"input": 0.00125,  "output": 0.005,   "avg_in": 700, "avg_out": 350},
    "gemini-1.5-flash":   {"input": 0.000075, "output": 0.0003,  "avg_in": 500, "avg_out": 250},
    "qwen3-local":        {"input": 0.0,      "output": 0.0,     "avg_in": 600, "avg_out": 300},
    "ollama-local":       {"input": 0.0,      "output": 0.0,     "avg_in": 600, "avg_out": 300},
}

# Default model slug for an unspecified LLM node. Overridable at runtime via the
# AADC_DEFAULT_LLM_MODEL env var (LLM config lives in .env, never hard-coded in
# call sites). Must be a key in AADC_MODEL_COSTS.
AADC_DEFAULT_LLM_MODEL: str = "gpt-4o"

# Cost-optimization swap suggestions: expensive model → cheaper alternative.
# BOTH sides MUST be keys in AADC_MODEL_COSTS — the alternative's prices are
# DERIVED from AADC_MODEL_COSTS at suggestion time so they never drift from the
# single pricing source above.
AADC_MODEL_SWAPS: dict[str, str] = {
    "claude-opus-4":  "claude-sonnet-4",
    "gpt-4o":         "gpt-4o-mini",
    "gemini-1.5-pro": "gemini-1.5-flash",
    "mistral-large":  "llama-3.3-70b",
}

# node-type prefix → (terraform_resource, helm_template_type)
AADC_IAC_NODE_MAP: dict[str, tuple[str, str]] = {
    "autonomous-agent":  ("kubernetes_deployment", "deployment"),
    "semi-auto-agent":   ("kubernetes_deployment", "deployment"),
    "orchestrator":      ("kubernetes_deployment", "deployment"),
    "sub-agent":         ("kubernetes_deployment", "deployment"),
    "researcher-agent":  ("kubernetes_deployment", "deployment"),
    "writer-agent":      ("kubernetes_deployment", "deployment"),
    "analyst-agent":     ("kubernetes_deployment", "deployment"),
    "reviewer-agent":    ("kubernetes_deployment", "deployment"),
    "llm":               ("null_resource",         "configmap"),      # managed model — no deploy
    "llm-local":         ("helm_release",          "ollama-values"),  # Ollama
    "embedding-model":   ("kubernetes_deployment", "deployment"),
    "mcp-server":        ("kubernetes_service",    "service"),
    "mcp-gateway":       ("kubernetes_service",    "service"),
    "tool-chain":        ("kubernetes_deployment", "deployment"),
    "function-caller":   ("kubernetes_deployment", "deployment"),
    "code-executor":     ("kubernetes_deployment", "deployment"),
    "vector-db":         ("helm_release",          "vector-db-values"),
    "doc-store":         ("kubernetes_deployment", "deployment"),
    "knowledge-graph":   ("kubernetes_deployment", "deployment"),
    "guardrail":         ("kubernetes_deployment", "deployment"),
    "pii-detector":      ("kubernetes_deployment", "deployment"),
    "trusted-monitor":   ("kubernetes_deployment", "deployment"),
    "hitl-gate":         ("kubernetes_deployment", "deployment"),
    "audit-logger":      ("kubernetes_deployment", "deployment"),
    "gpu-cluster":       ("null_resource",         "configmap"),      # infra-provisioned
    "model-registry":    ("kubernetes_deployment", "deployment"),
    "trace-collector":   ("helm_release",          "otel-values"),
    "metrics-emitter":   ("kubernetes_deployment", "deployment"),
}

# ── Ontology Mapping (AADC -> ICDEV AI ontology) ──────────────────────────────
AADC_ONTOLOGY_MAP: dict[str, str] = {
    # Models
    "llm":                "https://icdev.dev/ontology/ai#LanguageModel.LLM",
    "llm-local":          "https://icdev.dev/ontology/ai#LanguageModel.LocalLLM",
    "embedding-model":    "https://icdev.dev/ontology/ai#LanguageModel.Embedding",
    "fine-tuned-adapter": "https://icdev.dev/ontology/ai#LanguageModel.FineTunedAdapter",
    "classifier":         "https://icdev.dev/ontology/ai#LanguageModel.Classifier",
    "reranker":           "https://icdev.dev/ontology/ai#LanguageModel.Reranker",
    "multimodal":         "https://icdev.dev/ontology/ai#LanguageModel.Multimodal",
    # Memory
    "vector-db":          "https://icdev.dev/ontology/ai#Memory.VectorDatabase",
    "doc-store":          "https://icdev.dev/ontology/ai#Memory.DocumentStore",
    "short-term-mem":     "https://icdev.dev/ontology/ai#Memory.ShortTerm",
    "long-term-mem":      "https://icdev.dev/ontology/ai#Memory.LongTerm",
    "episodic-buffer":    "https://icdev.dev/ontology/ai#Memory.EpisodicBuffer",
    "knowledge-graph":    "https://icdev.dev/ontology/ai#Memory.KnowledgeGraph",
    "embedding-cache":    "https://icdev.dev/ontology/ai#Memory.EmbeddingCache",
    "working-memory":     "https://icdev.dev/ontology/ai#Memory.WorkingMemory",
    "semantic-cache":     "https://icdev.dev/ontology/ai#Memory.SemanticCache",
    "conversation-history": "https://icdev.dev/ontology/ai#Memory.ConversationHistory",
    # Agents
    "autonomous-agent":   "https://icdev.dev/ontology/ai#Agent.Autonomous",
    "semi-auto-agent":    "https://icdev.dev/ontology/ai#Agent.SemiAutonomous",
    "orchestrator":       "https://icdev.dev/ontology/ai#Agent.Orchestrator",
    "sub-agent":          "https://icdev.dev/ontology/ai#Agent.SubAgent",
    "researcher-agent":   "https://icdev.dev/ontology/ai#Agent.Researcher",
    "writer-agent":       "https://icdev.dev/ontology/ai#Agent.Writer",
    "analyst-agent":      "https://icdev.dev/ontology/ai#Agent.Analyst",
    "reviewer-agent":     "https://icdev.dev/ontology/ai#Agent.Reviewer",
    "swarm-agent":        "https://icdev.dev/ontology/ai#Agent.Swarm",
    "fan-out-coordinator": "https://icdev.dev/ontology/ai#Agent.FanOutCoordinator",
    "fan-in-aggregator":  "https://icdev.dev/ontology/ai#Agent.FanInAggregator",
    # Tools / MCP
    "mcp-server":         "https://icdev.dev/ontology/ai#Tool.MCPServer",
    "mcp-gateway":        "https://icdev.dev/ontology/ai#Tool.MCPGateway",
    "tool-chain":         "https://icdev.dev/ontology/ai#Tool.ToolChain",
    "function-caller":    "https://icdev.dev/ontology/ai#Tool.FunctionCaller",
    "output-validator":   "https://icdev.dev/ontology/ai#Tool.OutputValidator",
    "external-api":       "https://icdev.dev/ontology/ai#Tool.ExternalAPI",
    "code-executor":      "https://icdev.dev/ontology/ai#Tool.CodeExecutor",
    "web-search":         "https://icdev.dev/ontology/ai#Tool.WebSearch",
    "a2a-bridge":         "https://icdev.dev/ontology/ai#Tool.A2ABridge",
    "sandbox-exec":       "https://icdev.dev/ontology/ai#Tool.SandboxExecutor",
    # Data
    "training-data":      "https://icdev.dev/ontology/ai#Data.TrainingDataset",
    "inference-input":    "https://icdev.dev/ontology/ai#Data.InferenceInput",
    "feedback-collector": "https://icdev.dev/ontology/ai#Data.FeedbackCollector",
    "rlhf-pipeline":      "https://icdev.dev/ontology/ai#Data.RLHFPipeline",
    "data-lake":          "https://icdev.dev/ontology/ai#Data.DataLake",
    "chunker":            "https://icdev.dev/ontology/ai#Data.Chunker",
    "data-validator":     "https://icdev.dev/ontology/ai#Data.Validator",
    # Safety
    "guardrail":          "https://icdev.dev/ontology/ai#Safety.Guardrail",
    "pii-detector":       "https://icdev.dev/ontology/ai#Safety.PIIDetector",
    "toxicity-filter":    "https://icdev.dev/ontology/ai#Safety.ToxicityFilter",
    "confidence-threshold": "https://icdev.dev/ontology/ai#Safety.ConfidenceThreshold",
    "circuit-breaker":    "https://icdev.dev/ontology/ai#Safety.CircuitBreaker",
    "rate-limiter":       "https://icdev.dev/ontology/ai#Safety.RateLimiter",
    "input-sanitizer":    "https://icdev.dev/ontology/ai#Safety.InputSanitizer",
    "redaction-engine":   "https://icdev.dev/ontology/ai#Safety.RedactionEngine",
    "trusted-monitor":    "https://icdev.dev/ontology/ai#Safety.TrustedMonitor",
    "pii-field-detector": "https://icdev.dev/ontology/ai#Safety.PIIFieldDetector",
    # Governance
    "hitl-gate":          "https://icdev.dev/ontology/ai#Governance.HITLGate",
    "audit-logger":       "https://icdev.dev/ontology/ai#Governance.AuditLogger",
    "approval-workflow":  "https://icdev.dev/ontology/ai#Governance.ApprovalWorkflow",
    "caio-override":      "https://icdev.dev/ontology/ai#Governance.CAIOOverride",
    "compliance-reporter": "https://icdev.dev/ontology/ai#Governance.ComplianceReporter",
    "alert-manager":      "https://icdev.dev/ontology/ai#Governance.AlertManager",
    "prompt-registry":    "https://icdev.dev/ontology/ai#Governance.PromptRegistry",
    # Infrastructure
    "gpu-cluster":        "https://icdev.dev/ontology/ai#Infrastructure.GPUCluster",
    "model-registry":     "https://icdev.dev/ontology/ai#Infrastructure.ModelRegistry",
    "token-budget":       "https://icdev.dev/ontology/ai#Infrastructure.TokenBudget",
    "vector-index":       "https://icdev.dev/ontology/ai#Infrastructure.VectorIndex",
    "siem-forwarder":     "https://icdev.dev/ontology/ai#Infrastructure.SIEMForwarder",
    "baseline-snapshot":  "https://icdev.dev/ontology/ai#Infrastructure.BaselineSnapshot",
    "drift-detector":     "https://icdev.dev/ontology/ai#Infrastructure.DriftDetector",
    # Execution
    "checkpoint":         "https://icdev.dev/ontology/ai#Execution.Checkpoint",
    "fork":               "https://icdev.dev/ontology/ai#Execution.Fork",
    "join":               "https://icdev.dev/ontology/ai#Execution.Join",
    "parallel-group":     "https://icdev.dev/ontology/ai#Execution.ParallelGroup",
    # Observability
    "trace-collector":    "https://icdev.dev/ontology/ai#Observability.TraceCollector",
    "span-recorder":      "https://icdev.dev/ontology/ai#Observability.SpanRecorder",
    "metrics-emitter":    "https://icdev.dev/ontology/ai#Observability.MetricsEmitter",
}
