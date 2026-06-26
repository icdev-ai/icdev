# Network Design Canvas + IQE (ICDEV Query Engine)

> Tools for the Network Design Canvas (NDC) and the IQE declarative query engine.

---

## NDC — Migration Phases Engine

| Tool | Path | Purpose |
|------|------|---------|
| `compute_infoboxes()` | `tools/network/migration_phases.py` | Compute 8–16 critical info boxes (device inventory, link util, IP/VLAN, redundancy, routing, security, WAN, hardware health, plus phase-specific and final-specific boxes) from a topology graph dict. |
| `compute_final_infoboxes()` | `tools/network/migration_phases.py` | Final/To-Be panel boxes: all 8 standard + consolidation results, 3yr TCO delta, performance gains, compliance improvement. |
| `generate_phase_graph()` | `tools/network/migration_phases.py` | Overlay phase changes on current graph — colors nodes: existing=blue, new=green, changing=orange, retiring=red. |
| `generate_final_graph()` | `tools/network/migration_phases.py` | Apply all phases sequentially → derive Final/To-Be topology. |
| `run_consolidation_analysis()` | `tools/network/migration_phases.py` | Compute devices removed, rack units freed, power savings, capex/opex/TCO delta, SPOF reduction from current vs final graph. |
| `save_consolidation()` / `load_consolidation()` | `tools/network/migration_phases.py` | Persist/load consolidation analysis to `nc_consolidation_analysis` DB table. |
| `export_phase_pdf()` | `tools/network/pdf_export.py` | Multi-page PDF (fpdf2): cover + topology SVG diagram + info boxes + device inventory + port mapping + consolidation. Falls back to print-ready HTML if fpdf2 unavailable. |

**Routes:**

| Route | Method | Purpose |
|-------|--------|---------|
| `/network/migration-phases/<topo_id>` | GET | Three-panel view: Current State / Phase N stepper / Final To-Be. Each with SVG diagram + 8+ info boxes + PDF/Visio/Draw.io export. |
| `/network/api/migration-phases/<topo_id>/data` | GET | Return all phase graphs + info boxes + consolidation JSON. |
| `/network/api/migration-phases/<topo_id>/export/<phase_key>/<fmt>` | POST | Export one panel as PDF, Visio VSDX, or Draw.io XML. |

**DB Tables:** `nc_phase_infoboxes` (user overrides per box), `nc_consolidation_analysis` (cached analysis per topology).

```bash
# Access from any topology's action row in /network/
# Click "MP" button → /network/migration-phases/<topo_id>
```

---

## NDC — Migration Hub

Hub page aggregating all migration projects, phases, and linked documentation.

| Route | Method | Purpose |
|-------|--------|---------|
| `/network/migration-hub` | GET | Hub page: all projects, phase timelines, linked docs, runbook/SOP library sidebar. Filterable by status/doc type. |
| `/network/api/migration-hub/data` | GET | JSON: all projects + phases (with linked docs) + standalone docs + runbooks + SOPs + topologies. |
| `/network/api/migration-hub/phase-docs` | POST | Link a document/runbook/SOP to a migration phase. Body: `{phase_id, project_id, doc_source, doc_id, doc_title, doc_type?, relevance_note?}` |
| `/network/api/migration-hub/phase-docs/<id>` | DELETE | Remove a document link from a migration phase. |

**DB Table:** `nc_phase_documents` — join table: `phase_id → doc_id` with `doc_source` ('document'/'runbook'/'sop'/'external'), `relevance_note`, `display_order`.

**Template:** `tools/dashboard/templates/network/migration_hub.html`

```bash
# Access from /network/ → "Migration Hub" button in header
# Direct: http://localhost:5050/network/migration-hub
```

---

## NDC — Network Path Analysis

| Tool | Path | Purpose |
|------|------|---------|
| `find_paths()` | `tools/network/path_analyzer.py` | BFS all-simple-paths between two nodes; ACL filtering per edge; returns reachability verdict with `{src, dst, paths, reachable, blocked_by_acl, path_count}`. Uses `_resolve_node_id()` from `tools.network.twin` for fuzzy node lookup. |

```bash
python -c "
from tools.network.path_analyzer import find_paths
graph = {'nodes': {...}, 'edges': [...]}
print(find_paths('router-a', 'server-d', graph, max_depth=10))
"
```

---

## IQE — ICDEV Query Engine

| Tool | Path | Purpose |
|------|------|---------|
| `parse()` | `tools/iqe/parser.py` | Tokenize + parse IQE DSL string → Query AST. Zero dependencies. |
| `execute()` | `tools/iqe/interpreter.py` | Walk AST against an adapter; return filtered + projected row list. |
| `NDCAdapter` | `tools/iqe/adapters/ndc.py` | Maps IQE collections to network_canvas.db tables via NDC get_connection(). |
| `IQEAdapter` | `tools/iqe/adapters/__init__.py` | Abstract base class for all canvas adapters. |
| IQE CLI | `tools/iqe/cli.py` | CLI: `--query`, `--file`, `--adapter`, `--topology`, `--json`, `--count`. |

