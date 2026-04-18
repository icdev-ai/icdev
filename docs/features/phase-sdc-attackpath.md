# Phase SDC — Attack Path Twin (MVP)

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | SDC Digital Twin — Phase 1 |
| Title | Attack Path Twin: BAS-Style Replay on STRIDE/Attack Graph |
| Status | Shipped |
| Priority | P1 |
| Dependencies | SDC security_engine.py (attack path BFS), IQE executor, Caldera adapter |
| Author | ICDEV™ Architect Agent |
| Date | 2026-04-18 |

---

## 1. Problem Statement

Government and DoD system designers need to answer one critical question before an ATO
assessment: **"Can an adversary reach my IL5 data store from the internet?"** Existing
STRIDE tools label threats but do not enumerate traversal paths, score lateral movement
risk, or map findings to MITRE ATT&CK techniques an operator can actually replay in a
Breach-and-Attack Simulation environment.

The SDC Attack Path Twin closes this gap. It formalizes the Security Design Canvas threat
graph as a queryable, append-only snapshot, runs deterministic BFS path enumeration over
every attack surface, and enriches each path with ATT&CK technique IDs sourced from a
live Caldera adapter. Five seed IQE queries (data exfil, lateral-to-IL5, privilege
escalation, cross-boundary traversal, MTTR critical paths) give security engineers
instant answers without writing raw SQL.

---

## 2. Goals

1. Formalize the STRIDE/attack graph as an append-only `sdc_attack_snapshots` table
   (nodes JSON + edges JSON per component per snapshot event)
2. Enumerate all simple BFS paths from external entry points to high-value targets
   with a deterministic risk score per hop (encryption, authentication, IL boundary
   crossing, privilege level)
3. Register three IQE collections — `attack.nodes`, `attack.edges`, `attack.paths` —
   so security analysts can query the graph without writing DB-specific SQL
4. Seed five IQE rules covering the five most common adversary objectives in
   CUI/IL4/IL5 environments
5. Integrate the MITRE Caldera REST adapter to map detected abilities to ATT&CK
   technique IDs for BAS-style replay enrichment
6. Expose a stable HTTP API route (`POST /api/designs/<id>/attack-paths`) consumed by
   the SDC frontend attack-path overlay
7. Carry classification markings (IL level) on every edge and path so cross-boundary
   traversals are automatically flagged

---

## 3. Architecture

```
+--------------------------------------+
| SDC UI (security-canvas.js)          |
| Attack Path Overlay panel            |
+------------------+-------------------+
                   |
                   v  POST /api/designs/<id>/attack-paths
+--------------------------------------+
| blueprint.py  sc_api_attack_paths()  |
| → parse graph_data from request JSON |
+------------------+-------------------+
                   |
         +---------+---------+
         |                   |
         v                   v
+----------------+  +-------------------------+
| security_engine|  | caldera_adapter.py      |
| find_attack_   |  | CalderaAdapter          |
| paths(graph)   |  | ability_technique_map   |
|                |  | (cached, 5-min TTL)     |
| BFS from all   |  | graceful degradation    |
| entry nodes →  |  | when Caldera offline    |
| risk score per |  +-------------------------+
| path           |
+--------+-------+
         |
         v  on snapshot trigger
+--------------------------------------+
| sdc_attack_snapshots (DB table)      |
| id | component_id | nodes_json       |
| edges_json | created_at             |
+------------------+-------------------+
                   |
         +---------+---------+
         |         |         |
         v         v         v
  attack.nodes  attack.edges  attack.paths(src,goal)
  (IQE collection adapters — security.py)
         |
         v
  5 seed .iqe query files
  context/iqe/queries/security/
```

---

## 4. What Was Built

### New Files

