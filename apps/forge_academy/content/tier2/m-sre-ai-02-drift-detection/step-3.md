---
ontology_id: icdev:mission:m-sre-ai-02-drift-detection:step:3
step_class: icdev:Assessment
---

<!-- CUI // SP-CTI -->

# Drift Response Protocol

Detecting drift is only half the job. You need a pre-planned response for every severity so that on-call engineers make consistent decisions under pressure. This step covers the decision tree, the baseline reset trap, NIST retention, and how drift monitoring shows up in the Agentic AI Design Canvas.

## Drift Response Decision Tree

```
detect_drift() returns an event
       │
       ├── severity = 'warning'
       │       └── Action: ALERT + INCREASE CADENCE
       │               1. Notify on-call via your alerting channel
       │               2. Run detect_drift() more often (e.g. window_days=1, daily)
       │               3. Record the current get_baseline() output for comparison
       │               4. Review recent prompt template changes (git log)
       │               5. Do NOT reset the baseline yet
       │
       └── severity = 'critical'
               └── Action: ALERT + REMEDIATE
                       1. Page on-call immediately
                       2. Decide: prompt regression or model change?
                           ├── Prompt regression → roll back the prompt template
                           └── Model change → trigger_retrain(), or route the
                               function to its previous model
                       3. For a fine-tuned model: tools/finetune/model_registry.py
                          demote_model(function_name, reason=...) reverts the
                          function to default routing
                       4. Wait for a stable window before reset_baseline()
```

## The Baseline Reset Trap

Call `reset_baseline(model_id, function_name)` only after:
1. Retraining or rollback is confirmed successful.
2. The model has shown a **stable window**, with fresh `detect_drift()` runs returning no events.
3. A human SRE has explicitly approved the reset.

**Why this matters:** the baseline is the *oldest* 30 days of data. `reset_baseline()` **deletes** quality scores older than 30 days for that pair, so the most recent data becomes the new baseline. If you reset during an active drift event, you redefine "normal" as the degraded state. Every later comparison is made against the bad baseline, and the regression becomes invisible permanently. The deleted scores do not come back.

```python
from tools.llm.model_monitor import reset_baseline, detect_drift

# Safety check: no live drift before resetting
if detect_drift(model_id="qwen3-local", function_name="summarize"):
    raise RuntimeError("Cannot reset baseline while drift is still being detected.")

reset_baseline(model_id="qwen3-local", function_name="summarize")
# {"status": "baseline_reset", "archived_records": N, "new_baseline_start": "..."}
```

Write down who approved the reset and why, for example in the incident ticket. `reset_baseline()` takes no reason argument and keeps no audit row of its own.

## NIST SI-12: Information Management and Retention

NIST SI-12 requires that system output be handled and retained in line with applicable laws, regulations and organizational policy. For AI drift events this means:

- Every drift event is written to the append-only `model_drift_events` table, with no deletes and no updates to existing rows.
- `action_taken` is recorded at event time, not retroactively.
- Drift events about CUI-generating models are kept for the full CUI retention period.

ICDEV enforces the first point. `model_drift_events` is listed in `APPEND_ONLY_TABLES` in `.claude/hooks/pre_tool_use.py`, which blocks `UPDATE` and `DELETE` statements against it.

## Drift Monitoring in the Agentic AI Design Canvas (AADC)

When you design an agentic system in the AADC, `drift-detector` and `baseline-snapshot` are node types you place on the canvas (`INFRA_NODES` in `tools/agentic_ai_canvas/constants.py`). The canvas's NIST AI RMF check **MEA-2 "Drift monitoring enabled"** passes only when the design includes one of them. That makes drift monitoring a reviewable design decision, not an afterthought.

Do not confuse it with `tools/awareness/drift_detector.py`. That module detects *platform component* regressions (routes, imports, coherence) for ICDEV's self-awareness engine, not model drift.

## Drift Response Checklist

Before closing a drift incident, verify all of the following:

- [ ] Root cause documented in the incident ticket
- [ ] Prompt template changes audited (git history reviewed)
- [ ] Remediation recorded (`trigger_retrain()` writes its own `retrain_triggered` event)
- [ ] Baseline NOT reset while drift was still detected
- [ ] Stable window observed and human approval recorded before `reset_baseline()`
- [ ] NIST SI-12 retention confirmed (event is in the append-only table)
- [ ] AADC design for this agent includes a `drift-detector` or `baseline-snapshot` node
- [ ] Post-incident Kanban task closed with V&V sign-off

**Your task:** Answer the reflection questions.
