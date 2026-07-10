# ICDEV Cortex (Unified AI Facade)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## ICDEV Cortex (Unified AI Facade)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Cortex Schemas | tools/cortex/schemas.py | Unified normalization contract for the Cortex facade: Citation, CortexSearchResult (score 0-1, backend in {rag,graph,dic,kb}), CortexResult (structured payload in .data), CortexContext (tenant_id + classification, RLS-ready), GovernanceReport. All dataclasses round-trip via to_dict()/from_dict(). Mirrored to icdev/tools/cortex/. | dataclass construction / dicts | Normalized dataclass instances / JSON-safe dicts |
| Cortex Analyst | tools/cortex/analyst.py | IQE-primary NL ask-your-data endpoint (`cortex.ask(question, mode, ctx)`): resolves registered collections (explicit / canvas / question match), translates via nl_to_iqe, authorizes targets against the tools.iqe.executor registry, executes deterministically, and returns CortexResult with rows in .data plus one Citation(source_type="analyst", source_table=collection) per collection read. Unknown/unregistered collections raise CortexAnalystError (semantic fallback = ctx-analyst-02). Tenant/classification from CortexContext thread into the DB connection via SecurityContext. Mirrored to icdev/tools/cortex/. | question, mode ("auto"/"iqe"), CortexContext, canvas/collections/conn kwargs | CortexResult (text summary, .data rows, analyst citations) or CortexAnalystError |
| Cortex API facade | tools/cortex/api.py | Stable import surface for Cortex consumers: re-exports ask(), CortexAnalystError, and all schemas. Later ctx-* epics add search()/complete() alongside. Mirrored to icdev/tools/cortex/. | imports | public Cortex API |
