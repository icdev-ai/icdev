# CUI // SP-CTI
"""Telco/ISP RFP adapter for FCC programs: E-Rate (Form 470), BEAD, and RDOF.

Functions:
    parse_fcc_form470(form_data)          — Parse E-Rate Form 470, extract service categories,
                                            bandwidth requirements, and scoring criteria.
    generate_bead_compliance_matrix(rfp)  — Build BEAD (Broadband Equity, Access, Deployment)
                                            compliance matrix from NTIA program requirements.
    score_rdof_eligibility(network_data)  — Score eligibility for RDOF (Rural Digital Opportunity
                                            Fund) auction tiers based on speed and latency commitments.

Usage:
    python tools/govcon/telco_rfp_adapter.py --form470 --json
    python tools/govcon/telco_rfp_adapter.py --bead --json
    python tools/govcon/telco_rfp_adapter.py --rdof --json
"""

from __future__ import annotations
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger

import argparse
import json
from datetime import datetime, timezone
from typing import Any, Dict, List

logger = get_logger("govcon.telco_rfp")

# ── E-Rate Form 470 Service Categories ───────────────────────────────────────

_ERATE_CATEGORIES = {
    "C1": {
        "label": "Category 1 — Data Transmission / Internet",
        "services": [
            "WAN", "Internet Access", "Fiber Transport", "Ethernet WAN",
            "Dark Fiber", "Metro Ethernet", "Fixed Wireless",
        ],
        "discount_range": (20, 90),
        "eligible_entities": ["schools", "libraries"],
    },
    "C2": {
        "label": "Category 2 — Internal Connections",
        "services": [
            "Managed Internal Broadband Services", "Basic Maintenance",
            "Wireless Access Points", "Switches", "Routers",
        ],
        "discount_range": (20, 90),
        "budget_per_student_usd": 167,
        "budget_5yr_per_school": 25000,
        "eligible_entities": ["schools", "libraries"],
    },
}

# Bandwidth tiers expected in Form 470 (Mbps)
_BANDWIDTH_TIERS = [10, 25, 100, 200, 500, 1000, 10000]

# ── BEAD Program Requirements (NTIA) ─────────────────────────────────────────

_BEAD_REQUIREMENTS = {
    "speed_min_download_mbps": 100,
    "speed_min_upload_mbps": 20,
    "speed_gigabit_download_mbps": 1000,
    "speed_gigabit_upload_mbps": 1000,
    "latency_max_ms": 100,
    "technology_priority": [
        "Fiber-to-the-Premises (FTTP)",
        "Cable/HFC",
        "Fixed Wireless (≥100/20 Mbps, <100ms latency)",
        "Licensed Fixed Wireless",
        "Unlicensed Fixed Wireless",
        "Satellite (LEO only)",
    ],
    "reliability_target_pct": 99.9,
    "cybersecurity_required": True,
    "network_resiliency_required": True,
    "low_cost_option_required": True,
    "low_cost_option_max_usd_month": 30,
    "local_coordination_required": True,
    "affordability_plan_required": True,
    "workforce_development_required": True,
    "middle_mile_eligible": True,
    "aia_buy_american_required": True,
    "davis_bacon_required": True,
    "nepa_review_required": True,
    "matching_funds_pct": 25,
    "matching_funds_waivable": True,
    "eligible_entities": [
        "ISPs", "Utilities", "Electric Cooperatives",
        "Local Government", "Non-profits", "Tribal Entities",
    ],
}

