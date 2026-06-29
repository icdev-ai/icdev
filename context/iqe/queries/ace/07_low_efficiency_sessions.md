# Low-Efficiency Agent Sessions

## Query

```iqe
foreach e in ace.evals
  where e.efficiency_score < 0.3
  select e.session_id, e.efficiency_score, e.turns_used, e.max_iterations, e.outcome, e.graded_at
  order_by e.efficiency_score asc
  limit 20
```

## Description

Lists the 20 agent sessions with the lowest efficiency scores (below 30%).
Efficiency is computed as `turns_used / max_iterations` — sessions near the
limit or that hit `error_max_turns` will appear here. Use this to identify
coworker roles or task types that consistently exhaust their turn budget and
should have their system prompts or `max_iterations` tuned.
