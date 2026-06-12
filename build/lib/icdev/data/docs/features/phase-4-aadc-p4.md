# CUI // SP-CTI
# AADC Phase 4 — Competitive Edge Features

**Phase:** 4  
**Status:** Complete  
**Committed:** 2026-05-03  
**Classification:** CUI // SP-CTI (IL4/IL5)

---

## Overview

Phase 4 extends the Agentic AI Design Canvas (AADC) with competitive-edge capabilities that bring it to parity with LangGraph, CrewAI, and Haystack patterns while adding ICDEV-native compliance scoring. Eight new modules were added covering checkpoint/fork state management, parallel execution groups, observability trace nodes, A2A bridge + sandbox assessment, and PII/trusted-monitor safety extensions.

Canvas route: `/agentic-ai`  
DB: `data/agentic_ai_canvas.db` (migration 105)

---

## New Modules

| Module | Purpose |
|--------|---------|
| `tools/agentic_ai_canvas/checkpoint_manager.py` | Save, list, restore, fork, and delete design checkpoints (LangGraph pattern) |
| `tools/agentic_ai_canvas/parallel_graph.py` | Named parallel execution swim-lanes; fork/join structural validation |
| `tools/agentic_ai_canvas/observability_nodes.py` | Per-agent trace/span/metrics coverage scoring (Haystack/OTel pattern) |
| `tools/agentic_ai_canvas/a2a_sandbox.py` | A2A bridge audit-logger check + sandbox-exec guard-rail check |
| `tools/agentic_ai_canvas/safety_extensions.py` | Trusted monitor presence check + PII field detector → redaction-engine wiring |
| `tools/agentic_ai_canvas/safety_layer.py` | In-process circuit breaker singleton per design |

---

## New Node Types (16)

### Execution (purple #a855f7)
`checkpoint`, `fork`, `join`, `parallel-group`

### Observability (cyan #06b6d4)
`trace-collector`, `span-recorder`, `metrics-emitter`

### Memory (extended)
`working-memory`, `semantic-cache`, `conversation-history`

### Tool/MCP (extended)
`a2a-bridge`, `sandbox-exec`

### Safety (extended)
`trusted-monitor`, `pii-field-detector`

---

## Database Schema (Migration 105)

```sql
aadc_checkpoints   -- id, design_id, node_id, label, graph_json, created_by, created_at
aadc_parallel_groups -- id, design_id, label, color, node_ids_json, created_at, updated_at
```

---

## Phase 4 Scoring Formula

When Phase 4 node types are present in the design:

```
score = (NIST_RMF × 0.40) + (OWASP × 0.40) + (P4_avg × 0.20)
```

Where `P4_avg` is the average of whichever Phase 4 sub-scores are applicable (`obs_score`, `a2a_score`, `safety_ext_score`). Sub-scores return `None` (not 0) when their node types are absent, so designs without observability or A2A nodes are not penalized.

When no Phase 4 nodes are present, falls back to:

```
score = (NIST_RMF × 0.50) + (OWASP × 0.50)
```

---

## Phase 4 Compliance Rules

| Rule ID | Check | Severity |
|---------|-------|----------|
| p4-obs-trace-coverage | Every agent-type node should have trace-collector downstream | MEDIUM |
| p4-obs-span-missing | Agent nodes without span-recorder lose telemetry granularity | LOW |
| p4-obs-metrics-missing | Agent nodes without metrics-emitter lose runtime observability | LOW |
| p4-a2a-audit | a2a-bridge must have audit-logger as downstream node | HIGH |
| p4-sandbox-guard | sandbox-exec must have guardrail or input-sanitizer upstream | HIGH |
| p4-trusted-monitor | autonomous-agent present → trusted-monitor must exist anywhere | HIGH |
| p4-pii-field | pii-field-detector must connect to redaction-engine downstream | HIGH |
| p4-exec-checkpoint | Multi-agent designs without checkpoint nodes lose state recovery | LOW |

---

## Canvas UI Additions

### Checkpoints Drawer
- `💾 Checkpoints` toolbar button toggles a slide-in drawer
- Drawer lists all saved checkpoints with timestamps and restore/fork/delete actions
- "Save Checkpoint" button snapshots the current graph state with a user label

### Streaming Toggle
- LLM and LLM-local nodes expose a `⚡ STREAM` badge in the canvas when `streaming: true`
- Properties panel shows a toggle checkbox for these node types
- `streaming` is stored as a node-level property in `graph_json` (no extra DB column needed)

### Parallel Groups
- "⊞ Group Selected (Parallel)" in the AADC dropdown creates a named swim-lane
- Groups render as dashed purple rectangles behind their member nodes
- `POST /agentic-ai/api/designs/<id>/parallel-groups` persists groups; `validate-parallel` checks fork/join structure

### Compliance Legend
- Execution category (purple #a855f7) and Observability category (cyan #06b6d4) pills added

---

## API Endpoints Added (11)

| Method | Route | Purpose |
|--------|-------|---------|
| `GET` | `/agentic-ai/api/designs/<id>/checkpoints` | List checkpoints |
| `POST` | `/agentic-ai/api/designs/<id>/checkpoints` | Save checkpoint |
| `POST` | `/agentic-ai/api/designs/<id>/checkpoints/<cid>/restore` | Restore to checkpoint |
| `POST` | `/agentic-ai/api/designs/<id>/checkpoints/<cid>/fork` | Fork new design |
| `DELETE` | `/agentic-ai/api/designs/<id>/checkpoints/<cid>` | Delete checkpoint |
| `GET` | `/agentic-ai/api/designs/<id>/parallel-groups` | List groups |
| `POST` | `/agentic-ai/api/designs/<id>/parallel-groups` | Create group |
| `PUT` | `/agentic-ai/api/designs/<id>/parallel-groups/<gid>` | Update group |
| `DELETE` | `/agentic-ai/api/designs/<id>/parallel-groups/<gid>` | Delete group |
| `POST` | `/agentic-ai/api/designs/<id>/validate-parallel` | Validate fork/join structure |

---

## Smoke Test Results (2026-05-03)

```
Design: "Phase 4 Smoke Test"
Nodes: 8 (autonomous-agent, trusted-monitor, a2a-bridge, audit-logger,
          sandbox-exec, guardrail, pii-field-detector, redaction-engine)
Edges: 5

Overall score:  76.6%
NIST RMF:       75.0%
OWASP LLM:      80.0%
P4 obs_score:   66.7%   (no trace-collector or span-recorder present)
P4 a2a_score:   100.0%  (audit-logger wired)
P4 safety_ext:  100.0%  (trusted-monitor present, PII → redaction-engine wired)
```

---

## Coherence Gate (final)

```
total_checks: 18
failed_checks: 0
gate_passed: true (0 failures, 1 pre-existing warn on unrelated profile_theme)
```
