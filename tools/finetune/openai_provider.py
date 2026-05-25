#!/usr/bin/env python3
# CUI // SP-CTI
"""OpenAI fine-tuning provider (D-FT-20).

Uses OpenAI /v1/fine_tuning/jobs API. Long-running poll pattern.
Graceful ImportError when openai SDK not installed.

Usage:
    provider = OpenAIFineTuneProvider()
    status = provider.start_training(request)
    # Poll until terminal:
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

logger = get_logger("icdev.finetune.openai")

# OpenAI status → ICDEV™ status mapping
_STATUS_MAP = {
    "validating_files": "preparing",
    "queued": "pending",
    "running": "training",
    "succeeded": "completed",
    "failed": "failed",
    "cancelled": "canceled",
}


class OpenAIFineTuneProvider(FineTuneProvider):
    """OpenAI fine-tuning via /v1/fine_tuning/jobs (D-FT-20)."""

    def __init__(
        self,
        api_key: str = "",
        organization: str = "",
        base_url: str = "",
        config: Optional[Dict[str, Any]] = None,
    ):
        cloud_cfg = (config or {}).get("cloud", {}).get("openai", {})
        self._api_key = api_key or cloud_cfg.get("api_key", "") or os.environ.get("OPENAI_API_KEY", "")
        self._organization = organization or cloud_cfg.get("organization", "") or os.environ.get("OPENAI_ORG_ID", "")
        self._base_url = base_url or cloud_cfg.get("base_url", "")
        self._client = None

    def _get_client(self):
        """Lazy-init OpenAI client."""
        if self._client is not None:
            return self._client
        try:
            import openai

            kwargs: Dict[str, Any] = {"api_key": self._api_key}
            if self._organization:
                kwargs["organization"] = self._organization
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = openai.OpenAI(**kwargs)
            return self._client
        except ImportError:
            raise ImportError("openai package not installed. Install with: pip install openai")

    @property
    def provider_name(self) -> str:
        return "openai"

    def check_availability(self) -> Dict[str, Any]:
        """Check if OpenAI API is reachable and API key is set."""
        if not self._api_key:
            return {
                "available": False,
                "provider": "openai",
                "error": "OPENAI_API_KEY not set",
            }
        try:
            client = self._get_client()
            # Quick check — list models (lightweight)
            models = client.models.list()
            ft_models = [m.id for m in models.data if "gpt" in m.id and "ft" not in m.id][:5]
            return {
                "available": True,
                "provider": "openai",
                "base_models": ft_models,
            }
        except ImportError:
            return {
                "available": False,
                "provider": "openai",
                "error": "openai package not installed",
            }
        except Exception as e:
            return {
                "available": False,
                "provider": "openai",
                "error": str(e),
            }

    def start_training(self, request: FineTuneRequest) -> FineTuneStatus:
        """Start an OpenAI fine-tuning job.

        Uploads dataset file, creates fine-tuning job.
        """
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
            # Map base model name
            base_model = request.base_model
            if base_model.startswith("gpt-"):
                model_name = base_model
            else:
                model_name = "gpt-4o-mini-2024-07-18"  # Default

            hyperparams = {}
            if request.epochs:
                hyperparams["n_epochs"] = request.epochs
            if request.learning_rate:
                hyperparams["learning_rate_multiplier"] = request.learning_rate
            if request.batch_size:
                hyperparams["batch_size"] = request.batch_size

            job = client.fine_tuning.jobs.create(
                training_file=training_file_id,
                model=model_name,
                hyperparameters=hyperparams if hyperparams else None,
            )

            logger.info("Created fine-tuning job: %s", job.id)
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
        """Poll OpenAI fine-tuning job status."""
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
        """Cancel an OpenAI fine-tuning job."""
        try:
            client = self._get_client()
            client.fine_tuning.jobs.cancel(job_id)
            return True
        except Exception as e:
            logger.warning("Cancel failed for %s: %s", job_id, e)
            return False

    def list_models(self) -> List[Dict[str, Any]]:
        """List OpenAI models available for fine-tuning."""
        try:
            client = self._get_client()
            models = client.models.list()
            return [
                {"model_id": m.id, "provider": "openai"}
                for m in models.data
                if any(x in m.id for x in ("gpt-4o-mini", "gpt-3.5-turbo", "gpt-4o")) and "ft:" not in m.id
            ]
        except Exception:
            return []

    def validate_dataset(self, dataset_path: str) -> Dict[str, Any]:
        """Validate dataset format for OpenAI fine-tuning.

        OpenAI requires JSONL with {"messages": [...]} format.
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
