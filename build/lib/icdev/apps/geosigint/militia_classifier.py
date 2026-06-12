#!/usr/bin/env python3
# CUI // SP-CTI
"""Maritime militia (Little Blue Men) classifier — fishing-pattern anomaly + formation + dark-AIS signature.

Extends sg-ghost-05 ghost vessel detection with three-layer detection logic:
  Layer 1 — Fishing-pattern anomaly in contested/disputed zones (SCS, ECS, Senkaku vicinity).
  Layer 2 — Formation behavior: cluster spacing / bearing coherence inconsistent with fishing fleets.
  Layer 3 — AIS darkness events near PLAN-built artificial islands (Fiery Cross, Mischief, Subi, etc.).

Data: OSINT (CSIS China Sea Initiative, AMTI, ACLED, US DoD Pacific reports, open AIS archives).
Methodology: threshold-based scoring (deterministic). No ML inference.
"""

from __future__ import annotations

import math

# ── Disputed Zone Polygons ───────────────────────────────────────────────────
# Bounding boxes (lat_min, lat_max, lon_min, lon_max) for zones where
# fishing-pattern presence elevates militia suspicion.
# Source: UNCLOS EEZ data, AMTI artificial-island reports, US DoD FON reports.

DISPUTED_ZONES: list[dict] = [
    {
        "id": "SCS-SPRATLY",
        "name": "Spratly Islands (SCS)",
        "lat_min": 7.80,
        "lat_max": 12.20,
        "lon_min": 113.00,
        "lon_max": 117.80,
        "sensitivity": 0.95,
        "notes": "Core SCS militia operating theater; PLAN artificial-island facilities present.",
        "color": "#ef4444",
    },
    {
        "id": "SCS-PARACEL",
        "name": "Paracel Islands (SCS)",
        "lat_min": 15.70,
        "lat_max": 17.20,
        "lon_min": 110.80,
        "lon_max": 112.90,
        "sensitivity": 0.90,
        "notes": "China-controlled since 1974; Vietnam / China contested. Militia staging area.",
        "color": "#ef4444",
    },
    {
        "id": "ECS-SENKAKU",
        "name": "Senkaku / Diaoyu Islands (ECS)",
        "lat_min": 25.40,
        "lat_max": 26.20,
        "lon_min": 123.30,
        "lon_max": 124.80,
        "sensitivity": 0.85,
        "notes": "Japan-administered; China / Taiwan claim. CCG + militia incursion cadence high.",
        "color": "#f59e0b",
    },
    {
        "id": "SCS-SCARBOROUGH",
        "name": "Scarborough Shoal (SCS)",
        "lat_min": 15.00,
        "lat_max": 15.40,
        "lon_min": 117.40,
        "lon_max": 117.90,
        "sensitivity": 0.92,
        "notes": "Philippines EEZ; China de-facto control since 2012. Persistent militia presence.",
        "color": "#ef4444",
    },
    {
        "id": "SCS-SECOND-THOMAS",
        "name": "Second Thomas Shoal / Ayungin",
        "lat_min": 9.70,
        "lat_max": 10.00,
        "lon_min": 115.80,
        "lon_max": 116.10,
        "sensitivity": 0.93,
        "notes": "BRP Sierra Madre grounding; active Philippine resupply standoff with CCG/militia.",
        "color": "#ef4444",
    },
    {
        "id": "SCS-GULF-TONKIN",
        "name": "Gulf of Tonkin (SCS)",
        "lat_min": 17.00,
        "lat_max": 22.00,
        "lon_min": 106.00,
        "lon_max": 110.50,
        "sensitivity": 0.65,
        "notes": "Vietnam EEZ. Periodic China militia harassment of Vietnamese fishing fleets.",
        "color": "#fbbf24",
    },
]