### IQE Syntax
```
foreach <var> in <collection> [where <predicate>]* select <projection>, ...
```
- Multiple `where` clauses are AND-combined.
- Predicates: `==`, `!=`, `>`, `<`, `>=`, `<=`, `contains`, `startswith`, `and`, `or`, `not`.
- Dotted paths auto-expand JSON blob columns (`config_json` → `config.*`).

### NDC Collections
| Collection | SQL Source | Notes |
|-----------|-----------|-------|
| `network.topologies` | `topologies` | All topology records |
| `network.devices` | `nc_objects` (device types only) | Routers, switches, firewalls, etc. |
| `network.objects` | `nc_objects` | All canvas objects unfiltered |
| `network.circuits` | `nc_circuits` | WAN/carrier circuits |
| `network.sites` | `nc_sites` | Physical/logical sites |
| `network.ipam` | `nc_ipam_blocks` | IP address management blocks |
| `network.findings` | `nc_compliance_findings` | Compliance findings (STIG, FISMA, ZTA) |
| `network.projects` | `nc_projects` | Network projects |
| `network.versions` | `nc_versions` | Topology version history |
| `network.groups` | `nc_groups` | CSP/container groups |
| `network.cables` | `nc_cables` | Physical cable plant |
| `network.cross_connects` | `nc_cross_connects` | Facility cross-connects |

### Seed Queries (`context/iqe/queries/network/`)
| File | ID | Description |
|------|----|-------------|
| `01_device_inventory.iqe` | IQE-NDC-001 | All devices: label, type, topology |
| `02_high_cost_circuits.iqe` | IQE-NDC-002 | Circuits > $5,000/month |
| `03_cat1_open_findings.iqe` | IQE-NDC-003 | Open CAT1 compliance findings |
| `04_sla_below_threshold.iqe` | IQE-NDC-004 | Circuits with SLA < 99.9% |
| `05_non_us_sites.iqe` | IQE-NDC-005 | Sites outside the US (ITAR/EAR review) |
| `06_high_ipam_utilization.iqe` | IQE-NDC-006 | IPAM blocks > 80% utilized |
| `07_unplanned_circuits.iqe` | IQE-NDC-007 | Circuits not yet active |
| `08_classified_sites.iqe` | IQE-NDC-008 | SECRET-classification sites |
| `09_stig_open_findings.iqe` | IQE-NDC-009 | Open STIG findings |
| `10_cui_topologies.iqe` | IQE-NDC-010 | Topologies carrying CUI |

### CLI Examples
```bash
# Run a seed query
python tools/iqe/cli.py --file context/iqe/queries/network/03_cat1_open_findings.iqe --json

# Inline query scoped to a specific topology
python tools/iqe/cli.py --adapter ndc --topology <id> \
  --query "foreach c in network.circuits where c.sla_uptime_pct < 99.9 select c.circuit_id, c.carrier, c.sla_uptime_pct"

# List all NDC collections
python tools/iqe/cli.py --list-collections --adapter ndc

# Count only
python tools/iqe/cli.py --file context/iqe/queries/network/09_stig_open_findings.iqe --count
```

---

## Network Design Canvas — Existing Tools

| Tool | Path | Purpose |
|------|------|---------|
| NDC DB init | `tools/network/db/init_db.py` | Create/migrate network_canvas.db; seeds 12 templates |
| NDC blueprint | `tools/network/blueprint.py` | Core topology CRUD, graph operations |
| NDC simulation | `tools/network/simulation.py` | Monte Carlo + what-if simulations |
| NDC intelligence | `tools/network/network_intelligence.py` | AI-driven topology analysis |
| NDC compliance | `tools/network/compliance.py` | STIG/FISMA/ZTA compliance checks |
| NDC NLQ | `tools/network/nl_query.py` | Natural language queries over topology |
| NDC ingestion | `tools/network/ingestion_pipeline.py` | Ingest external topology data |
| NDC config parser | `tools/network/config_parser.py` | Parse device configs (Cisco IOS, etc.) |
| NDC config gen | `tools/network/config_generator.py` | Generate device configs from topology |
| NetBox client | `tools/network/netbox_client.py` | NetBox integration (CMDB sync) |
| NDC routes (API) | `tools/network/routes/` | Flask blueprints for NDC API endpoints |
| NDC adapters | `tools/network/adapters/` | Network data source adapters (extend here) |
| TFW narrative generator | `tools/network/narrative_generator.py` | Traffic Flow Walkthrough narratives — per-persona LLM narratives + deterministic detail_json (CSP detection, multi-CSP hops, classification overlay, NIST 800-53 pre-population). CLI: `--flow-id <id> --json` |
| NDC Digital Twin | tools/network/twin.py | Network digital twin: topology snapshot capture, intent rule validation against deltas, and blast-radius impact analysis for network nodes/links with fuzzy node resolution | `take_snapshot()`, `simulate_delta()`, `blast_radius()` (library) | Snapshot/delta/impact dicts |
| NDC Stencil Routes | tools/network/routes/stencils.py | Flask Blueprint registering 8 REST endpoints for Visio stencil catalog, upload, import-by-URL, library CRUD, and shape icon serving on NDC network canvas | `register_stencil_routes(bp)` (library) | HTTP responses |

