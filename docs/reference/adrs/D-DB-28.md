---
id: D-DB-28
title: Migrated 8 production tools from direct sqlite3.connect() to get_connection()
version: '1'
status: accepted
section: D-DB — Database & Storage
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
Migrated 8 production tools from direct `sqlite3.connect()` to `get_connection()` — ensures all SQL goes through the translation layer. Remaining direct calls are infrastructure-only (init, migration, backup, code generators).
