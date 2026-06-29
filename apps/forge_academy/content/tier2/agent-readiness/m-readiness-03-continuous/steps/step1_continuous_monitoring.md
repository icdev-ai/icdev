---
ontology_id: icdev:mission:m-readiness-03-continuous:step:1
step_class: icdev:configure
---
# Continuous Readiness Monitoring

A one-time readiness check is useful. A continuous monitor that alerts on regression is essential.

## Setting up continuous monitoring

The ICDEV `tools/awareness/drift_detector.py` can detect readiness regressions as part of the awareness engine cycle. Configure it with a readiness check as an additional probe:

```yaml
# args/awareness_config.yaml — add under probes:
probes:
  - type: readiness
    target: "."
    threshold: 0.70
    alert_on_regression: true
    cadence_hours: 3
```

The drift detector runs every 3 hours, compares current readiness to baseline, and promotes any regression to the kanban `suggested` column.

## Your task

Add the readiness probe to `args/awareness_config.yaml`. Run `python tools/awareness/drift_detector.py --detect --json` and confirm the readiness probe appears in the output. Set a baseline: `python tools/awareness/drift_detector.py --set-baseline --json`.
