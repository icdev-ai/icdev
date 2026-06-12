# ISP / Carrier Tools — Tool Manifest

Domain: ISP/Carrier operations | NDC extensions + DataBridge connectors

## Modules

| Module | Path | Functions |
|--------|------|-----------|
| ISP Capacity Planner | `tools/network/isp_capacity_planner.py` | `model_traffic_growth()`, `dwdm_capacity_analysis()`, `dark_fiber_roi()`, `capacity_planning_summary()` |
| Equinix ECX Connector | `tools/databridge/connectors/equinix_ecx_connector.py` | `read()`, `write()`, `health_check()` — tables: connections, ports, loa_requests, metrics |
| Megaport Connector | `tools/databridge/connectors/megaport_connector.py` | `read()`, `write()`, `health_check()` — tables: ports, vxcs, mcrs, services |

## Config Generator OS Types (NDC extensions)

Added to `tools/network/config_generator.py`:

| OS Type | `_DEFAULT_OS` device types | Interface naming |
|---------|--------------------------|-----------------|
| `ios_xr` | pe-router, p-router, asr, ncs | `HundredGigE0/0/0/<N>`, `Loopback<N>` |
| `nokia_sros` | sr-os, 7750sr, 7950xrs | `1/1/<N>`, `system` (loopback) |

Template features:
- **IOS-XR**: service provider BGP with per-neighbor route-policy stanzas, SSH v2, exec-timeout
- **Nokia SR OS**: `configure router bgp` syntax, group-based neighbor config, system loopback

## Capacity Planner Functions

### `model_traffic_growth(historical_gbps, forecast_months, method)`
- Methods: `cagr` (default), `linear`, `exponential`
- Returns: CAGR %, doubling time (years), 12-month forecast, recommended capacity with 40% headroom

### `dwdm_capacity_analysis(fiber_pairs, modulation, grid, ...)`
- Modulations: 100G-DP-QPSK, 200G-DP-16QAM, 400G-DP-16QAM, 400G-DP-64QAM, 800G, 1.2T
- Grids: C-band-50GHz (96 ch), C-band-37.5GHz (128 ch flex), L-band, S-band
- Returns: utilization %, available channels, reach estimate (km), upgrade options

### `dark_fiber_roi(route_km, fiber_pairs, ...)`
- Modes: IRU (one-time capex), lease (annual), build (per-km capex)
- Returns: NPV, IRR, break-even year, 20-year cash flow table, recommendation string

## Equinix ECX Connector

Auth: OAuth2 client credentials (`EQUINIX_ECX_CLIENT_ID` + `EQUINIX_ECX_CLIENT_SECRET`)
Base URL: `https://api.equinix.com/fabric/v4`

| Table | Description |
|-------|-------------|
| `connections` | Virtual circuits (A→Z side, bandwidth, state) |
| `ports` | Physical ports at IBX facilities |
| `loa_requests` | Letter of Authorization lifecycle |
| `metrics` | Per-port/circuit throughput and utilization |

## Megaport Connector

Auth: JWT via `MEGAPORT_USER` + `MEGAPORT_PASS` (24h TTL)
Base URL: `https://api.megaport.com/v2`

| Table | Description |
|-------|-------------|
| `ports` | Physical Megaport ports |
| `vxcs` | Virtual Cross Connects (Layer 2) |
| `mcrs` | Megaport Cloud Routers (virtual BGP) |
| `services` | Aggregated view of all services |

## AppForge Verticals Added

`args/appforge_config.yaml` Telecommunications entry now includes:
- Telecommunications / ISP Transit
- Telecommunications / ISP Peering & Interconnect
- Telecommunications / Enterprise WAN & SD-WAN
- Telecommunications / Carrier Ethernet & MEF 3.0
- Telecommunications / Wholesale & Access Networks

## FCC Compliance Tools (Phase 4)

| Module | Path | Functions |
|--------|------|-----------|
| FCC Compliance | `tools/network/fcc_compliance.py` | `calea_checklist()`, `part36_assessment()`, `nanp_number_inventory()`, `e911_capability_check()` |
| Telco RFP Adapter | `tools/govcon/telco_rfp_adapter.py` | `parse_fcc_form470()`, `generate_bead_compliance_matrix()`, `score_rdof_eligibility()` |
| Transit Pricing Benchmark | `tools/pmc_canvas/transit_pricing_benchmark.py` | `benchmark_transit_cost()`, `peering_vs_transit_roi()`, `get_ix_benchmarks()` |
| PMC Config | `args/pmc_config.yaml` | Decision engine weights, RPKI thresholds, PeeringDB sync, BGP config defaults |

