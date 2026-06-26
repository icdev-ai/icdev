---
ontology_id: icdev:mission:m-gov-01-transparency:step:3
step_class: icdev:configure
---
# Generate a Model Card

A model card documents a specific AI model: its training data, intended use, limitations, fairness considerations, and performance metrics.

## Model card API

```json
POST /ai-transparency/api/model-card
{
  "system_id": "your-inventory-id-from-step-2",
  "model_name": "ICDEV Knowledge RAG",
  "model_version": "1.0",
  "intended_use": "Knowledge retrieval for ICDEV platform users",
  "out_of_scope_use": "Medical, legal, or financial advice",
  "training_data_description": "ICDEV documentation corpus, mission content, compliance artifacts",
  "known_limitations": "May hallucinate details not in the knowledge base",
  "fairness_considerations": "No demographic data processed; access gated by role",
  "performance_metrics": {"retrieval_accuracy": 0.89, "latency_p95_ms": 250}
}
```

## Your task

Generate a model card for one of the AI systems you inventoried. Include all fields above, filling in accurate values for the ICDEV system you chose. GET the completed card and verify all required OMB M-25-21 fields are present.
