---
id: D-CRX-5
title: Kanban SLA/deadline fields
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
Kanban SLA/deadline fields (`due_date`, `sla_hours`, migration 285) + cycle-time/velocity metrics (crx-kan-01 #677). Notification routing/escalation/per-user-prefs extend the existing `notification_service` (crx-not-01 #684); PagerDuty/Opsgenie on-call integration REJECTED (external-SaaS, no demand).
