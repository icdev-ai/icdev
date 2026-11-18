---
id: D-VL-12
title: Mistral/LeanStral models added to LLM router as openai_compatible provider
version: '1'
status: accepted
section: D-VL — Verification Loop (LeanStral-adapted)
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
Mistral/LeanStral models added to LLM router as `openai_compatible` provider — cloud via `api.mistral.ai/v1` (requires `MISTRAL_API_KEY`), self-hosted via vLLM (`MISTRAL_VLLM_BASE_URL`). LeanStral: 119B sparse MoE, 6.5B active/token, Apache 2.0
