# Tool Manifest — GeoSIGINT Indo-Pacific Analyzer

**Module:** `apps/geosigint/`
**Blueprint route prefix:** `/geosigint` (pages), `/api/geosigint` (API)
**Feature flag:** none (always enabled)
**Data source:** Open-source OSINT (CSIS, RAND, NTI, Jane's public data)

---

## Architecture

Two-blueprint factory pattern registered in `tools/dashboard/app.py`:
- `create_geosigint_blueprint()` — page routes at `/geosigint/`
- `create_geosigint_api_blueprint()` — API routes at `/api/geosigint/`

Both factories live in `apps/geosigint/blueprint.py`. Registration uses
`importlib.util.spec_from_file_location` with `__path__` and `__package__`
set on the synthetic `apps` and `apps.geosigint` modules so intra-package
imports (e.g. `from apps.geosigint.a2ad_mapper import ...`) resolve at
request time.

---

## Analyzers

### `apps/geosigint/a2ad_mapper.py`
A2/AD zone mapper — weapon system deployment sites and unclassified range rings.
- `WEAPON_SYSTEMS` — dict of 10+ PLA/PLAAF/PLAN systems (DF-21D, DF-26, HQ-9, etc.)
- `get_zones()` → list of zone dicts with center lat/lon, range_km, color
- API: `GET /api/geosigint/a2ad/zones` → `{systems, zones}`

### `apps/geosigint/amphibious_analyzer.py`
Taiwan Strait amphibious assault assessment.
- `LANDING_ZONES` — list of beach landing zones with slope_deg, width_km
- `AMPHIBIOUS_FLEET` — PLAN amphibious ship inventory
- `slope_viability(deg)` / `slope_color(deg)` — beach slope classification
- `calc_lift_capacity()` → troops/vehicles per wave
- `get_weather_windows()` → seasonal weather viability windows
- `get_crossing_analysis()` → corridor-by-corridor crossing risk
- `get_detection_curve()` → radar detection probability vs. range
- `get_summary()` → aggregate summary stats
- APIs: `GET /api/geosigint/amphibious/{summary,zones,lift,weather,crossing,detection}`

### `apps/geosigint/strait_crossing.py`
Taiwan Strait crossing time and intercept analysis.
- `PRIMARY_CORRIDOR` — main crossing corridor geometry (lat/lon waypoints)
- `RADAR_SYSTEMS` / `ROCAF_BASES` — Taiwan defensive disposition
- `get_speed_matrix()` → crossing time by vessel class × speed
- `get_intercept_table(speed_kts)` → intercept probability by zone
- `get_detection_curve()` → detection probability vs. crossing progress
- `get_summary()` → aggregate summary stats
- APIs: `GET /api/geosigint/strait-crossing/{summary,speed-matrix,intercept,detection,corridor}`

### `apps/geosigint/island_chain_defense.py`
First/Second Island Chain base network and THAAD battery disposition.
- `THAAD_BATTERIES` — list of THAAD battery locations with coverage radii
- `CHOKEPOINTS` — strategic maritime chokepoints with coordinates
- `get_all_bases()` → full base list (US, JP, AU, allied) with tier labels
- `get_summary()` → aggregate summary stats
- APIs: `GET /api/geosigint/island-chain/{summary,bases,thaad,chokepoints}`

### `apps/geosigint/militia_classifier.py`
People's Armed Forces Maritime Militia (PAFMM) vessel classifier.
- `DISPUTED_ZONES` — SCS disputed zone rectangles with sovereignty claims
- `ARTIFICIAL_ISLANDS` — PRC artificial island coordinates and features
- `classify_fleet(vessels)` → 3-layer classification (L1 AIS pattern, L2 behavior, L3 network)
- `detect_swarm_events(vessels)` → temporal clustering → swarm event list
- `get_summary()` → aggregate summary stats
- APIs: `GET /api/geosigint/militia/summary`, `POST /api/geosigint/militia/{classify,swarms}`, `GET /api/geosigint/militia/zones`

### `apps/geosigint/semiconductor_chain.py`
Global semiconductor supply chain disruption simulator.
- `DISRUPTION_SCENARIOS` — list of 5 pre-built disruption scenarios (Taiwan blockade, ASML embargo, REE cutoff, etc.)
- `simulate_disruption(node_id, severity)` → cascade impact dict
- `run_scenario(scenario_id)` → full cascade from named scenario
- `get_exposure_map()` → all supply chain nodes with criticality scores + lat/lon
- `get_ree_flow(element)` → rare earth element flow network
- `get_summary()` → aggregate summary stats
- APIs: `GET /api/geosigint/semiconductor/{summary,scenarios,exposure-map,ree-flow}`, `POST /api/geosigint/semiconductor/simulate`

---

## Templates

| Template | Route | Description |
|----------|-------|-------------|
| `apps/geosigint/templates/geosigint_index.html` | `/geosigint/` | Overview — Leaflet map + 6 analyzer cards + summary stats |
| `apps/geosigint/templates/a2ad.html` | `/geosigint/a2ad` | A2/AD zone mapper — range rings on Leaflet map |
| `apps/geosigint/templates/amphibious.html` | `/geosigint/amphibious` | Amphibious assessment — landing zones, lift capacity, weather windows |
| `apps/geosigint/templates/strait_crossing.html` | `/geosigint/strait-crossing` | Strait crossing — speed matrix, intercept table, detection curve |
| `apps/geosigint/templates/island_chain.html` | `/geosigint/island-chain` | Island chain — base network map, THAAD batteries, chokepoints |
| `apps/geosigint/templates/militia.html` | `/geosigint/militia` | Militia classifier — vessel input form, 3-layer scoring, swarm detection |
| `apps/geosigint/templates/semiconductor.html` | `/geosigint/semiconductor` | Semiconductor simulator — scenario cards, cascade chart, REE flow table |

All templates extend `base.html` and include Leaflet CSS/JS via `{% block head %}` / `{% block scripts %}`.

---

## Page Routes (7)

| Route | Template |
|-------|----------|
| `GET /geosigint/` | `geosigint_index.html` |
| `GET /geosigint/a2ad` | `a2ad.html` |
| `GET /geosigint/amphibious` | `amphibious.html` |
| `GET /geosigint/strait-crossing` | `strait_crossing.html` |
| `GET /geosigint/island-chain` | `island_chain.html` |
| `GET /geosigint/militia` | `militia.html` |
| `GET /geosigint/semiconductor` | `semiconductor.html` |

## API Routes (23)

| Route | Method | Module |
|-------|--------|--------|
| `/api/geosigint/a2ad/zones` | GET | a2ad_mapper |
| `/api/geosigint/amphibious/summary` | GET | amphibious_analyzer |
| `/api/geosigint/amphibious/zones` | GET | amphibious_analyzer |
| `/api/geosigint/amphibious/lift` | GET | amphibious_analyzer |
| `/api/geosigint/amphibious/weather` | GET | amphibious_analyzer |
| `/api/geosigint/amphibious/crossing` | GET | amphibious_analyzer |
| `/api/geosigint/amphibious/detection` | GET | amphibious_analyzer |
| `/api/geosigint/strait-crossing/summary` | GET | strait_crossing |
| `/api/geosigint/strait-crossing/speed-matrix` | GET | strait_crossing |
| `/api/geosigint/strait-crossing/intercept` | GET | strait_crossing |
| `/api/geosigint/strait-crossing/detection` | GET | strait_crossing |
| `/api/geosigint/strait-crossing/corridor` | GET | strait_crossing |
| `/api/geosigint/island-chain/summary` | GET | island_chain_defense |
| `/api/geosigint/island-chain/bases` | GET | island_chain_defense |
| `/api/geosigint/island-chain/thaad` | GET | island_chain_defense |
| `/api/geosigint/island-chain/chokepoints` | GET | island_chain_defense |
| `/api/geosigint/militia/summary` | GET | militia_classifier |
| `/api/geosigint/militia/classify` | POST | militia_classifier |
| `/api/geosigint/militia/swarms` | POST | militia_classifier |
| `/api/geosigint/militia/zones` | GET | militia_classifier |
| `/api/geosigint/semiconductor/summary` | GET | semiconductor_chain |
| `/api/geosigint/semiconductor/scenarios` | GET | semiconductor_chain |
| `/api/geosigint/semiconductor/simulate` | POST | semiconductor_chain |
| `/api/geosigint/semiconductor/exposure-map` | GET | semiconductor_chain |
| `/api/geosigint/semiconductor/ree-flow` | GET | semiconductor_chain |
