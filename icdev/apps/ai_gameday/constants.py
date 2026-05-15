# CUI // SP-CTI
"""AI GameDay app constants."""

APP_NAME = "AI GameDay"
APP_SLUG = "ai_gameday"
SCENARIO_SLUG = "ai_gameday"

# XP level thresholds (mirrors FORGE Academy)
LEVELS = [
    {"slug": "recruit",    "label": "Recruit",    "min_xp": 0,     "icon": "⬡", "color": "#6c6c80"},
    {"slug": "operative",  "label": "Operative",  "min_xp": 500,   "icon": "◈", "color": "#4a90d9"},
    {"slug": "specialist", "label": "Specialist", "min_xp": 2000,  "icon": "◆", "color": "#00D4FF"},
    {"slug": "architect",  "label": "Architect",  "min_xp": 5000,  "icon": "❖", "color": "#FF6B35"},
    {"slug": "sensei",     "label": "Sensei",     "min_xp": 10000, "icon": "★", "color": "#FFB800"},
]

# Inject type categories for scenario builder
INJECT_TYPES = [
    {"id": "intel_assessment",   "label": "Intel Assessment",   "icon": "🔍"},
    {"id": "coa_recommendation", "label": "COA Recommendation", "icon": "🗺️"},
    {"id": "ir_response",        "label": "IR Response",        "icon": "🚨"},
    {"id": "ai_build_sprint",    "label": "AI Build Sprint",    "icon": "🤖"},
    {"id": "strategic_brief",    "label": "Strategic Brief",    "icon": "📋"},
    {"id": "custom",             "label": "Custom",             "icon": "⚙️"},
    {"id": "aadc_design_challenge", "label": "AADC Design Challenge", "icon": "🛡️"},
]

# Ontology event types surfaced in the GameDay event stream
EVENT_ONTOLOGY_TYPES = [
    {"id": "intel",      "label": "Intel Assessment",      "icon": "🔍", "class": "strategy:IntelligenceAssessment"},
    {"id": "coa",        "label": "COA Recommendation",   "icon": "🗺️", "class": "strategy:CourseOfAction"},
    {"id": "ir",         "label": "Incident Response",    "icon": "🚨", "class": "security:IncidentResponse"},
    {"id": "build",      "label": "AI Build Sprint",      "icon": "🤖", "class": "security:MLDevOps"},
    {"id": "strategic",  "label": "Strategic Brief",        "icon": "📋", "class": "strategy:JointOperation"},
    {"id": "aadc",       "label": "AADC Design Challenge",  "icon": "🛡️", "class": "security:ZeroTrust"},
    {"id": "custom",     "label": "Custom Inject",        "icon": "⚙️", "class": "strategy:CustomEvent"},
]

# Scoreboard filter categories for ontology-based filtering
SCOREBOARD_ONTOLOGY_FILTERS = [
    {"id": "all",        "label": "All Events",     "filter": None},
    {"id": "strategy",   "label": "Strategy",       "filter": "strategy"},
    {"id": "security",   "label": "Security",       "filter": "security"},
    {"id": "war",        "label": "Warfighting",    "filter": "war"},
    {"id": "geospatial", "label": "Geospatial",     "filter": "geospatial"},
]

# ICDEV tool slugs surfaced to players in the "Link AI Tool" picker
AI_TOOLS_CATALOG = [
    {"slug": "strategos.oracle",          "label": "Strategos Oracle",       "endpoint": "/api/strategos/oracle",           "icon": "🔮"},
    {"slug": "strategos.signals",         "label": "Signal Prioritizer",     "endpoint": "/api/strategos/signals",          "icon": "📡"},
    {"slug": "strategos.wargame.coa",     "label": "Wargame COA Generator",  "endpoint": "/api/strategos/wargame",          "icon": "🗺️"},
    {"slug": "strategos.wargame.ooda",    "label": "OODA Tempo Analysis",    "endpoint": "/api/strategos/wargame/{id}/ooda", "icon": "⏱️"},
    {"slug": "strategos.iw.composite",   "label": "IW Composite (PMESII)",  "endpoint": "/api/strategos/iw/composite",     "icon": "📊"},
    {"slug": "strategos.simulate",        "label": "Supply Chain Simulate",  "endpoint": "/api/strategos/simulate/run",     "icon": "🔁"},
    {"slug": "finetune.deploy",           "label": "Fine-Tune Deploy",       "endpoint": "/api/finetune/jobs",              "icon": "🧠"},
    {"slug": "knowledge.search",          "label": "Knowledge RAG Search",   "endpoint": "/api/knowledge/search",           "icon": "📚"},
    {"slug": "aadc.assess",              "label": "AADC Compliance Assess", "endpoint": "/agentic-ai/canvas",  "icon": "🛡️"},
    {"slug": "aadc.threat_model",        "label": "AADC Threat Model",      "endpoint": "/agentic-ai/canvas",  "icon": "⚔️"},
    {"slug": "aadc.recommend",           "label": "AADC Auto-Recommend",    "endpoint": "/agentic-ai/canvas",  "icon": "💡"},
]
