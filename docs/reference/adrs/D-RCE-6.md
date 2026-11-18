---
id: D-RCE-6
title: Turbopuffer — SKIP
version: '1'
status: accepted
section: Phase 74 — RCE (RAG Context Engineering)
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
**Turbopuffer — SKIP.** Cloud-only; `pip install turbopuffer` is only an API client with no embedded/`:memory:` mode, so it cannot satisfy the SQLite air-gap/offline fallback that `storage.py`'s dual-backend design depends on.
