---
ontology_id: icdev:mission:m-gov-01-transparency:step:2
step_class: icdev:configure
---
# Build Your AI Inventory

ICDEV keeps the inventory in the `ai_use_case_inventory` table. Entries are registered per project; the dashboard and REST API read them.

## Registering an entry

From a shell:

```bash
python tools/compliance/ai_inventory_manager.py --project-id <project-id> --register \
  --name "ICDEV Knowledge RAG" \
  --purpose "Retrieve relevant documentation chunks for user queries" \
  --risk-level minimal_risk \
  --responsible-official "Platform Team Lead" \
  --oversight-role "Knowledge base curator" \
  --appeal-mechanism "Users flag an answer; curator reviews within 5 days" \
  --json
```

`--risk-level` must be one of `minimal_risk`, `high_impact`, `safety_impacting`. The same registration is exposed to AI assistants as the MCP tool `ai_inventory_register`.

## Reading it back

```bash
python tools/compliance/ai_inventory_manager.py --project-id <project-id> --list --json
python tools/compliance/ai_inventory_manager.py --project-id <project-id> --export --json   # OMB reporting format
```

or `GET /api/ai-transparency/inventory?project_id=<project-id>`. The Security canvas page `/security/ai-transparency` shows the same rows.

## Your task

For the 3 systems you chose in Step 1, write the `--register` command for each, with every flag filled in. Which of the three did you classify `high_impact`, and what in M-25-21 does that classification oblige you to do next? Press **Configure** to record that you completed the exercise.