---

## TFW Narrative Generator — Detailed Analysis

**File:** `tools/network/narrative_generator.py`
**Classification:** CUI // SP-CTI

### Purpose

Wraps `TrafficFlowEngine` walkthrough steps with per-persona LLM narratives and deterministic `detail_json` enrichment. For each hop in a traffic flow, it generates a role-specific narrative (security engineer, network engineer, compliance officer, etc.) explaining what happens at that node, plus structured metadata (NIST controls, CSP info, latency, endpoints).

### Public API

| Function | Signature | Returns |
|----------|-----------|---------|
| `generate_for_persona` | `(step, node, persona_id, flow, classification, prev_node, llm_client, use_llm)` | `{"narrative": str, "detail_json": dict}` |
| `generate_all` | `(flow_id, conn, personas, classification, use_llm)` | `{"steps": [...], "summary": {...}}` |
| `load_personas` | `()` | `list[dict]` — all personas from `tfw_personas.yaml` |
| `load_classification_context` | `(level)` | `dict` — encryption/MFA/audit overlay for a classification level |
| `load_cross_cloud_context` | `(node)` | `dict | None` — CSP metadata for a node, or None |
| `detect_csp` | `(node)` | `str | None` — `cross_cloud_contexts` key (e.g. `aws_govcloud`) |
| `build_classification_overlay` | `(flow)` | `str` — classification context string for LLM system prompt injection |

### Key Internal Functions

| Function | Purpose |
|----------|---------|
| `_load_personas_config()` | Lazy-loads and caches `args/tfw_personas.yaml` |
| `_build_system_prompt()` | Assembles persona base prompt + classification overlay + CSP context block |
| `_build_compofficer_detail()` | Maps `action_type` → NIST 800-53 + FedRAMP controls via `_ACTION_NIST_MAP` |
| `_build_appdev_detail()` | Infers endpoint URL, DNS name, and token endpoint from node config + CSP type |
| `_invoke_llm()` | Routes to `LLMRouter` via function key `tfw_narrative`; returns `None` on any failure |
| `_highest_risk()` | Scans all persona `detail_json` and returns highest `risk_level`/`risk_rating` |

### Dependencies

| Dependency | Type | Notes |
|-----------|------|-------|
| `tools.llm.router.LLMRouter` | Internal (optional) | Used for LLM narrative generation; gracefully skipped when unavailable |
| `tools.llm.provider.LLMRequest` | Internal (optional) | Request wrapper for LLM invocations |
| `tools.network.traffic_flow.TrafficFlowEngine` | Internal | Loads walkthrough steps and generates them if missing |
| `tools.network.db.init_db.get_connection` | Internal | DB connection for CLI mode |
| `args/tfw_personas.yaml` | Config | Persona definitions, system prompts, CSP contexts, classification levels |
| `yaml` | stdlib/PyPI | YAML config loading |
| `json`, `logging`, `uuid`, `pathlib` | stdlib | Standard utilities |

### Configuration

All behavioral data lives in `args/tfw_personas.yaml`:
- **`personas[]`** — list of persona objects with `id`, `short`, `system_prompt`
- **`cross_cloud_contexts`** — CSP metadata keyed by `aws_govcloud`, `azure_gov`, `gcp_gov`, `oci_gov`; includes `name`, `regions[]`, `il_levels[]`, `network_constructs[]`, `identity`, `connectivity_to_bcap`
- **`classification_levels`** — `NIPR`, `IL4`, `IL5`, `IL6`, `SIPR`; each with `label`, `encryption`, `key_mgmt`, `mfa`, `audit_retention`

### Personas Supported

`seceng` (Security Engineer), `neteng` (Network Engineer), `cloudarch` (Cloud Architect), `compofficer` (Compliance Officer), `appdev` (App Developer), `missionowner` (Mission Owner), `ciso` (CISO)

### NIST 800-53 Action Mapping

Action types are normalized to canonical keys (`authenticate`, `encrypt_vpn`, `security_inspect`, `tls_inspect`, `route_lookup`, `authorize`) then mapped to NIST and FedRAMP controls in `_ACTION_NIST_MAP`. Raw action aliases (e.g. `mfa-verify`, `idps-scan`, `waf-filter`) are resolved via `_ACTION_NORMALIZE`.

