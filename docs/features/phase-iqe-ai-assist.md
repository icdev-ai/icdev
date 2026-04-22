# IQE AI Assist — NL→IQE Translation

**Phase:** IQE AI Assist  
**Epic:** `dt-iqe` (tasks dt-iqe-14 through dt-iqe-19)  
**Shipped:** 2026-04-21  
**Status:** Done

## What Was Built

Natural-language-to-IQE translation layer and Query Topology UI tab for the Network Digital Canvas twin page.

### Components

| File | Purpose |
|------|---------|
| `tools/iqe/nl_to_iqe.py` | `nl_to_iqe(question, collections) -> {iqe, explanation}` via LLMRouter |
| `tools/iqe/adapters/ndc.py` | NDC graph_json IQE adapters: `network.nodes`, `network.edges`, `network.snapshots` |
| `tools/network/blueprint.py` | POST `/api/twin/<topo_id>/iqe-query` endpoint |
| `tools/dashboard/templates/network/twin.html` | "Query Topology" tab with textarea, IQE preview, results table |
| `icdev/tools/...` | Mirrored via companion sync |

### API

```
POST /api/twin/<topo_id>/iqe-query
Body: { "question": "show all routers", "execute": true }
Response: { "iqe": "foreach n in network.nodes ...", "explanation": "...", "results": [...], "row_count": N }
```

### UI

Second tab "Query Topology" alongside "Generate Delta" in the twin chat panel:
- Textarea for plain-English question
- Run Query button + spinner
- IQE preview (`<pre>` element)
- Results table rendered from JSON array
- Explanation caption

## Relation to Forward Networks NQE

IQE v0.1 shipped 2026-04-18 as ICDEV's NQE equivalent (`foreach / where / select` grammar). This phase wires the NL→IQE translation so users can query network topology in plain English — equivalent to Forward Networks' "Forward AI" agentic query capability.

## V&V

- 79 pytest tests pass (`tests/test_iqe_nl_to_iqe.py`, `tests/test_iqe_network_adapters.py`)
- Selenium E2E: Query Topology tab renders, `iqeQueryInput` visible after tab click, 0 JS errors
- Coherence gate: 16/16 checks pass
- Companion sync: complete