# ── PLAN Artificial Islands ───────────────────────────────────────────────────
# Lat/lon of runway-capable or fortified artificial islands; AIS darkness events
# within DARK_ISLAND_RADIUS_KM of these sites elevate Layer 3 score.
# Source: CSIS AMTI, US DoD "Militarization of the South China Sea" reports.

ARTIFICIAL_ISLANDS: list[dict] = [
    {
        "id": "FIERY-CROSS",
        "name": "Fiery Cross Reef",
        "lat": 9.55,
        "lon": 112.89,
        "facilities": "3,000m runway, HN-12 hangar, radar arrays",
        "threat_tier": "high",
        "color": "#ef4444",
    },
    {
        "id": "SUBI",
        "name": "Subi Reef",
        "lat": 10.92,
        "lon": 114.08,
        "facilities": "3,000m runway, naval basin, VHF/radar",
        "threat_tier": "high",
        "color": "#ef4444",
    },
    {
        "id": "MISCHIEF",
        "name": "Mischief Reef",
        "lat": 9.90,
        "lon": 115.53,
        "facilities": "3,000m runway, large harbor, coastal defense batteries",
        "threat_tier": "high",
        "color": "#ef4444",
    },
    {
        "id": "CUARTERON",
        "name": "Cuarteron Reef",
        "lat": 8.86,
        "lon": 112.84,
        "facilities": "Sensors, lighthouse, UAV pad (assessed)",
        "threat_tier": "medium",
        "color": "#f59e0b",
    },
    {
        "id": "GAVIN",
        "name": "Gaven / Nansha Reef",
        "lat": 10.21,
        "lon": 114.22,
        "facilities": "Helipad, gun emplacements, radar",
        "threat_tier": "medium",
        "color": "#f59e0b",
    },
    {
        "id": "WOODY",
        "name": "Woody Island (Paracels)",
        "lat": 16.84,
        "lon": 112.34,
        "facilities": "Air base, YJ-12B ASCM, HQ-9 SAM",
        "threat_tier": "high",
        "color": "#ef4444",
    },
]

DARK_ISLAND_RADIUS_KM = 50.0   # AIS-dark event within this radius → Layer 3 trigger

# ── Fishing-Pattern Anomaly Thresholds ───────────────────────────────────────
# A legitimate fishing vessel exhibits: low speed (2–5 kt), irregular course,
# high loiter time, single/dispersed spacing. Militia deviation:
#   • Sustained 7–12 kt transit into zone (not fishing on approach)
#   • Minimal gear-deployment indicators (SOG never drops below 1.5 kt)
#   • Overnight loitering with AIS on but no position change > 0.5 nm / 4h

FISHING_SPEED_MIN_KTS = 1.5     # below this: probable gear deployment
FISHING_SPEED_MAX_KTS = 5.5     # above this inside disputed zone: anomalous
APPROACH_SPEED_KTS = 7.0        # transit speed to zone — not fishing behavior
LOITER_DISPLACEMENT_NM = 0.5   # max drift in 4h for true loitering (anchor/gear)

# ── Formation Behavior Signatures ────────────────────────────────────────────
# Militia swarms observed at Whitsun (March 2021): 220 vessels in 3-6 nm clusters,
# bearing coherence within ±15°, inter-vessel spacing 0.2–0.8 nm.
# Source: AMTI overhead imagery analysis; Philippine Coast Guard advisories.

FORMATION_MAX_SPACING_NM = 0.8   # nm; vessels tighter than this within swarm
FORMATION_BEARING_TOLERANCE_DEG = 15.0   # degrees; heading coherence threshold
FORMATION_MIN_VESSELS = 5        # minimum cluster size to trigger swarm signature


# ── Haversine helper ──────────────────────────────────────────────────────────

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _nm_to_km(nm: float) -> float:
    return nm * 1.852


# ── Layer 1: Disputed Zone + Fishing Anomaly ─────────────────────────────────