### Narrative Generation Strategy

1. **LLM path** — builds a system prompt (persona base + classification overlay + CSP context block), sends hop details as user content to `LLMRouter` with function key `tfw_narrative`
2. **Template fallback** — if LLM unavailable, uses `NARRATIVE_TEMPLATES[action_type][persona_id]` with `{node_label}`, `{action_type}`, `{classification}` substitution
3. **Generic fallback** — constructs a plain-text narrative from step metadata

### `generate_all()` Summary Fields

| Field | Description |
|-------|-------------|
| `hop_count` | Total number of walkthrough steps |
| `csps_traversed` | List of CSP names encountered |
| `inter_csp_hops` | List of CSP-to-CSP transition strings (e.g. `"AWS GovCloud → Azure Government"`) |
| `classification` | Human-readable classification label |
| `encryption` | Encryption chain string from classification context |
| `key_risk` | Highest risk level found across all persona `detail_json` outputs |
| `total_latency_ms` | Accumulated per-hop latency estimates from `_DOMAIN_LATENCY_MS` |
| `description` | Human-readable flow summary sentence |

### DB Persistence

After generating narratives, `generate_all()` persists all persona responses to `nc_step_persona_responses` via `INSERT OR REPLACE`, keyed by `step_id` + `persona_id`. Failures are logged as warnings (non-blocking).

### CLI Usage

```bash
# Generate JSON output for a traffic flow
python tools/network/narrative_generator.py --flow-id <uuid> --json

# With explicit classification and personas
python tools/network/narrative_generator.py --flow-id <uuid> \
  --classification IL4 --personas seceng compofficer --json

# Deterministic only (no LLM)
python tools/network/narrative_generator.py --flow-id <uuid> --no-llm --json
```

### Latency Domain Estimates

| Domain | Latency (ms) |
|--------|-------------|
| `on_prem` | 2 |
| `nipr` | 5 |
| `bcap_vdms` | 8 |
| `bcap_vdss` | 12 |
| `csp_il4` | 15 |
| `csp_il5` | 18 |

---

## NDC — Vendor Stencil Library (Cisco / Juniper / AWS / Azure / Custom)

| Tool | Path | Purpose |
|------|------|---------|
| `parse_vssx()` | `tools/network/stencil_importer.py` | Parse .vssx / .vsdx OpenXML stencil → list of shape dicts with name, icon_data (base64 PNG/SVG) |
| `parse_cisco_zip()` | `tools/network/stencil_importer.py` | Extract shapes from Cisco legacy ZIP (contains .vss + optional PNG/VSSX) |
| `parse_svg_pack()` | `tools/network/stencil_importer.py` | Extract shapes from AWS/Azure SVG icon pack ZIPs |
| `import_from_url()` | `tools/network/stencil_importer.py` | Download vendor stencil URL → parse → save to `nc_stencil_libraries` + `nc_stencil_shapes` |
| `import_from_bytes()` | `tools/network/stencil_importer.py` | Upload stencil file bytes → parse → save to DB |
| `save_library()` | `tools/network/stencil_importer.py` | Persist stencil library + shapes to network_canvas.db |
| `get_cisco_catalog()` | `tools/network/stencil_catalog.py` | Fetch live Cisco stencil listing (falls back to 18-entry static catalog on 403) |
| `get_catalog(vendor)` | `tools/network/stencil_catalog.py` | Get catalog for any vendor: cisco / juniper / aws / azure / custom |
| `get_vendor_list()` | `tools/network/stencil_catalog.py` | Return metadata for all 5 vendors (label, color, logo_char, docs_url) |

**DB Tables:** `nc_stencil_libraries`, `nc_stencil_shapes` (in `data/network_canvas.db`)

**Routes (registered on NDC blueprint at `/network`):**
- `GET /stencils` — stencil manager page
- `GET /api/stencils/vendors` — vendor list
- `GET /api/stencils/catalog/<vendor>` — available packages from vendor catalog
- `POST /api/stencils/import-url` — download + import by URL
- `POST /api/stencils/upload` — file upload + import
- `GET /api/stencils/libraries` — list imported libraries
- `DELETE /api/stencils/libraries/<id>` — delete library + shapes
- `GET /api/stencils/shapes` — list shapes (vendor, library_id, q filters)
- `GET /api/stencils/shapes/<id>/icon` — serve shape icon (PNG/SVG)

```bash
# Import Cisco ISR 4000 stencil by URL
python -c "
from tools.network.stencil_importer import import_from_url
result = import_from_url(
    'https://www.cisco.com/c/dam/assets/prod/visio/visio/routers-cisco-4000-series-isr.zip',
    vendor='cisco', lib_name='Cisco ISR 4000'
)
print(result)  # {'library_id': '...', 'shape_count': N, 'format': 'vss_zip'}
"

# Import AWS icon pack
python -c "
from tools.network.stencil_importer import import_from_url
result = import_from_url(
    'https://d1.awsstatic.com/webteam/architecture-icons/q4-2023/Asset-Package_10242023.e47d9fa5db10be08af8ae6e44cee5b7e7b55a59f.zip',
    vendor='aws', lib_name='AWS Architecture Icons'
)
print(result)
"
```

