---
ontology_id: icdev:mission:m-sre-ai-04-incident-response:step:1
step_class: icdev:Lesson
---

<!-- CUI // SP-CTI -->

# AI Incident Types and Triage

AI incidents don't fit neatly into traditional incident management. A "500 Internal Server Error" is easy to detect and page on. A model that has confidently produced wrong answers for three days is not: it raises zero infrastructure errors while causing serious harm. You need AI-specific incident types, severity rules and triage logic.

## The 5 AI-Specific Incident Types

### Type 1: Hallucination Surge

**Signals:** quality scores fall below about 0.6; users report factually wrong outputs; `model_drift_events` shows a critical `quality_degradation` event.

**Why it's hard to detect:** the model still returns HTTP 200, and latency and token counts may look normal. Only a quality evaluator catches it.

**Who handles it:** a human. No automated resolver can decide whether output is factually wrong without domain context.

### Type 2: Prompt Injection Attack

**Signals:** `tools/security/prompt_injection_detector.py` flags inputs, and the router records the result in `ai_telemetry.injection_scan_result`. `AITelemetryLogger.detect_anomalies()` reports an `injection_attempts` anomaly. Unusual tool calls appear in agent traces, and system-prompt fragments show up in user-visible output.

**Why it's dangerous:** successful injection can make an agent issue unauthorized API calls, exfiltrate CUI, or run destructive database operations.

**Who handles it:** a human, with security. Contain first (revoke the session or key, disable the agent), then investigate. Confirmed exfiltration is a `data_breach` incident.

### Type 3: Model Drift

**Signals:** gradual quality degradation over days or weeks; `detect_drift()` records `warning` or `critical` events; user complaints slowly rising.

**Why it's hard to detect:** no single call looks wrong. The degradation is statistical, and without monitoring it can persist for weeks.

**Who handles it:** on-call SRE, following the drift protocol from Mission SRE-AI-02. Retrain, roll the prompt back, or route the function to its previous model. A production model swap needs human approval.

### Type 4: Cost Runaway

**Signals:** `check_budget()` returns `action: 'block'` for one or more agents; `detect_cost_anomalies()` reports a `critical` spike (recent spend more than 5x the 7-day baseline).

**Why it's dangerous:** an agent-loop bug or misconfigured retry can generate tens of thousands of API calls before anyone notices.

**Who handles it:** budgets contain it automatically (`hard_stop` blocks further calls), but a human must find the loop or misconfiguration.

### Type 5: Context Window Overflow

**Signals:** HTTP 400 errors with token-limit messages; conversation history growing without bound; requests failing with "prompt too long".

**Why it happens:** agents that append full conversation history with no compression or truncation strategy eventually hit the context limit.

**Who handles it:** the owning team, by adding a history window or summarizer. It is a code fix, not a runtime toggle.

## Severity Classification

| Incident Type | User Impact | Default Severity | Automatic containment |
|---|---|---|---|
| Hallucination Surge | Wrong outputs reaching users | High | None: human judgment |
| Prompt Injection | Security breach possible | Critical | Detection only |
| Model Drift | Gradual quality degradation | Medium → High | Detection + `alert` event |
| Cost Runaway | Budget blocked, agents down | High | Budget `hard_stop` |
| Context Window Overflow | Request failures | Medium | None: code fix |

## Where ICDEV records and routes AI incidents

- **AI incident log.** `tools/compliance/ai_incident_response.py` writes `log_incident(project_id, incident_type, description, ai_system=..., severity=...)` to `ai_incident_log`. The incident types are `confabulation`, `bias_detected`, `unauthorized_access`, `model_drift`, `data_breach`, `safety_violation`, `appeal_escalation` and `other`; severity is `critical`, `high`, `medium` or `low`.
- **Auto-resolver.** `tools/monitor/auto_resolver.py` is ICDEV's general alert auto-resolver. It takes an alert from Sentry, Prometheus, ELK or a generic payload, normalizes it, and matches it against known `knowledge_patterns`. It acts **only** when confidence is at least 0.7 **and** the matched pattern is marked auto-healable. It has no built-in knowledge of AI incident types: it can help only where your knowledge base holds a matching pattern. That is why Types 1 and 2 stay with humans.

**Your task:** In the next step, configure your runbook.
