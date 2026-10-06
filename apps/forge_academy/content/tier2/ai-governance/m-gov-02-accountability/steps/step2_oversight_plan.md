---
ontology_id: icdev:mission:m-gov-02-accountability:step:2
step_class: icdev:configure
---
# Build an Oversight Plan

ICDEV records an oversight plan in the `ai_oversight_plans` table: a **name**, a **description**, who **created** it, and an **approval status** that starts at `draft` (then `submitted`, `approved` or `rejected`). The structure inside the plan is yours to write; ICDEV makes it a tracked, audited record.

## Registering the plan

```bash
python tools/compliance/accountability_manager.py --project-id <project-id> \
  --register-oversight \
  --plan-name "Knowledge RAG Human Oversight Plan v1" \
  --description "<your plan: oversight board, review frequency, shutdown authority, appeals process>" \
  --created-by "Platform CAIO" \
  --json
```

The MCP tool `ai_oversight_plan_create` does the same. Registration writes an audit event (`accountability.oversight_plan`).

## The appeals half

M-25-21's minimum practices for high-impact AI include a way for affected people to seek remedy or appeal. ICDEV tracks appeals as their own records:

```bash
python tools/compliance/accountability_manager.py --project-id <project-id> \
  --file-appeal --appellant "Jane Doe" --ai-system "Knowledge RAG" --grievance "<what was contested>" --json
```

An appeal moves through `submitted`, `under_review`, `resolved` or `dismissed`. The **AI Accountability** page on the Security canvas (`/security/ai-accountability`) and `GET /api/ai-accountability/appeals` show them.

## Your task

Write the `--description` text for an oversight plan for one system in your inventory. It must cover: the oversight board members, review frequency, shutdown authority (primary, escalation, SLA in minutes, conditions) and the appeals process (who may appeal, to whom, response time). Press **Configure** to record that you completed the exercise.