---

## NDC — SOP Library

| Tool | Path | Purpose |
|------|------|---------|
| `seed()` | `tools/network/seed_sops.py` | Seeds 49 approved SOPs into `ndc_sops` (8 categories: physical_connectivity, vpn_configuration, hub_transit, cross_cloud, dod_scca, ipsec_reference, routing_protocols, troubleshooting). Idempotent — deduplicates by title. Calls `init_db()` before seeding. |
| `get_connectivity_matrix()` | `tools/network/connectivity_ref.py` | Returns full CSP×connection-type reference matrix (AWS/Azure/GCP/OCI/IBM). Each entry includes service name, abbrev, bandwidth, compliance levels (IL4/IL5/FedRAMP), BCAP-compatible flag, MACsec flag, and SOP title list. |
| `get_onprem_to_csp_patterns()` | `tools/network/connectivity_ref.py` | Returns on-prem→CSP pattern detail (description, pros/cons, diagram, SOP titles, DoD notes) for a given CSP and pattern type (ipsec_vpn, dedicated_private, partner_managed, sdwan_overlay). |
| `get_csp_to_csp_patterns()` | `tools/network/connectivity_ref.py` | Returns available cloud-to-cloud patterns for a given src/dst CSP pair. Supports IPSec-overlay, SD-WAN, cloud-exchange, and native interconnect. |
| `get_scca_flow()` | `tools/network/connectivity_ref.py` | Returns ordered SCCA component flow (BCAP→VDSS→VDMS→TCCM) with CSP-specific service mappings and SOP title list. |
| `seed_patterns()` | `tools/network/connectivity_ref.py` | Seeds `nc_connectivity_patterns` table from `HYBRID_CONNECTIVITY_PATTERNS` constants. Idempotent (INSERT OR IGNORE). 19 rows across 7 pattern types. |
| `list_sops_by_category()` | `tools/network/connectivity_ref.py` | Proxy for `sops.list_sops()` — returns approved SOPs for a given category with minimal fields (sop_id, title, category, status, version). |

```bash
# Seed 49 SOPs (idempotent)
python tools/network/seed_sops.py --json
python tools/network/seed_sops.py --status approved --json

# Dry-run preview
python tools/network/seed_sops.py --dry-run --json
python tools/network/seed_sops.py --dry-run --category dod_scca --json

# Seed connectivity patterns (idempotent, 19 rows)
python tools/network/connectivity_ref.py --json

# Query matrix
python -c "
from tools.network.connectivity_ref import get_connectivity_matrix
m = get_connectivity_matrix()
print('CSPs:', list(m.keys()))
print('AWS DX compliance:', m['aws']['dedicated_private']['compliance_levels'])
"

# SCCA flow
python -c "
from tools.network.connectivity_ref import get_scca_flow
f = get_scca_flow()
print('Flow:', f['flow_ascii'])
print('Components:', [c['key'] for c in f['components']])
"
```

### SOP Endpoints (Flask)
- `GET /network/sops` — SOP Library UI (category/CSP/status filters, expandable step viewer, copy-to-clipboard CLI blocks)
- `GET /network/api/sops` — JSON list (params: `category`, `status`, `limit`)
- `GET /network/api/sops/<sop_id>` — Single SOP JSON
- `GET /network/api/sops/<sop_id>/history` — Approval history JSON

### Connectivity Reference Endpoints (Flask)
- `GET /network/connectivity` — 4-tab Connectivity Reference UI (Matrix, On-Prem→Cloud, Cloud→Cloud, DoD/SCCA)
- `GET /network/api/connectivity/matrix` — Full CSP×type matrix JSON
- `GET /network/api/connectivity/onprem-pattern?csp=<csp>&type=<type>` — Single on-prem→CSP pattern JSON
- `GET /network/api/connectivity/c2c-patterns?src=<csp>&dst=<csp>` — C2C pattern list JSON

---

## NDC — Analysis Routes (Gap Fill — 2026-05-09)

Replaced 231-byte stub `tools/network/routes/analysis.py` with 5 full route implementations.

