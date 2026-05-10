<!-- CUI // SP-CTI -->

# AI Drift — 4 Types You Must Monitor

Model drift is the silent production killer for AI systems. Unlike a server crash, drift is gradual. A model that scored 0.85 quality six months ago may be scoring 0.61 today — and your users have been getting degraded outputs the entire time. Standard health checks won't catch it. You need dedicated drift monitoring.

## The 4 Drift Types in `model_drift_events`

ICDEV tracks drift via the `model_drift_events` table in `data/icdev.db`. The `drift_type` column uses four canonical values:

### 1. `quality_degradation`

Output quality score drops from its established baseline. Causes: model update on the provider side, prompt template changes, input distribution shift (users asking new types of questions), or RAG corpus degradation. This is the most dangerous type because it directly impacts user trust.

**Detection signal:** Rolling 24h average quality score vs. 30-day baseline.

### 2. `latency_increase`

P99 response time rises beyond the acceptable threshold. Causes: token inflation (prompts growing longer over time), provider infrastructure issues, context window accumulation in multi-turn conversations. Latency increase often precedes quality degradation — a model struggling to respond quickly is often struggling to respond well.

**Detection signal:** P99 latency ratio: `current_p99 / baseline_p99 > threshold`.

### 3. `token_inflation`

Average token count per call grows over time for the same set of prompts. Causes: system prompt additions, conversation history accumulation, verbose model behavior after provider updates. Token inflation directly drives cost increases and is often invisible without per-function tracking.

**Detection signal:** Rolling average `output_tokens` per function vs. 30-day baseline.

### 4. `availability_drop`

Success rate (non-error responses / total requests) falls. Causes: rate limiting, provider outages, misconfigured timeouts, upstream dependency failures. Unlike the other drift types, availability drop is usually acute rather than gradual.

**Detection signal:** 1h rolling success rate vs. 30-day baseline.

## Severity Levels

| Deviation from Baseline | Severity | Default Action |
|---|---|---|
| > 5% | `info` | Log only |
| > 15% | `warning` | Alert on-call; increase monitoring cadence |
| > 30% | `critical` | Alert + auto-remediate (retrain or swap) |

The `action_taken` column records: `none`, `alert`, `retrain_triggered`, or `model_swapped`.

## How `detect_drift()` Works

```python
from tools.llm.model_monitor import record_quality_score, detect_drift

# Record a data point after each LLM call
record_quality_score(
    model_id="qwen3-local",
    function_name="summarize",
    score=0.74,
    response_time_ms=1240.0,
    token_count=312,
)

# Run drift detection — computes rolling 24h window vs stored baseline
result = detect_drift(model_id="qwen3-local", function_name="summarize")
```

The `detect_drift()` function:
1. Queries `model_quality_scores` for all records in the past 24 hours for the given `(model_id, function_name)` pair.
2. Retrieves the stored baseline from `model_baselines`.
3. Computes deviation percentage for quality, latency, and token count.
4. If deviation exceeds a severity threshold, writes a record to `model_drift_events`.
5. Returns a dict with the full drift assessment.

## Sample Return Value

```python
{
    "model_id": "qwen3-local",
    "function_name": "summarize",
    "drift_detected": True,
    "drift_type": "quality_degradation",
    "baseline_value": 0.83,
    "current_value": 0.61,
    "deviation_pct": 26.5,
    "severity": "warning",
    "action_taken": "alert",
    "event_id": "dft_20260509_0047a3"
}
```

## CLI Quick-Check

```bash
python tools/llm/model_monitor.py --model qwen3-local --function summarize --check
# Output:
# [qwen3-local/summarize] Last 24h quality avg: 0.61 (baseline: 0.83)
# Deviation: 26.5% — severity: WARNING
```

**Your task:** In the next step, configure your thresholds.
