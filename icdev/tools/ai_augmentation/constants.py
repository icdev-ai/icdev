# CUI // SP-CTI
"""AI Augmentation Canvas (AAC) constants.

Pattern types, AI paradigms, supported languages, scoring weights,
and IL levels for the AI Augmentation Opportunity Assessment engine.
"""

from __future__ import annotations

# ── Feature Flag ──────────────────────────────────────────────────────────────
AAC_FEATURE_FLAG = "ICDEV_AAC_ENABLED"

# ── Pattern Types ─────────────────────────────────────────────────────────────
# 8 AI-augmentable code patterns detected by the pattern classifier.
# IDs must match entries in context/ai_augmentation/pattern_catalog.json.
PATTERN_TYPES: list[str] = [
    "nested_conditionals",      # if-depth ≥ 3 → ML classifier
    "regex_user_input",         # regex on user-provided strings → NLP/NLU extractor
    "string_template_rendering",# format/f-str/Jinja2/Velocity/Razor/Sprintf → LLM generation
    "scheduled_cron",           # schedule/APScheduler/@Scheduled/Ticker/setInterval → agentic trigger
    "hardcoded_threshold",      # x > LITERAL comparisons → ML anomaly detection
    "db_render_notify_chain",   # ORM + template + SMTP pipeline → LLM synthesis
    "keyword_list_search",      # x in list / .contains() → vector semantic search
    "large_rule_table",         # dict/HashMap/map ≥ 10 keys → ML/RL decision agent
]

# ── AI Paradigms ──────────────────────────────────────────────────────────────
AI_PARADIGMS: list[str] = [
    "llm_generation",
    "ml_classifier",
    "embedding_search",
    "anomaly_detection",
    "decision_agent",
    "agentic_trigger",
    "nlp_extractor",
]

# ── Supported Languages ───────────────────────────────────────────────────────
SUPPORTED_LANGUAGES: list[str] = [
    "python",
    "java",
    "csharp",
    "go",
    "rust",
    "typescript",
]

# ── Scoring Weights ───────────────────────────────────────────────────────────
# Matches the scoring formula in docs/features/aac-ai-augmentation-canvas.md.
SCORING_WEIGHTS: dict[str, dict[str, float]] = {
    # value_score = Σ(component × weight) — higher = more business value
    "value": {
        "usage_freq": 0.40,
        "task_complexity": 0.35,
        "automation_deficit": 0.25,
    },
    # feasibility_score = Σ(component × weight) — higher = easier to implement
    "feasibility": {
        "data_availability": 0.40,
        "il_model_exists": 0.35,
        "integration_simplicity": 0.25,  # stored as (1 - integration_complexity)
    },
    # risk_score = Σ(component × weight) — lower = less risky
    "risk": {
        "reversibility": 0.40,
        "compliance_safety": 0.35,       # stored as (1 - compliance_impact)
        "dependency_simplicity": 0.25,   # stored as (1 - dep_complexity)
    },
    # composite = weighted aggregate of the three sub-scores
    "composite": {
        "value": 0.45,
        "feasibility": 0.35,
        "risk_inverted": 0.20,           # applied as (1 - risk_score)
    },
}

# ── IL Levels ─────────────────────────────────────────────────────────────────
IL_LEVELS: list[str] = [
    "il4",
    "il5",
    "il6",
]

# ── SQL CHECK Constraint Strings ──────────────────────────────────────────────
# Derived from the constants above — never hardcode these in SQL directly.
_pattern_list = ", ".join(f"'{p}'" for p in PATTERN_TYPES)
_paradigm_list = ", ".join(f"'{p}'" for p in AI_PARADIGMS)

CHECK_PATTERN_TYPE = f"pattern_type IN ({_pattern_list})"
CHECK_AI_PARADIGM = f"ai_paradigm IN ({_paradigm_list})"
