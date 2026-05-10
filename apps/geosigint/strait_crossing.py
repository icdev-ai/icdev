"""Taiwan Strait crossing analysis — Xiamen→Danshui corridor, radar thresholds, intercept windows.

Data: open-source OSINT (RAND, CSIS, IISS Military Balance, CWA climatology).
Radar parameters: publicly available / unclassified figures.
"""

from __future__ import annotations

import math

# ── Primary Crossing Corridor ────────────────────────────────────────────────
# Xiamen (PLAN East Sea Fleet staging) → Danshui (N. Taiwan landing zone)
# Distance ~333 km / ~180 nm.  At 12 kt sustained speed: 15.0 h transit.
# Source: RAND "War with China" 2016, CSIS China Power, IISS Military Balance 2024.

PRIMARY_CORRIDOR = {
    "id": "XM-DAN",
    "name": "Xiamen → Danshui",
    "origin": "Xiamen",
    "destination": "Danshui",
    "origin_lat": 24.48,
    "origin_lon": 118.08,
    "dest_lat": 25.18,
    "dest_lon": 121.42,
    "dist_km": 333.0,
    "dist_nm": 180.0,
    "notes": (
        "Longest primary west-to-north corridor. Targets Danshui/Taoyuan corridor "
        "within 25 km of Taipei Metro. Crosses deepest portion of Taiwan Strait."
    ),
}

# Speed scenario matrix — representative PLAN amphibious group speeds.
# Source: Jane's Fighting Ships, RAND assessment of PLAN fleet sea-state tolerance.
SPEED_SCENARIOS: list[dict] = [
    {"label": "Slow (LST/LSM-limited)",     "speed_kts": 10, "color": "#ef4444"},
    {"label": "Standard (LPD/LHD-led)",     "speed_kts": 12, "color": "#f59e0b"},
    {"label": "Fast (LHD-sprint, calm sea)", "speed_kts": 15, "color": "#22c55e"},
    {"label": "Destroyer escort speed",      "speed_kts": 18, "color": "#3b82f6"},
    {"label": "Fast-mover (LCAC/hover)",    "speed_kts": 40, "color": "#a78bfa",
     "notes": "LCAC air-cushion; does not apply to full amphibious group"},
]


def calc_crossing(dist_nm: float, speed_kts: float) -> dict:
    """Return crossing time breakdown for a given distance and speed."""
    hours_decimal = dist_nm / speed_kts
    h = int(hours_decimal)
    m = int((hours_decimal - h) * 60)
    dist_km = round(dist_nm * 1.852, 1)
    return {
        "dist_nm": round(dist_nm, 1),
        "dist_km": dist_km,
        "speed_kts": speed_kts,
        "hours_decimal": round(hours_decimal, 2),
        "hours": h,
        "minutes": m,
        "label": f"{h}h {m:02d}m",
    }


def get_speed_matrix(dist_nm: float = PRIMARY_CORRIDOR["dist_nm"]) -> list[dict]:
    """All speed scenarios for the primary corridor."""
    results = []
    for s in SPEED_SCENARIOS:
        ct = calc_crossing(dist_nm, s["speed_kts"])
        results.append({**s, **ct})
    return results


# ── Radar Detection Thresholds ───────────────────────────────────────────────
# Taiwan operates OTH-B class radar (~Longbow OTH), AN/FPS-115 PAVE PAWS (Leshan),
# and Tien Kung SAM acquisition radars.
# Ranges are unclassified publicly reported assessments.
# Source: RAND, CSIS China Power, Taiwan MND public statements.

RADAR_SYSTEMS: list[dict] = [
    {
        "id": "PAVE_PAWS",
        "name": "AN/FPS-115 PAVE PAWS (Leshan)",
        "type": "Early Warning / Ballistic",
        "range_km": 5000,
        "surface_ship_range_km": None,
        "notes": "Primary missile warning; limited surface ship tracking capability below 3,000 km.",
        "color": "#a78bfa",
    },
    {
        "id": "OTH",
        "name": "OTH-B Class (Leshan OTH)",
        "type": "Over-the-Horizon",
        "range_km": 3000,
        "surface_ship_range_km": 250,
        "notes": (
            "Detects fleet concentrations at 80–250 km. Below ~80 km ground clutter "
            "degrades track quality. Cued by satellite/P-3 Orion."
        ),
        "color": "#6366f1",
    },
    {
        "id": "TPS77",
        "name": "TPS-77 (Air Surveillance)",
        "type": "Air Search / Surface",
        "range_km": 450,
        "surface_ship_range_km": 200,
        "notes": "Long-range air surveillance; secondary surface vessel detection > 200 km.",
        "color": "#3b82f6",
    },
    {
        "id": "SURFACE_SEARCH",
        "name": "Surface-Search Radar (coastal/frigate)",
        "type": "Surface Search",
        "range_km": None,
        "surface_ship_range_km": 40,
        "notes": (
            "Effective range vs. LST-class target ~40 km. "
            "Operated by ROCN frigates and P-3C Orion maritime patrol aircraft."
        ),
        "color": "#22c55e",
    },
    {
        "id": "TK3_RADAR",
        "name": "Tien Kung III SAM Acquisition",
        "type": "SAM Acquisition",
        "range_km": 200,
        "surface_ship_range_km": None,
        "notes": "Tracks aerial threats; not optimized for surface ship detection.",
        "color": "#f59e0b",
    },
]

# Detection probability as a function of range (identical physics to amphibious_analyzer
# but parametrized for each sensor).  Uses sigmoid + Gaussian OTH composite.

