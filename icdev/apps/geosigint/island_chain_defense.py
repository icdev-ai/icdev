"""Island chain defense analysis — US/allied base coverage rings, THAAD envelopes, chokepoints.

Data: open-source OSINT (DoD, RAND, CSIS, Jane's publicly available, FAS).
Ranges: unclassified publicly reported figures only.
"""

from __future__ import annotations

import math

# ── First Island Chain — US/Allied Bases ────────────────────────────────────
# Source: DoD Base Structure Report, RAND PACOM basing studies, FAS.org (unclassified).

FIRST_ISLAND_CHAIN_BASES: list[dict] = [
    {
        "id": "KADENA",
        "name": "Kadena AB",
        "location": "Okinawa, Japan",
        "lat": 26.36,
        "lon": 127.77,
        "nation": "USA",
        "primary_assets": "F-15C/D, F-22A, KC-135, E-3G AWACS",
        "combat_radius_km": 1850,
        "role": "Air superiority / SEAD",
        "notes": (
            "Largest USAF base in Asia-Pacific. F-22 rotational deployments. "
            "Within 700 km of Taiwan Strait. Host nation: Japan."
        ),
        "color": "#3b82f6",
        "chain": 1,
    },
    {
        "id": "MISAWA",
        "name": "Misawa AB",
        "location": "Aomori, Japan",
        "lat": 40.70,
        "lon": 141.37,
        "nation": "USA",
        "primary_assets": "F-16C/D, P-8A Poseidon",
        "combat_radius_km": 1600,
        "role": "Tactical fighter / ASW",
        "notes": "Northern Japan; P-8A ASW coverage across Sea of Japan and northern Pacific.",
        "color": "#60a5fa",
        "chain": 1,
    },
    {
        "id": "YOKOTA",
        "name": "Yokota AB",
        "location": "Tokyo, Japan",
        "lat": 35.74,
        "lon": 139.35,
        "nation": "USA",
        "primary_assets": "C-130J, C-12, USAF Pacific HQ",
        "combat_radius_km": None,
        "role": "PACAF HQ / Airlift hub",
        "notes": "PACAF headquarters; mobility hub; not a primary combat base.",
        "color": "#93c5fd",
        "chain": 1,
    },
    {
        "id": "FUTENMA",
        "name": "MCAS Futenma (Relocated to Camp Courtney/Schwab)",
        "location": "Okinawa, Japan",
        "lat": 26.27,
        "lon": 127.75,
        "nation": "USA",
        "primary_assets": "MV-22 Osprey, CH-53E, F/A-18 det.",
        "combat_radius_km": 900,
        "role": "Marine expeditionary / VSTOL",
        "notes": "Being relocated to Camp Schwab under DPRI agreements.",
        "color": "#bfdbfe",
        "chain": 1,
    },
    {
        "id": "CLARK",
        "name": "Clark AB / Fort Magsaysay (NAS Subic Bay)",
        "location": "Luzon, Philippines",
        "lat": 15.18,
        "lon": 120.56,
        "nation": "USA / Philippines",
        "primary_assets": "P-8A (rotational), MQ-9, ISR",
        "combat_radius_km": 1200,
        "role": "ISR / ASW / EDCA site",
        "notes": (
            "EDCA Enhanced Defense Cooperation Agreement site. "
            "P-8A ASW operations in South China Sea and Luzon Strait. "
            "Controls northern Luzon Strait approach."
        ),
        "color": "#22c55e",
        "chain": 1,
    },
    {
        "id": "BASA",
        "name": "Basa Air Base (AFP)",
        "location": "Pampanga, Philippines",
        "lat": 14.99,
        "lon": 120.50,
        "nation": "Philippines",
        "primary_assets": "FA-50, A-29 Super Tucano",
        "combat_radius_km": 850,
        "role": "Philippine Air Force tactical",
        "notes": "AFP primary fighter base on Luzon. EDCA-access partner.",
        "color": "#4ade80",
        "chain": 1,
    },
    {
        "id": "OSAN",
        "name": "Osan AB",
        "location": "Gyeonggi-do, South Korea",
        "lat": 37.09,
        "lon": 127.03,
        "nation": "USA",
        "primary_assets": "A-10C (transitioning), OA-1K Skyraider II, U-2, RC-135",
        "combat_radius_km": 1400,
        "role": "CAS / ISR / 7th AF HQ",
        "notes": "HQ 7th Air Force. Critical ISR hub for Korean Peninsula and Sea of Japan.",
        "color": "#f59e0b",
        "chain": 1,
    },
    {
        "id": "KUNSAN",
        "name": "Kunsan AB",
        "location": "North Jeolla, South Korea",
        "lat": 35.90,
        "lon": 126.62,
        "nation": "USA",
        "primary_assets": "F-16C/D Block 40/50",
        "combat_radius_km": 1600,
        "role": "Air superiority / strike",
        "notes": "Wolf Pack 8th FW. Western Korea; Yellow Sea patrol.",
        "color": "#fbbf24",
        "chain": 1,
    },
]

