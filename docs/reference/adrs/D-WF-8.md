---
id: D-WF-8
title: Coherence Engine
version: '1'
status: accepted
section: D-WF — Workflow Discipline Engine
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
Coherence Engine — 7 deterministic checks (schema_code, config_code, signature_call, fixture_schema, manifest, append_only, import_usage) with 2-tier auto-fix (auto: imports/append-only, suggest: schema/config/fixture). Wired into: workflow UNIFY reconciler, Genesis audit reflex, GKP promotion gate, CI/CD SDLC pipeline, marketplace publish gate, test orchestrator, heartbeat daemon, production audit. Child apps inherit via DIRECTORY_TREE. LLM-agnostic (stdlib only, air-gap safe)
