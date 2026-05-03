# CUI // SP-CTI
"""Agentic AI Design Canvas — Constants, node palette, compliance rule maps."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Node type sets — used by agentic_engine.py for rule evaluation
# ---------------------------------------------------------------------------

MODEL_NODES = {"llm", "llm-local", "embedding-model", "fine-tuned-adapter",
               "classifier", "reranker", "multimodal"}

MEMORY_NODES = {"vector-db", "doc-store", "short-term-mem", "long-term-mem",
                "episodic-buffer", "knowledge-graph", "embedding-cache"}

AGENT_NODES = {"autonomous-agent", "semi-auto-agent", "orchestrator",
               "sub-agent", "researcher-agent", "writer-agent",
               "analyst-agent", "reviewer-agent"}

TOOL_MCP_NODES = {"mcp-server", "mcp-gateway", "tool-chain",
                  "function-caller", "output-validator", "external-api",
                  "code-executor", "web-search"}

DATA_NODES = {"training-data", "inference-input", "feedback-collector",
              "rlhf-pipeline", "data-lake", "chunker", "data-validator"}

SAFETY_NODES = {"guardrail", "pii-detector", "toxicity-filter",
                "confidence-threshold", "circuit-breaker", "rate-limiter",
                "input-sanitizer", "redaction-engine"}

GOVERNANCE_NODES = {"hitl-gate", "audit-logger", "approval-workflow",
                    "caio-override", "compliance-reporter", "alert-manager",
                    "prompt-registry"}

INFRA_NODES = {"gpu-cluster", "model-registry", "token-budget",
               "vector-index", "siem-forwarder", "baseline-snapshot",
               "drift-detector"}

ALL_NODE_TYPES = (MODEL_NODES | MEMORY_NODES | AGENT_NODES | TOOL_MCP_NODES
                  | DATA_NODES | SAFETY_NODES | GOVERNANCE_NODES | INFRA_NODES)

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
    }
}

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
}
