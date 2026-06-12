# NDC Path Reachability Analysis

**Phase:** NDC Path Reachability  
**Epic:** `dt-ndc` (tasks dt-ndc-01 through dt-ndc-05)  
**Shipped:** 2026-04-21  
**Status:** Done

## What Was Built

BFS-based path reachability engine for the Network Digital Canvas, with ACL simulation, fuzzy node resolution, and a Path Analysis panel on the twin page.

### Components

| File | Purpose |
|------|---------|
| `tools/network/path_analyzer.py` | `find_paths(src_id, dst_id, graph, max_depth=10) -> dict` |
| `tools/network/blueprint.py` | POST `/api/twin/<topo_id>/analyze-path` endpoint |
| `tools/dashboard/templates/network/twin.html` | Path Analysis panel: src/dst inputs, verdict banner, paths table |
| `tests/test_path_analyzer.py` | Unit tests: direct path, no path, ACL block, fuzzy resolve, cycle detection |
| `tests/fixtures/test_topology.json` | 5-node test topology fixture |
| `icdev/tools/...` | Mirrored via companion sync |

### API

```
POST /api/twin/<topo_id>/analyze-path
Body: { "src": "dc-core-r1", "dst": "web-server-01", "max_depth": 10 }
Response: {
  "src": "dc-core-r1", "dst": "web-server-01",
  "reachable": true,
  "blocked_by_acl": false,
  "path_count": 2,
  "paths": [
    { "hops": ["dc-core-r1", "transit-1", "web-server-01"], "hop_count": 2, "acl_blocked": false, "protocols": ["tcp"] }
  ]
}
```

### UI

Collapsible Path Analysis panel on the twin page (`#pathPanel`):
- Source node input with datalist autocomplete from loaded topology
- Destination node input with datalist autocomplete
- Analyze Path button + spinner
- Verdict banner: **REACHABLE** (green) / **ALL PATHS ACL-BLOCKED** (orange) / **UNREACHABLE** (red)
- Paths table: hop count, protocols, ACL-blocked indicator per path
- Resolve note paragraph for fuzzy-matched labels

## ACL Simulation

If a graph edge has `acl_rules` containing a `deny` action, any path traversing that edge is marked `acl_blocked: true`. If all paths are ACL-blocked, `reachable: false` and `blocked_by_acl: true` in the response.

## Fuzzy Node Resolution

Uses `_resolve_node_id()` from `tools.network.twin` — 4-pass: exact ID → exact label → substring match → token overlap with abbreviation expansion (e.g., "tgw" → "transit gateway").

## Relation to Forward Networks

Forward Networks' blast radius and path reachability use header-space analysis (mathematical model). This implementation uses BFS over `graph_json` with ACL edge filtering — equivalent capability for ICDEV's canvas-based topologies without requiring vendor configuration ingestion.

## V&V

- Pytest: path tests pass (direct, disconnected, ACL-blocked, fuzzy, cycle)
- Selenium E2E: `#pathPanel`, `#pathSrcInput`, `#pathDstInput`, `#pathVerdictBanner`, `#pathTableBody`, `#pathResolveNote` all present; 0 JS errors
- Coherence gate: 16/16 checks pass
- Companion sync: complete
