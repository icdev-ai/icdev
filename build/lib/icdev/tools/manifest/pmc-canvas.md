# Peering Management Canvas (PMC) — Tool Manifest

Canvas key: `pmc` | Feature flag: `ICDEV_PMC_ENABLED` | DB: `data/pmc_canvas.db` (PostgreSQL default)

## Modules

| Module | Path | Functions |
|--------|------|-----------|
| DB init | `tools/pmc_canvas/db/init_db.py` | `init_db()`, `get_connection()` |
| Constants | `tools/pmc_canvas/constants.py` | `PMC_FEATURE_FLAG`, `PEER_TYPES`, `RPKI_STATUSES`, `INTENT_RULES`, `PEERING_SCORE_WEIGHTS` |
| PeeringDB Client | `tools/pmc_canvas/peeringdb_client.py` | `get_asn_info(asn)`, `search_peers_at_ix(ix_id, our_asn)`, `sync_peer_from_peeringdb(asn, conn)` |
| RPSL Generator | `tools/pmc_canvas/rpsl_generator.py` | `generate_aut_num()`, `generate_route_object()`, `generate_as_set()`, `export_to_irr()` |
| RPKI Validator | `tools/pmc_canvas/rpki_validator.py` | `validate_prefix()`, `validate_peer_prefixes()`, `generate_roa_report()`, `get_rpki_summary()` |
| BGP Config Gen | `tools/pmc_canvas/bgp_config_generator.py` | `generate_peer_session(peer, our_asn, os_type, neighbor_ip, ...)`, `generate_prefix_list()` |
| Decision Engine | `tools/pmc_canvas/peering_decision_engine.py` | `evaluate_peer()`, `generate_peering_brief()` |
| PMC Aggregator | `tools/pmc_canvas/pmc_aggregator.py` | `get_pmc_overview()` |
| Blueprint | `tools/pmc_canvas/blueprint.py` | `create_pmc_blueprint()` |
| IQE Adapter | `tools/iqe/adapters/pmc.py` | 5 collections: `pmc.peers`, `pmc.ix_memberships`, `pmc.prefixes`, `pmc.peering_requests`, `pmc.route_policies` |

## DB Tables

- `peering_peers` — BGP peer registry (ASN, org, type, policy, status, PeeringDB sync)
- `peering_ix` — Internet Exchange memberships (name, city, country, our IPs, cost)
- `peering_prefixes` — Per-peer prefix list with RPKI/IRR status
- `peering_requests` — Peering session request lifecycle
- `peering_policies` — BGP import/export route policies
- `pmc_audit` — Append-only audit trail (NIST AU)

## Routes

```
GET  /pmc                        — overview dashboard
GET  /pmc/peers                  — BGP peer management
GET  /pmc/peers/<id>             — peer detail + config gen + RPSL
GET  /pmc/ix                     — Internet Exchange memberships
GET  /pmc/rpki                   — RPKI validation dashboard
GET  /pmc/policies               — route policies + aut-num generator
GET  /pmc/requests               — peering request lifecycle
GET  /api/pmc/overview           — aggregated PMC overview JSON
GET  /api/pmc/peers              — list all peers
POST /api/pmc/peers              — add peer
GET  /api/pmc/peers/lookup       — lookup ASN in PeeringDB (no DB write)
POST /api/pmc/peers/sync-asn     — sync ASN from PeeringDB → DB
GET  /api/pmc/peers/<id>         — get peer
POST /api/pmc/peers/<id>/sync    — re-sync peer from PeeringDB
POST /api/pmc/peers/<id>/evaluate — run 6-dimension decision engine
POST /api/pmc/peers/<id>/validate-rpki — validate all prefixes for peer
POST /api/pmc/peers/<id>/generate-config — generate BGP session config
GET  /api/pmc/peers/<id>/rpsl    — generate aut-num RPSL object
GET  /api/pmc/peers/<id>/prefixes — list peer prefixes
GET  /api/pmc/ix                 — list IX memberships
POST /api/pmc/ix                 — add IX membership
GET  /api/pmc/rpki/report        — RPKI summary with invalid/notfound rows
GET  /api/pmc/rpki/validate      — validate single prefix (query params)
POST /api/pmc/rpki/validate-all  — validate all prefixes in DB
GET  /api/pmc/policies           — list route policies
POST /api/pmc/policies           — create route policy
POST /api/pmc/export/aut-num     — generate aut-num RPSL
GET  /api/pmc/requests           — list peering requests
POST /api/pmc/requests           — create peering request
PUT  /api/pmc/requests/<id>      — update request status
POST /api/pmc/iqe-query          — IQE NL query
```

## BGP OS Types Supported

`ios_xr` | `junos` | `eos` | `nokia_sros` | `frr` | `bird2`

## Peering Decision Engine Dimensions

1. **traffic_ratio** (25%) — Balanced exchange ratio
2. **prefix_count** (15%) — Healthy prefix bounds
3. **rpki_validity** (20%) — ROA coverage percentage
4. **irr_registration** (15%) — IRR route object hygiene
5. **noc_responsiveness** (10%) — NOC contact availability
6. **ix_presence** (15%) — Shared IX memberships

Score ≥ 0.75 → `open` | Score ≥ 0.45 → `selective` | Score < 0.45 → `no`

## IQE Seed Queries

Located at `context/iqe/queries/pmc_canvas/`:
- `01_active_peers.iqe`
- `02_rpki_invalid_prefixes.iqe`
- `03_ix_by_cost.iqe`
- `04_pending_peering_requests.iqe`
- `05_high_prefix_peers.iqe`
