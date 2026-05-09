# CUI // SP-CTI
"""AIMC — Deployment Planner.

Recommends inference server configuration, quantization strategy,
and resource requirements based on design graph and IL constraints.
"""

from __future__ import annotations

from tools.aiml_canvas.constants import (
    FOUNDATION_MODELS,
    IL_REQUIREMENTS,
    QUANTIZATION_CONFIGS,
)


def plan(
    *,
    model_id: str,
    il_level: str = "IL4",
    vram_gb: float = 8.0,
    latency_target_ms: int = 2000,
    throughput_rps: int = 1,
    air_gap_required: bool | None = None,
) -> dict:
    """Return deployment recommendation for a given model + constraints."""
    model = next((m for m in FOUNDATION_MODELS if m["id"] == model_id), None)
    if not model:
        return {"error": f"Model {model_id} not found in catalog"}

    il_req = IL_REQUIREMENTS.get(il_level, {})
    if air_gap_required is None:
        air_gap_required = il_req.get("must_be_air_gap", False)

    allowed_providers = il_req.get("allowed_providers", [])
    model_provider = model.get("provider", "")
    provider_allowed = any(p in model_provider for p in allowed_providers) if allowed_providers else True

    # Select quantization
    model_vram = model.get("vram_gb", 16)
    quant = _select_quantization(model_vram, vram_gb)
    effective_vram = model_vram * QUANTIZATION_CONFIGS[quant]["vram_multiplier"]

    # Select inference server
    server = _select_server(model, il_level, air_gap_required, throughput_rps, vram_gb)

    warnings: list[str] = []
    if not provider_allowed:
        warnings.append(f"Model provider '{model_provider}' not approved for {il_level} — use Ollama local models")
    if not model.get("air_gap_ready") and air_gap_required:
        warnings.append("Model is not air-gap ready but air-gap is required — select an Ollama/GGUF model")
    if effective_vram > vram_gb:
        warnings.append(f"Effective VRAM needed ({effective_vram:.1f}GB) exceeds available ({vram_gb}GB) — use stronger quantization or CPU offload")

    return {
        "model": {
            "id": model_id,
            "name": model.get("name"),
            "provider": model_provider,
            "air_gap_ready": model.get("air_gap_ready", False),
        },
        "quantization": {
            "recommended": quant,
            "label": QUANTIZATION_CONFIGS[quant]["label"],
            "effective_vram_gb": round(effective_vram, 1),
            "quality": QUANTIZATION_CONFIGS[quant]["quality"],
            "notes": QUANTIZATION_CONFIGS[quant]["notes"],
        },
        "inference_server": server,
        "il_assessment": {
            "il_level": il_level,
            "air_gap_required": air_gap_required,
            "provider_approved": provider_allowed,
            "encryption_required": il_req.get("required_encryption", "TLS 1.2+"),
        },
        "warnings": warnings,
        "gpu_requirements": {
            "vram_gb_required": round(effective_vram, 1),
            "vram_gb_available": vram_gb,
            "gpu_sufficient": effective_vram <= vram_gb,
            "recommended_gpu": _recommend_gpu(effective_vram),
        },
    }


def _select_quantization(model_vram_fp16: float, available_vram: float) -> str:
    for quant, cfg in [("fp16", 1.0), ("Q8_0", 0.5), ("Q5_K_M", 0.31), ("Q4_K_M", 0.25), ("int4", 0.22)]:
        if model_vram_fp16 * cfg <= available_vram * 0.9:
            return quant
    return "Q4_K_M"


_CSP_SERVER_MAP: dict[str, dict] = {
    "Azure OpenAI": {
        "name": "Azure ML Managed Online Endpoint",
        "type": "deploy-azure-ml",
        "api_compatible": "Azure OpenAI REST API (OpenAI-compatible)",
        "latency_p50_ms": 400,
        "cost_per_hour_usd": 0.0,  # per-token pricing
        "notes": "Azure Government for IL4; FedRAMP High; auto-scaling",
    },
    "GCP Vertex AI": {
        "name": "Vertex AI Prediction",
        "type": "deploy-vertex-ai",
        "api_compatible": "Vertex AI REST API",
        "latency_p50_ms": 500,
        "cost_per_hour_usd": 0.0,
        "notes": "GCP FedRAMP High; serverless or dedicated GPU node",
    },
    "OCI GenAI": {
        "name": "OCI GenAI Service Endpoint",
        "type": "deploy-oci-genai",
        "api_compatible": "OCI GenAI REST API (Cohere-compatible)",
        "latency_p50_ms": 600,
        "cost_per_hour_usd": 0.0,
        "notes": "OCI GovCloud for IL4; FedRAMP Moderate/High",
    },
    "IBM watsonx.ai": {
        "name": "IBM watsonx.ai Deployment Space",
        "type": "deploy-watsonx-ai",
        "api_compatible": "watsonx.ai REST API (IBM Cloud SDK)",
        "latency_p50_ms": 700,
        "cost_per_hour_usd": 0.0,
        "notes": "IBM GovCloud FedRAMP High; integrated governance and audit",
    },
    "AWS Bedrock": {
        "name": "AWS Bedrock On-Demand Inference",
        "type": "deploy-bedrock",
        "api_compatible": "Bedrock InvokeModel API (Converse API recommended)",
        "latency_p50_ms": 350,
        "cost_per_hour_usd": 0.0,
        "notes": "GovCloud FedRAMP High; no provisioning; per-token billing",
    },
}


def _select_server(model: dict, il_level: str, air_gap: bool, rps: int, vram_gb: float) -> dict:
    if air_gap or il_level in ("IL5", "IL6"):
        return {
            "name": "Ollama",
            "type": "deploy-ollama",
            "image": "ollama/ollama:latest",
            "port": 11434,
            "api_compatible": "OpenAI-compatible (/api/chat)",
            "reason": "Air-gap / IL5-IL6 requires local-only inference",
            "config": {
                "OLLAMA_NUM_GPU": 1 if vram_gb >= 4 else 0,
                "OLLAMA_MAX_LOADED_MODELS": 2,
            },
        }

    # CSP managed inference — prefer cloud endpoint when model is cloud-native
    provider = model.get("provider", "")
    for csp_key, server_info in _CSP_SERVER_MAP.items():
        if csp_key in provider:
            return {
                **server_info,
                "reason": f"Model is hosted on {csp_key} managed infrastructure — use native endpoint",
            }

    # High-throughput self-hosted
    if rps >= 20:
        return {
            "name": "vLLM",
            "type": "deploy-vllm",
            "image": "vllm/vllm-openai:latest",
            "port": 8000,
            "api_compatible": "OpenAI-compatible",
            "reason": f"High throughput ({rps} RPS) favors vLLM continuous batching",
            "config": {
                "--tensor-parallel-size": max(1, int(vram_gb / 24)),
                "--max-model-len": 8192,
            },
        }
    return {
        "name": "Ollama",
        "type": "deploy-ollama",
        "image": "ollama/ollama:latest",
        "port": 11434,
        "api_compatible": "OpenAI-compatible (/api/chat)",
        "reason": "Default local server — easy setup, GGUF support",
        "config": {"OLLAMA_NUM_GPU": 1},
    }


def _recommend_gpu(vram_needed: float) -> str:
    if vram_needed <= 8:
        return "NVIDIA RTX 4060 Ti 8GB (development) / L4 24GB (production)"
    if vram_needed <= 24:
        return "NVIDIA RTX 4090 24GB / L40S 48GB"
    if vram_needed <= 48:
        return "NVIDIA A100 40GB or L40S 48GB"
    return "NVIDIA A100 80GB or H100 80GB"
