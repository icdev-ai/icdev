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
    "safety_architect": {"icon": "🛡️", "label": "Safety Architect", "desc": "Highest AADC compliance score across design challenges"},
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
)
