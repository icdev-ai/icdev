"""
SecOps Mission 5 — AADC Threat Modeling
Goal: Run a STRIDE + MITRE ATLAS threat model against an agentic pipeline design graph.
"""

# ── Target design graph (document processing pipeline with intentional security gaps) ──

DESIGN_GRAPH = {
    "nodes": [
        {"id": "n1", "type": "inference-input",  "label": "User Query"},
        {"id": "n2", "type": "llm",              "label": "GPT-4 Reasoner"},
        {"id": "n3", "type": "tool-chain",       "label": "Tool Orchestrator"},
        {"id": "n4", "type": "external-api",     "label": "Document API"},
        {"id": "n5", "type": "output-validator", "label": "Response Validator"},
        {"id": "n6", "type": "vector-db",        "label": "RAG Corpus"},
        {"id": "n7", "type": "training-data",    "label": "Fine-tune Dataset"},
        {"id": "n8", "type": "autonomous-agent", "label": "Doc Analysis Agent"},
    ],
    "edges": [
        {"source": "n1", "target": "n2"},
        {"source": "n2", "target": "n3"},
        {"source": "n3", "target": "n4"},
        {"source": "n4", "target": "n5"},
        {"source": "n2", "target": "n6"},
        {"source": "n8", "target": "n3"},
    ],
}

# ── ATLAS threat map (subset of the full AADC constants) ──

ATLAS_THREAT_MAP = {
    "llm":              ["AML.T0051", "AML.T0048", "AML.T0040"],
    "llm-local":        ["AML.T0048", "AML.T0012"],
    "training-data":    ["AML.T0020", "AML.T0019"],
    "vector-db":        ["AML.T0048", "AML.T0020"],
    "mcp-server":       ["AML.T0051", "AML.T0043"],
    "external-api":     ["AML.T0043", "AML.T0044"],
    "fine-tuned-adapter": ["AML.T0012", "AML.T0019"],
    "autonomous-agent": ["AML.T0051", "AML.T0052"],
    "rlhf-pipeline":    ["AML.T0020"],
    "embedding-model":  ["AML.T0048"],
}

# STRIDE rules: each entry applies to a set of node types
STRIDE_RULES = [
    {
        "id": "STR-01", "category": "Spoofing", "severity": "HIGH",
        "applies_to": {"llm", "llm-local", "autonomous-agent", "semi-auto-agent",
                       "orchestrator", "sub-agent"},
        "title": "Agent/Model Identity Spoofing",
        "description": "An attacker impersonates a legitimate AI agent or model endpoint.",
        "mitigation": "Implement mutual TLS + agent identity attestation. Add audit-logger.",
    },
    {
        "id": "TAM-01", "category": "Tampering", "severity": "CRITICAL",
        "applies_to": {"training-data", "inference-input", "data-lake",
                       "feedback-collector", "rlhf-pipeline"},
        "title": "Training Data / RAG Corpus Poisoning",
        "description": "Adversarial content injected into training data or retrieval corpus.",
        "mitigation": "Add data-validator node upstream. Cryptographic signatures on ingested docs.",
    },
    {
        "id": "REP-01", "category": "Repudiation", "severity": "HIGH",
        "applies_to": {"autonomous-agent", "semi-auto-agent", "orchestrator",
                       "hitl-gate", "approval-workflow"},
        "title": "Agent Action Non-Repudiation",
        "description": "Agent actions cannot be traced to a specific invocation.",
        "mitigation": "Require audit-logger downstream of every autonomous-agent.",
    },
    {
        "id": "INF-01", "category": "Information Disclosure", "severity": "CRITICAL",
        "applies_to": {"llm", "llm-local", "vector-db", "long-term-mem",
                       "short-term-mem", "embedding-cache"},
        "title": "Sensitive Data Extraction",
        "description": "Model memorization or embedding inversion exposes sensitive/PII data.",
        "mitigation": "Add pii-detector and redaction-engine to the output path.",
    },
    {
        "id": "DOS-01", "category": "Denial of Service", "severity": "MEDIUM",
        "applies_to": {"mcp-server", "tool-chain", "external-api",
                       "function-caller", "llm", "llm-local"},
        "title": "Token/Resource Exhaustion",
        "description": "Unbounded requests exhaust model tokens or API rate limits.",
        "mitigation": "Add token-budget and rate-limiter nodes to the pipeline.",
    },
    {
        "id": "EOP-01", "category": "Elevation of Privilege", "severity": "CRITICAL",
        "applies_to": {"autonomous-agent", "orchestrator"},
        "title": "Autonomous Agent Privilege Escalation",
        "description": "Fully autonomous agent acts beyond its intended scope without oversight.",
        "mitigation": "Add circuit-breaker and hitl-gate downstream of every autonomous-agent.",
    },
]


# ── Step 1: STRIDE Analysis ───────────────────────────────────────────────────

def run_stride_analysis(graph: dict) -> list[dict]:
    """TODO: Identify applicable STRIDE threats for the given design graph.

    For each STRIDE_RULE, check if any node in graph["nodes"] has a type that
    appears in the rule's "applies_to" set. If so, add a threat finding dict:
        {
            "id": rule["id"],
            "category": rule["category"],
            "severity": rule["severity"],
            "title": rule["title"],
            "description": rule["description"],
            "mitigation": rule["mitigation"],
            "affected_nodes": [list of node ids whose type matched],
        }

    Return the list of all matching threat findings.
    """
    # YOUR CODE HERE
    pass


# ── Step 2: ATLAS Technique Mapping ──────────────────────────────────────────

def map_atlas_techniques(graph: dict) -> dict:
    """TODO: Map node types to MITRE ATLAS adversarial techniques.

    Iterate graph["nodes"]. For each node whose type exists in ATLAS_THREAT_MAP,
    add an entry to the result dict: {node_type: [technique_ids]}.
    Each node type should appear at most once (use a dict to deduplicate by type).

    Return: {node_type: [technique_id, ...], ...}
    """
    # YOUR CODE HERE
    pass


# ── Step 3: Threat Report Generator ──────────────────────────────────────────

def generate_threat_report(threats: list[dict], atlas_mapping: dict) -> str:
    """TODO: Generate a structured threat model report string.

    The report must include:
    1. A header: "## Threat Model Report"
    2. A summary line: "Total findings: N (CRITICAL: X, HIGH: Y, MEDIUM: Z)"
    3. For CRITICAL findings: title, description, mitigation
    4. For HIGH findings: title and mitigation only
    5. An "## ATLAS Technique Mapping" section listing each node type and its techniques
       — must include at least one "AML.T" reference

    Return the full report as a single multi-line string.
    """
    # YOUR CODE HERE
    pass


# ── Test ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    threats = run_stride_analysis(DESIGN_GRAPH)
    print(f"Threats found: {len(threats)}")
    for t in threats:
        print(f"  [{t['severity']}] {t['id']} — {t['title']}")

    atlas = map_atlas_techniques(DESIGN_GRAPH)
    print(f"\nATLAS mappings: {len(atlas)} node types")

    report = generate_threat_report(threats, atlas)
    print("\n" + report)