# ── Second Island Chain — Strategic Depth Bases ──────────────────────────────

SECOND_ISLAND_CHAIN_BASES: list[dict] = [
    {
        "id": "ANDERSEN",
        "name": "Andersen AFB",
        "location": "Guam",
        "lat": 13.58,
        "lon": 144.93,
        "nation": "USA",
        "primary_assets": "B-52H, B-2A, B-21 (future), KC-135, KC-46A, E-8C JSTARS",
        "combat_radius_km": 6400,
        "role": "Strategic bomber / tanker staging",
        "notes": (
            "Primary Pacific strategic bomber hub. "
            "B-52H range from Guam reaches Taiwan Strait (3,300 km). "
            "THAAD battery co-located. Hardened POL and munitions storage."
        ),
        "color": "#ef4444",
        "chain": 2,
    },
    {
        "id": "NAS_GUAM",
        "name": "NAS Agaña (Naval Base Guam)",
        "location": "Guam",
        "lat": 13.44,
        "lon": 144.79,
        "nation": "USA",
        "primary_assets": "SSN/SSGN forward deployed, P-8A, MH-60R",
        "combat_radius_km": None,
        "role": "Submarine / maritime patrol",
        "notes": (
            "Forward deployed Virginia-class and Ohio-class SSGN. "
            "P-8A Poseidon ASW patrols covering Philippine Sea."
        ),
        "color": "#f87171",
        "chain": 2,
    },
    {
        "id": "WAKE",
        "name": "Wake Island Airfield",
        "location": "Wake Island (US Territory)",
        "lat": 19.28,
        "lon": 166.64,
        "nation": "USA",
        "primary_assets": "Emergency recovery / fuel depot",
        "combat_radius_km": None,
        "role": "CONPLAN recovery / logistics node",
        "notes": "Emergency divert strip. Fuel pre-positioned for Pacific CONPLAN contingencies.",
        "color": "#fca5a5",
        "chain": 2,
    },
]

# ── THAAD Batteries ──────────────────────────────────────────────────────────
# Range / altitude figures: publicly reported (Lockheed Martin / MDA unclassified briefs).
# Intercept altitude: 40–150 km. Effective intercept range: ~200 km (terminal phase).

THAAD_BATTERIES: list[dict] = [
    {
        "id": "THAAD_GUAM",
        "name": "THAAD — Guam",
        "lat": 13.52,
        "lon": 144.86,
        "range_km": 200,
        "notes": (
            "Permanent deployment on Guam since 2015. "
            "Defends Andersen AFB and port infrastructure. "
            "Extended via SM-3 sea-based layering from CSG."
        ),
        "color": "#a78bfa",
        "fill_opacity": 0.12,
    },
    {
        "id": "THAAD_KOREA",
        "name": "THAAD — Seongju, South Korea",
        "lat": 35.99,
        "lon": 128.03,
        "range_km": 200,
        "notes": (
            "Deployed 2017 under US Forces Korea. "
            "Covers Seoul and forward-deployed US forces. "
            "DPRK missile defense primary; secondary Taiwan scenario coverage."
        ),
        "color": "#c4b5fd",
        "fill_opacity": 0.12,
    },
]

# ── Maritime Chokepoints ──────────────────────────────────────────────────────
# Strategic narrow straits controlling PLAN fleet egress to Pacific.
# Source: DoD South China Sea Freedom of Navigation reports, CSIS China Power.

