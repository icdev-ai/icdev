# CUI // SP-CTI
"""FORGE Academy constants — XP, levels, roles, achievements, skill tree."""

# ---------------------------------------------------------------------------
# Rank system
# ---------------------------------------------------------------------------
LEVELS = [
    {"slug": "recruit",    "label": "Recruit",    "min_xp": 0,     "icon": "⬡",  "color": "#6c6c80"},
    {"slug": "operative",  "label": "Operative",  "min_xp": 500,   "icon": "◈",  "color": "#4a90d9"},
    {"slug": "specialist", "label": "Specialist", "min_xp": 2000,  "icon": "◆",  "color": "#00D4FF"},
    {"slug": "architect",  "label": "Architect",  "min_xp": 5000,  "icon": "❖",  "color": "#FF6B35"},
    {"slug": "sensei",     "label": "Sensei",     "min_xp": 10000, "icon": "★",  "color": "#FFB800"},
]

LEVEL_BY_SLUG = {l["slug"]: l for l in LEVELS}

def xp_to_level(xp: int) -> dict:
    current = LEVELS[0]
    for lvl in LEVELS:
        if xp >= lvl["min_xp"]:
            current = lvl
    return current

def xp_to_next_level(xp: int) -> dict:
    """Returns {level, xp_needed, xp_current, xp_max, pct}."""
    current = xp_to_level(xp)
    idx = next(i for i, l in enumerate(LEVELS) if l["slug"] == current["slug"])
    if idx + 1 < len(LEVELS):
        nxt = LEVELS[idx + 1]
        span = nxt["min_xp"] - current["min_xp"]
        earned = xp - current["min_xp"]
        return {
            "level": current,
            "next": nxt,
            "xp_current": earned,
            "xp_needed": span - earned,
            "xp_max": span,
            "pct": min(100, int(earned / span * 100)),
        }
    return {
        "level": current,
        "next": None,
        "xp_current": xp - current["min_xp"],
        "xp_needed": 0,
        "xp_max": 1,
        "pct": 100,
    }

# ---------------------------------------------------------------------------
# Role definitions
# ---------------------------------------------------------------------------
ROLES = {
    "devops":       {"label": "DevOps / Platform Eng",          "type": "technical", "icon": "⚙️",  "color": "#4a90d9", "tier2_topic": "devops"},
    "secops_eng":   {"label": "SecOps Engineer",                "type": "technical", "icon": "🛡️",  "color": "#dc3545", "tier2_topic": "secops"},
    "dataops":      {"label": "Data / ML Engineer",             "type": "technical", "icon": "🧪",  "color": "#00D4FF", "tier2_topic": "dataops"},
    "swe_arch":     {"label": "SWE / Architect",                "type": "technical", "icon": "🏗️",  "color": "#FF6B35", "tier2_topic": "swe_arch"},
    "netops":       {"label": "Network Engineer",               "type": "technical", "icon": "🌐",  "color": "#6f42c1", "tier2_topic": "netops"},
    "sre":          {"label": "Site Reliability Engineer",      "type": "technical", "icon": "📡",  "color": "#fd7e14", "tier2_topic": "sre"},
    "isso":         {"label": "ISSO",                           "type": "guided",    "icon": "🔐",  "color": "#FFB800", "tier2_topic": "isso"},
    "issm":         {"label": "ISSM",                           "type": "guided",    "icon": "📋",  "color": "#FF6B35", "tier2_topic": "issm"},
    "ciso":         {"label": "CISO",                           "type": "guided",    "icon": "👁️",  "color": "#dc3545", "tier2_topic": "ciso"},
    "pm":           {"label": "Program Manager",                "type": "guided",    "icon": "📊",  "color": "#28a745", "tier2_topic": "pm"},
    "analyst":      {"label": "Analyst (Intel / Financial)",    "type": "guided",    "icon": "🔍",  "color": "#20c997", "tier2_topic": "analyst"},
    "leadership":   {"label": "Leadership / Executive",         "type": "guided",    "icon": "🎯",  "color": "#e83e8c", "tier2_topic": "leadership"},
}

TECHNICAL_ROLES = {k for k, v in ROLES.items() if v["type"] == "technical"}
GUIDED_ROLES    = {k for k, v in ROLES.items() if v["type"] == "guided"}

