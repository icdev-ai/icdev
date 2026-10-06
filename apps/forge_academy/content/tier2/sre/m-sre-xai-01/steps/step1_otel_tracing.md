---
ontology_id: icdev:mission:m-sre-xai-01:step:1
step_class: icdev:Lesson
---

# Distributed Tracing with OTel + AgentSHAP

ICDEV's observability stack (`tools/observability/`) gives you two things. Distributed tracing shows what happened. AgentSHAP shows which tool mattered.

## OpenTelemetry traces in ICDEV

ICDEV instruments its hot paths with OTel-style spans. Real span names you will see:
- `gen_ai.invoke`: one span per LLM call made through `LLMRouter` (model, tokens, latency in the GenAI semantic-convention attributes)
- `gen_ai.bedrock.invoke`: Bedrock calls made through the Bedrock client
- `mcp.tool_call`: one span per MCP tool invocation, with the tool name in the `mcp.tool.name` attribute
- `agent.turn`: one span per agent turn (`tools/observability/agent_trace.py`)

Spans are written to the `otel_spans` table in the main ICDEV database (`tools/observability/sqlite_tracer.py`; PostgreSQL by default, SQLite as the fallback). You can view them at `/traces` in the dashboard, which reads `/api/traces/`.

## AgentSHAP: tool attribution

AgentSHAP (`tools/observability/shap/agent_shap.py`) computes **Monte Carlo Shapley values** for the tools in one trace. It answers: **"Which tool call had the most impact on the outcome?"** It reads the `mcp.tool_call` spans of a trace and returns, for each tool, a `shapley_value`, a 95% confidence interval and a `normalized` share. For example:

```
Tool attribution for response to "What CVEs affect ICDEV dependencies?":
  knowledge.search   → 0.62 (62% of the outcome)
  supply_chain.check → 0.29 (29%)
  llm.summarize      → 0.09 (9%)
```

The `/xai` dashboard page runs it through `/api/xai/shap/analyze`.

## W3C PROV-AGENT provenance

`tools/observability/provenance/prov_recorder.py` records W3C PROV relations between entities, activities and agents. The relations are `wasGeneratedBy`, `used`, `wasInformedBy`, `wasDerivedFrom` and `wasAttributedTo`. They answer:
- `wasGeneratedBy`: which activity produced an artifact
- `used`: which inputs that activity consumed
- `wasAttributedTo`: which agent is responsible for the artifact

You can browse the lineage graph at `/provenance`.

## Your task

Open `/traces` in the dashboard and pick a recent trace. Identify: (1) how many LLM (`gen_ai.*`) spans it contains, (2) which span had the highest latency, (3) the overall trace duration. If your install has no traces yet, run any LLM-backed feature once and refresh.
