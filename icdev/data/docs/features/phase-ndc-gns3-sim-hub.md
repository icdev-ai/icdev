# CUI // SP-CTI
# Feature: Studio Simulation Hub — Phase 6

**Phase:** Phase 6 — Studio Simulation Hub  
**Status:** Complete (2026-05-10)  
**Classification:** CUI // SP-CTI  
**Route:** `/studio/sim`

---

## Overview

A dedicated visibility and control page for GNS3 simulation state across all 12 ICDEV design canvases. Phase 6 completes the GNS3 integration arc (Phases 1–5) by adding the missing UI layer — operators can see gate status, probe results, traffic flow reachability, and training data counts at a glance, and trigger simulations without leaving the browser.

---

## Architecture

```
/studio/sim (GET)
        │
        ▼
tools/studio/sim/sim_hub.py         ← get_all_canvas_statuses() reads artifact dirs
        │                              get_ft_dataset_counts() reads ft_datasets DB table
        │                              run_canvas_sim(canvas) spawns gns3_sim.py subprocess
        ▼
data/studio_artifacts/<canvas>/training/*/training_pair.json
        │
        ▼
tools/dashboard/templates/studio/sim_hub.html   ← 12-canvas grid, auto-refresh every 10s
```

### API surface

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/studio/sim` | Simulation Hub page |
| GET | `/api/studio/sim/status` | JSON — per-canvas last run status (12 items) |
| POST | `/api/studio/sim/<canvas>/run` | Launch one canvas simulation (background subprocess) |
| POST | `/api/studio/sim/run-all` | Launch all 12 canvas simulations in parallel |

---

## Canvas Cards

Each of the 12 canvas cards shows:

| Field | Source |
|-------|--------|
| Gate (PASS/WARN/FAIL/NEVER) | `training_pair.json → output.gate` |
| Last run timestamp | `training_pair.json` file mtime |
| Nodes deployed | `output.nodes_deployed` |
| Links deployed | `output.links_deployed` |
| Probe pass/total | `output.probes_passed / probes_total` |
| Mode (DUAL/GNS3/CLOUD/DRY) | `output.mode` (first 4 chars) |
| Traffic flows reachable/tested | `output.traffic_flows_reachable / tested` |
| Training pairs on disk | Count of `training_pair.json` files in artifact dir |
| FT DB examples | `ft_datasets.example_count` where `name = icdev-<canvas>-sim` |

---

## Components

| File | Role |
|------|------|
| `tools/studio/sim/sim_hub.py` | Backing module: reads artifact dirs, spawns sims |
| `tools/dashboard/templates/studio/sim_hub.html` | Page template: 12-canvas grid + JS auto-refresh |
| `tools/dashboard/app.py` | Routes: `/studio/sim`, `/api/studio/sim/*` |
| `tools/dashboard/templates/base.html` | Nav: Studio → Simulation Hub + `PATH_CANVAS` entry |
| `tools/iqe/adapters/studio_sim.py` | IQE adapter: `sim.statuses`, `sim.training_pairs`, `sim.probes` |
| `context/iqe/queries/studio_sim/seed_queries.json` | 5 seed IQE queries |
| `tools/manifest/icdev-studio-low-code-no-code-platform.md` | Manifest entry |

---

## IQE Collections

| Collection | Contents |
|-----------|---------|
| `sim.statuses` | All 12 canvas last-run dicts (filterable by `gate`, `canvas`) |
| `sim.training_pairs` | Canvas, training_examples, gate |
| `sim.probes` | Canvas, probes_passed, probes_total, probes_failed (for canvases with probe data) |

Sample questions:
- "Which canvases have a PASS gate?"
- "Show me simulation probe results for all canvases"
- "How many training pairs does each canvas have?"
- "Which canvases have never been simulated?"
- "What is the traffic flow reachability across all canvases?"

---

## Build Sequence (Phases 1–6)

| Phase | Deliverable |
|-------|-------------|
| 1 | DDC topology builder + gns3_sim.py executor + base_topology.py |
| 2 | NDC/SDC/BDC topology builders + GNS3 panel in execution viewer + training pipeline |
| 3 | 8 remaining canvas topology builders with artifact-aware parsing |
| 4 | training_exporter.py → ft_datasets integration + Genesis sim_training_export reflex |
| 5 | GNS3TrafficEngine canonicalization → canvas_traffic_engine.py (12-canvas ZTP+traffic) |
| **6** | **Studio Simulation Hub (`/studio/sim`) — this phase** |

---

## V&V

- `python tools/studio/sim/sim_hub.py --status --json` returns 12-item list; no exceptions
- `/studio/sim` renders without error (all 12 canvas cards present)
- `/api/studio/sim/status` returns 200 with 12-item JSON array
- Nav "Studio → Simulation Hub" navigates to `/studio/sim`
- Coherence gate: passes
- Companion sync: complete
# CUI // SP-CTI
