# CUI // SP-CTI
"""AI Modernization Decision Tree — maps existing stack to recommended injection pattern.

Given current stack (language, architecture style, data state) returns:
  - recommended_pattern: slug from INJECTION_PATTERNS
  - aadc_template: AADC canvas template to load
  - learning_path: ordered list of Academy mission slugs
  - rationale: plain-English explanation
  - phase: Phase 0-3 recommendation
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

# Ensure the ICDev project root is on sys.path so apps.forge_academy resolves
# correctly even when icdev/ package prepends its own apps/ to sys.path.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


def _get_pattern_by_id() -> dict:
    """Import PATTERN_BY_ID using a direct file path to avoid icdev/apps/ shadowing."""
    import importlib.util
    _pat_file = os.path.join(_PROJECT_ROOT, "apps", "forge_academy", "patterns.py")
    spec = importlib.util.spec_from_file_location("_forge_academy_patterns", _pat_file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.PATTERN_BY_ID


# ---------------------------------------------------------------------------
# Decision tree inputs
# ---------------------------------------------------------------------------

LANGUAGES = ["Python", "Java", "TypeScript / JavaScript", "Go", "C# / .NET", "Other"]

ARCHITECTURES = [
    "Monolith (single deployable)",
    "Microservices / APIs",
    "Serverless / Functions",
    "Legacy / Mainframe",
    "Desktop App",
]

DATA_STATES = [
    "Clean, structured database (SQL)",
    "Unstructured documents (PDFs, Word, reports)",
    "Time-series / metrics / logs",
    "Mixed structured + unstructured",
    "No significant data corpus",
]

USER_GOALS = [
    "Add natural language interface for end users",
    "Make documents and knowledge searchable",
    "Automate a repetitive manual review process",
    "Improve search / discovery in existing catalog",
    "Add AI recommendations to a human workflow",
    "Detect anomalies or predict failures",
    "Modernize a multi-step workflow end-to-end",
    "Enhance existing API responses with AI insights",
]

TEAM_AI_READINESS = [
    "No AI experience — starting from scratch",
    "Some prompt engineering experience",
    "Have built RAG pipelines",
    "Have deployed production AI agents",
]


# ---------------------------------------------------------------------------
# Decision logic
# ---------------------------------------------------------------------------

@dataclass
class ModernizationRecommendation:
    pattern_id: str
    pattern_title: str
    phase: str
    phase_tag: str
    aadc_template: str
    learning_path: list[str] = field(default_factory=list)
    rationale: str = ""
    prerequisite_work: list[str] = field(default_factory=list)
    sdk_mission: str = ""


def recommend(
    language: str = "",
    architecture: str = "",
    data_state: str = "",
    goal: str = "",
    team_readiness: str = "",
) -> ModernizationRecommendation:
    """Run the decision tree and return a recommendation."""

    # Normalize inputs
    lang = (language or "").lower()
    arch = (architecture or "").lower()
    data = (data_state or "").lower()
    goal_lower = (goal or "").lower()
    ready = (team_readiness or "").lower()

    # 1. Goal-first routing (strongest signal)
    if "natural language" in goal_lower or "chat" in goal_lower:
        pattern_id = "conversational-wrapper"
        rationale = "Your goal of adding natural language interaction maps directly to the Conversational Wrapper pattern — the lowest-risk, highest-value AI injection for any existing CRUD application."
        learning_path = ["m02-prompt-engineering", "m03-rag-basics", "m05-mcp-protocol"]
    elif "search" in goal_lower or "discover" in goal_lower:
        pattern_id = "intelligent-search"
        rationale = "Replacing keyword search with semantic search is a Phase 1 Quick Win with minimal architectural change. Your existing data store becomes the index."
        learning_path = ["m03-rag-basics", "m-dataops-02-chromadb-rag"]
    elif "document" in goal_lower or "knowledge" in goal_lower:
        pattern_id = "document-intelligence"
        rationale = "Unstructured document corpora are the highest-value RAG target. Your users will be able to query years of accumulated knowledge in seconds."
        learning_path = ["m03-rag-basics", "m-dataops-01-advanced-rag", "m-dataops-02-chromadb-rag"]
    elif "review" in goal_lower or "approval" in goal_lower or "recommendation" in goal_lower:
        pattern_id = "decision-assist"
        rationale = "Human-in-the-loop decision support is the safest Phase 1 approach — AI drafts, human decides. No automation risk, immediate productivity gain."
        learning_path = ["m02-prompt-engineering", "m03-rag-basics"]
    elif "anomaly" in goal_lower or "predict" in goal_lower or "detect" in goal_lower:
        pattern_id = "predictive-alert"
        rationale = "Your existing metrics stream is the input. The AI layer adds pattern recognition and contextual explanations without replacing your monitoring infrastructure."
        learning_path = ["m-sre-01-slo-agent", "m-analyst-03-pattern-detection"]
    elif "automate" in goal_lower or "manual" in goal_lower or "triage" in goal_lower:
        if "starting from scratch" in ready or "some prompt" in ready:
            pattern_id = "decision-assist"
            rationale = "Given your team's current readiness, start with Decision Assist (human-in-the-loop) before moving to full automation. It delivers value immediately while building confidence."
            learning_path = ["m02-prompt-engineering", "m03-rag-basics", "m04-first-agent"]
        else:
            pattern_id = "autonomous-background-agent"
            rationale = "Your goal of automating manual processes maps to the Autonomous Background Agent. With your team's AI experience, you can safely deploy a supervised agent with audit logging."
            learning_path = ["m04-first-agent", "m07-multi-agent", "m-devops-01-pipeline-agent"]
    elif "workflow" in goal_lower or "orchestrat" in goal_lower or "end-to-end" in goal_lower:
        pattern_id = "workflow-orchestration"
        rationale = "Full workflow orchestration is a Phase 3 Transformation initiative. Ensure your team completes Phase 1 Quick Wins first to build the foundational skills and stakeholder trust."
        learning_path = ["m07-multi-agent", "m08-strands-agents", "m09-langchain"]
    elif "api" in goal_lower or "enhance" in goal_lower:
        pattern_id = "ai-augmented-api"
        rationale = "AI-augmented APIs are a backward-compatible Phase 1 pattern — add intelligence to your existing API contract without changing upstream or downstream systems."
        learning_path = ["m02-prompt-engineering", "m05-mcp-protocol"]
    else:
        # Data-state fallback routing
        if "unstructured" in data or "document" in data or "pdf" in data:
            pattern_id = "document-intelligence"
            rationale = "Your unstructured document corpus is your most valuable untapped AI asset. RAG over documents delivers measurable value in days."
            learning_path = ["m03-rag-basics", "m-dataops-01-advanced-rag"]
        elif "time-series" in data or "metrics" in data or "logs" in data:
            pattern_id = "predictive-alert"
            rationale = "Time-series and log data are the natural input for predictive alerting. Your existing monitoring stack provides the baseline."
            learning_path = ["m-sre-01-slo-agent"]
        else:
            pattern_id = "conversational-wrapper"
            rationale = "Without a specific data constraint, the Conversational Wrapper is the universal Phase 1 starting point — it works on any existing application."
            learning_path = ["m02-prompt-engineering", "m03-rag-basics"]

    # Determine SDK mission based on language
    sdk_map = {
        "java": "m-swe-sdk-java",
        "typescript": "m-swe-sdk-typescript",
        "javascript": "m-swe-sdk-typescript",
        "go": "m-swe-sdk-go",
        "c#": "m-swe-sdk-dotnet",
        ".net": "m-swe-sdk-dotnet",
    }
    sdk_mission = ""
    for k, v in sdk_map.items():
        if k in lang:
            sdk_mission = v
            break
    if sdk_mission and sdk_mission not in learning_path:
        learning_path.append(sdk_mission)

    # Phase determination
    PATTERN_BY_ID = _get_pattern_by_id()
    pattern = PATTERN_BY_ID.get(pattern_id, {})
    phase = pattern.get("phase", "Phase 1 — Quick Win")
    phase_tag = pattern.get("phase_tag", "phase1")
    aadc_template = pattern.get("aadc_template", "")
    pattern_title = pattern.get("title", pattern_id)

    # Prerequisite work based on team readiness
    prereqs = []
    if "starting from scratch" in ready:
        prereqs.append("Complete FORGE Academy Tier 1 (M01–M10) before starting this pattern")
        prereqs.append("Ensure Ollama is running locally or an LLM API key is configured")
    if "legacy" in arch or "mainframe" in arch:
        prereqs.append("Build an API adapter layer around your legacy system before injecting AI")
    if "no significant data" in data and pattern_id in ("document-intelligence", "intelligent-search"):
        prereqs.append("Identify and collect your document corpus before building the RAG pipeline")

    return ModernizationRecommendation(
        pattern_id=pattern_id,
        pattern_title=pattern_title,
        phase=phase,
        phase_tag=phase_tag,
        aadc_template=aadc_template,
        learning_path=learning_path,
        rationale=rationale,
        prerequisite_work=prereqs,
        sdk_mission=sdk_mission,
    )


def recommend_json(inputs: dict) -> dict:
    """Convenience wrapper returning a JSON-serializable dict."""
    rec = recommend(**inputs)
    PATTERN_BY_ID = _get_pattern_by_id()
    pattern = PATTERN_BY_ID.get(rec.pattern_id, {})
    return {
        "pattern_id": rec.pattern_id,
        "pattern_title": rec.pattern_title,
        "phase": rec.phase,
        "phase_tag": rec.phase_tag,
        "aadc_template": rec.aadc_template,
        "learning_path": rec.learning_path,
        "rationale": rec.rationale,
        "prerequisite_work": rec.prerequisite_work,
        "sdk_mission": rec.sdk_mission,
        "pattern": pattern,
    }
