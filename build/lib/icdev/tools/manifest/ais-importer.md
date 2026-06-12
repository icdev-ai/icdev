# AIS Vessel Data Importer

Parses NMEA AIS (Automatic Identification System) files via `pyais` and persists
vessel track records to the `sg_tracks` table in the geosigint database.
Air-gap safe — reads local files only, no network I/O.

## Tools

### `tools/ais/ais_importer.py`
**Purpose:** Import NMEA AIS files → `sg_tracks` (mmsi, lat, lon, speed, heading,
timestamp, vessel_type).

**Supported message types:** 1/2/3 (Class A position), 5 (vessel type lookup),
18 (Class B position), 21 (Aid-to-Navigation).

**CLI:**
```bash
python tools/ais/ais_importer.py --file data.nmea --json
python tools/ais/ais_importer.py --dir /path/to/nmea/ --json
python tools/ais/ais_importer.py --file a.nmea --file b.nmea
```

**Output (--json):**
```json
{
  "status": "ok",
  "files_processed": 1,
  "tracks_inserted": 4823,
  "errors": [],
  "vessel_types_seen": 12
}
```

**Two-pass strategy:** Pass 1 harvests vessel types from type 5 messages into a
per-mmsi cache. Pass 2 inserts position rows with resolved vessel type labels.
Type 5 fields without position (lat/lon) are not inserted as tracks.

**Schema target:** `apps/geosigint/models.py::sg_tracks`

**Dependency:** `pyais>=2.0` (see `requirements.txt`). Gracefully reports an error
if not installed rather than crashing.
