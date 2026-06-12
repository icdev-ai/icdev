"""AppForge Architect Reflex — generate app blueprint from selected challenge.

GREEN tier. Creates a structured specification for the build reflex.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

# App blueprint templates by vertical
VERTICAL_BLUEPRINTS: Dict[str, Dict[str, Any]] = {
    "Cybersecurity & InfoSec": {
        "data_sources": ["NVD CVE feeds", "CISA KEV catalog", "MITRE ATT&CK", "VirusTotal public API"],
        "entities": ["Threat", "Vulnerability", "Asset", "Indicator", "Campaign", "Actor"],
        "relationships": ["targets", "exploits", "mitigates", "attributed_to", "indicates"],
        "pages": ["Dashboard", "Threat Map", "Vulnerabilities", "Indicators", "Knowledge Graph"],
        "map_type": "threat_origin_geo",
    },
    "Transportation & Logistics": {
        "data_sources": ["OpenStreetMap routes", "AIS vessel tracking", "FAA flight data", "NOAA weather"],
        "entities": ["Shipment", "Supplier", "Route", "Facility", "Disruption", "Risk"],
        "relationships": ["ships_via", "supplies_to", "disrupted_by", "alternative_to", "located_at"],
        "pages": ["Dashboard", "Supply Map", "Shipments", "Risk Matrix", "Knowledge Graph"],
        "map_type": "route_network",
    },
    "Healthcare & Life Sciences": {
        "data_sources": ["ClinicalTrials.gov", "FDA FAERS", "WHO ICD-11", "PubMed abstracts"],
        "entities": ["Patient", "Site", "Protocol", "Adverse_Event", "Drug", "Outcome"],
        "relationships": ["enrolled_at", "experienced", "treated_with", "deviated_from", "associated_with"],
        "pages": ["Dashboard", "Site Map", "Enrollment", "Events", "Knowledge Graph"],
        "map_type": "site_locations",
    },
    "Energy & Utilities": {
        "data_sources": ["EIA open data", "NOAA weather", "NERC event logs", "OpenStreetMap grid"],
        "entities": ["Sensor", "Substation", "Anomaly", "Outage", "Demand_Zone", "Generator"],
        "relationships": ["feeds_into", "monitored_by", "caused_by", "predicted_by", "serves"],
        "pages": ["Dashboard", "Grid Map", "Sensors", "Anomalies", "Knowledge Graph"],
        "map_type": "infrastructure_grid",
    },
    "Space & Aerospace": {
        "data_sources": ["CelesTrak TLE data", "Space-Track.org", "NOAA space weather", "NASA HORIZONS"],
        "entities": ["Satellite", "Ground_Station", "Conjunction", "Maneuver", "Telemetry", "Orbit"],
        "relationships": ["communicates_with", "risks_collision", "orbits_in", "operated_by", "tracks"],
        "pages": ["Dashboard", "Orbital Map", "Fleet Health", "Conjunctions", "Knowledge Graph"],
        "map_type": "orbital_ground_track",
    },
    "Agriculture & Environment": {
        "data_sources": ["USDA NASS", "NOAA climate", "Sentinel-2 NDVI", "USGS water data"],
        "entities": ["Field", "Sensor", "Crop", "Weather_Event", "Prescription", "Yield"],
        "relationships": ["planted_in", "measured_by", "affected_by", "recommended_for", "produced"],
        "pages": ["Dashboard", "Field Map", "Sensors", "Prescriptions", "Knowledge Graph"],
        "map_type": "field_parcels",
    },
    "Financial Services & Banking": {
        "data_sources": ["SEC EDGAR", "FDIC BankFind", "FinCEN SAR data", "OFAC sanctions list"],
        "entities": ["Transaction", "Account", "Entity", "Alert", "Case", "Network"],
        "relationships": ["sent_to", "flagged_by", "linked_to", "investigated_in", "sanctioned"],
        "pages": ["Dashboard", "Network Graph", "Alerts", "Cases", "Knowledge Graph"],
        "map_type": "transaction_geography",
    },
}

# Default blueprint for unrecognized verticals
DEFAULT_BLUEPRINT = {
    "data_sources": ["Public APIs", "Open datasets", "Government data portals"],
    "entities": ["Entity", "Event", "Location", "Pattern", "Anomaly", "Relationship"],
    "relationships": ["related_to", "located_at", "detected_by", "correlated_with", "caused_by"],
    "pages": ["Dashboard", "Map", "Data Table", "Analysis", "Knowledge Graph"],
    "map_type": "point_markers",
}


def run(config: Dict[str, Any], trust) -> Dict[str, Any]:
    """Generate app blueprint for the selected challenge."""
    conn = get_connection()
    try:
        # Find selected challenge
        challenge = conn.execute("SELECT * FROM appforge_challenges WHERE status = 'selected' LIMIT 1").fetchone()
        if not challenge:
            return {
                "success": True,
                "metric_value": 0.0,
                "details": {"action": "none", "reason": "No selected challenge"},
            }

        challenge = dict(challenge)
        vertical = challenge["vertical"]
        blueprint_template = VERTICAL_BLUEPRINTS.get(vertical, DEFAULT_BLUEPRINT)

        # Build the blueprint
        app_name = _slugify(challenge["title"])
        blueprint = {
            "app_name": app_name,
            "challenge_id": challenge["challenge_id"],
            "title": challenge["title"],
            "vertical": vertical,
            "description": challenge["description"],
            "pain_points": json.loads(challenge.get("pain_points", "[]")),
            "data_sources": blueprint_template["data_sources"],
            "entities": blueprint_template["entities"],
            "relationships": blueprint_template["relationships"],
            "pages": blueprint_template["pages"],
            "map_type": blueprint_template["map_type"],
            "tech_stack": {
                "backend": "Flask + SQLite",
                "frontend": "Vanilla JS + Leaflet.js + D3.js",
                "theme": "Professional dark",
                "port": _next_port(),
            },
            "tables": _generate_table_list(blueprint_template["entities"]),
        }

        # Store blueprint as JSON in challenge record
        conn.execute(
            "UPDATE appforge_challenges SET status = 'architected' WHERE challenge_id = ?",
            (challenge["challenge_id"],),
        )
        # Store blueprint in a separate field or via app_path
        conn.execute(
            "UPDATE appforge_challenges SET app_path = ? WHERE challenge_id = ?",
            (json.dumps(blueprint), challenge["challenge_id"]),
        )
        conn.commit()

        return {
            "success": True,
            "metric_value": len(blueprint["entities"]),
            "details": {
                "action": "architected",
                "challenge_id": challenge["challenge_id"],
                "app_name": app_name,
                "vertical": vertical,
                "entities": blueprint["entities"],
                "pages": blueprint["pages"],
                "tables": len(blueprint["tables"]),
            },
        }
    finally:
        conn.close()


def _slugify(title: str) -> str:
    """Convert title to filesystem-safe app name."""
    import re

    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    return slug[:40]


def _next_port() -> int:
    """Find next available port starting from 5100."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT MAX(app_port) m FROM appforge_challenges WHERE app_port IS NOT NULL").fetchone()
        return (row["m"] or 5099) + 1
    except Exception:
        return 5100
    finally:
        conn.close()


def _generate_table_list(entities: list[str]) -> list[str]:
    """Generate table names from entity list."""
    return [e.lower() + "s" for e in entities] + ["kg_edges"]
