"""Amphibious threat assessment — PLAN lift capacity and landing zone analysis.

Data: open-source OSINT (RAND, CSIS, IISS Military Balance, Jane's publicly available).
Beach gradients: derived from GEBCO/SRTM30_PLUS public bathymetric data.
Fleet figures: unclassified publicly reported values only.
"""

from __future__ import annotations

import math

# ── Taiwan Coastal Landing Zones ────────────────────────────────────────────
# Slope (deg) = mean seafloor gradient over 5 km approach corridor.
# Source: GEBCO 2023 public bathymetry; slope derived from SRTM30_PLUS.
# Viability: < 2° = viable | 2–5° = marginal | > 5° = unsuitable

LANDING_ZONES: list[dict] = [
    {
        "id": "TW-W1",
        "name": "Zhanghua Coastal Plain",
        "sector": "West",
        "lat": 24.07,
        "lon": 120.45,
        "slope_deg": 1.3,
        "beach_width_m": 320,
        "tidal_range_m": 3.8,
        "substrate": "sand/silt",
        "obstacles": "tidal flats (3–5 km offshore)",
        "notes": "Widest coastal plain on Taiwan west coast; suitable for mechanized landing craft.",
    },
    {
        "id": "TW-W2",
        "name": "Yunlin–Chiayi Littoral",
        "sector": "West",
        "lat": 23.62,
        "lon": 120.18,
        "slope_deg": 1.5,
        "beach_width_m": 280,
        "tidal_range_m": 3.2,
        "substrate": "fine sand",
        "obstacles": "offshore aquaculture ponds",
        "notes": "Gentle gradient; shallow draft suitable. Aquaculture infrastructure provides cover.",
    },
    {
        "id": "TW-W3",
        "name": "Tainan Coastal Strip",
        "sector": "West",
        "lat": 23.02,
        "lon": 120.12,
        "slope_deg": 1.8,
        "beach_width_m": 210,
        "tidal_range_m": 2.9,
        "substrate": "medium sand",
        "obstacles": "industrial port approaches",
        "notes": "Near Tainan industrial zone; road network 2 km inland. Historically assessed viable.",
    },
    {
        "id": "TW-P1",
        "name": "Penghu (Magong) Archipelago",
        "sector": "West (Offshore)",
        "lat": 23.57,
        "lon": 119.62,
        "slope_deg": 1.2,
        "beach_width_m": 150,
        "tidal_range_m": 2.1,
        "substrate": "coarse sand/coral",
        "obstacles": "coral shoals, Penghu garrison",
        "notes": "Stepping-stone objective. Control provides protected assembly area 50 km from main island.",
    },
    {
        "id": "TW-NW1",
        "name": "Taoyuan–Danshui Corridor",
        "sector": "Northwest",
        "lat": 25.03,
        "lon": 121.01,
        "slope_deg": 2.1,
        "beach_width_m": 180,
        "tidal_range_m": 1.8,
        "substrate": "mixed sand/gravel",
        "obstacles": "TAOYUAN airport 8 km inland",
        "notes": "Marginal gradient; strategic value due to proximity to Taipei Metro and airport.",
    },
    {
        "id": "TW-N1",
        "name": "Danshui Estuary",
        "sector": "North",
        "lat": 25.18,
        "lon": 121.42,
        "slope_deg": 3.4,
        "beach_width_m": 80,
        "tidal_range_m": 1.5,
        "substrate": "mixed",
        "obstacles": "Danshui River mouth, urban density",
        "notes": "Marginal–unsuitable. River mouth complicates landing craft maneuver.",
    },
    {
        "id": "TW-S1",
        "name": "Kaohsiung South Coast",
        "sector": "Southwest",
        "lat": 22.53,
        "lon": 120.38,
        "slope_deg": 2.2,
        "beach_width_m": 160,
        "tidal_range_m": 1.6,
        "substrate": "sand",
        "obstacles": "port security zone, industrial piers",
        "notes": "Marginal. Proximity to Kaohsiung naval base increases defender response speed.",
    },
    {
        "id": "TW-E1",
        "name": "Hualien Valley Approach",
        "sector": "East",
        "lat": 23.98,
        "lon": 121.61,
        "slope_deg": 11.4,
        "beach_width_m": 40,
        "tidal_range_m": 0.6,
        "substrate": "gravel/rock",
        "obstacles": "Pacific deep-water trench, LCC mountains",
        "notes": "Unsuitable. Pacific face drops steeply; Central Mountain Range blocks inland advance.",
    },
    {
        "id": "TW-E2",
        "name": "Taitung Coastal Shelf",
        "sector": "East",
        "lat": 22.75,
        "lon": 121.15,
        "slope_deg": 8.7,
        "beach_width_m": 30,
        "tidal_range_m": 0.5,
        "substrate": "rock/gravel",
        "obstacles": "near-vertical bathymetry",
        "notes": "Unsuitable. Pacific Plate boundary produces extreme seabed gradients.",
    },
]


