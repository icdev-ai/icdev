---
ontology_id: icdev:mission:m-issm-02-aadc-ato-design:step:1
step_class: icdev:Lesson
---

# Design a Compliant Agentic System for ATO

Your ATO package just got a new requirement: any AI system deployed must pass OWASP LLM Top 10.
The STIG auditor wants to see the architecture diagram and the compliance assessment before the ATO decision.

In this mission you'll design an agentic system on the AADC canvas and harden it until it passes
the six OWASP LLM checks that ATO reviewers focus on most.

## The target: a RAG-powered document analysis agent

You'll build this architecture:

```
inference-input → [input-sanitizer] → llm → [output-validator] → external-api
                                       │
                                    vector-db
```

Nodes in brackets are the ones you need to add.

## The 6 OWASP LLM checks for ATO review

| Check ID | OWASP | What It Requires |
|---|---|---|
| llm01 | Prompt Injection | `input-sanitizer` upstream of every LLM node |
| llm02 | Insecure Output Handling | `output-validator` downstream of every LLM node |
| llm04 | Model DoS | `token-budget` or `rate-limiter` present |
| llm06 | Sensitive Info Disclosure | Both `pii-detector` AND `redaction-engine` present |
| llm08 | Excessive Agency | Every `autonomous-agent` has a `circuit-breaker` |
| llm10 | Model Theft | `audit-logger` present |

## Step-by-step

1. **Open the AADC canvas** — start with the base RAG design
2. **Add `input-sanitizer`** — connect it between inference-input and the LLM
3. **Add `output-validator`** — connect it after the LLM
4. **Add `pii-detector` and `redaction-engine`** — connect pii-detector before the LLM output path, redaction-engine after
5. **Add `token-budget`** — place it alongside the LLM to enforce request limits
6. **Add `audit-logger`** — connect it to the output path for the model theft check
7. **Run assessment** — all 6 OWASP checks above must pass
8. **Export OSCAL** — in the Artifacts panel, generate the OSCAL export. This becomes your ATO evidence artifact
9. **Save and submit** — paste your design ID in the submission box

## Why this matters for ATO

The ATO reviewer will look at:
1. Does the architecture diagram show safety controls?
2. Can you demonstrate the controls work (assessment score)?
3. Is there a machine-readable compliance artifact (OSCAL)?

Your AADC design answers all three questions automatically.

## Success criteria

- OWASP checks `llm01`, `llm02`, `llm04`, `llm06`, `llm08`, `llm10` all pass
- Overall design score ≥ 75
- OSCAL artifact generated (visible in the Artifacts tab)
