<!-- CUI // SP-CTI -->

# OWASP LLM Top 10 — Red Team Framework

Red teaming AI systems requires a structured framework. The OWASP LLM Top 10 (2025) is the industry-standard attack taxonomy for LLM-based applications. Before you can defend your system, you need to understand all 10 attack surfaces and the methodology to test each one systematically.

## The OWASP LLM Top 10 (2025)

| ID | Name | Description |
|---|---|---|
| LLM01 | Prompt Injection | User input manipulates LLM behavior beyond intended scope |
| LLM02 | Insecure Output Handling | LLM output is consumed by downstream components without validation |
| LLM03 | Training Data Poisoning | Malicious data influences model weights or fine-tuning |
| LLM04 | Model Denial of Service | Adversarial inputs cause excessive resource consumption |
| LLM05 | Supply Chain Vulnerabilities | Third-party models, datasets, or plugins introduce risk |
| LLM06 | Sensitive Information Disclosure | Model reveals confidential data from training or context |
| LLM07 | Insecure Plugin Design | LLM tool/plugin calls execute with insufficient validation |
| LLM08 | Excessive Agency | LLM has more permissions than required for its task |
| LLM09 | Overreliance | Users or systems trust LLM output without verification |
| LLM10 | Model Theft | Adversary extracts model weights or replicates behavior via API |

## Red Teaming Methodology

Red teaming LLMs follows a structured 5-phase process:

### Phase 1: Map the Attack Surface

Enumerate every input channel, data source, and tool the LLM can access:
- Direct user inputs (chat, forms, API calls)
- Indirect inputs (documents retrieved by RAG, tool outputs, database query results)
- Tool permissions (file read/write, HTTP calls, database queries, shell execution)
- External integrations (webhooks, email parsing, document ingestion)

### Phase 2: Threat Model Per OWASP Item

For each of the 10 items, answer: "Is my system vulnerable? What is the blast radius?"

### Phase 3: Craft Test Cases

Write specific, reproducible test cases for each vulnerability. Test cases should be in code — not ad-hoc manual probing.

### Phase 4: Run Exploits in Isolated Environment

Never red team a production system. Use a staging environment with production-equivalent data (sanitized of real CUI).

### Phase 5: Document Findings

Every finding needs: vulnerability ID, test case, reproduction steps, severity, and remediation.

## LLM08 (Excessive Agency): The Agentic System's Biggest Risk

LLM08 is the most critical vulnerability for agentic systems. An agent with file read/write, network access, and database write permissions that becomes compromised (via LLM01 injection, LLM07 plugin exploit, or LLM06 disclosure) has an enormous blast radius.

**Blast radius calculation:**

```python
# Enumerate every tool registered to an agent
agent_tools = [
    "read_file",          # can access any file on the filesystem
    "write_file",         # can modify any file
    "execute_sql",        # can modify any database table
    "make_http_request",  # can exfiltrate data or call external APIs
    "send_email",         # can send emails as the system
]

# Every tool capability multiplies the blast radius of a compromise
# An agent with all 5 tools above, when compromised, can:
# - Exfiltrate all files and database contents
# - Modify database records
# - Call external attacker infrastructure
# - Send phishing emails as your system
```

The principle of least privilege applies: an agent that only reads documents should have `read_file` only. Writing requires explicit approval.

## The AADC Canvas and MITRE ATLAS

The AADC (AI-Assisted Design Canvas) includes node types mapped to the MITRE ATLAS (Adversarial Threat Landscape for AI Systems) framework. Each OWASP LLM item has a corresponding ATLAS technique ID. When you model your system in the AADC, the guardrail node automatically flags designs that expose OWASP LLM attack surfaces.

ATLAS technique catalog relevant to this mission:
- `AML.T0054` — LLM Prompt Injection
- `AML.T0048` — Societal Harm — maps to LLM09 Overreliance
- `AML.T0051` — LLM Plugin Compromise — maps to LLM07

**Your task:** In the next step, audit your agent.
