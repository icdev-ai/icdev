# CUI // SP-CTI
"""Configuration for Autonomous Coder — reads from environment / LLM router."""

from __future__ import annotations

import os


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


# LLM backend selection — prefers ICDEV router, falls back to direct Ollama
LLM_BACKEND = _env("AUTOCODER_LLM", "icdev")  # "icdev" | "ollama" | "stub"
OLLAMA_BASE_URL = _env("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = _env("OLLAMA_MODEL", "qwen2.5-coder:7b")
ICDEV_MODEL_FUNCTION = _env("AUTOCODER_ICDEV_FUNCTION", "code_generation")

# Circuit breaker defaults (can be overridden per call)
MAX_STEPS = int(_env("AUTOCODER_MAX_STEPS", "20"))
MAX_TOKENS = int(_env("AUTOCODER_MAX_TOKENS", "32000"))
MAX_DURATION_S = float(_env("AUTOCODER_MAX_DURATION_S", "120"))
