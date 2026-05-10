<!-- CUI // SP-CTI -->

# Configure Drift Thresholds with model_monitor.py

Now that you understand the four drift types, this step covers the full `model_monitor.py` API, how to set and retrieve baselines, how to interpret `detect_drift()` output, and how to configure thresholds in `args/llm_config.yaml`.

## Core API Reference

### `record_quality_score()`

Called after every LLM response to feed the drift detection pipeline:

```python
from tools.llm.model_monitor import record_quality_score

record_quality_score(
    model_id="qwen3-local",          # model identifier
    function_name="code_generation", # logical function name
    score=0.81,                      # 0.0–1.0 quality score from your evaluator
    response_time_ms=1430.0,         # end-to-end latency
    token_count=487,                 # output token count
)
```

This writes a row to `model_quality_scores`. The table is append-only.

### `get_baseline()`

Retrieves the stored performance baseline for a model/function pair:

```python
from tools.llm.model_monitor import get_baseline

baseline = get_baseline(model_id="qwen3-local", function_name="code_generation")
# Returns:
# {
#   "model_id": "qwen3-local",
#   "function_name": "code_generation",
#   "baseline_quality": 0.84,
#   "baseline_latency_p99_ms": 2100.0,
#   "baseline_token_count_avg": 412.0,
#   "baseline_success_rate": 0.997,
#   "established_at": "2026-04-01T00:00:00Z",
#   "sample_count": 1200
# }
```

### `detect_drift()`

Runs drift detection and returns the full assessment:

```python
from tools.llm.model_monitor import detect_drift

result = detect_drift(
    model_id="qwen3-local",
    function_name="code_generation",
)
# Returns dict: drift_detected, drift_type, baseline_value,
# current_value, deviation_pct, severity, action_taken, event_id
```

### `trigger_retrain()`

Called automatically for `critical` drift events, or manually:

```python
from tools.llm.model_monitor import trigger_retrain

trigger_retrain(
    model_id="qwen3-local",
    reason="quality_degradation: 31.2% deviation over 24h",
)
# Writes a retraining request to model_retrain_queue
# For Ollama local models: triggers fine-tune job if configured
```

### `get_drift_history()`

Retrieve recent drift events for a model:

```python
from tools.llm.model_monitor import get_drift_history

history = get_drift_history(model_id="qwen3-local", hours=24)
# Returns list of drift event dicts, ordered by detected_at DESC
```

## CLI Usage

```bash
# Check drift status for a specific model/function pair
python tools/llm/model_monitor.py --model qwen3-local --function summarize --check

# View drift history (last 48 hours)
python tools/llm/model_monitor.py --model qwen3-local --history --hours 48

# Manually establish a new baseline (use with caution — see Step 3)
python tools/llm/model_monitor.py --model qwen3-local --function summarize --set-baseline
```

## Configuring Thresholds in `args/llm_config.yaml`

```yaml
drift_detection:
  enabled: true
  window_hours: 24
  thresholds:
    info:
      deviation_pct: 5.0
    warning:
      deviation_pct: 15.0
    critical:
      deviation_pct: 30.0
  per_function_overrides:
    # Tighter threshold for revenue-critical functions
    payment_extraction:
      warning_pct: 8.0
      critical_pct: 15.0
    # Looser threshold for exploratory functions
    brainstorm:
      warning_pct: 25.0
      critical_pct: 50.0
```

## Drift Type → Threshold → Action Reference

| Drift Type | Info Threshold | Warning Threshold | Critical Threshold | Critical Action |
|---|---|---|---|---|
| `quality_degradation` | 5% score drop | 15% score drop | 30% score drop | `retrain_triggered` |
| `latency_increase` | 5% P99 rise | 15% P99 rise | 30% P99 rise | `alert` |
| `token_inflation` | 5% token avg rise | 15% token avg rise | 30% token avg rise | `alert` |
| `availability_drop` | 1% success rate drop | 3% drop | 5% drop | `model_swapped` |

Availability thresholds are intentionally tighter than quality thresholds — a 5% availability drop can block hundreds of users while a 5% quality drop may be imperceptible.

**Your task:** Answer the configuration questions.