def slope_viability(slope_deg: float) -> str:
    """Return viability assessment string for a given seafloor gradient."""
    if slope_deg < 2.0:
        return "viable"
    if slope_deg < 5.0:
        return "marginal"
    return "unsuitable"


def slope_color(slope_deg: float) -> str:
    v = slope_viability(slope_deg)
    return {"viable": "#22c55e", "marginal": "#f59e0b", "unsuitable": "#ef4444"}[v]


# ── PLAN Amphibious Fleet Composition ───────────────────────────────────────
# Figures: IISS Military Balance 2024, RAND PLA Navy assessments, publicly available.

AMPHIBIOUS_FLEET: list[dict] = [
    {
        "class": "Type 075 LHD",
        "name": "Yushen-class",
        "vessels": 3,
        "troops_per_ship": 900,
        "vehicles_per_ship": 40,
        "helicopter_spots": 30,
        "lcac_capacity": 2,
        "notes": "Hainan, Guangxi, Anhui commissioned 2021–2024. Well deck supports 2× LCAC or 4× LCU.",
        "color": "#6366f1",
    },
    {
        "class": "Type 071 LPD",
        "name": "Yuzhao-class",
        "vessels": 8,
        "troops_per_ship": 800,
        "vehicles_per_ship": 15,
        "helicopter_spots": 4,
        "lcac_capacity": 1,
        "notes": "8 ships as of 2024. LCAC-capable well deck; primary PLAN amphibious workhorse.",
        "color": "#8b5cf6",
    },
    {
        "class": "Type 072A/B LST",
        "name": "Yuting-class",
        "vessels": 12,
        "troops_per_ship": 200,
        "vehicles_per_ship": 10,
        "helicopter_spots": 1,
        "lcac_capacity": 0,
        "notes": "Beach-landing capable. Bow ramp direct vehicle discharge onto beaches < 2° slope.",
        "color": "#a78bfa",
    },
    {
        "class": "Type 072III LST",
        "name": "Yuting III-class",
        "vessels": 6,
        "troops_per_ship": 180,
        "vehicles_per_ship": 8,
        "helicopter_spots": 0,
        "lcac_capacity": 0,
        "notes": "Older LST variant. Limited sea-state tolerance; optimized for < Beaufort 5.",
        "color": "#c4b5fd",
    },
    {
        "class": "Type 073/074 LSM",
        "name": "Yuliang/Yuhai-class",
        "vessels": 14,
        "troops_per_ship": 120,
        "vehicles_per_ship": 6,
        "helicopter_spots": 0,
        "lcac_capacity": 0,
        "notes": "Medium landing ships. Supplement first wave with equipment and sustainment.",
        "color": "#ddd6fe",
    },
]


def calc_lift_capacity() -> dict:
    """Calculate total first-wave lift capacity for assessed PLAN amphibious fleet."""
    total_troops = 0
    total_vehicles = 0
    breakdown = []
    for ship in AMPHIBIOUS_FLEET:
        troops = ship["vessels"] * ship["troops_per_ship"]
        vehicles = ship["vessels"] * ship["vehicles_per_ship"]
        total_troops += troops
        total_vehicles += vehicles
        breakdown.append({
            "class": ship["class"],
            "name": ship["name"],
            "vessels": ship["vessels"],
            "troops": troops,
            "vehicles": vehicles,
            "color": ship["color"],
        })
    return {
        "total_troops": total_troops,
        "total_vehicles": total_vehicles,
        "breakdown": breakdown,
        "note": (
            "First-wave capacity. Does not include civilian RORO/ferry conscription (~2,000–4,000 additional)"
            " or second-echelon follow-on forces."
        ),
    }


# ── Taiwan Strait Crossing Analysis ─────────────────────────────────────────

