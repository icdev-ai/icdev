#!/usr/bin/env python3
from __future__ import annotations
# CUI // SP-CTI
"""Rare Earth Element (REE) Supply Cascade Analyzer.

Models embargo scenarios affecting REE supply chains for US defense programs.
Calculates blast radius (affected program count) and time-to-depletion per
system from stockpile data.

Data: stockpile figures are mock estimates for planning purposes — actual
DoD National Defense Stockpile data is controlled.

CLI:
  python tools/supply_chain/rare_earth_cascade.py --impact "China RE embargo" --severity critical --json
  python tools/supply_chain/rare_earth_cascade.py --list-programs
"""

import argparse
import json
import sys
from typing import Any

# ---------------------------------------------------------------------------
# REE stockpile data (mock planning estimates; real data is classified)
# Units: kg for stockpile / monthly_consumption.
# china_supply_pct: fraction of US supply currently sourced from China.
# ---------------------------------------------------------------------------

STOCKPILE_DATA: dict[str, dict[str, Any]] = {
    "neodymium": {
        "symbol": "Nd",
        "current_kg": 450_000,
        "monthly_consumption_kg": 28_000,
        "china_supply_pct": 0.78,
        "uses": ["permanent magnets", "EW motors", "actuators", "MRI"],
    },
    "dysprosium": {
        "symbol": "Dy",
        "current_kg": 12_000,
        "monthly_consumption_kg": 1_800,
        "china_supply_pct": 0.92,
        "uses": ["high-temp magnets", "nuclear reactor control rods"],
    },
    "terbium": {
        "symbol": "Tb",
        "current_kg": 5_500,
        "monthly_consumption_kg": 950,
        "china_supply_pct": 0.88,
        "uses": ["solid-state devices", "fuel cells", "sonar transducers"],
    },
    "lanthanum": {
        "symbol": "La",
        "current_kg": 180_000,
        "monthly_consumption_kg": 8_200,
        "china_supply_pct": 0.72,
        "uses": ["night-vision lenses", "camera optics", "hydrogen storage"],
    },
    "cerium": {
        "symbol": "Ce",
        "current_kg": 320_000,
        "monthly_consumption_kg": 15_400,
        "china_supply_pct": 0.65,
        "uses": ["radar components", "optical polishing", "catalytic converters"],
    },
    "praseodymium": {
        "symbol": "Pr",
        "current_kg": 95_000,
        "monthly_consumption_kg": 5_600,
        "china_supply_pct": 0.75,
        "uses": ["alloy strengthening", "magnets", "aircraft engines"],
    },
    "samarium": {
        "symbol": "Sm",
        "current_kg": 8_500,
        "monthly_consumption_kg": 620,
        "china_supply_pct": 0.70,
        "uses": ["SmCo magnets", "precision-guided munitions gyros"],
    },
    "europium": {
        "symbol": "Eu",
        "current_kg": 4_200,
        "monthly_consumption_kg": 390,
        "china_supply_pct": 0.80,
        "uses": ["phosphors", "laser materials", "anti-counterfeit markings"],
    },
    "gadolinium": {
        "symbol": "Gd",
        "current_kg": 7_800,
        "monthly_consumption_kg": 510,
        "china_supply_pct": 0.74,
        "uses": ["neutron capture", "MRI contrast", "magnetocaloric cooling"],
    },
    "erbium": {
        "symbol": "Er",
        "current_kg": 6_100,
        "monthly_consumption_kg": 420,
        "china_supply_pct": 0.77,
        "uses": ["fiber amplifiers", "laser rangefinders", "vanadium alloys"],
    },
    "yttrium": {
        "symbol": "Y",
        "current_kg": 85_000,
        "monthly_consumption_kg": 3_800,
        "china_supply_pct": 0.68,
        "uses": ["ceramic coatings", "LED phosphors", "radar components"],
    },
    "holmium": {
        "symbol": "Ho",
        "current_kg": 2_300,
        "monthly_consumption_kg": 180,
        "china_supply_pct": 0.82,
        "uses": ["nuclear reactor shielding", "magnetic flux concentrators"],
    },
    "thulium": {
        "symbol": "Tm",
        "current_kg": 1_800,
        "monthly_consumption_kg": 140,
        "china_supply_pct": 0.85,
        "uses": ["portable X-ray sources", "laser materials"],
    },
}