def _zone_for_position(lat: float, lon: float) -> dict | None:
    """Return the highest-sensitivity disputed zone containing (lat, lon), or None."""
    best: dict | None = None
    for z in DISPUTED_ZONES:
        if z["lat_min"] <= lat <= z["lat_max"] and z["lon_min"] <= lon <= z["lon_max"]:
            if best is None or z["sensitivity"] > best["sensitivity"]:
                best = z
    return best


def score_fishing_anomaly(
    lat: float,
    lon: float,
    sog_kts: float,
    cog_deg: float,
    ais_on: bool = True,
    gear_deployed: bool = False,
) -> dict:
    """Layer 1 score — fishing-pattern anomaly inside a disputed zone.

    Returns score [0.0, 1.0] where 1.0 = maximum anomaly confidence.
    """
    zone = _zone_for_position(lat, lon)
    if zone is None:
        return {"score": 0.0, "zone": None, "reason": "outside_disputed_zone"}

    base = zone["sensitivity"]
    penalty = 0.0

    if sog_kts > APPROACH_SPEED_KTS:
        penalty += 0.30   # high speed inside zone
    elif sog_kts > FISHING_SPEED_MAX_KTS:
        penalty += 0.20   # above fishing speed band
    elif sog_kts < FISHING_SPEED_MIN_KTS and not gear_deployed:
        penalty += 0.15   # dead stop without gear: loiter signature

    if not gear_deployed and sog_kts < FISHING_SPEED_MIN_KTS:
        penalty += 0.10   # loitering without fishing activity

    if not ais_on:
        penalty += 0.25   # AIS dark inside zone is major flag

    score = round(min(1.0, base * (0.5 + penalty)), 4)
    return {
        "score": score,
        "zone_id": zone["id"],
        "zone_name": zone["name"],
        "zone_sensitivity": zone["sensitivity"],
        "sog_kts": sog_kts,
        "ais_on": ais_on,
        "gear_deployed": gear_deployed,
        "reason": "fishing_anomaly_in_disputed_zone",
    }


# ── Layer 2: Formation Behavior ───────────────────────────────────────────────

def score_formation(vessels: list[dict]) -> dict:
    """Layer 2 score — formation behavior for a group of vessels.

    Each vessel dict: {id, lat, lon, sog_kts, cog_deg}.
    Returns aggregate formation score [0.0, 1.0] and per-vessel analysis.
    """
    n = len(vessels)
    if n < FORMATION_MIN_VESSELS:
        return {
            "score": 0.0,
            "vessel_count": n,
            "reason": f"cluster_too_small (need >= {FORMATION_MIN_VESSELS})",
            "pairs_within_threshold": 0,
        }

    # Bearing coherence — std-dev of headings
    headings = [v["cog_deg"] % 360 for v in vessels]
    mean_hdg = sum(headings) / n
    bearing_var = math.sqrt(sum((h - mean_hdg) ** 2 for h in headings) / n)
    bearing_coherent = bearing_var <= FORMATION_BEARING_TOLERANCE_DEG

    # Spacing — count pairs within FORMATION_MAX_SPACING_NM
    close_pairs = 0
    total_pairs = 0
    for i in range(n):
        for j in range(i + 1, n):
            dist_km = _haversine_km(
                vessels[i]["lat"], vessels[i]["lon"],
                vessels[j]["lat"], vessels[j]["lon"],
            )
            dist_nm = dist_km / 1.852
            total_pairs += 1
            if dist_nm <= FORMATION_MAX_SPACING_NM:
                close_pairs += 1

    spacing_ratio = close_pairs / max(total_pairs, 1)

    # Score: coherence 40% + spacing 60%
    coherence_component = 0.40 if bearing_coherent else 0.0
    spacing_component = 0.60 * spacing_ratio
    score = round(min(1.0, coherence_component + spacing_component), 4)

    return {
        "score": score,
        "vessel_count": n,
        "bearing_variance_deg": round(bearing_var, 2),
        "bearing_coherent": bearing_coherent,
        "close_pairs": close_pairs,
        "total_pairs": total_pairs,
        "spacing_ratio": round(spacing_ratio, 4),
        "reason": "formation_behavior",
    }


