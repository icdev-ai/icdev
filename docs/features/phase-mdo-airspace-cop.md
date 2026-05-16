# CUI // SP-CTI
# Phase: MDO Multi-Domain Airspace COP

**Route:** `/strategos/airspace`
**Completed:** 2026-05-01
**Migration:** 083 (`083_sg_multidomain_tracks`)
**Nav entry:** Strategos → Multi-Domain COP

---

## Overview

The Multi-Domain Airspace Common Operating Picture (COP) is a real-time geospatial
intelligence layer within the Strategos module. It aggregates four tracking domains
onto a single Leaflet map, giving operators a unified view of the battlespace.

---

## Domain Coverage

| Domain | Table | Source |
|--------|-------|--------|
| Aircraft (ADS-B) | `sg_aircraft_tracks` | OpenSky Network REST API |
| UAS / Drones | `sg_uas_tracks` | FAA DroneZone + ADS-B Exchange |
| Satellites | `sg_satellite_passes` | CelesTrak TLE (sgp4 propagator) |
| Ground Vehicles | `sg_ground_vehicle_events` | GDELT GKG + Oryx project |

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

### Frontend (`tools/dashboard/templates/strategos/airspace.html`)

- Full-viewport Leaflet map with dark military theme.
- Layer toggles: Aircraft / UAS / Satellites / Ground Vehicles.
- Color-coded markers: blue (aircraft), orange (UAS), purple (satellite), red (ground).
- MDO toolbar with live domain count badges and layer controls.

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
on all records. Military flag is explicitly tracked for aircraft.
