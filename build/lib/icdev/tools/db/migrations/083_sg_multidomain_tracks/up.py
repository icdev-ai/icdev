"""
Migration 083 — Multi-Domain Tracking tables.

Creates (if not present):
  sg_aircraft_tracks   — ADS-B aircraft positions (OpenSky Network)
  sg_uas_tracks        — UAS/drone tracks (FAA DroneZone + simulated)
  sg_satellite_passes  — Satellite pass predictions (CelesTrak TLE)
  sg_ground_vehicle_events — Ground vehicle sightings (GDELT/OSINT)
"""
from tools.db.storage import get_connection, is_pg


def up(conn=None) -> None:
    conn = get_connection()
    ph = "SERIAL PRIMARY KEY" if is_pg() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    try:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS sg_aircraft_tracks (
                id {ph},
                icao24 TEXT NOT NULL,
                callsign TEXT,
                origin_country TEXT,
                lat REAL,
                lon REAL,
                baro_altitude REAL,
                geo_altitude REAL,
                velocity REAL,
                true_track REAL,
                vertical_rate REAL,
                on_ground INTEGER DEFAULT 0,
                squawk TEXT,
                aircraft_type TEXT DEFAULT 'unknown',
                military_flag INTEGER DEFAULT 0,
                track_ts TEXT NOT NULL,
                source TEXT DEFAULT 'opensky',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_aircraft_icao24 "
            "ON sg_aircraft_tracks(icao24)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_aircraft_ts "
            "ON sg_aircraft_tracks(track_ts)"
        )

        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS sg_uas_tracks (
                id {ph},
                uas_id TEXT NOT NULL,
                operator TEXT,
                uas_type TEXT DEFAULT 'fixed_wing',
                lat REAL,
                lon REAL,
                altitude_m REAL,
                speed_kts REAL,
                heading REAL,
                threat_level TEXT DEFAULT 'low',
                operator_country TEXT,
                payload TEXT,
                track_ts TEXT NOT NULL,
                source TEXT DEFAULT 'faa',
                anomaly_flag INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_uas_id "
            "ON sg_uas_tracks(uas_id)"
        )

        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS sg_satellite_passes (
                id {ph},
                norad_id TEXT NOT NULL,
                sat_name TEXT,
                sat_type TEXT DEFAULT 'other',
                tle_line1 TEXT,
                tle_line2 TEXT,
                pass_start TEXT,
                pass_end TEXT,
                max_elevation REAL,
                aos_az REAL,
                los_az REAL,
                ground_track_json TEXT,
                observer_lat REAL DEFAULT 25.0,
                observer_lon REAL DEFAULT 90.0,
                military_flag INTEGER DEFAULT 0,
                source TEXT DEFAULT 'celestrak',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_sat_norad "
            "ON sg_satellite_passes(norad_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_sat_pass_start "
            "ON sg_satellite_passes(pass_start)"
        )

        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS sg_ground_vehicle_events (
                id {ph},
                event_id TEXT,
                vehicle_type TEXT DEFAULT 'military',
                lat REAL,
                lon REAL,
                country TEXT,
                actor TEXT,
                description TEXT,
                event_ts TEXT,
                threat_level TEXT DEFAULT 'medium',
                source TEXT DEFAULT 'gdelt',
                source_url TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_gv_event_ts "
            "ON sg_ground_vehicle_events(event_ts)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_gv_country "
            "ON sg_ground_vehicle_events(country)"
        )

        conn.commit()
        print(
            "Migration 083 up: sg_aircraft_tracks, sg_uas_tracks, "
            "sg_satellite_passes, sg_ground_vehicle_events created."
        )
    finally:
        conn.close()


if __name__ == "__main__":
    up()