# ── Layer 3: AIS Dark Near Artificial Island ──────────────────────────────────

def score_dark_island(lat: float, lon: float) -> dict:
    """Layer 3 score — AIS darkness event proximity to PLAN artificial island.

    Returns score [0.0, 1.0] and nearest island metadata.
    """
    nearest: dict | None = None
    min_dist = float("inf")
    for island in ARTIFICIAL_ISLANDS:
        d = _haversine_km(lat, lon, island["lat"], island["lon"])
        if d < min_dist:
            min_dist = d
            nearest = island

    if nearest is None or min_dist > DARK_ISLAND_RADIUS_KM:
        return {
            "score": 0.0,
            "nearest_island": nearest["name"] if nearest else None,
            "dist_km": round(min_dist, 1) if nearest else None,
            "reason": "outside_dark_island_radius",
        }

    # Score falls off linearly with distance inside the radius
    proximity_factor = 1.0 - (min_dist / DARK_ISLAND_RADIUS_KM)
    tier_boost = 0.20 if nearest["threat_tier"] == "high" else 0.10
    score = round(min(1.0, proximity_factor + tier_boost), 4)

    return {
        "score": score,
        "nearest_island": nearest["name"],
        "island_id": nearest["id"],
        "dist_km": round(min_dist, 1),
        "threat_tier": nearest["threat_tier"],
        "reason": "ais_dark_near_artificial_island",
    }


# ── Composite Classifier ──────────────────────────────────────────────────────
# Weights: Layer 1 (fishing anomaly) 35% + Layer 2 (formation) 35% + Layer 3 (dark/island) 30%
# Thresholds: CONFIRMED ≥ 0.75, SUSPECTED 0.45–0.74, UNLIKELY < 0.45

_L1_W = 0.35
_L2_W = 0.35
_L3_W = 0.30

_CONFIRMED_THRESHOLD = 0.75
_SUSPECTED_THRESHOLD = 0.45

_CLASSIFICATION_COLORS = {
    "CONFIRMED": "#ef4444",
    "SUSPECTED": "#f59e0b",
    "UNLIKELY": "#22c55e",
}


def classify_vessel(
    lat: float,
    lon: float,
    sog_kts: float,
    cog_deg: float,
    ais_on: bool,
    gear_deployed: bool,
    formation_vessels: list[dict] | None = None,
    dark_near_island: bool = False,
) -> dict:
    """Full three-layer militia classification for a single vessel.

    formation_vessels: list of sibling vessel dicts {id, lat, lon, sog_kts, cog_deg}
    including the vessel being classified. Pass None if formation data unavailable.
    dark_near_island: True if vessel had AIS gap within DARK_ISLAND_RADIUS_KM of any island.
    """
    l1 = score_fishing_anomaly(lat, lon, sog_kts, cog_deg, ais_on, gear_deployed)

    if formation_vessels and len(formation_vessels) >= FORMATION_MIN_VESSELS:
        l2 = score_formation(formation_vessels)
    else:
        l2 = {"score": 0.0, "reason": "no_formation_data"}

    if dark_near_island:
        l3 = score_dark_island(lat, lon)
    else:
        l3 = {"score": 0.0, "reason": "ais_not_dark"}

    composite = round(
        _L1_W * l1["score"] + _L2_W * l2["score"] + _L3_W * l3["score"], 4
    )

    if composite >= _CONFIRMED_THRESHOLD:
        classification = "CONFIRMED"
    elif composite >= _SUSPECTED_THRESHOLD:
        classification = "SUSPECTED"
    else:
        classification = "UNLIKELY"

    return {
        "composite_score": composite,
        "classification": classification,
        "color": _CLASSIFICATION_COLORS[classification],
        "layer_1": l1,
        "layer_2": l2,
        "layer_3": l3,
        "weights": {"l1": _L1_W, "l2": _L2_W, "l3": _L3_W},
    }


