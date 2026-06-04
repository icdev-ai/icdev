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

# CLS Compliance Score — AI Data Warehouse posture assessment
CLS_COMPLIANCE_SCORE = "cls_compliance_score"

CLS_ASSESSMENT_CHECKS = [
    "no_persistent_ai_warehouse_access",
    "cls_native_enforcement_active",
    "uuid_grounding_enabled",
    "ttl_purge_registered",
    "stateless_context_injection",
    "ghost_data_prevention_configured",
]

CLS_CHECK_WEIGHTS = {
    "no_persistent_ai_warehouse_access": 0.25,
    "cls_native_enforcement_active": 0.20,
    "uuid_grounding_enabled": 0.20,
    "ttl_purge_registered": 0.15,
    "stateless_context_injection": 0.10,
    "ghost_data_prevention_configured": 0.10,
}

CLS_SCORE_THRESHOLDS = {
    "green": 0.80,
    "yellow": 0.50,
}
