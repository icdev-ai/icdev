# CUI // SP-CTI
# AADC Phase 3 — AADC-Unique Features

**Phase:** 3  
**Status:** Complete  
**Committed:** 2026-05-03  
**Classification:** CUI // SP-CTI (IL4/IL5)

---

## Overview

Phase 3 delivers four AADC-exclusive capabilities that have no direct equivalent in LangGraph, CrewAI, or Haystack: safety redundancy graph analysis, multi-agent coordination matrix, model provenance chain tracking, and agent behavior simulation. These features provide security reviewers, AI architects, and compliance officers with ICDEV-native analytical tools built directly into the design canvas.

Canvas route: `/agentic-ai`  
DB: `data/agentic_ai_canvas.db` (migration 106)

---

## New Modules

| Module | Purpose |
|--------|---------|
| `tools/agentic_ai_canvas/safety_redundancy.py` | Per-agent safety coverage analysis (protected/unprotected) |
| `tools/agentic_ai_canvas/coordination_matrix.py` | N×N agent communication matrix + topology classification |
| `tools/agentic_ai_canvas/model_provenance.py` | Provenance chain extraction + GPL/proprietary compliance flags |
| `tools/agentic_ai_canvas/simulation_engine.py` | BFS execution trace — halts at HITL/circuit-breaker, marks filters |

---

## Database Schema (Migration 106)

```sql
aadc_safety_graphs     -- id, design_id, score, protected_count, unprotected_count, analysis_json
aadc_agent_simulations -- id, design_id, start_node_id, trace_json, decisions_json, status, halted_by
```

---

## Feature: Safety Redundancy Graph (p3-02/03)

**What it does:** For every agent node in the design, traces backwards through the graph to find upstream safety or governance nodes. Produces:
- `protected_agents` — agent nodes with at least one safety predecessor
- `unprotected_agents` — agent nodes with no safety coverage
- `safety_chains` — connected sequences of safety nodes (multi-layer pipelines)
- `score` — protected / total × 100%

**UI:** "🛡 Safety Map" toolbar button activates an overlay:
- Green halo → protected agent
- Red halo → unprotected agent  
- Blue halo → safety/governance node
- Score badge appears at bottom center

**API:** `GET /agentic-ai/api/designs/<id>/safety-redundancy`  
Each call persists a snapshot to `aadc_safety_graphs`.

---

## Feature: Multi-Agent Coordination Matrix (p3-04/05)

**What it does:** Builds an N×N matrix showing how agents communicate:
- `direct` — direct edge between two agents
- `indirect` — two-hop path through a non-agent intermediary
- `none` — no reachability

Classifies topology: **mesh** (fully connected), **hub-spoke** (one hub to all), **pipeline** (linear chain), **hierarchical** (layered tree).

**UI:** "⊡ Coord. Matrix" in the AADC dropdown opens a modal with:
- Topology badge (color-coded)
- Hub node label(s)
- Isolated agent warnings
- Color-coded matrix table (● direct, ◌ indirect)

**API:** `GET /agentic-ai/api/designs/<id>/coordination-matrix`

---

## Feature: Model Provenance Chain (p3-06)

**What it does:** Extracts provenance metadata from each model-type node's properties:
- `model_source` — e.g., "OpenAI GPT-4", "Meta Llama-3"
- `training_data` — e.g., "Common Crawl + RedPajama"
- `model_version` — e.g., "gpt-4-turbo-2024-04-09"
- `model_license` — e.g., "Apache-2.0", "Proprietary"

Surfaces compliance flags:
- `proprietary-model` (INFO) — needs FedRAMP/DoD authorization check
- `gpl-model` (WARN) — copyleft may apply to derivatives
- `missing-training-data` (LOW) — required by NIST AI RMF MAP-2
- `missing-model-source` (LOW) — incomplete provenance chain

**UI:**
- Properties panel: model nodes show 4 provenance input fields
- "🔗 Model Provenance" in the AADC dropdown opens provenance chain modal
- Flags listed below the chain table

**APIs:**
- `GET /agentic-ai/api/designs/<id>/provenance`
- `PUT /agentic-ai/api/designs/<id>/nodes/<nid>/provenance`

---

## Feature: Agent Behavior Simulation (p3-07/08)

**What it does:** BFS-traces execution from a selected start node through the graph:
- **Halts** at `hitl-gate`, `approval-workflow`, `caio-override`, `circuit-breaker` — execution suspends, requires human
- **Filters** at guardrails, PII detectors, sanitizers — marks step as "filtered" but continues
- **Passes** through all other nodes
- Stops at `max_steps=50` to prevent infinite loops

Returns `trace` (ordered activation steps) + `decisions` (halt/filter events).

**UI:** "▶ Simulate" toolbar button opens a right-side panel:
- Start node dropdown (all nodes in graph)
- "▶ Run Simulation" button
- Results panel: step-by-step trace with 🟢 activated / 🟡 filtered / 🔴 halted icons
- Canvas nodes animate with glowing borders during playback (120ms per step)
- "Clear" button resets all highlights

**APIs:**
- `POST /agentic-ai/api/designs/<id>/simulate`
- `GET /agentic-ai/api/designs/<id>/simulations`

---

## Simulation CSS Classes

```css
.sim-active   { box-shadow: 0 0 0 3px #22c55e, 0 0 12px #22c55e88 !important; }
.sim-halted   { box-shadow: 0 0 0 3px #f97316, 0 0 12px #f9731688 !important; }
.sim-filtered { box-shadow: 0 0 0 3px #fbbf24, 0 0 12px #fbbf2488 !important; }
```

---

## Coherence Gate (final)

```
total_checks: 18
failed_checks: 0
gate_passed: true
```
