#!/usr/bin/env python3
# CUI // SP-CTI
"""STRATEGOS — A2/AD Zone Mapper (Anti-Access / Area Denial).

Computes threat rings for PRC A2/AD weapon systems in the Indo-Pacific theater:
  DF-21D ASBM   (carrier killer, ~900nm reach)
  DF-26  IRBM   (~3,500nm, Guam strike range)
  YJ-18  ASCM   (~290nm from submarine, ~540nm from Type 055)
  HHQ-9B SAM    (~75nm)
  S-400          (~200nm, ~270nm w/ 40N6E)
  DF-17 HGV     (~1,600nm, hypersonic)

Each system is modeled as a circle (center_lon, center_lat, radius_km).
Outputs GeoJSON FeatureCollections for map overlay.

Also produces a chokepoint analysis:
  - Taiwan Strait (110nm wide at narrowest)
  - Luzon Strait (250nm)
  - Miyako Strait (170nm)
  - Soya Strait (40nm)

Usage:
  from tools.strategos.a2ad_mapper import A2ADMapper

  mapper = A2ADMapper()
  geojson = mapper.compute_threat_rings("pacific")
  chokepoints = mapper.analyze_chokepoints("pacific")
  overlay = mapper.full_overlay("pacific")
"""
from __future__ import annotations

import math
from typing import Any

from tools.strategos.theater import get_theater


# ── Constants ─────────────────────────────────────────────────────────────────

_NM_TO_KM = 1.852
_KM_TO_DEG_LAT = 1.0 / 111.0  # approximate degrees latitude per km


def _km_to_deg_lon(lat_deg: float) -> float:
    return 1.0 / (111.0 * math.cos(math.radians(lat_deg)))


# A2/AD system definitions: (system_id, label, threat_ring_nm, color, category)
A2AD_SYSTEMS = [
    ("df-21d",   "DF-21D ASBM",        900,   "#e74c3c", "ASBM"),
    ("df-26",    "DF-26 IRBM",        3500,   "#c0392b", "IRBM"),
    ("df-17",    "DF-17 HGV",         1600,   "#8e44ad", "HGV"),
    ("yj-18-sub","YJ-18 ASCM (sub)",   290,   "#e67e22", "ASCM"),
    ("yj-18-055","YJ-18 ASCM (Type055)",540,  "#f39c12", "ASCM"),
    ("hhq-9b",   "HHQ-9B SAM",          75,   "#27ae60", "SAM"),
    ("s-400",    "S-400 / HQ-9",       200,   "#2980b9", "SAM"),
    ("ld-2000",  "LD-2000 CIWS",        12,   "#7f8c8d", "CIWS"),
]

# Chokepoints: (id, label, lon, lat, width_nm, strategic_importance)
PACIFIC_CHOKEPOINTS = [
    ("taiwan-strait",   "Taiwan Strait (Narrowest)", 120.00, 24.70, 110, "critical"),
    ("luzon-strait",    "Luzon Strait",              121.80, 19.50, 250, "critical"),
    ("miyako-strait",   "Miyako Strait",             125.30, 24.80, 170, "high"),
    ("soya-strait",     "Soya Strait (La Perouse)",  141.85, 45.50, 40,  "high"),
    ("tsushima",        "Tsushima Strait",           129.50, 34.20, 100, "medium"),
    ("malacca",         "Strait of Malacca",         103.80,  2.50, 50,  "critical"),
    ("lombok",          "Lombok Strait",             115.75, -8.80, 40,  "high"),
    ("sunda",           "Sunda Strait",              105.90, -6.00, 25,  "medium"),
    ("second-island",   "Second Island Chain (Guam)", 144.65, 13.54, 500, "high"),
]


# ── Circle GeoJSON approximation ──────────────────────────────────────────────

def _circle_geojson(center_lon: float, center_lat: float, radius_nm: float, n_points: int = 64) -> dict:
    """Approximate a great-circle ring as a GeoJSON Polygon."""
    radius_km = radius_nm * _NM_TO_KM
    coords = []
    for i in range(n_points + 1):
        angle = math.radians(i * 360 / n_points)
        dlat = radius_km * math.cos(angle) * _KM_TO_DEG_LAT
        dlon = radius_km * math.sin(angle) * _km_to_deg_lon(center_lat)
        coords.append([round(center_lon + dlon, 5), round(center_lat + dlat, 5)])
    return {"type": "Polygon", "coordinates": [coords]}


# ── A2AD Mapper ───────────────────────────────────────────────────────────────