| Route | Method | Purpose |
|-------|--------|---------|
| `/api/topologies/<topo_id>/analysis/summary` | GET | Aggregate counts: devices, links, open findings, compliance score, EOL device count |
| `/api/topologies/<topo_id>/analysis/topology-health` | GET | 5-dimension health score: compliance (25%), security (25%), eol (20%), redundancy (20%), capacity (10%) |
| `/api/topologies/<topo_id>/analysis/risk-matrix` | GET | Likelihood × impact matrix from nc_compliance_findings; grouped by severity (cat1/cat2/cat3) |
| `/api/topologies/<topo_id>/analysis/trend` | GET | Compliance score over time from nc_versions snapshot history |
| `/api/topologies/<topo_id>/analysis/export` | GET | Full analysis JSON or PDF summary (delegates to pdf_export.py); `?format=pdf` |

**Reads from:** `nc_compliance_findings`, `nc_objects`, `nc_circuits`, `nc_intent_validations`, `nc_versions`.

---

## NDC — Governance Routes (Gap Fill — 2026-05-09)

Replaced 231-byte stub `tools/network/routes/governance.py` with 5 full route implementations.

| Route | Method | Purpose |
|-------|--------|---------|
| `/api/topologies/<topo_id>/governance/change-requests` | GET, POST | List (with optional `?status=` filter) or create change requests; `POST` body: `{title, change_type, risk_level?, description?, items[]}` |
| `/api/topologies/<topo_id>/governance/change-requests/<cr_id>` | GET, PATCH, DELETE | Fetch full CR + line items; update status/risk/approval fields; delete |
| `/api/topologies/<topo_id>/governance/intent-policies` | GET, POST | List or create intent policies; `POST` body: `{name, description?, rule: {type, ...}, severity?}` |
| `/api/topologies/<topo_id>/governance/intent-policies/<policy_id>/validate` | POST | Evaluate intent rule against topology; writes result to `nc_intent_validations`; rule types: `no_single_points_of_failure`, `no_open_cat1_findings`, `all_devices_managed` |
| `/api/governance/dashboard` | GET | Cross-topology summary: open CRs by topology, failed intent policies, pending approvals |

**Reads/Writes:** `nc_change_requests`, `nc_change_request_items`, `nc_intent_policies`, `nc_intent_validations`.

**`_evaluate_intent_rule()` supported types:**
- `no_single_points_of_failure` — delegates to `tools.network.twin.blast_radius()` if available
- `no_open_cat1_findings` — counts open CAT1 rows in nc_compliance_findings
- `all_devices_managed` — counts unmanaged=0 devices in nc_objects

---

## IQE Full Ecosystem Integration (2026-05-09)

IQE is now wired across all 9 canvases + ICDEV core. Every canvas has a `POST /api/iqe-query` route and an embedded widget. A global dispatch route accepts any canvas name.

### New Adapters

| Adapter | Path | Collections |
|---------|------|-------------|
| BDC | `tools/iqe/adapters/bdc.py` | `bdc.designs`, `bdc.assessments`, `bdc.isas`, `bdc.alerts` |
| MC (Migration) | `tools/iqe/adapters/mc.py` | `mc.designs`, `mc.waves`, `mc.assessments` |
| AADC (Agentic AI) | `tools/iqe/adapters/aadc.py` | `aadc.designs`, `aadc.assessments`, `aadc.artifacts` |
| AIMC (AI/ML) | `tools/iqe/adapters/aimc.py` | `aimc.designs`, `aimc.nodes`, `aimc.assessments`, `aimc.artifacts` |
| Core Kanban | `tools/iqe/adapters/core_kanban.py` | `kanban.tasks`, `kanban.epics` |
| Core Agents/Projects | `tools/iqe/adapters/core_agents.py` | `agents.registry`, `projects.list` |

NDC adapter (`tools/iqe/adapters/ndc.py`) now registers all 13 `network.*` collections on the module-level executor (in addition to the class-based `NDCAdapter` pattern) so the dispatch route can use it.

### Per-Canvas Routes

| Canvas | Route | Blueprint |
|--------|-------|-----------|
| SDC | `POST /security/api/iqe-query` | `tools/security_canvas/blueprint.py` |
| PDC | `POST /devops/api/iqe-query` | `tools/pipeline/blueprint.py` |
| DDC | `POST /data/api/iqe-query` | `tools/data_canvas/blueprint.py` |
| IDC | `POST /infra/api/iqe-query` | `tools/infra_canvas/blueprint.py` |
| ODC | `POST /observability/api/iqe-query` | `tools/observability_canvas/blueprint.py` |
| BDC | `POST /boundary/api/iqe-query` | `tools/boundary_canvas/blueprint.py` |
| MC | `POST /migration-canvas/api/iqe-query` | `tools/migration_canvas/blueprint.py` |
| AADC | `POST /agentic-ai/api/iqe-query` | `tools/agentic_ai_canvas/blueprint.py` |
| AIMC | `POST /ai-ml/api/iqe-query` | `tools/aiml_canvas/blueprint.py` |
| Compliance | `POST /api/compliance/iqe-query` | `tools/dashboard/api/compliance.py` |
| Kanban | `POST /api/kanban/iqe-query` | `tools/dashboard/api/kanban.py` |
| Agents/Projects | `POST /api/core/iqe-query` | `tools/dashboard/app.py` |

