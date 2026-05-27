# CUI // SP-CTI — Mission Canvas Constants
"""Mission Canvas module-level constants."""

CANVAS_NAME = "mission_canvas"
CANVAS_LABEL = "Mission Control"
CANVAS_ICON = "MC"

# Intent rules for IQE / natural-language routing
INTENT_RULES = [
    {
        "intent": "mission_status",
        "patterns": ["mission status", "program status", "operational picture", "common operating picture"],
        "description": "Return the current mission/program operational status.",
    },
    {
        "intent": "agent_orchestration",
        "patterns": ["deploy agents", "run agent team", "agent mission", "orchestrate"],
        "description": "Trigger agent team orchestration for a mission.",
    },
    {
        "intent": "digital_twin",
        "patterns": ["digital twin", "twin snapshot", "system twin", "environment twin"],
        "description": "Interact with the living digital twin.",
    },
    {
        "intent": "evidence_trace",
        "patterns": ["trace evidence", "provenance", "source attribution", "audit trail"],
        "description": "Retrieve traceable source-attributed evidence.",
    },
    {
        "intent": "security_posture",
        "patterns": ["security posture", "zta score", "fedramp status", "zero trust"],
        "description": "Return current ZTA / FedRAMP security posture.",
    },
    {
        "intent": "conflict_detect",
        "patterns": ["detect conflict", "policy conflict", "data conflict", "merge conflict"],
        "description": "Detect and surface conflicts across data or policy.",
    },
    {
        "intent": "portfolio_optimize",
        "patterns": ["portfolio scale", "capacity plan", "optimize deployment", "scaling analysis"],
        "description": "Run portfolio scaling and optimization simulation.",
    },
    {
        "intent": "cicd_status",
        "patterns": ["cicd status", "pipeline status", "deployment queue", "build health"],
        "description": "Return current DevSecOps CI/CD pipeline health.",
    },
    {
        "intent": "ai_trust",
        "patterns": ["ai trust", "model governance", "confabulation check", "ai audit"],
        "description": "Run AI trust / governance checks.",
    },
    {
        "intent": "spatial_temporal",
        "patterns": ["spatial analysis", "temporal correlation", "geospatial", "time series"],
        "description": "Perform spatial and temporal correlation analysis.",
    },
    {
        "intent": "discovery",
        "patterns": ["discover components", "auto discovery", "system map", "health probe"],
        "description": "Run automated discovery and health probes.",
    },
    {
        "intent": "narrative",
        "patterns": ["mission narrative", "sitrep", "plain english summary", "briefing"],
        "description": "Generate plain-English mission-ready narrative output.",
    },
]

OBJECT_TYPES = {
    "zones": [
        {"type": "zone-situation", "label": "Situation", "icon": "SIT", "desc": "Digital twin + real-time correlation + spatial/temporal"},
        {"type": "zone-intelligence", "label": "Intelligence", "icon": "INT", "desc": "Evidence + narrative + discovery"},
        {"type": "zone-execution", "label": "Execution", "icon": "EXE", "desc": "Agent orchestration + CI/CD + portfolio"},
        {"type": "zone-security", "label": "Security", "icon": "SEC", "desc": "ZTA + AI trust + conflict resolution"},
    ],
    "status_indicators": [
        {"type": "status-green", "label": "Nominal", "color": "#27ae60"},
        {"type": "status-amber", "label": "Degraded", "color": "#e67e22"},
        {"type": "status-red", "label": "Critical", "color": "#e74c3c"},
        {"type": "status-blue", "label": "Standby", "color": "#3498db"},
    ],
}

_MC = "https://icdev.dev/ontology/mission#"

MISSION_ONTOLOGY_MAP: dict[str, str] = {
    # Operational zones
    "zone-situation":   f"{_MC}Zone.Situation",
    "zone-intelligence":f"{_MC}Zone.Intelligence",
    "zone-execution":   f"{_MC}Zone.Execution",
    "zone-security":    f"{_MC}Zone.Security",
    # Status indicators
    "status-green":     f"{_MC}Status.Nominal",
    "status-amber":     f"{_MC}Status.Degraded",
    "status-red":       f"{_MC}Status.Critical",
    "status-blue":      f"{_MC}Status.Standby",
}
