# High Tool Error Rate Sessions

## Query

```iqe
foreach e in ace.evals
  where e.tool_error_rate > 0.4
  select e.session_id, e.tool_error_rate, e.total_tool_calls, e.error_tool_calls, e.outcome, e.graded_at
  order_by e.tool_error_rate desc
  limit 20
```

## Description

Lists sessions where more than 40% of tool calls returned errors. A high tool
error rate indicates the agent is mis-using tools (wrong inputs, wrong paths,
out-of-scope operations) and would benefit from more explicit system prompt
constraints. Cross-reference with `/api/ace/sessions/<id>/eval/suggestions`
to see the specific improvement recommendations for each session.
