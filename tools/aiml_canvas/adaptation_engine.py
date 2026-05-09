# CUI // SP-CTI
"""AIMC — Adaptation Decision Engine.

Given use-case parameters, recommends the optimal adaptation strategy
(Prompt-only / RAG / Fine-tune / Hybrid) with rationale and tradeoffs.
"""

from __future__ import annotations

from tools.aiml_canvas.constants import ADAPTATION_MATRIX, FOUNDATION_MODELS, IL_REQUIREMENTS

# Maps CSP provider name → cloud fine-tuning node type for recommend()
CLOUD_FT_OPTIONS: dict[str, str] = {
    "AWS Bedrock":         "adapt-bedrock-ft",
    "Azure OpenAI":        "adapt-azure-openai-ft",
    "GCP Vertex AI":       "adapt-vertex-ft",
}

# Provider tier for ranking: higher = more preferred at a given IL
_PROVIDER_TIER: dict[str, int] = {
    "Ollama": 5,              # air-gap, zero cost
    "AWS Bedrock": 4,         # FedRAMP High GovCloud
    "Azure OpenAI": 4,        # Azure Government FedRAMP High
    "OCI GenAI": 3,           # OCI GovCloud
    "IBM watsonx.ai": 3,      # IBM GovCloud
    "GCP Vertex AI": 2,       # FedRAMP High (commercial)
    "HuggingFace Endpoints": 1,  # IL2 only
}


def recommend(
    *,
    has_corpus: bool = False,
    has_training_data: bool = False,
    training_examples: int = 0,
    has_gpu: bool = False,
    vram_gb: float = 0.0,
    latency_budget_ms: int = 2000,
    accuracy_target_pct: float = 75.0,
    il_level: str = "IL4",
    knowledge_changes_frequently: bool = False,
    requires_source_citation: bool = False,
    requires_consistent_format: bool = False,
    domain_specific_reasoning: bool = False,
) -> dict:
    """Return ranked strategy recommendations with scores and rationale."""

    scores: dict[str, float] = {"prompt_only": 0.0, "rag": 0.0, "finetune": 0.0, "hybrid": 0.0}

    # Prompt-only signals
    if not has_corpus and not has_training_data:
        scores["prompt_only"] += 40
    if latency_budget_ms < 500:
        scores["prompt_only"] += 20
    if accuracy_target_pct < 75:
        scores["prompt_only"] += 15
    if not domain_specific_reasoning:
        scores["prompt_only"] += 10

    # RAG signals
    if has_corpus:
        scores["rag"] += 40
        scores["hybrid"] += 20
    if requires_source_citation:
        scores["rag"] += 25
        scores["hybrid"] += 10
    if knowledge_changes_frequently:
        scores["rag"] += 20
    if not has_gpu or vram_gb < 8:
        scores["rag"] += 10  # RAG doesn't need GPU

    # Fine-tune signals
    if has_training_data and training_examples >= 500:
        scores["finetune"] += 35
        scores["hybrid"] += 20
    if requires_consistent_format:
        scores["finetune"] += 20
    if domain_specific_reasoning:
        scores["finetune"] += 25
        scores["hybrid"] += 15
    if has_gpu and vram_gb >= 8:
        scores["finetune"] += 15
        scores["hybrid"] += 10
    if accuracy_target_pct >= 90:
        scores["finetune"] += 15
        scores["hybrid"] += 25

    # IL constraints
    il_req = IL_REQUIREMENTS.get(il_level, {})
    if il_req.get("must_be_local") and not has_gpu:
        # Cloud-only models unavailable; RAG or prompt with local model
        scores["rag"] += 10
        scores["prompt_only"] += 5

    # Hybrid penalty unless both components are worth it
    if not has_corpus or not (has_training_data and training_examples >= 500):
        scores["hybrid"] = max(0, scores["hybrid"] - 20)

    best = max(scores, key=lambda k: scores[k])
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)

    # Suggest cloud FT node when appropriate
    cloud_ft_hint = None
    if has_training_data and training_examples >= 200 and not has_gpu:
        il_req = IL_REQUIREMENTS.get(il_level, {})
        allowed = il_req.get("allowed_providers", [])
        for provider, node_type in CLOUD_FT_OPTIONS.items():
            if any(p in provider for p in allowed):
                cloud_ft_hint = {"provider": provider, "node_type": node_type}
                break

    return {
        "recommended": best,
        "recommended_label": ADAPTATION_MATRIX[best]["label"],
        "ranked": [
            {
                "strategy": s,
                "label": ADAPTATION_MATRIX[s]["label"],
                "score": round(sc, 1),
                "cost": ADAPTATION_MATRIX[s]["cost"],
                "time_to_deploy": ADAPTATION_MATRIX[s]["time_to_deploy"],
                "accuracy_ceiling": ADAPTATION_MATRIX[s]["accuracy_ceiling"],
            }
            for s, sc in ranked
        ],
        "rationale": _build_rationale(best, has_corpus, has_training_data,
                                       training_examples, has_gpu, vram_gb,
                                       il_level, latency_budget_ms),
        "caveats": _build_caveats(best, training_examples, has_gpu, vram_gb),
        "cloud_ft_hint": cloud_ft_hint,
    }


