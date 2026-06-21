# CUI // SP-CTI
"""AI-ify Canvas constants.

Pattern types, AI paradigms, supported languages, scoring weights,
and IL levels for the AI-ify Opportunity Assessment engine.
"""

from __future__ import annotations

# ── Feature Flag ──────────────────────────────────────────────────────────────
AIIFY_FEATURE_FLAG = "ICDEV_AIIFY_ENABLED"

# ── Pattern Types ─────────────────────────────────────────────────────────────
# AI-augmentable code patterns detected by the pattern classifier.
# IDs must match entries in context/aiify/pattern_catalog.json.
PATTERN_TYPES: list[str] = [
    "nested_conditionals",      # if-depth ≥ 3 → ML classifier
    "regex_user_input",         # regex on user-provided strings → NLP/NLU extractor
    "string_template_rendering",# format/f-str/Jinja2/Velocity/Razor/Sprintf → LLM generation
    "scheduled_cron",           # schedule/APScheduler/@Scheduled/Ticker/setInterval → agentic trigger
    "hardcoded_threshold",      # x > LITERAL comparisons → ML anomaly detection
    "db_render_notify_chain",   # ORM + template + SMTP pipeline → LLM synthesis
    "keyword_list_search",      # x in list / .contains() → vector semantic search
    "large_rule_table",         # dict/HashMap/map ≥ 10 keys → ML/RL decision agent
    # Framework-level architectural patterns (added 2026-05-31)
    "document_ingestion_pipeline",  # Document upload/consumer pipeline → agentic trigger
    "ocr_extraction_pipeline",      # OCR library usage (tesseract, pytesseract) → NLP extractor
    "fulltext_search_engine",       # DB full-text search (tsvector, SearchQuery) → embedding search
    "manual_classification_ui",     # Manual tagging/classification UI/API → ML classifier
    "metadata_extraction",          # Manual metadata entry / regex extraction → NLP extractor
    "document_routing",             # Static document routing rules → decision agent
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
# Matches the scoring formula in docs/features/aiify-ai-ify-canvas.md.
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

# ── Pattern → AI Paradigm map ─────────────────────────────────────────────────
# Single source of truth shared by engine.py and opportunity_scorer.py.
PATTERN_TO_PARADIGM: dict[str, str] = {
    "nested_conditionals": "ml_classifier",
    "regex_user_input": "nlp_extractor",
    "string_template_rendering": "llm_generation",
    "scheduled_cron": "agentic_trigger",
    "hardcoded_threshold": "anomaly_detection",
    "db_render_notify_chain": "llm_generation",
    "keyword_list_search": "embedding_search",
    "large_rule_table": "decision_agent",
    "document_ingestion_pipeline": "agentic_trigger",
    "ocr_extraction_pipeline": "nlp_extractor",
    "fulltext_search_engine": "embedding_search",
    "manual_classification_ui": "ml_classifier",
    "metadata_extraction": "nlp_extractor",
    "document_routing": "decision_agent",
}

# ── AI-readiness verdict tiers ────────────────────────────────────────────────
# Encodes "is this worth AI-ifying?" — anything below VERDICT_PARTIAL_MIN, or
# that trips a disqualifier, is NOT SUITABLE ("don't AI-ify for the sake of it").
AI_READINESS_TIERS: list[str] = ["ai_ready", "partial", "not_suitable"]
VERDICT_LABELS: dict[str, str] = {
    "ai_ready": "AI-ready (wholly)",
    "partial": "Partially AI-ready",
    "not_suitable": "Not suitable for AI",
}
VERDICT_AI_READY_MIN: float = 0.65
VERDICT_PARTIAL_MIN: float = 0.45
SCAN_AI_READY_RATIO_MIN: float = 0.50
SCAN_PARTIAL_RATIO_MIN: float = 0.20

# ── Innovation / Automation / Enhancement category ────────────────────────────
CATEGORIES: list[str] = ["innovation", "automation", "enhancement"]
CATEGORY_MAP: dict[str, str] = {
    "scheduled_cron": "automation",
    "db_render_notify_chain": "automation",
    "large_rule_table": "innovation",
    "nested_conditionals": "innovation",
    "regex_user_input": "enhancement",
    "keyword_list_search": "enhancement",
    "string_template_rendering": "enhancement",
    "hardcoded_threshold": "enhancement",
    "document_ingestion_pipeline": "automation",
    "ocr_extraction_pipeline": "enhancement",
    "fulltext_search_engine": "enhancement",
    "manual_classification_ui": "innovation",
    "metadata_extraction": "enhancement",
    "document_routing": "automation",
}

# ── Component derivation tables (replace the old flat-0.5 defaults) ────────────
PATTERN_BASE_SIGNALS: dict[str, dict[str, float]] = {
    "nested_conditionals":       {"usage_freq": 0.55, "automation_deficit": 0.55},
    "regex_user_input":          {"usage_freq": 0.65, "automation_deficit": 0.55},
    "string_template_rendering": {"usage_freq": 0.55, "automation_deficit": 0.50},
    "scheduled_cron":            {"usage_freq": 0.70, "automation_deficit": 0.65},
    "hardcoded_threshold":       {"usage_freq": 0.45, "automation_deficit": 0.70},
    "db_render_notify_chain":    {"usage_freq": 0.75, "automation_deficit": 0.70},
    "keyword_list_search":       {"usage_freq": 0.60, "automation_deficit": 0.55},
    "large_rule_table":          {"usage_freq": 0.60, "automation_deficit": 0.60},
    "document_ingestion_pipeline":{"usage_freq": 0.80, "automation_deficit": 0.70},
    "ocr_extraction_pipeline":   {"usage_freq": 0.75, "automation_deficit": 0.65},
    "fulltext_search_engine":    {"usage_freq": 0.70, "automation_deficit": 0.60},
    "manual_classification_ui":  {"usage_freq": 0.65, "automation_deficit": 0.70},
    "metadata_extraction":       {"usage_freq": 0.70, "automation_deficit": 0.60},
    "document_routing":          {"usage_freq": 0.60, "automation_deficit": 0.55},
}
DATA_AVAIL_BY_PARADIGM: dict[str, float] = {
    "embedding_search": 0.85, "llm_generation": 0.80, "nlp_extractor": 0.70,
    "anomaly_detection": 0.65, "agentic_trigger": 0.60, "ml_classifier": 0.55,
    "decision_agent": 0.45,
}
INTEGRATION_COMPLEXITY_BY_PARADIGM: dict[str, float] = {
    "llm_generation": 0.30, "embedding_search": 0.35, "nlp_extractor": 0.40,
    "anomaly_detection": 0.45, "ml_classifier": 0.55, "agentic_trigger": 0.60,
    "decision_agent": 0.70,
}
DEP_COMPLEXITY_BY_PARADIGM: dict[str, float] = {
    "llm_generation": 0.30, "nlp_extractor": 0.35, "agentic_trigger": 0.40,
    "anomaly_detection": 0.40, "ml_classifier": 0.50, "embedding_search": 0.55,
    "decision_agent": 0.65,
}
SIDE_EFFECT_PATTERNS: set[str] = {"scheduled_cron", "db_render_notify_chain"}

HIGH_USAGE_PATH_RE = r"(handler|view|controller|route|api|service|endpoint|blueprint)"
SCHED_PATH_RE = r"(scheduler|cron|job|worker|task)"
LOW_USAGE_PATH_RE = r"(test|spec|migration|fixture|mock|/scripts/|example)"

# ── Disqualifier thresholds ("don't AI-ify for the sake of it") ───────────────
DISQ_TRIVIAL_MIN_DEPTH: int = 3
DISQ_TRIVIAL_MIN_LOC: int = 6
DISQ_TRIVIAL_MIN_KEYS: int = 10
DISQ_NO_DATA_MAX: float = 0.30
DISQ_COMPLIANCE_INV_MAX: float = 0.40
DISQ_REVERSIBILITY_MAX: float = 0.50
SECURITY_INAPPROPRIATE_PARADIGMS: set[str] = {
    "ml_classifier", "decision_agent", "anomaly_detection",
}

# ── SQL CHECK strings for the new scoring columns ─────────────────────────────
_readiness_list = ", ".join(f"'{r}'" for r in AI_READINESS_TIERS)
_category_list = ", ".join(f"'{c}'" for c in CATEGORIES)
CHECK_AI_READINESS = f"ai_readiness IN ({_readiness_list})"
CHECK_CATEGORY = f"category IN ({_category_list})"
CHECK_OVERALL_AI_READINESS = f"overall_ai_readiness IN ({_readiness_list})"

# ── Compliance Posture ────────────────────────────────────────────────────────
# Deterministic AI-governance posture dimensions for the AI-ify canvas. Weights
# sum to 1.0; the posture engine renormalises over the dimensions that apply.
# See tools/aiify/posture.py for the per-dimension computation.
POSTURE_WEIGHTS: dict[str, float] = {
    "human_oversight": 0.20,
    "data_sovereignty": 0.20,
    "auditability": 0.15,
    "risk_triage": 0.15,
    "lifecycle_management": 0.15,
    "classification_marking": 0.15,
}

POSTURE_DIMENSION_LABELS: dict[str, str] = {
    "human_oversight": "Human Oversight (HITL)",
    "data_sovereignty": "Data Sovereignty (IL Model Routing)",
    "auditability": "Auditability",
    "risk_triage": "Risk Triage",
    "lifecycle_management": "Lifecycle Management",
    "classification_marking": "Classification Marking (CUI)",
}

# Each dimension maps to the AI-governance controls it provides evidence for.
POSTURE_FRAMEWORK_CROSSWALK: dict[str, list[dict]] = {
    "human_oversight": [
        {"framework": "NIST AI RMF", "control": "GOVERN-1.1"},
        {"framework": "OMB M-25-21", "control": "Human Oversight"},
        {"framework": "DoD AI Ethics", "control": "Governable"},
        {"framework": "NIST 800-53", "control": "AC-3"},
    ],
    "data_sovereignty": [
        {"framework": "DoD Cloud", "control": "IL4 / IL5 / IL6"},
        {"framework": "NIST AI RMF", "control": "MAP-3.4"},
        {"framework": "NIST 800-53", "control": "SC-7"},
    ],
    "auditability": [
        {"framework": "NIST 800-53", "control": "AU-2 / AU-12"},
        {"framework": "NIST AI RMF", "control": "GOVERN-1.2"},
    ],
    "risk_triage": [
        {"framework": "NIST AI RMF", "control": "MEASURE-2"},
        {"framework": "NIST AI 600-1", "control": "GAI Risk"},
        {"framework": "NIST 800-53", "control": "RA-3"},
    ],
    "lifecycle_management": [
        {"framework": "NIST AI RMF", "control": "MANAGE-1"},
        {"framework": "NIST 800-53", "control": "SA-3 / SA-8"},
    ],
    "classification_marking": [
        {"framework": "32 CFR 2002", "control": "CUI"},
        {"framework": "NIST 800-53", "control": "AC-16 / MP-3"},
        {"framework": "DoDI 5200.48", "control": "CUI Marking"},
    ],
}

POSTURE_DIMENSION_RATIONALE: dict[str, str] = {
    "human_oversight": "Triage each scan's AI opportunities into a governed workflow — promote worthwhile ones onto the tracked kanban board and record HITL accept/reject decisions.",
    "data_sovereignty": "Assign an IL-appropriate recommended model to every opportunity so AI workloads stay within their authorised impact level.",
    "auditability": "Ensure each scan emits audit-trail events; the AI-ify audit log is append-only evidence for AU-2/AU-12.",
    "risk_triage": "Run the deterministic opportunity scorer on every opportunity to triage value, feasibility and risk before AI adoption.",
    "lifecycle_management": "Generate a managed remediation roadmap for each scan so AI adoption follows a governed lifecycle.",
    "classification_marking": "Maintain CUI // SP-CTI markings on all generated artifacts (PRDs, roadmaps, scan records).",
}
