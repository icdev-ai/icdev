---
ontology_id: icdev:mission:m-gov-01-transparency:step:2
step_class: icdev:configure
---
# Build Your AI Inventory

Use ICDEV's AI Transparency canvas to build and submit your AI inventory.

## Inventory API

```json
POST /ai-transparency/api/inventory
{
  "system_name": "ICDEV Knowledge RAG",
  "owner": "Platform Team",
  "purpose": "Retrieve relevant documentation chunks for user queries",
  "data_classification": "CUI",
  "risk_tier": "medium",
  "autonomy_level": "supervised",
  "frameworks": ["OMB-M-25-21", "NIST-AI-600-1"],
  "omm_section": "5a"
}
```

## Your task

Submit 3 AI inventory entries for ICDEV systems. For each, use the API above and include all required fields. After submitting all 3, GET `/ai-transparency/api/inventory` and confirm all 3 appear with status `active`.
