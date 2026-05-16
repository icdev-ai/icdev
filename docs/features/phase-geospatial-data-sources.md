# CUI // SP-CTI
# Phase: Geospatial Data Sources — Real-Time Ingestion Framework

**Status:** Operational
**Importers completed:** 2026-04-29 (AIS) / 2026-05-01 (ADS-B, UAS, TLE, Ground Vehicles)
**Interface fix:** 2026-05-10 (Leaflet z-index across all geo pages)

---

## Overview

ICDEV™ ingests real-time and near-real-time geospatial data from five public-domain
OSINT sources into the Strategos multi-domain COP and GeoSIGINT apps. All importers
write to shared SQLite / PostgreSQL tables via `get_connection()` and are air-gap
compatible.

---

## Data Sources

### 1. Aircraft Tracking (ADS-B)

| Attribute | Value |
|-----------|-------|
| **Importer** | `tools/strategos/adsb_importer.py` |
| **Source** | OpenSky Network REST API (no auth for basic use) |
| **Table** | `sg_aircraft_tracks` |
| **Key fields** | icao24, callsign, origin_country, lat, lon, baro_altitude, velocity, true_track, squawk, military_flag |
| **Military detection** | Squawk codes 7500/7600/7700/7777 + known ICAO24 hex prefixes (ae, 43, 07, 48, ac) |

**Usage:**
```bash
python -m tools.strategos.adsb_importer --bbox 20,60,50,140   # INDOPACOM
python -m tools.strategos.adsb_importer --bbox 45,22,52,40    # Black Sea
python -m tools.strategos.adsb_importer --file data/states.json  # air-gap
python -m tools.strategos.adsb_importer --seed                   # demo data
```

---

### 2. Maritime Vessel Tracking (AIS)

| Attribute | Value |
|-----------|-------|
| **Importer** | `tools/strategos/ais_importer.py` |
| **Source** | NOAA Marine Cadastre AIS CSV (public domain) |
| **Table** | `sg_vessel_tracks` |
| **Key fields** | mmsi, vessel_name, vessel_type, lat, lon, sog, cog, heading, status |
| **Vessel classification** | Fishing, cargo, tanker, warship (AIS type codes 35–37), unknown |
| **Dashboard** | `/strategos/maritime` |

**Download URL pattern:** `https://coast.noaa.gov/htdata/CMSP/AISDataHandler/{YEAR}/AIS_{DATE}.zip`

**Usage:**
```bash
python -m tools.strategos.ais_importer --csv path/to/AIS_2023_01_01.csv
python -m tools.strategos.ais_importer --download 2023-01-01 --bbox 20,100,50,130
python -m tools.strategos.ais_importer --csv data.csv --bbox 40,27,48,42 --sample 5
python -m tools.strategos.ais_importer --list                    # show loaded MMSIs
```

---

### 3. Satellite Passes (TLE / CelesTrak)

| Attribute | Value |
|-----------|-------|
| **Importer** | `tools/strategos/tle_importer.py` |
| **Source** | CelesTrak TLE sets (military, visual, Starlink, weather, GNSS, ISR) |
| **Table** | `sg_satellite_passes` |
| **Key fields** | norad_id, sat_name, sat_type, max_elevation, pass_start, pass_end, ground_track_json, military_flag |
| **Propagator** | SGP4 (falls back to simplified Keplerian if sgp4 not installed) |
| **Satellite categories** | Russian military, US military, Chinese military, commercial LEO, weather |

**Usage:**
```bash
python -m tools.strategos.tle_importer --fetch                  # live from CelesTrak
python -m tools.strategos.tle_importer --file data/military.tle # air-gap
python -m tools.strategos.tle_importer --seed                   # demo passes
```

---

### 4. UAS / Drone Tracks

| Attribute | Value |
|-----------|-------|
| **Importer** | `tools/strategos/uas_importer.py` |
| **Source** | FAA DroneZone CSV / analyst JSON |
| **Table** | `sg_uas_tracks` |
| **Key fields** | uas_id, operator, uas_type, operator_country, threat_level, payload, lat, lon, altitude_m, speed_kts, heading, anomaly_flag |
| **Threat levels** | low / medium / high / critical |

**Usage:**
```bash
python -m tools.strategos.uas_importer --file data/uas_tracks.json
python -m tools.strategos.uas_importer --seed
```

---

### 5. Ground Vehicle Events

| Attribute | Value |
|-----------|-------|
| **Importer** | `tools/strategos/ground_vehicle_importer.py` |
| **Sources** | GDELT GKG (vehicle/armor events), Oryx project (equipment losses) |
| **Table** | `sg_ground_vehicle_events` |
| **Key fields** | event_id, vehicle_type, country, threat_level, lat, lon, event_ts, source, description |

