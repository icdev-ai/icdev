---
id: D-DISP-1
title: Dispatcher-only orchestrator mode restricts orchestrator to delegation tools
version: '1'
status: accepted
section: D-DISP — Dispatcher Mode
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
Dispatcher-only orchestrator mode restricts orchestrator to delegation tools (dispatch, route, status, mailbox) and blocks domain tools (scaffold, generate_code, ssp_generate, etc.) — enforces FORGE separation of concerns, per-project DB overrides
