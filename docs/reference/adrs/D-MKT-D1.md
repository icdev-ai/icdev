---
id: D-MKT-D1
title: Marketplace disabled by default
version: '1'
status: accepted
section: D-MKT-D — Marketplace Defaults
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
Marketplace disabled by default (air-gapped/OSS). Enable with `ICDEV_MARKETPLACE_ENABLED=true` env var or `args/marketplace_config.yaml enabled: true`. When disabled: `is_module_enabled()` returns True (all features are core), marketplace commerce routes return 501, no token checks, no network calls, no encrypted import hooks.