# ---------------------------------------------------------------------------
# Defense program catalog with REE dependencies.
# Each entry lists critical_elements (must-have) and monthly consumption per
# element (aggregate across active production / sustainment).
# ---------------------------------------------------------------------------

DEFENSE_PROGRAMS: list[dict[str, Any]] = [
    # ----- AIRCRAFT -----
    {
        "id": "F-35A", "name": "F-35A Lightning II", "category": "aircraft",
        "critical_elements": ["neodymium", "dysprosium", "terbium", "erbium"],
        "monthly_consumption_kg": {"neodymium": 420, "dysprosium": 65, "terbium": 18, "erbium": 22},
    },
    {
        "id": "F-35B", "name": "F-35B Lightning II (STOVL)", "category": "aircraft",
        "critical_elements": ["neodymium", "dysprosium", "terbium"],
        "monthly_consumption_kg": {"neodymium": 310, "dysprosium": 48, "terbium": 14},
    },
    {
        "id": "F-35C", "name": "F-35C Lightning II (CV)", "category": "aircraft",
        "critical_elements": ["neodymium", "dysprosium", "terbium"],
        "monthly_consumption_kg": {"neodymium": 280, "dysprosium": 44, "terbium": 12},
    },
    {
        "id": "F-22A", "name": "F-22 Raptor", "category": "aircraft",
        "critical_elements": ["neodymium", "dysprosium", "yttrium"],
        "monthly_consumption_kg": {"neodymium": 190, "dysprosium": 30, "yttrium": 55},
    },
    {
        "id": "B-2A", "name": "B-2 Spirit", "category": "aircraft",
        "critical_elements": ["neodymium", "yttrium", "cerium"],
        "monthly_consumption_kg": {"neodymium": 145, "yttrium": 40, "cerium": 280},
    },
    {
        "id": "B-21", "name": "B-21 Raider", "category": "aircraft",
        "critical_elements": ["neodymium", "dysprosium", "terbium", "yttrium"],
        "monthly_consumption_kg": {"neodymium": 380, "dysprosium": 58, "terbium": 16, "yttrium": 70},
    },
    {
        "id": "F-15EX", "name": "F-15EX Eagle II", "category": "aircraft",
        "critical_elements": ["neodymium", "praseodymium", "cerium"],
        "monthly_consumption_kg": {"neodymium": 220, "praseodymium": 90, "cerium": 310},
    },
    {
        "id": "FA-18EF", "name": "F/A-18E/F Super Hornet", "category": "aircraft",
        "critical_elements": ["neodymium", "praseodymium"],
        "monthly_consumption_kg": {"neodymium": 195, "praseodymium": 78},
    },
    {
        "id": "EA-18G", "name": "EA-18G Growler", "category": "aircraft",
        "critical_elements": ["neodymium", "dysprosium", "samarium"],
        "monthly_consumption_kg": {"neodymium": 175, "dysprosium": 28, "samarium": 35},
    },
    {
        "id": "E-2D", "name": "E-2D Advanced Hawkeye", "category": "aircraft",
        "critical_elements": ["neodymium", "yttrium", "cerium"],
        "monthly_consumption_kg": {"neodymium": 130, "yttrium": 35, "cerium": 190},
    },
    {
        "id": "P-8A", "name": "P-8A Poseidon", "category": "aircraft",
        "critical_elements": ["neodymium", "lanthanum"],
        "monthly_consumption_kg": {"neodymium": 165, "lanthanum": 120},
    },
    {
        "id": "KC-46A", "name": "KC-46A Pegasus", "category": "aircraft",
        "critical_elements": ["neodymium", "cerium"],
        "monthly_consumption_kg": {"neodymium": 210, "cerium": 340},
    },
    {
        "id": "C-17A", "name": "C-17 Globemaster III", "category": "aircraft",
        "critical_elements": ["neodymium", "cerium", "praseodymium"],
        "monthly_consumption_kg": {"neodymium": 180, "cerium": 295, "praseodymium": 72},
    },
    # ----- ROTARY & TILTROTOR -----
    {
        "id": "CH-47F", "name": "CH-47F Chinook", "category": "rotary",
        "critical_elements": ["neodymium", "cerium"],
        "monthly_consumption_kg": {"neodymium": 140, "cerium": 215},
    },
    {
        "id": "UH-60M", "name": "UH-60M Black Hawk", "category": "rotary",
        "critical_elements": ["neodymium", "praseodymium"],
        "monthly_consumption_kg": {"neodymium": 110, "praseodymium": 44},
    },
    {
        "id": "AH-64E", "name": "AH-64E Apache Guardian", "category": "rotary",
        "critical_elements": ["neodymium", "dysprosium", "lanthanum"],
        "monthly_consumption_kg": {"neodymium": 125, "dysprosium": 19, "lanthanum": 85},
    },
    {
        "id": "MH-60R", "name": "MH-60R Seahawk", "category": "rotary",
        "critical_elements": ["neodymium", "praseodymium"],
        "monthly_consumption_kg": {"neodymium": 105, "praseodymium": 42},
    },
    {
        "id": "CMV-22B", "name": "CMV-22B Osprey", "category": "tiltrotor",
        "critical_elements": ["neodymium", "dysprosium", "cerium"],
        "monthly_consumption_kg": {"neodymium": 160, "dysprosium": 25, "cerium": 230},
    },
    # ----- UAS -----
    {
        "id": "MQ-9A", "name": "MQ-9 Reaper", "category": "uas",
        "critical_elements": ["neodymium", "dysprosium", "erbium"],
        "monthly_consumption_kg": {"neodymium": 95, "dysprosium": 15, "erbium": 18},
    },
    {
        "id": "MQ-4C", "name": "MQ-4C Triton", "category": "uas",
        "critical_elements": ["neodymium", "lanthanum", "erbium"],
        "monthly_consumption_kg": {"neodymium": 88, "lanthanum": 62, "erbium": 16},
    },
    {
        "id": "RQ-4B", "name": "RQ-4B Global Hawk", "category": "uas",
        "critical_elements": ["neodymium", "lanthanum"],
        "monthly_consumption_kg": {"neodymium": 82, "lanthanum": 58},
    },
    # ----- GROUND SYSTEMS -----
    {
        "id": "M1A2SEPv3", "name": "M1A2 Abrams SEPv3", "category": "ground",
        "critical_elements": ["neodymium", "dysprosium", "lanthanum"],
        "monthly_consumption_kg": {"neodymium": 260, "dysprosium": 40, "lanthanum": 175},
    },
    {
        "id": "M2A3", "name": "M2A3 Bradley IFV", "category": "ground",
        "critical_elements": ["neodymium", "cerium"],
        "monthly_consumption_kg": {"neodymium": 145, "cerium": 210},
    },
    {
        "id": "M10", "name": "M10 Booker Light Tank", "category": "ground",
        "critical_elements": ["neodymium", "praseodymium"],
        "monthly_consumption_kg": {"neodymium": 120, "praseodymium": 48},
    },
    {
        "id": "M109A7", "name": "M109A7 Paladin SPH", "category": "ground",
        "critical_elements": ["neodymium", "cerium", "yttrium"],
        "monthly_consumption_kg": {"neodymium": 135, "cerium": 195, "yttrium": 50},
    },
    # ----- NAVAL -----
    {
        "id": "DDG-51", "name": "DDG-51 Arleigh Burke (Flight IIA)", "category": "naval",
        "critical_elements": ["neodymium", "dysprosium", "yttrium", "cerium"],
        "monthly_consumption_kg": {"neodymium": 480, "dysprosium": 74, "yttrium": 130, "cerium": 520},
    },
    {
        "id": "DDG-51F3", "name": "DDG-51 Arleigh Burke (Flight III)", "category": "naval",
        "critical_elements": ["neodymium", "dysprosium", "terbium", "yttrium"],
        "monthly_consumption_kg": {"neodymium": 520, "dysprosium": 80, "terbium": 22, "yttrium": 145},
    },
    {
        "id": "DDG-1000", "name": "DDG-1000 Zumwalt", "category": "naval",
        "critical_elements": ["neodymium", "dysprosium", "terbium", "samarium"],
        "monthly_consumption_kg": {"neodymium": 580, "dysprosium": 90, "terbium": 25, "samarium": 48},
    },
    {
        "id": "SSN-774", "name": "SSN-774 Virginia-class", "category": "naval",
        "critical_elements": ["neodymium", "dysprosium", "holmium", "gadolinium"],
        "monthly_consumption_kg": {"neodymium": 540, "dysprosium": 84, "holmium": 32, "gadolinium": 68},
    },
    {
        "id": "SSBN-826", "name": "SSBN-826 Columbia-class", "category": "naval",
        "critical_elements": ["neodymium", "dysprosium", "holmium", "gadolinium", "terbium"],
        "monthly_consumption_kg": {"neodymium": 620, "dysprosium": 96, "holmium": 38, "gadolinium": 78, "terbium": 28},
    },
    {
        "id": "LCS-IN", "name": "LCS Independence-class", "category": "naval",
        "critical_elements": ["neodymium", "praseodymium", "cerium"],
        "monthly_consumption_kg": {"neodymium": 310, "praseodymium": 124, "cerium": 420},
    },
    {
        "id": "LCS-FR", "name": "LCS Freedom-class", "category": "naval",
        "critical_elements": ["neodymium", "praseodymium", "cerium"],
        "monthly_consumption_kg": {"neodymium": 295, "praseodymium": 118, "cerium": 400},
    },
    {
        "id": "LPD-17", "name": "San Antonio-class LPD-17", "category": "naval",
        "critical_elements": ["neodymium", "cerium", "yttrium"],
        "monthly_consumption_kg": {"neodymium": 345, "cerium": 465, "yttrium": 95},
    },
    # ----- MISSILE & AIR DEFENSE -----
    {
        "id": "PAC3MSE", "name": "Patriot PAC-3 MSE", "category": "missile",
        "critical_elements": ["neodymium", "dysprosium", "samarium"],
        "monthly_consumption_kg": {"neodymium": 185, "dysprosium": 29, "samarium": 38},
    },
    {
        "id": "THAAD", "name": "THAAD Terminal Defense", "category": "missile",
        "critical_elements": ["neodymium", "dysprosium", "terbium", "europium"],
        "monthly_consumption_kg": {"neodymium": 210, "dysprosium": 33, "terbium": 9, "europium": 28},
    },
    {
        "id": "SM3IIA", "name": "SM-3 Block IIA", "category": "missile",
        "critical_elements": ["neodymium", "dysprosium", "samarium"],
        "monthly_consumption_kg": {"neodymium": 165, "dysprosium": 26, "samarium": 32},
    },
    {
        "id": "SM6", "name": "SM-6 Block I", "category": "missile",
        "critical_elements": ["neodymium", "dysprosium"],
        "monthly_consumption_kg": {"neodymium": 145, "dysprosium": 23},
    },
    {
        "id": "TLAM-V", "name": "Tomahawk Block V", "category": "missile",
        "critical_elements": ["neodymium", "samarium", "cerium"],
        "monthly_consumption_kg": {"neodymium": 175, "samarium": 30, "cerium": 245},
    },
    {
        "id": "JASSM-ER", "name": "JASSM-ER", "category": "missile",
        "critical_elements": ["neodymium", "dysprosium", "samarium"],
        "monthly_consumption_kg": {"neodymium": 155, "dysprosium": 24, "samarium": 28},
    },
    {
        "id": "LRASM", "name": "LRASM Long-Range Anti-Ship", "category": "missile",
        "critical_elements": ["neodymium", "dysprosium"],
        "monthly_consumption_kg": {"neodymium": 140, "dysprosium": 22},
    },
    {
        "id": "AIM120D", "name": "AIM-120D AMRAAM", "category": "missile",
        "critical_elements": ["neodymium", "samarium"],
        "monthly_consumption_kg": {"neodymium": 128, "samarium": 22},
    },
    {
        "id": "AIM9X", "name": "AIM-9X Sidewinder Block II", "category": "missile",
        "critical_elements": ["neodymium", "samarium"],
        "monthly_consumption_kg": {"neodymium": 96, "samarium": 16},
    },
    {
        "id": "AGM88E", "name": "AGM-88E AARGM-ER", "category": "missile",
        "critical_elements": ["neodymium", "dysprosium", "samarium"],
        "monthly_consumption_kg": {"neodymium": 105, "dysprosium": 16, "samarium": 18},
    },
    {
        "id": "JAV148", "name": "Javelin FGM-148", "category": "missile",
        "critical_elements": ["neodymium", "lanthanum"],
        "monthly_consumption_kg": {"neodymium": 78, "lanthanum": 52},
    },
    {
        "id": "FIM92", "name": "Stinger FIM-92", "category": "missile",
        "critical_elements": ["neodymium", "samarium"],
        "monthly_consumption_kg": {"neodymium": 65, "samarium": 11},
    },
    # ----- RADAR & SENSORS -----
    {
        "id": "APG-77", "name": "AN/APG-77 AESA (F-22)", "category": "sensor",
        "critical_elements": ["neodymium", "dysprosium", "yttrium", "cerium"],
        "monthly_consumption_kg": {"neodymium": 92, "dysprosium": 14, "yttrium": 26, "cerium": 135},
    },
    {
        "id": "APG-81", "name": "AN/APG-81 AESA (F-35)", "category": "sensor",
        "critical_elements": ["neodymium", "dysprosium", "yttrium", "cerium"],
        "monthly_consumption_kg": {"neodymium": 110, "dysprosium": 17, "yttrium": 32, "cerium": 155},
    },
    {
        "id": "APG-82", "name": "AN/APG-82(V)1 AESA (F-15E/EX)", "category": "sensor",
        "critical_elements": ["neodymium", "yttrium", "cerium"],
        "monthly_consumption_kg": {"neodymium": 88, "yttrium": 24, "cerium": 120},
    },
    {
        "id": "TPY-2", "name": "AN/TPY-2 X-Band Radar (THAAD)", "category": "sensor",
        "critical_elements": ["neodymium", "terbium", "yttrium", "cerium"],
        "monthly_consumption_kg": {"neodymium": 115, "terbium": 6, "yttrium": 38, "cerium": 170},
    },
    {
        "id": "SPY-6", "name": "SPY-6(V)1 AMDR", "category": "sensor",
        "critical_elements": ["neodymium", "dysprosium", "terbium", "yttrium"],
        "monthly_consumption_kg": {"neodymium": 145, "dysprosium": 22, "terbium": 8, "yttrium": 48},
    },
    {
        "id": "NVDS", "name": "Night Vision / GPNVG-18 (AN/PVS-14)", "category": "sensor",
        "critical_elements": ["lanthanum", "cerium", "gadolinium"],
        "monthly_consumption_kg": {"lanthanum": 320, "cerium": 485, "gadolinium": 95},
    },
    {
        "id": "TPS-80", "name": "AN/TPS-80 G/ATOR Radar", "category": "sensor",
        "critical_elements": ["neodymium", "yttrium", "cerium"],
        "monthly_consumption_kg": {"neodymium": 98, "yttrium": 28, "cerium": 140},
    },
    # ----- MUNITIONS / PRECISION -----
    {
        "id": "M982", "name": "M982 Excalibur 155mm", "category": "munition",
        "critical_elements": ["neodymium", "samarium"],
        "monthly_consumption_kg": {"neodymium": 82, "samarium": 14},
    },
    {
        "id": "GBU53B", "name": "GBU-53B StormBreaker (SDB II)", "category": "munition",
        "critical_elements": ["neodymium", "dysprosium", "erbium"],
        "monthly_consumption_kg": {"neodymium": 68, "dysprosium": 11, "erbium": 9},
    },
    {
        "id": "JDAM-ER", "name": "JDAM-ER Extended Range", "category": "munition",
        "critical_elements": ["neodymium", "praseodymium"],
        "monthly_consumption_kg": {"neodymium": 72, "praseodymium": 29},
    },
    {
        "id": "SW600", "name": "Switchblade 600 Loitering Munition", "category": "munition",
        "critical_elements": ["neodymium", "dysprosium", "lanthanum"],
        "monthly_consumption_kg": {"neodymium": 55, "dysprosium": 8, "lanthanum": 38},
    },
]

