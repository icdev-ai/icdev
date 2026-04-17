# Network Design Canvas + IQE (ICDEV Query Engine)

> Tools for the Network Design Canvas (NDC) and the IQE declarative query engine.

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
