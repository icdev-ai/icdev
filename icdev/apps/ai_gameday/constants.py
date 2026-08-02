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
    {"id": "ace_delegation_sprint",  "label": "ACE Delegation Sprint",   "icon": "🤝"},
    {"id": "readiness_gauntlet",     "label": "Readiness Gauntlet",      "icon": "✅"},
    {"id": "governance_challenge",   "label": "AI Governance Challenge", "icon": "⚖️"},
    {"id": "docgen_race",            "label": "DocGen Race",             "icon": "📄"},
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
    {"id": "ace",         "label": "ACE Co-Worker",        "icon": "🤝", "class": "ai:ACEDelegation"},
    {"id": "readiness",   "label": "Agent Readiness",      "icon": "✅", "class": "ai:ReadinessCheck"},
    {"id": "governance",  "label": "AI Governance",        "icon": "⚖️", "class": "ai:GovernanceAssessment"},
    {"id": "docgen",      "label": "DocGen Race",          "icon": "📄", "class": "ai:DocGenWorkflow"},
]

# Scoreboard filter categories for ontology-based filtering
SCOREBOARD_ONTOLOGY_FILTERS = [
    {"id": "all",        "label": "All Events",     "filter": None},
    {"id": "strategy",   "label": "Strategy",       "filter": "strategy"},
    {"id": "security",   "label": "Security",       "filter": "security"},
    {"id": "war",        "label": "Warfighting",    "filter": "war"},
    {"id": "geospatial", "label": "Geospatial",     "filter": "geospatial"},
    {"id": "ai_ops",    "label": "AI Operations",  "filter": "ai"},
]

# ICDEV tool slugs surfaced to players in the "Link AI Tool" picker.
# Entries that map to a real MCP tool carry an `mcp_tool` key whose value is a
# key in tools/mcp/tool_registry.py::TOOL_REGISTRY. The freshness test
# (tests/test_penta_gd_content.py) asserts every `mcp_tool` resolves there.
def tool_is_navigable(tool: dict) -> bool:
    """True when a catalog entry can be opened in a browser tab.

    12 of the 34 entries are POST-only ``/api/...`` endpoints. The player
    console rendered every entry as ``<a href=...>``, so clicking one of those
    opened a tab that 405s or 404s (fga-gd-02). A page cannot GET a POST API,
    so these are shown as reference rather than as links.
    """
    return not str(tool.get("endpoint", "")).startswith("/api/")


