# CUI // SP-CTI
"""Transit pricing benchmark dataset and ROI analysis for peering vs. transit decisions.

Functions:
    benchmark_transit_cost(region, speed_gbps, commit_pct)  — Look up transit price benchmarks
                                                               by region and port speed.
    peering_vs_transit_roi(peers, transit_costs, capex)      — Calculate NPV of peering vs.
                                                               transit for a traffic profile.

Data sources (static, last updated 2026-Q1):
    - Tier 1 transit (Level3/Lumen, Cogent, NTT, Tata, GTT, Zayo)
    - Regional ISP transit pricing (North America, Europe, APAC, LATAM, MEA)
    - Internet Exchange port fees (DE-CIX, LINX, AMS-IX, Equinix IX, NYIIX, JPNAP)

Usage:
    python tools/pmc_canvas/transit_pricing_benchmark.py --benchmark --region na --json
    python tools/pmc_canvas/transit_pricing_benchmark.py --roi --json
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
from typing import Any, Dict, List, Optional

logger = get_logger("pmc.transit_pricing")

# ── Transit Price Benchmarks (USD/Mbps/month) ─────────────────────────────────
# Prices reflect 95th-percentile burstable billing, 1-year term, no commit discount.
# Port speeds: 1G, 10G, 100G. Values are midpoint of market range.

_TRANSIT_BENCHMARKS: Dict[str, Dict[str, Any]] = {
    "na": {
        "region": "North America",
        "description": "USA and Canada; major Tier 1 backbone pricing",
        "providers_sampled": ["Lumen/L3", "Cogent", "NTT", "Zayo", "GTT", "Hurricane Electric"],
        "pricing_by_speed": {
            "1g": {
                "port_speed_gbps": 1,
                "price_low_usd_mbps_mo": 0.10,
                "price_mid_usd_mbps_mo": 0.18,
                "price_high_usd_mbps_mo": 0.35,
                "typical_commit_pct": 10,
                "port_fee_usd_mo": 250,
            },
            "10g": {
                "port_speed_gbps": 10,
                "price_low_usd_mbps_mo": 0.06,
                "price_mid_usd_mbps_mo": 0.12,
                "price_high_usd_mbps_mo": 0.22,
                "typical_commit_pct": 10,
                "port_fee_usd_mo": 500,
            },
            "100g": {
                "port_speed_gbps": 100,
                "price_low_usd_mbps_mo": 0.03,
                "price_mid_usd_mbps_mo": 0.06,
                "price_high_usd_mbps_mo": 0.12,
                "typical_commit_pct": 5,
                "port_fee_usd_mo": 2000,
            },
        },
        "trends": "Declining ~10-15% YoY; 100G now standard; 400G emerging at major IXPs",
    },
    "eu": {
        "region": "Europe",
        "description": "Western and Central Europe; major DE-CIX/LINX/AMS-IX markets",
        "providers_sampled": ["Cogent", "NTT", "RETN", "DE-CIX Global", "Lumen EU", "GTT"],
        "pricing_by_speed": {
            "1g": {
                "port_speed_gbps": 1,
                "price_low_usd_mbps_mo": 0.08,
                "price_mid_usd_mbps_mo": 0.15,
                "price_high_usd_mbps_mo": 0.28,
                "typical_commit_pct": 10,
                "port_fee_usd_mo": 200,
            },
            "10g": {
                "port_speed_gbps": 10,
                "price_low_usd_mbps_mo": 0.05,
                "price_mid_usd_mbps_mo": 0.10,
                "price_high_usd_mbps_mo": 0.18,
                "typical_commit_pct": 10,
                "port_fee_usd_mo": 450,
            },
            "100g": {
                "port_speed_gbps": 100,
                "price_low_usd_mbps_mo": 0.025,
                "price_mid_usd_mbps_mo": 0.05,
                "price_high_usd_mbps_mo": 0.10,
                "typical_commit_pct": 5,
                "port_fee_usd_mo": 1500,
            },
        },
        "trends": "Strong peering ecosystem; Frankfurt and Amsterdam among world's lowest transit prices",
    },
    "apac": {
        "region": "Asia-Pacific",
        "description": "Japan, Singapore, Hong Kong, Australia; higher pricing due to subsea capacity",
        "providers_sampled": ["NTT", "PCCW", "Telstra", "SingTel", "Tata", "KDDI"],
        "pricing_by_speed": {
            "1g": {
                "port_speed_gbps": 1,
                "price_low_usd_mbps_mo": 0.40,
                "price_mid_usd_mbps_mo": 0.80,
                "price_high_usd_mbps_mo": 2.00,
                "typical_commit_pct": 20,
                "port_fee_usd_mo": 600,
            },
            "10g": {
                "port_speed_gbps": 10,
                "price_low_usd_mbps_mo": 0.20,
                "price_mid_usd_mbps_mo": 0.45,
                "price_high_usd_mbps_mo": 1.00,
                "typical_commit_pct": 15,
                "port_fee_usd_mo": 1500,
            },
            "100g": {
                "port_speed_gbps": 100,
                "price_low_usd_mbps_mo": 0.10,
                "price_mid_usd_mbps_mo": 0.25,
                "price_high_usd_mbps_mo": 0.60,
                "typical_commit_pct": 10,
                "port_fee_usd_mo": 5000,
            },
        },
        "trends": "Declining as subsea cable capacity expands; APAC IXP growth reducing transit dependency",
    },
    "latam": {
        "region": "Latin America",
        "description": "Brazil, Mexico, Colombia, Argentina; emerging market pricing",
        "providers_sampled": ["Cogent", "Tata", "GTT", "Lumen", "Telxius", "Cirion"],
        "pricing_by_speed": {
            "1g": {
                "port_speed_gbps": 1,
                "price_low_usd_mbps_mo": 0.50,
                "price_mid_usd_mbps_mo": 1.20,
                "price_high_usd_mbps_mo": 3.00,
                "typical_commit_pct": 20,
                "port_fee_usd_mo": 500,
            },
            "10g": {
                "port_speed_gbps": 10,
                "price_low_usd_mbps_mo": 0.30,
                "price_mid_usd_mbps_mo": 0.70,
                "price_high_usd_mbps_mo": 1.80,
                "typical_commit_pct": 15,
                "port_fee_usd_mo": 1200,
            },
            "100g": {
                "port_speed_gbps": 100,
                "price_low_usd_mbps_mo": 0.15,
                "price_mid_usd_mbps_mo": 0.40,
                "price_high_usd_mbps_mo": 1.00,
                "typical_commit_pct": 10,
                "port_fee_usd_mo": 4000,
            },
        },
        "trends": "Higher prices; São Paulo and Bogotá growing IXP hubs reducing Tier 1 dependency",
    },
    "mea": {
        "region": "Middle East & Africa",
        "description": "GCC, South Africa, Nigeria; limited peering infrastructure",
        "providers_sampled": ["Tata", "PCCW", "STC", "Etisalat/e&", "Liquid Telecom"],
        "pricing_by_speed": {
            "1g": {
                "port_speed_gbps": 1,
                "price_low_usd_mbps_mo": 1.00,
                "price_mid_usd_mbps_mo": 2.50,
                "price_high_usd_mbps_mo": 6.00,
                "typical_commit_pct": 30,
                "port_fee_usd_mo": 800,
            },
            "10g": {
                "port_speed_gbps": 10,
                "price_low_usd_mbps_mo": 0.60,
                "price_mid_usd_mbps_mo": 1.50,
                "price_high_usd_mbps_mo": 4.00,
                "typical_commit_pct": 20,
                "port_fee_usd_mo": 2000,
            },
            "100g": {
                "port_speed_gbps": 100,
                "price_low_usd_mbps_mo": 0.30,
                "price_mid_usd_mbps_mo": 0.80,
                "price_high_usd_mbps_mo": 2.50,
                "typical_commit_pct": 15,
                "port_fee_usd_mo": 8000,
            },
        },
        "trends": "High prices; IXP development accelerating in UAE, Kenya, South Africa",
    },
}

# ── Internet Exchange Port Fee Benchmarks ─────────────────────────────────────

_IX_PORT_BENCHMARKS: List[Dict[str, Any]] = [
    {
        "ix_name": "DE-CIX Frankfurt",
        "city": "Frankfurt",
        "region": "eu",
        "peak_gbps": 12000,
        "member_count": 1000,
        "port_fees_usd_mo": {"1g": 120, "10g": 400, "100g": 1500},
        "annual_membership_usd": 1200,
    },
    {
        "ix_name": "LINX LON1",
        "city": "London",
        "region": "eu",
        "peak_gbps": 8000,
        "member_count": 900,
        "port_fees_usd_mo": {"1g": 150, "10g": 480, "100g": 1800},
        "annual_membership_usd": 2000,
    },
    {
        "ix_name": "AMS-IX",
        "city": "Amsterdam",
        "region": "eu",
        "peak_gbps": 9000,
        "member_count": 950,
        "port_fees_usd_mo": {"1g": 130, "10g": 420, "100g": 1600},
        "annual_membership_usd": 1500,
    },
    {
        "ix_name": "Equinix IX New York (NYIIX)",
        "city": "New York",
        "region": "na",
        "peak_gbps": 4000,
        "member_count": 300,
        "port_fees_usd_mo": {"1g": 200, "10g": 600, "100g": 2000},
        "annual_membership_usd": 2400,
    },
    {
        "ix_name": "Any2 Los Angeles",
        "city": "Los Angeles",
        "region": "na",
        "peak_gbps": 2500,
        "member_count": 150,
        "port_fees_usd_mo": {"1g": 150, "10g": 450, "100g": 1500},
        "annual_membership_usd": 1200,
    },
    {
        "ix_name": "JPNAP Tokyo",
        "city": "Tokyo",
        "region": "apac",
        "peak_gbps": 5000,
        "member_count": 200,
        "port_fees_usd_mo": {"1g": 300, "10g": 900, "100g": 3500},
        "annual_membership_usd": 3000,
    },
    {
        "ix_name": "SGIX Singapore",
        "city": "Singapore",
        "region": "apac",
        "peak_gbps": 2000,
        "member_count": 170,
        "port_fees_usd_mo": {"1g": 400, "10g": 1200, "100g": 4500},
        "annual_membership_usd": 4000,
    },
    {
        "ix_name": "PTT São Paulo (IX.br)",
        "city": "São Paulo",
        "region": "latam",
        "peak_gbps": 3500,
        "member_count": 2200,
        "port_fees_usd_mo": {"1g": 60, "10g": 200, "100g": 800},
        "annual_membership_usd": 600,
        "notes": "Free to NIC.br members; pricing for non-members shown",
    },
    {
        "ix_name": "UAE-IX Dubai",
        "city": "Dubai",
        "region": "mea",
        "peak_gbps": 600,
        "member_count": 100,
        "port_fees_usd_mo": {"1g": 800, "10g": 2500, "100g": 9000},
        "annual_membership_usd": 5000,
    },
]


# ── Public API ────────────────────────────────────────────────────────────────

def benchmark_transit_cost(
    region: str,
    speed_gbps: float,
    commit_pct: float = 10.0,
    price_point: str = "mid",
) -> Dict[str, Any]:
    """Look up transit price benchmark for a region and port speed.

    Args:
        region: Region code — 'na', 'eu', 'apac', 'latam', 'mea'
        speed_gbps: Port speed in Gbps — 1, 10, or 100
        commit_pct: Committed traffic as % of port capacity (for 95th-pct billing)
        price_point: 'low', 'mid', or 'high' (market quartile)

    Returns:
        Benchmark price per Mbps/month, estimated monthly cost, and context.
    """
    region = region.lower()
    if region not in _TRANSIT_BENCHMARKS:
        return {"error": f"Unknown region '{region}'. Valid: {list(_TRANSIT_BENCHMARKS.keys())}"}

    bench = _TRANSIT_BENCHMARKS[region]

    # Map speed to tier key
    if speed_gbps <= 1:
        speed_key = "1g"
    elif speed_gbps <= 10:
        speed_key = "10g"
    else:
        speed_key = "100g"

    pricing = bench["pricing_by_speed"][speed_key]
    price_key = f"price_{price_point}_usd_mbps_mo"
    price_per_mbps = pricing.get(price_key, pricing["price_mid_usd_mbps_mo"])

    # 95th-percentile billing: pay for commit_pct% of port
    port_mbps = speed_gbps * 1000
    commit_mbps = port_mbps * (commit_pct / 100)
    monthly_traffic_cost = round(commit_mbps * price_per_mbps, 2)
    monthly_total = round(monthly_traffic_cost + pricing["port_fee_usd_mo"], 2)
    annual_total = round(monthly_total * 12, 2)

    # Find IX options in region
    ix_options = [ix for ix in _IX_PORT_BENCHMARKS if ix["region"] == region]
    ix_port_key = speed_key
    ix_cost_examples = [
        {
            "ix": ix["ix_name"],
            "city": ix["city"],
            "port_fee_usd_mo": ix["port_fees_usd_mo"].get(ix_port_key, "N/A"),
        }
        for ix in ix_options[:3]
    ]

    return {
        "region": bench["region"],
        "port_speed_gbps": pricing["port_speed_gbps"],
        "price_point": price_point,
        "price_per_mbps_usd_month": price_per_mbps,
        "commit_pct": commit_pct,
        "commit_mbps": round(commit_mbps, 1),
        "monthly_traffic_cost_usd": monthly_traffic_cost,
        "port_fee_usd_month": pricing["port_fee_usd_mo"],
        "monthly_total_usd": monthly_total,
        "annual_total_usd": annual_total,
        "providers_sampled": bench["providers_sampled"],
        "market_trends": bench["trends"],
        "ix_alternatives": ix_cost_examples,
        "data_as_of": "2026-Q1",
    }


def peering_vs_transit_roi(
    peers: List[Dict[str, Any]],
    transit_costs: Dict[str, Any],
    capex: Dict[str, Any],
    analysis_years: int = 3,
    discount_rate: float = 0.10,
) -> Dict[str, Any]:
    """Calculate the ROI of building a peering program vs. remaining on paid transit.

    Args:
        peers: List of peer dicts. Each: {asn, traffic_gbps_95th, direction ('inbound'|'outbound'|'both')}
        transit_costs: Transit benchmark dict (output of benchmark_transit_cost, or custom dict with
                       price_per_mbps_usd_month, port_fee_usd_month)
        capex: Peering infrastructure costs. Keys:
               ix_port_monthly_usd (per-IX port cost), ix_count (number of IXes to join),
               router_upgrade_usd (one-time), noc_hours_per_peer (labor to activate)
        analysis_years: Payback analysis horizon (default 3 years)
        discount_rate: Annual discount rate for NPV (default 10%)

    Returns:
        Monthly transit savings, peering TCO, NPV, payback period, and recommendation.
    """
    price_per_mbps = float(transit_costs.get("price_per_mbps_usd_month", 0.12))
    transit_port_fee = float(transit_costs.get("port_fee_usd_month", 500))

    # Calculate total peereable traffic (Gbps → Mbps)
    total_traffic_mbps = 0.0
    peer_detail = []
    for peer in peers:
        gbps = float(peer.get("traffic_gbps_95th", 0))
        mbps = gbps * 1000
        total_traffic_mbps += mbps
        peer_detail.append({
            "asn": peer.get("asn"),
            "traffic_gbps_95th": gbps,
            "traffic_mbps": mbps,
            "monthly_transit_cost_usd": round(mbps * price_per_mbps, 2),
        })

    # Current transit cost for this traffic
    monthly_transit_cost = round(total_traffic_mbps * price_per_mbps + transit_port_fee, 2)
    annual_transit_cost = round(monthly_transit_cost * 12, 2)

    # Peering cost (IX ports + operational labor)
    ix_count = int(capex.get("ix_count", 2))
    ix_port_monthly = float(capex.get("ix_port_monthly_usd", 500))
    noc_hours = float(capex.get("noc_hours_per_peer", 4))
    noc_rate = float(capex.get("noc_hourly_rate_usd", 120))
    router_capex = float(capex.get("router_upgrade_usd", 0))

    monthly_peering_opex = round(ix_port_monthly * ix_count, 2)
    activation_labor = round(len(peers) * noc_hours * noc_rate, 2)
    total_capex = round(router_capex + activation_labor, 2)

    annual_peering_opex = round(monthly_peering_opex * 12, 2)
    annual_savings = round(annual_transit_cost - annual_peering_opex, 2)
    monthly_savings = round(monthly_transit_cost - monthly_peering_opex, 2)

    # Payback period (months)
    payback_months = round(total_capex / monthly_savings, 1) if monthly_savings > 0 else float("inf")

    # NPV over analysis_years
    monthly_rate = discount_rate / 12
    npv = -total_capex
    for month in range(1, analysis_years * 12 + 1):
        npv += monthly_savings / ((1 + monthly_rate) ** month)
    npv = round(npv, 2)

    # Cash flow table (annual)
    cash_flow = []
    cumulative = -total_capex
    for year in range(1, analysis_years + 1):
        cumulative += annual_savings
        cash_flow.append({
            "year": year,
            "transit_cost_avoided_usd": annual_transit_cost,
            "peering_opex_usd": annual_peering_opex,
            "net_savings_usd": annual_savings,
            "cumulative_usd": round(cumulative, 2),
        })

    # Recommendation
    if npv > 0 and payback_months < 24:
        recommendation = f"STRONG BUY — NPV ${npv:,.0f} over {analysis_years}yr; payback {payback_months:.1f}mo"
    elif npv > 0:
        recommendation = f"BUY — NPV positive (${npv:,.0f}) but payback {payback_months:.1f}mo exceeds 2yr"
    elif monthly_savings > 0:
        recommendation = f"MARGINAL — positive monthly savings ${monthly_savings:,.0f}/mo but CAPEX not recovered in {analysis_years}yr"
    else:
        recommendation = "HOLD — peering does not reduce costs at current traffic volumes; revisit when traffic exceeds benchmarks"

    return {
        "analysis": "Peering vs. Transit ROI",
        "peer_count": len(peers),
        "total_peereable_traffic_gbps": round(total_traffic_mbps / 1000, 1),
        "transit": {
            "price_per_mbps_usd_month": price_per_mbps,
            "monthly_cost_usd": monthly_transit_cost,
            "annual_cost_usd": annual_transit_cost,
        },
        "peering": {
            "ix_count": ix_count,
            "monthly_opex_usd": monthly_peering_opex,
            "annual_opex_usd": annual_peering_opex,
            "initial_capex_usd": total_capex,
            "router_upgrade_usd": router_capex,
            "activation_labor_usd": activation_labor,
        },
        "financials": {
            "monthly_savings_usd": monthly_savings,
            "annual_savings_usd": annual_savings,
            "payback_months": payback_months,
            "npv_usd": npv,
            "discount_rate_pct": discount_rate * 100,
            "analysis_years": analysis_years,
        },
        "cash_flow_table": cash_flow,
        "peer_detail": peer_detail,
        "recommendation": recommendation,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }


def get_ix_benchmarks(region: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return Internet Exchange port fee benchmarks, optionally filtered by region."""
    if region:
        return [ix for ix in _IX_PORT_BENCHMARKS if ix["region"] == region.lower()]
    return _IX_PORT_BENCHMARKS


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli() -> None:
    parser = argparse.ArgumentParser(description="Transit Pricing Benchmark — peering vs. transit ROI")
    parser.add_argument("--benchmark", action="store_true")
    parser.add_argument("--region", default="na", choices=list(_TRANSIT_BENCHMARKS.keys()))
    parser.add_argument("--speed", type=float, default=10.0)
    parser.add_argument("--roi", action="store_true")
    parser.add_argument("--ix", action="store_true", help="List IX benchmarks")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result: Any = {}

    if args.benchmark:
        result = benchmark_transit_cost(args.region, args.speed)
    elif args.roi:
        sample_peers = [
            {"asn": 13335, "traffic_gbps_95th": 5.0, "direction": "both"},
            {"asn": 15169, "traffic_gbps_95th": 8.0, "direction": "inbound"},
            {"asn": 16509, "traffic_gbps_95th": 3.0, "direction": "inbound"},
            {"asn": 8075,  "traffic_gbps_95th": 2.5, "direction": "inbound"},
        ]
        transit = benchmark_transit_cost("na", 100)
        result = peering_vs_transit_roi(
            peers=sample_peers,
            transit_costs=transit,
            capex={"ix_count": 3, "ix_port_monthly_usd": 600, "router_upgrade_usd": 25000, "noc_hours_per_peer": 4},
            analysis_years=3,
        )
    elif args.ix:
        result = get_ix_benchmarks(args.region if args.region != "na" else None)
    else:
        parser.print_help()
        return

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    _cli()