**Usage:**
```bash
python -m tools.strategos.ground_vehicle_importer --fetch       # GDELT live
python -m tools.strategos.ground_vehicle_importer --file data/events.json
python -m tools.strategos.ground_vehicle_importer --seed
```

---

## Database Migration

Migration **083** (`tools/db/migrations/083_sg_multidomain_tracks/up.py`) creates
all four airspace/ground tracking tables in a single transaction:

```
sg_aircraft_tracks        — ADS-B positions (indexed on icao24, track_ts)
sg_uas_tracks             — UAS/drone tracks (indexed on uas_id, track_ts)
sg_satellite_passes       — Satellite pass windows + ground_track_json
sg_ground_vehicle_events  — Ground vehicle sightings (indexed on country, event_ts)
```

The `sg_vessel_tracks` table is created by a separate migration run alongside
the AIS importer initialization.

---

## API Endpoints

All endpoints served under `/api/strategos`:

| Method | Path | Data source |
|--------|------|-------------|
| GET | `/airspace/aircraft` | `sg_aircraft_tracks` |
| GET | `/airspace/uas` | `sg_uas_tracks` |
| GET | `/airspace/satellites` | `sg_satellite_passes` |
| GET | `/airspace/ground-vehicles` | `sg_ground_vehicle_events` |
| POST | `/airspace/sync` | triggers all four importers |
| GET | `/maritime` | `sg_vessel_tracks` (page) |
| GET | `/api/strategos/maritime` | `sg_vessel_tracks` (JSON) |

---

## Resolved Interface Issues

### Leaflet Map Z-Index (2026-05-10, commit cc522359)

**Problem:** Leaflet map containers on GeoSIGINT and Strategos pages used GPU
compositing layers that stacked above the navbar dropdown menus, making navigation
unusable while a map was in view.

**Fix:** Added `position:relative; z-index:0; isolation:isolate` to every Leaflet
map `<div>` across all affected templates:

- `apps/geosigint/templates/` — all 8 map pages (a2ad, amphibious, index, island_chain,
  map, militia, sea, semiconductor)
- `icdev/apps/geosigint/templates/` — mirrored icdev/ package copies

**Status:** Resolved. Navbar dropdowns render above all map layers.

---

## GeoSIGINT Application (Indo-Pacific Theater)

The GeoSIGINT app (`apps/geosigint/`) extends the base geospatial framework with
theater-specific intelligence analysis across six strategic domains:

| Route | Module | Analysis |
|-------|--------|----------|
| `/geosigint/a2ad` | `a2ad_mapper.py` | A2AD weapon system range rings |
| `/geosigint/amphibious` | `amphibious_analyzer.py` | PLAN amphibious lift capacity |
| `/geosigint/strait-crossing` | `strait_crossing.py` | Taiwan Strait crossing corridors |
| `/geosigint/island-chain` | `island_chain_defense.py` | US/allied base coverage |
| `/geosigint/militia` | `militia_classifier.py` | Maritime militia detection (SCS) |
| `/geosigint/semiconductor` | `semiconductor_chain.py` | Supply chain disruption cascade |

All GeoSIGINT coordinates are OSINT-derived from public sources (CSIS, RAND, IISS,
NTI, Jane's, FAS, DoD public releases). No classified data.

---

## IQE Query Definitions

Three IQE seed queries are registered under `context/iqe/queries/mcip_geospatial/`:

| ID | File | Purpose |
|----|------|---------|
| IQE-GEO-001 | `01_osint_feeds_recent.iqe` | Recent OSINT signals from geospatial feed |
| IQE-GEO-002 | `02_satellite_anomalies.iqe` | EO/IR imagery scenes flagged as anomalies |
| IQE-GEO-003 | `03_conflict_events_by_type.iqe` | Geospatial conflict events grouped by type |

---

## Air-Gap Mode

All five importers support offline operation:

- `--file <path>` — load from a previously downloaded local file
- `--seed` — insert deterministic synthetic demo data
- `--clear` — purge all rows from the target table before re-importing

Set `OLLAMA_BASE_URL` and `ICDEV_LLM_PROVIDER=ollama` in `.env` to route any
LLM-backed enrichment through a local model.

---

## Classification

All geospatial records are tagged CUI // SP-CTI at the row level via the `source`
field. Military flags (`military_flag`) are explicitly tracked for aircraft and
satellites. No classified sources are used; all data is public-domain OSINT.