def classify_fleet(vessels: list[dict]) -> list[dict]:
    """Classify an entire vessel list for militia signature.

    Each vessel dict must contain:
      id, lat, lon, sog_kts, cog_deg, ais_on, gear_deployed, dark_near_island (bool).
    Formation context is derived from the full list.
    """
    results = []
    for v in vessels:
        result = classify_vessel(
            lat=v["lat"],
            lon=v["lon"],
            sog_kts=v["sog_kts"],
            cog_deg=v.get("cog_deg", 0.0),
            ais_on=v.get("ais_on", True),
            gear_deployed=v.get("gear_deployed", False),
            formation_vessels=vessels,
            dark_near_island=v.get("dark_near_island", False),
        )
        results.append({"vessel_id": v.get("vessel_id") or v.get("id", "unknown"), **result})
    return results


# ── Whitsun-Style Swarm Detector ─────────────────────────────────────────────

def detect_swarm_events(vessels: list[dict], zone_id: str | None = None) -> list[dict]:
    """Identify DBSCAN-like clusters of vessels with high militia scores.

    Simple grid-cell clustering (0.1° × 0.1° cells ≈ 8–11 km).
    Returns list of swarm events with centroid, vessel count, and mean score.
    """
    classifications = classify_fleet(vessels)

    # Filter to CONFIRMED + SUSPECTED
    flagged = [
        (v, c) for v, c in zip(vessels, classifications)
        if c["classification"] in ("CONFIRMED", "SUSPECTED")
    ]
    if not flagged:
        return []

    # Assign to 0.1° grid cells
    cells: dict[tuple, list] = {}
    for v, c in flagged:
        cell = (round(v["lat"] * 10) / 10, round(v["lon"] * 10) / 10)
        cells.setdefault(cell, []).append((v, c))

    swarms = []
    for (cell_lat, cell_lon), members in cells.items():
        if len(members) < FORMATION_MIN_VESSELS:
            continue
        mean_score = sum(c["composite_score"] for _, c in members) / len(members)
        confirmed = sum(1 for _, c in members if c["classification"] == "CONFIRMED")
        swarms.append({
            "centroid_lat": cell_lat,
            "centroid_lon": cell_lon,
            "vessel_count": len(members),
            "confirmed_count": confirmed,
            "mean_composite_score": round(mean_score, 4),
            "zone_filter": zone_id,
            "color": "#ef4444" if mean_score >= _CONFIRMED_THRESHOLD else "#f59e0b",
        })

    swarms.sort(key=lambda s: s["mean_composite_score"], reverse=True)
    return swarms


# ── Summary API ───────────────────────────────────────────────────────────────

def get_summary(vessels: list[dict] | None = None) -> dict:
    """Dashboard summary: zone count, island count, and optional fleet-level stats."""
    base = {
        "disputed_zones": len(DISPUTED_ZONES),
        "artificial_islands": len(ARTIFICIAL_ISLANDS),
        "dark_island_radius_km": DARK_ISLAND_RADIUS_KM,
        "formation_min_vessels": FORMATION_MIN_VESSELS,
        "confirmed_threshold": _CONFIRMED_THRESHOLD,
        "suspected_threshold": _SUSPECTED_THRESHOLD,
    }
    if vessels:
        results = classify_fleet(vessels)
        confirmed = sum(1 for r in results if r["classification"] == "CONFIRMED")
        suspected = sum(1 for r in results if r["classification"] == "SUSPECTED")
        base.update({
            "fleet_size": len(vessels),
            "confirmed": confirmed,
            "suspected": suspected,
            "unlikely": len(vessels) - confirmed - suspected,
            "swarm_events": len(detect_swarm_events(vessels)),
        })
    return base
