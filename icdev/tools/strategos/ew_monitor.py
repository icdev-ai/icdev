#!/usr/bin/env python3
# CUI // SP-CTI
"""EW (Electronic Warfare) Monitor — jamming sites, EMCON scoring, supply chain overlay.

EW_SITES: GPS/radar jamming platforms with lat/lon, range_km, and jamming_active status.
SUPPLY_NODES: Semiconductor supply chain critical nodes (fabs, materials, equipment).
CHOKEPOINTS: Maritime chokepoints relevant to semiconductor logistics.

EMCON score = dark_ais_rate × jamming_active  (0.0 – 1.0)
"""

from __future__ import annotations

import math
from typing import Any

# ---------------------------------------------------------------------------
# EW jamming platform data
# ---------------------------------------------------------------------------

EW_SITES: list[dict[str, Any]] = [
    # R-330Zh Zhitel — GPS/comms jammer, range 450 km
    {
        "site_id": "ew-r330-kal",
        "system": "R-330Zh Zhitel",
        "system_short": "R-330Zh",
        "range_km": 450,
        "lat": 54.71,
        "lon": 20.51,
        "location": "Kaliningrad Oblast",
        "country": "RU",
        "jamming_active": 1,
        "dark_ais_rate": 0.41,
    },
    {
        "site_id": "ew-r330-cri",
        "system": "R-330Zh Zhitel",
        "system_short": "R-330Zh",
        "range_km": 450,
        "lat": 44.60,
        "lon": 33.52,
        "location": "Crimea (Sevastopol)",
        "country": "RU",
        "jamming_active": 1,
        "dark_ais_rate": 0.63,
    },
    {
        "site_id": "ew-r330-zap",
        "system": "R-330Zh Zhitel",
        "system_short": "R-330Zh",
        "range_km": 450,
        "lat": 47.82,
        "lon": 35.17,
        "location": "Zaporizhzhia Front",
        "country": "RU",
        "jamming_active": 1,
        "dark_ais_rate": 0.77,
    },
    {
        "site_id": "ew-r330-lat",
        "system": "R-330Zh Zhitel",
        "system_short": "R-330Zh",
        "range_km": 450,
        "lat": 35.52,
        "lon": 35.79,
        "location": "Latakia, Syria",
        "country": "RU",
        "jamming_active": 0,
        "dark_ais_rate": 0.18,
    },
    # Krasukha-4 — broadband multi-purpose EW complex, range 300 km
    {
        "site_id": "ew-kr4-psk",
        "system": "Krasukha-4",
        "system_short": "Krasukha-4",
        "range_km": 300,
        "lat": 57.82,
        "lon": 28.34,
        "location": "Pskov Oblast",
        "country": "RU",
        "jamming_active": 1,
        "dark_ais_rate": 0.09,
    },
    {
        "site_id": "ew-kr4-bel",
        "system": "Krasukha-4",
        "system_short": "Krasukha-4",
        "range_km": 300,
        "lat": 53.90,
        "lon": 27.57,
        "location": "Minsk Region, Belarus",
        "country": "BY",
        "jamming_active": 0,
        "dark_ais_rate": 0.05,
    },
    {
        "site_id": "ew-kr4-don",
        "system": "Krasukha-4",
        "system_short": "Krasukha-4",
        "range_km": 300,
        "lat": 48.02,
        "lon": 37.80,
        "location": "Donetsk Front",
        "country": "RU",
        "jamming_active": 1,
        "dark_ais_rate": 0.71,
    },
    {
        "site_id": "ew-kr4-mur",
        "system": "Krasukha-4",
        "system_short": "Krasukha-4",
        "range_km": 300,
        "lat": 68.97,
        "lon": 33.07,
        "location": "Murmansk Oblast",
        "country": "RU",
        "jamming_active": 1,
        "dark_ais_rate": 0.22,
    },
]

# ---------------------------------------------------------------------------
# Semiconductor supply chain nodes
# ---------------------------------------------------------------------------

