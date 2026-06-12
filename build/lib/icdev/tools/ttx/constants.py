# CUI // SP-CTI
"""TTX Engine — shared constants."""

SESSION_STATES = ("pending", "active", "paused", "ended")
INJECT_STATES  = ("pending", "dispatched", "closed")
SESSION_MODES  = ("live", "async")

SCORE_CATEGORIES = ("receipt_pts", "judge_pts", "time_bonus_pts", "total_pts")

# Time-bonus brackets (seconds → bonus points)
TIME_BONUS_BRACKETS = [
    (120,  50),   # ≤ 120 s  → +50 (Lightning)
    (300,  25),   # ≤ 300 s  → +25 (Fast)
    (600,  10),   # ≤ 600 s  → +10 (On-time)
]

# Category ribbon definitions for end-of-session awards
RIBBON_DEFS = {
    "speed_king":      {"icon": "⚡", "label": "Speed King",      "desc": "Fastest average response"},
    "ai_innovator":    {"icon": "🤖", "label": "AI Innovator",    "desc": "Most AI receipts + highest quality"},
    "doctrine_scholar":{"icon": "📚", "label": "Doctrine Scholar","desc": "Highest LLM judge score"},
    "strategist":      {"icon": "🎯", "label": "Strategist",      "desc": "Highest COA inject score"},
    "safety_architect":{"icon": "🛡️", "label": "Safety Architect","desc": "Highest AADC compliance score across design challenges"},
    "model_architect": {"icon": "🏛️", "label": "Model Architect", "desc": "Highest AIMC governance score in the Model Governance inject"},
}

# ICDEV tool slugs that produce scoreable receipts
SCOREABLE_TOOLS = (
    "strategos.oracle",
    "strategos.signals",
    "strategos.wargame.coa",
    "strategos.wargame.lanchester",
    "strategos.wargame.ooda",
    "strategos.iw.composite",
    "strategos.simulate",
    "finetune.deploy",
    "knowledge.search",
    "genesis.run",
    "aadc.assess",
    "aadc.threat_model",
    "aadc.recommend",
    "aimc.assess",         # POST /ai-ml/api/designs/<id>/assess
    "aimc.gov_assess",     # POST /ai-ml/api/designs/<id>/assess-gov
    "aimc.adapt_recommend",# POST /ai-ml/api/adapt/recommend
    "aimc.deploy_plan",    # POST /ai-ml/api/deploy/plan
)

# Maps tool slug → ICDEV API endpoint (relative to http://localhost:5050)
TOOL_ENDPOINTS: dict[str, str] = {
    "strategos.oracle":         "/strategos/api/oracle",
    "strategos.signals":        "/strategos/api/signals",
    "strategos.wargame.coa":    "/strategos/api/wargame/coa",
    "strategos.wargame.lanchester": "/strategos/api/wargame/lanchester",
    "strategos.wargame.ooda":   "/strategos/api/wargame/ooda",
    "strategos.iw.composite":   "/strategos/api/iw/composite",
    "strategos.simulate":       "/strategos/api/simulate",
    "finetune.deploy":          "/finetune/api/deploy",
    "knowledge.search":         "/knowledge/api/search",
    "genesis.run":              "/genesis/api/run",
    "aadc.assess":              "/agentic-ai/api/designs/{design_id}/assess",
    "aadc.threat_model":        "/agentic-ai/api/designs/{design_id}/threat-model",
    "aadc.recommend":           "/agentic-ai/api/adapt/recommend",
    "aimc.assess":              "/ai-ml/api/designs/{design_id}/assess",
    "aimc.gov_assess":          "/ai-ml/api/designs/{design_id}/assess-gov",
    "aimc.adapt_recommend":     "/ai-ml/api/adapt/recommend",
    "aimc.deploy_plan":         "/ai-ml/api/deploy/plan",
}

# Ontology mappings for the Knowledge Graph ontology bridge
TTX_ONTOLOGY_MAP: dict[str, str] = {
    "inject":          "https://icdev.dev/ontology/mission#ExerciseInject",
    "decision_point":  "https://icdev.dev/ontology/mission#DecisionPoint",
    "action":          "https://icdev.dev/ontology/mission#CourseOfAction",
    "after_action":    "https://icdev.dev/ontology/mission#AfterActionReport",
    "lesson_learned":  "https://icdev.dev/ontology/mission#LessonLearned",
    "threat_scenario": "https://icdev.dev/ontology/security#ThreatScenario",
}