**Route contract (all canvases):**
```
POST /api/iqe-query (or per-canvas path above)
Body:    {"question": "natural language question", "execute": true}
Returns: {"ok": true, "iqe": "...", "explanation": "...", "results": [...], "row_count": N}
Error:   {"error": "...", "iqe": "..."}  HTTP 500
```

### Global Dispatch Route

`POST /api/iqe/dispatch` — canvas-aware dispatcher in `tools/dashboard/app.py`.

```json
{"question": "show all attack paths", "canvas": "sdc"}
```

Valid `canvas` values: `ndc`, `sdc`, `pdc`, `ddc`, `idc`, `odc`, `bdc`, `mc`, `aadc`, `aimc`, `compliance`, `kanban`, `agents`, `projects`.

### Shared Widget

`tools/dashboard/templates/includes/iqe_query_widget.html` — reusable Jinja2 partial included in every canvas template.

Include with:
```jinja
{% set iqe_canvas = "sdc" %}
{% set iqe_api_route = "/security/api/iqe-query" %}
{% set iqe_title = "Query Security Data" %}
{% set iqe_examples = [{"label": "...", "query": "..."}] %}
{% include "includes/iqe_query_widget.html" %}
```

### Global Mini-bar

`tools/dashboard/templates/base.html` — collapsible bottom drawer available on every dashboard page.

- Toggle: `Ctrl+Shift+Q`
- Auto-detects current canvas from `window.location.pathname`
- Dispatches to `POST /api/iqe/dispatch`

### Seed Queries

| Directory | Files | Canvas |
|-----------|-------|--------|
| `context/iqe/queries/migration/` | 5 | MC |
| `context/iqe/queries/aadc/` | 5 | AADC |
| `context/iqe/queries/aimc/` | 3 | AIMC |

---

## NDC — PVM: Predictive Vulnerability Management (pvm-*)

### Vulnerability Risk Predictor (`tools/network/vuln_predictor.py`)

4-weight composite risk predictor for CVE advisories. Reads `nc_advisories` + `nc_advisory_assessments`, writes APPEND-ONLY to `nc_vuln_predictions`.

| Function | Returns | Notes |
|----------|---------|-------|
| `predict_advisory_risk(advisory_id)` | dict | Scores and persists one advisory |
| `predict_all_open_advisories()` | list[dict] | Scores all open/in_progress advisories |
| `get_risk_trajectory(advisory_id, limit)` | list[dict] | Historical prediction rows ASC |
| `get_top_risks(limit)` | list[dict] | Latest prediction per advisory DESC |
| `_compute_scores(advisory, assessments)` | dict | Pure formula: cvss×0.35 + exploit×0.30 + lag×0.20 + trend×0.15 |

**Exploit weight tiers:** exploited_in_wild=1 → 1.0; cvss≥7 → 0.5; else → 0.1
**Confidence:** 0 assessments → 0.30; 1 → 0.40; 2 → 0.60; 3+ → 0.85
**Model version constant:** `MODEL_VERSION = "1.0"`
**APPEND-ONLY table:** `nc_vuln_predictions`

### Attack Surface Mapper (`tools/network/attack_surface_mapper.py`)

Cross-correlates Forward Networks NQE device inventory with Nessus/ACAS scan findings and CVE advisories.

| Function | Returns | Notes |
|----------|---------|-------|
| `map_attack_surface(network_id)` | dict | Full NQE+Nessus+advisory-model-match pass |
| `get_attack_surface(cve_id, device_name, min_score, limit)` | list[dict] | Filter `nc_attack_surface` |
| `get_surface_summary()` | dict | Counts: total, reachable, critical, by_criticality |
| `_surface_score(cvss, reachable, bgp_exposed)` | float | cvss/10×0.5 + reachable×0.3 + bgp_exposed×0.2 |
| `_criticality_from_cvss(cvss)` | int | 9+ → 5; 7+ → 4; 5+ → 3; 3+ → 2; else → 1 |

**UPSERT key:** `(device_name, cve_id)` in `nc_attack_surface`
**NQE queries:** `network.devices[config]`, `network.interfaces[ip]`, `network.bgp.sessions[down]`

### Vulnerability Triage Engine (`tools/network/vuln_triage_engine.py`)

4-factor priority scoring with Bayesian reranking and HITL gates.

| Function | Returns | Notes |
|----------|---------|-------|
| `score_advisories(advisory_ids)` | dict | Score + Bayesian rank; returns {scored, auto_approved, pending_hitl, queue} |
| `get_triage_queue(status, limit)` | list[dict] | Filter `nc_triage_queue` by status |
| `approve_advisory(advisory_id, approved_by)` | dict | Sets status=approved; audits triage_approve |
| `defer_advisory(advisory_id, approved_by)` | dict | Sets status=deferred; audits triage_approve |
| `_compute_priority(adv, asset_crit_norm, net_exp_norm)` | (float, dict) | Formula + rationale |
| `_determine_status(score)` | (str, int) | <0.40 → auto-approved; ≥0.75 → HITL pending |