# Representative crossing corridors (Fujian coast → Taiwan west coast)
CROSSING_CORRIDORS: list[dict] = [
    {"id": "C1", "name": "Xiamen → Tainan", "dist_km": 165, "origin_lat": 24.48, "origin_lon": 118.08, "dest_lat": 23.02, "dest_lon": 120.12},
    {"id": "C2", "name": "Quanzhou → Zhanghua", "dist_km": 148, "origin_lat": 24.90, "origin_lon": 118.60, "dest_lat": 24.07, "dest_lon": 120.45},
    {"id": "C3", "name": "Pingtan → Taoyuan", "dist_km": 130, "origin_lat": 25.52, "origin_lon": 119.78, "dest_lat": 25.03, "dest_lon": 121.01},
    {"id": "C4", "name": "Zhoushan → Danshui", "dist_km": 290, "origin_lat": 29.87, "origin_lon": 121.55, "dest_lat": 25.18, "dest_lon": 121.42},
]


def calc_crossing_time(dist_km: float, speed_kts: float = 15.0) -> dict:
    """Return crossing time breakdown for given distance and speed."""
    dist_nm = dist_km / 1.852
    hours = dist_nm / speed_kts
    h = int(hours)
    m = int((hours - h) * 60)
    return {
        "dist_km": round(dist_km, 1),
        "dist_nm": round(dist_nm, 1),
        "speed_kts": speed_kts,
        "hours_decimal": round(hours, 2),
        "hours": h,
        "minutes": m,
        "label": f"{h}h {m:02d}m",
    }


def get_crossing_analysis() -> list[dict]:
    """Return all corridors with crossing times at 15 kts."""
    result = []
    for c in CROSSING_CORRIDORS:
        ct = calc_crossing_time(c["dist_km"])
        result.append({**c, **ct})
    return result


# ── Radar Detection Probability Curve ───────────────────────────────────────
# Simplified model — publicly available radar performance parameters.
# Taiwan operates AN/FPS-115 PAVE PAWS, indigenous TPS-77, OTH-B class radar.
# Detection probability uses a modified sigmoid as a function of slant range.

# Assessed radar horizon for surface ship detection (carrier fleet / amphibious group):
_RADAR_HORIZON_KM = 40.0   # surface-search radar effective range vs. LST-class target
_OTH_SATURATION_KM = 250.0  # OTH-B class saturation range (unreliable below ~80 km noise)
_ALPHA = 6.0                # sigmoid slope factor


def detection_probability(dist_km: float, ew_offset_h: float = 0.0) -> float:
    """
    P(detection) at given range.

    ew_offset_h: electronic warfare / emissions control reduction offset (hours of EMCON
                 advantage modeled as equivalent range extension, ~15 km/h).
    """
    effective_dist = dist_km + ew_offset_h * 15.0
    # Combined surface-radar + OTH probability
    p_surface = 1.0 / (1.0 + math.exp(_ALPHA * (effective_dist - _RADAR_HORIZON_KM) / _RADAR_HORIZON_KM))
    # OTH degrades below ~80 km (cluttered by surface multipath), peaks ~120–200 km
    oth_center = 150.0
    oth_width = 80.0
    p_oth = 0.65 * math.exp(-0.5 * ((effective_dist - oth_center) / oth_width) ** 2)
    p_combined = min(1.0, p_surface + p_oth * (1.0 - p_surface))
    return round(p_combined, 4)


def get_detection_curve(step_km: float = 5.0) -> list[dict]:
    """Return detection probability at discrete distances 0–300 km."""
    curve = []
    d = 0.0
    while d <= 300.0:
        curve.append({
            "dist_km": round(d, 1),
            "p_no_ew": detection_probability(d, 0),
            "p_2h_ew": detection_probability(d, 2),
            "p_4h_ew": detection_probability(d, 4),
        })
        d += step_km
    return curve


# ── Weather Windows ──────────────────────────────────────────────────────────
# Taiwan Strait meteorological patterns — publicly available WMO/CWA climatology.
# Beaufort scale averaged for month; typhoon risk from JTWC climatology.

