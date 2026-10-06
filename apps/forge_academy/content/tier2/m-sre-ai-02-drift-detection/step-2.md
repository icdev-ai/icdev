---
ontology_id: icdev:mission:m-sre-ai-02-drift-detection:step:2
step_class: icdev:Lesson
---

<!-- CUI // SP-CTI -->

# Configure Drift Thresholds with model_monitor.py

Now that you understand the drift types, this step walks the `model_monitor.py` API: recording scores, reading the baseline, interpreting `detect_drift()` output, and choosing your own thresholds.

## Core API Reference

### `record_quality_score()`

Call it after every LLM response to feed the drift pipeline:

```python
from tools.llm.model_monitor import record_quality_score

record_quality_score(
    model_id="qwen3-local",          # model identifier
    function_name="code_generation", # logical function name
    score=0.81,                      # 0.0-1.0 quality score from your evaluator
    response_time_ms=1430,           # end-to-end latency
    token_count=487,                 # output token count
    method="automated",              # automated | human | judge
)
```

This writes a row to `model_quality_scores`. Note that this table is **not** append-only: `reset_baseline()` deletes old rows from it, as covered in Step 3.

### `get_baseline()`

Returns the baseline statistics for a model/function pair. The baseline is the first `baseline_days` (default 30) of recorded data:

```python
from tools.llm.model_monitor import get_baseline

baseline = get_baseline(model_id="qwen3-local", function_name="code_generation")
# {
#   "status": "ok",
#   "model_id": "qwen3-local",
#   "function_name": "code_generation",
#   "sample_count": 1200,
#   "baseline_cutoff": "2026-05-01T...",
#   "quality":    {"mean": 0.84, "stdev": 0.05, "p50": 0.85, ...},
#   "latency_ms": {"mean": 1310.0, "p95": 1980.0, "p99": 2100.0},
#   "tokens":     {"mean": 412.0, "stdev": 61.0}
# }
```

### `detect_drift()`

```python
from tools.llm.model_monitor import detect_drift

events = detect_drift(model_id="qwen3-local", function_name="code_generation",
                      window_days=7, baseline_days=30)
# list of drift-event dicts (empty list = no drift)
```

### `trigger_retrain()`

```python
from tools.llm.model_monitor import trigger_retrain

trigger_retrain(
    model_id="qwen3-local",
    function_name="code_generation",
    reason="quality_degradation: 31.2% below baseline over 7 days",
)
# Records a critical model_drift_events row with action_taken='retrain_triggered'.
# The fine-tuning pipeline itself is tools/finetune/ (see Mission DataOps-05).
```

### `get_drift_history()`

```python
from tools.llm.model_monitor import get_drift_history

history = get_drift_history(limit=50, model_id="qwen3-local")
# newest first, ordered by created_at DESC
```

## CLI Usage

```bash
# Drift check for one model/function pair
python tools/llm/model_monitor.py --detect-drift --model qwen3-local --function summarize --window-days 7 --json

# Baseline statistics
python tools/llm/model_monitor.py --baseline --model qwen3-local --function summarize --json

# Recent drift events
python tools/llm/model_monitor.py --drift-history --model qwen3-local --limit 20 --json

# CI/heartbeat gate: exit 1 if a critical drift event was recorded in the last 24h
python tools/llm/model_monitor.py --gate
```

## Where the thresholds live

Today the thresholds are constants in `detect_drift()`. There is no YAML block for them, so changing them is a code change that goes through review:

| Drift type | Warning | Critical | Suggested follow-up |
|---|---|---|---|
| `quality_degradation` | > 10% mean drop | > 25% mean drop | `trigger_retrain()` or roll back the prompt/model |
| `latency_increase` | > 25% P95 rise | > 50% P95 rise | Check token inflation, then the provider |
| `token_inflation` | > 20% mean rise | > 40% mean rise | Audit prompt templates and history handling |
| `availability_drop` | not computed by `detect_drift()` | — | Track through error-rate SLOs |

Every finding must also be statistically significant (p < 0.05), so a handful of bad responses will not page anyone.

In the form, record the thresholds **you** would want for your own model and function. If they differ from the defaults, say why: a revenue-critical extraction function deserves tighter limits than a brainstorming helper.

**Your task:** Answer the configuration questions.
