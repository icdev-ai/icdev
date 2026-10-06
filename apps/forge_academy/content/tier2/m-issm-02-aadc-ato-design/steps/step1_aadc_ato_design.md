---
ontology_id: icdev:mission:m-issm-02-aadc-ato-design:step:1
step_class: icdev:Lesson
---

# Design a Compliant Agentic System for ATO

Your ATO package just got a new requirement: any AI system deployed must pass OWASP LLM Top 10.
The STIG auditor wants to see the architecture diagram and the compliance assessment before the ATO decision.

In this mission you'll design an agentic system on the **Agentic AI Design Canvas (AADC)** and
harden it until it passes the six OWASP LLM checks that ATO reviewers focus on most.

## The target: a RAG-powered document analysis agent

Open a new canvas at **`/agentic-ai/canvas`** (it starts empty) and build this base design from the
palette:

```
inference-input → llm → external-api
                   ↑
               vector-db
```

Then harden it so it looks like this. The nodes in brackets are the ones you add:

```
inference-input → [input-sanitizer] → llm → [confidence-threshold] → [output-validator] → external-api
                                       ↑                │
                                   vector-db       [hitl-gate]
```

## The 6 OWASP LLM checks for ATO review

| Check ID | OWASP | What It Requires |
|---|---|---|
| llm01 | Prompt Injection | `input-sanitizer` upstream of every LLM node |
| llm02 | Insecure Output Handling | `output-validator` downstream of every LLM node |
| llm04 | Model DoS | `token-budget` or `rate-limiter` present |
| llm06 | Sensitive Info Disclosure | Both `pii-detector` AND `redaction-engine` present |
| llm08 | Excessive Agency | Every `autonomous-agent` has a `circuit-breaker` |
| llm10 | Model Theft | `audit-logger` present |

(These IDs follow the 2023 v1.1 OWASP numbering that the canvas uses. The 2025 list renumbers
several of them. See the SecOps-AI missions.)

## Step-by-step

1. **Build the base design** above on `/agentic-ai/canvas`
2. **Add `input-sanitizer`** and connect it directly between inference-input and the LLM (`llm01` checks for an input-sanitizer immediately upstream of every LLM)
3. **Add `output-validator`** downstream of the LLM (`llm02`)
4. **Add `pii-detector` and `redaction-engine`**. Connect llm → pii-detector → redaction-engine → output-validator (`llm06` needs both present)
5. **Add `token-budget`** anywhere in the design to enforce request limits (`llm04`)
6. **Add `audit-logger`** after the output-validator (`llm10`)
7. **Raise the overall score**. The six OWASP checks alone leave the design at about 50, because the NIST AI RMF checks still fail. Add a `confidence-threshold` between the LLM and the output-validator, with a `hitl-gate` branching off it (low-confidence answers go to a human). Add a `model-registry` connected into the LLM (supply chain, `llm05`) and a `drift-detector` anywhere in the design
8. **Save**, then click **Assess**. All six OWASP checks must pass, and the score should read about 76
9. **Export OSCAL**: from the toolbar menu choose **OSCAL Export**. That file is your machine-readable ATO evidence artifact
10. Note your design ID (in the URL, `/agentic-ai/canvas/<design_id>`). You can also open **Artifacts** for this design

## Why this matters for ATO

The ATO reviewer will look at:
1. Does the architecture diagram show safety controls?
2. Can you demonstrate the controls work (assessment score)?
3. Is there a machine-readable compliance artifact (OSCAL)?

Your AADC design answers all three questions automatically.

## Success criteria

- OWASP checks `llm01`, `llm02`, `llm04`, `llm06`, `llm08`, `llm10` all pass
- Overall design score ≥ 75
- OSCAL export generated

When your assessment is green, come back and click **Understood → Continue**.