# ---------------------------------------------------------------------------
# Mission types
# ---------------------------------------------------------------------------
MISSION_TYPES = ["coding", "guided", "concept", "hybrid"]

STEP_TYPES = [
    "coding",           # write + run Python code
    "configure",        # configure a system or API via wizard
    "verify",           # inspect output, check results
    "reflect",          # written reflection, no code
    "watch",            # video or demo content
    "deploy",           # deploy to an environment
    "design",           # architecture or system design
    "coworker",         # delegate to an ACE co-worker and review output
    "docgen",           # generate a document artifact via DocGen
    "agent_readiness",  # run 11-pillar readiness check on a target repo
]

# ---------------------------------------------------------------------------
# Step progress vocabulary
# ---------------------------------------------------------------------------
# fa_step_progress.status. 'attempted' exists because a failed submission used to
# be filed as 'completed' with score 0 (aca-int-05) — a failure is now visibly a
# failure. If a CHECK constraint is ever added to the column, derive it from
# STEP_STATUSES rather than hardcoding the list in SQL.
STEP_STATUS_NOT_STARTED = "not_started"
STEP_STATUS_ATTEMPTED   = "attempted"
STEP_STATUS_COMPLETED   = "completed"
STEP_STATUSES = (STEP_STATUS_NOT_STARTED, STEP_STATUS_ATTEMPTED, STEP_STATUS_COMPLETED)

# ---------------------------------------------------------------------------
# Tier gating (aca-ux-04)
# ---------------------------------------------------------------------------
# Percentage of the PRIOR tier's *completable* missions required to unlock a tier.
# Tier 1 is deliberately absent: it is the entry point and must never gate.
#
# "Completable" excludes missions with zero steps. This is not a nicety — Tier 1
# contains m-chat-agent-interview, which has no steps by design (fga-wire-06 marks
# genuinely-empty missions Coming Soon). A "100% of the prior tier" rule would
# therefore have made Tier 2 PERMANENTLY unreachable. 12 of 13 Tier-1 missions and
# 95 of 104 Tier-2 missions are completable.
#
# Tier 3's bar is deliberately much lower than Tier 2's: Tier 2 is a 95-mission
# role-track catalogue that no single learner is expected to finish (a netops
# engineer has no reason to complete the ISSM track), so a high percentage there
# would gate Tier 3 on irrelevant work. Scoping the Tier-3 threshold to the
# learner's own role missions is the better answer and is deliberately deferred:
# role matching currently uses LIKE '%role%', so 'swe' matches 'swe_arch'
# (aca-hyg-02). Revisit once that is exact-token.
TIER_UNLOCK_PCT = {
    2: 80,
    3: 25,
}

# fa_mission_progress.status. Same rule as above: if a CHECK is ever added to the
# column, derive it from MISSION_STATUSES.
MISSION_STATUS_NOT_STARTED = "not_started"
MISSION_STATUS_IN_PROGRESS = "in_progress"
MISSION_STATUS_COMPLETED   = "completed"
MISSION_STATUSES = (
    MISSION_STATUS_NOT_STARTED, MISSION_STATUS_IN_PROGRESS, MISSION_STATUS_COMPLETED,
)

# ---------------------------------------------------------------------------
# XP multipliers
# ---------------------------------------------------------------------------
XP_MULT_FIRST_TRY_NO_HINTS = 1.5
XP_MULT_WITH_HINTS         = 0.75
XP_MULT_REFLECT_CORRECT    = 1.25
XP_HINT_PENALTY            = 10    # deducted per hint from final step score
XP_DAILY_LOGIN_BASE        = 25
XP_STREAK_BONUS_PER_DAY    = 10    # bonus per streak day (capped at 7 days)
XP_SPEED_BONUS_THRESHOLD_S = 90    # seconds — faster = speed bonus
XP_SPEED_BONUS             = 25