_BEAD_COMPLIANCE_SECTIONS = [
    {
        "section": "Technical Requirements",
        "criteria": [
            ("Minimum 100/20 Mbps to all served locations", "speed_min"),
            ("Gigabit capability (1Gbps/1Gbps) preferred", "speed_gigabit"),
            ("Latency ≤100ms round-trip", "latency"),
            ("Technology selection justification", "technology"),
            ("Network reliability ≥99.9%", "reliability"),
        ],
    },
    {
        "section": "Financial & Matching",
        "criteria": [
            ("25% non-federal matching funds (may be waived)", "matching"),
            ("Project cost per location estimate", "cost_estimate"),
            ("Financial sustainability plan (5-year)", "financial_plan"),
            ("Letter of credit or performance bond", "financial_assurance"),
        ],
    },
    {
        "section": "Deployment & Coverage",
        "criteria": [
            ("Unserved location coverage plan (first priority)", "unserved_coverage"),
            ("Underserved location coverage plan (second priority)", "underserved_coverage"),
            ("Middle mile connectivity plan", "middle_mile"),
            ("Network map / coverage polygons (GIS)", "gis_data"),
        ],
    },
    {
        "section": "Affordability & Adoption",
            "criteria": [
            ("Low-cost service option ≤$30/month", "low_cost_option"),
            ("ACP / Lifeline enrollment support plan", "acp_support"),
            ("Digital literacy / adoption plan", "adoption_plan"),
        ],
    },
    {
        "section": "Regulatory & Compliance",
        "criteria": [
            ("Buy American / AIA compliance", "buy_american"),
            ("Davis-Bacon prevailing wages", "davis_bacon"),
            ("NEPA environmental review", "nepa"),
            ("Cybersecurity plan (NIST CSF)", "cybersecurity"),
            ("Supply chain risk management", "scrm"),
            ("CPNI / privacy compliance", "cpni"),
        ],
    },
    {
        "section": "Workforce & Local",
        "criteria": [
            ("Workforce development plan", "workforce"),
            ("Local hiring commitment", "local_hiring"),
            ("Community engagement / consultation", "community"),
            ("Tribal consultation (if applicable)", "tribal"),
        ],
    },
]

# ── RDOF Auction Tiers ────────────────────────────────────────────────────────

_RDOF_TIERS = {
    "Tier 1 — Gigabit": {
        "download_mbps": 1000,
        "upload_mbps": 500,
        "latency_max_ms": 100,
        "support_weight": 0.0,  # highest support (reference bid)
        "reserve_price_multiplier": 1.0,
        "deployment_deadline_years": 6,
        "notes": "Best score; highest support priority",
    },
    "Tier 2 — 500/500": {
        "download_mbps": 500,
        "upload_mbps": 500,
        "latency_max_ms": 100,
        "support_weight": 0.25,
        "reserve_price_multiplier": 0.75,
        "deployment_deadline_years": 6,
    },
    "Tier 3 — 100/20": {
        "download_mbps": 100,
        "upload_mbps": 20,
        "latency_max_ms": 100,
        "support_weight": 0.5,
        "reserve_price_multiplier": 0.5,
        "deployment_deadline_years": 6,
    },
    "Tier 4 — 25/3": {
        "download_mbps": 25,
        "upload_mbps": 3,
        "latency_max_ms": 100,
        "support_weight": 0.75,
        "reserve_price_multiplier": 0.25,
        "deployment_deadline_years": 6,
        "notes": "Lowest tier; eligible only in areas with no qualified bids",
    },
    "Tier 5 — Low Latency Bonus": {
        "download_mbps": 25,
        "upload_mbps": 3,
        "latency_max_ms": 25,
        "support_weight": 0.5,
        "reserve_price_multiplier": 0.4,
        "deployment_deadline_years": 6,
        "notes": "Low-latency bonus applies; prefer Tier 3+ if speed eligible",
    },
}


# ── Public API ────────────────────────────────────────────────────────────────

