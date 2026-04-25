# Network Design Canvas + IQE (ICDEV Query Engine)

> Tools for the Network Design Canvas (NDC) and the IQE declarative query engine.

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
