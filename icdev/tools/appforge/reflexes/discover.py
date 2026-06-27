"""AppForge Discover Reflex — scan innovation, creative, and research engines
to identify high-value challenges across verticals and industries.

GREEN tier (read-only). Runs daily.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.daemon.base import generate_id, utcnow_iso  # noqa: E402
from tools.db.storage import get_connection  # noqa: E402

# Verticals to scan across
VERTICALS = [
    "Defense & Intelligence",
    "Healthcare & Life Sciences",
    "Financial Services & Banking",
    "Energy & Utilities",
    "Transportation & Logistics",
    "Manufacturing & Industrial",
    "Cybersecurity & InfoSec",
    "Government & Civic Tech",
    "Education & Research",
    "Agriculture & Environment",
    "Telecommunications",
    "Space & Aerospace",
]


def _get_innovation_signals() -> list[dict]:
    """Pull recent innovation signals from the Innovation Engine."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM innovation_signals "
            "WHERE created_at > datetime('now', '-7 days') "
            "ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _get_creative_gaps() -> list[dict]:
    """Pull recent creative engine gaps."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM creative_gaps "
            "WHERE created_at > datetime('now', '-7 days') "
            "ORDER BY composite_score DESC LIMIT 30"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _get_research_challenges() -> list[dict]:
    """Pull recent research engine challenges."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM research_challenges "
            "WHERE created_at > datetime('now', '-7 days') "
            "ORDER BY score DESC LIMIT 30"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _already_discovered(title: str) -> bool:
    """Check if a similar challenge was already discovered."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT COUNT(*) c FROM appforge_challenges WHERE title = %s", (title,)).fetchone()
        return row["c"] > 0
    except Exception:
        return False
    finally:
        conn.close()


def _synthesize_challenges(
    signals: list[dict],
    gaps: list[dict],
    research: list[dict],
) -> list[dict]:
    """Synthesize cross-engine findings into app challenge candidates.

    Uses keyword overlap and vertical classification to create
    actionable challenge definitions.
    """
    challenges = []

    # From innovation signals — look for tool/platform opportunities
    for sig in signals:
        title = sig.get("title", sig.get("signal_title", ""))
        desc = sig.get("description", sig.get("signal_description", ""))
        if not title or _already_discovered(title):
            continue
        vertical = _classify_vertical(title + " " + desc)
        challenges.append(
            {
                "title": title,
                "description": desc,
                "vertical": vertical,
                "source": "innovation_engine",
                "pain_points": json.dumps([desc[:200]]),
                "score": float(sig.get("score", sig.get("relevance_score", 0.5))),
            }
        )

    # From creative gaps — customer pain points
    for gap in gaps:
        title = gap.get("title", gap.get("gap_title", ""))
        desc = gap.get("description", gap.get("gap_description", ""))
        if not title or _already_discovered(title):
            continue
        vertical = _classify_vertical(title + " " + desc)
        challenges.append(
            {
                "title": title,
                "description": desc,
                "vertical": vertical,
                "source": "creative_engine",
                "pain_points": gap.get("pain_points", "[]"),
                "score": float(gap.get("composite_score", 0.5)),
            }
        )

    # From research challenges
    for res in research:
        title = res.get("title", res.get("challenge_title", ""))
        desc = res.get("description", res.get("challenge_description", ""))
        if not title or _already_discovered(title):
            continue
        vertical = _classify_vertical(title + " " + desc)
        challenges.append(
            {
                "title": title,
                "description": desc,
                "vertical": vertical,
                "source": "research_engine",
                "pain_points": json.dumps([desc[:200]]),
                "score": float(res.get("score", 0.5)),
            }
        )

    # If no engine data available, generate seed challenges from vertical catalog
    if not challenges:
        challenges = _generate_seed_challenges()

    return sorted(challenges, key=lambda c: c["score"], reverse=True)


def _classify_vertical(text: str) -> str:
    """Simple keyword-based vertical classification."""
    text_lower = text.lower()
    keywords = {
        "Defense & Intelligence": ["signal", "sigint", "radar", "military", "defense", "intelligence", "spectrum"],
        "Healthcare & Life Sciences": ["patient", "hospital", "clinical", "medical", "health", "pharma", "ehr"],
        "Financial Services & Banking": ["banking", "financial", "trading", "fraud", "compliance", "fintech"],
        "Energy & Utilities": ["energy", "grid", "solar", "wind", "utility", "power", "oil", "gas"],
        "Transportation & Logistics": ["logistics", "shipping", "fleet", "supply chain", "transport", "route"],
        "Manufacturing & Industrial": ["manufacturing", "factory", "iot", "sensor", "quality", "assembly"],
        "Cybersecurity & InfoSec": ["cyber", "threat", "vulnerability", "breach", "malware", "soc", "siem"],
        "Government & Civic Tech": ["government", "civic", "voting", "permit", "regulation", "public"],
        "Education & Research": ["education", "university", "student", "research", "academic", "learning"],
        "Agriculture & Environment": ["agriculture", "farm", "crop", "climate", "weather", "environment"],
        "Telecommunications": ["telecom", "5g", "network", "bandwidth", "wireless", "spectrum"],
        "Space & Aerospace": ["space", "satellite", "orbit", "aerospace", "launch", "rocket"],
    }
    best_match = "Government & Civic Tech"
    best_score = 0
    for vertical, words in keywords.items():
        score = sum(1 for w in words if w in text_lower)
        if score > best_score:
            best_score = score
            best_match = vertical
    return best_match


def _generate_seed_challenges() -> list[dict]:
    """Generate initial seed challenges when engines have no recent data."""
    seeds = [
        {
            "title": "Real-Time Cyber Threat Intelligence Dashboard",
            "description": "Monitor CVE feeds, threat intel sources, and SIEM alerts with automated severity scoring and knowledge graph correlation",
            "vertical": "Cybersecurity & InfoSec",
            "source": "appforge_seed",
            "pain_points": json.dumps(["Manual CVE triage", "Alert fatigue from SIEM", "No cross-source correlation"]),
            "score": 0.92,
        },
        {
            "title": "Supply Chain Visibility & Risk Platform",
            "description": "Track multi-tier supplier networks with geospatial mapping, disruption detection, and alternative sourcing recommendations",
            "vertical": "Transportation & Logistics",
            "source": "appforge_seed",
            "pain_points": json.dumps(
                ["No visibility past tier-1 suppliers", "Disruption detected too late", "Manual risk assessment"]
            ),
            "score": 0.89,
        },
        {
            "title": "Clinical Trial Patient Flow Optimizer",
            "description": "Visualize patient enrollment pipelines, site performance, and protocol deviations with predictive analytics",
            "vertical": "Healthcare & Life Sciences",
            "source": "appforge_seed",
            "pain_points": json.dumps(
                ["Slow patient enrollment", "Site underperformance", "Protocol deviation detection lag"]
            ),
            "score": 0.87,
        },
        {
            "title": "Smart Grid Anomaly Detection Platform",
            "description": "Monitor power grid sensors in real-time with anomaly detection, outage prediction, and demand forecasting",
            "vertical": "Energy & Utilities",
            "source": "appforge_seed",
            "pain_points": json.dumps(["Undetected grid faults", "Reactive outage response", "Manual meter reading"]),
            "score": 0.85,
        },
        {
            "title": "Satellite Constellation Operations Dashboard",
            "description": "Track satellite positions, health telemetry, ground station contacts, and collision avoidance with orbital visualization",
            "vertical": "Space & Aerospace",
            "source": "appforge_seed",
            "pain_points": json.dumps(
                ["Manual conjunction screening", "Fragmented telemetry tools", "No fleet-wide health view"]
            ),
            "score": 0.83,
        },
        {
            "title": "Agricultural Precision Farming Platform",
            "description": "Integrate soil sensors, drone imagery, weather data, and crop models for field-level prescriptive analytics",
            "vertical": "Agriculture & Environment",
            "source": "appforge_seed",
            "pain_points": json.dumps(
                ["Over-application of inputs", "No field-level visibility", "Weather surprise losses"]
            ),
            "score": 0.81,
        },
        {
            "title": "Financial Fraud Detection & Investigation Platform",
            "description": "Real-time transaction monitoring with network analysis, anomaly scoring, and regulatory filing automation",
            "vertical": "Financial Services & Banking",
            "source": "appforge_seed",
            "pain_points": json.dumps(["High false positive rate", "Manual SAR filing", "No network visualization"]),
            "score": 0.80,
        },
    ]
    return seeds


def run(config: Dict[str, Any], trust) -> Dict[str, Any]:
    """Execute discover reflex."""
    # Pull from all three engines
    signals = _get_innovation_signals()
    gaps = _get_creative_gaps()
    research = _get_research_challenges()

    # Synthesize into challenges
    challenges = _synthesize_challenges(signals, gaps, research)

    # Store new challenges
    conn = get_connection()
    stored = 0
    try:
        for ch in challenges[:10]:  # Max 10 per run
            cid = generate_id("afg-ch")
            conn.execute(
                "INSERT OR IGNORE INTO appforge_challenges "
                "(challenge_id, vertical, title, description, pain_points, source, score, status, discovered_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, 'discovered', %s)",
                (
                    cid,
                    ch["vertical"],
                    ch["title"],
                    ch["description"],
                    ch.get("pain_points", "[]"),
                    ch["source"],
                    ch["score"],
                    utcnow_iso(),
                ),
            )
            stored += 1
        conn.commit()
    finally:
        conn.close()

    return {
        "success": True,
        "metric_value": float(stored),
        "details": {
            "innovation_signals": len(signals),
            "creative_gaps": len(gaps),
            "research_challenges": len(research),
            "challenges_stored": stored,
            "top_challenge": challenges[0]["title"] if challenges else None,
            "verticals_covered": list({c["vertical"] for c in challenges}),
        },
    }
