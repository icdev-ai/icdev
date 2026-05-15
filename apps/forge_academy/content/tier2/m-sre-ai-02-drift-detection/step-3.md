---
ontology_id: icdev:mission:m-sre-ai-02-drift-detection:step:3
step_class: icdev:Assessment
---

<!-- CUI // SP-CTI -->

# Drift Response Protocol

Detecting drift is only half the job. You need a clear, pre-planned response for every severity level so that on-call engineers make consistent decisions under pressure. This step defines the decision tree, the baseline reset trap, NIST traceability requirements, and integration with the AADC drift-detector node.

## Drift Response Decision Tree

```
detect_drift() fires
       │
       ├── severity = 'info'
       │       └── Action: LOG ONLY
       │               Write to model_drift_events (automatic)
       │               No alert. Review at next sprint retrospective.
       │
       ├── severity = 'warning'
       │       └── Action: ALERT + INCREASE CADENCE
       │               1. Page on-call via configured alerting channel
       │               2. Reduce drift detection interval from 24h → 4h
       │               3. Capture a baseline snapshot for comparison
       │               4. Review recent prompt template changes (git log)
       │               5. Do NOT reset baseline yet
       │
       └── severity = 'critical'
               └── Action: ALERT + AUTO-REMEDIATE
                       1. Page on-call immediately
                       2. Evaluate: is this a prompt regression or a model change?
                           ├── Prompt regression → roll back prompt template
                           └── Model change → trigger_retrain() or model swap
                       3. If model swap: model_registry.get_previous_stable()
                       4. Monitor for 48h stable window before reset_baseline()
```

## The Baseline Reset Trap

`reset_baseline()` should be called only after:
1. Successful retraining or model rollback is confirmed.
2. The model has shown a **stable 48-hour window** with quality scores above the warning threshold.
3. A human SRE has explicitly approved the reset.

**Why this matters:** If you call `reset_baseline()` during an active drift event, you are redefining "normal" as the degraded state. All future drift calculations will compare against the bad baseline. The regression becomes invisible permanently.

```python
from tools.llm.model_monitor import reset_baseline, get_drift_history

# Safety check: verify no active critical events before resetting
history = get_drift_history(model_id="qwen3-local", hours=48)
critical_events = [e for e in history if e["severity"] == "critical"]
if critical_events:
    raise RuntimeError("Cannot reset baseline during active critical drift event.")

# Only reset after 48h clean window
reset_baseline(
    model_id="qwen3-local",
    function_name="summarize",
    reason="Post-retrain stable window confirmed by SRE on 2026-05-09",
)
```

## NIST SI-12: Information Management and Retention

NIST SI-12 requires that information system output be handled and retained in accordance with applicable laws, regulations, and organizational policies. For AI drift events this means:

- Every drift event must be written to the append-only `model_drift_events` table — no deletes, no updates to existing rows.
- `action_taken` must be recorded at event time, not retroactively.
- Drift events involving CUI-generating models must be retained for the full CUI retention period.

ICDEV enforces this automatically: `model_drift_events` is listed in `APPEND_ONLY_TABLES` in `.claude/hooks/pre_tool_use.py`, which blocks any `UPDATE` or `DELETE` on the table.

## Integration with AADC Drift-Detector Node

The AADC (AI-Assisted Design Canvas) includes a `drift-detector` node type in the Awareness Engine. When the `drift_detector.py` tool runs its detection cycle:

```bash
python tools/awareness/drift_detector.py --detect --json
```

It queries `model_drift_events` for unresolved critical events and creates Kanban suggestions via `suggested_card_writer.py`. This closes the loop: drift detection automatically surfaces remediation tasks in your task board without manual intervention.

## Drift Response Checklist

Before closing a drift incident, verify all of the following:

- [ ] Root cause documented in drift event record (`notes` field)
- [ ] Prompt template changes audited (git history reviewed)
- [ ] Remediation action recorded in `action_taken`
- [ ] Baseline NOT reset during active drift
- [ ] 48h stable window observed before baseline reset
- [ ] NIST SI-12 retention confirmed (event is in append-only table)
- [ ] AADC drift-detector node acknowledgment recorded
- [ ] Post-incident Kanban task closed with V&V sign-off

**Your task:** Answer the reflection questions.
