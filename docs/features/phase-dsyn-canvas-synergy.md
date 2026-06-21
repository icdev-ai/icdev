# Phase DSYN — DIC Canvas Synergy

**Status:** Complete  
**Classification:** CUI // SP-CTI  
**Date:** 2026-06-12

## Summary

DSYN wires every major ICDEV canvas into the Document Intelligence Canvas (DIC) so that state changes across the platform automatically surface as AI-drafted document update suggestions for human review. The integration is event-driven, idempotent, and HITL-gated — no content is auto-published.

## Architecture

```
Canvas State Change
    ↓  (event emitter in each canvas module)
canvas_events table  (event_type = "{canvas}.{entity}.{action}")
    ↓  (dic_integration Genesis reflex — 15-min cadence)
dic_suggestions table  (status=pending, canvas_source="{canvas}")
    ↓  (HITL review — editor/reviewer in doc_detail.html)
dic_suggestion_decisions  (accepted/rejected, append-only NIST AU)
    ↓  (on accept)
dic_sections.content updated + dic_edit_history row recorded
```

## Canvases Integrated

| Canvas | Module | Events |
|--------|--------|--------|
| NDC (Network Device) | `tools/ndc/event_emitter.py` | topology.drift_detected, config.push, baseline.deviation |
| Network | `tools/network/event_emitter.py` | migration_phase_complete, anomaly_detected |
| ZIG (Zero-Trust) | `tools/security/zig/event_emitter.py` | posture_score_drop, pillar_gap_detected |
| Compliance | `tools/compliance/event_emitter.py` | finding_created, poam_overdue |
| SIPA (Integrity) | `tools/integrity/event_emitter.py` | vulnerability_found, quarantine_triggered |
| DevSecOps | `tools/devsecops/event_emitter.py` | pipeline_stage_failed, stig_finding_new |
| CloudForge | `tools/cloudforge/event_emitter.py` | resource_provisioned, runbook_executed |
| AI-ify | `tools/aiify/event_emitter.py` | canvas_scored, gap_identified |
| Crowdsource | (route in blueprint.py) | viewer suggest-edit via POST /api/sections/<id>/suggest |

## Key Components

### Event Processing
- `tools/genesis/reflexes/dic_integration.py` — 15-min Genesis reflex; polls canvas_events, resolves affected collections via `args/dic_canvas_integrations.yaml`, drafts suggestions via LLMRouter (patch_mode for targeted edits)
- `args/dic_canvas_integrations.yaml` — maps every event_type to DIC collection tags, doc_types, priority, and patch_mode

### Suggestion Lifecycle
- `tools/document_intelligence/suggestion_store.py` — create/query/decide suggestions; best-effort notification to editors/reviewers on creation
- `tools/document_intelligence/doc_generator.py` — extended with `patch_mode` and `change_context` params for targeted minimal-diff regeneration
- `tools/dashboard/templates/document_intelligence/doc_detail.html` — AI Suggestions panel, per-section suggestion badges, accept/reject HITL workflow, viewer Suggest Edit button

### Review Cadence
- `tools/genesis/reflexes/dic_review_cadence.py` — nightly reflex (1440-min); detects collections overdue for periodic review based on `review_interval_days`; emits `dic.review_overdue` canvas events, notifies editors, creates HITL kanban tasks via `task_factory`

### Cross-Document Consistency
- `tools/document_intelligence/consistency_checker.py` — KG neighbor walk (`find_related_docs`); concept extraction (`extract_changed_concepts`)
- `tools/document_intelligence/history_recorder.py` — extended `record_edit()` to propagate `dic.consistency_flag` events to related docs when `|char_delta| > 50`

## Kanban Tasks Completed

| ID | Title |
|----|-------|
| dsyn-adapt-01 | canvas_events schema review |
| dsyn-adapt-02 | dic_canvas_integrations.yaml seed |
| dsyn-adapt-03 | canvas_adapter.py integration resolver |
| dsyn-adapt-04 | suggestions API (list/accept/reject) |
| dsyn-reflex-01 | genesis_config.yaml reflex entry |
| dsyn-reflex-02 | dic_integration reflex (full) |
| dsyn-emit-01 | NDC event emitter + injections |
| dsyn-emit-02 | Network event emitter + injections |
| dsyn-emit-03 | ZIG event emitter + injections |
| dsyn-emit-04 | Compliance event emitter + injections |
| dsyn-emit-05 | SIPA event emitter + injections |
| dsyn-emit-06 | DevSecOps event emitter + injections |
| dsyn-emit-07 | CloudForge event emitter |
| dsyn-emit-08 | AI-ify event emitter + injections |
| dsyn-patch-01 | patch_mode + change_context in doc_generator |
| dsyn-patch-02 | suggestion_store notification wiring |
| dsyn-patch-03 | Suggestion review panel UI |
| dsyn-suggest-01 | Viewer suggest-edit workflow |
| dsyn-suggest-02 | Review cadence reflex |
| dsyn-suggest-03 | Auto-create HITL kanban task on overdue |
| dsyn-consist-01 | KG neighbor walk for cross-doc consistency |
| dsyn-consist-02 | Consistency flag propagation in record_edit |
| dsyn-vv-01 | End-to-end V&V smoke test |

## Test Coverage

| File | Tests | Covers |
|------|-------|--------|
| `tests/test_dsyn_reflex_registration.py` | 14 | Reflex registration, config |
| `tests/test_dsyn_emit_ndc.py` | 8 | NDC emitter |
| `tests/test_dsyn_emit_network.py` | 4 | Network emitter |
| `tests/test_dsyn_emit_zig.py` | 4 | ZIG emitter |
| `tests/test_dsyn_emit_compliance.py` | 6 | Compliance emitter |
| `tests/test_dsyn_emit_sipa.py` | 4 | SIPA emitter |
| `tests/test_dsyn_emit_devsecops.py` | 5 | DevSecOps emitter |
| `tests/test_dsyn_emit_cloudforge.py` | 4 | CloudForge emitter |
| `tests/test_dsyn_emit_aiify.py` | 6 | AI-ify emitter |
| `tests/test_dsyn_patch_mode.py` | 23 | patch_mode, notifications, API filtering |
| `tests/test_dsyn_suggest.py` | 11 | Viewer suggest-edit route |
| `tests/test_dsyn_review_cadence.py` | 16 | Review cadence reflex |
| `tests/test_dsyn_hitl_task.py` | 8 | HITL task idempotency |
| `tests/test_dsyn_consistency.py` | 16 | KG walk, concept extraction, flag propagation |
| `tests/test_dsyn_vv_smoke.py` | 19 | End-to-end synergy loop + artifact inventory |

**Total: ~148 tests**

## Security & Compliance

- All new tables append-only where required (dic_suggestion_decisions, dic_edit_history)
- CUI // SP-CTI classification headers on all new Python modules
- Event emitters wrapped in try/except — failures never block canvas operations
- HITL gate enforced — no suggestion is auto-applied; editor/reviewer decision required
- Notifications best-effort — notification failure never blocks suggestion creation
