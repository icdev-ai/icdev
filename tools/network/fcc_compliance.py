# CUI // SP-CTI
"""FCC regulatory compliance tools for ISPs and telecommunications carriers.

Covers:
    CALEA (Communications Assistance for Law Enforcement Act) — lawful intercept readiness
    Part 36 (Separations) — jurisdictional cost allocation for interstate/intrastate traffic
    NANP (North American Numbering Plan) — number inventory and porting compliance
    E-911 — Enhanced 911 location accuracy and callback capability

Usage:
    python tools/network/fcc_compliance.py --calea --json
    python tools/network/fcc_compliance.py --part36 --json
    python tools/network/fcc_compliance.py --nanp --json
    python tools/network/fcc_compliance.py --e911 --json
    python tools/network/fcc_compliance.py --all --json
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
from typing import Any, Dict, Optional

logger = get_logger("network.fcc_compliance")

# ── CALEA Categories ──────────────────────────────────────────────────────────

_CALEA_CATEGORIES = [
    {
        "category": "Lawful Intercept Infrastructure",
        "requirements": [
            {
                "id": "CALEA-01",
                "requirement": "CALEA-compliant mediation device or LI gateway",
                "mandatory": True,
                "reference": "47 U.S.C. § 1002",
            },
            {
                "id": "CALEA-02",
                "requirement": "Content of Communications (CC) delivery capability",
                "mandatory": True,
                "reference": "CALEA Technical Standards (T1.678, ATIS-1000678)",
            },
            {
                "id": "CALEA-03",
                "requirement": "Call Identifying Information (CII) delivery capability",
                "mandatory": True,
                "reference": "47 U.S.C. § 1002(a)(2)",
            },
            {
                "id": "CALEA-04",
                "requirement": "Pen register / trap-and-trace data delivery",
                "mandatory": True,
                "reference": "18 U.S.C. § 3121",
            },
            {
                "id": "CALEA-05",
                "requirement": "Simultaneous intercept capacity ≥ court orders on hand",
                "mandatory": True,
                "reference": "FCC 06-56",
            },
        ],
    },
    {
        "category": "Telecommunications Carrier Obligations",
        "requirements": [
            {
                "id": "CALEA-06",
                "requirement": "Determination: is carrier a 'telecommunications carrier' under CALEA?",
                "mandatory": True,
                "reference": "47 U.S.C. § 1001(8)",
            },
            {
                "id": "CALEA-07",
                "requirement": "VOIP provider covered under FCC 05-153 (if offering interconnected VoIP)",
                "mandatory": True,
                "reference": "FCC 05-153",
            },
            {
                "id": "CALEA-08",
                "requirement": "FBI/DOJ CALEA compliance certification submitted",
                "mandatory": True,
                "reference": "47 U.S.C. § 1008(b)",
            },
            {
                "id": "CALEA-09",
                "requirement": "Trusted third-party (TTP) arrangement documented (if used)",
                "mandatory": False,
                "reference": "FCC Order DA 05-543",
            },
        ],
    },
    {
        "category": "Packet-Mode / IP Network Requirements",
        "requirements": [
            {
                "id": "CALEA-10",
                "requirement": "Broadband Internet Access Service (BIAS) LI capability",
                "mandatory": True,
                "reference": "FCC 05-153, extended to BIAS providers",
            },
            {
                "id": "CALEA-11",
                "requirement": "Deep packet inspection / flow-level isolation for LI delivery",
                "mandatory": True,
                "reference": "ATIS-1000678 Packet Mode",
            },
            {
                "id": "CALEA-12",
                "requirement": "LI delivery encrypted to law enforcement collection function",
                "mandatory": True,
                "reference": "ATIS-1000678 § 7",
            },
            {
                "id": "CALEA-13",
                "requirement": "No disclosure of intercept to subject (non-disclosure obligation)",
                "mandatory": True,
                "reference": "18 U.S.C. § 2511",
            },
        ],
    },
    {
        "category": "Security & Access Controls",
        "requirements": [
            {
                "id": "CALEA-14",
                "requirement": "Physical security controls on LI equipment",
                "mandatory": True,
                "reference": "CALEA Technical Standards",
            },
            {
                "id": "CALEA-15",
                "requirement": "Audit log of all intercept activations and deliveries (append-only)",
                "mandatory": True,
                "reference": "DOJ/FBI CALEA Implementation Guide",
            },
            {
                "id": "CALEA-16",
                "requirement": "Background-checked personnel with access to LI systems",
                "mandatory": True,
                "reference": "DOJ/FBI CALEA Implementation Guide",
            },
            {
                "id": "CALEA-17",
                "requirement": "Incident response plan for LI system compromise",
                "mandatory": False,
                "reference": "Best practice",
            },
        ],
    },
]


def calea_checklist(carrier_profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Generate a CALEA compliance checklist for a telecommunications carrier.

    Args:
        carrier_profile: Optional dict with carrier capabilities. Recognized keys:
            is_interconnected_voip (bool), offers_broadband (bool),
            has_li_gateway (bool), has_ttp (bool), state (str), annual_revenue_usd

    Returns:
        Full checklist with mandatory/optional status, self-assessment fields,
        and compliance gap summary.
    """
    profile = carrier_profile or {}
    is_voip = bool(profile.get("is_interconnected_voip", False))
    offers_broadband = bool(profile.get("offers_broadband", False))

    checklist = []
    mandatory_count = 0
    applicable_count = 0

    for cat in _CALEA_CATEGORIES:
        for req in cat["requirements"]:
            # CALEA-07 only applies if interconnected VoIP
            if req["id"] == "CALEA-07" and not is_voip:
                continue
            # CALEA-10/11/12 only apply if broadband provider
            if req["id"] in ("CALEA-10", "CALEA-11", "CALEA-12") and not offers_broadband:
                continue
            applicable_count += 1
            if req["mandatory"]:
                mandatory_count += 1
            checklist.append({
                "id": req["id"],
                "category": cat["category"],
                "requirement": req["requirement"],
                "mandatory": req["mandatory"],
                "reference": req["reference"],
                "self_assessment": "pending",
            })

    gaps = [c for c in checklist if c["mandatory"] and c["self_assessment"] == "pending"]

    return {
        "framework": "CALEA — Communications Assistance for Law Enforcement Act",
        "carrier_type": _calea_carrier_type(is_voip, offers_broadband),
        "checklist": checklist,
        "mandatory_items": mandatory_count,
        "applicable_items": applicable_count,
        "open_gaps": len(gaps),
        "compliance_deadline": "Ongoing — no grace period for covered carriers",
        "enforcement_agency": "DOJ / FBI (primary); FCC (secondary)",
        "penalty_max": "$10,000/day for non-compliance (47 U.S.C. § 1007)",
        "assessed_at": datetime.now(timezone.utc).isoformat(),
    }