class A2ADMapper:
    """Compute A2/AD threat rings and chokepoint overlays for the Pacific theater."""

    def compute_threat_rings(self, theater_id: str = "pacific") -> dict[str, Any]:
        """Return GeoJSON FeatureCollection of all A2/AD threat rings."""
        theater = get_theater(theater_id)
        features = []

        # Get PRC ORBAT units with weapon system data
        prc_units = [u for u in theater.orbat if u.actor == "PRC" and u.lat != 0 and u.lon != 0]

        for unit in prc_units:
            threat_nm = float(unit.properties.get("threat_ring_nm", 0))
            if threat_nm <= 0:
                continue
            weapon_type = unit.properties.get("weapon_type", unit.unit_type)
            system_id = unit.id
            color = "#e74c3c"
            if "IRBM" in weapon_type.upper():
                color = "#c0392b"
            elif "SAM" in weapon_type.upper():
                color = "#2980b9"
            elif "ASBM" in weapon_type.upper():
                color = "#e74c3c"
            elif unit.unit_type == "aircraft":
                color = "#8e44ad"
            elif unit.unit_type == "submarine":
                color = "#e67e22"

            geom = _circle_geojson(unit.lon, unit.lat, threat_nm)
            features.append({
                "type": "Feature",
                "geometry": geom,
                "properties": {
                    "id": system_id,
                    "label": unit.name,
                    "actor": unit.actor,
                    "threat_ring_nm": threat_nm,
                    "threat_ring_km": round(threat_nm * _NM_TO_KM, 1),
                    "unit_type": unit.unit_type,
                    "weapon_type": weapon_type,
                    "count": unit.count,
                    "color": color,
                    "opacity": 0.15,
                    "layer": "a2ad",
                },
            })

        # Add doctrine-based fixed emitter positions (First Island Chain coverage)
        doctrine_emitters = [
            ("df-21d-fujian",  "DF-21D Battery (Fujian Province)", 119.30, 26.05, 900,   "#e74c3c", "ASBM"),
            ("df-21d-guangd",  "DF-21D Battery (Guangdong)", 113.50, 23.10, 900,          "#e74c3c", "ASBM"),
            ("df-26-central",  "DF-26 Battery (Central China)", 110.50, 30.20, 3500,       "#c0392b", "IRBM"),
            ("df-17-zhengzhou","DF-17 HGV Battery (Zhengzhou)", 113.67, 34.76, 1600,       "#8e44ad", "HGV"),
            ("s400-hq9-hainan","HQ-9/S-400 Battery (Hainan)", 110.35, 19.60, 200,         "#2980b9", "SAM"),
            ("s400-hq9-spratly","HQ-9 Battery (Spratly Islands)", 114.28, 10.20, 200,     "#2980b9", "SAM"),
        ]
        for did, dlabel, dlon, dlat, drange, dcolor, dtype in doctrine_emitters:
            geom = _circle_geojson(dlon, dlat, drange)
            features.append({
                "type": "Feature",
                "geometry": geom,
                "properties": {
                    "id": did,
                    "label": dlabel,
                    "actor": "PRC",
                    "threat_ring_nm": drange,
                    "threat_ring_km": round(drange * _NM_TO_KM, 1),
                    "weapon_type": dtype,
                    "color": dcolor,
                    "opacity": 0.12,
                    "layer": "a2ad_doctrine",
                },
            })

        return {
            "type": "FeatureCollection",
            "features": features,
            "metadata": {
                "theater_id": theater_id,
                "feature_count": len(features),
                "layer": "a2ad",
            },
        }

    def analyze_chokepoints(self, theater_id: str = "pacific") -> dict[str, Any]:
        """Return chokepoint analysis with A2/AD coverage assessment."""
        theater = get_theater(theater_id)
        results = []

        for cp_id, label, lon, lat, width_nm, importance in PACIFIC_CHOKEPOINTS:
            if not theater.bounds.contains(lon, lat):
                continue

            # Count how many PRC A2/AD systems can reach this chokepoint
            coverage = []
            for unit in theater.orbat:
                if unit.actor != "PRC":
                    continue
                if unit.lat == 0 and unit.lon == 0:
                    continue
                threat_nm = float(unit.properties.get("threat_ring_nm", 0))
                if threat_nm <= 0:
                    continue
                dist_km = _haversine(unit.lon, unit.lat, lon, lat)
                dist_nm = dist_km / _NM_TO_KM
                if dist_nm <= threat_nm:
                    coverage.append({
                        "unit_id": unit.id,
                        "unit_name": unit.name,
                        "distance_nm": round(dist_nm, 1),
                        "threat_ring_nm": threat_nm,
                    })

            results.append({
                "id": cp_id,
                "label": label,
                "lon": lon,
                "lat": lat,
                "width_nm": width_nm,
                "strategic_importance": importance,
                "prc_coverage_systems": len(coverage),
                "covered_by": coverage,
                "denial_probability": min(1.0, len(coverage) * 0.15),
            })

        return {
            "theater_id": theater_id,
            "chokepoints": results,
            "critical_count": sum(1 for c in results if c["strategic_importance"] == "critical"),
        }

    def full_overlay(self, theater_id: str = "pacific") -> dict[str, Any]:
        """Return combined A2/AD rings + chokepoints for map rendering."""
        rings = self.compute_threat_rings(theater_id)
        chokepoints = self.analyze_chokepoints(theater_id)

        # Add chokepoint markers as Point features
        for cp in chokepoints["chokepoints"]:
            rings["features"].append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [cp["lon"], cp["lat"]]},
                "properties": {
                    "id": cp["id"],
                    "label": cp["label"],
                    "width_nm": cp["width_nm"],
                    "importance": cp["strategic_importance"],
                    "prc_systems": cp["prc_coverage_systems"],
                    "denial_probability": cp["denial_probability"],
                    "layer": "chokepoint",
                    "color": "#f1c40f" if cp["strategic_importance"] == "critical" else "#ecf0f1",
                },
            })

        return {
            "type": "FeatureCollection",
            "features": rings["features"],
            "metadata": {
                "theater_id": theater_id,
                "a2ad_rings": len(rings["features"]) - len(chokepoints["chokepoints"]),
                "chokepoints": len(chokepoints["chokepoints"]),
                "total_features": len(rings["features"]),
            },
        }


def _haversine(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Great-circle distance in km."""
    R = 6371.0
    dlon = math.radians(lon2 - lon1)
    dlat = math.radians(lat2 - lat1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))
