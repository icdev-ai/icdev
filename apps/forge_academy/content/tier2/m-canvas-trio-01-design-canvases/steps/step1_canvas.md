---
ontology_id: icdev:mission:m-canvas-trio-01-design-canvases:step:1
step_class: icdev:Lab
---

# The Design Canvas Trio — DDC, ODC, NDC

The Tier-3 canvas-selection lesson taught the meta-rule: **ICDEV is registry-driven, there is
no fixed "7 canvas" model, and the registry (`args/component_registry.yaml`) is the source of
truth.** This lab makes that concrete for the three **Design Canvases** you will reach for most
— and shows the discipline of returning *nothing* when a need doesn't fit.

## What each canvas actually does today

| Canvas | Key | Route | Registry purpose (verbatim) |
|--------|-----|-------|------------------------------|
| **Data Design Canvas** | `ddc` | `/data` | Data lineage, schemas, synthetic data, quality. |
| **Observability Design Canvas** | `odc` | `/observability` | Logging, monitoring, distributed tracing, SRE. |
| **Network Design Canvas** | `ndc` | `/network` | Topology, routing, capacity, redundancy, EOL analysis. |

Grounded in the real blueprints:

- **DDC** (`tools/data_canvas/blueprint.py`) — data **lineage** graphs, schema/design
  assessment, **PII scanning** and data-quality rules, governance & classification, a data
  **mesh** (domains / data products / contracts), and AI field **mapping** (`ai_mapper.py`).
- **ODC** (`tools/observability_canvas/blueprint.py`) — observability *architecture* design:
  it assesses a logging/monitoring pipeline (sources → collector → SIEM), computes **MITRE
  ATT&CK detection coverage**, generates **Sigma** detection rules, and manages SOPs/runbooks.
  (Note: MLOps / model-registry / SLO dashboards are a *different* surface — the `ohc_*` tools —
  not this canvas.)
- **NDC** (`tools/network/blueprint.py`) — network **topology** ingest, routing/BGP analysis,
  **capacity** and **EOL** prediction, redundancy/SPOF, and protocol-**migration** planning
  (MCP tools `mc_net_ingest_topology`, `mc_net_plan_protocol_migration`, `mc_net_recommend_hardware`).

## Routing a need to a canvas

The selection heuristic is simple and honest: score the request against each canvas's signal
vocabulary, pick the strongest match — and if **nothing** matches, return `None` rather than
jam a data problem into the network canvas. That is the registry-driven mindset: *when a domain
doesn't match, go back to the registry; do not force a fit.* The platform ships 30+ canvases;
these three are a representative subset, not the whole menu.

Your three functions build that router: `match_signals()` scores all three canvases,
`classify_design_need()` picks the winner (or `None`), and `route_design_request()` returns the
full decision — canvas key, verbatim purpose, and route prefix.

Open `step1_starter.py` and implement the three `TODO`s.