def _build_rationale(strategy: str, has_corpus: bool, has_training_data: bool,
                     training_examples: int, has_gpu: bool, vram_gb: float,
                     il_level: str, latency_ms: int) -> list[str]:
    reasons: list[str] = []
    if strategy == "prompt_only":
        reasons.append("No document corpus or training dataset available — prompting is the only viable path.")
        if latency_ms < 500:
            reasons.append(f"Latency budget ({latency_ms}ms) favors prompt-only — RAG adds 50-200ms retrieval overhead.")
    elif strategy == "rag":
        if has_corpus:
            reasons.append("Document corpus available — RAG retrieval will inject relevant context at inference time.")
        if not has_gpu:
            reasons.append("No GPU configured — RAG works on CPU with local embedding models.")
        reasons.append("Source citation is achievable — retrieved chunks can be surfaced as references.")
    elif strategy == "finetune":
        if has_training_data and training_examples >= 500:
            reasons.append(f"{training_examples} training examples available — sufficient for QLoRA fine-tuning.")
        if has_gpu and vram_gb >= 8:
            reasons.append(f"{vram_gb}GB VRAM available — QLoRA/LoRA training is feasible.")
        reasons.append("Domain-specific reasoning pattern required — fine-tuning embeds these patterns in weights.")
    elif strategy == "hybrid":
        reasons.append("Both document corpus and training data available — hybrid maximizes accuracy.")
        reasons.append("Fine-tuned weights provide domain reasoning; RAG provides current facts from classified corpus.")
    if il_level in ("IL5", "IL6"):
        reasons.append(f"{il_level} constraint requires local/air-gap deployment — all strategies use Ollama.")
    return reasons


def _build_caveats(strategy: str, training_examples: int, has_gpu: bool, vram_gb: float) -> list[str]:
    caveats: list[str] = []
    if strategy == "finetune":
        if training_examples < 500:
            caveats.append("WARNING: fewer than 500 examples — fine-tuning may underfit; consider prompt-only first.")
        if not has_gpu:
            caveats.append("WARNING: no GPU detected — CPU-only fine-tuning is impractical; use cloud provider.")
        elif vram_gb < 8:
            caveats.append(f"WARNING: {vram_gb}GB VRAM may be insufficient for QLoRA — min 8GB recommended.")
    if strategy == "hybrid":
        caveats.append("Hybrid requires both GPU (fine-tuning) and corpus (RAG) — highest cost and complexity.")
    return caveats


def rank_models_for_il(il_level: str) -> list[dict]:
    """Return foundation models sorted by suitability for a given IL level.

    Scoring:
      +40 air_gap_ready (mandatory for IL5/IL6)
      +20 provider in IL allowed_providers list
      +15 zero cost (local models)
      +10 provider tier bonus (Ollama=5 > Bedrock/Azure Gov=4 > OCI/IBM=3 > GCP=2 > HF=1) * 2
    """
    il_int = int(il_level.replace("IL", "")) if il_level.startswith("IL") else 4
    il_req = IL_REQUIREMENTS.get(il_level, {})
    allowed_providers = il_req.get("allowed_providers", [m["provider"] for m in FOUNDATION_MODELS])

    ranked = []
    for m in FOUNDATION_MODELS:
        if il_int not in m.get("il_suitability", []):
            continue
        provider = m.get("provider", "")
        score = 0
        if m.get("air_gap_ready"):
            score += 40
        if any(p in provider for p in allowed_providers):
            score += 20
        if m.get("cost_per_1k_tokens", 1.0) == 0.0:
            score += 15
        tier = _PROVIDER_TIER.get(provider, 0)
        score += tier * 2
        ranked.append({**m, "_score": score})

    ranked.sort(key=lambda x: x["_score"], reverse=True)
    for r in ranked:
        r.pop("_score", None)
    return ranked
