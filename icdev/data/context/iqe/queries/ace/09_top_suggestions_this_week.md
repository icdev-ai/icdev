# Top Improvement Suggestions This Week

## Query

```iqe
foreach e in ace.evals
  where e.graded_at >= now() - 7d
  select e.session_id, e.outcome, e.efficiency_score, e.tool_error_rate,
         e.plan_stated, e.reasoning_coverage, e.scope_violations
  order_by e.efficiency_score asc
  limit 50
```

## Description

Returns the 50 lowest-scoring sessions from the past 7 days for bulk
suggestion analysis. For each session, call
`GET /api/ace/sessions/<session_id>/eval/suggestions` to retrieve the
rule-based improvement recommendations (no LLM calls needed). Common
suggestion fields: `system_prompt`, `max_iterations`, `folder_access`,
`reasoning_style`. Use this to find recurring patterns across sessions
and update coworker role definitions accordingly.
