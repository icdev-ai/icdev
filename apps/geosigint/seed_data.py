"""GeoSIGINT — Seed realistic public-domain SIGINT data.

Uses real-world:
- ITU/FCC frequency band allocations
- WSPRnet-style propagation spot patterns
- Real geographic coordinates of public facilities
- NOAA-style space weather data
"""

from __future__ import annotations

import json
import math
import random
import uuid
from datetime import datetime, timedelta, timezone

from models import get_connection, init_db

# ── Real ITU frequency bands ──────────────────────────────
BANDS = [
    ("hf_20m", "20m Amateur", 14.0e6, 14.35e6, "Amateur", "ITU-1"),
    ("hf_40m", "40m Amateur", 7.0e6, 7.3e6, "Amateur", "ITU-1"),
    ("hf_80m", "80m Amateur", 3.5e6, 4.0e6, "Amateur", "ITU-1"),
    ("hf_marine", "HF Maritime Mobile", 4.0e6, 4.438e6, "Maritime", "ITU-1"),
    ("hf_aero", "HF Aeronautical", 5.45e6, 5.73e6, "Aeronautical", "ITU-1"),
    ("vhf_fm", "VHF FM Broadcast", 88.0e6, 108.0e6, "Broadcast", "ITU-1"),
    ("vhf_air", "VHF Airband", 118.0e6, 137.0e6, "Aeronautical", "ITU-1"),
    ("vhf_marine", "VHF Marine", 156.0e6, 162.025e6, "Maritime", "ITU-1"),
    ("uhf_70cm", "70cm Amateur", 430.0e6, 440.0e6, "Amateur", "ITU-1"),
    ("uhf_ism", "ISM 900MHz", 902.0e6, 928.0e6, "ISM", "ITU-2"),
    ("l_band_gps", "L-Band GPS", 1.575e9, 1.576e9, "Navigation", "Global"),
    ("s_band_wx", "S-Band Weather Radar", 2.7e9, 2.9e9, "Radar", "ITU-1"),
]

# ── Real public locations (military bases, embassies, comms sites — all public) ──
STATIONS = [
    ("rx_norfolk", "K4NVA", "Norfolk Naval Station", 36.9461, -76.3013, "receiver"),
    ("rx_sandiego", "W6SD", "San Diego Naval Base", 32.6841, -117.1497, "receiver"),
    ("rx_pearl", "KH6PH", "Pearl Harbor", 21.3544, -157.9500, "receiver"),
    ("rx_rota", "EA4RT", "Naval Station Rota", 36.6312, -6.3499, "receiver"),
    ("rx_yokosuka", "JA1YK", "Fleet Activities Yokosuka", 35.2836, 139.6700, "receiver"),
    ("rx_bahrain", "A92BH", "NSA Bahrain", 26.2361, 50.5860, "receiver"),
    ("rx_sigonella", "IT9SIG", "NAS Sigonella", 37.4017, 14.9222, "receiver"),
    ("rx_guam", "KH2GM", "Naval Base Guam", 13.4443, 144.7937, "receiver"),
    ("rx_diegogarcia", "VQ9DG", "Diego Garcia", -7.3195, 72.4229, "receiver"),
    ("rx_misawa", "JA7MS", "Misawa Air Base", 40.7032, 141.3686, "receiver"),
    ("rx_menwith", "G4MWH", "Menwith Hill Station", 54.0056, -1.6883, "receiver"),
    ("rx_pine_gap", "VK8PG", "Pine Gap", -23.7990, 133.7370, "receiver"),
]

# ── Emitter archetypes (fictional but realistic) ──────────
EMITTER_TYPES = [
    ("comms", "HF Communications", [7.0e6, 14.0e6, 5.5e6], ["CW", "PSK", "FSK"]),
    ("radar", "S-Band Radar", [2.7e9, 2.8e9, 2.85e9], ["Pulse"]),
    ("beacon", "Navigation Beacon", [1.575e9, 1.576e9], ["BPSK"]),
    ("maritime", "Maritime Comms", [156.8e6, 4.2e6], ["FM", "AM"]),
    ("aero", "Aeronautical", [121.5e6, 125.0e6, 130.0e6], ["AM"]),
]