| File | Purpose |
|------|---------|
| `tools/iqe/adapters/security.py` | IQE collection adapters: `attack.nodes`, `attack.edges`, `attack.paths` |
| `tools/security_canvas/caldera_adapter.py` | Read-only MITRE Caldera v2 REST adapter with ability→ATT&CK map |
| `context/iqe/queries/security/data_exfil_paths.iqe` | IQE rule: edges to database/storage nodes, risk ≥ 7, unencrypted |
| `context/iqe/queries/security/lateral_to_il5.iqe` | IQE rule: any edge whose target_il_level = 5 |
| `context/iqe/queries/security/priv_escal_paths.iqe` | IQE rule: nodes with privilege = root reachable from user-space |
| `context/iqe/queries/security/cross_boundary_paths.iqe` | IQE rule: unencrypted edges crossing classification boundaries |
| `context/iqe/queries/security/mttr_critical_paths.iqe` | IQE rule: shortest path internet→db_server for MTTR estimation |
| `tests/test_iqe_security_adapter.py` | Unit tests for IQE adapter registrations and BFS paths |
| `tests/test_iqe_security_seeds.py` | Integration tests for all 5 seed queries against fixture graph |

### Modified Files

| File | Change |
|------|--------|
| `tools/security_canvas/security_engine.py` | Added `find_attack_paths()`, `_score_attack_path()`, `_suggest_mitigations()` |
| `tools/security_canvas/blueprint.py` | Added route `POST /api/designs/<design_id>/attack-paths`; B608 suppression |
| `tools/manifest/design-canvases.md` | Registered SDC Attack Path Twin in manifest |

---

## 5. IQE Query Surface

### Collections

| Collection | Source | Grain |
|------------|--------|-------|
| `attack.nodes` | `sdc_attack_snapshots.nodes_json` | One row per node per snapshot |
| `attack.edges` | `sdc_attack_snapshots.edges_json` | One row per directed edge per snapshot |
| `attack.paths(src, goal)` | BFS over `attack.edges` in memory | One row per simple path from src to goal |

### Seed Queries

| File | Rule | Severity |
|------|------|----------|
| `data_exfil_paths.iqe` | Unencrypted edges to database/storage nodes with risk_score ≥ 7 | Critical |
| `lateral_to_il5.iqe` | Any edge reaching an IL5-classified target component | Critical |
| `priv_escal_paths.iqe` | Nodes with `privilege = root` reachable from user-privilege nodes | High |
| `cross_boundary_paths.iqe` | Unencrypted edges that cross an IL classification boundary | High |
| `mttr_critical_paths.iqe` | Shortest BFS path from internet entry to primary database (MTTR proxy) | Medium |

---