# Severity multipliers: fraction of China-dependent supply that is
# immediately unavailable.
SEVERITY_FACTORS: dict[str, float] = {
    "low": 0.30,       # partial disruption / export controls
    "moderate": 0.60,  # significant restrictions
    "high": 0.85,      # near-total export suspension
    "critical": 1.00,  # full embargo — zero new imports from China
}

# Programs whose limiting element depletes within this many months are
# counted in blast_radius.
BLAST_RADIUS_THRESHOLD_MONTHS = 36


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def _compute_element_depletion_months(
    element: str,
    severity_factor: float,
    stockpile: dict[str, dict[str, Any]] | None = None,
) -> float:
    """Return months until the shared element stockpile is exhausted.

    Under an embargo, China-sourced replenishment drops by severity_factor,
    so the effective monthly deficit = consumption * china_supply_pct * factor.
    We run down the stockpile at that net deficit rate.
    """
    data = (stockpile or STOCKPILE_DATA).get(element)
    if not data:
        return float("inf")

    current_kg: float = data["current_kg"]
    monthly_kg: float = data["monthly_consumption_kg"]
    china_pct: float = data["china_supply_pct"]

    # Monthly net consumption above what non-China sources can supply
    monthly_deficit = monthly_kg * china_pct * severity_factor
    if monthly_deficit <= 0:
        return float("inf")

    return current_kg / monthly_deficit