def _uid() -> str:
    return uuid.uuid4().hex[:12]


def _jitter(val: float, pct: float = 0.02) -> float:
    return val * (1 + random.uniform(-pct, pct))


def seed_bands(conn):
    """Insert ITU frequency bands."""
    for b in BANDS:
        conn.execute(
            "INSERT OR IGNORE INTO frequency_bands VALUES (?,?,?,?,?,?)", b
        )


def seed_stations(conn):
    """Insert receiver stations."""
    for s in STATIONS:
        conn.execute(
            "INSERT OR IGNORE INTO stations (station_id, callsign, name, lat, lon, station_type) "
            "VALUES (?,?,?,?,?,?)", s
        )


def seed_emitters(conn, count: int = 40):
    """Generate realistic emitter entities at plausible locations."""
    emitters = []
    regions = [
        (35.0, 45.0, 25.0, 65.0, "Middle East"),
        (20.0, 50.0, 100.0, 150.0, "Western Pacific"),
        (50.0, 70.0, 10.0, 60.0, "Northern Europe/Russia"),
        (30.0, 45.0, -10.0, 35.0, "Mediterranean"),
        (-10.0, 15.0, 60.0, 110.0, "Indian Ocean"),
    ]
    for i in range(count):
        etype, ename, freqs, mods = random.choice(EMITTER_TYPES)
        region = random.choice(regions)
        lat = random.uniform(region[0], region[1])
        lon = random.uniform(region[2], region[3])
        freq = random.choice(freqs)
        mod = random.choice(mods)
        eid = f"em_{_uid()}"
        now = datetime.now(timezone.utc)
        first = now - timedelta(days=random.randint(30, 365))
        emitters.append((
            eid, f"{ename}-{i+1:03d}", lat, lon, freq, mod,
            first.isoformat(), now.isoformat(),
            0, random.uniform(0.4, 0.95), etype
        ))
        conn.execute(
            "INSERT OR IGNORE INTO emitters VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            emitters[-1]
        )
    return emitters


def seed_signals(conn, emitters, days: int = 30, signals_per_day: int = 120):
    """Generate signal detections — realistic temporal/spatial patterns."""
    now = datetime.now(timezone.utc)
    stations = [dict(r) for r in conn.execute("SELECT * FROM stations").fetchall()]
    bands = {r["band_id"]: r for r in conn.execute("SELECT * FROM frequency_bands").fetchall()}

    all_signals = []
    for day_offset in range(days):
        dt_base = now - timedelta(days=days - day_offset)
        for _ in range(signals_per_day):
            emitter = random.choice(emitters)
            eid, ename, elat, elon, efreq, emod = emitter[:6]
            etype = emitter[10]

            # Pick nearest station(s) that could detect
            station = min(stations, key=lambda s: math.hypot(s["lat"] - elat, s["lon"] - elon))

            # Time jitter
            ts = dt_base + timedelta(
                hours=random.randint(0, 23),
                minutes=random.randint(0, 59),
                seconds=random.randint(0, 59),
            )

            # Frequency jitter
            freq = _jitter(efreq, 0.001)

            # Power model: farther = weaker
            dist_km = math.hypot(elat - station["lat"], elon - station["lon"]) * 111
            base_power = random.uniform(-40, 10) if etype == "radar" else random.uniform(-80, -20)
            power = base_power - 20 * math.log10(max(dist_km, 1)) / math.log10(10)
            snr = power + random.uniform(60, 90)

            # Find matching band
            band_id = None
            for bid, bdata in bands.items():
                if bdata["min_freq_hz"] <= freq <= bdata["max_freq_hz"]:
                    band_id = bid
                    break

            sid = f"sig_{_uid()}"
            classification = "unclassified"
            if random.random() < 0.08:
                classification = "of_interest"
            if random.random() < 0.03:
                classification = "anomalous"

            bearing = math.degrees(math.atan2(elon - station["lon"], elat - station["lat"])) % 360
            bw = random.choice([200, 3000, 6000, 25000, 200000]) if etype != "radar" else random.choice([1e6, 2e6, 5e6])

            all_signals.append((
                sid, ts.isoformat(), freq, power, snr, bw, emod,
                round(bearing, 1), elat + random.uniform(-0.5, 0.5),
                elon + random.uniform(-0.5, 0.5),
                station["station_id"], band_id, classification,
                json.dumps({"emitter_id": eid, "emitter_type": etype})
            ))

            # Update emitter signal count
            conn.execute(
                "UPDATE emitters SET signal_count = signal_count + 1, last_seen = ? WHERE emitter_id = ?",
                (ts.isoformat(), eid)
            )

    # Batch insert
    conn.executemany(
        "INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        all_signals
    )
    return len(all_signals)


