---
ontology_id: icdev:mission:m-docgen-01-session-lifecycle:step:2
step_class: icdev:configure
---
# DocGen Workflow Orchestration

The workflow layer dispatches parallel section writers and merges their outputs. Each section is an independent LLM call — faster and more parallel than generating the entire document in one shot.

## Starting a workflow

```json
POST /api/docgen/workflow/run
{
  "session_id": "sess-abc123",
  "workflow_config": {
    "doc_type": "ssp",
    "sections": ["system_description", "boundary", "controls_summary", "risk_assessment"],
    "parallel": true,
    "template": "fedramp_high"
  }
}
```

## Monitoring progress

```
GET /api/docgen/workflow/{workflow_id}/status
→ {
    "sections_dispatched": 4,
    "sections_complete": 2,
    "sections_failed": 0,
    "estimated_remaining_seconds": 45
  }
```

## Your task

Start a workflow for your SSP session from Step 1. Use the `fedramp_high` template. Monitor progress until all sections are complete. Note which sections finished first and which took longest.
