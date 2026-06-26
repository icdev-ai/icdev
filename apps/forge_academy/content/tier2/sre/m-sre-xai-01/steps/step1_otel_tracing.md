---
ontology_id: icdev:mission:m-sre-xai-01:step:1
step_class: icdev:Lesson
---

# Distributed Tracing with OTel + AgentSHAP

ICDEV's observability stack provides distributed tracing (OpenTelemetry + SQLite backend) and explainability (AgentSHAP — tool attribution for agent decisions).

## OpenTelemetry traces in ICDEV

Every ICDEV agent operation emits OTel spans:
- `agent.loop.step` — one span per agent loop iteration
- `tool.call` — span for each tool invocation
- `llm.inference` — span for each LLM call (model, tokens, latency)
- `rag.retrieval` — span for each RAG query

Traces are stored in SQLite (`data/traces.db`) and viewable at `/traces` in the dashboard.

## AgentSHAP: tool attribution

AgentSHAP uses SHAP (SHapley Additive exPlanations) adapted for agentic tool attribution. It answers: **"Which tool call had the most impact on the final answer?"**

```
Tool attribution for response to "What CVEs affect ICDEV dependencies?":
  knowledge.search   → 0.62 (62% of response quality)
  supply_chain.check → 0.29 (29%)
  llm.summarize      → 0.09 (9%)
```

## W3C PROV-AGENT provenance

Every trace includes W3C PROV-AGENT annotations:
- `prov:wasGeneratedBy` — which agent produced each artifact
- `prov:used` — which inputs were consumed
- `prov:wasAttributedTo` — which entity is responsible

## Your task

Navigate to `/traces` in the ICDEV dashboard. Find a trace from the last 24 hours. Identify: (1) how many LLM calls were made, (2) which tool had the highest latency, (3) what the overall trace duration was.
