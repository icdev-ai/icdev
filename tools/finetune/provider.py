#!/usr/bin/env python3
# CUI // SP-CTI
"""FineTuneProvider ABC and supporting dataclasses (D-FT-1).

All fine-tuning providers implement this interface. Four implementations:
  - UnslothLocalProvider  — local QLoRA via Unsloth subprocess
  - OpenAIFineTuneProvider — OpenAI /v1/fine_tuning/jobs
  - BedrockFineTuneProvider — AWS Bedrock create_model_customization_job
  - AzureOpenAIFineTuneProvider — Azure OpenAI /fine_tuning/jobs
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List


# ── Request / Status dataclasses ──────────────────────────────────────


@dataclass
class FineTuneRequest:
    """Parameters for a fine-tuning job."""

    dataset_path: str = ""
    base_model: str = "qwen3:latest"
    lora_rank: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.0
    target_modules: List[str] = field(
        default_factory=lambda: [
            "q_proj",
            "k_proj",
            "v_proj",
            "o_proj",
            "gate_proj",
            "up_proj",
            "down_proj",
        ]
    )
    learning_rate: float = 2e-4
    epochs: int = 3
    batch_size: int = 2
    max_seq_length: int = 2048
    warmup_steps: int = 10
    scheduler: str = "cosine"
    output_dir: str = ""
    gpu_count: int = 1
    distributed: bool = False
    quantization: str = "4bit"
    job_id: str = ""
    classification: str = "CUI"
    tenant_id: str = ""
    project_id: str = ""
    extra_params: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "FineTuneRequest":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in known}
        return cls(**filtered)


@dataclass
class FineTuneStatus:
    """Status of a fine-tuning job."""

    job_id: str = ""
    status: str = "pending"
    progress_pct: float = 0.0
    current_step: int = 0
    total_steps: int = 0
    current_loss: float = 0.0
    loss_history: List[float] = field(default_factory=list)
    elapsed_seconds: int = 0
    error: str = ""
    adapter_path: str = ""
    gguf_path: str = ""
    ollama_model_name: str = ""
    cloud_job_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def is_terminal(self) -> bool:
        return self.status in ("completed", "failed", "canceled")


# ── Provider ABC ──────────────────────────────────────────────────────


class FineTuneProvider(ABC):
    """Abstract base class for fine-tuning providers (D-FT-1, D66 pattern)."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return provider identifier (e.g. 'unsloth_local', 'openai')."""
        ...

    @abstractmethod
    def start_training(self, request: FineTuneRequest) -> FineTuneStatus:
        """Start a fine-tuning job. Returns initial status."""
        ...

    @abstractmethod
    def get_status(self, job_id: str) -> FineTuneStatus:
        """Poll current status of a training job."""
        ...

    @abstractmethod
    def cancel_training(self, job_id: str) -> bool:
        """Cancel a running training job. Returns True on success."""
        ...

    @abstractmethod
    def check_availability(self) -> Dict[str, Any]:
        """Check if this provider is available (SDK installed, GPU present, etc.)."""
        ...

    def export_gguf(
        self,
        adapter_path: str,
        output_path: str,
        quantization: str = "q4_k_m",
    ) -> str:
        """Export adapter to GGUF format. Not all providers support this."""
        raise NotImplementedError(f"GGUF export not supported by {self.provider_name}")

    def list_models(self) -> List[Dict[str, Any]]:
        """List available base models for fine-tuning."""
        return []

    def validate_dataset(self, dataset_path: str) -> Dict[str, Any]:
        """Validate dataset format before training."""
        return {"valid": True, "errors": []}
