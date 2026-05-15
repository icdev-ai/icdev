# CUI // SP-CTI
"""AI GameDay Ontology — strategy, security, war, and geospatial concept mappings.

Namespaces:
  strategy:   Military strategy and operational concepts
  security:   Cybersecurity, threat, and defensive concepts
  war:        Warfighting and military unit concepts
  geospatial: Geographic and spatial intelligence concepts
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Namespaces
# ---------------------------------------------------------------------------

ONTOLOGY_NAMESPACES = {
    "strategy": "http://icdev.io/ontology/strategy#",
    "security": "http://icdev.io/ontology/security#",
    "war": "http://icdev.io/ontology/war#",
    "geospatial": "http://icdev.io/ontology/geospatial#",
}


def _iri(prefix: str, local: str) -> str:
    return f"{ONTOLOGY_NAMESPACES[prefix]}{local}"


# ---------------------------------------------------------------------------
# Scenario → ontology concept mapping
# ---------------------------------------------------------------------------

SCENARIO_ONTOLOGY: dict[str, dict[str, Any]] = {
    "ai_gameday": {
        "classes": [
            {"prefix": "strategy", "concept": "JointOperation", "iri": _iri("strategy", "JointOperation")},
            {"prefix": "security", "concept": "CyberOperation", "iri": _iri("security", "CyberOperation")},
        ],
        "description": "AI-enabled joint operations tabletop exercise",
    },
    "hunt_the_fleet": {
        "classes": [
            {"prefix": "strategy", "concept": "NavalOperation", "iri": _iri("strategy", "NavalOperation")},
            {"prefix": "geospatial", "concept": "MaritimeZone", "iri": _iri("geospatial", "MaritimeZone")},
            {"prefix": "strategy", "concept": "AntiAccessAreaDenial", "iri": _iri("strategy", "AntiAccessAreaDenial")},
        ],
        "description": "Naval hunt-and-target exercise in contested maritime zone",
    },
    "red_team_the_ai": {
        "classes": [
            {"prefix": "security", "concept": "CyberOperation", "iri": _iri("security", "CyberOperation")},
            {"prefix": "security", "concept": "AdversarialAssessment", "iri": _iri("security", "AdversarialAssessment")},
            {"prefix": "security", "concept": "ThreatModeling", "iri": _iri("security", "ThreatModeling")},
        ],
        "description": "Adversarial AI red-team assessment exercise",
    },
    "meridian": {
        "classes": [
            {"prefix": "strategy", "concept": "CrisisManagement", "iri": _iri("strategy", "CrisisManagement")},
            {"prefix": "security", "concept": "AIGovernance", "iri": _iri("security", "AIGovernance")},
            {"prefix": "security", "concept": "SupplyChainRisk", "iri": _iri("security", "SupplyChainRisk")},
        ],
        "description": "AI governance crisis and leadership decision exercise",
    },
    "forge_ascent": {
        "classes": [
            {"prefix": "security", "concept": "MLDevOps", "iri": _iri("security", "MLDevOps")},
            {"prefix": "strategy", "concept": "IncidentResponse", "iri": _iri("strategy", "IncidentResponse")},
            {"prefix": "security", "concept": "ZeroTrust", "iri": _iri("security", "ZeroTrust")},
        ],
        "description": "ML engineering and DevOps incident response exercise",
    },
    "interagency": {
        "classes": [
            {"prefix": "strategy", "concept": "JointOperation", "iri": _iri("strategy", "JointOperation")},
            {"prefix": "security", "concept": "InformationOperation", "iri": _iri("security", "InformationOperation")},
            {"prefix": "strategy", "concept": "CivilMilitaryCooperation", "iri": _iri("strategy", "CivilMilitaryCooperation")},
        ],
        "description": "Interagency civil-military joint operations exercise",
    },
    "taiwan_strait_amphibious_assault": {
        "classes": [
            {"prefix": "strategy", "concept": "AmphibiousOperation", "iri": _iri("strategy", "AmphibiousOperation")},
            {"prefix": "geospatial", "concept": "LandingZone", "iri": _iri("geospatial", "LandingZone")},
        ],
        "description": "Taiwan Strait amphibious assault tabletop exercise",
    },
    "cyber_hunt": {
        "classes": [
            {"prefix": "security", "concept": "CyberOperation", "iri": _iri("security", "CyberOperation")},
            {"prefix": "war", "concept": "BehaviorProfile", "iri": _iri("war", "BehaviorProfile")},
        ],
        "description": "Cyber hunt and behavioral profiling exercise",
    },
}


def resolve_scenario_ontology(slug: str) -> dict[str, Any]:
    """Return ontology tags for a scenario slug, or empty defaults."""
    return SCENARIO_ONTOLOGY.get(slug, {"classes": [], "description": ""})


# ---------------------------------------------------------------------------
# Role → ontology concept mapping
# ---------------------------------------------------------------------------

ROLE_ONTOLOGY: dict[str, dict[str, Any]] = {
    # Military / command roles
    "captain": {
        "classes": [
            {"prefix": "war", "concept": "MilitaryUnit", "iri": _iri("war", "MilitaryUnit"), "role": "commander"},
        ],
    },
    "military_intel": {
        "classes": [
            {"prefix": "war", "concept": "MilitaryUnit", "iri": _iri("war", "MilitaryUnit"), "role": "intelligence"},
            {"prefix": "security", "concept": "CyberOperator", "iri": _iri("security", "CyberOperator"), "role": "analyst"},
        ],
    },
    "signals_intel": {
        "classes": [
            {"prefix": "war", "concept": "MilitaryUnit", "iri": _iri("war", "MilitaryUnit"), "role": "signals"},
            {"prefix": "security", "concept": "CyberOperator", "iri": _iri("security", "CyberOperator"), "role": "analyst"},
        ],
    },
    "sonar_analyst": {
        "classes": [
            {"prefix": "war", "concept": "MilitaryUnit", "iri": _iri("war", "MilitaryUnit"), "role": "analyst"},
            {"prefix": "geospatial", "concept": "AcousticAnalyst", "iri": _iri("geospatial", "AcousticAnalyst")},
        ],
    },
    "imagery_analyst": {
        "classes": [
            {"prefix": "war", "concept": "MilitaryUnit", "iri": _iri("war", "MilitaryUnit"), "role": "analyst"},
            {"prefix": "geospatial", "concept": "ImageryAnalyst", "iri": _iri("geospatial", "ImageryAnalyst")},
        ],
    },
    "leadership": {
        "classes": [
            {"prefix": "war", "concept": "MilitaryUnit", "iri": _iri("war", "MilitaryUnit"), "role": "executive"},
            {"prefix": "strategy", "concept": "DecisionAuthority", "iri": _iri("strategy", "DecisionAuthority")},
        ],
    },
    "pm": {
        "classes": [
            {"prefix": "war", "concept": "MilitaryUnit", "iri": _iri("war", "MilitaryUnit"), "role": "planner"},
            {"prefix": "strategy", "concept": "ProgramManager", "iri": _iri("strategy", "ProgramManager")},
        ],
    },
    "general_counsel": {
        "classes": [
            {"prefix": "war", "concept": "MilitaryUnit", "iri": _iri("war", "MilitaryUnit"), "role": "legal"},
            {"prefix": "strategy", "concept": "LegalAdvisor", "iri": _iri("strategy", "LegalAdvisor")},
        ],
    },
    "legal_advisor": {
        "classes": [
            {"prefix": "war", "concept": "MilitaryUnit", "iri": _iri("war", "MilitaryUnit"), "role": "legal"},
            {"prefix": "strategy", "concept": "LegalAdvisor", "iri": _iri("strategy", "LegalAdvisor")},
        ],
    },
    # Civilian / contractor roles
    "civilian_analyst": {
        "classes": [
            {"prefix": "war", "concept": "MilitaryUnit", "iri": _iri("war", "MilitaryUnit"), "role": "analyst"},
            {"prefix": "security", "concept": "CyberOperator", "iri": _iri("security", "CyberOperator"), "role": "analyst"},
        ],
    },
    "contractor_swe": {
        "classes": [
            {"prefix": "security", "concept": "CyberOperator", "iri": _iri("security", "CyberOperator"), "role": "engineer"},
        ],
    },
    "swe": {
        "classes": [
            {"prefix": "security", "concept": "CyberOperator", "iri": _iri("security", "CyberOperator"), "role": "engineer"},
        ],
    },
    # Security / cyber roles
    "ciso_lead": {
        "classes": [
            {"prefix": "security", "concept": "CyberOperator", "iri": _iri("security", "CyberOperator"), "role": "security_lead"},
            {"prefix": "security", "concept": "Defender", "iri": _iri("security", "Defender")},
        ],
    },
    "ciso": {
        "classes": [
            {"prefix": "security", "concept": "CyberOperator", "iri": _iri("security", "CyberOperator"), "role": "security_lead"},
            {"prefix": "security", "concept": "Defender", "iri": _iri("security", "Defender")},
        ],
    },
    "cyber_analyst": {
        "classes": [
            {"prefix": "security", "concept": "CyberOperator", "iri": _iri("security", "CyberOperator"), "role": "analyst"},
            {"prefix": "security", "concept": "Defender", "iri": _iri("security", "Defender")},
        ],
    },
    "network_engineer": {
        "classes": [
            {"prefix": "security", "concept": "CyberOperator", "iri": _iri("security", "CyberOperator"), "role": "network"},
            {"prefix": "security", "concept": "Defender", "iri": _iri("security", "Defender")},
        ],
    },
    "security_engineer": {
        "classes": [
            {"prefix": "security", "concept": "CyberOperator", "iri": _iri("security", "CyberOperator"), "role": "security"},
            {"prefix": "security", "concept": "Defender", "iri": _iri("security", "Defender")},
        ],
    },
    # Data / ML roles
    "data_scientist": {
        "classes": [
            {"prefix": "security", "concept": "CyberOperator", "iri": _iri("security", "CyberOperator"), "role": "analyst"},
            {"prefix": "strategy", "concept": "DataScientist", "iri": _iri("strategy", "DataScientist")},
        ],
    },
    "ml_engineer": {
        "classes": [
            {"prefix": "security", "concept": "CyberOperator", "iri": _iri("security", "CyberOperator"), "role": "engineer"},
            {"prefix": "strategy", "concept": "MLEngineer", "iri": _iri("strategy", "MLEngineer")},
        ],
    },
    "devops_engineer": {
        "classes": [
            {"prefix": "security", "concept": "CyberOperator", "iri": _iri("security", "CyberOperator"), "role": "engineer"},
            {"prefix": "strategy", "concept": "DevOpsEngineer", "iri": _iri("strategy", "DevOpsEngineer")},
        ],
    },
    "cto": {
        "classes": [
            {"prefix": "security", "concept": "CyberOperator", "iri": _iri("security", "CyberOperator"), "role": "technical_lead"},
            {"prefix": "strategy", "concept": "TechnicalAuthority", "iri": _iri("strategy", "TechnicalAuthority")},
        ],
    },
    # Red-team roles
    "blue_architect": {
        "classes": [
            {"prefix": "security", "concept": "Defender", "iri": _iri("security", "Defender"), "role": "architect"},
            {"prefix": "security", "concept": "BlueTeam", "iri": _iri("security", "BlueTeam")},
        ],
    },
    "blue_defender": {
        "classes": [
            {"prefix": "security", "concept": "Defender", "iri": _iri("security", "Defender"), "role": "security"},
            {"prefix": "security", "concept": "BlueTeam", "iri": _iri("security", "BlueTeam")},
        ],
    },
    "red_analyst": {
        "classes": [
            {"prefix": "security", "concept": "ThreatActor", "iri": _iri("security", "ThreatActor"), "role": "analyst"},
            {"prefix": "security", "concept": "RedTeam", "iri": _iri("security", "RedTeam")},
        ],
    },
    "red_exploiter": {
        "classes": [
            {"prefix": "security", "concept": "ThreatActor", "iri": _iri("security", "ThreatActor"), "role": "exploiter"},
            {"prefix": "security", "concept": "RedTeam", "iri": _iri("security", "RedTeam")},
        ],
    },
    # Analyst roles
    "analyst": {
        "classes": [
            {"prefix": "war", "concept": "MilitaryUnit", "iri": _iri("war", "MilitaryUnit"), "role": "analyst"},
            {"prefix": "security", "concept": "CyberOperator", "iri": _iri("security", "CyberOperator"), "role": "analyst"},
        ],
    },
    # GameDay League specific roles
    "blue_team_commander": {
        "classes": [
            {"prefix": "war", "concept": "MilitaryUnit", "iri": _iri("war", "MilitaryUnit"), "role": "commander"},
            {"prefix": "security", "concept": "BlueTeam", "iri": _iri("security", "BlueTeam")},
        ],
    },
    "red_team_hacker": {
        "classes": [
            {"prefix": "security", "concept": "ThreatActor", "iri": _iri("security", "ThreatActor")},
            {"prefix": "security", "concept": "RedTeam", "iri": _iri("security", "RedTeam")},
        ],
    },
}


def resolve_role_ontology(role_id: str) -> dict[str, Any]:
    """Return ontology tags for a role id, or empty defaults."""
    return ROLE_ONTOLOGY.get(role_id, {"classes": []})


# ---------------------------------------------------------------------------
# Game event → ontology concept mapping
# ---------------------------------------------------------------------------

EVENT_ONTOLOGY: dict[str, dict[str, Any]] = {
    "move_event": {
        "parent": {"prefix": "strategy", "concept": "GameEvent", "iri": _iri("strategy", "GameEvent")},
        "subclass": {"prefix": "strategy", "concept": "MoveEvent", "iri": _iri("strategy", "MoveEvent")},
        "description": "A positional or strategic move during gameplay",
    },
    "attack_event": {
        "parent": {"prefix": "strategy", "concept": "GameEvent", "iri": _iri("strategy", "GameEvent")},
        "subclass": {"prefix": "strategy", "concept": "AttackEvent", "iri": _iri("strategy", "AttackEvent")},
        "description": "An offensive action or strike event during gameplay",
    },
    "intel_event": {
        "parent": {"prefix": "strategy", "concept": "GameEvent", "iri": _iri("strategy", "GameEvent")},
        "subclass": {"prefix": "strategy", "concept": "IntelEvent", "iri": _iri("strategy", "IntelEvent")},
        "description": "An intelligence collection or assessment event",
    },
    "inject_dispatch": {
        "parent": {"prefix": "strategy", "concept": "GameEvent", "iri": _iri("strategy", "GameEvent")},
        "subclass": {"prefix": "strategy", "concept": "InjectEvent", "iri": _iri("strategy", "InjectEvent")},
        "description": "An inject dispatched to teams during a session",
    },
    "response_submit": {
        "parent": {"prefix": "strategy", "concept": "GameEvent", "iri": _iri("strategy", "GameEvent")},
        "subclass": {"prefix": "strategy", "concept": "ResponseEvent", "iri": _iri("strategy", "ResponseEvent")},
        "description": "A team response submitted to an inject",
    },
    "score_update": {
        "parent": {"prefix": "strategy", "concept": "GameEvent", "iri": _iri("strategy", "GameEvent")},
        "subclass": {"prefix": "strategy", "concept": "ScoreEvent", "iri": _iri("strategy", "ScoreEvent")},
        "description": "A scoreboard or leaderboard update event",
    },
    "receipt_log": {
        "parent": {"prefix": "strategy", "concept": "GameEvent", "iri": _iri("strategy", "GameEvent")},
        "subclass": {"prefix": "strategy", "concept": "ReceiptEvent", "iri": _iri("strategy", "ReceiptEvent")},
        "description": "An AI tool receipt logged by a team",
    },
    "consequence_trigger": {
        "parent": {"prefix": "strategy", "concept": "GameEvent", "iri": _iri("strategy", "GameEvent")},
        "subclass": {"prefix": "strategy", "concept": "ConsequenceEvent", "iri": _iri("strategy", "ConsequenceEvent")},
        "description": "A world-state consequence triggered by team actions",
    },
}


def resolve_event_ontology(event_type: str) -> dict[str, Any]:
    """Return ontology tags for an event type, or empty defaults."""
    return EVENT_ONTOLOGY.get(event_type, {"parent": {}, "subclass": {}, "description": ""})


# ---------------------------------------------------------------------------
# Filtering helpers
# ---------------------------------------------------------------------------

ONTOLOGY_FILTERS = {
    "geospatial": {
        "label": "Geospatial Events",
        "concepts": ["MaritimeZone", "LandingZone", "ImageryAnalyst", "AcousticAnalyst"],
        "event_types": ["intel_event", "move_event"],
    },
    "cyber": {
        "label": "Cyber Events",
        "concepts": ["CyberOperation", "CyberOperator", "ThreatActor", "Defender",
                      "AdversarialAssessment", "ThreatModeling", "AIGovernance", "ZeroTrust",
                      "MLDevOps", "InformationOperation", "BlueTeam", "RedTeam"],
        "event_types": ["attack_event", "response_submit", "receipt_log"],
    },
    "military": {
        "label": "Military Events",
        "concepts": ["MilitaryUnit", "JointOperation", "NavalOperation",
                      "AntiAccessAreaDenial", "CrisisManagement", "IncidentResponse",
                      "CivilMilitaryCooperation", "DecisionAuthority", "ProgramManager",
                      "LegalAdvisor", "DataScientist", "MLEngineer", "DevOpsEngineer",
                      "TechnicalAuthority", "AmphibiousOperation", "BehaviorProfile"],
        "event_types": ["move_event", "attack_event", "intel_event", "inject_dispatch"],
    },
    "strategy": {
        "label": "Strategy Events",
        "concepts": ["GameEvent", "MoveEvent", "AttackEvent", "IntelEvent",
                      "InjectEvent", "ResponseEvent", "ScoreEvent", "ReceiptEvent",
                      "ConsequenceEvent", "JointOperation", "NavalOperation",
                      "CrisisManagement", "IncidentResponse", "CivilMilitaryCooperation",
                      "AmphibiousOperation"],
        "event_types": list(EVENT_ONTOLOGY.keys()),
    },
}


def filter_by_ontology_class(
    items: list[dict],
    ontology_class: str | None = None,
    exclude_class: str | None = None,
    key: str = "ontology_tags",
) -> list[dict]:
    """Filter a list of dicts by ontology class tag.

    Args:
        items: List of dicts expected to contain ontology tag metadata.
        ontology_class: One of the ONTOLOGY_FILTERS keys or a specific concept name.
            If provided, only items matching this class are included.
        exclude_class: One of the ONTOLOGY_FILTERS keys or a specific concept name.
            If provided, items matching this class are excluded.
        key: Dict key where ontology tags live.
    """

    def _matches(item: dict, cls: str) -> bool:
        tags = item.get(key, {})
        classes = tags.get("classes", [])
        event_type = tags.get("event_type", item.get("event_type", ""))
        if cls not in ONTOLOGY_FILTERS:
            return any(
                cls in (c.get("concept") or "")
                for c in classes
            )
        cfg = ONTOLOGY_FILTERS[cls]
        allowed_concepts = set(cfg.get("concepts", []))
        allowed_events = set(cfg.get("event_types", []))
        for c in classes:
            if c.get("concept") in allowed_concepts:
                return True
        return event_type in allowed_events

    out = items
    if ontology_class is not None:
        out = [item for item in out if _matches(item, ontology_class)]
    if exclude_class is not None:
        out = [item for item in out if not _matches(item, exclude_class)]
    return out


def get_all_ontology_concepts() -> list[dict]:
    """Return a flat list of all defined ontology concepts for indexing."""
    concepts = []
    for slug, data in SCENARIO_ONTOLOGY.items():
        for c in data.get("classes", []):
            concepts.append({"domain": "scenario", "slug": slug, **c})
    for role_id, data in ROLE_ONTOLOGY.items():
        for c in data.get("classes", []):
            concepts.append({"domain": "role", "role_id": role_id, **c})
    for event_type, data in EVENT_ONTOLOGY.items():
        for key in ("parent", "subclass"):
            if key in data:
                concepts.append({"domain": "event", "event_type": event_type, **data[key]})
    return concepts
