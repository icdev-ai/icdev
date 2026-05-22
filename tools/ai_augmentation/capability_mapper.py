# CUI // SP-CTI
"""AI Augmentation Canvas — Capability Mapper.

Maps detected code patterns to recommended AI/ML capabilities,
models, and providers based on IL level and pattern catalog.

Public API:
    map_capability(pattern_type: str, il_level: str = "il4") -> dict
    suggest_implementation_approach(paradigm: str) -> str
"""

from __future__ import annotations

import json
import pathlib

# ── Config ────────────────────────────────────────────────────────────────────

_REPO_ROOT = pathlib.Path(__file__).parent.parent.parent

_PATTERN_CATALOG_PATH = _REPO_ROOT / "context" / "ai_augmentation" / "pattern_catalog.json"
_IL_MODEL_MATRIX_PATH = _REPO_ROOT / "context" / "ai_augmentation" / "il_model_matrix.json"

_SUPPORTED_IL_LEVELS: frozenset[str] = frozenset({"il4", "il5", "il6"})

# ── Cache ───────────────────────────────────────────────────────────────────────

_catalog: dict | None = None
_matrix: dict | None = None


def _load_json(path: pathlib.Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _get_catalog() -> dict:
    global _catalog
    if _catalog is None:
        _catalog = _load_json(_PATTERN_CATALOG_PATH)
    return _catalog


def _get_matrix() -> dict:
    global _matrix
    if _matrix is None:
        _matrix = _load_json(_IL_MODEL_MATRIX_PATH)
    return _matrix


# ── Core API ────────────────────────────────────────────────────────────────────


def map_capability(pattern_type: str, il_level: str = "il4") -> dict:
    """Return capability mapping for a pattern at a given IL level.

    Args:
        pattern_type: Canonical pattern ID (e.g. "nested_conditionals").
        il_level: Information level — "il4" (default), "il5", or "il6".

    Returns:
        Dict with keys:
            - ai_paradigm: str
            - recommended_model: str
            - provider: str
            - data_requirements: str
            - zero_shot_feasible: bool
            - notes: str

    Raises:
        ValueError: If pattern_type is unknown, il_level is unsupported,
            or the matrix lacks a mapping for the (paradigm, level) pair.
    """
    if il_level not in _SUPPORTED_IL_LEVELS:
        raise ValueError(
            f"Unsupported il_level '{il_level}'. Must be one of: {sorted(_SUPPORTED_IL_LEVELS)}"
        )

    catalog = _get_catalog()
    patterns = catalog.get("patterns", [])
    pattern = next((p for p in patterns if p.get("id") == pattern_type), None)
    if pattern is None:
        raise ValueError(f"Unknown pattern_type '{pattern_type}'. Check pattern_catalog.json.")

    ai_paradigm: str = pattern.get("ai_paradigm", "")
    if not ai_paradigm:
        raise ValueError(f"Pattern '{pattern_type}' has no ai_paradigm mapping in catalog.")

    matrix = _get_matrix()
    paradigm_mappings = matrix.get("mappings", {}).get(ai_paradigm)
    if paradigm_mappings is None:
        raise ValueError(
            f"No IL model matrix entry for paradigm '{ai_paradigm}'."
        )

    level_mapping = paradigm_mappings.get(il_level)
    if level_mapping is None:
        raise ValueError(
            f"No IL model matrix entry for paradigm '{ai_paradigm}' at level '{il_level}'."
        )

    return {
        "ai_paradigm": ai_paradigm,
        "recommended_model": level_mapping.get("recommended_model", ""),
        "provider": level_mapping.get("provider", ""),
        "data_requirements": level_mapping.get("data_requirements", ""),
        "zero_shot_feasible": level_mapping.get("zero_shot_feasible", False),
        "notes": level_mapping.get("notes", ""),
    }


# ── Implementation Suggestions ───────────────────────────────────────────────

_IMPLEMENTATION_SUGGESTIONS: dict[str, str] = {
    "llm_generation": (
        "Replace static templates with an LLM prompt pipeline: define a system prompt, "
        "validate outputs with a JSON schema, and cache completions to reduce token costs."
    ),
    "ml_classifier": (
        "Start with a scikit-learn baseline (Random Forest or XGBoost) on your labeled dataset; "
        "once accuracy plateaus, consider AutoML or neural architectures if data volume supports it."
    ),
    "embedding_search": (
        "Index your corpus into a vector store (ChromaDB, Qdrant, or Milvus) using sentence-transformers; "
        "serve queries via cosine-similarity retrieval with optional metadata filtering."
    ),
    "anomaly_detection": (
        "Establish a statistical baseline first (z-score or IQR), then layer an Isolation Forest "
        "or autoencoder for multivariate anomalies; ensure historical data covers seasonal variance."
    ),
    "decision_agent": (
        "Model the decision space as a POMDP or multi-agent graph; train policies in simulation, "
        "then deploy with guardrails and human-in-the-loop approval for high-stakes actions."
    ),
    "agentic_trigger": (
        "Replace cron with an event-driven agent that observes state changes and reasons via ReAct; "
        "use LangChain/LangGraph tools and log every trigger for auditability."
    ),
    "nlp_extractor": (
        "Leverage a pre-trained NER model (BERT or spaCy) for zero-shot extraction; "
        "fine-tune on domain annotations only if entity types are specialized or accuracy falls below 90%."
    ),
}


def suggest_implementation_approach(paradigm: str) -> str:
    """Return a 1-2 sentence implementation suggestion for the given AI paradigm.

    Args:
        paradigm: One of the canonical AI paradigm identifiers.

    Returns:
        A concise architect-facing implementation recommendation.
    """
    suggestion = _IMPLEMENTATION_SUGGESTIONS.get(paradigm)
    if suggestion is None:
        return (
            f"No standard implementation suggestion for paradigm '{paradigm}'. "
            "Review the AI Augmentation pattern catalog and select a model that matches your data and latency constraints."
        )
    return suggestion
