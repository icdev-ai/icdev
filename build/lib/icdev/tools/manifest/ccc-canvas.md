# CCC Canvas — Manifest Shard

## Circuit & Capacity Canvas (CCC)

**Feature flag:** `ICDEV_CCC_ENABLED=true`  
**DB:** `data/ccc_canvas.db` (PostgreSQL default, SQLite fallback)  
**Backend env:** `CCC_STORAGE_BACKEND` (default: `postgresql`)

### Core Modules

| File | Purpose |
|------|---------|
| `tools/ccc_canvas/db/init_db.py` | Dual-backend DB init; 5 tables + audit |
| `tools/ccc_canvas/constants.py` | Feature flag, circuit types, status enums, utilization thresholds |
| `tools/ccc_canvas/circuit_aggregator.py` | Overview JSON: active circuits, utilization health, XC/LOA counts |
| `tools/ccc_canvas/capacity_engine.py` | Capacity planning: growth rate, months-to-saturation, recommended action |
| `tools/ccc_canvas/loa_workflow.py` | LOA request creation, document generation |
| `tools/ccc_canvas/blueprint.py` | Flask blueprint `create_ccc_blueprint()`, gated by `ICDEV_CCC_ENABLED` |

### DB Schema (`ccc_canvas.db`)

| Table | Key Fields |
|-------|-----------|
| `ccc_circuits` | circuit_id, circuit_type, carrier, bandwidth_gbps, utilization_pct, mrr_usd, contract_end |
| `ccc_cross_connects` | xc_number, facility, port_a, port_z, speed_gbps, status, monthly_cost_usd |
| `ccc_loa_requests` | loa_number, facility, requester_*, rack_a/z, status, valid_days |
| `ccc_capacity_plans` | circuit_id, current_util_pct, projected_util_pct, months_to_saturation, growth_rate_pct |
| `ccc_dwdm_spans` | span_id, fiber_route, total_capacity_tbps, used_capacity_tbps, available_wavelengths, osnr_db |
| `ccc_audit` | APPEND-ONLY audit trail (NIST AU) |

### Routes

```
GET  /ccc, /ccc/circuits, /ccc/cross-connects, /ccc/loa, /ccc/capacity, /ccc/dwdm
POST /api/ccc/circuits, /api/ccc/cross-connects, /api/ccc/loa, /api/ccc/dwdm
GET  /api/ccc/overview, /api/ccc/circuits, /api/ccc/cross-connects, /api/ccc/loa
GET  /api/ccc/loa/<id>/document, /api/ccc/capacity/report, /api/ccc/dwdm
POST /api/ccc/circuits/<id>/refresh-utilization
PUT  /api/ccc/circuits/<circuit_id>
POST /api/ccc/iqe-query
```

### IQE

- Adapter: `tools/iqe/adapters/ccc.py`
- Collections: `ccc.circuits`, `ccc.cross_connects`, `ccc.loa`, `ccc.capacity_plans`, `ccc.dwdm_spans`
- Seed queries: `context/iqe/queries/ccc_canvas/` (5 queries)

### MCP Tools

| Tool | Handler |
|------|---------|
| `ccc_circuit_ingest` | Add/update circuit record |
| `ccc_capacity_analyze` | Run capacity analysis for a circuit |
| `ccc_loa_create` | Create LOA request |

### Security Gates

| Gate ID | Condition |
|---------|-----------|
| `CCC-UTIL-001` | Any circuit ≥85% utilization → WARN |
| `CCC-EXPIRY-001` | Contract expiring within 90 days → WARN |

### Related Systems

- Integrates Equinix ECX connector (`tools/databridge/connectors/equinix_ecx_connector.py`)
- Integrates Megaport connector (`tools/databridge/connectors/megaport_connector.py`)
- Uses ISP capacity planner (`tools/network/isp_capacity_planner.py`)
- NOCC alarms reference `circuit_id` from `ccc_circuits`
- PMC peers reference IX cross-connects from `ccc_cross_connects`
