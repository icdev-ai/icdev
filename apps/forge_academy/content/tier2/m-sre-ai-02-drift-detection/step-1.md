---
ontology_id: icdev:mission:m-sre-ai-02-drift-detection:step:1
step_class: icdev:Lesson
---

<!-- CUI // SP-CTI -->

# AI Drift — 4 Types You Must Monitor

Model drift is the silent production killer for AI systems. Unlike a server crash, drift is gradual. A model that scored 0.85 quality six months ago may score 0.61 today, and your users have been getting degraded outputs the entire time. Standard health checks won't catch it; you need dedicated drift monitoring.

## The 4 Drift Types in `model_drift_events`

ICDEV's drift monitor is `tools/llm/model_monitor.py`. It records every drift finding in the append-only `model_drift_events` table in the ICDEV database (PostgreSQL by default, SQLite as the fallback). The `drift_type` column allows four canonical values:

### 1. `quality_degradation`

Output quality score drops from its established baseline. Causes: a model update on the provider side, prompt template changes, input distribution shift (users asking new kinds of questions), or RAG corpus degradation. This is the most dangerous type because it directly erodes user trust.

**Detection signal:** mean quality score of the recent window vs. the baseline mean.

### 2. `latency_increase`

Response time rises beyond the acceptable threshold. Causes: token inflation (prompts growing longer over time), provider infrastructure issues, context accumulating in multi-turn conversations. Latency increase often comes before quality degradation: a model struggling to respond quickly is often struggling to respond well.

**Detection signal:** P95 latency of the recent window vs. baseline P95.

### 3. `token_inflation`

Average token count per call grows over time for the same kind of work. Causes: system prompt additions, conversation history accumulation, more verbose model behaviour after a provider update. Token inflation directly drives cost and is invisible without per-function tracking.

**Detection signal:** mean `token_count` of the recent window vs. baseline mean.

### 4. `availability_drop`

Success rate (non-error responses / total requests) falls. Causes: rate limiting, provider outages, misconfigured timeouts, upstream dependency failures. Unlike the others it is usually acute rather than gradual. The table accepts this type, but `detect_drift()` does **not** compute it today: it only sees scored responses, never failed calls. Watch availability through error rates and SLOs (Mission SRE-AI-01 and SRE-AI-04).

## Severity Levels — what `detect_drift()` actually applies

A deviation is reported only when it is also statistically significant (Welch's t-test, p < 0.05). The thresholds are fixed in `model_monitor.py`:

| Drift type | `warning` when | `critical` when |
|---|---|---|
| `quality_degradation` | mean drops > 10% | mean drops > 25% |
| `latency_increase` | P95 rises > 25% | P95 rises > 50% |
| `token_inflation` | mean rises > 20% | mean rises > 40% |

There is no `info` tier in practice. The `severity` column allows `info`, but `detect_drift()` never writes it. The `action_taken` column accepts `none`, `alert`, `retrain_triggered` or `model_swapped`. `detect_drift()` records `alert`, and `trigger_retrain()` records `retrain_triggered`.

## How `detect_drift()` Works

```python
from tools.llm.model_monitor import record_quality_score, detect_drift

# Record a data point after each LLM call
record_quality_score(
    model_id="qwen3-local",
    function_name="summarize",
    score=0.74,
    response_time_ms=1240,
    token_count=312,
)

# Compare the last 7 days against the baseline
events = detect_drift(model_id="qwen3-local", function_name="summarize")
```

`detect_drift(model_id=None, function_name=None, window_days=7, baseline_days=30)`:
1. Finds every `(model_id, function_name)` pair in `model_quality_scores` (filtered by the arguments you pass).
2. Takes the **baseline**: the first `baseline_days` of recorded data for that pair (`get_baseline()`). At least 5 samples are needed.
3. Takes the **recent window**: the last `window_days` of data. At least 3 samples are needed.
4. Compares quality, P95 latency and token count, applying the thresholds above plus the t-test.
5. Writes each finding to `model_drift_events` and returns a **list** of event dicts. The list is empty when nothing drifted.

## Sample Return Value

```python
[
  {
    "id": "...",
    "model_id": "qwen3-local",
    "function_name": "summarize",
    "drift_type": "quality_degradation",
    "baseline_value": 0.83,
    "current_value": 0.61,
    "deviation_pct": 26.51,
    "severity": "critical",          # > 25% drop
    "action_taken": "alert",
    "window_start": "...", "window_end": "...", "created_at": "..."
  }
]
```

## CLI Quick-Check

```bash
python tools/llm/model_monitor.py --detect-drift --model qwen3-local --function summarize --json
python tools/llm/model_monitor.py --health --json        # per-model health summary
```

**Your task:** In the next step, configure your thresholds.
