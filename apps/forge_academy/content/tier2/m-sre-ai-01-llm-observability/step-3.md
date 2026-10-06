---
ontology_id: icdev:mission:m-sre-ai-01-llm-observability:step:3
step_class: icdev:Assessment
---

<!-- CUI // SP-CTI -->

# Observability Runbook

Instrumentation is only useful if you know what to do when the signals fire. This step defines the four primary runbook scenarios for LLM observability events, the NIST audit requirement, and how ICDEV handles the audit trail automatically.

## Runbook Scenario 1: `check_budget()` Returns `action: 'block'`

**What it means:** The agent has spent its full monthly budget and `hard_stop` is on.

**Response: circuit breaker pattern**

```python
from tools.agent.token_tracker import check_budget, BudgetExceededError

def safe_invoke(agent_id, prompt, fallback_response=None):
    budget = check_budget(agent_id)
    if budget["action"] == "block":
        # Option A: Return cached last response
        cached = get_cached_response(prompt)          # your cache lookup
        if cached:
            return {"output": cached, "source": "cache", "degraded": True}
        # Option B: Graceful degradation: return static fallback
        if fallback_response:
            return {"output": fallback_response, "source": "fallback", "degraded": True}
        # Option C: Fail visibly rather than silently drop the request
        raise BudgetExceededError(agent_id, budget)
```

`LLMRouter.invoke()` raises the same `BudgetExceededError` on its own when the request carries an `agent_id`, so callers of the router need only the `except` branch.

Never silently drop requests. Always surface the degraded state to the caller so it can inform the user.

## Runbook Scenario 2: P99 Latency Spike

**What it means:** The 99th percentile response time has risen significantly from baseline (e.g., 800ms → 14s).

**Diagnosis steps:**

1. Check if `output_tokens` increased for the same function. Token inflation from prompt changes is the #1 cause of latency spikes.
2. Compare `avg(output_tokens)` in the last 2 hours vs. the prior 24-hour baseline.
3. If token count is stable, check Ollama process health: `curl http://localhost:11434/api/ps`
4. If cloud model: check provider status page and rate-limit headers in recent responses.

**Resolution:** If token inflation is confirmed, audit recent prompt template changes. Trim context window. Roll back prompt if quality score is unaffected.

## Runbook Scenario 3: Error Rate Above 5%

**Triage matrix:**

| Error Type | HTTP Code | Likely Cause | Action |
|---|---|---|---|
| Rate limit | 429 | Too many concurrent agents | Implement exponential backoff; reduce concurrency |
| Model error | 500 | Provider issue or malformed prompt | Check provider status; simplify prompt |
| Timeout | N/A | Token inflation or provider slowness | Reduce `max_tokens`; add timeout + retry |
| Context overflow | 400 | Prompt exceeds context window | Truncate history; use sliding window |

```python
import time, requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Exponential backoff with jitter for 429s
session = requests.Session()
retry = Retry(
    total=3,
    backoff_factor=1.5,
    status_forcelist=[429, 500, 502, 503],
)
session.mount("http://", HTTPAdapter(max_retries=retry))
```

## Runbook Scenario 4: Quality Score Drops Below 0.7

**What it means:** The model's output quality has degraded. Could indicate prompt regression, model update, or data drift.

**Steps:**

1. Run `detect_drift(model_id, function_name)` from `tools/llm/model_monitor.py` to confirm statistical significance.
2. Check if a prompt template was changed recently (`git log --oneline -- hardprompts/`).
3. If drift is confirmed, trigger the drift response protocol (covered in Mission SRE-AI-02).
4. Severity comes from `detect_drift()` itself. A quality drop above 10% (and statistically significant, Welch t-test p < 0.05) is `warning`; above 25% it is `critical`.

## NIST AU-2: LLM Audit Trail Requirement

NIST AU-2 requires that audit-relevant events be identified and logged. For AI systems, every LLM interaction is an audit event. ICDEV handles this automatically. `LLMRouter` calls `AITelemetryLogger.log_ai_interaction()` (`tools/security/ai_telemetry_logger.py`) after every invocation:

```python
from tools.security.ai_telemetry_logger import AITelemetryLogger

# Called for you by LLMRouter. Shown here to make the record visible.
AITelemetryLogger().log_ai_interaction(
    model_id="qwen3-local", provider="ollama",
    prompt_hash=prompt_hash, response_hash=response_hash,   # SHA-256 digests
    input_tokens=812, output_tokens=164, latency_ms=1240,
    agent_id="my-agent", function="summarize", classification="CUI",
)
# Writes to: ai_telemetry (append-only; listed in APPEND_ONLY_TABLES)
```

`prompt_hash` and `response_hash` hold SHA-256 digests, not the raw content, so CUI-marked prompts are never stored in plaintext in the audit log.

## Quick-Reference Summary

| Signal | Threshold | Action |
|---|---|---|
| `check_budget()` action `'warn'` | ≥80% budget used (`warning_threshold`) | Alert SRE; review top callers |
| `check_budget()` action `'block'` | 100% budget used, `hard_stop: true` | Circuit-break; degrade gracefully |
| P99 latency | >2x baseline | Check token inflation; check provider |
| Error rate | >5% | Triage by error type (table above) |
| Quality score | <0.7 | Run detect_drift(); check prompt history |

**Your task:** Answer the reflection questions.
