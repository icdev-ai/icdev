---
ontology_id: icdev:mission:m-ace-01-roles-delegation:step:3
step_class: icdev:verify
---
# Inspect the Co-Worker Result

After a launch, poll `GET /api/ace/<instance_id>/status` until every co-worker has finished (`done` or `failed`) or one is waiting on a human (`hitl_pending`).

## Status response

```json
{
  "instance_id": "…",
  "name": "…",
  "state": "…",
  "trust_tier": "yellow",
  "created_at": "…",
  "updated_at": "…",
  "coworkers": [
    {"id": "…", "role_id": "ai_developer", "state": "done", "assigned_step": "…"}
  ]
}
```

Co-worker states you will see: `working`, `hitl_pending`, `done`, `failed`, `suspended`.

## Reading the output

- `GET /api/ace/<instance_id>/artifacts` lists what the team produced (`artifact_type`, `title`, `classification`, `content_md`, `content_json`)
- `GET /api/ace/<instance_id>/messages` shows the co-workers' message-bus traffic
- `GET /api/ace/<instance_id>/audit` is the append-only audit trail: every step, gate and decision
- The live view of a run is `/coworker/live/<instance_id>` on the dashboard

## Your task

For the launch you drafted in Step 2, write down the three calls you would make, in order, to (1) confirm the run finished, (2) read the generated code, and (3) prove afterwards which steps actually ran. If a co-worker ended in `failed`, which endpoint tells you why?
