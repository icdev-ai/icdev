"""ZTA LAC Simulator constants."""

CLEARANCE_ORDER = ["UNCLASS", "CUI", "SECRET", "TS", "TS//SCI"]

COI_TYPES = {
    "COI_ALPHA": {"classification": "TS//SCI", "citizenship_required": ["US"]},
    "COI_BRAVO": {"classification": "SECRET", "citizenship_required": ["US", "FVEY"]},
    "COI_CHARLIE": {"classification": "CUI", "citizenship_required": ["US", "FVEY", "NATO"]},
    "COI_DELTA": {"classification": "TS", "citizenship_required": ["US"]},
    "COI_ECHO": {"classification": "SECRET", "citizenship_required": ["US", "FVEY", "COALITION"]},
}

ECI_RESTRICTIONS = {
    "citizenship_allowed": ["US"],
    "citizenship_denied": ["FVEY", "NATO", "COALITION"],
    "break_glass": {
        "role_required": "ECI_EMERGENCY_ACCESS",
        "max_duration_hours": 4,
        "supervisor_approval_required": True,
    },
}

LAC_DECISION = {"PERMIT": "PERMIT", "DENY": "DENY"}

LAC_SIMULATION_STATES = ["configured", "simulating", "completed", "error"]

TWIN_PHASES = [
    "scope_and_ingestion",
    "model_development",
    "state_sync",
    "simulation_execution",
]