def _program_depletion_months(
    program: dict[str, Any],
    element_depletion: dict[str, float],
) -> dict[str, float]:
    """Return per-element depletion months for the program's critical elements."""
    return {
        elem: element_depletion.get(elem, float("inf"))
        for elem in program["critical_elements"]
    }


def run_scenario(
    impact: str = "China RE embargo",
    severity: str = "critical",
    stockpile: dict[str, dict[str, Any]] | None = None,
    programs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run a rare-earth supply disruption scenario.

    Args:
        impact:    Scenario label. Currently supports 'China RE embargo'.
        severity:  'low', 'moderate', 'high', or 'critical'.
        stockpile: Override stockpile data (defaults to STOCKPILE_DATA).
        programs:  Override program catalog (defaults to DEFENSE_PROGRAMS).

    Returns:
        {
            'blast_radius': int,           # programs with ≥1 critical element
                                           # depleting within threshold window
            'depletion_times': {           # program_id → months to first
                '<program_id>': float,     # critical element exhaustion
                ...
            },
            'scenario': str,
            'severity': str,
            'element_depletion_months': {  # per-element global depletion
                '<element>': float,
                ...
            },
            'affected_programs': [         # full detail for affected programs
                {
                    'id', 'name', 'category',
                    'limiting_element', 'limiting_months',
                    'element_breakdown': {element: months}
                },
                ...
            ],
        }

    Raises:
        ValueError: unknown severity level.
    """
    impact_lower = impact.lower().strip()
    severity_lower = severity.lower().strip()

    if severity_lower not in SEVERITY_FACTORS:
        raise ValueError(
            f"Unknown severity '{severity}'. "
            f"Valid values: {sorted(SEVERITY_FACTORS)}"
        )

    factor = SEVERITY_FACTORS[severity_lower]
    data = stockpile or STOCKPILE_DATA
    prog_list = programs or DEFENSE_PROGRAMS

    # For non-China scenarios, fall back to a generic disruption model
    if "china" not in impact_lower:
        factor = factor * 0.5  # non-China source disruptions are smaller

    # 1. Compute global element depletion times
    element_depletion: dict[str, float] = {
        elem: _compute_element_depletion_months(elem, factor, data)
        for elem in data
    }

    # 2. Evaluate each program
    affected: list[dict[str, Any]] = []
    depletion_times: dict[str, float] = {}

    for prog in prog_list:
        breakdown = _program_depletion_months(prog, element_depletion)

        # Limiting element = earliest depleting
        if not breakdown:
            continue
        limiting_elem = min(breakdown, key=lambda e: breakdown[e])
        limiting_months = breakdown[limiting_elem]
        depletion_times[prog["id"]] = round(limiting_months, 1)

        if limiting_months <= BLAST_RADIUS_THRESHOLD_MONTHS:
            affected.append(
                {
                    "id": prog["id"],
                    "name": prog["name"],
                    "category": prog["category"],
                    "limiting_element": limiting_elem,
                    "limiting_months": round(limiting_months, 1),
                    "element_breakdown": {e: round(v, 1) for e, v in breakdown.items()},
                }
            )

    # Sort affected by limiting_months ascending (worst first)
    affected.sort(key=lambda x: x["limiting_months"])

    return {
        "scenario": impact,
        "severity": severity_lower,
        "blast_radius": len(affected),
        "depletion_times": depletion_times,
        "element_depletion_months": {e: round(v, 1) for e, v in element_depletion.items()},
        "affected_programs": affected,
    }


# ---------------------------------------------------------------------------
# Dual-cascade scenario (RE + semiconductor combined)
# ---------------------------------------------------------------------------

# Map DEFENSE_PROGRAMS category → system type used by semiconductor_chain
_CATEGORY_TO_SYSTEM_TYPE: dict[str, str] = {
    "aircraft": "aviation",
    "rotary": "aviation",
    "tiltrotor": "aviation",
    "uas": "uas",
    "ground": "ground",
    "naval": "naval",
    "missile": "missile_defense",
    "sensor": "sensor",
    "munition": "munition",
}

_DAYS_PER_MONTH: float = 30.44


def dual_cascade_scenario(
    re_severity: str = "critical",
    semi_severity: str = "critical",
    stockpile: dict[str, dict[str, Any]] | None = None,
    programs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Combine rare-earth and semiconductor disruption cascades.

    Runs both supply-chain disruption models independently, then merges
    results to compute US military endurance by system type.  Endurance is
    the minimum of the two constraints — whichever supply chain fails first
    limits operational capability.

    Args:
        re_severity:   Severity for the RE embargo ('low'/'moderate'/'high'/'critical').
        semi_severity: Severity for the semiconductor disruption (same scale).
        stockpile:     Override RE stockpile data (testing).
        programs:      Override defense program catalog (testing).

    Returns:
        {
            'scenario': 'dual_cascade_re_semiconductor',
            're_severity': str,
            'semi_severity': str,
            'endurance_days': {                  # combined worst-case per type
                '<system_type>': int,
                ...                              # 7 types: aviation, naval,
            },                                   # missile_defense, ground,
            'system_detail': {                   # uas, sensor, munition
                '<system_type>': {
                    'endurance_days': int,
                    're_endurance_days': int,
                    'semi_endurance_days': int,
                    'limiting_factor': 'rare_earth' | 'semiconductor',
                },
            },
            'blast_radius': {
                're': int,
                'semiconductor': int,
            },
            're_result': dict,       # full run_scenario() output
            'semi_result': dict,     # full semiconductor_chain.run_scenario() output
        }

    Raises:
        ValueError: unknown severity for either model.
    """
    from tools.supply_chain import semiconductor_chain  # local import avoids circular dep

    # Run both disruption models
    re_result = run_scenario(
        impact="China RE embargo",
        severity=re_severity,
        stockpile=stockpile,
        programs=programs,
    )
    semi_result = semiconductor_chain.run_scenario(
        scenario="Taiwan strait contingency",
        severity=semi_severity,
    )

    prog_list = programs or DEFENSE_PROGRAMS

    # RE worst-case depletion per system type (minimum months across all programs)
    re_by_type: dict[str, float] = {}
    for prog in prog_list:
        stype = _CATEGORY_TO_SYSTEM_TYPE.get(prog["category"], prog["category"])
        months = re_result["depletion_times"].get(prog["id"], float("inf"))
        if stype not in re_by_type or months < re_by_type[stype]:
            re_by_type[stype] = months

    # Semiconductor worst-case depletion per system type (already per-type)
    semi_by_type: dict[str, float] = semi_result["depletion_months_by_system"]

    # Merge: combined endurance = min(RE, semi) per type
    all_types = sorted(set(re_by_type) | set(semi_by_type))
    system_detail: dict[str, dict[str, Any]] = {}
    for stype in all_types:
        re_mo = re_by_type.get(stype, float("inf"))
        semi_mo = semi_by_type.get(stype, float("inf"))
        combined_mo = min(re_mo, semi_mo)
        limiting = "rare_earth" if re_mo <= semi_mo else "semiconductor"
        system_detail[stype] = {
            "endurance_days": round(combined_mo * _DAYS_PER_MONTH),
            "re_endurance_days": (
                round(re_mo * _DAYS_PER_MONTH) if re_mo < float("inf") else None
            ),
            "semi_endurance_days": (
                round(semi_mo * _DAYS_PER_MONTH) if semi_mo < float("inf") else None
            ),
            "limiting_factor": limiting,
        }

    return {
        "scenario": "dual_cascade_re_semiconductor",
        "re_severity": re_severity.lower().strip(),
        "semi_severity": semi_severity.lower().strip(),
        "endurance_days": {k: v["endurance_days"] for k, v in system_detail.items()},
        "system_detail": system_detail,
        "blast_radius": {
            "re": re_result["blast_radius"],
            "semiconductor": semi_result["blast_radius"],
        },
        "re_result": re_result,
        "semi_result": semi_result,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rare Earth Element Supply Cascade Analyzer"
    )
    parser.add_argument(
        "--impact",
        default="China RE embargo",
        help="Scenario name (default: 'China RE embargo')",
    )
    parser.add_argument(
        "--severity",
        default="critical",
        choices=list(SEVERITY_FACTORS),
        help="Disruption severity level (default: critical)",
    )
    parser.add_argument(
        "--list-programs",
        action="store_true",
        help="List all tracked defense programs and exit",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    if args.list_programs:
        rows = [{"id": p["id"], "name": p["name"], "category": p["category"]} for p in DEFENSE_PROGRAMS]
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            print(f"{'ID':<14} {'Category':<12} Name")
            print("-" * 72)
            for r in rows:
                print(f"{r['id']:<14} {r['category']:<12} {r['name']}")
        return

    try:
        result = run_scenario(impact=args.impact, severity=args.severity)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(result, indent=2))
        return

    # Human-readable output
    print(f"Scenario : {result['scenario']}")
    print(f"Severity : {result['severity'].upper()}")
    print(f"Blast Radius : {result['blast_radius']} affected programs")
    print()
    print("Element Depletion Timeline:")
    for elem, months in sorted(result["element_depletion_months"].items(), key=lambda x: x[1]):
        bar_len = min(40, max(1, int(months / 3)))
        bar = "#" * bar_len
        label = f"{months:.1f}mo" if months < 1000 else "  >83yr"
        print(f"  {elem:<16} [{bar:<40}] {label}")
    print()
    print(f"Affected Programs ({result['blast_radius']}) — sorted by time to first depletion:")
    print(f"  {'ID':<14} {'Cat':<10} {'Limiting Element':<18} Months")
    print("  " + "-" * 60)
    for p in result["affected_programs"]:
        print(f"  {p['id']:<14} {p['category']:<10} {p['limiting_element']:<18} {p['limiting_months']:.1f}")


if __name__ == "__main__":
    main()
