---
ontology_id: icdev:mission:m-readiness-01-eleven-pillars:step:3
step_class: icdev:reflect
---
# Anomaly Detection

The readiness checker includes an anomaly detector that flags pillars whose scores deviate significantly from the expected range. An anomaly means the score is suspiciously low (possible systematic gap) or suspiciously high (possible false positive).

## Reading anomaly output

```json
"score_anomalies": [
  {
    "pillar_id": "stig-compliance",
    "score_pct": 12.5,
    "threshold": 45.0,
    "is_anomalous": true,
    "reason": "Score is 2.3 standard deviations below the project mean",
    "ai_reasoning": "The STIG compliance score is much lower than other pillars. This typically indicates no STIG markers are present, not that the system is actually non-compliant."
  }
]
```

## Your task

Look at the `score_anomalies` in your checker output. For each anomalous pillar: (1) Is the anomaly a real gap or a false positive? (2) What one change would most improve that pillar's score?
