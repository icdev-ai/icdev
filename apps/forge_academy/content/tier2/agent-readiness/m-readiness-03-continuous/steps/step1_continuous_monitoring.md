---
ontology_id: icdev:mission:m-readiness-03-continuous:step:1
step_class: icdev:configure
---
# Continuous Readiness Monitoring

A one-time readiness check is useful. A monitor that alerts on regression is essential.

## What ICDEV already monitors, and what it does not

ICDEV's Internal Awareness Engine runs on a 3-hour cadence (`schedule.interval_hours: 3` in `args/awareness_config.yaml`). Its drift detector, `tools/awareness/drift_detector.py`, compares component health snapshots and calls a **regression** after 2 consecutive failing snapshots (`drift.consecutive_fails_for_regression`) against a 7-day rolling baseline. Its probes are `http_head`, `module_import`, `db_table_present`, `api_surface_match`, `coherence_status` and the (disabled) `test_green`.

**Readiness is not one of those probes.** Adding a `readiness:` key to `args/awareness_config.yaml` would do nothing; the detector has no code that reads it. So continuous readiness monitoring is something you wire yourself, from parts that exist:

1. **Run the check on a schedule.** A nightly CI job (or cron) runs `run_readiness_check('.')` and writes `overall_readiness_score` and each pillar's `percentage` to a dated JSON file or a table
2. **Keep a baseline.** Compare tonight's scores to the last accepted run, not to an ideal
3. **Alert on regression, not on low scores.** A drop of a pillar by more than your tolerance, or the overall score crossing below `0.7`, is the signal
4. **Route the alert where work happens:** a failing CI job, an issue, or a kanban card

The drift detector itself is still useful next to this: `python tools/awareness/drift_detector.py --detect --json` (add `--dry-run` to collect findings without writing) tells you whether the *platform's components* regressed in the same window.

## Your task

Design your readiness monitor: the schedule, where the score history is stored, the baseline rule, the regression threshold (per pillar and overall) and where the alert goes. Then name one regression that the 0.7 overall gate from m-readiness-02 would miss but your per-pillar rule would catch. Press **Configure** to record that you completed the design.