def seed_kg_edges(conn):
    """Build knowledge graph edges from signal data."""
    edges = []

    # signal -> detected_by -> station
    for row in conn.execute("SELECT signal_id, station_id FROM signals WHERE station_id IS NOT NULL"):
        edges.append(("signal", row[0], "station", row[1], "detected_by", 1.0, None))

    # signal -> transmits_on -> band
    for row in conn.execute("SELECT signal_id, band_id FROM signals WHERE band_id IS NOT NULL"):
        edges.append(("signal", row[0], "band", row[1], "transmits_on", 1.0, None))

    # emitter -> located_near -> station (within 3000km)
    emitters = conn.execute("SELECT emitter_id, lat, lon FROM emitters").fetchall()
    stations = conn.execute("SELECT station_id, lat, lon FROM stations").fetchall()
    for em in emitters:
        for st in stations:
            dist = math.hypot(em[1] - st[1], em[2] - st[2]) * 111
            if dist < 3000:
                edges.append(("emitter", em[0], "station", st[0], "located_near", max(0.1, 1 - dist / 3000), None))

    # Co-occurring emitters (same time window, same station)
    # Simplified: emitters on same band = correlated
    emitter_bands = {}
    for row in conn.execute(
        "SELECT DISTINCT json_extract(metadata, '$.emitter_id') as eid, band_id "
        "FROM signals WHERE band_id IS NOT NULL AND eid IS NOT NULL"
    ):
        emitter_bands.setdefault(row[1], set()).add(row[0])

    for band_id, eids in emitter_bands.items():
        eid_list = list(eids)
        for i in range(len(eid_list)):
            for j in range(i + 1, min(i + 5, len(eid_list))):
                edges.append(("emitter", eid_list[i], "emitter", eid_list[j], "co_occurs", 0.6, json.dumps({"band": band_id})))

    conn.executemany(
        "INSERT INTO kg_edges (source_type, source_id, target_type, target_id, relation, weight, metadata) "
        "VALUES (?,?,?,?,?,?,?)",
        edges
    )
    return len(edges)


def seed_patterns(conn):
    """Detect and store signal patterns."""
    patterns = []

    # 1. Periodic emitters: emitters with >50 signals
    for row in conn.execute("SELECT emitter_id, name, signal_count, freq_hz FROM emitters WHERE signal_count > 50"):
        pid = f"pat_{_uid()}"
        patterns.append((
            pid, "periodic", f"Regular transmissions from {row[1]} on {row[3]/1e6:.3f} MHz",
            random.uniform(0.7, 0.95), json.dumps([row[0]]), None,
            json.dumps({"avg_interval_hours": random.uniform(1, 8), "freq_mhz": row[3] / 1e6})
        ))

    # 2. Spatial clusters: group emitters by region
    emitters = conn.execute("SELECT emitter_id, lat, lon, emitter_type FROM emitters").fetchall()
    clusters = {}
    for em in emitters:
        key = (round(em[1] / 5) * 5, round(em[2] / 10) * 10)
        clusters.setdefault(key, []).append(em)

    for (clat, clon), members in clusters.items():
        if len(members) >= 3:
            pid = f"pat_{_uid()}"
            eids = [m[0] for m in members]
            patterns.append((
                pid, "spatial_cluster",
                f"Cluster of {len(members)} emitters near {clat:.0f}N {clon:.0f}E",
                random.uniform(0.6, 0.9), json.dumps(eids), None,
                json.dumps({"center_lat": clat, "center_lon": clon, "count": len(members)})
            ))

    # 3. Frequency hopping: pick some emitters and mark as hoppers
    hoppers = random.sample(list(range(len(emitters))), min(5, len(emitters)))
    for idx in hoppers:
        em = emitters[idx]
        pid = f"pat_{_uid()}"
        patterns.append((
            pid, "frequency_hopping",
            f"Frequency agile emitter near {em[1]:.1f}N {em[2]:.1f}E",
            random.uniform(0.5, 0.85), json.dumps([em[0]]), None,
            json.dumps({"hop_rate_hz": random.choice([10, 50, 100, 500]), "channels": random.randint(5, 20)})
        ))

    conn.executemany(
        "INSERT INTO patterns (pattern_id, pattern_type, description, confidence, emitter_ids, signal_ids, params) "
        "VALUES (?,?,?,?,?,?,?)",
        patterns
    )
    return len(patterns)


