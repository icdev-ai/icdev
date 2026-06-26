---
ontology_id: icdev:mission:m-ace-03-multi-role-pipeline:step:1
step_class: icdev:design
---
# Multi-Role Co-Worker Pipelines

Real tasks need more than two co-workers. A pipeline chains multiple roles: each co-worker's output becomes the next one's input.

## Example: Secure Feature Pipeline

```
agent_developer ──► data_engineer ──► security_analyst ──► compliance_officer ──► HITL ──► Result
(design agent)    (wire RAG)       (security review)    (compliance check)
```

Each stage is a separate ACE co-worker. The pipeline is configured as a DAG in the delegation request.

## Pipeline request

```json
{
  "pipeline": [
    {"role": "agent_developer", "task": "Design a RAG agent for contract document search"},
    {"role": "data_engineer",   "task": "Configure the retrieval pipeline for the agent design"},
    {"role": "security_analyst","task": "Security review the RAG agent and retrieval config"},
    {"role": "compliance_officer", "task": "Check the design against DoD data handling requirements", "hitl_required": true}
  ],
  "sequential": true
}
```

## Your task

Design a 3-stage pipeline to: (1) build a monitoring agent, (2) add observability hooks, (3) compliance-check the observability data flows. Write the pipeline request JSON.