SUPPLY_NODES: list[dict[str, Any]] = [
    # Fabs
    {
        "node_id": "sc-tsmc-tw",
        "name": "TSMC",
        "category": "fab",
        "detail": "World's largest contract fab — 3nm/5nm",
        "lat": 24.79,
        "lon": 120.99,
        "location": "Hsinchu, Taiwan",
        "criticality": 0.98,
    },
    {
        "node_id": "sc-samsung-kr",
        "name": "Samsung Foundry",
        "category": "fab",
        "detail": "2nd largest — 3nm GAA",
        "lat": 37.27,
        "lon": 127.01,
        "location": "Hwaseong, South Korea",
        "criticality": 0.82,
    },
    {
        "node_id": "sc-skhynix-kr",
        "name": "SK Hynix",
        "category": "fab",
        "detail": "DRAM / HBM memory — HBM3E for AI GPUs",
        "lat": 37.42,
        "lon": 127.15,
        "location": "Icheon, South Korea",
        "criticality": 0.79,
    },
    {
        "node_id": "sc-intel-or",
        "name": "Intel Fab 52/62",
        "category": "fab",
        "detail": "Intel 18A — leading-edge US fab",
        "lat": 45.53,
        "lon": -122.91,
        "location": "Hillsboro, Oregon, USA",
        "criticality": 0.65,
    },
    # Lithography / equipment
    {
        "node_id": "sc-asml-nl",
        "name": "ASML",
        "category": "equipment",
        "detail": "Sole supplier of EUV lithography machines",
        "lat": 51.41,
        "lon": 5.47,
        "location": "Veldhoven, Netherlands",
        "criticality": 0.97,
    },
    {
        "node_id": "sc-applmat-us",
        "name": "Applied Materials",
        "category": "equipment",
        "detail": "Deposition/etch equipment",
        "lat": 37.39,
        "lon": -121.98,
        "location": "Santa Clara, CA, USA",
        "criticality": 0.71,
    },
    # Rare earth / materials
    {
        "node_id": "sc-re-baotou",
        "name": "Baotou Rare Earth",
        "category": "rare_earth",
        "detail": "~60% global rare earth processing",
        "lat": 40.66,
        "lon": 109.84,
        "location": "Baotou, Inner Mongolia, China",
        "criticality": 0.88,
    },
    {
        "node_id": "sc-re-jiangxi",
        "name": "Jiangxi REE Cluster",
        "category": "rare_earth",
        "detail": "Ion-adsorption clay mines — heavy REE",
        "lat": 28.68,
        "lon": 115.86,
        "location": "Ganzhou, Jiangxi, China",
        "criticality": 0.76,
    },
    {
        "node_id": "sc-neon-ua",
        "name": "Cryoin / Ingas (Neon)",
        "category": "rare_earth",
        "detail": "~50% global neon supply pre-2022 (disrupted)",
        "lat": 46.48,
        "lon": 30.73,
        "location": "Odesa, Ukraine",
        "criticality": 0.55,
    },
]

# ---------------------------------------------------------------------------
# Maritime chokepoints
# ---------------------------------------------------------------------------

CHOKEPOINTS: list[dict[str, Any]] = [
    {
        "cp_id": "cp-malacca",
        "name": "Strait of Malacca",
        "detail": "~25% global trade; critical for TSMC/Samsung shipments",
        "lat": 1.34,
        "lon": 103.83,
        "transit_pct": 25,
    },
    {
        "cp_id": "cp-bosphorus",
        "name": "Bosphorus / Turkish Straits",
        "detail": "Black Sea egress; blocked to Russian warships since 2022",
        "lat": 41.08,
        "lon": 29.05,
        "transit_pct": 3,
    },
    {
        "cp_id": "cp-hormuz",
        "name": "Strait of Hormuz",
        "detail": "~20% global oil; Iranian interdiction risk",
        "lat": 26.56,
        "lon": 56.25,
        "transit_pct": 20,
    },
    {
        "cp_id": "cp-suez",
        "name": "Suez Canal",
        "detail": "~12% global trade; Houthi disruption ongoing",
        "lat": 30.50,
        "lon": 32.35,
        "transit_pct": 12,
    },
    {
        "cp_id": "cp-taiwan",
        "name": "Taiwan Strait",
        "detail": "Critical to TSMC logistics; ~48% global advanced chip production",
        "lat": 24.50,
        "lon": 119.50,
        "transit_pct": 8,
    },
]


# ---------------------------------------------------------------------------
# EMCON scoring
# ---------------------------------------------------------------------------

def compute_emcon(dark_ais_rate: float, jamming_active: int) -> float:
    """EMCON score = dark_ais_rate × jamming_active, clamped to [0.0, 1.0]."""
    return round(min(1.0, max(0.0, dark_ais_rate * jamming_active)), 4)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _jamming_exposure(node: dict[str, Any]) -> float:
    """Return max EMCON score from any active jammer within range of a node."""
    best = 0.0
    for site in EW_SITES:
        if not site["jamming_active"]:
            continue
        dist = _haversine_km(node["lat"], node["lon"], site["lat"], site["lon"])
        if dist <= site["range_km"]:
            score = compute_emcon(site["dark_ais_rate"], site["jamming_active"])
            best = max(best, score)
    return best


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_ew_data() -> dict[str, Any]:
    """Return full EW situational picture: sites, supply nodes, chokepoints."""
    sites_out = []
    for s in EW_SITES:
        row = dict(s)
        row["emcon_score"] = compute_emcon(s["dark_ais_rate"], s["jamming_active"])
        sites_out.append(row)

    supply_out = []
    for n in SUPPLY_NODES:
        row = dict(n)
        row["jamming_exposure"] = _jamming_exposure(n)
        supply_out.append(row)

    active_count = sum(1 for s in EW_SITES if s["jamming_active"])
    mean_emcon = (
        sum(compute_emcon(s["dark_ais_rate"], s["jamming_active"]) for s in EW_SITES) / len(EW_SITES)
        if EW_SITES else 0.0
    )

    return {
        "ew_sites": sites_out,
        "supply_nodes": supply_out,
        "chokepoints": CHOKEPOINTS,
        "summary": {
            "total_sites": len(EW_SITES),
            "active_jammers": active_count,
            "mean_emcon_score": round(mean_emcon, 4),
            "supply_nodes_at_risk": sum(1 for n in supply_out if n["jamming_exposure"] > 0),
        },
    }
