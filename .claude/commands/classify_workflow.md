# ICDEV Workflow Extraction

Extract ICDEV workflow information from the text below and return a JSON response.

## Instructions

- Look for ICDEV workflow commands in the text (e.g., `/icdev_plan`, `/icdev_build`, `/icdev_test`, `/icdev_review`, `/icdev_comply`, `/icdev_secure`, `/icdev_deploy`, `/icdev_plan_build`, `/icdev_plan_build_test`, `/icdev_plan_build_test_review`, `/icdev_sdlc`)
- Look for ICDEV run IDs (8-character alphanumeric strings, often after "run_id:" or "ICDEV ID:" or similar)
- Return a JSON object with the extracted information
- If no ICDEV workflow is found, return empty JSON: `{}`

## Valid ICDEV Commands

- `/icdev_plan` — Planning only
- `/icdev_build` — Building only (requires run_id)
- `/icdev_test` — Testing only (requires run_id)
- `/icdev_review` — Review only (requires run_id)
- `/icdev_comply` — Compliance artifacts
- `/icdev_secure` — Security scanning
- `/icdev_deploy` — Deployment
- `/icdev_plan_build` — Plan + Build
- `/icdev_plan_build_test` — Plan + Build + Test
- `/icdev_plan_build_test_review` — Plan + Build + Test + Review
- `/icdev_sdlc` — Complete SDLC: Plan + Build + Test + Review + Comply

## Response Format

Respond ONLY with a JSON object in this format:
```json
{
  "icdev_slash_command": "/icdev_plan",
  "run_id": "abc12345"
}
```

Fields:
- `icdev_slash_command`: The ICDEV command found (include the slash)
- `run_id`: The 8-character run ID if found

If only one field is found, include only that field.
If nothing is found, return: `{}`

## Text to Analyze

$ARGUMENTS
