# Threat Model an Agentic AI Pipeline

Attackers don't target your code — they target your AI's behavior. In this mission you'll run a
STRIDE + MITRE ATLAS threat model against a realistic agentic pipeline graph and generate
an actionable remediation report.

## The target system

You're analyzing an autonomous document processing pipeline:

```
inference-input → llm → tool-chain → external-api → output-validator
                   │
                   ▼
              vector-db (RAG corpus)
```

This system has intentional security gaps. Your job: find them all.

## What you'll build

```python
design_graph = { "nodes": [...], "edges": [...] }

# Step 1: Run STRIDE analysis
threats = run_stride_analysis(design_graph)
# → [{"id": "TAM-01", "category": "Tampering", "severity": "CRITICAL", ...}, ...]

# Step 2: Map MITRE ATLAS techniques
atlas_mapping = map_atlas_techniques(design_graph)
# → {"llm": ["AML.T0051", ...], "vector-db": ["AML.T0048", ...]}

# Step 3: Generate remediation report
report = generate_threat_report(threats, atlas_mapping)
# → "## Threat Model Report\n### CRITICAL findings: ..."
```

## STRIDE rules

Apply these rules to each node in the graph:

| Category | Applies To | Trigger |
|---|---|---|
| Spoofing | LLM, Agent nodes | Agent identity can be impersonated |
| Tampering | Data, Training nodes | Corpus/weights can be poisoned |
| Repudiation | Agent, Governance | Actions not traceable without audit-logger |
| Info Disclosure | Model, Memory | PII/sensitive data can be extracted |
| DoS | Tool, MCP | Rate-limiter or token-budget absent |
| Elevation of Privilege | Agent nodes | Autonomous agent without circuit-breaker |

## ATLAS technique mapping

Use the provided `ATLAS_THREAT_MAP` — it maps node types to known adversarial ML technique IDs.
Your `map_atlas_techniques()` must iterate all nodes and build a dict of `{node_type: [technique_ids]}`.

## Success criteria

- `run_stride_analysis()` returns ≥ 4 threat findings for this graph
- At least 2 findings have `severity = "CRITICAL"` or `"HIGH"`
- `map_atlas_techniques()` maps ≥ 3 node types to ATLAS techniques
- `generate_threat_report()` returns a string containing "## Threat Model" and at least one "AML.T" reference
- The report includes at least one concrete mitigation for a CRITICAL finding
