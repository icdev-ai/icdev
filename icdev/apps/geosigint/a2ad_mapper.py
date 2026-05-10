"""A2/AD zone mapper — weapon system deployment sites and range rings.

Data: open-source OSINT (CSIS, RAND, NTI, Jane's publicly available).
Ranges: unclassified publicly reported figures only.
"""

from __future__ import annotations

# Weapon system definitions — range in km, display properties
WEAPON_SYSTEMS: dict[str, dict] = {
    "DF-21D": {
        "id": "DF-21D",
        "name": "DF-21D MRBM",
        "category": "ASBM",
        "description": (
            "Anti-ship ballistic missile — carrier-killer. "
            "Maneuvering reentry vehicle (MaRV) with over-the-horizon targeting "
            "via OTHT radar and satellite cueing."
        ),
        "range_km": 1500,
        "color": "#E63946",
        "fill_opacity": 0.07,
        "weight": 2,
        "dash_array": None,
        "notes": (
            "Assessed range ~1,450–1,550 km. Terminally guided against "
            "carrier strike groups. Road-mobile; 2nd Artillery / PLARF brigades."
        ),
    },
    "DF-26": {
        "id": "DF-26",
        "name": "DF-26 IRBM",
        "category": "ASBM/LACM",
        "description": (
            "Intermediate-range 'Guam-killer'. "
            "Dual conventional/nuclear capable. "
            "Precision land-attack and anti-ship variants."
        ),
        "range_km": 4000,
        "color": "#9B2226",
        "fill_opacity": 0.04,
        "weight": 2,
        "dash_array": "8 4",
        "notes": (
            "4,000 km range places Guam firmly inside engagement envelope. "
            "Road-mobile TEL; rapid reload. Also designated as 'Guam Express'."
        ),
    },
    "YJ-18": {
        "id": "YJ-18",
        "name": "YJ-18 ASCM",
        "category": "ASCM",
        "description": (
            "Anti-ship cruise missile — ship and submarine launched. "
            "Sub-sonic cruise phase, supersonic (~Mach 3) terminal dash."
        ),
        "range_km": 540,
        "color": "#F4A261",
        "fill_opacity": 0.09,
        "weight": 2,
        "dash_array": "4 3",
        "notes": (
            "Carried by Type 055/052D destroyers and Type 039A submarines. "
            "540 km range exceeds standard USN SAM engagement envelope."
        ),
    },
    "S-400": {
        "id": "S-400",
        "name": "S-400 Triumf SAM",
        "category": "SAM",
        "description": (
            "Long-range surface-to-air missile system. "
            "40N6 missile provides ~400 km engagement range. "
            "Engages aircraft, cruise missiles, and ballistic missiles."
        ),
        "range_km": 400,
        "color": "#457B9D",
        "fill_opacity": 0.09,
        "weight": 1.5,
        "dash_array": "3 3",
        "notes": (
            "PRC operates ~6 regiment-equivalents purchased from Russia. "
            "Denies airspace to 4th/5th-gen aircraft at contested distances."
        ),
    },
}

# Deployment sites — open-source OSINT only (unclassified)
_SITES: list[dict] = [
    # ── DF-21D (PLARF — 2nd Artillery Brigade locations) ──────────────────
    {"system": "DF-21D", "name": "Tonghua Brigade (96th Brigade)", "lat": 41.72, "lon": 125.93},
    {"system": "DF-21D", "name": "Jianshui Brigade (815th Brigade)", "lat": 23.62, "lon": 102.83},
    {"system": "DF-21D", "name": "Guangshui Brigade (804th Brigade)", "lat": 31.62, "lon": 114.02},
    {"system": "DF-21D", "name": "Qingyuan Brigade (820th Brigade)", "lat": 23.70, "lon": 112.48},
    {"system": "DF-21D", "name": "Dalian Forward Base", "lat": 38.92, "lon": 121.60},

    # ── DF-26 (PLARF — mobile TEL garrisons) ──────────────────────────────
    {"system": "DF-26", "name": "Xinyang Brigade", "lat": 32.12, "lon": 114.07},
    {"system": "DF-26", "name": "Korla Garrison (XJMR)", "lat": 41.75, "lon": 86.13},
    {"system": "DF-26", "name": "Dali Garrison", "lat": 25.70, "lon": 100.18},
    {"system": "DF-26", "name": "Yulin Garrison", "lat": 38.29, "lon": 109.73},

    # ── YJ-18 (representative PLAN fleet concentration areas) ─────────────
    {"system": "YJ-18", "name": "South Sea Fleet — Sanya (Yulin)", "lat": 18.23, "lon": 109.52},
    {"system": "YJ-18", "name": "East Sea Fleet — Ningbo/Zhoushan", "lat": 29.87, "lon": 121.55},
    {"system": "YJ-18", "name": "North Sea Fleet — Qingdao", "lat": 36.07, "lon": 120.38},
    {"system": "YJ-18", "name": "East China Sea Patrol Zone", "lat": 26.50, "lon": 124.00},

    # ── S-400 (PRC deployment sites — OSINT / commercial satellite imagery) ─
    {"system": "S-400", "name": "Zhuhai SAM Battery", "lat": 22.27, "lon": 113.57},
    {"system": "S-400", "name": "Nanjing SAM Battery", "lat": 32.06, "lon": 118.79},
    {"system": "S-400", "name": "Beijing Capital Defense Battery", "lat": 40.05, "lon": 116.69},
    {"system": "S-400", "name": "Shenyang SAM Battery", "lat": 41.80, "lon": 123.43},
    {"system": "S-400", "name": "Hotan SAM Battery (XJMR)", "lat": 37.12, "lon": 79.93},
]


def get_zones() -> list[dict]:
    """Return deployment sites enriched with weapon system metadata."""
    zones = []
    for site in _SITES:
        sys_meta = WEAPON_SYSTEMS.get(site["system"], {})
        zones.append({
            "system": site["system"],
            "name": site["name"],
            "lat": site["lat"],
            "lon": site["lon"],
            "range_km": sys_meta.get("range_km", 0),
            "color": sys_meta.get("color", "#ffffff"),
            "fill_opacity": sys_meta.get("fill_opacity", 0.07),
            "weight": sys_meta.get("weight", 1),
            "dash_array": sys_meta.get("dash_array"),
        })
    return zones
