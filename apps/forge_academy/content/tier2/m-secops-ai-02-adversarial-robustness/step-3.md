<!-- CUI // SP-CTI -->

# Remediation Plan

Your audit produced a findings report. Now you need to prioritize, remediate, and verify. This step defines the priority matrix, critical remediations for the highest-impact findings, and the verification process.

## Remediation Priority Matrix

Score each finding by severity × exploitability to set priority order:

```
               EXPLOITABILITY
               Trivial     Moderate    Complex
           ┌───────────┬───────────┬───────────┐
  Critical │ P1 — NOW  │ P1 — NOW  │ P2 — NEXT │
           ├───────────┼───────────┼───────────┤
  High     │ P1 — NOW  │ P2 — NEXT │ P3 — PLAN │
           ├───────────┼───────────┼───────────┤
  Medium   │ P2 — NEXT │ P3 — PLAN │ P4 — LOG  │
           ├───────────┼───────────┼───────────┤
  Low      │ P3 — PLAN │ P4 — LOG  │ P4 — LOG  │
           └───────────┴───────────┴───────────┘
```

**P1** = remediate before next deployment. **P4** = log for next quarterly review.

## Critical Remediations

### LLM01: Multi-Layer Injection Defense

If your audit found injection vulnerabilities, the remediation is the three-layer detector from Mission SecOps-AI-01. This is a P1 remediation for any agent that has tool access (file, network, database).

```python
# Wire detector into every agent entrypoint
from tools.security.prompt_injection import PromptInjectionDetector
detector = PromptInjectionDetector()

def agent_entrypoint(user_input: str, session_id: str):
    result = detector.detect(user_input, session_id=session_id)
    if result.detected:
        return error_response("Request could not be processed.", code=400)
    return process_request(user_input)
```

### LLM06: Strip PII/CUI Before Cloud API Calls

System prompts for cloud models must never contain CUI-marked content. Use `classification_manager.py` to detect and mask before transmission:

```python
from tools.security.classification_manager import ClassificationManager

cm = ClassificationManager()

def prepare_system_prompt_for_cloud(system_prompt: str) -> str:
    classification = cm.classify(system_prompt)
    if classification.level in ("CUI", "SECRET"):
        return cm.mask_cui(system_prompt)
    return system_prompt
```

For IL4/IL5 systems: route all requests containing CUI to the local Ollama model only. Never transmit to cloud APIs.

### LLM08: Principle of Least Privilege for Agent Tools

An agent's tool set should be the minimum required for its defined function:

```python
# Before: agent had all tools
RESEARCH_AGENT_TOOLS = [
    "read_file", "write_file", "execute_sql",
    "make_http_request", "send_email",
]

# After: research agent only reads
RESEARCH_AGENT_TOOLS = [
    "read_file",          # read documents only
    "make_http_request",  # read-only HTTP, no POST
]

# Write operations require a separate, human-in-the-loop approval step
WRITE_APPROVAL_REQUIRED = True
```

### LLM09: Mandatory Confidence Indicators

Every surface that displays AI-generated content must include a confidence indicator and an "AI-generated" label. This is not optional for production systems. Users who treat AI output as authoritative without this labeling will make decisions based on hallucinated content.

```html
<!-- Required template pattern for all LLM output surfaces -->
<div class="ai-output-container">
  <span class="ai-badge">AI-Generated</span>
  <span class="confidence-indicator" title="Confidence score">
    {{ (quality_score * 100)|round(0)|int }}%
  </span>
  <div class="ai-content">{{ llm_output | e }}</div>
</div>
```

Note: use `| e` (HTML escape) to remediate LLM02 (Insecure Output Handling) simultaneously.

## Verification Process

After implementing each remediation, re-run the specific audit test from Step 2:

```bash
# Re-run full OWASP audit after remediations
python tools/security/owasp_audit.py --agent research-agent --json > post_remediation.json

# Compare findings count
python -c "
import json
before = json.load(open('pre_remediation.json'))
after = json.load(open('post_remediation.json'))
print(f'Before: {before[\"total\"]} findings')
print(f'After:  {after[\"total\"]} findings')
print(f'Closed: {before[\"total\"] - after[\"total\"]} findings')
"
```

## Track in FORGE IGNITE Innovation Pipeline

After closing findings, register the remediation patterns in the FORGE IGNITE innovation pipeline so they are applied to all future agents automatically:

```bash
python tools/dx/companion.py --sync --write --json
python tools/workflow/coherence_checker.py --all --fix --gate
```

The coherence checker enforces that new agent blueprints include injection detection and least-privilege tool sets, so future engineers inherit the security baseline automatically.

**Your task:** Answer the reflection questions.
