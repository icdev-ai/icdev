# System Graph — Unified Federated Graph

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## System Graph (Sigma.js Federated Graph Platform)
Federates 6 ICDEV data sources into a single Sigma.js 3.0.2 WebGL graph at `/system-graph`. Pre-computes layout (NetworkX spring_layout) and community detection (greedy_modularity_communities) server-side for air-gap compatibility. 5-minute in-process cache; 3 REST API endpoints. Shipped 2026-05-07.

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Graph Builder | tools/system_graph/graph_builder.py | Federates 7 sources (awareness_kg, canvas_kg, kanban_deps, goals, migrations, codebase, ndc) into a unified node/edge list. The `ndc` source contributes the Network Design Canvas node with edges to its nc_*/ndc_*/ni_* table group, the /network route surface, and the Security Design Canvas (on_ndc_topology_saved coupling); topology count is optional and DB-outage-safe. Runs NetworkX spring_layout + greedy_modularity_communities. 5-min internal cache for node detail lookups. | build_graph(sources, filter_type, filter_health, filter_cluster, search), get_node_detail(node_id) | {nodes, edges, stats} JSON + per-node detail dict |
| Blueprint | tools/system_graph/blueprint.py | Flask blueprint — 4 routes + 5-min API cache on the unfiltered full graph. Filtered/searched requests bypass cache. | GET /system-graph, GET /api/system-graph/graph, GET /api/system-graph/node/<id>, GET /api/system-graph/node-types | HTML page + JSON API |
| Constants | tools/system_graph/constants.py | 13 node types with display colours, 7 edge types, 12-colour cluster palette, layout params (LAYOUT_K, LAYOUT_ITERATIONS), MAX_FULL_NODES, GRAPH_SOURCES list | (constants) | NODE_TYPES, EDGE_TYPES, CLUSTER_COLORS, GRAPH_SOURCES |
| Page Template | tools/dashboard/templates/system_graph/page.html | Sigma.js 3-pane UI: collapsible legend sidebar (13 node types + counts, client-side nodeReducer/edgeReducer filter), canvas + tooltip + zoom controls, slide-in node detail panel. JS-driven full-bleed layout. | — | Interactive WebGL graph at /system-graph |
| Vendor — Sigma.js | tools/dashboard/static/vendor/sigma/sigma.min.js | Sigma.js 3.0.2 vendored for air-gap (186 KB) | — | WebGL graph renderer |
| Vendor — Graphology | tools/dashboard/static/vendor/sigma/graphology.min.js | Graphology 0.26.0 vendored for air-gap (73 KB) | — | Graph data structure library |