**Priority formula:** kev×0.40 + criticality×0.25 + exposure×0.20 + urgency×0.15
**HITL thresholds:** from `args/network_canvas_config.yaml` under `pvm:` section
**Bayesian reranking:** `tools.intelligence.bayesian_teacher.optimal_compliance_order()` with fallback

### AI Patch Planner (`tools/network/patch_planner.py`)

Reads approved triage items, clusters by site, finds maintenance windows, simulates blast radius, writes APPEND-ONLY patch plans.

| Function | Returns | Notes |
|----------|---------|-------|
| `create_patch_plan(approved_by)` | dict | {plan_id, batches, devices, plan[]} |
| `get_patch_plans(plan_id, advisory_id, limit)` | list[dict] | Filter `nc_patch_plans` |
| `get_plan_summary(plan_id)` | dict | Aggregates: batches, devices, by_simulation_status, risk_reduction_total |
| `_site_from_device(name)` | str | First segment split by '-' or '.' |
| `_find_next_window(conn, site, after_utc)` | dict|None | Projects recurrence forward past after_utc |
| `_run_simulation(device_name)` | dict | Calls `remediation_simulator.simulate_remediation(-1)`; degrades to skipped |

**Clustering:** by site prefix → one batch per site cluster per advisory
**risk_reduction:** priority_score × surface_score
**APPEND-ONLY table:** `nc_patch_plans`
**Audit action:** `plan_create`

### PVM Routes (`tools/network/routes/pvm.py`)

Registered via `register_pvm_routes(bp)` called from `tools/network/blueprint.py`.

| Route | Method | Purpose |
|-------|--------|---------|
| `/network/vulnerability-intelligence` | GET | 4-panel PVM dashboard page |
| `/network/api/pvm/predict/<id>` | POST | Predict risk for one advisory |
| `/network/api/pvm/predict-all` | POST | Predict all open advisories |
| `/network/api/pvm/trajectory/<id>` | GET | Risk trajectory for one advisory |
| `/network/api/pvm/top-risks` | GET | Top-N advisories by composite risk |
| `/network/api/pvm/map-surface` | POST | Run attack surface mapping pass |
| `/network/api/pvm/attack-surface` | GET | Query attack surface (filters: cve, device, min_score) |
| `/network/api/pvm/surface-summary` | GET | Aggregate attack surface counts |
| `/network/api/pvm/score-advisories` | POST | Score + triage advisories |
| `/network/api/pvm/triage-queue` | GET | Get triage queue (filter by status) |
| `/network/api/pvm/approve/<id>` | POST | Approve advisory for patch scheduling |
| `/network/api/pvm/defer/<id>` | POST | Defer advisory |
| `/network/api/pvm/create-plan` | POST | Create patch plan |
| `/network/api/pvm/plans` | GET | List patch plans |
| `/network/api/pvm/plan-summary/<plan_id>` | GET | Plan aggregate summary |

### PVM DB Tables (Migration 221)

| Table | Type | Notes |
|-------|------|-------|
| `nc_vuln_predictions` | APPEND-ONLY | model_version, risk_score_composite/30d/90d, trend, confidence |
| `nc_attack_surface` | UPSERT | (device_name, cve_id) unique; surface_score, reachable, criticality |
| `nc_triage_queue` | INSERT OR REPLACE | advisory_id unique; priority_score, rank, status, rationale_json |
| `nc_patch_plans` | APPEND-ONLY | plan_id+batch_id+device_name; risk_reduction, simulation_status |
| `nc_maintenance_windows` | CRUD | site, start_utc, end_utc, recurrence, blackout_days_json |

### PVM IQE Collections (in `tools/iqe/adapters/ndc.py`)

| Collection | Source Table | Notes |
|-----------|-------------|-------|
| `network.vuln_predictions` | `nc_vuln_predictions` | Ordered by risk_score_composite DESC |
| `network.attack_surface` | `nc_attack_surface` | Ordered by surface_score DESC |
| `network.triage_queue` | `nc_triage_queue` | Ordered by rank ASC |
| `network.patch_plans` | `nc_patch_plans` | Ordered by created_at DESC |
| `network.advisories` | `nc_advisories` | All advisory metadata |

### PVM IQE Seed Queries (`context/iqe/queries/network/`)

| File | Purpose |
|------|---------|
| `pvm_01_risk_trajectory.iqe` | Latest risk scores ordered by composite DESC |
| `pvm_02_attack_surface.iqe` | Reachable attack surface entries by criticality |
| `pvm_03_triage_queue.iqe` | Pending triage queue items ordered by rank |
