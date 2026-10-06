---
ontology_id: icdev:mission:m-docgen-01-session-lifecycle:step:2
step_class: icdev:configure
---
# DocGen Workflow Orchestration

The workflow is driven one stage at a time. Each call below is a real route on the DocGen blueprint (prefix `/docgen`):

```
POST /docgen/api/sessions/<id>/uploads            stage 1: attach source files
POST /docgen/api/sessions/<id>/analyze            stage 2: run each upload's analyzer
POST /docgen/api/sessions/<id>/detect-conflicts   stage 3: find contradictory facts
GET  /docgen/api/sessions/<id>/conflicts          ... and list them
POST /docgen/api/conflicts/<conflict_id>/resolve  a human resolves each one (HITL)
POST /docgen/api/sessions/<id>/generate           stages 4-5: synthesize context, draft
POST /docgen/api/sessions/<id>/writeguard         stage 6: quality gate
POST /docgen/api/sessions/<id>/advance            move to the next stage (gates enforced)
```

## Generation

```json
POST /docgen/api/sessions/<id>/generate
{
  "use_ace": true,
  "role_ids": ["technical_writer", "security_analyst"],
  "supplemental_text": "Emphasise the IL4 boundary and the continuous monitoring section."
}
```

All fields are optional. With `use_ace: true` an ACE co-worker team drafts the document; for an `ato_ssp` the expected sections are System Overview, System Boundary, Data Flows, Control Implementation and Continuous Monitoring.

## Monitoring progress

`GET /docgen/api/sessions/<id>/progress` is a Server-Sent Events stream that reports the session's stage and status as generation runs; `GET /docgen/api/sessions/<id>` returns the current row at any time.

## Your task

For your SSP session from Step 1, write the ordered list of calls that takes it from stage 0 to a WriteGuard pass. Mark the two places where the workflow will refuse to continue without a human or a passing quality score, and say what the refusal response tells you. Press **Configure** to record that you completed the plan.