WEATHER_WINDOWS: list[dict] = [
    {"month": "Jan", "num": 1,  "beaufort_avg": 8.5, "wave_h_m": 3.8, "typhoon_risk": 0,   "window": "unsuitable",  "notes": "Peak NE monsoon. Sustained gale-force. Amphibious ops infeasible."},
    {"month": "Feb", "num": 2,  "beaufort_avg": 7.8, "wave_h_m": 3.2, "typhoon_risk": 0,   "window": "unsuitable",  "notes": "Monsoon persists. Crossing hazardous for LSTs/LSMs."},
    {"month": "Mar", "num": 3,  "beaufort_avg": 5.2, "wave_h_m": 1.9, "typhoon_risk": 0,   "window": "marginal",    "notes": "Monsoon weakening. Calmer windows 7–12 day duration. Operational risk elevated."},
    {"month": "Apr", "num": 4,  "beaufort_avg": 4.1, "wave_h_m": 1.4, "typhoon_risk": 0,   "window": "favorable",   "notes": "Spring transition. Multi-day calm windows. Good amphibious weather."},
    {"month": "May", "num": 5,  "beaufort_avg": 3.4, "wave_h_m": 1.1, "typhoon_risk": 2,   "window": "optimal",     "notes": "Peak spring window. Consistently calm. Preferred historical assault month in RAND/CSIS scenarios."},
    {"month": "Jun", "num": 6,  "beaufort_avg": 3.9, "wave_h_m": 1.5, "typhoon_risk": 8,   "window": "favorable",   "notes": "Plum rain season. Generally workable; typhoon probability rising."},
    {"month": "Jul", "num": 7,  "beaufort_avg": 5.8, "wave_h_m": 2.3, "typhoon_risk": 28,  "window": "unsuitable",  "notes": "Typhoon season onset. Unpredictable 48h weather breaks; extreme operational risk."},
    {"month": "Aug", "num": 8,  "beaufort_avg": 6.1, "wave_h_m": 2.6, "typhoon_risk": 35,  "window": "unsuitable",  "notes": "Peak typhoon season. Catastrophic risk to amphibious fleet in transit."},
    {"month": "Sep", "num": 9,  "beaufort_avg": 5.5, "wave_h_m": 2.2, "typhoon_risk": 25,  "window": "unsuitable",  "notes": "Late typhoon season. Occasional large storms through late September."},
    {"month": "Oct", "num": 10, "beaufort_avg": 4.3, "wave_h_m": 1.6, "typhoon_risk": 8,   "window": "favorable",   "notes": "Post-typhoon transition. Autumn window. NE monsoon not yet established; 10–14 day calm periods."},
    {"month": "Nov", "num": 11, "beaufort_avg": 6.2, "wave_h_m": 2.5, "typhoon_risk": 2,   "window": "marginal",    "notes": "NE monsoon building. Short favorable windows possible early November."},
    {"month": "Dec", "num": 12, "beaufort_avg": 7.5, "wave_h_m": 3.1, "typhoon_risk": 0,   "window": "unsuitable",  "notes": "Monsoon established. Gale conditions frequent. Ops infeasible."},
]

_WINDOW_COLORS = {
    "optimal":    "#22c55e",
    "favorable":  "#84cc16",
    "marginal":   "#f59e0b",
    "unsuitable": "#ef4444",
}


def get_weather_windows() -> list[dict]:
    """Return monthly weather window assessments with display metadata."""
    return [
        {**w, "color": _WINDOW_COLORS[w["window"]]}
        for w in WEATHER_WINDOWS
    ]


# ── Summary API ──────────────────────────────────────────────────────────────

def get_summary() -> dict:
    """Aggregate summary for the dashboard card."""
    viable_zones = [z for z in LANDING_ZONES if slope_viability(z["slope_deg"]) == "viable"]
    optimal_months = [w["month"] for w in WEATHER_WINDOWS if w["window"] == "optimal"]
    favorable_months = [w["month"] for w in WEATHER_WINDOWS if w["window"] in ("optimal", "favorable")]
    lift = calc_lift_capacity()
    shortest = min(CROSSING_CORRIDORS, key=lambda c: c["dist_km"])
    crossing = calc_crossing_time(shortest["dist_km"])

    return {
        "viable_landing_zones": len(viable_zones),
        "total_zones_assessed": len(LANDING_ZONES),
        "first_wave_troops": lift["total_troops"],
        "optimal_months": optimal_months,
        "favorable_months": favorable_months,
        "shortest_crossing_km": shortest["dist_km"],
        "shortest_crossing_time": crossing["label"],
        "shortest_corridor": shortest["name"],
    }
