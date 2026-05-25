#!/usr/bin/env python3
# CUI // SP-CTI
"""Azure OpenAI fine-tuning provider (D-FT-20).

Uses Azure OpenAI /fine_tuning/jobs API. Long-running poll pattern.
Graceful ImportError when openai SDK not installed.

Usage:
    provider = AzureOpenAIFineTuneProvider()
    status = provider.start_training(request)
    while not status.is_terminal:
        status = provider.get_status(status.cloud_job_id)
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.finetune.provider import FineTuneProvider, FineTuneRequest, FineTuneStatus

logger = get_logger("icdev.finetune.azure")

# Azure OpenAI status → ICDEV™ status mapping
_STATUS_MAP = {
    "validating_files": "preparing",
    "queued": "pending",
    "running": "training",
    "succeeded": "completed",
    "failed": "failed",
    "cancelled": "canceled",
}

# Azure OpenAI models supporting fine-tuning
_SUPPORTED_MODELS = {
    "gpt-4o-mini-2024-07-18": "GPT-4o Mini",
    "gpt-4o-2024-08-06": "GPT-4o",
    "gpt-35-turbo-0613": "GPT-3.5 Turbo",
}


class AzureOpenAIFineTuneProvider(FineTuneProvider):
    """Azure OpenAI fine-tuning via /fine_tuning/jobs (D-FT-20)."""

    def __init__(
        self,
        api_key: str = "",
        endpoint: str = "",
        api_version: str = "",
        config: Optional[Dict[str, Any]] = None,
    ):
        cloud_cfg = (config or {}).get("cloud", {}).get("azure_openai", {})
        self._api_key = api_key or cloud_cfg.get("api_key", "") or os.environ.get("AZURE_OPENAI_API_KEY", "")
        self._endpoint = endpoint or cloud_cfg.get("endpoint", "") or os.environ.get("AZURE_OPENAI_ENDPOINT", "")
        self._api_version = (
            api_version
            or cloud_cfg.get("api_version", "")
            or os.environ.get("AZURE_OPENAI_API_VERSION", "2024-08-01-preview")
        )
        self._client = None

    def _get_client(self):
        """Lazy-init Azure OpenAI client."""
        if self._client is not None:
            return self._client
        try:
            import openai

            self._client = openai.AzureOpenAI(
                api_key=self._api_key,
                azure_endpoint=self._endpoint,
                api_version=self._api_version,
            )
            return self._client
        except ImportError:
            raise ImportError("openai package not installed. Install with: pip install openai")

    @property
    def provider_name(self) -> str:
        return "azure_openai"

    def check_availability(self) -> Dict[str, Any]:
        """Check if Azure OpenAI API is reachable."""
        if not self._api_key:
            return {
                "available": False,
                "provider": "azure_openai",
                "error": "AZURE_OPENAI_API_KEY not set",
            }
        if not self._endpoint:
            return {
                "available": False,
                "provider": "azure_openai",
                "error": "AZURE_OPENAI_ENDPOINT not set",
            }
        try:
            client = self._get_client()
            models = client.models.list()
            ft_models = [m.id for m in models.data if hasattr(m, "id")][:5]
            return {
                "available": True,
                "provider": "azure_openai",
                "endpoint": self._endpoint,
                "models": ft_models,
            }
        except ImportError:
            return {
                "available": False,
                "provider": "azure_openai",
                "error": "openai package not installed",
            }
        except Exception as e:
            return {
                "available": False,
                "provider": "azure_openai",
                "error": str(e),
            }

    def start_training(self, request: FineTuneRequest) -> FineTuneStatus:
        """Start an Azure OpenAI fine-tuning job.

        Uploads dataset file, creates fine-tuning job.
        D-FT-4: Classification boundary check before upload.
        """
        # D-FT-4: CUI boundary check
        if request.classification in ("SECRET", "TOP_SECRET"):
            return FineTuneStatus(
                job_id=request.job_id,
                status="failed",
                error=(
                    f"Classification {request.classification} cannot be sent to "
                    f"cloud fine-tuning. Use local provider (unsloth_local) instead."
                ),
            )

        client = self._get_client()

        # 1. Upload training file
        dataset_path = Path(request.dataset_path)
        if not dataset_path.exists():
            return FineTuneStatus(
                job_id=request.job_id,
                status="failed",
                error=f"Dataset file not found: {request.dataset_path}",
            )

        try:
            with open(dataset_path, "rb") as f:
                file_response = client.files.create(
                    file=f,
                    purpose="fine-tune",
                )
            training_file_id = file_response.id
            logger.info("Uploaded training file: %s", training_file_id)
        except Exception as e:
            return FineTuneStatus(
                job_id=request.job_id,
                status="failed",
                error=f"File upload failed: {e}",
            )

        # 2. Create fine-tuning job
        try:
            base_model = request.base_model
            if base_model not in _SUPPORTED_MODELS:
                base_model = "gpt-4o-mini-2024-07-18"

            hyperparams = {}
            if request.epochs:
                hyperparams["n_epochs"] = request.epochs
            if request.learning_rate:
                hyperparams["learning_rate_multiplier"] = request.learning_rate
            if request.batch_size:
                hyperparams["batch_size"] = request.batch_size

            job = client.fine_tuning.jobs.create(
                training_file=training_file_id,
                model=base_model,
                hyperparameters=hyperparams if hyperparams else None,
            )

            logger.info("Created Azure fine-tuning job: %s", job.id)
            return FineTuneStatus(
                job_id=request.job_id,
                status=_STATUS_MAP.get(job.status, "pending"),
                cloud_job_id=job.id,
            )
        except Exception as e:
            return FineTuneStatus(
                job_id=request.job_id,
                status="failed",
                error=f"Job creation failed: {e}",
            )

    def get_status(self, job_id: str) -> FineTuneStatus:
        """Poll Azure OpenAI fine-tuning job status."""
        client = self._get_client()
        try:
            job = client.fine_tuning.jobs.retrieve(job_id)

            status = FineTuneStatus(
                cloud_job_id=job.id,
                status=_STATUS_MAP.get(job.status, job.status),
            )

            # Extract training metrics from events
            if job.status == "running":
                try:
                    events = client.fine_tuning.jobs.list_events(
                        fine_tuning_job_id=job.id,
                        limit=10,
                    )
                    for event in events.data:
                        if hasattr(event, "data") and event.data:
                            data = event.data if isinstance(event.data, dict) else {}
                            if "step" in data:
                                status.current_step = data["step"]
                            if "train_loss" in data:
                                status.current_loss = data["train_loss"]
                                status.loss_history.append(data["train_loss"])
                            if "total_steps" in data:
                                status.total_steps = data["total_steps"]
                except Exception:
                    pass

            if status.total_steps > 0:
                status.progress_pct = (status.current_step / status.total_steps) * 100

            # Completed model
            if job.status == "succeeded" and job.fine_tuned_model:
                status.ollama_model_name = job.fine_tuned_model

            # Error info
            if job.status == "failed" and hasattr(job, "error") and job.error:
                status.error = str(job.error)

            return status
        except Exception as e:
            return FineTuneStatus(
                cloud_job_id=job_id,
                status="failed",
                error=f"Status check failed: {e}",
            )

    def cancel_training(self, job_id: str) -> bool:
        """Cancel an Azure OpenAI fine-tuning job."""
        try:
            client = self._get_client()
            client.fine_tuning.jobs.cancel(job_id)
            return True
        except Exception as e:
            logger.warning("Cancel failed for %s: %s", job_id, e)
            return False

    def list_models(self) -> List[Dict[str, Any]]:
        """List Azure OpenAI models available for fine-tuning."""
        try:
            client = self._get_client()
            models = client.models.list()
            return [
                {"model_id": m.id, "provider": "azure_openai"}
                for m in models.data
                if hasattr(m, "id") and any(x in m.id for x in ("gpt-4o-mini", "gpt-35-turbo", "gpt-4o"))
            ]
        except Exception:
            return []

    def validate_dataset(self, dataset_path: str) -> Dict[str, Any]:
        """Validate dataset format for Azure OpenAI fine-tuning.

        Azure OpenAI requires JSONL with {"messages": [...]} format
        (same as OpenAI).
        """
        errors = []
        line_count = 0
        path = Path(dataset_path)

        if not path.exists():
            return {"valid": False, "errors": [f"File not found: {dataset_path}"]}

        try:
            with open(path) as f:
                for i, line in enumerate(f, 1):
                    line_count += 1
                    try:
                        obj = json.loads(line.strip())
                        if "messages" not in obj:
                            errors.append(f"Line {i}: missing 'messages' key")
                        elif not isinstance(obj["messages"], list):
                            errors.append(f"Line {i}: 'messages' must be a list")
                        elif len(obj["messages"]) < 2:
                            errors.append(f"Line {i}: need at least 2 messages")
                    except json.JSONDecodeError:
                        errors.append(f"Line {i}: invalid JSON")

            if line_count < 10:
                errors.append(f"Too few examples: {line_count} (minimum 10)")

        except Exception as e:
            errors.append(f"File read error: {e}")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "line_count": line_count,
        }