# ---------------------------------------------------------------------------
# Achievements (20 total)
# ---------------------------------------------------------------------------
ACHIEVEMENTS = [
    # Technical path
    {
        "slug": "first_spark",
        "title": "First Spark",
        "description": "Complete your very first step. The journey begins.",
        "icon": "⚡",
        "xp_bonus": 50,
        "criteria_json": '{"type": "steps_completed", "count": 1}',
        "rarity": "common",
    },
    {
        "slug": "prompt_whisperer",
        "title": "Prompt Whisperer",
        "description": "Master the art of speaking to AI. M02 complete.",
        "icon": "🗣️",
        "xp_bonus": 100,
        "criteria_json": '{"type": "mission_complete", "mission_slug": "m02-prompt-engineering"}',
        "rarity": "common",
    },
    {
        "slug": "rag_architect",
        "title": "RAG Architect",
        "description": "You can now make any LLM smarter with your data.",
        "icon": "🧠",
        "xp_bonus": 150,
        "criteria_json": '{"type": "mission_complete", "mission_slug": "m03-rag-basics"}',
        "rarity": "uncommon",
    },
    {
        "slug": "tool_forger",
        "title": "Tool Forger",
        "description": "An MCP server lives. You built it.",
        "icon": "⚒️",
        "xp_bonus": 150,
        "criteria_json": '{"type": "mission_complete", "mission_slug": "m06-fastmcp"}',
        "rarity": "uncommon",
    },
    {
        "slug": "speed_demon",
        "title": "Speed Demon",
        "description": "Blazing. Completed a step in under 90 seconds.",
        "icon": "⚡",
        "xp_bonus": 75,
        "criteria_json": '{"type": "speed_step", "seconds": 90}',
        "rarity": "uncommon",
    },
    {
        "slug": "no_hints_needed",
        "title": "No Hints Needed",
        "description": "A full mission. Zero hints. You are the AI.",
        "icon": "🎯",
        "xp_bonus": 200,
        "criteria_json": '{"type": "mission_no_hints"}',
        "rarity": "rare",
    },
    {
        "slug": "arena_gladiator",
        "title": "Arena Gladiator",
        "description": "You stepped into the Arena. Most never do.",
        "icon": "⚔️",
        "xp_bonus": 100,
        "criteria_json": '{"type": "challenge_entered", "count": 1}',
        "rarity": "uncommon",
    },
    # Non-technical path
    {
        "slug": "first_deploy",
        "title": "First Deploy",
        "description": "Your first AI agent is live. Nothing will be the same.",
        "icon": "🚀",
        "xp_bonus": 100,
        "criteria_json": '{"type": "deploy_step_completed", "count": 1}',
        "rarity": "common",
    },
    {
        "slug": "compliance_crusader",
        "title": "Compliance Crusader",
        "description": "STIG triage agent deployed. Your future self thanks you.",
        "icon": "🛡️",
        "xp_bonus": 150,
        "criteria_json": '{"type": "mission_complete", "mission_slug": "m-isso-01-stig-triage"}',
        "rarity": "uncommon",
    },
    {
        "slug": "ato_accelerator",
        "title": "ATO Accelerator",
        "description": "ATO in weeks, not years. You found the shortcut.",
        "icon": "📋",
        "xp_bonus": 200,
        "criteria_json": '{"type": "mission_complete", "mission_slug": "m-issm-01-ato-acceleration"}',
        "rarity": "rare",
    },
    {
        "slug": "ciso_configured",
        "title": "CISO Configured",
        "description": "AI governance posture set. Board meeting? Bring it.",
        "icon": "👁️",
        "xp_bonus": 200,
        "criteria_json": '{"type": "role_track_complete", "role": "ciso"}',
        "rarity": "rare",
    },
    {
        "slug": "proposal_pro",
        "title": "Proposal Pro",
        "description": "GovCon capture cycle complete. Pwin just went up.",
        "icon": "📊",
        "xp_bonus": 150,
        "criteria_json": '{"type": "mission_complete", "mission_slug": "m-pm-06-capstone"}',
        "rarity": "uncommon",
    },
    {
        "slug": "governance_guardian",
        "title": "Governance Guardian",
        "description": "OMB M-25-21 compliant AI inventory deployed.",
        "icon": "⚖️",
        "xp_bonus": 150,
        "criteria_json": '{"type": "mission_complete", "mission_slug": "m-ciso-01-ai-inventory"}',
        "rarity": "uncommon",
    },
    # All-roles
    {
        "slug": "tier1_complete",
        "title": "Tier 1 Complete",
        "description": "Foundations mastered. You now speak fluent AI.",
        "icon": "🏆",
        "xp_bonus": 500,
        "criteria_json": '{"type": "tier_complete", "tier": 1}',
        "rarity": "epic",
    },
    {
        "slug": "role_chosen",
        "title": "Role Chosen",
        "description": "Your mission is personal now. Tier 2 unlocked.",
        "icon": "🎯",
        "xp_bonus": 75,
        "criteria_json": '{"type": "role_set"}',
        "rarity": "common",
    },
    {
        "slug": "workflow_author",
        "title": "Workflow Author",
        "description": "You built a FORGE workflow. It will outlast this session.",
        "icon": "📝",
        "xp_bonus": 200,
        "criteria_json": '{"type": "workflow_submitted"}',
        "rarity": "rare",
    },
    {
        "slug": "guild_founder",
        "title": "Guild Founder",
        "description": "Your guild exists. Recruit wisely.",
        "icon": "🏰",
        "xp_bonus": 100,
        "criteria_json": '{"type": "guild_created"}',
        "rarity": "uncommon",
    },
    {
        "slug": "squad_goals",
        "title": "Squad Goals",
        "description": "Your guild cleared a mission together.",
        "icon": "🤝",
        "xp_bonus": 150,
        "criteria_json": '{"type": "squad_mission_complete"}',
        "rarity": "uncommon",
    },
    {
        "slug": "tier3_complete",
        "title": "FORGE Expert",
        "description": "You shipped a real ICDEV child app. Legendary.",
        "icon": "🌟",
        "xp_bonus": 1000,
        "criteria_json": '{"type": "tier_complete", "tier": 3}',
        "rarity": "legendary",
    },
    {
        "slug": "founding_agent",
        "title": "Founding Agent",
        "description": "Among the first 10 on FORGE Academy. A pioneer.",
        "icon": "🎖️",
        "xp_bonus": 300,
        "criteria_json": '{"type": "user_rank", "rank": 10}',
        "rarity": "legendary",
    },
    # AADC — AI design & safety
    {
        "slug": "threat_modeler",
        "title": "Threat Modeler",
        "description": "You ran a STRIDE threat model on an agentic system. Most engineers never bother.",
        "icon": "⚔️",
        "xp_bonus": 150,
        "criteria_json": '{"type": "mission_complete", "mission_slug": "m-secops-05-aadc-threat-model"}',
        "rarity": "uncommon",
    },
    {
        "slug": "owasp_defender",
        "title": "OWASP Defender",
        "description": "All 10 OWASP LLM checks pass on your design. You built it right.",
        "icon": "🛡️",
        "xp_bonus": 250,
        "criteria_json": '{"type": "aadc_owasp_clean"}',
        "rarity": "rare",
    },
    {
        "slug": "governance_builder",
        "title": "Governance Builder",
        "description": "NIST AI RMF GOVERN checks all green. Your AI has a chain of command.",
        "icon": "⚖️",
        "xp_bonus": 250,
        "criteria_json": '{"type": "mission_complete", "mission_slug": "m-ciso-02-aadc-governance"}',
        "rarity": "rare",
    },
    {
        "slug": "full_stack_designer",
        "title": "Full-Stack Designer",
        "description": "AADC assessment score ≥ 90. Architecture, safety, governance — all locked in.",
        "icon": "🏛️",
        "xp_bonus": 400,
        "criteria_json": '{"type": "aadc_score_gte", "score": 90}',
        "rarity": "epic",
    },
    # ACE Co-Worker
    {
        "slug": "ace_orchestrator",
        "title": "ACE Orchestrator",
        "description": "A 3-role ACE co-worker pipeline completed. You're building teams, not bots.",
        "icon": "🤝",
        "xp_bonus": 300,
        "criteria_json": '{"type": "mission_complete", "mission_slug": "m-ace-03-multi-role-pipeline"}',
        "rarity": "rare",
    },
    {
        "slug": "docgen_author",
        "title": "DocGen Author",
        "description": "A DocGen portfolio artifact produced and linked to your profile. Documentation that writes itself.",
        "icon": "📄",
        "xp_bonus": 200,
        "criteria_json": '{"type": "mission_complete", "mission_slug": "m-docgen-02-portfolio-artifact"}',
        "rarity": "uncommon",
    },
    {
        "slug": "readiness_champion",
        "title": "Readiness Champion",
        "description": "Agent readiness score ≥ 80% after remediation. Your repo is ready for production.",
        "icon": "✅",
        "xp_bonus": 250,
        "criteria_json": '{"type": "readiness_score_gte", "score": 80}',
        "rarity": "rare",
    },
    {
        "slug": "governance_steward",
        "title": "Governance Steward",
        "description": "Full AI governance portfolio: inventory + model card + oversight plan + ethics review. OMB M-25-21 compliant.",
        "icon": "⚖️",
        "xp_bonus": 400,
        "criteria_json": '{"type": "mission_complete", "mission_slug": "m-gov-capstone"}',
        "rarity": "epic",
    },
    {
        "slug": "gameday_champion",
        "title": "GameDay Champion",
        "description": "Top-10 finish in a GameDay tournament. You competed against AI and won.",
        "icon": "🏆",
        "xp_bonus": 500,
        "criteria_json": '{"type": "gameday_top_n", "n": 10}',
        "rarity": "epic",
    },
]