def _calea_carrier_type(voip: bool, broadband: bool) -> str:
    parts = ["Telecommunications Carrier"]
    if voip:
        parts.append("Interconnected VoIP Provider")
    if broadband:
        parts.append("Broadband Internet Access Service (BIAS) Provider")
    return " / ".join(parts)


# ── Part 36 Separations ───────────────────────────────────────────────────────

def part36_assessment(traffic_data: Dict[str, Any]) -> Dict[str, Any]:
    """Assess FCC Part 36 jurisdictional separations for a carrier.

    Part 36 governs cost allocation between interstate and intrastate traffic
    for carriers that are subject to FCC oversight (primarily ILECs and CLECs
    with interstate access charges).

    Args:
        traffic_data: Dict with traffic and cost data. Recognized keys:
            total_minutes_of_use (long distance), interstate_minutes_pct (0-100),
            intrastate_minutes_pct (0-100), total_network_cost_usd,
            carrier_type (ILEC|CLEC|VoIP|Wireless), state,
            access_lines (subscriber lines count)

    Returns:
        Jurisdictional allocation, Interstate Access charges estimate,
        and compliance notes.
    """
    total_mou = float(traffic_data.get("total_minutes_of_use", 0))
    interstate_pct = float(traffic_data.get("interstate_minutes_pct", 50))
    intrastate_pct = float(traffic_data.get("intrastate_minutes_pct", 50))
    total_cost = float(traffic_data.get("total_network_cost_usd", 0))
    carrier_type = traffic_data.get("carrier_type", "CLEC")
    state = traffic_data.get("state", "")
    access_lines = int(traffic_data.get("access_lines", 0))

    # Normalize percentages
    if interstate_pct + intrastate_pct != 100:
        total = interstate_pct + intrastate_pct
        if total > 0:
            interstate_pct = interstate_pct / total * 100
            intrastate_pct = intrastate_pct / total * 100

    interstate_mou = total_mou * (interstate_pct / 100)
    intrastate_mou = total_mou * (intrastate_pct / 100)

    # Cost allocation (simplified Part 36 category 1: jurisdictional)
    interstate_cost = round(total_cost * (interstate_pct / 100), 2)
    intrastate_cost = round(total_cost * (intrastate_pct / 100), 2)

    # Common Line (CL) charge for ILECs/CLECs with access lines
    # FCC Part 69 access charge — approximate CLEC rate ~$0.0007/min
    access_charge_per_min = 0.0007
    interstate_access_revenue = round(interstate_mou * access_charge_per_min, 2) if access_lines else 0

    compliance_items = [
        {
            "item": "Annual Part 36 Separations Study",
            "applicable": carrier_type in ("ILEC", "CLEC"),
            "frequency": "Annual (FCC Form 492)",
            "notes": "File with state PUC and submit to NECA if participating",
        },
        {
            "item": "NECA Traffic Study",
            "applicable": carrier_type == "ILEC",
            "frequency": "Annual",
            "notes": "National Exchange Carrier Association pool participation",
        },
        {
            "item": "Interstate Access Charge Tariff",
            "applicable": carrier_type in ("ILEC", "CLEC"),
            "frequency": "As needed",
            "notes": "FCC Part 69; CLECs may use default rates or file tariff",
        },
        {
            "item": "USF Contribution (Form 499-A / 499-Q)",
            "applicable": True,
            "frequency": "Quarterly/Annual",
            "notes": "All carriers with interstate revenues contribute to Universal Service Fund",
        },
        {
            "item": "Connect America Fund (CAF) Reporting",
            "applicable": carrier_type == "ILEC",
            "frequency": "Quarterly",
            "notes": "If receiving CAF support; report broadband deployment milestones",
        },
    ]

    return {
        "framework": "FCC Part 36 — Jurisdictional Separations",
        "carrier_type": carrier_type,
        "state": state,
        "traffic_analysis": {
            "total_minutes_of_use": total_mou,
            "interstate_minutes": round(interstate_mou, 0),
            "intrastate_minutes": round(intrastate_mou, 0),
            "interstate_pct": round(interstate_pct, 1),
            "intrastate_pct": round(intrastate_pct, 1),
        },
        "cost_allocation": {
            "total_network_cost_usd": total_cost,
            "interstate_cost_usd": interstate_cost,
            "intrastate_cost_usd": intrastate_cost,
            "interstate_access_revenue_estimate_usd": interstate_access_revenue,
        },
        "compliance_requirements": compliance_items,
        "applicable_requirements": [c for c in compliance_items if c["applicable"]],
        "notes": (
            "Part 36 separations methodology is under FCC forbearance for most carriers. "
            "CLECs with limited interstate access traffic may qualify for simplified reporting. "
            "Consult regulatory counsel for jurisdiction-specific requirements."
        ),
        "assessed_at": datetime.now(timezone.utc).isoformat(),
    }


