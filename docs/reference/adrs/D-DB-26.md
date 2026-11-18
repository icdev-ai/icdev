---
id: D-DB-26
title: Extended SQL translation layer
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
Extended SQL translation layer (4 new rules): `LIKE` → `ILIKE` for case-insensitive matching, `GROUP_CONCAT` → `string_agg()`, `GLOB` → `~` regex, `last_insert_rowid()` → `lastval()`. All 13 translation rules in `translate_sql()`.
