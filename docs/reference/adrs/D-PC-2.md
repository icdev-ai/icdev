---
id: D-PC-2
title: Prompt chains use LLMRouter.invoke() for model invocation, not A2A agent dispatch
version: '1'
status: accepted
section: D-PC — Prompt Chains
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
Prompt chains use LLMRouter.invoke() for model invocation, not A2A agent dispatch — chains are reasoning pipelines, not agent orchestration