## 6. API Surface

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/designs/<design_id>/attack-paths` | Enumerate attack paths for a saved or inline graph |

### Request Body
```json
{
  "graph": {
    "nodes": [
      {"id": "internet", "label": "Internet", "node_type": "external"},
      {"id": "web_app",  "label": "Web App",  "node_type": "service"},
      {"id": "db",       "label": "DB",       "node_type": "asset-database"}
    ],
    "edges": [
      {"source": "internet", "target": "web_app", "encrypted": false, "authenticated": false},
      {"source": "web_app",  "target": "db",      "encrypted": false, "authenticated": false}
    ]
  }
}
```

### Response
```json
{
  "attack_paths": [
    {
      "id": "AP-intern-db-0",
      "path": ["internet", "web_app", "db"],
      "hops": 2,
      "risk_score": 8.2,
      "risk_level": "critical",
      "mitigations": ["Encrypt internet→web_app flow", "Add authentication on web_app→db"],
      "nodes": [
        {"id": "internet", "label": "Internet", "node_type": "external"},
        {"id": "web_app",  "label": "Web App",  "node_type": "service"},
        {"id": "db",       "label": "DB",       "node_type": "asset-database"}
      ]
    }
  ],
  "total_paths": 1,
  "critical_paths": 1
}
```

---

## 7. Database Schema

### `sdc_attack_snapshots`

```sql
CREATE TABLE IF NOT EXISTS sdc_attack_snapshots (
    id           TEXT PRIMARY KEY,
    component_id TEXT NOT NULL,
    nodes_json   TEXT NOT NULL DEFAULT '[]',
    edges_json   TEXT NOT NULL DEFAULT '[]',
    created_at   TEXT NOT NULL
);
```

The table is append-only (NIST AU). IQE adapters read from it via `get_connection()`;
no raw `sqlite3.connect()` calls.

---

## 8. Caldera Integration

`CalderaAdapter` wraps the Caldera v2 REST API using stdlib `urllib` only (no external
deps). It exposes:

- `fetch_scenarios()` — list of adversary dicts from `/api/v2/adversaries`
- `fetch_abilities()` — list of ability dicts from `/api/v2/abilities`
- `ability_technique_map` — `{ability_id: technique_id}` dict, cached in memory +
  on-disk (`caldera_cache/ability_technique_map.json`) with 5-minute TTL
- `health()` — returns `{ok: bool, url, error}` for feature-flag gating

Graceful degradation: all methods return empty collections (not exceptions) when
Caldera is unreachable, so SDC operates fully without a running Caldera instance.

---

## 9. Risk Scoring

Each attack path receives a composite risk score (0–10):

| Factor | Weight | Condition |
|--------|--------|-----------|
| Unencrypted hop | +2.0 | `encrypted == false` on any edge in path |
| Unauthenticated hop | +2.0 | `authenticated == false` on any edge |
| IL5 boundary crossing | +2.5 | Any edge `target_il_level == 5` |
| Privilege escalation | +1.5 | Any `node_type` change to root privilege |
| External entry point | +1.0 | Path starts from `node_type == external` |

Risk levels: **critical** (≥ 7.5), **high** (5.0–7.4), **medium** (2.5–4.9), **low** (< 2.5).

---

## 10. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D-SDC-1 | Append-only `sdc_attack_snapshots` | NIST AU; graph history feeds drift detection and MTTR trending |
| D-SDC-2 | IQE collections over raw SQL | Survives SQLite↔PostgreSQL migration via `get_connection()`; queries are version-controlled DSL, not hardcoded SQL |
| D-SDC-3 | BFS in-memory (not DB-side) | Attack graphs are small (< 200 nodes); in-memory BFS avoids recursive CTE portability issues across backends |
| D-SDC-4 | Caldera adapter stdlib-only | Air-gap compatibility — no PyPI dependency; graceful degradation when Caldera offline |
| D-SDC-5 | Seed queries at context/iqe/queries/security/ | Co-located with other canvas seeds; picked up by IQE test harness automatically |

---

## 11. Security Gate

**Attack Path Gate:**
- 0 critical-severity attack paths with `risk_score ≥ 7.5` from external entry to IL5 asset
- All edges reaching IL5 components must have `encrypted = true` AND `authenticated = true`
- No path with `node_type = external` → `privilege = root` in ≤ 3 hops

---

## 12. Phase Roadmap

| Phase | Status | Description |
|-------|--------|-------------|
| **1** | Shipped | BFS attack path enumeration + IQE collections + Caldera adapter + 5 seed queries |
| 2 | Backlog | Caldera scenario orchestration — trigger selected adversary scenarios from detected paths; feed execution results back into SDC as verified findings |
| 3 | Backlog | Closed-loop remediation — violation rows auto-generate blue-team runbook cards with NIST 800-53 control mappings |
| 4 | Backlog | Classification-aware boundary enforcement — paths crossing BDC-defined authorization boundaries flagged with IL/CUI markings from BDC integration |

---

## 13. Commands

```bash
# Run attack path analysis on a saved design
python -c "
from tools.security_canvas.security_engine import find_attack_paths
import json
graph = {'nodes': [...], 'edges': [...]}
result = find_attack_paths(graph)
print(json.dumps(result, indent=2))
"

# Run IQE query against live DB
python -c "
from tools.iqe.executor import Executor
from tools.iqe.parser import parse
from tools.iqe.adapters.security import edges_adapter, nodes_adapter, paths_adapter
from tools.db.storage import get_connection
ex = Executor()
ex.register_collection('attack.nodes', nodes_adapter)
ex.register_collection('attack.edges', edges_adapter)
ex.register_collection('attack.paths', paths_adapter)
q = parse(open('context/iqe/queries/security/lateral_to_il5.iqe').read())
with get_connection() as conn:
    rows = ex.run(q, conn)
print(rows)
"

# Check Caldera connectivity
python -c "
from tools.security_canvas.caldera_adapter import CalderaAdapter
a = CalderaAdapter('http://localhost:8888', api_key='ADMIN123')
print(a.health())
print(a.ability_technique_map)
"

# Run seed query tests
python -m pytest tests/test_iqe_security_seeds.py tests/test_iqe_security_adapter.py -v
```

---

**CUI // SP-CTI**
