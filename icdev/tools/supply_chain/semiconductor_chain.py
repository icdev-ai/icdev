#!/usr/bin/env python3
# CUI // SP-CTI
"""Semiconductor Supply Chain Disruption Analyzer.

Models Taiwan/TSMC disruption scenarios affecting advanced semiconductor
components critical to US defense systems. Calculates per-system-type
depletion timelines from stockpile and consumption estimates.

Data: stockpile figures are mock estimates for planning purposes — actual
DoD inventory data is controlled.

CLI:
  python tools/supply_chain/semiconductor_chain.py --severity critical --json
  python tools/supply_chain/semiconductor_chain.py --list-components
"""

import argparse
import json
import sys
from typing import Any

# ---------------------------------------------------------------------------
# Semiconductor component stockpile data (mock planning estimates).
# Units: thousands of units (k_units) for stockpile / monthly_consumption.
# taiwan_supply_pct: fraction of US defense supply from Taiwan (primarily TSMC).
# ---------------------------------------------------------------------------

SEMI_STOCKPILE: dict[str, dict[str, Any]] = {
    "advanced_fpga": {
        "description": "Advanced FPGAs (7nm/10nm, Xilinx Virtex UltraScale+)",
        "current_k_units": 480,
        "monthly_consumption_k_units": 22,
        "taiwan_supply_pct": 0.88,
        "used_by": ["aviation", "naval", "sensor", "uas"],
    },
    "avionics_soc": {
        "description": "Avionics System-on-Chip (ARM-based, DO-178C certified)",
        "current_k_units": 310,
        "monthly_consumption_k_units": 14,
        "taiwan_supply_pct": 0.76,
        "used_by": ["aviation", "missile_defense", "ground", "uas", "munition"],
    },
    "gan_pa": {
        "description": "GaN Power Amplifiers for radar/EW (RFIC, 0.15µm GaN)",
        "current_k_units": 195,
        "monthly_consumption_k_units": 9,
        "taiwan_supply_pct": 0.62,
        "used_by": ["naval", "sensor", "aviation"],
    },
    "mission_processor": {
        "description": "Mission/Navigation Processors (x86-64, hardened)",
        "current_k_units": 560,
        "monthly_consumption_k_units": 28,
        "taiwan_supply_pct": 0.82,
        "used_by": ["aviation", "naval", "ground", "missile_defense", "uas"],
    },
    "rad_hard_asic": {
        "description": "Radiation-Hardened ASICs (mil-spec SEE/TID tolerant)",
        "current_k_units": 140,
        "monthly_consumption_k_units": 5,
        "taiwan_supply_pct": 0.42,
        "used_by": ["missile_defense", "munition", "naval"],
    },
    "lpddr5_mem": {
        "description": "LPDDR5 / HBM memory (advanced node, Samsung/SK Hynix)",
        "current_k_units": 820,
        "monthly_consumption_k_units": 48,
        "taiwan_supply_pct": 0.58,
        "used_by": ["aviation", "naval", "ground", "sensor"],
    },
    "sar_processor": {
        "description": "SAR/EO-IR signal processors (custom ASIC, TSMC 5nm)",
        "current_k_units": 95,
        "monthly_consumption_k_units": 4,
        "taiwan_supply_pct": 0.94,
        "used_by": ["sensor", "uas", "aviation"],
    },
    "sige_bicmos": {
        "description": "SiGe BiCMOS radar receivers (130nm, InPhi/Qorvo)",
        "current_k_units": 220,
        "monthly_consumption_k_units": 11,
        "taiwan_supply_pct": 0.70,
        "used_by": ["naval", "sensor", "missile_defense"],
    },
    "crypto_asic": {
        "description": "Crypto/KMI ASICs (NSA-evaluated, Type 1 encryption)",
        "current_k_units": 185,
        "monthly_consumption_k_units": 7,
        "taiwan_supply_pct": 0.55,
        "used_by": ["aviation", "naval", "ground", "missile_defense"],
    },
    "power_mgmt_ic": {
        "description": "High-reliability Power Management ICs (MIL-PRF-38535)",
        "current_k_units": 680,
        "monthly_consumption_k_units": 35,
        "taiwan_supply_pct": 0.73,
        "used_by": ["aviation", "naval", "ground", "uas", "munition"],
    },
}

# System types that aggregate across defense programs
SYSTEM_TYPES: list[str] = [
    "aviation",
    "naval",
    "missile_defense",
    "ground",
    "uas",
    "sensor",
    "munition",
]

# Severity multipliers (fraction of Taiwan-dependent supply immediately lost)
SEVERITY_FACTORS: dict[str, float] = {
    "low": 0.25,
    "moderate": 0.55,
    "high": 0.80,
    "critical": 1.00,
}

