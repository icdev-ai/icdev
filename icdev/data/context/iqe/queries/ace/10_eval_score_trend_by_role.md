# Eval Score Trend by Role

## Query

```iqe
foreach e in ace.evals
  join s in ace.sessions on s.session_id == e.session_id
  where e.graded_at >= now() - 30d
  group_by s.role, week(e.graded_at)
  select s.role, week(e.graded_at) as period,
         avg(e.efficiency_score) as avg_efficiency,
         avg(e.reasoning_coverage) as avg_reasoning,
         avg(e.tool_error_rate) as avg_error_rate,
         count(*) as session_count
  order_by s.role asc, period asc
```

## Description

Weekly aggregated eval scores broken down by coworker role over the past
30 days. Use this to track whether a role's quality is improving after
system prompt changes, or whether a specific role is regressing. The
`/coworker/evals/trends` dashboard page renders this same data visually
with a live line chart and supports filtering by role, days, and bucket.