### FCC Compliance Coverage

| Module | Regulations |
|--------|-------------|
| CALEA | 17-item checklist: LI gateway, CC/CII delivery, BIAS broadband, packet-mode DPI, non-disclosure, security |
| Part 36 | Jurisdictional separations: interstate/intrastate cost allocation, USF/NECA, access charges |
| NANP | Number utilization (≥60%), LNP (1 biz day), STIR/SHAKEN, robocall mitigation, CNAM |
| E-911 | 12-item checklist: ANI/ALI, dispatchable location, MLTS direct dial, NG911, z-axis, PSAP callback |

### Telco RFP Programs

| Program | Function | Key Output |
|---------|----------|-----------|
| FCC E-Rate Form 470 | `parse_fcc_form470()` | RFP complexity score, service categories, response requirements |
| BEAD (NTIA) | `generate_bead_compliance_matrix()` | 12-criterion compliance matrix, gap list, eligibility decision |
| RDOF (FCC) | `score_rdof_eligibility()` | Tier 1–5 eligibility, recommended tier, estimated support |

### Transit Pricing Benchmarks (2026-Q1)

| Region | 1G ($/Mbps/mo) | 10G ($/Mbps/mo) | 100G ($/Mbps/mo) |
|--------|----------------|-----------------|------------------|
| North America | 0.10–0.35 | 0.06–0.22 | 0.03–0.12 |
| Europe | 0.08–0.28 | 0.05–0.18 | 0.025–0.10 |
| APAC | 0.40–2.00 | 0.20–1.00 | 0.10–0.60 |
| LATAM | 0.50–3.00 | 0.30–1.80 | 0.15–1.00 |
| MEA | 1.00–6.00 | 0.60–4.00 | 0.30–2.50 |

IXP benchmarks: 9 exchanges across NA, EU, APAC, LATAM, MEA.

## Genesis Reflexes (Phase 5)

| Reflex | Path | Cadence | Logic |
|--------|------|---------|-------|
| NOCC Alarm Triage | `tools/genesis/reflexes/nocc_alarm_triage.py` | 2h | Detect alarm storms (≥5 alarms/device/15min) → auto-create P2 incidents |
| NOCC SLA Watcher | `tools/genesis/reflexes/nocc_sla_watcher.py` | 4h | Mark breach=1, publish warning when measured < target − 0.5% |
| BGP Route Monitor | `tools/genesis/reflexes/bgp_route_monitor.py` | 1h | LibreNMS/SolarWinds BGP session query → create/clear noc_alarms |
| Peering Health Monitor | `tools/genesis/reflexes/peering_health_monitor.py` | 6h | PeeringDB re-sync (>7d stale) + RPKI re-validate (traffic_ratio ≥ 0.5) |

All reflexes follow the canonical pattern: `CADENCE_HOURS` constant, `run(ctx, conn) -> dict` signature, no LLM calls, dry_run support.

## NOC Skill Card

`icdev-noc` skill: `.agents/skills/icdev-noc/SKILL.md` — invokes noc_aggregator, alarm_correlator, sla_predictor, maintenance_planner, pmc_aggregator, and Genesis reflex status.

## Security Gates Added

| Gate ID | Canvas | Trigger | Severity |
|---------|--------|---------|---------|
| NOC-OPS-001 | NOCC | P1 incident open >15min without ACK | WARN |
| PMC-RPKI-001 | PMC | RPKI-invalid prefix on ≥1Gbps peer | WARN |
| PMC-IRR-001 | PMC | Open-policy peer with >20% unregistered IRR | WARN |

## MCP Tools Registered

| Tool | Category | Description |
|------|----------|-------------|
| `noc_alarm_ingest` | nocc | Ingest and correlate alarm |
| `noc_incident_create` | nocc | Create P1–P4 incident |
| `pmc_peer_evaluate` | pmc | 6-dimension peering decision engine |
| `pmc_rpki_validate` | pmc | Cloudflare RPKI prefix validation |

## Env Variables

```
EQUINIX_ECX_CLIENT_ID=       # Equinix ECX OAuth2 client ID
EQUINIX_ECX_CLIENT_SECRET=   # Equinix ECX OAuth2 client secret
MEGAPORT_USER=               # Megaport login username
MEGAPORT_PASS=               # Megaport login password
PEERINGDB_API_KEY=           # PeeringDB API key (optional, increases rate limits)
```
