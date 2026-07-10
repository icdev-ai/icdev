# ICDEV Cortex (Unified AI Facade)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## ICDEV Cortex (Unified AI Facade)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Cortex Schemas | tools/cortex/schemas.py | Unified normalization contract for the Cortex facade: Citation, CortexSearchResult (score 0-1, backend in {rag,graph,dic,kb}), CortexResult, CortexContext (tenant_id + classification, RLS-ready), GovernanceReport. All dataclasses round-trip via to_dict()/from_dict(). Mirrored to icdev/tools/cortex/. | dataclass construction / dicts | Normalized dataclass instances / JSON-safe dicts |
| Cortex API (complete/classify/extract) | tools/cortex/api.py | First 3 of 7 facade functions over LLMRouter: complete(prompt), classify(text, labels) with deterministic query_classifier degradation when the router raises, extract(text, schema) with output_schema + fenced-JSON parsing. Routing via cortex_complete/cortex_classify/cortex_extract entries in args/llm_config.yaml — no model IDs in code. CortexContext tenant_id/classification threaded into LLMRequest. Mirrored to icdev/tools/cortex/. | prompt/text + optional CortexContext | CortexResult with provider/model/cost/latency_ms accounting |