ACHIEVEMENT_BY_SLUG = {a["slug"]: a for a in ACHIEVEMENTS}

RARITY_COLORS = {
    "common":    "#a0a0b8",
    "uncommon":  "#4a90d9",
    "rare":      "#9b59b6",
    "epic":      "#FF6B35",
    "legendary": "#FFB800",
}

# ---------------------------------------------------------------------------
# Tier colors
# ---------------------------------------------------------------------------
TIER_COLORS = {
    1: "#4a90d9",   # blue  — Foundations
    2: "#FF6B35",   # orange — Applied ICDEV
    3: "#FF2D55",   # red   — ICDEV Expert
}

TIER_LABELS = {
    1: "Foundations",
    2: "Applied ICDEV",
    3: "ICDEV Expert",
}

# ---------------------------------------------------------------------------
# Skill tree nodes
# ---------------------------------------------------------------------------
SKILL_NODES = [
    # Tier 1 — linear chain
    {"slug": "llm-basics",     "title": "LLM Basics",          "tier": 1, "role_filter": "all", "prereqs": [],              "pos": (0, 0)},
    {"slug": "prompting",      "title": "Prompt Engineering",   "tier": 1, "role_filter": "all", "prereqs": ["llm-basics"],  "pos": (1, 0)},
    {"slug": "rag",            "title": "RAG",                  "tier": 1, "role_filter": "all", "prereqs": ["prompting"],   "pos": (2, 0)},
    {"slug": "agents",         "title": "Agents",               "tier": 1, "role_filter": "all", "prereqs": ["rag"],         "pos": (3, 0)},
    {"slug": "mcp",            "title": "MCP Protocol",         "tier": 1, "role_filter": "all", "prereqs": ["agents"],      "pos": (4, 0)},
    {"slug": "fastmcp",        "title": "FastMCP",              "tier": 1, "role_filter": "all", "prereqs": ["mcp"],         "pos": (5, -1)},
    {"slug": "multi-agent",    "title": "Multi-Agent",          "tier": 1, "role_filter": "all", "prereqs": ["mcp"],         "pos": (5, 1)},
    {"slug": "strands",        "title": "Amazon Strands",       "tier": 1, "role_filter": "all", "prereqs": ["multi-agent"], "pos": (6, 0)},
    {"slug": "langchain",      "title": "LangChain",            "tier": 1, "role_filter": "all", "prereqs": ["strands"],     "pos": (7, 0)},
    # Tier 2 — branches
    {"slug": "pipeline-agent", "title": "Pipeline Agent",       "tier": 2, "role_filter": "devops",     "prereqs": ["langchain"], "pos": (8, -3)},
    {"slug": "stig-agent",     "title": "STIG Triage Agent",    "tier": 2, "role_filter": "secops_eng,isso", "prereqs": ["langchain"], "pos": (8, -1)},
    {"slug": "advanced-rag",   "title": "Advanced RAG",         "tier": 2, "role_filter": "dataops",    "prereqs": ["langchain"], "pos": (8, 1)},
    {"slug": "mcp-server",     "title": "MCP Server Builder",   "tier": 2, "role_filter": "swe_arch",   "prereqs": ["langchain"], "pos": (8, 3)},
    {"slug": "ato-ai",         "title": "ATO with AI",          "tier": 2, "role_filter": "issm,ciso",  "prereqs": ["langchain"], "pos": (8, 5)},
    {"slug": "workflow-build", "title": "Workflow Builder",      "tier": 2, "role_filter": "all",        "prereqs": ["langchain"], "pos": (9, 0)},
    # Tier 3
    # AADC — AI safety design branch (after multi-agent)
    {"slug": "agentic-safety",  "title": "Agentic Safety Design", "tier": 2, "role_filter": "all",        "prereqs": ["multi-agent"],   "pos": (6, -2)},
    {"slug": "ai-threat-model", "title": "AI Threat Modeling",    "tier": 2, "role_filter": "secops_eng,isso,issm,ciso", "prereqs": ["agentic-safety"], "pos": (7, -2)},
    {"slug": "compliance-design","title": "Compliance Architecture","tier": 2, "role_filter": "issm,ciso", "prereqs": ["agentic-safety"], "pos": (7, -3)},
    {"slug": "forge-deep",     "title": "FORGE Framework",      "tier": 3, "role_filter": "all", "prereqs": ["workflow-build"], "pos": (10, 0)},
    {"slug": "tool-author",    "title": "Tool Author",          "tier": 3, "role_filter": "all", "prereqs": ["forge-deep"],    "pos": (11, 0)},
    {"slug": "goal-author",    "title": "Goal Author",          "tier": 3, "role_filter": "all", "prereqs": ["tool-author"],   "pos": (12, 0)},
    {"slug": "blueprint",      "title": "Blueprint Builder",    "tier": 3, "role_filter": "all", "prereqs": ["goal-author"],   "pos": (13, 0)},
    {"slug": "child-app",      "title": "Child App Creator",    "tier": 3, "role_filter": "all", "prereqs": ["blueprint"],     "pos": (14, 0)},
    # ACE Co-Worker branch (after multi-agent)
    {"slug": "ace-roles",       "title": "ACE Roles & Delegation",  "tier": 2, "role_filter": "ai_developer,agent_developer,swe_arch", "prereqs": ["multi-agent"],   "pos": (6, 2)},
    {"slug": "ace-patterns",    "title": "Creator-Verifier Pattern", "tier": 2, "role_filter": "ai_developer,agent_developer,swe_arch", "prereqs": ["ace-roles"],     "pos": (7, 2)},
    {"slug": "ace-pipelines",   "title": "Multi-Role Pipeline",      "tier": 2, "role_filter": "ai_developer,agent_developer,swe_arch", "prereqs": ["ace-patterns"],  "pos": (8, 2)},
    # DocGen branch (after workflow-build)
    {"slug": "docgen-sessions", "title": "DocGen Session Lifecycle", "tier": 2, "role_filter": "isso,issm,swe_arch,dataops", "prereqs": ["workflow-build"],  "pos": (10, -2)},
    {"slug": "docgen-portfolio","title": "DocGen Portfolio",          "tier": 2, "role_filter": "isso,issm,swe_arch,dataops", "prereqs": ["docgen-sessions"], "pos": (11, -2)},
    # Agent Readiness branch (after stig-agent)
    {"slug": "readiness-check", "title": "11-Pillar Readiness",     "tier": 2, "role_filter": "secops_eng,isso,swe_arch,devops", "prereqs": ["stig-agent"],      "pos": (9, -1)},
    {"slug": "readiness-ci",    "title": "Readiness CI Gate",        "tier": 2, "role_filter": "secops_eng,isso,swe_arch,devops", "prereqs": ["readiness-check"], "pos": (10, -1)},
    # AI Governance branch (after ato-ai)
    {"slug": "ai-transparency",   "title": "AI Transparency",    "tier": 2, "role_filter": "ciso,issm,leadership,pm", "prereqs": ["ato-ai"],          "pos": (9, 5)},
    {"slug": "ai-accountability", "title": "AI Accountability",  "tier": 2, "role_filter": "ciso,issm,leadership,pm", "prereqs": ["ai-transparency"], "pos": (10, 5)},
    {"slug": "gov-intake",        "title": "Governance Intake",  "tier": 2, "role_filter": "ciso,issm,leadership,pm", "prereqs": ["ai-accountability"],"pos": (11, 5)},
]

