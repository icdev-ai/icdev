# ICDEV Cortex (Unified AI Facade)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## ICDEV Cortex (Unified AI Facade)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Cortex Schemas | tools/cortex/schemas.py | Unified normalization contract for the Cortex facade: Citation, CortexSearchResult (score 0-1, backend in {rag,graph,dic,kb}), CortexResult, CortexContext (tenant_id + classification, RLS-ready), GovernanceReport. All dataclasses round-trip via to_dict()/from_dict(). Mirrored to icdev/tools/cortex/. | dataclass construction / dicts | Normalized dataclass instances / JSON-safe dicts |