# ── NANP Number Inventory ─────────────────────────────────────────────────────

_NANP_NUMBER_TYPES = {
    "DID": "Direct Inward Dial — assigned to subscribers",
    "Toll-Free": "8XX numbers (800/833/844/855/866/877/888)",
    "Premium": "900-series premium-rate",
    "Local": "Local POTS numbers (LATA-assigned)",
    "NXX": "NPA-NXX central office code blocks",
    "PRI": "PRI/T1 trunks with DID ranges",
}

_NANP_COMPLIANCE_RULES = [
    {
        "rule": "Number Aging (porting out)",
        "requirement": "Released numbers must age 90 days before reassignment",
        "reference": "FCC 96-286; NANPA Numbering Resource Utilization",
    },
    {
        "rule": "Utilization Threshold",
        "requirement": "Carriers must use ≥60% of assigned number blocks (10K blocks)",
        "reference": "FCC NUA rules; 47 CFR § 52.15",
    },
    {
        "rule": "Porting (LNP)",
        "requirement": "Port-out requests must be completed within 1 business day",
        "reference": "47 CFR § 52.26; FCC 07-70",
    },
    {
        "rule": "Spare Number Reporting",
        "requirement": "Report number utilization to NANPA quarterly (Form 502)",
        "reference": "47 CFR § 52.15(f)",
    },
    {
        "rule": "Number Pooling",
        "requirement": "1,000-block number pooling required in top-100 MSAs",
        "reference": "47 CFR § 52.15(g)",
    },
    {
        "rule": "CNAM Database",
        "requirement": "Maintain accurate CNAM records for all assigned DIDs",
        "reference": "FCC Truth in Caller ID Act enforcement",
    },
    {
        "rule": "STIR/SHAKEN",
        "requirement": "Implement STIR/SHAKEN caller ID authentication",
        "reference": "47 U.S.C. § 227b; FCC 20-48",
    },
    {
        "rule": "Robocall Mitigation",
        "requirement": "Submit Robocall Mitigation Database certification to FCC",
        "reference": "FCC 20-172; 47 CFR § 64.6305",
    },
]


