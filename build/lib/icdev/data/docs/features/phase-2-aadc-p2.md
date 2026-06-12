# CUI // SP-CTI
# Phase 2 — AADC Ecosystem Wiring

**Canvas:** Agentic AI Design Canvas (AADC)  
**Epic key:** aadc-p2  
**Shipped:** 2026-05-01  
**Classification:** CUI // SP-CTI

---

## Summary

Phase 2 wires the AADC into the broader ICDEV™ ecosystem: every design save emits an activity feed event, a Genesis reflex consumer processes AADC assessment findings into the knowledge graph, agent nodes are auto-synced to the MCP registry, assessment findings are surfaced in the fine-tuning dashboard, and Kanban task status badges appear on linked designs.

---

## Features Shipped

### 1. Activity Feed Events (`tools/agentic_ai_canvas/events.py`)
- New DB table: `aadc_design_events` — append-only (NIST AU-2 compliant)
- Events emitted on: design save, assessment run, simulation run, workflow launch, risk item create/update
- Fields: design_id, event_type, payload_json, created_at
- Powers the AADC activity feed panel in canvas.html

### 2. Genesis Reflex Consumer
- `tools/genesis/reflexes/aadc.py` — reflex that fires on new AADC assessment events
- Promotes high-confidence AADC assessment findings to Genesis knowledge graph (GKP nodes)
- Runs on Genesis 3-hour cadence; uses COOLDOWN_HOURS guard to prevent duplicate promotions
- Activity feed events in `aadc_design_events` trigger the reflex

### 3. MCP Registry Auto-Populate (`tools/agentic_ai_canvas/mcp_sync.py`)
- `sync_agent_nodes_to_mcp(design_id, nodes)` — registers each `mcp-server` / `mcp-gateway` node in the MCP tool registry
- Called on every design save
- Allows MCP-aware tools elsewhere in ICDEV™ to discover canvas-defined MCP agents

### 4. Fine-Tuning Dashboard Linkage (`tools/agentic_ai_canvas/ft_linkage.py`)
- `get_fine_tuning_summary(design_id)` — surfaces AADC assessment findings as training signal candidates
- Findings with severity HIGH/CRITICAL are flagged as candidate fine-tuning examples
- Visible in the Fine-Tuning dashboard under "AADC Signals" tab

### 5. Kanban Back-Sync
- Design cards on index.html show linked Kanban task status badge if `loop_id` is set
- Badge states: scheduled / in_progress / done / failed
- `aadc_loop_links` table maps design_id → loop_id (created when design is launched via loop_engine)
- Canvas.html toolbar shows task status inline when a design is linked to a running Kanban task

---

## New Files

| File | Purpose |
|------|---------|
| `tools/agentic_ai_canvas/events.py` | Activity feed event emitter |
| `tools/agentic_ai_canvas/mcp_sync.py` | MCP registry sync for agent nodes |
| `tools/agentic_ai_canvas/ft_linkage.py` | Fine-tuning dashboard linkage |
| `tools/genesis/reflexes/aadc.py` | Genesis reflex consumer for AADC events |

---

## New DB Tables

| Table | Purpose |
|-------|---------|
| `aadc_design_events` | Append-only activity feed (NIST AU-2) |

---

## New API Routes

| Method + Route | Purpose |
|----------------|---------|
| `GET /agentic-ai/api/designs/<id>/events` | List activity feed events for a design |

---

*CUI // SP-CTI — ICDEV™ AADC Phase 2*
