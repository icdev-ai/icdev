---
ontology_id: icdev:mission:m-secops-ai-02-adversarial-robustness:step:3
step_class: icdev:Assessment
---

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
# Wire the detector into every agent entrypoint
from tools.security.prompt_injection_detector import PromptInjectionDetector
detector = PromptInjectionDetector()

def agent_entrypoint(user_input: str):
    result = detector.scan_text(user_input, source="agent_entrypoint")
    if result["action"] in ("block", "flag"):
        detector.log_detection(result)          # append-only prompt_injection_log
        return error_response("Request could not be processed.", code=400)
    return process_request(user_input)
```

### LLM06: Strip PII/CUI Before Cloud API Calls

System prompts for cloud models must never contain CUI-marked content. In ICDEV, two mechanisms do this work, and neither is "classify then mask":

- **PII**: every `LLMRouter.invoke()` call runs the pre-invoke redaction hook (`tools/redaction/`, configured in `args/redaction_config.yaml`). The hook masks names, emails, identifiers and similar before the request leaves. With `fail_closed: true`, the call is blocked if a required sanitizer cannot run.
- **CUI**: redaction does not decide whether text is CUI. Keep CUI away from cloud models by **routing**. Give each LLM function that handles CUI a provider chain of local or IL-appropriate models in `args/llm_config.yaml`. `tools/compliance/classification_manager.py` supplies markings and IL requirements (for example `get_classification_for_il`), not text masking.

For IL4/IL5 systems: route every function that sees CUI to an approved local or IL-authorized model. Never send it to commercial cloud APIs.

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
# Re-run your own audit harness from Step 2 (save its JSON before and after),
# and re-score the project with ICDEV's assessor:
python tools/compliance/owasp_llm_assessor.py --project-id <id> --json
python my_owasp_audit.py --agent research-agent > post_remediation.json   # your Step 2 harness

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

## Make the Fix Stick

A remediation that lives only in one agent will be missing from the next one. Make it structural:

- **Design time**: model the agent on the Agentic AI Design Canvas (`/agentic-ai`). Checks `llm01` (input-sanitizer upstream of every LLM), `llm06` (pii-detector + redaction-engine), `llm08` (circuit-breaker or hitl-gate downstream of every autonomous agent) and `llm10` (audit-logger) fail any future design that drops these controls.
- **Run time**: send every model call through `LLMRouter`, so the injection screen and redaction hook apply without anyone having to remember them.
- **Static check**: `python tools/security/atlas_red_team.py --all --json` re-checks that the defensive tooling is present.

**Your task:** Answer the reflection questions.
