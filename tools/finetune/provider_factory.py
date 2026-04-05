#!/usr/bin/env python3
# CUI // SP-CTI
"""FineTuneProvider factory with GPU auto-detection (D-FT-1, D-FT-8).

Creates the appropriate provider based on configuration and hardware:
  - Local GPU available → UnslothLocalProvider
  - Cloud configured → OpenAI / Bedrock / Azure provider
  - CPU only → CPU serving via Ollama (training not supported)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from tools.finetune.provider import FineTuneProvider
from tools.finetune.gpu_detector import detect_gpu, GPUDetectionResult

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _load_config() -> Dict[str, Any]:
    """Load finetune_config.yaml."""
    config_path = BASE_DIR / "args" / "finetune_config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml

        with open(config_path, "r") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        return {}


def get_provider(
    provider_name: Optional[str] = None,
    gpu_result: Optional[GPUDetectionResult] = None,
) -> FineTuneProvider:
    """Create a FineTuneProvider instance.

    Args:
        provider_name: Explicit provider ('unsloth_local', 'openai', 'bedrock',
                       'azure_openai'). If None, auto-detect from config + GPU.
        gpu_result: Pre-computed GPU detection result (avoids re-detection).

    Returns:
        A FineTuneProvider implementation.

    Raises:
        ImportError: If the required SDK for the provider is not installed.
        ValueError: If the provider name is unknown.
    """
    config = _load_config()

    if provider_name is None:
        provider_name = _auto_select_provider(config, gpu_result)

    if provider_name == "unsloth_local":
        from tools.finetune.unsloth_provider import UnslothLocalProvider

        return UnslothLocalProvider(config=config, gpu_result=gpu_result)

    elif provider_name == "openai":
        from tools.finetune.openai_provider import OpenAIFineTuneProvider

        return OpenAIFineTuneProvider(config=config)

    elif provider_name == "bedrock":
        from tools.finetune.bedrock_provider import BedrockFineTuneProvider

        return BedrockFineTuneProvider(config=config)

    elif provider_name == "azure_openai":
        from tools.finetune.azure_provider import AzureOpenAIFineTuneProvider

        return AzureOpenAIFineTuneProvider(config=config)

    else:
        raise ValueError(f"Unknown provider: {provider_name}. Valid: unsloth_local, openai, bedrock, azure_openai")


def _auto_select_provider(
    config: Dict[str, Any],
    gpu_result: Optional[GPUDetectionResult] = None,
) -> str:
    """Auto-select the best provider based on config and hardware.

    Priority:
        1. Explicit default in config
        2. Local GPU available → unsloth_local
        3. Cloud provider configured → first available cloud
        4. Fallback to unsloth_local (will fail at training time if no GPU)
    """
    # Check explicit config default
    default = config.get("default_provider", "")
    if default:
        return default

    # Detect GPU if not provided
    if gpu_result is None:
        gpu_result = detect_gpu()

    # Local GPU → use Unsloth
    if gpu_result.can_train:
        return "unsloth_local"

    # Check cloud providers
    cloud = config.get("cloud", {})
    for provider_key in ("openai", "bedrock", "azure_openai"):
        provider_config = cloud.get(provider_key, {})
        if provider_config.get("enabled", False):
            return provider_key

    # Default to unsloth_local (user needs GPU or will get error)
    return "unsloth_local"


def list_available_providers(
    gpu_result: Optional[GPUDetectionResult] = None,
) -> Dict[str, Any]:
    """List all providers and their availability status."""
    config = _load_config()
    if gpu_result is None:
        gpu_result = detect_gpu()

    providers = {}

    # Unsloth local
    providers["unsloth_local"] = {
        "available": gpu_result.can_train,
        "reason": "GPU detected" if gpu_result.can_train else "No GPU / insufficient VRAM",
        "gpu_count": gpu_result.gpu_count,
        "vram_mb": gpu_result.total_vram_mb,
    }

    # Cloud providers
    cloud = config.get("cloud", {})
    for name, display in [
        ("openai", "OpenAI Fine-Tuning"),
        ("bedrock", "AWS Bedrock Custom Model"),
        ("azure_openai", "Azure OpenAI Fine-Tuning"),
    ]:
        pc = cloud.get(name, {})
        enabled = pc.get("enabled", False)
        providers[name] = {
            "available": enabled,
            "reason": "Enabled in config" if enabled else "Not configured",
        }

    return {
        "providers": providers,
        "auto_selected": _auto_select_provider(config, gpu_result),
        "gpu": gpu_result.to_dict(),
    }