def nanp_number_inventory(inventory: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Assess NANP number inventory compliance and utilization.

    Args:
        inventory: Optional dict with number inventory data. Recognized keys:
            npa_nxx_blocks (list of {npa, nxx, assigned, used, aging} dicts),
            total_dids_assigned, total_dids_active, porting_requests_pending,
            stir_shaken_implemented (bool), cnam_registered (bool),
            robocall_mitigation_filed (bool)

    Returns:
        Compliance assessment, utilization metrics, and regulatory action items.
    """
    inv = inventory or {}
    blocks = inv.get("npa_nxx_blocks", [])
    total_assigned = int(inv.get("total_dids_assigned", 0))
    total_active = int(inv.get("total_dids_active", 0))
    porting_pending = int(inv.get("porting_requests_pending", 0))
    stir_shaken = bool(inv.get("stir_shaken_implemented", False))
    cnam_registered = bool(inv.get("cnam_registered", False))
    robocall_filed = bool(inv.get("robocall_mitigation_filed", False))

    utilization_pct = round(total_active / total_assigned * 100, 1) if total_assigned else 0
    utilization_ok = utilization_pct >= 60

    # Assess each block
    block_assessments = []
    under_utilized_blocks = 0
    for block in blocks:
        npa = block.get("npa", "")
        nxx = block.get("nxx", "")
        assigned = int(block.get("assigned", 0))
        used = int(block.get("used", 0))
        utilization = round(used / assigned * 100, 1) if assigned else 0
        ok = utilization >= 60
        if not ok:
            under_utilized_blocks += 1
        block_assessments.append({
            "npa_nxx": f"{npa}-{nxx}",
            "assigned": assigned,
            "active": used,
            "utilization_pct": utilization,
            "compliant": ok,
            "action": "None" if ok else "Reclaim or activate unused numbers",
        })

    action_items = []
    if not stir_shaken:
        action_items.append({"priority": "HIGH", "action": "Implement STIR/SHAKEN (FCC 20-48)"})
    if not cnam_registered:
        action_items.append({"priority": "MEDIUM", "action": "Register all DIDs in CNAM database"})
    if not robocall_filed:
        action_items.append({"priority": "HIGH", "action": "File Robocall Mitigation Database certification"})
    if not utilization_ok and total_assigned:
        action_items.append({"priority": "HIGH", "action": f"Improve number utilization (current: {utilization_pct}%; required: ≥60%)"})
    if porting_pending > 0:
        action_items.append({"priority": "HIGH", "action": f"{porting_pending} LNP port(s) pending — complete within 1 business day"})
    if under_utilized_blocks:
        action_items.append({"priority": "MEDIUM", "action": f"{under_utilized_blocks} NXX block(s) below 60% utilization — file return or activation plan with NANPA"})

    return {
        "framework": "NANP — North American Numbering Plan Administration",
        "number_types": _NANP_NUMBER_TYPES,
        "inventory_summary": {
            "total_dids_assigned": total_assigned,
            "total_dids_active": total_active,
            "utilization_pct": utilization_pct,
            "utilization_compliant": utilization_ok,
            "porting_requests_pending": porting_pending,
            "stir_shaken_implemented": stir_shaken,
            "cnam_registered": cnam_registered,
            "robocall_mitigation_filed": robocall_filed,
        },
        "npa_nxx_assessments": block_assessments,
        "compliance_rules": _NANP_COMPLIANCE_RULES,
        "action_items": action_items,
        "overall_compliant": len(action_items) == 0,
        "assessed_at": datetime.now(timezone.utc).isoformat(),
    }


# ── E-911 Capability Check ────────────────────────────────────────────────────

_E911_REQUIREMENTS = [
    {
        "id": "E911-01",
        "requirement": "Basic 911 — route 911 calls to PSAP",
        "mandatory": True,
        "applies_to": ["POTS", "VoIP", "Wireless", "Broadband"],
        "reference": "47 CFR § 9.3",
    },
    {
        "id": "E911-02",
        "requirement": "Automatic Number Identification (ANI) delivery to PSAP",
        "mandatory": True,
        "applies_to": ["POTS", "VoIP", "Wireless"],
        "reference": "47 CFR § 9.5",
    },
    {
        "id": "E911-03",
        "requirement": "Automatic Location Identification (ALI) delivery",
        "mandatory": True,
        "applies_to": ["POTS", "Wireless"],
        "reference": "47 CFR § 9.10(a)",
    },
    {
        "id": "E911-04",
        "requirement": "VoIP dispatchable location delivery (interconnected VoIP)",
        "mandatory": True,
        "applies_to": ["VoIP"],
        "reference": "47 CFR § 9.11; FCC 19-40 Kari's Law / RAY BAUM'S Act",
    },
    {
        "id": "E911-05",
        "requirement": "Multi-line telephone system (MLTS) direct 911 dialing (no prefix)",
        "mandatory": True,
        "applies_to": ["VoIP", "Enterprise"],
        "reference": "Kari's Law — 47 U.S.C. § 1471",
    },
    {
        "id": "E911-06",
        "requirement": "MLTS 911 notification to on-site security / front desk",
        "mandatory": True,
        "applies_to": ["VoIP", "Enterprise"],
        "reference": "Kari's Law — 47 U.S.C. § 1471(b)",
    },
    {
        "id": "E911-07",
        "requirement": "Location database / ESRP integration (GIS-based routing)",
        "mandatory": True,
        "applies_to": ["VoIP"],
        "reference": "NG911 transition; FCC 19-40",
    },
    {
        "id": "E911-08",
        "requirement": "Wireless E-911 Phase II location accuracy (horizontal: ≤50m/150m)",
        "mandatory": True,
        "applies_to": ["Wireless"],
        "reference": "47 CFR § 9.10(i); FCC Indoor Location Accuracy",
    },
    {
        "id": "E911-09",
        "requirement": "z-axis (vertical/floor) location for indoor 911 (Z-axis NPRM)",
        "mandatory": False,
        "applies_to": ["Wireless"],
        "reference": "FCC 21-14; FCC-DA-22-157 (rollout schedule)",
    },
    {
        "id": "E911-10",
        "requirement": "NG911 IP-based 911 interconnection readiness",
        "mandatory": False,
        "applies_to": ["POTS", "VoIP", "Wireless"],
        "reference": "NG911 transition guidance; FCC 20-11",
    },
    {
        "id": "E911-11",
        "requirement": "Annual E-911 test with PSAP (call and location verification)",
        "mandatory": True,
        "applies_to": ["VoIP", "POTS"],
        "reference": "NENA best practice; state PUC requirements vary",
    },
    {
        "id": "E911-12",
        "requirement": "Callback capability from PSAP to 911 caller",
        "mandatory": True,
        "applies_to": ["VoIP", "POTS", "Wireless"],
        "reference": "47 CFR § 9.3",
    },
]


def e911_capability_check(network_profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Assess E-911 capability compliance for a carrier.

    Args:
        network_profile: Optional dict with network capabilities. Recognized keys:
            service_types (list: POTS, VoIP, Wireless, Broadband, Enterprise),
            has_ani_delivery (bool), has_ali_delivery (bool),
            has_dispatchable_location (bool), has_mlts_direct_dial (bool),
            has_mlts_notification (bool), has_location_database (bool),
            has_ng911_readiness (bool), state, carrier_name

    Returns:
        Applicability-filtered checklist, gap list, and compliance status.
    """
    profile = network_profile or {}
    service_types = set(profile.get("service_types", ["VoIP"]))
    carrier = profile.get("carrier_name", "Unknown Carrier")
    state = profile.get("state", "")

    checklist = []
    gap_count = 0

    for req in _E911_REQUIREMENTS:
        # Filter by applicability
        applicable = any(st in req["applies_to"] for st in service_types) or "All" in req["applies_to"]
        if not applicable:
            continue

        # Map capability fields to requirement IDs
        capability_map = {
            "E911-02": "has_ani_delivery",
            "E911-03": "has_ali_delivery",
            "E911-04": "has_dispatchable_location",
            "E911-05": "has_mlts_direct_dial",
            "E911-06": "has_mlts_notification",
            "E911-07": "has_location_database",
            "E911-10": "has_ng911_readiness",
        }
        cap_key = capability_map.get(req["id"])
        if cap_key:
            implemented = bool(profile.get(cap_key, False))
        else:
            implemented = None  # unknown / not self-assessed

        status = "IMPLEMENTED" if implemented is True else ("GAP" if implemented is False else "PENDING")
        if req["mandatory"] and status == "GAP":
            gap_count += 1

        checklist.append({
            "id": req["id"],
            "requirement": req["requirement"],
            "mandatory": req["mandatory"],
            "applies_to": req["applies_to"],
            "reference": req["reference"],
            "status": status,
        })

    return {
        "framework": "E-911 — Enhanced 911 Capability Requirements (FCC)",
        "carrier": carrier,
        "state": state,
        "service_types": list(service_types),
        "checklist": checklist,
        "applicable_items": len(checklist),
        "mandatory_gaps": gap_count,
        "overall_compliant": gap_count == 0,
        "enforcement_note": (
            "FCC can issue forfeitures up to $10,000/day for E-911 non-compliance. "
            "State PUC requirements may be stricter than federal minimums. "
            "PSAP testing is mandatory in most jurisdictions."
        ),
        "assessed_at": datetime.now(timezone.utc).isoformat(),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli() -> None:
    parser = argparse.ArgumentParser(description="FCC Compliance Tools — CALEA, Part 36, NANP, E-911")
    parser.add_argument("--calea", action="store_true")
    parser.add_argument("--part36", action="store_true")
    parser.add_argument("--nanp", action="store_true")
    parser.add_argument("--e911", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    results: Dict[str, Any] = {}

    if args.calea or args.all:
        results["calea"] = calea_checklist({
            "is_interconnected_voip": True,
            "offers_broadband": True,
        })
    if args.part36 or args.all:
        results["part36"] = part36_assessment({
            "total_minutes_of_use": 5_000_000,
            "interstate_minutes_pct": 35,
            "intrastate_minutes_pct": 65,
            "total_network_cost_usd": 2_500_000,
            "carrier_type": "CLEC",
            "state": "VA",
            "access_lines": 25000,
        })
    if args.nanp or args.all:
        results["nanp"] = nanp_number_inventory({
            "total_dids_assigned": 50000,
            "total_dids_active": 38000,
            "stir_shaken_implemented": True,
            "cnam_registered": True,
            "robocall_mitigation_filed": False,
        })
    if args.e911 or args.all:
        results["e911"] = e911_capability_check({
            "service_types": ["VoIP", "Wireless"],
            "carrier_name": "Sample ISP",
            "state": "VA",
            "has_ani_delivery": True,
            "has_dispatchable_location": True,
            "has_mlts_direct_dial": False,
            "has_mlts_notification": False,
        })

    if not results:
        parser.print_help()
        return

    output = results if len(results) > 1 else next(iter(results.values()))
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    _cli()
