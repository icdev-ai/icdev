# Plan: Network Migration COA Selection + Topology Diagrams + SOP Integration

## Goal

Extend the Network Device Migration wizard so it behaves like a migration engineering cockpit:

1. Auto-generate a topology diagram from the imported source config (and optional neighbor discovery).
2. Offer three Courses of Action (COAs), with **side-by-side parallel** as the safe default.
3. Capture free-text engineer context and ask yes/no questions to tailor the COA recommendation.
4. Drive port mapping, config mapping, test plan, and cutover plan based on the selected COA.
5. Surface relevant ICDEV SOPs/runbooks and index everything into RAG + KG.

## User decisions already provided

| # | Question | Answer |
|---|---|---|
| 1 | Topology source | Auto-generated from config + neighbor devices |
| 2 | Default COA | Auto-select COA-A unless engineer overrides |
| 3 | Free-text impact | LLM uses it to **rewrite** mapping proposals |
| 4 | L2 replacement case | Auto-suppress L3/IGP proposals when context indicates L2-only |
| 5 | COA change impact | Yes — changing COA resets/re-highlights port + config mapping |
| 6 | Management VLAN | Bundled into COA-A (same data + mgmt VLAN side-by-side) |
| 7 | Rollback | Each COA carries explicit rollback plan |
| 8 | Topology rendering | Interactive canvas (reuse existing JointJS network canvas) |
| 9 | SOP citations | Internal `mc_sops` + RAG/KG; external vendor links only if already in KG |
| 10 | Phasing | Implement COA selection + topology first; defer LLM recommendation tuning |

## Recommended 3 COAs

| ID | Name | Strategy | When default |
|---|---|---|---|
| **COA-A** | Side-by-Side Parallel | New device joins same data-plane VLAN and management VLAN as old; validate adjacency/forwarding before cutover. | **Default** — lowest blast radius. |
| **COA-B** | Warm Cutover | Brief coexistence; move L3 endpoints / IGP neighbors one at a time; rollback by reverting to old device. | Moderate time pressure, controllable downstream IGP, maintenance window exists. |
| **COA-C** | Cold Cutover | Old device decommissioned first; new device configured offline then swapped. | Tight window, no spare ports/VLANs, downstream IGP stable and controlled. |

## Reuse inventory (verified in codebase)

| Capability | Existing asset | How we reuse it |
|---|---|---|
| Config parsing | `tools/network/config_parser.py` + `tools/migration_canvas/network_migration.py::parse_source_config()` | Already extracts hostname, interfaces, IP, BGP/OSPF/ISIS neighbors, LAG, firewall filters. |
| Interactive topology canvas | `tools/dashboard/static/js/network-canvas.js` (JointJS) | Render a read-only or lightly editable topology sidecar in the wizard. |
| Port diagrams | `tools/dashboard/static/js/network-port-diagram.js` | Already used in the wizard for Step 3 port mapping; reuse for side-by-side hardware view. |
| Topology enrichment | `tools/network/topology_enricher.py` | Algorithm for adding site grouping, rack infra, labels. |
| COA framework | `context/simulation/coa_templates.json` | General `speed`/`balanced`/`comprehensive` schema; extend with network-specific COA metadata. |
| SOP system | `tools/migration_canvas/sops.py`, `SOP_TYPES` in `constants.py`, `mc_sops` table | Create new `network_device_replacement` SOP type; seed canonical SOPs. |
| Runbooks | `mc_runbooks` table already exists in migration canvas DB | Generate per-session rollback runbooks from COA. |
| RAG indexing | `tools/migration_canvas/network_migration.py::_index_to_rag()` | Index topology summary, COA rationale, and SOP snippets. |
| KG update | `tools/migration_canvas/network_migration.py::_update_kg()` + `tools/canvas/kg_builder.py` | Extend graph with topology nodes, COA node, and neighbor edges. |
| LLM routing | `tools/llm/router.py` | Use `LLMRouter().invoke(...)` for recommendation text and proposal rewriting (no hard-coded model). |
| DB connection | `tools.migration_canvas.db.init_db.get_connection()` (canvas-safe, RLS-free) | Already used; extend with new tables via migration. |

