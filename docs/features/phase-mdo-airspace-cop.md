# CUI // SP-CTI
# Phase: MDO Multi-Domain Airspace COP

**Route:** `/strategos/airspace`
**Completed:** 2026-05-01
**Interface fix:** 2026-05-10 (Leaflet z-index)
**Migration:** 083 (`083_sg_multidomain_tracks`)
**Nav entry:** Strategos → Multi-Domain COP

---

## Overview

The Multi-Domain Airspace Common Operating Picture (COP) is a real-time geospatial
intelligence layer within the Strategos module. It aggregates four tracking domains
onto a single Leaflet map, giving operators a unified view of the battlespace.

All four domain importers are operational and the real-time API endpoints are live.
The Leaflet map z-index interface issue (GPU compositing stacking above navbar
dropdowns) was resolved on 2026-05-10 by adding `position:relative; z-index:0;
isolation:isolate` to all map containers across the Strategos and GeoSIGINT apps.

---

## Domain Coverage

| Domain | Table | Source | Importer |
|--------|-------|--------|----------|
| Aircraft (ADS-B) | `sg_aircraft_tracks` | OpenSky Network REST API | `adsb_importer.py` |
| UAS / Drones | `sg_uas_tracks` | FAA DroneZone + ADS-B Exchange | `uas_importer.py` |
| Satellites | `sg_satellite_passes` | CelesTrak TLE (sgp4 propagator) | `tle_importer.py` |
| Ground Vehicles | `sg_ground_vehicle_events` | GDELT GKG + Oryx project | `ground_vehicle_importer.py` |

Maritime vessel tracking (AIS) lives on `/strategos/maritime` (see
`docs/features/phase-geospatial-data-sources.md`).

---

## API Endpoints

All endpoints are mounted under `/api/strategos`:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/airspace/aircraft` | Aircraft tracks grouped by ICAO24 for animation |
| GET | `/airspace/uas` | UAS/drone tracks grouped by uas_id |
| GET | `/airspace/satellites` | Satellite passes with ground track JSON |
| GET | `/airspace/ground-vehicles` | Ground vehicle events as point markers |
| POST | `/airspace/sync` | Trigger fresh import across all four domains |

Query params on `/airspace/ground-vehicles`: `country=<iso>`, `threat_level=<low|medium|high|critical>`.

---

## Architecture

### Importers (`tools/strategos/`)

- `adsb_importer.py` — polls OpenSky Network bounding box API; stores icao24,
  callsign, lat/lon, altitude, squawk, military flag into `sg_aircraft_tracks`.
- `uas_importer.py` — ingests FAA DroneZone CSV / analyst JSON; classifies
  threat level (low/medium/high/critical) into `sg_uas_tracks`.
- `tle_importer.py` — fetches CelesTrak TLE sets, propagates with sgp4 (falls
  back to simplified Keplerian if sgp4 not installed), stores pass windows and
  ground tracks into `sg_satellite_passes`.
- `ground_vehicle_importer.py` — pulls GDELT GKG vehicle/armor events and Oryx
  loss data into `sg_ground_vehicle_events`.

All importers are air-gap compatible via `--file` flag for local data ingestion
and `--seed` for synthetic demo data.

### Frontend

- **Template:** `tools/dashboard/templates/strategos/airspace.html`
- Full-viewport Leaflet map with dark military theme.
- Layer toggles: Aircraft / UAS / Satellites / Ground Vehicles.
- Color-coded markers: blue (aircraft), orange (UAS), purple (satellite), red (ground).
- MDO toolbar with live domain count badges and layer controls.
- Map container uses `position:relative; z-index:0; isolation:isolate` to prevent
  GPU compositing from stacking the map above navbar dropdowns.

### Database Migration (`tools/db/migrations/083_sg_multidomain_tracks/up.py`)

Creates four tables with appropriate indexes:

```
sg_aircraft_tracks       — ADS-B positions
sg_uas_tracks            — UAS/drone tracks
sg_satellite_passes      — Satellite pass predictions
sg_ground_vehicle_events — Ground vehicle sightings
```

---

## Access

- **URL:** `http://localhost:5050/strategos/airspace`
- **Nav path:** Strategos dropdown → Multi-Domain COP (blue diamond)

---

## Classification

All data stored under CUI // SP-CTI classification. Importers tag source field
on all records. Military flag is explicitly tracked for aircraft and satellites.