CHOKEPOINTS: list[dict] = [
    {
        "id": "BASHI",
        "name": "Bashi Channel",
        "lat": 21.0,
        "lon": 121.5,
        "width_km": 250,
        "depth_m": 2500,
        "significance": "critical",
        "notes": (
            "Separates Taiwan from Luzon (Philippines). Width ~250 km. "
            "Primary PLAN submarine / surface egress to Philippine Sea. "
            "US/Philippine P-8A ASW patrols; Clark AB within 600 km. "
            "Key choke for blockade of Taiwan sea lines."
        ),
        "control_nations": ["USA", "Philippines"],
        "color": "#ef4444",
    },
    {
        "id": "MIYAKO",
        "name": "Miyako Strait",
        "lat": 24.5,
        "lon": 125.5,
        "width_km": 250,
        "depth_m": 1300,
        "significance": "critical",
        "notes": (
            "Between Miyako-jima and Okinawa; width ~250 km. "
            "Primary PLAN surface fleet transit to Pacific. "
            "JMSDF P-1 MPA and US P-8A persistent surveillance. "
            "Regular transits by PLAN carrier groups (OSINT confirmed 2022–2024)."
        ),
        "control_nations": ["Japan", "USA"],
        "color": "#f59e0b",
    },
    {
        "id": "LUZON",
        "name": "Luzon Strait",
        "lat": 19.5,
        "lon": 121.8,
        "width_km": 400,
        "depth_m": 3000,
        "significance": "major",
        "notes": (
            "Broader passage between Luzon and Taiwan. Width ~400 km. "
            "Deep water (3,000 m) favors submarine operations. "
            "PLAN SSN transit corridor to Pacific Basin. "
            "US-Philippines EDCA sites provide land-based sensor coverage."
        ),
        "control_nations": ["USA", "Philippines"],
        "color": "#fbbf24",
    },
    {
        "id": "TSUSHIMA",
        "name": "Tsushima Strait",
        "lat": 34.3,
        "lon": 129.2,
        "width_km": 50,
        "depth_m": 90,
        "significance": "major",
        "notes": (
            "Narrowest Japan Sea–Pacific choke (50 km). "
            "JMSDF Tsushima ASW sensors + shore-based radar; shallow (90 m) limits SSBN transit."
        ),
        "control_nations": ["Japan", "South Korea"],
        "color": "#22c55e",
    },
]

_SIGNIFICANCE_COLOR = {
    "critical": "#ef4444",
    "major": "#f59e0b",
    "minor": "#6b7280",
}


# ── Coverage Ring Calculations ────────────────────────────────────────────────

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two lat/lon points."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def get_coverage_overlap(base_a: dict, base_b: dict) -> float:
    """Distance between two bases in km (for coverage gap analysis)."""
    return haversine_km(base_a["lat"], base_a["lon"], base_b["lat"], base_b["lon"])


def taiwan_in_coverage(base: dict) -> bool:
    """True if Taiwan's geographic center is within this base's combat radius."""
    if base.get("combat_radius_km") is None:
        return False
    tw_lat, tw_lon = 23.97, 120.97  # Taiwan geographic center
    dist = haversine_km(base["lat"], base["lon"], tw_lat, tw_lon)
    return dist <= base["combat_radius_km"]


def get_all_bases() -> list[dict]:
    """Combined list of all bases with Taiwan coverage flag."""
    all_bases = FIRST_ISLAND_CHAIN_BASES + SECOND_ISLAND_CHAIN_BASES
    return [
        {
            **b,
            "taiwan_coverage": taiwan_in_coverage(b),
            "dist_to_taiwan_km": round(
                haversine_km(b["lat"], b["lon"], 23.97, 120.97), 1
            ),
        }
        for b in all_bases
    ]


# ── Summary API ──────────────────────────────────────────────────────────────

def get_summary() -> dict:
    all_bases = get_all_bases()
    covering = [b for b in all_bases if b["taiwan_coverage"]]
    critical_chokes = [c for c in CHOKEPOINTS if c["significance"] == "critical"]
    return {
        "first_island_chain_bases": len(FIRST_ISLAND_CHAIN_BASES),
        "second_island_chain_bases": len(SECOND_ISLAND_CHAIN_BASES),
        "thaad_batteries": len(THAAD_BATTERIES),
        "chokepoints_total": len(CHOKEPOINTS),
        "critical_chokepoints": len(critical_chokes),
        "bases_covering_taiwan": len(covering),
        "closest_base_name": min(all_bases, key=lambda b: b["dist_to_taiwan_km"])["name"],
        "closest_base_dist_km": min(all_bases, key=lambda b: b["dist_to_taiwan_km"])["dist_to_taiwan_km"],
    }