BLAST_RADIUS_THRESHOLD_MONTHS = 36


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def _component_depletion_months(component: str, severity_factor: float) -> float:
    """Months until component stockpile exhausts under Taiwan supply disruption."""
    data = SEMI_STOCKPILE.get(component)
    if not data:
        return float("inf")

    current: float = data["current_k_units"]
    monthly: float = data["monthly_consumption_k_units"]
    taiwan_pct: float = data["taiwan_supply_pct"]

    monthly_deficit = monthly * taiwan_pct * severity_factor
    if monthly_deficit <= 0:
        return float("inf")

    return current / monthly_deficit


def run_scenario(
    scenario: str = "Taiwan strait contingency",
    severity: str = "critical",
) -> dict[str, Any]:
    """Run a semiconductor supply disruption scenario.

    Args:
        scenario: Scenario label.
        severity: 'low', 'moderate', 'high', or 'critical'.

    Returns:
        {
            'scenario': str,
            'severity': str,
            'blast_radius': int,
            'component_depletion_months': {component: float},
            'depletion_months_by_system': {system_type: float},
            'affected_components': [{id, description, depletion_months, used_by}],
        }

    Raises:
        ValueError: unknown severity.
    """
    severity_lower = severity.lower().strip()
    if severity_lower not in SEVERITY_FACTORS:
        raise ValueError(
            f"Unknown severity '{severity}'. Valid: {sorted(SEVERITY_FACTORS)}"
        )

    factor = SEVERITY_FACTORS[severity_lower]

    # 1. Per-component depletion
    comp_depletion: dict[str, float] = {
        comp: _component_depletion_months(comp, factor)
        for comp in SEMI_STOCKPILE
    }

    # 2. Per-system-type depletion = min depletion among components used by that type
    sys_depletion: dict[str, float] = {}
    for stype in SYSTEM_TYPES:
        relevant = [
            comp_depletion[comp]
            for comp, data in SEMI_STOCKPILE.items()
            if stype in data["used_by"]
        ]
        sys_depletion[stype] = min(relevant) if relevant else float("inf")

    # 3. Affected components (deplete within threshold)
    affected: list[dict[str, Any]] = []
    blast = 0
    for comp, months in comp_depletion.items():
        if months <= BLAST_RADIUS_THRESHOLD_MONTHS:
            blast += 1
            affected.append(
                {
                    "id": comp,
                    "description": SEMI_STOCKPILE[comp]["description"],
                    "depletion_months": round(months, 1),
                    "used_by": SEMI_STOCKPILE[comp]["used_by"],
                }
            )
    affected.sort(key=lambda x: x["depletion_months"])

    return {
        "scenario": scenario,
        "severity": severity_lower,
        "blast_radius": blast,
        "component_depletion_months": {c: round(v, 1) for c, v in comp_depletion.items()},
        "depletion_months_by_system": {s: round(v, 1) for s, v in sys_depletion.items()},
        "affected_components": affected,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Semiconductor Supply Chain Disruption Analyzer"
    )
    parser.add_argument(
        "--scenario",
        default="Taiwan strait contingency",
        help="Scenario label",
    )
    parser.add_argument(
        "--severity",
        default="critical",
        choices=list(SEVERITY_FACTORS),
    )
    parser.add_argument(
        "--list-components",
        action="store_true",
        help="List tracked semiconductor components and exit",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    if args.list_components:
        rows = [
            {"id": cid, "description": d["description"], "taiwan_pct": d["taiwan_supply_pct"]}
            for cid, d in SEMI_STOCKPILE.items()
        ]
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            print(f"{'ID':<22} {'Taiwan%':>8}  Description")
            print("-" * 80)
            for r in rows:
                print(f"{r['id']:<22} {r['taiwan_pct']:>7.0%}  {r['description']}")
        return

    try:
        result = run_scenario(scenario=args.scenario, severity=args.severity)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(result, indent=2))
        return

    print(f"Scenario  : {result['scenario']}")
    print(f"Severity  : {result['severity'].upper()}")
    print(f"Blast Radius : {result['blast_radius']} components at risk")
    print()
    print("Depletion by System Type:")
    for stype, months in sorted(result["depletion_months_by_system"].items(), key=lambda x: x[1]):
        days = round(months * 30.44)
        print(f"  {stype:<18} {months:>6.1f} mo  ({days} days)")
    print()
    if result["affected_components"]:
        print("Affected Components (soonest first):")
        for c in result["affected_components"]:
            print(f"  {c['id']:<22} {c['depletion_months']:>6.1f} mo  {c['description'][:50]}")


if __name__ == "__main__":
    main()
