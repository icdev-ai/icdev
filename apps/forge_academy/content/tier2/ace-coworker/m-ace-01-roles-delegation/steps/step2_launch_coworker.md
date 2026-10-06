---
ontology_id: icdev:mission:m-ace-01-roles-delegation:step:2
step_class: icdev:configure
---
# Launch Your First Co-Worker

An ACE run is launched with `POST /api/ace/launch`. The only required field is `problem_text`; add `role_ids` to skip the problem classifier and choose the team yourself. The call is non-blocking: it returns `202` with an `instance_id` straight away while the team works in the background.

## Launch request

```json
POST /api/ace/launch
{
  "problem_text": "Run a STIG compliance review of the authentication module in tools/auth/",
  "role_ids": ["security_analyst"],
  "trigger_source": "dashboard"
}
```

Response:

```json
{"instance_id": "…"}
```

The same launch is available from the dashboard at `/coworker` and from a shell:

```bash
python -m icdev.tools.ace.controller --launch "Run a STIG review of tools/auth/" --json
```

## What happens next

1. ACE assembles the team (here, one `security_analyst` co-worker)
2. The co-worker loads its role YAML (`steps`, `communication`, `llm_function`, `tool_permissions`)
3. It executes its steps through the LLM router
4. If a HITL gate trips (Step 4), it pauses in state `hitl_pending` until a human resolves it
5. Its output is stored as artifacts on the instance

There is no per-request "HITL on/off" switch: HITL is decided by the gates described in Step 4, not by the caller.

## Your task

Write the launch request for an `ai_developer` co-worker to: "Implement a Python function that reads a CSV file and returns a summary dict with row count, column names, and null count per column." Keep it in your notes for Step 3.

> A launch on a live platform spends LLM tokens, so this step does not send it for you. The **Configure** button below records that you completed the exercise.
