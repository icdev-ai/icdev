---
ontology_id: icdev:mission:m-ace-01-roles-delegation:step:2
step_class: icdev:configure
---
# Launch Your First Co-Worker

ACE co-workers are launched via the `/api/ace/coworker/delegate` endpoint. The request body specifies the role, task description, and any context the co-worker needs.

## Delegation Request

```json
{
  "role": "security_analyst",
  "task": "Run a STIG compliance check on the authentication module",
  "context": {
    "target_path": "tools/auth/",
    "il_level": "IL4",
    "framework": "NIST-800-53"
  },
  "hitl_required": true
}
```

## What happens next

1. ACE creates a co-worker instance with the `security_analyst` role
2. The co-worker reads its role YAML (`listen_topics`, `steps`, `system_prompt`)
3. It executes each step using the LLM router
4. If `hitl_required: true`, it pauses and posts to the HITL queue
5. After human approval, the result is returned

## Your task

Configure a delegation request for the `ai_developer` role to: "Implement a Python function that reads a CSV file and returns a summary dict with row count, column names, and null count per column." Set `hitl_required: false` for this non-sensitive task.
