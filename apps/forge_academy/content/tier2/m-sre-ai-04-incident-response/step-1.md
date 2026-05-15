---
ontology_id: icdev:mission:m-sre-ai-04-incident-response:step:1
step_class: icdev:Lesson
---

<!-- CUI // SP-CTI -->

# AI Incident Types and Triage

AI incidents don't fit neatly into traditional incident management frameworks. A "500 Internal Server Error" is easy to detect and page on. A model that is confidently producing wrong answers for three days is not — it takes zero infrastructure errors while causing serious harm. You need AI-specific incident types, severity classifications, and different triage logic.

## The 5 AI-Specific Incident Types

### Type 1: Hallucination Surge

**Signals:** Quality scores drop below 0.6; users report factually wrong outputs; `model_drift_events` shows `quality_degradation` critical event.

**Why it's hard to detect:** The model still returns HTTP 200. Latency may be normal. Token counts may be normal. Only a quality evaluator catches this.

**Who handles it:** Human judgment required. Auto-resolver cannot determine whether output is factually wrong without domain context.

### Type 2: Prompt Injection Attack

**Signals:** `ai_telemetry_logger` fires `security_anomaly` events; unusual tool calls appear in agent logs; system prompt fragments appear in user-visible output.

**Why it's dangerous:** Successful prompt injection can cause an agent to make unauthorized API calls, exfiltrate CUI, or execute destructive database operations.

**Who handles it:** `auto_resolver.py` handles at confidence ≥0.7 (block the offending session, reset agent context). Human review required for confirmed exfiltration.

### Type 3: Model Drift

**Signals:** Gradual quality degradation over days/weeks; `detect_drift()` fires `warning` or `critical` events; user complaint rate slowly rising.

**Why it's hard to detect:** No single call looks wrong. The degradation is statistical. Without monitoring, it can persist for weeks.

**Who handles it:** `auto_resolver.py` handles at confidence ≥0.7 (trigger retrain or model swap). Human approval required for production model swap.

### Type 4: Cost Runaway

**Signals:** `check_budget()` returns `'block'` for multiple agents; `detect_cost_anomalies()` returns critical `spike` or `agent_loop_runaway` anomaly; daily spend 5x above baseline.

**Why it's dangerous:** An agent loop bug or mis-configured retry logic can generate tens of thousands of API calls before anyone notices.

**Who handles it:** Human judgment required. Auto-resolver will block the agent, but root cause analysis (finding the loop or misconfiguration) requires human review.

### Type 5: Context Window Overflow

**Signals:** HTTP 400 errors with token limit messages; `conversation_history` growing unbounded; requests failing with "prompt too long."

**Why it happens:** Agents that append full conversation history without a compression or truncation strategy hit the context window limit as conversations grow.

**Who handles it:** `auto_resolver.py` handles automatically (truncate history to last N turns).

## Severity Classification

| Incident Type | User Impact | Default Severity | Auto-Resolvable |
|---|---|---|---|
| Hallucination Surge | Wrong outputs reaching users | High | No |
| Prompt Injection | Security breach possible | Critical | Partial |
| Model Drift | Gradual quality degradation | Medium → High | Yes (≥0.7 confidence) |
| Cost Runaway | Budget blocked, agents down | High | No (block only) |
| Context Window Overflow | Request failures | Medium | Yes |

## ICDEV Auto-Resolver Coverage

`tools/ai_ops/auto_resolver.py` handles Types 2, 3, and 5 automatically when resolution confidence is ≥0.7. Types 1 and 4 require human judgment because:

- **Type 1 (Hallucination Surge):** Determining whether an output is factually wrong requires domain expertise the resolver doesn't have.
- **Type 4 (Cost Runaway):** The resolver can block agents, but identifying the root bug (loop, misconfiguration, attack) needs human investigation.

For all types, `normalize_alert()` structures the raw signal into a standard alert dict, and `analyze_alert()` produces resolution candidates with confidence scores regardless of whether auto-resolution is possible.

**Your task:** In the next step, configure your runbook.
