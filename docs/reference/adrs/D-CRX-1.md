---
id: D-CRX-1
title: Every one of the 160 review findings gets a recorded disposition
version: '1'
status: accepted
section: Phase 75 — CRX Component Review Remediation (D-CRX)
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
Every one of the 160 review findings gets a recorded disposition (BUILT / COVERED / SUPERSEDED / DEFERRED / REJECTED) — dropped items are documented, not lost. Disposition of a "missing" finding is verified against the live tree before recording, because the reviews repeatedly flagged existing capabilities as absent.