def parse_fcc_form470(form_data: Dict[str, Any]) -> Dict[str, Any]:
    """Parse an E-Rate Form 470 structure and extract key procurement fields.

    Args:
        form_data: Dict containing Form 470 fields. Recognized keys:
            billed_entity_name, billed_entity_number, form470_number,
            service_type (C1|C2), bandwidth_mbps, technology, contract_term_years,
            fiber_mileage, number_of_locations, state, contact_email,
            narrative (free-text description), requested_services (list of strings)

    Returns:
        Parsed form with scored requirements and procurement metadata.
    """
    entity = form_data.get("billed_entity_name", "Unknown Entity")
    form_num = form_data.get("form470_number", "")
    service_type = form_data.get("service_type", "C1").upper()
    bandwidth = int(form_data.get("bandwidth_mbps", 0))
    technology = form_data.get("technology", "")
    term_years = int(form_data.get("contract_term_years", 1))
    locations = int(form_data.get("number_of_locations", 1))
    state = form_data.get("state", "")
    narrative = form_data.get("narrative", "")
    requested = form_data.get("requested_services", [])

    # Classify bandwidth requirement
    bw_tier = "unknown"
    for t in _BANDWIDTH_TIERS:
        if bandwidth >= t:
            bw_tier = f">={t} Mbps"

    # Detect service keywords in narrative
    detected_services: List[str] = []
    cat_info = _ERATE_CATEGORIES.get(service_type, _ERATE_CATEGORIES["C1"])
    for svc in cat_info["services"]:
        if svc.lower() in narrative.lower() or any(svc.lower() in r.lower() for r in requested):
            detected_services.append(svc)

    # Scoring: rough E-Rate RFP complexity score (1–10)
    complexity = 0
    complexity += min(4, locations // 10)
    complexity += 2 if bandwidth >= 1000 else 1 if bandwidth >= 100 else 0
    complexity += 2 if term_years >= 3 else 1 if term_years >= 2 else 0
    complexity += 2 if "dark fiber" in narrative.lower() or "IRU" in narrative else 0
    complexity = min(10, complexity)

    discount_min, discount_max = cat_info["discount_range"]

    return {
        "form470_number": form_num,
        "billed_entity": entity,
        "state": state,
        "service_category": service_type,
        "category_label": cat_info["label"],
        "bandwidth_requirement_mbps": bandwidth,
        "bandwidth_tier": bw_tier,
        "technology_requested": technology,
        "contract_term_years": term_years,
        "locations": locations,
        "detected_services": detected_services,
        "requested_services": requested,
        "eligible_discount_range_pct": f"{discount_min}–{discount_max}%",
        "rfp_complexity_score": complexity,
        "procurement_notes": _form470_procurement_notes(service_type, bandwidth, locations, term_years),
        "response_requirements": _form470_response_requirements(service_type, technology),
        "parsed_at": datetime.now(timezone.utc).isoformat(),
    }


def _form470_procurement_notes(service_type: str, bandwidth: int, locations: int, term: int) -> List[str]:
    notes = []
    if service_type == "C1" and bandwidth >= 1000:
        notes.append("Gigabit WAN — consider fiber IRU or dark fiber pricing")
    if service_type == "C1" and locations > 50:
        notes.append("Large multi-site deployment — aggregate pricing and SLA tier critical")
    if term >= 3:
        notes.append(f"{term}-year term — include escalation clause and technology refresh rights")
    if service_type == "C2":
        notes.append("Category 2 — budget cap per student applies; document device counts")
    return notes


def _form470_response_requirements(service_type: str, technology: str) -> List[str]:
    reqs = [
        "Pricing must be per-location (not aggregated)",
        "SLA with uptime ≥99.9% and mean repair time ≤4 hours",
        "SPIN/SPI registration verification",
        "Certificate of independent price determination",
        "Confirmation of non-debarment (SAM.gov)",
    ]
    if service_type == "C1":
        reqs.append("Network diagram showing path diversity")
        reqs.append("IPv6 dual-stack capability statement")
    if "fiber" in technology.lower() or "dark" in technology.lower():
        reqs.append("Fiber route map with splice diagram")
        reqs.append("OTDR test results or performance guarantee")
    return reqs


def generate_bead_compliance_matrix(rfp: Dict[str, Any]) -> Dict[str, Any]:
    """Generate a BEAD (Broadband Equity, Access, Deployment) compliance matrix.

    Args:
        rfp: Dict with operator's proposed solution. Recognized keys:
            download_mbps, upload_mbps, latency_ms, technology,
            reliability_pct, low_cost_price_usd, matching_funds_pct,
            buy_american (bool), davis_bacon (bool), cybersecurity_framework,
            workforce_plan (bool), community_engagement (bool),
            coverage_locations_unserved, coverage_locations_underserved

    Returns:
        compliance_matrix: list of section → criteria → status/gap dicts.
        overall_score: percentage of criteria met.
        gaps: list of unfulfilled requirements.
    """
    dl = float(rfp.get("download_mbps", 0))
    ul = float(rfp.get("upload_mbps", 0))
    latency = float(rfp.get("latency_ms", 999))
    reliability = float(rfp.get("reliability_pct", 0))
    low_cost = float(rfp.get("low_cost_price_usd", 999))
    matching = float(rfp.get("matching_funds_pct", 0))
    buy_american = bool(rfp.get("buy_american", False))
    davis_bacon = bool(rfp.get("davis_bacon", False))
    cybersec = rfp.get("cybersecurity_framework", "")
    workforce = bool(rfp.get("workforce_plan", False))
    community = bool(rfp.get("community_engagement", False))

    checks = [
        ("Technical", "Download ≥100 Mbps", dl >= 100, f"Proposed: {dl} Mbps"),
        ("Technical", "Upload ≥20 Mbps", ul >= 20, f"Proposed: {ul} Mbps"),
        ("Technical", "Latency ≤100ms", latency <= 100, f"Proposed: {latency}ms"),
        ("Technical", "Reliability ≥99.9%", reliability >= 99.9, f"Proposed: {reliability}%"),
        ("Technical", "Gigabit capable (1000/1000)", dl >= 1000 and ul >= 1000, f"DL:{dl}/UL:{ul}"),
        ("Financial", "25% matching funds", matching >= 25, f"Proposed: {matching}%"),
        ("Affordability", "Low-cost option ≤$30/month", low_cost <= 30, f"Proposed: ${low_cost}"),
        ("Compliance", "Buy American / AIA", buy_american, "Required by BEAD statute"),
        ("Compliance", "Davis-Bacon prevailing wages", davis_bacon, "Required for federally funded construction"),
        ("Compliance", "Cybersecurity plan (NIST CSF)", bool(cybersec), f"Framework: {cybersec or 'not specified'}"),
        ("Workforce", "Workforce development plan", workforce, "Required for BEAD applications"),
        ("Community", "Community engagement / consultation", community, "Required for BEAD applications"),
    ]

    matrix: List[Dict[str, Any]] = []
    gaps: List[str] = []
    met = 0

    for section, criterion, passed, detail in checks:
        status = "PASS" if passed else "GAP"
        if not passed:
            gaps.append(f"[{section}] {criterion} — {detail}")
        else:
            met += 1
        matrix.append({
            "section": section,
            "criterion": criterion,
            "status": status,
            "detail": detail,
        })

    score_pct = round(met / len(checks) * 100, 1) if checks else 0
    eligible = score_pct >= 75 and dl >= 100 and ul >= 20 and latency <= 100

    return {
        "program": "BEAD (Broadband Equity, Access, Deployment) — NTIA",
        "compliance_matrix": matrix,
        "overall_score_pct": score_pct,
        "criteria_met": met,
        "criteria_total": len(checks),
        "eligible": eligible,
        "gaps": gaps,
        "recommendation": _bead_recommendation(eligible, gaps),
        "requirements_reference": _BEAD_REQUIREMENTS,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _bead_recommendation(eligible: bool, gaps: List[str]) -> str:
    if eligible:
        return "Application appears BEAD-eligible. Address any gaps before submission."
    critical = [g for g in gaps if any(k in g for k in ["Download", "Upload", "Latency", "Buy American"])]
    if critical:
        return f"NOT eligible — {len(critical)} critical gap(s) must be resolved: {'; '.join(critical[:2])}"
    return f"Marginally ineligible — {len(gaps)} gap(s) to address before applying."


def score_rdof_eligibility(network_data: Dict[str, Any]) -> Dict[str, Any]:
    """Score RDOF auction eligibility and recommend the optimal bid tier.

    Args:
        network_data: Dict with network capability. Recognized keys:
            download_mbps, upload_mbps, latency_ms, technology,
            voice_capable (bool), route_km (for fiber builds),
            cost_per_location_usd, eligible_locations (CAF-II unserved count)

    Returns:
        Tier eligibility list, recommended tier, support estimate,
        and deployment requirements.
    """
    dl = float(network_data.get("download_mbps", 0))
    ul = float(network_data.get("upload_mbps", 0))
    latency = float(network_data.get("latency_ms", 999))
    voice = bool(network_data.get("voice_capable", False))
    locations = int(network_data.get("eligible_locations", 0))
    cost_per_loc = float(network_data.get("cost_per_location_usd", 0))

    eligible_tiers = []
    for tier_name, tier in _RDOF_TIERS.items():
        speed_ok = dl >= tier["download_mbps"] and ul >= tier["upload_mbps"]
        latency_ok = latency <= tier["latency_max_ms"]
        if speed_ok and latency_ok:
            eligible_tiers.append({
                "tier": tier_name,
                "download_mbps": tier["download_mbps"],
                "upload_mbps": tier["upload_mbps"],
                "latency_max_ms": tier["latency_max_ms"],
                "support_weight": tier["support_weight"],
                "notes": tier.get("notes", ""),
            })

    # Recommend highest eligible tier (lowest support_weight = highest support)
    recommended = None
    if eligible_tiers:
        recommended = min(eligible_tiers, key=lambda t: t["support_weight"])

    # Estimated total support (simplified: higher tier → higher weight on reserve price)
    estimated_support_usd = 0
    if recommended and locations and cost_per_loc:
        total_cost = locations * cost_per_loc
        weight = recommended["support_weight"]
        estimated_support_usd = round(total_cost * (1 - weight * 0.5), 0)

    deployment_reqs = [
        "40% of locations served within 2 years",
        "60% within 4 years",
        "100% within 6 years",
        "Annual progress reports to FCC",
        "Buildout in areas with no competitive overlap",
    ]
    if voice:
        deployment_reqs.append("Voice-grade service (VoIP or traditional) required at all locations")

    return {
        "program": "RDOF (Rural Digital Opportunity Fund) — FCC",
        "network_capability": {
            "download_mbps": dl,
            "upload_mbps": ul,
            "latency_ms": latency,
            "voice_capable": voice,
        },
        "eligible_tiers": eligible_tiers,
        "recommended_tier": recommended["tier"] if recommended else "No eligible tier",
        "eligible": bool(eligible_tiers),
        "estimated_support_usd": estimated_support_usd,
        "eligible_locations": locations,
        "cost_per_location_usd": cost_per_loc,
        "deployment_requirements": deployment_reqs,
        "auction_notes": (
            "RDOF Phase II is pending. Ensure CAF-II census block eligibility "
            "and avoid areas with existing ≥25/3 Mbps service."
        ),
        "scored_at": datetime.now(timezone.utc).isoformat(),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli() -> None:
    parser = argparse.ArgumentParser(description="Telco RFP Adapter — FCC Form 470, BEAD, RDOF")
    parser.add_argument("--form470", action="store_true", help="Parse sample Form 470")
    parser.add_argument("--bead", action="store_true", help="Generate BEAD compliance matrix (sample)")
    parser.add_argument("--rdof", action="store_true", help="Score RDOF eligibility (sample)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    result: Any = {}

    if args.form470:
        sample = {
            "billed_entity_name": "Metro School District",
            "form470_number": "221001234",
            "service_type": "C1",
            "bandwidth_mbps": 10000,
            "technology": "Fiber",
            "contract_term_years": 3,
            "number_of_locations": 45,
            "state": "VA",
            "narrative": "Requesting dark fiber IRU for district-wide WAN upgrade",
        }
        result = parse_fcc_form470(sample)

    elif args.bead:
        sample = {
            "download_mbps": 1000,
            "upload_mbps": 1000,
            "latency_ms": 5,
            "reliability_pct": 99.95,
            "low_cost_price_usd": 25,
            "matching_funds_pct": 25,
            "buy_american": True,
            "davis_bacon": True,
            "cybersecurity_framework": "NIST CSF 2.0",
            "workforce_plan": True,
            "community_engagement": True,
        }
        result = generate_bead_compliance_matrix(sample)

    elif args.rdof:
        sample = {
            "download_mbps": 1000,
            "upload_mbps": 500,
            "latency_ms": 10,
            "voice_capable": True,
            "eligible_locations": 1200,
            "cost_per_location_usd": 3500,
        }
        result = score_rdof_eligibility(sample)

    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    _cli()