_RADAR_HORIZON_KM = 40.0
_OTH_CENTER_KM = 150.0
_OTH_WIDTH_KM = 80.0
_ALPHA = 6.0


def detection_probability(dist_km: float, ew_offset_h: float = 0.0) -> float:
    """Combined detection probability at range `dist_km` with optional EW offset.

    ew_offset_h: hours of EMCON/ECM advantage, modeled as +15 km/h effective range.
    """
    eff = dist_km + ew_offset_h * 15.0
    p_surface = 1.0 / (1.0 + math.exp(_ALPHA * (eff - _RADAR_HORIZON_KM) / _RADAR_HORIZON_KM))
    p_oth = 0.65 * math.exp(-0.5 * ((eff - _OTH_CENTER_KM) / _OTH_WIDTH_KM) ** 2)
    return round(min(1.0, p_surface + p_oth * (1.0 - p_surface)), 4)


def get_detection_curve(step_km: float = 5.0) -> list[dict]:
    points = []
    d = 0.0
    while d <= 350.0:
        points.append({
            "dist_km": round(d, 1),
            "p_no_ew": detection_probability(d, 0),
            "p_2h_ew": detection_probability(d, 2),
            "p_4h_ew": detection_probability(d, 4),
        })
        d += step_km
    return points


# ── Intercept Window Calculator ──────────────────────────────────────────────
# Model: fleet detected at dist_km from Taiwan coast moving at speed_kts.
# Defender response:
#   scramble_h  = time to sortie first ROCAF interceptors (assessed 0.25h / 15 min)
#   transit_h   = intercept transit at ROCAF F-16 cruise (550 kt) to detection point
# Intercept window = remaining_transit_h - (scramble_h + transit_h)
# Source: RAND, open-source ROCAF readiness assessments.

_ROCAF_CRUISE_KTS = 550.0    # F-16V cruise intercept speed
_SCRAMBLE_H = 0.25           # 15 min QRA scramble
_BASE_TO_COAST_KM = 50.0     # approximate Hsinchu/Tainan base → coast vector

# ROCAF base intercept vectors (representative, open-source)
ROCAF_BASES: list[dict] = [
    {"name": "Hsinchu AB", "lat": 24.82, "lon": 120.94, "assets": "F-16V",
     "dist_to_strait_km": 45, "color": "#22c55e"},
    {"name": "Tainan AB",  "lat": 23.00, "lon": 120.21, "assets": "F-16V / AIDC F-CK-1",
     "dist_to_strait_km": 60, "color": "#3b82f6"},
    {"name": "Chiayi AB",  "lat": 23.46, "lon": 120.39, "assets": "F-16V",
     "dist_to_strait_km": 55, "color": "#f59e0b"},
    {"name": "Ching Chuan Kang (Taichung)", "lat": 24.27, "lon": 120.62,
     "assets": "F-CK-1 IDF", "dist_to_strait_km": 40, "color": "#a78bfa"},
]


def calc_intercept_window(
    detect_km: float,
    speed_kts: float = 12.0,
    base_dist_km: float = _BASE_TO_COAST_KM,
) -> dict:
    """Return intercept window parameters for a given detection range and fleet speed."""
    time_to_coast_h = detect_km / (speed_kts * 1.852)
    transit_to_intercept_h = (detect_km + base_dist_km) / (_ROCAF_CRUISE_KTS * 1.852)
    total_response_h = _SCRAMBLE_H + transit_to_intercept_h
    window_h = time_to_coast_h - total_response_h
    feasible = window_h > 0
    h = int(abs(window_h))
    m = int((abs(window_h) - h) * 60)
    sign = "" if window_h >= 0 else "-"
    return {
        "detect_km": round(detect_km, 1),
        "detect_nm": round(detect_km / 1.852, 1),
        "fleet_speed_kts": speed_kts,
        "time_to_coast_h": round(time_to_coast_h, 2),
        "time_to_coast_label": f"{int(time_to_coast_h)}h {int((time_to_coast_h % 1) * 60):02d}m",
        "scramble_h": _SCRAMBLE_H,
        "transit_to_intercept_h": round(transit_to_intercept_h, 2),
        "total_response_h": round(total_response_h, 2),
        "window_h": round(window_h, 2),
        "window_label": f"{sign}{h}h {m:02d}m",
        "feasible": feasible,
        "p_detection": detection_probability(detect_km),
    }


def get_intercept_table(speed_kts: float = 12.0) -> list[dict]:
    """Intercept window at standard detection ranges for a given fleet speed."""
    detect_ranges_km = [333, 280, 220, 160, 120, 80, 40]
    return [calc_intercept_window(d, speed_kts) for d in detect_ranges_km]


# ── Summary API ──────────────────────────────────────────────────────────────

def get_summary() -> dict:
    c = PRIMARY_CORRIDOR
    at_12kt = calc_crossing(c["dist_nm"], 12.0)
    intercept_at_detect = calc_intercept_window(c["dist_km"] / 2, 12.0)  # detected at midpoint
    return {
        "corridor": c["name"],
        "dist_km": c["dist_km"],
        "dist_nm": c["dist_nm"],
        "crossing_at_12kt": at_12kt["label"],
        "crossing_hours_decimal": at_12kt["hours_decimal"],
        "radar_systems": len(RADAR_SYSTEMS),
        "rocaf_bases": len(ROCAF_BASES),
        "midpoint_window": intercept_at_detect["window_label"],
        "midpoint_window_feasible": intercept_at_detect["feasible"],
    }