# ---------------------------------------------------------------------------
# AI Competency Framework (L1–L5, cross-walked to DoD AI Workforce Framework)
# ---------------------------------------------------------------------------
AI_COMPETENCY_LEVELS = [
    {
        "key": "l1",
        "label": "L1 — AI Aware",
        "xp_min": 0,
        "xp_max": 500,
        "gate": "Tier 1 complete",
        "real_world": "Understands LLMs; can use AI tools in daily workflow",
        "dod_mapping": "AI Literacy",
        "omb_m25_21": "AI Awareness",
        "color": "#888888",
    },
    {
        "key": "l2",
        "label": "L2 — AI Capable",
        "xp_min": 500,
        "xp_max": 2000,
        "gate": "Tier 2 partial (role track 50%)",
        "real_world": "Builds AI features in role domain",
        "dod_mapping": "AI Practitioner",
        "omb_m25_21": "AI Application",
        "color": "#4A90E2",
    },
    {
        "key": "l3",
        "label": "L3 — AI Proficient",
        "xp_min": 2000,
        "xp_max": 5000,
        "gate": "Tier 2 complete",
        "real_world": "Designs and deploys AI systems using AADC/AIMC",
        "dod_mapping": "AI Developer",
        "omb_m25_21": "AI Integration",
        "color": "#00D4FF",
    },
    {
        "key": "l4",
        "label": "L4 — AI Expert",
        "xp_min": 5000,
        "xp_max": 10000,
        "gate": "Tier 3 complete",
        "real_world": "Builds AI platforms; leads AI initiatives",
        "dod_mapping": "AI Lead",
        "omb_m25_21": "AI Leadership",
        "color": "#FF6B35",
    },
    {
        "key": "l5",
        "label": "L5 — AI Architect",
        "xp_min": 10000,
        "xp_max": None,
        "gate": "Sensei rank + GameDay top-10",
        "real_world": "Defines AI strategy for an organization",
        "dod_mapping": "AI Strategist",
        "omb_m25_21": "AI Governance",
        "color": "#FFB800",
    },
]

