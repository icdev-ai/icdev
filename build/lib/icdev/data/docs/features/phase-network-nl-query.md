# Network Canvas: Natural Language Query over Topology

**Phase:** Tier 4 — Network Canvas Research
**Status:** Implemented
**Task ID:** task-999851c692

---

## Overview

Allows users to ask plain-English questions about any Network Canvas topology and receive structured, accurate answers — powered by deterministic graph algorithms with a local LLM (Ollama) fallback for open-ended questions.

**Example queries:**
- `"Show all paths between Core-Router and Web-Server"`
- `"What happens if Core-Switch goes down?"`
- `"How many firewalls are in this topology?"`
- `"What is directly connected to Edge-Firewall?"`
- `"Are there any CAT1 STIG findings?"`

---

## Architecture

```
User Question
      │
      ▼
QueryClassifier (regex, no LLM)
      │
      ├─ path      → PathQueryEngine (NetworkX all_simple_paths, BFS)
      ├─ failure   → FailureQueryEngine (articulation_points, blast radius)
      ├─ neighbor  → NeighborQueryEngine (adjacency lookup)
      ├─ inventory → InventoryQueryEngine (type counting/filtering)
      ├─ compliance→ ComplianceQueryEngine (nc_compliance_findings DB)
      └─ general   → LLMQueryEngine (Ollama qwen3.5, compact topology context)
```

**Air-gap safe:** All 5 deterministic engines require zero LLM. The Ollama fallback is used only for open-ended questions and gracefully reports unavailability if Ollama is offline.

---

## New Files

| File | Purpose |
|------|---------|
| `tools/network/nl_query.py` | Core NL query engine (600 lines) |
| `tests/e2e_network_nl_query.py` | 34 unit/integration tests |

---

## New API Endpoints

### `POST /api/nl-query/<topology_id>`

**Request:**
```json
{ "question": "Show all paths between Router-A and Server-B" }
```

**Response:**
```json
{
  "answer": "Found **2** path(s) between **Router-A** and **Server-B**...",
  "intent": "path",
  "engine": "path",
  "data": {
    "source": "Router-A",
    "target": "Server-B",
    "path_count": 2,
    "shortest_path": ["Router-A", "Core-Switch", "Server-B"],
    "shortest_hops": 2,
    "all_paths": [...]
  },
  "topology_name": "Production Network",
  "node_count": 42,
  "edge_count": 38,
  "query_id": "uuid"
}
```

### `GET /api/nl-query/<topology_id>/history?limit=20`

Returns recent queries for a topology from `nc_query_log`.

---

## New DB Table

**`nc_query_log`** — append-only query history:
- `id`, `topology_id`, `question`, `intent`, `answer`, `engine`, `ts`

---

## Query Engines

### PathQueryEngine
- Converts JointJS graph_json → NetworkX undirected graph
- Uses `nx.all_simple_paths(cutoff=8)` to find all paths
- Reports shortest path, hop count, all paths (up to 5 shown)
- Detects disconnected segments (no path found)

### FailureQueryEngine
- Removes named device from graph copy
- Checks `nx.articulation_points()` — is it a critical bridge?
- Reports: direct neighbors, isolated devices, network segments before/after

### NeighborQueryEngine
- Simple adjacency lookup via NetworkX neighbors
- Reports neighbor labels and device types

### InventoryQueryEngine
- Detects device type keywords in question (router, switch, firewall, server, etc.)
- Falls back to full inventory summary (node/edge counts by type)

### ComplianceQueryEngine
- Queries `nc_compliance_findings` table for the topology
- Filters by CAT1/CAT2/CAT3 if mentioned in question
- Returns severity counts + finding details

### LLMQueryEngine (Ollama)
- Serializes topology as compact text (nodes + links, truncated at 200 nodes)
- Sends to local Ollama (`qwen3.5:latest` by default)
- Strips `<think>` blocks from qwen3.5 reasoning output
- Graceful degradation: returns helpful error if Ollama offline

---

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `OLLAMA_NL_QUERY_MODEL` | `qwen3.5:latest` | LLM model for general queries |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_NL_QUERY_TIMEOUT` | `60` | Request timeout (seconds) |
| `NL_QUERY_MAX_NODES` | `200` | Max nodes in LLM context |

---

## Test Results

**34 tests, 34 passed** in 1.36s (no Ollama required for CI):

- `TestTopologyGraphAdapter` (10 tests) — parsing, look-ups, compact text
- `TestQueryClassifier` (9 tests) — intent detection
- `TestPathEngine` (3 tests) — path finding, no-path, hop count
- `TestFailureEngine` (3 tests) — critical node, neighbors, isolated
- `TestNeighborEngine` (2 tests) — connected devices, isolated
- `TestInventoryEngine` (3 tests) — type count, list, summary
- `TestEdgeCases` (4 tests) — empty question, invalid topo, query logging, query_id
