"""ZTA canonical scenario library."""

from __future__ import annotations

SCENARIOS: dict[str, dict] = {
    "SCENARIO_AUTHORIZED_COI": {
        "id": "SCENARIO_AUTHORIZED_COI",
        "label": "Authorized COI Access",
        "description": "US/SECRET/COI_ALPHA principal accessing COI_ALPHA non-ECI resource",
        "expected_decision": "PERMIT",
        "mission_rationale": "Standard mission workflow for cleared COI_ALPHA member",
        "principal_config": {
            "citizenship": "US",
            "clearance_level": "SECRET",
            "cois": ["COI_ALPHA"],
            "roles": ["ANALYST"],
        },
        "resource_config": {
            "resource_id": "res-alpha-001",
            "is_eci": False,
            "cois": ["COI_ALPHA"],
            "classification": "SECRET",
            "ownership": "US",
        },
        "action": "read",
        "environment": {"network": "SIPR", "location": "CONUS"},
    },
    "SCENARIO_ECI_SPILLAGE_PREVENTION": {
        "id": "SCENARIO_ECI_SPILLAGE_PREVENTION",
        "label": "ECI Spillage Prevention",
        "description": "FVEY partner attempting to access ECI resource — export control DENY",
        "expected_decision": "DENY",
        "mission_rationale": "Prevents unauthorized ECI exposure to foreign nationals",
        "principal_config": {
            "citizenship": "FVEY",
            "clearance_level": "TS//SCI",
            "cois": ["COI_ALPHA"],
            "roles": ["ANALYST"],
        },
        "resource_config": {
            "resource_id": "res-eci-alpha-007",
            "is_eci": True,
            "cois": ["COI_ALPHA"],
            "classification": "TS//SCI",
            "ownership": "US",
        },
        "action": "read",
        "environment": {"network": "JWICS", "location": "CONUS"},
    },
    "SCENARIO_CROSS_COI_VIOLATION": {
        "id": "SCENARIO_CROSS_COI_VIOLATION",
        "label": "Cross-COI Violation",
        "description": "COI_BRAVO member attempting COI_ALPHA resource — compartmentalization DENY",
        "expected_decision": "DENY",
        "mission_rationale": "Enforces COI compartmentalization — BRAVO cannot access ALPHA",
        "principal_config": {
            "citizenship": "US",
            "clearance_level": "TS//SCI",
            "cois": ["COI_BRAVO"],
            "roles": ["ANALYST"],
        },
        "resource_config": {
            "resource_id": "res-alpha-002",
            "is_eci": False,
            "cois": ["COI_ALPHA"],
            "classification": "SECRET",
            "ownership": "US",
        },
        "action": "read",
        "environment": {"network": "SIPR", "location": "CONUS"},
    },
    "SCENARIO_BREAK_GLASS": {
        "id": "SCENARIO_BREAK_GLASS",
        "label": "Break-Glass Emergency Access",
        "description": "US/TS//SCI with ECI_EMERGENCY_ACCESS role — break-glass PERMIT with mandatory audit",
        "expected_decision": "PERMIT",
        "mission_rationale": "Emergency override for mission-critical ECI access with supervisor approval",
        "principal_config": {
            "citizenship": "US",
            "clearance_level": "TS//SCI",
            "cois": ["COI_ALPHA"],
            "roles": ["ANALYST", "ECI_EMERGENCY_ACCESS"],
        },
        "resource_config": {
            "resource_id": "res-eci-critical-001",
            "is_eci": True,
            "cois": ["COI_ALPHA"],
            "classification": "TS//SCI",
            "ownership": "US",
        },
        "action": "read",
        "environment": {"network": "JWICS", "location": "CONUS", "emergency": True},
    },
}


def list_scenarios() -> list[dict]:
    return [
        {
            "id": s["id"],
            "label": s["label"],
            "description": s["description"],
            "expected_decision": s["expected_decision"],
            "principal_config": s.get("principal_config", {}),
            "resource_config": s.get("resource_config", {}),
        }
        for s in SCENARIOS.values()
    ]


def get_scenario(scenario_id: str) -> dict | None:
    return SCENARIOS.get(scenario_id)
