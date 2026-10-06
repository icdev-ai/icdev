---
ontology_id: icdev:mission:m-readiness-01-eleven-pillars:step:3
step_class: icdev:reflect
---
# Anomaly Detection

The readiness result carries two lists that flag pillars needing attention.

## `score_anomalies`: one entry per scored pillar

A pillar is marked anomalous when its pass fraction falls below `min_passing_pct` (default `0.3`; a `score_anomaly:` block in `args/agent_readiness_config.yaml` can override it):

```json
"score_anomalies": [
  {
    "pillar_id": "stig-compliance",
    "score_pct": 0.125,
    "threshold": 0.3,
    "is_anomalous": true,
    "reason": "Pillar 'stig-compliance' scored 12% — anomalously low (configured threshold: 30%).",
    "ai_reasoning": ""
  }
]
```

`ai_reasoning` stays empty unless `ai_analysis_enabled` is switched on in that same block; then an LLM writes a short remediation note from the pillar's failing criteria.

## `anomalies`: severity-ranked findings

The second list holds findings such as a **critical** pillar (security, IL classification, NIST controls, STIG compliance, append-only audit) scoring below 50%.

## Reading them well

A very low score usually has one of two causes: a real gap, or a **missing marker** (for example, a codebase that is STIG-hardened but never references a V-ID scores near zero on pillar 10). The criterion `message` fields in `icdev_checks` tell you which.

## Reflect

Look at the anomalous pillars in your checker output (or, if you did not run it, the `testing` and `stig-compliance` pillars in the Step 2 sample). For each: (1) is it a real gap or a missing-marker false alarm, and which criterion message tells you? (2) what one change would most improve that pillar's score? Then press Continue.
