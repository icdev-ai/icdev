---
ontology_id: icdev:mission:m-ace-01-roles-delegation:step:3
step_class: icdev:verify
---
# Inspect the Co-Worker Result

After delegation, poll `/api/ace/coworker/{id}/result` until status is `completed` or `pending_hitl`.

## Result Schema

```json
{
  "coworker_id": "ace-abc123",
  "role": "ai_developer",
  "status": "completed",
  "output": {
    "artifact": "...",
    "confidence": 0.92,
    "steps_completed": 3,
    "hitl_triggered": false
  },
  "created_at": "2026-06-25T12:00:00Z",
  "completed_at": "2026-06-25T12:00:45Z"
}
```

## Reading the output

- `confidence` ≥ 0.8: accept the result directly
- `confidence` 0.6–0.8: review carefully, may need iteration
- `confidence` < 0.6: the co-worker flagged uncertainty — add more context and retry

## Your task

Call `/api/ace/coworker/{id}/result` for the co-worker you launched in Step 2. Check the confidence score. If < 0.8, identify what context is missing from the delegation request and improve it.
