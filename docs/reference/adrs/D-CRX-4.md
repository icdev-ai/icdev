---
id: D-CRX-4
title: 'DB observability/retention built on the existing storage layer: query/pool health metrics'
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
DB observability/retention built on the existing storage layer: query/pool health metrics (crx-db-02 #681), config-driven retention/archival (crx-db-03 #700, `retention_sweep` reflex). PostgreSQL-native RLS shipped as a go/no-go **spike only** (crx-db-01 #693), sanitized for the public repo (#702) — the app-level predicate remains the authoritative boundary.
