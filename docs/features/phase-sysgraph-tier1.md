# Feature: Unified System Graph — Sigma.js Tier 1

**Phase:** sysgraph-tier1  
**Shipped:** 2026-05-07  
**Route:** `/system-graph`  
**Classification:** CUI // SP-CTI

---

## Summary

ICDEV previously had 5 disconnected graph systems with no unified view and JointJS rendering that degraded at ~500 nodes. This feature replaces fragmentation with a single federated graph page powered by **Sigma.js 3.0.2** WebGL rendering, capable of handling the full ICDEV system graph (3,554+ nodes, 1,611+ edges) at interactive frame rates.

Inspired by GitNexus's unified AST graph philosophy — adapted for ICDEV's Python/Flask stack without any Node.js or npm dependencies.

---

## What Was Built

### New Files
| File | Role |
|------|------|
| `tools/system_graph/__init__.py` | Module marker |
| `tools/system_graph/constants.py` | 13 node types, 7 edge types, cluster palette, layout params |
| `tools/system_graph/graph_builder.py` | Federation engine — loads 5 sources, NetworkX layout + community detection, 5-min in-process cache |
| `tools/system_graph/blueprint.py` | Flask blueprint — 5 routes + 5-min API cache layer |
| `tools/dashboard/templates/system_graph/page.html` | Sigma.js 3-pane UI (toolbar + legend sidebar + canvas + detail panel) |
| `tools/dashboard/static/vendor/sigma/sigma.min.js` | Sigma.js 3.0.2 (186KB, vendored for air-gap) |
| `tools/dashboard/static/vendor/sigma/graphology.min.js` | Graphology 0.26.0 (73KB, vendored for air-gap) |

### Modified Files
| File | Change |
|------|--------|
| `tools/dashboard/app.py` | Blueprint registration for `system_graph` |
| `tools/dashboard/templates/base.html` | "System Graph" nav link under Ops dropdown |
| `args/projects.yaml` | `sysgraph` project registered with 6 epics |

---

## Five Federated Graph Sources

| Source | Nodes | Edges | Type |
|--------|-------|-------|------|
| Awareness KG (`kg_nodes`, graph_id=`kg-icdev-self-awareness`) | 1,678 | 0 | skills, tools, reflexes, MCP servers, canvas modules |
| Canvas KG (`canvas_kg_nodes` / `canvas_kg_edges`) | 761 | 758 | NDC/SDC/ODC/BDC design entities |
| Kanban DAG (`kanban_tasks` + `kanban_task_deps`) | 1,000 | 261 | task dependency graph |
| Goals (`goals/**/*.md` directory scan) | 551 | 480 | goal→tool call edges |
| Migrations (`tools/db/migrations/` chain) | 126 | 125 | sequential migration ordering |

**Total (after dedup):** 3,554 nodes, 1,611 edges across 6 relationship types.

---

## Architecture

```
Browser → GET /system-graph
       → Jinja template renders sidebar legend + Sigma.js scripts
       → JS: fetch /api/system-graph/graph (5-min TTL cache)
                 → graph_builder.py: 5 source loaders
                 → NetworkX spring_layout() → (x,y) per node
                 → NetworkX greedy_modularity_communities() → cluster_id per node
                 → JSON response: {nodes, edges, stats}
       → Sigma.js renders WebGL canvas with pre-positioned nodes
       → Click node → GET /api/system-graph/node/<id> → sidebar detail panel
```

---

## UI Features

- **Search bar** — live filter by node label (350ms debounce)
- **Type filter dropdown** — filter to any of 13 entity types
- **Health filter** — ok / warn / error / unknown (from awareness KG health prober)
- **⟳ Refresh** — rebuild graph and update stats
- **⬡ Clusters** — toggle between community-detection cluster colors and entity-type colors
- **⊞ Fit** — animated camera reset to show all nodes
- **Legend** — 13 node types with colored dots; click to filter
- **Sidebar detail panel** — shows label, type, source, health, path, status, and up to 20 inbound/outbound neighbors
- **Hover tooltip** — label + type + health on mouseover

---

## Performance

| Metric | Value |
|--------|-------|
| Cold build (first load) | ~3,200ms |
| Cached build (5-min TTL) | ~100ms |
| Node detail (uses cache) | ~100ms |
| Sigma.js render (3,554 nodes) | <500ms |
| Canvas size | 1320×400px (fills viewport below nav) |

---

## Deferred (Tier 2)

- Evidence/confidence fields on edges (`canvas_kg_edges.confidence`)
- Blast-radius traversal API (`/api/system-graph/impact/<node_id>`)  
- Graph-augmented hybrid search (RRF fusion of BM25 + semantic + graph hop distance)