AI_TOOLS_CATALOG = [
    {"slug": "strategos.oracle",          "label": "Strategos Oracle",       "endpoint": "/api/strategos/oracle",           "icon": "🔮"},
    {"slug": "strategos.signals",         "label": "Signal Prioritizer",     "endpoint": "/api/strategos/signals",          "icon": "📡"},
    {"slug": "strategos.wargame.coa",     "label": "Wargame COA Generator",  "endpoint": "/api/strategos/wargame",          "icon": "🗺️"},
    {"slug": "strategos.wargame.ooda",    "label": "OODA Tempo Analysis",    "endpoint": "/api/strategos/wargame/{id}/ooda", "icon": "⏱️"},
    {"slug": "strategos.iw.composite",   "label": "IW Composite (PMESII)",  "endpoint": "/api/strategos/iw/composite",     "icon": "📊"},
    {"slug": "strategos.simulate",        "label": "Supply Chain Simulate",  "endpoint": "/api/strategos/simulate/run",     "icon": "🔁"},
    {"slug": "finetune.deploy",           "label": "Fine-Tune Deploy",       "endpoint": "/api/finetune/jobs",              "icon": "🧠"},
    {"slug": "knowledge.search",          "label": "Knowledge RAG Search",   "endpoint": "/api/knowledge/search",           "icon": "📚", "mcp_tool": "rag_search"},
    {"slug": "aadc.assess",              "label": "AADC Compliance Assess", "endpoint": "/agentic-ai/canvas",  "icon": "🛡️"},
    {"slug": "aadc.threat_model",        "label": "AADC Threat Model",      "endpoint": "/agentic-ai/canvas",  "icon": "⚔️"},
    {"slug": "aadc.recommend",           "label": "AADC Auto-Recommend",    "endpoint": "/agentic-ai/canvas",  "icon": "💡"},
    {"slug": "ace.delegate",          "label": "ACE Co-Worker Delegate",   "endpoint": "/api/ace/coworker/delegate",        "icon": "🤝"},
    {"slug": "ace.inspect",           "label": "ACE Co-Worker Inspect",    "endpoint": "/api/ace/coworker/{id}/result",     "icon": "🔍"},
    {"slug": "readiness.check",       "label": "Agent Readiness Check",   "endpoint": "/api/readiness/check",              "icon": "✅"},
    {"slug": "readiness.remediate",   "label": "Readiness Remediation",   "endpoint": "/api/readiness/remediate",          "icon": "🔧"},
    {"slug": "transparency.inventory","label": "AI Transparency Inventory","endpoint": "/ai-transparency/api/inventory",    "icon": "📋"},
    {"slug": "transparency.card",     "label": "Model Card Generator",    "endpoint": "/ai-transparency/api/model-card",   "icon": "🃏"},
    {"slug": "accountability.plan",   "label": "AI Oversight Plan",       "endpoint": "/ai-accountability/api/plan",       "icon": "⚖️"},
    # ── Cortex unified AI layer ──────────────────────────────────────────────
    {"slug": "cortex.ask",            "label": "Cortex Ask (grounded Q&A)", "endpoint": "/cortex",              "icon": "🧠", "mcp_tool": "cortex_ask"},
    {"slug": "cortex.extract",        "label": "Cortex Extract",           "endpoint": "/cortex",              "icon": "🔎", "mcp_tool": "cortex_extract"},
    {"slug": "cortex.classify",       "label": "Cortex Classify",          "endpoint": "/cortex",              "icon": "🏷️", "mcp_tool": "cortex_classify"},
    {"slug": "cortex.govern",         "label": "Cortex Govern (policy gate)", "endpoint": "/cortex",           "icon": "⚖️", "mcp_tool": "cortex_govern"},
    # ── Document Intelligence Canvas (replaces legacy docgen.*) ───────────────
    {"slug": "dic.ingest",            "label": "DIC Document Ingest",      "endpoint": "/document-intelligence", "icon": "📥", "mcp_tool": "dic_ingest"},
    {"slug": "dic.search",            "label": "DIC Grounded Search",      "endpoint": "/document-intelligence", "icon": "📚", "mcp_tool": "dic_search"},
    {"slug": "dic.generate",          "label": "DIC Grounded Generate",    "endpoint": "/document-intelligence", "icon": "📝", "mcp_tool": "dic_generate"},
    # ── Knowledge graph / GraphRAG ───────────────────────────────────────────
    {"slug": "kg.search",             "label": "Knowledge Graph Search",   "endpoint": "/knowledge",           "icon": "🕸️", "mcp_tool": "kg_search"},
    {"slug": "kg.federated",          "label": "Federated KG Search",      "endpoint": "/knowledge",           "icon": "🌐", "mcp_tool": "kg_federated_search"},
    # ── Network Design Canvas (replaces legacy pna.predict) ───────────────────
    {"slug": "ndc.ai_assist",         "label": "NDC Network AI Assist",    "endpoint": "/network",             "icon": "📈", "mcp_tool": "mc_net_ai_assist"},
    {"slug": "ndc.topology",          "label": "NDC Topology Build",       "endpoint": "/network",             "icon": "🗺️", "mcp_tool": "topology_build"},
    {"slug": "ndc.spof",              "label": "NDC SPOF Finder",          "endpoint": "/network",             "icon": "⚠️", "mcp_tool": "topology_spof"},
    # ── Observability (ODC/OHC) ──────────────────────────────────────────────
    {"slug": "obs.slo",               "label": "SLO Dashboard",            "endpoint": "/observability",       "icon": "📊", "mcp_tool": "slo_dashboard"},
    {"slug": "obs.drift",             "label": "Model Drift Monitor",      "endpoint": "/observability",       "icon": "📉", "mcp_tool": "model_monitor_drift"},
    # ── Autonomous Capability Foundry ────────────────────────────────────────
    {"slug": "foundry.run",           "label": "Foundry Capability Run",   "endpoint": "/foundry",             "icon": "🏭", "mcp_tool": "foundry_run"},
    # ── Governed delivery pipeline ───────────────────────────────────────────
    {"slug": "kanban.summary",        "label": "Kanban Board Summary",     "endpoint": "/kanban",              "icon": "📋", "mcp_tool": "kanban_board_summary"},
]


def catalog_for_render() -> list:
    """AI_TOOLS_CATALOG with a ``navigable`` flag on each entry.

    Kept next to the catalog so every render site gets the same answer; the
    player console previously linked all 34 entries regardless of method.
    """
    return [{**t, "navigable": tool_is_navigable(t)} for t in AI_TOOLS_CATALOG]


# ── Registration / scenario recommendation (gdx-reg-01) ─────────────────────
# Vocabulary that marks a role as hands-on-technical, used by
# registration.technical_ratio() to tell a facilitator what kind of room they
# have before they pick a scenario.
#
# This is the one place a word list is unavoidable: "technical" is a judgement
# about language, not something the scenario packs declare. It is deliberately
# NOT a per-role or per-scenario weighting table (the earlier design sketched
# ROLE_TECH_WEIGHTS / SCENARIO_TECH_PROFILES) — keying on role ids or scenario
# slugs would hardcode both, and every new scenario pack would need an entry
# here before it scored correctly. Matching on the scenario's OWN wording means
# a new pack works with no change to this file.
TECHNICAL_MARKERS = frozenset({
    "engineer", "engineering", "developer", "devops", "sre", "sysadmin",
    "administrator", "architect", "analyst", "forensics", "malware", "reverse",
    "network", "packet", "firewall", "kernel", "database", "sql", "python",
    "code", "coding", "script", "scripting", "api", "cloud", "kubernetes",
    "container", "terraform", "pipeline", "telemetry", "siem", "detection",
    "threat", "hunting", "incident", "vulnerability", "exploit", "patch",
})
