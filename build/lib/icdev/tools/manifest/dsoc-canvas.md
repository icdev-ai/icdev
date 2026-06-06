# DSOC Canvas — Manifest Shard

## DDoS & Security Ops Canvas (DSOC)

**Feature flag:** `ICDEV_DSOC_ENABLED=true`  
**DB:** `data/dsoc_canvas.db` (PostgreSQL default, SQLite fallback)  
**Backend env:** `DSOC_STORAGE_BACKEND` (default: `postgresql`)

### Core Modules

| File | Purpose |
|------|---------|
| `tools/dsoc_canvas/db/init_db.py` | Dual-backend DB init; 5 tables + audit |
| `tools/dsoc_canvas/constants.py` | Feature flag, action/threat enums, RFC community defaults, IQE INTENT_RULES |
| `tools/dsoc_canvas/flowspec_engine.py` | BGP flowspec NLRI formatter, IOS-XR/JunOS config gen, activate/withdraw |
| `tools/dsoc_canvas/rtbh_manager.py` | RTBH trigger/withdraw, auto-expire, active blackhole query |
| `tools/dsoc_canvas/dsoc_aggregator.py` | Overview JSON: mitigation counts, scrubbing utilization, threat stats |
| `tools/dsoc_canvas/blueprint.py` | Flask blueprint `create_dsoc_blueprint()`, gated by `ICDEV_DSOC_ENABLED` |

### DB Schema (`dsoc_canvas.db`)

| Table | Purpose |
|-------|---------|
| `dsoc_flowspec_rules` | BGP flowspec rules: source/dest prefix, protocol, port, action, community |
| `dsoc_rtbh_entries` | RTBH blackhole entries: prefix, trigger reason, auto-withdraw timer |
| `dsoc_scrubbing_centers` | Scrubbing center inventory: capacity, load, anycast prefix |
| `dsoc_threats` | Threat intelligence: source prefix, type, confidence, feed source |
| `dsoc_mitigations` | Active DDoS mitigations: type (rtbh/flowspec/scrubbing/acl/hybrid), status |
| `dsoc_audit` | Append-only audit trail (NIST AU) |

### Routes

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/dsoc` | DSOC overview |
| GET | `/dsoc/flowspec` | Flowspec rule list |
| GET | `/dsoc/rtbh` | RTBH blackhole list |
| GET | `/dsoc/scrubbing` | Scrubbing center inventory |
| GET | `/dsoc/threats` | Threat feed |
| GET | `/dsoc/mitigations` | Active mitigation tracker |
| GET | `/api/dsoc/overview` | Overview JSON |
| GET/POST | `/api/dsoc/flowspec` | List / create flowspec rules |
| PUT | `/api/dsoc/flowspec/<id>/withdraw` | Withdraw flowspec rule |
| GET/POST | `/api/dsoc/rtbh` | List / trigger RTBH |
| POST | `/api/dsoc/rtbh/<id>/withdraw` | Withdraw RTBH entry |
| GET/POST | `/api/dsoc/scrubbing` | List / add scrubbing center |
| POST | `/api/dsoc/scrubbing/<id>/update-load` | Update current load |
| GET/POST | `/api/dsoc/threats` | List / ingest threats |
| GET/POST | `/api/dsoc/mitigations` | List / create mitigation |
| POST | `/api/dsoc/mitigations/<id>/complete` | Complete mitigation |
| POST | `/api/dsoc/iqe-query` | IQE natural-language query |

### IQE Collections

| Collection | Table |
|------------|-------|
| `dsoc.flowspec_rules` | `dsoc_flowspec_rules` |
| `dsoc.rtbh_entries` | `dsoc_rtbh_entries` |
| `dsoc.scrubbing_centers` | `dsoc_scrubbing_centers` |
| `dsoc.threats` | `dsoc_threats` |
| `dsoc.mitigations` | `dsoc_mitigations` |

### MCP Tools

| Tool | Purpose |
|------|---------|
| `dsoc_rtbh_trigger` | Trigger RTBH for a prefix |
| `dsoc_flowspec_activate` | Activate a flowspec rule by ID |
| `dsoc_threat_ingest` | Get DSOC overview metrics |

### Security Gates

| Gate ID | Condition | Severity |
|---------|-----------|----------|
| DSOC-RTBH-001 | RTBH entries with no auto_withdraw_minutes | WARN |
| DSOC-SCRUB-001 | Scrubbing center > 85% capacity | WARN |
| DSOC-THREAT-001 | High-confidence (≥90%) threats without mitigation | WARN |

### NIST Controls

SC-5 (Denial of Service Protection), IR-4 (Incident Handling), CP-2 (Contingency Plan), SI-3 (Malicious Code Protection), SA-9 (External System Services)

### Related Systems

- **NOCC** — alarms from scrubbing center overload published to `noc_alarms`
- **CCC** — circuit utilization during attacks tracked in `ccc_circuits.utilization_pct`
- **PMC** — RTBH community signaling coordinated with BGP peers via `peering_peers`
- **Genesis reflex** — `circuit_capacity_monitor` watches CCC circuits; future `dsoc_threat_monitor` will watch threat feed