## Data model changes

### New columns on `mc_net_sessions`

- `engineer_context` TEXT — free-text circumstance description.
- `recommended_coa` TEXT CHECK IN ('coa_a','coa_b','coa_c','') — system recommendation.
- `selected_coa` TEXT CHECK IN ('coa_a','coa_b','coa_c','') — engineer override.
- `coa_rationale` TEXT — human-readable reason for recommendation.
- `topology_json` TEXT — generated topology graph JSON.
- `topology_neighbors_json` TEXT — discovered/declared neighbor devices.

### New table: `mc_net_coa_questions`

```sql
CREATE TABLE mc_net_coa_questions (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES mc_net_sessions(id) ON DELETE CASCADE,
    question_key TEXT NOT NULL,
    question_text TEXT NOT NULL,
    default_answer INTEGER DEFAULT NULL,
    user_answer INTEGER DEFAULT NULL,
    coa_a_weight REAL DEFAULT 0,
    coa_b_weight REAL DEFAULT 0,
    coa_c_weight REAL DEFAULT 0,
    UNIQUE(session_id, question_key)
);
```

### New table: `mc_net_topology_neighbors`

```sql
CREATE TABLE mc_net_topology_neighbors (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES mc_net_sessions(id) ON DELETE CASCADE,
    neighbor_name TEXT DEFAULT '',
    neighbor_ip TEXT DEFAULT '',
    relationship TEXT DEFAULT '',  -- 'bgp_peer', 'ospf_neighbor', 'isis_neighbor', 'l2_peer', 'downstream'
    source_interface TEXT DEFAULT '',
    is_discovered INTEGER DEFAULT 0,
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

### New SOP entries

Create `context/migration/sop_catalog/network-device-replacement.json` with:
- `network_device_side_by_side_validation`
- `network_device_warm_cutover`
- `network_device_cold_cutover`
- `network_device_rollback`
- `network_device_post_migration_validation`

Seed into `mc_sops` via existing `tools/migration_canvas/sops.py::create_sop()`.

## Backend changes

### `tools/migration_canvas/network_migration.py`

1. **`build_topology(session_id)`** — parse `src_config_raw` and produce:
   - Source device node.
   - Target device node.
   - Interface nodes (grouped by speed/media).
   - Neighbor nodes from BGP/OSPF/ISIS/static routes.
   - VLAN / L2 segments inferred from interface descriptions / sub-interfaces.
   - Return `graph_json` compatible with JointJS `network-canvas.js` node types.

2. **`discover_neighbors(session_id)`** — optional enrichment from `network_canvas.db` `ni_devices` / `ni_device_configs` by matching IP/hostname; falls back to declared config.

3. **`recommend_coa(session_id)`** — scoring function using:
   - Free-text engineer context (LLM extracts constraints).
   - Yes/no answers from `mc_net_coa_questions`.
   - Parsed facts: IGP presence, BGP count, LAG count, mgmt interfaces, firewall complexity.
   - Returns recommended COA + rationale.

4. **`apply_coa_to_mappings(session_id, coa)`** — idempotent function that:
   - Resets `mc_net_port_map` defaults (e.g., COA-A proposes same-VLAN mgmt mapping).
   - Resets `mc_net_config_map` proposals (e.g., COA-C suppresses IGP neighbor preservation).
   - Regenerates `mc_net_test_cases` and `mc_net_cutover_steps` from COA-specific templates.

5. **Extend `_update_kg(session_id)`** to include topology nodes + COA node + neighbor edges.

6. **Extend `_index_to_rag(...)** to index COA rationale and topology summary.

### `tools/migration_canvas/blueprint.py`