AI_COMPETENCY_BY_KEY = {c["key"]: c for c in AI_COMPETENCY_LEVELS}


def xp_to_competency_level(xp: int) -> dict:
    """Return the highest AI competency level achieved for given XP."""
    current = AI_COMPETENCY_LEVELS[0]
    for lvl in AI_COMPETENCY_LEVELS:
        if xp >= lvl["xp_min"]:
            current = lvl
    return current


# ---------------------------------------------------------------------------
# Certification tiers
# ---------------------------------------------------------------------------
CERT_TIERS = [
    {
        "key": "foundation",
        "label": "FORGE AI Foundation",
        "requirements": {
            "tier1_complete": True,
            "role_tier2_pct": 100,
            "assessment_score_min": 70,
        },
        "description": "Tier 1 complete + full role Tier 2 track + 20-question adaptive assessment",
        "xp_bonus": 1000,
        "color": "#4A90E2",
        "icon": "🎖️",
    },
    {
        "key": "practitioner",
        "label": "FORGE AI Practitioner",
        "requirements": {
            "foundation": True,
            "aadc_score_min": 80,
            "gameday_scenarios_min": 1,
        },
        "description": "Foundation + AADC design score ≥80 + 1 GameDay scenario completed",
        "xp_bonus": 2500,
        "color": "#FF6B35",
        "icon": "🏆",
    },
    {
        "key": "expert",
        "label": "FORGE AI Expert",
        "requirements": {
            "practitioner": True,
            "tier3_complete": True,
            "gameday_top_percentile": 50,
        },
        "description": "Practitioner + Tier 3 Capstone + top-50% in a GameDay",
        "xp_bonus": 5000,
        "color": "#FFB800",
        "icon": "⭐",
    },
]

CERT_BY_KEY = {c["key"]: c for c in CERT_TIERS}

# ---------------------------------------------------------------------------
# Intel feed messages (dynamic — filled at request time from DB)
# ---------------------------------------------------------------------------
INTEL_MESSAGES_STATIC = [
    "FORGE ACADEMY IS LIVE. Your mission: master the agentic stack.",
    "NEW: Tier 2 role tracks unlocked after completing M10 Capstone.",
    "FORGE Sensei is online. Ask for a hint — but each one costs you XP.",
    "BUILD YOUR OWN WORKFLOW: Available in Tier 2 for all roles.",
]