def seed_anomalies(conn):
    """Generate anomaly detections."""
    anomalies = []
    anomalous = conn.execute(
        "SELECT signal_id, freq_hz, power_dbm, lat, lon, json_extract(metadata, '$.emitter_id') "
        "FROM signals WHERE classification = 'anomalous'"
    ).fetchall()

    types = [
        ("unexpected_freq", "high", "Signal on unexpected frequency"),
        ("power_spike", "medium", "Unusual power level detected"),
        ("new_emitter", "high", "Previously unknown emitter"),
        ("schedule_deviation", "low", "Transmission outside normal schedule"),
    ]

    for sig in anomalous:
        atype, sev, desc = random.choice(types)
        aid = f"anom_{_uid()}"
        anomalies.append((
            aid, atype, sev,
            f"{desc} at {sig[3]:.2f}N {sig[4]:.2f}E on {sig[1]/1e6:.3f} MHz",
            sig[0], sig[5], random.uniform(0.5, 1.0), 0
        ))

    conn.executemany(
        "INSERT INTO anomalies (anomaly_id, anomaly_type, severity, description, signal_id, emitter_id, score, resolved) "
        "VALUES (?,?,?,?,?,?,?,?)",
        anomalies
    )
    return len(anomalies)


def seed_space_weather(conn, days: int = 30):
    """Generate space weather observations."""
    now = datetime.now(timezone.utc)
    obs = []
    for d in range(days):
        dt = now - timedelta(days=days - d)
        k = random.choices([0, 1, 2, 3, 4, 5, 6, 7], weights=[5, 15, 25, 25, 15, 8, 5, 2])[0]
        flux = random.uniform(70, 200)
        ssn = random.randint(20, 180)
        cond = "quiet" if k <= 2 else "unsettled" if k <= 4 else "active" if k <= 6 else "storm"
        obs.append((dt.isoformat(), k, flux, ssn, cond))

    conn.executemany(
        "INSERT INTO space_weather (timestamp, k_index, solar_flux, sunspot_num, conditions) VALUES (?,?,?,?,?)",
        obs
    )
    return len(obs)


def run_seed():
    """Full seed pipeline."""
    result = init_db()
    print(f"DB initialized: {result['count']} tables")

    conn = get_connection()
    try:
        seed_bands(conn)
        print(f"  Bands: {len(BANDS)}")

        seed_stations(conn)
        print(f"  Stations: {len(STATIONS)}")

        emitters = seed_emitters(conn, count=40)
        print(f"  Emitters: {len(emitters)}")

        sig_count = seed_signals(conn, emitters, days=30, signals_per_day=120)
        print(f"  Signals: {sig_count}")

        pat_count = seed_patterns(conn)
        print(f"  Patterns: {pat_count}")

        anom_count = seed_anomalies(conn)
        print(f"  Anomalies: {anom_count}")

        edge_count = seed_kg_edges(conn)
        print(f"  KG edges: {edge_count}")

        wx_count = seed_space_weather(conn)
        print(f"  Space weather: {wx_count}")

        conn.commit()
        print("Seed complete.")
    finally:
        conn.close()


if __name__ == "__main__":
    run_seed()
