---
id: D-OWUI-1
title: Open WebUI may be deployed as a SEPARATE, digest-pinned sidecar that holds NO authority; ICDEV
  is the only backend it may reach
version: '1'
status: accepted
section: Phase 82 — OWUI (Open WebUI as a Governed Sidecar Client)
format: legacy
supersedes: []
superseded_by: ''
links:
  requirements: []
  standards: []
context: ''
consequences: ''
recorded:
  by: bootstrap:icx-coe-02-adr-migration
  kind: bootstrap
  step_id: ''
  note: migrated from docs/reference/adrs.md at bbd0b5df6
---
**Open WebUI may be deployed as a SEPARATE, digest-pinned sidecar that holds NO authority; ICDEV is the only backend it may reach.** This narrows D-CRX-9 rather than reversing it: D-CRX-9 keeps SPA/Vite/npm out of THIS repository, and nothing of Open WebUI enters it — it runs as its own image (compose profile / Helm values), and anything ICDEV ships for it is plain Python (an Open WebUI function file) or configuration. It differs from the anything-llm rejection (`docs/spikes/oss-02-nine-project-adaptation.md` §2.1) in exactly the property that sank anything-llm: anything-llm would have REPLACED DIC, RAG, `/chat` and tenants, whereas here every one of those stays in ICDEV and Open WebUI's equivalents are switched off. Decisions: (1) models reach Open WebUI ONLY through `tools/saas/openai_compat.py` (`/v1/models`, `/v1/chat/completions` on the SaaS gateway), which calls `LLMRouter.invoke`, and the exposed ids are ICDEV capabilities from `args/openai_compat.yaml`, never vendor models; (2) a capability whose router function is absent from the ACTIVE llm_config is hidden and refused, because the router would otherwise serve it from the cloud-first `routing.default`; (3) tools reach it only through `/mcp/v1/` with a curated toolset profile, read-only first, and `MCPToolAuthorizer` is moved to `enforce` for that caller only after a measured monitor-mode survey; (4) identity is a shared enterprise IdP with the user's own token forwarded — `X-OpenWebUI-User-*` headers are spoofable and grant nothing; (5) ICDEV, not Open WebUI's database, is the system of record: every exchange is audited in ICDEV and uploads go to document intelligence. **Forbidden configurations** (each one is a governance bypass): an Open WebUI connection to Ollama, OpenAI or any provider directly; a connection to ICDEV's LiteLLM proxy (it sits DOWNSTREAM of the router's redaction and routing policy); Open WebUI's own RAG/vector store holding CUI; header-forwarded identity. Deliberately NOT adopted: embedding or forking Open WebUI code, rebuilding HITL/kanban UI inside it, and token streaming through `invoke_streaming` until that path de-anonymizes and audits (owui-api-02).
