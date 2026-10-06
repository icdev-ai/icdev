---
ontology_id: icdev:mission:m-sre-ai-04-incident-response:step:2
step_class: icdev:Lesson
---

<!-- CUI // SP-CTI -->

# Build Your AI Runbook with auto_resolver.py

`tools/monitor/auto_resolver.py` is ICDEV's first responder for alerts. It normalizes a raw alert, matches it against known failure patterns, and decides whether to fix it automatically, suggest a fix, or escalate to a human. This step covers its real API, the confidence thresholds, and how a model rollback works in ICDEV.

## The decision rule

The thresholds live under `auto_resolution` in `args/monitoring_config.yaml`:

```yaml
auto_resolution:
  enabled: true
  confidence_threshold: 0.7     # at or above this AND pattern auto_healable -> auto_fix
  escalation_threshold: 0.3     # below this (or no matching pattern) -> escalate
  max_auto_fixes_per_hour: 5
  cooldown_minutes: 10
  auto_create_pr: true
  run_tests_before_pr: true
```

| Confidence | Pattern matched? | Decision |
|---|---|---|
| ≥ 0.7 and pattern `auto_healable` | yes | `auto_fix`: branch, fix, tests, PR |
| 0.3 to 0.7 | yes | `suggest`: root cause and remediation shown to a human |
| < 0.3, or no pattern | no or weak | `escalate` |

## `auto_resolver.py` API Reference

### `normalize_alert(payload, source="generic")`

```python
from tools.monitor.auto_resolver import normalize_alert

raw = {
    "title": "quality_degradation",
    "description": "qwen3-local/summarize quality 31.2% below baseline",
    "service": "llm-router",
    "severity": "critical",
    "source": "model_monitor",
}
alert = normalize_alert(raw, source="generic")
# {"error_type": "quality_degradation", "error_message": "...", "service_name": "llm-router",
#  "severity": "critical", "source": "model_monitor", "timestamp": "...", "raw_payload": {...}}
```

`source` selects the normalizer: `sentry`, `prometheus`, `elk` or `generic`.

### `analyze_alert(payload, source="generic")`

Runs normalize, feature extraction and pattern matching without changing anything:

```python
from tools.monitor.auto_resolver import analyze_alert

analysis = analyze_alert(raw)
# {"status": "ok", "confidence": 0.42, "decision": "suggest",
#  "reason": "Confidence 0.42 between 0.3-0.7",
#  "suggestion": {"pattern": ..., "root_cause": ..., "remediation": ...},
#  "alert_normalized": {...}, "features": {...}}
```

### `resolve_alert(payload, source="generic", dry_run=False)`

Runs the full pipeline: analyze, record in `auto_resolution_log`, then (for `auto_fix`) create a fix branch, run tests and open a PR. Use `dry_run=True` to preview.

### `get_resolution_history(project_id=None, limit=50)`

Reads `auto_resolution_log`, newest first.

```bash
python tools/monitor/auto_resolver.py --analyze --alert-file alert.json --json
python tools/monitor/auto_resolver.py --resolve --alert-file alert.json --dry-run --json
python tools/monitor/auto_resolver.py --history --limit 20 --json
```

## Model Rollback in ICDEV

"Roll back the model" means changing which model serves a **function**:

```python
from tools.llm.model_monitor import detect_drift
from tools.finetune.model_registry import get_active_model, demote_model
from tools.compliance.ai_incident_response import log_incident

def handle_critical_drift(project_id: str, model_id: str, function_name: str):
    # 1. Confirm the drift
    events = detect_drift(model_id=model_id, function_name=function_name)
    critical = [e for e in events if e["severity"] == "critical"]
    if not critical:
        return None

    # 2. Record the AI incident
    log_incident(project_id=project_id, incident_type="model_drift",
                 description=f"{critical[0]['drift_type']} {critical[0]['deviation_pct']}% on "
                             f"{model_id}/{function_name}",
                 ai_system=model_id, severity="critical")

    # 3a. Fine-tuned model active for this function? Demote it: routing falls
    #     back to the function's default chain in args/llm_config.yaml.
    if get_active_model(function_name).get("active_model"):
        return demote_model(function_name, reason="critical drift", demoted_by="oncall-sre")

    # 3b. Otherwise reorder the function's `routing:` chain in args/llm_config.yaml
    #     through a reviewed PR, never by rewriting the YAML from a script.
    return {"action": "edit routing chain via PR", "function": function_name}
```

Do **not** reset the drift baseline straight after a rollback. Wait for a stable window first (Mission SRE-AI-02, Step 3).

## Escalation Path for Low-Confidence Alerts

When `analyze_alert()` returns `decision: "escalate"` (confidence < 0.3, or no pattern):

1. `resolve_alert()` records the alert in `auto_resolution_log` with `resolution_status = 'escalated'` and notifies on-call.
2. The on-call engineer receives the normalized alert and any partial pattern match.
3. The engineer resolves it by hand, and logs an AI incident with `log_incident()` if it was an AI failure.
4. Once the root cause is understood, record it as a knowledge pattern, so next time the alert lands in `suggest`, or in `auto_fix` if it is safe to automate.

The resolver never silently drops an alert, whatever the confidence.

**Your task:** Answer the configuration questions.