Add routes:
- `POST /api/network-migration/<sid>/topology` — generate/refresh topology.
- `GET /api/network-migration/<sid>/topology` — return stored `topology_json`.
- `GET /api/network-migration/<sid>/coa-questions` — return seeded questions.
- `POST /api/network-migration/<sid>/coa-questions` — save answers + return recommendation.
- `POST /api/network-migration/<sid>/select-coa` — persist selected COA and apply to mappings.
- `GET /api/network-migration/<sid>/sops` — return relevant SOPs for selected COA.

## Frontend changes

### Step 1 enhancement

- Add **Engineer Context** textarea with placeholder examples:
  > "Replacement device is layer-2 only; all IGP happens downstream on switches I do not control."
- Add collapsible **Yes/No Questionnaire** (seeded from backend).
- Add **Recommended COA** card with rationale and explicit "Accept" / "Override" buttons.
- Store context + answers on `Create Session & Continue`.

### New topology sidecar

- Add a **Topology** tab/panel visible from Step 2 onward.
- Reuse JointJS canvas (`network-canvas.js`) with a simplified read-only mode.
- Render source device, target device, interfaces, neighbor peers, and VLAN clouds.
- Provide "Refresh topology" button to re-parse config.

### COA impact in later steps

- **Step 3 Port Mapping**: show COA badge; pre-fill same-VLAN mgmt mapping for COA-A.
- **Step 5 Config Mapping**: questions and proposals adapt to COA (e.g., COA-C skips "preserve IGP peers" question if selected).
- **Step 7 Test Plan**: seed COA-specific tests (parallel adjacency check for COA-A, rollback timing for COA-C).
- **Step 8 Cutover Plan**: include rollback triggers per COA.
- **Step 9 ERB/Package**: link selected SOPs/runbooks.

### SOP / runbook surfacing

- In Step 1 and Step 8, show a "Relevant SOPs" panel.
- Cite internal SOPs from `mc_sops`.
- Provide RAG/KG search link: "Search knowledge base for similar migrations".

## Implementation phases

| Phase | Scope | Deliverable |
|---|---|---|
| **A** | COA selection in Step 1 | DB columns, questions table, `recommend_coa()` API, Step 1 UI, commit. |
| **B** | Topology auto-generation | `build_topology()`, `/topology` API, topology sidecar panel using JointJS, commit. |
| **C** | COA-driven mappings | `apply_coa_to_mappings()`, Step 3/5 adaptations, reset-on-COA-change, commit. |
| **D** | COA-driven test + cutover | COA-specific test cases and cutover steps, rollback runbooks, commit. |
| **E** | SOP + RAG + KG integration | Seed network SOPs, relevant-SOP API, KG/RAG indexing extensions, E2E tests, commit. |

## Testing plan

1. **Unit tests** for `recommend_coa()` scoring and `build_topology()` graph shape.
2. **API tests** for new `/topology`, `/coa-questions`, `/select-coa` routes.
3. **Playwright E2E** extending the existing `migration_network_config_map.spec.ts` to:
   - Enter engineer context.
   - Answer COA questions.
   - Accept recommended COA-A.
   - Verify topology panel renders source + target + at least one neighbor.
   - Verify Step 5 config mapping reflects COA-A defaults.
   - Verify SOP panel cites the side-by-side SOP.
4. **Coherence gate** after each phase.

## Risks and open questions

1. **JointJS dependency** — `network-canvas.js` expects `TOPOLOGY_ID` injected by template. We may need a lightweight standalone JointJS init for the wizard sidecar, or reuse the same canvas in a stripped-down mode.
2. **Neighbor discovery** — Without live SNMP/LLDP, neighbors are inferred from config (BGP peers, next-hops). We should label inferred neighbors clearly and allow manual override.
3. **LLM cost** — Free-text context + proposal rewriting calls LLM. Keep it gated behind existing `ICDEV_MIGRATION_CANVAS_ENABLED` and allow offline fallback (rule-based suppression for L2 keywords).
4. **PG-primary schema changes** — New tables/columns need a migration file; do not rely on `CREATE TABLE IF NOT EXISTS` for production. Add a new `tools/db/migrations/` SQL file.

## Next step

Approve this plan so I can begin Phase A implementation.
