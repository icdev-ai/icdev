---
id: D-CRX-3
title: Genesis reflex hardening is real
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
Genesis reflex hardening is real (not stale): shared `get_connection` across reflexes matched prior PG lock-storm incidents. Fixed via per-reflex connection scope (crx-gen-01 #657), failure-health alerting + genesis_audit indexes (crx-gen-02 #668, migration 284), and `depends_on` topo-order + resource caps (crx-gen-03 #673).
