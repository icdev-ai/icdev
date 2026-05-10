<!-- CUI // SP-CTI -->

# Build Your AI Runbook with auto_resolver.py

The `auto_resolver.py` tool is the first responder for AI incidents. It normalizes raw alerts into structured objects, analyzes them for resolution candidates, and executes resolutions when confidence is high enough. This step covers the full API and the model rollback pattern.

## `auto_resolver.py` API Reference

### `normalize_alert(raw_alert)`

Converts a raw alert dict (from monitoring, drift detector, or manual input) into a structured alert:

```python
from tools.ai_ops.auto_resolver import normalize_alert

raw = {
    "source": "drift_detector",
    "model_id": "qwen3-local",
    "function_name": "summarize",
    "drift_type": "quality_degradation",
    "deviation_pct": 31.2,
    "severity": "critical",
    "detected_at": "2026-05-09T14:32:00Z",
}

alert = normalize_alert(raw)
# Returns:
# {
#   "alert_id": "alr_20260509_a7f3c1",
#   "incident_type": "model_drift",
#   "severity": "critical",
#   "model_id": "qwen3-local",
#   "function_name": "summarize",
#   "metadata": {...},
#   "status": "open"
# }
```

### `analyze_alert(alert)`

Produces a ranked list of resolution candidates with confidence scores:

```python
from tools.ai_ops.auto_resolver import analyze_alert

candidates = analyze_alert(alert)
# Returns:
# [
#   {
#     "resolution_type": "model_rollback",
#     "confidence": 0.82,
#     "action": "swap_to_previous_stable",
#     "previous_stable_model": "qwen3-local-v1.2",
#     "estimated_recovery_time_min": 2
#   },
#   {
#     "resolution_type": "retrain_trigger",
#     "confidence": 0.61,
#     "action": "queue_retrain_job",
#     "estimated_recovery_time_min": 120
#   }
# ]
```

### `resolve_alert(alert_id)`

Executes the highest-confidence resolution if `confidence >= 0.7`, otherwise escalates to human:

```python
from tools.ai_ops.auto_resolver import resolve_alert

result = resolve_alert(alert_id="alr_20260509_a7f3c1")
# If confidence >= 0.7:
# {"status": "resolved", "resolution_type": "model_rollback", "applied_at": "..."}
#
# If confidence < 0.7:
# {"status": "escalated", "reason": "confidence=0.61 below threshold",
#  "oncall_notified": True}
```

### `get_resolution_history(hours=24)`

```python
from tools.ai_ops.auto_resolver import get_resolution_history

history = get_resolution_history(hours=24)
# Returns list of past resolution dicts with timestamps, types, and outcomes
```

## Full Model Rollback Pattern

When `detect_drift()` returns a critical event and the resolver recommends `model_rollback`:

```python
from tools.llm.model_monitor import detect_drift, reset_baseline
from tools.llm.model_registry import get_previous_stable
from tools.ai_ops.auto_resolver import normalize_alert, resolve_alert
import yaml
from pathlib import Path

def handle_critical_drift(model_id: str, function_name: str):
    # 1. Detect drift
    drift = detect_drift(model_id=model_id, function_name=function_name)
    if drift["severity"] != "critical":
        return

    # 2. Get the last known-good model version
    previous_stable = get_previous_stable(model_id=model_id)
    # Returns: {"model_id": "qwen3-local-v1.2", "quality_avg": 0.84, "validated_at": "..."}

    # 3. Swap model in llm_config.yaml
    config_path = Path("args/llm_config.yaml")
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["two_tier"]["edge"]["primary_model"] = previous_stable["model_id"]
    config_path.write_text(yaml.dump(config), encoding="utf-8")

    # 4. Normalize and resolve via auto_resolver
    alert = normalize_alert({
        "source": "manual_rollback",
        "model_id": model_id,
        "function_name": function_name,
        "drift_type": drift["drift_type"],
        "severity": "critical",
        "resolution_hint": "model_rollback",
    })
    result = resolve_alert(alert["alert_id"])

    # 5. Do NOT reset baseline yet — wait for 48h stable window
    print(f"Rollback complete. Monitoring for 48h before baseline reset.")
    return result
```

## Escalation Path for Low-Confidence Incidents

When `analyze_alert()` returns `confidence < 0.3`:

1. `auto_resolver.py` writes the alert to `ai_incident_log` with `status='escalated'`.
2. On-call engineer receives full alert context including all candidate resolutions and their confidence scores.
3. Engineer selects the resolution manually: `resolve_alert(alert_id, override_resolution_type='retrain_trigger')`.
4. Engineer documents root cause in the alert's `notes` field.

The `confidence < 0.3` threshold triggers full escalation with complete context. The resolver never silently drops an alert regardless of confidence.

**Your task:** Answer the configuration questions.
