#!/usr/bin/env python3
# CUI // SP-CTI
"""AWS Bedrock fine-tuning provider (D-FT-20).

Uses Bedrock create_model_customization_job API. Long-running poll.
Graceful ImportError when boto3 not installed.

CUI boundary enforcement (D-FT-4): Blocks cloud fine-tuning when
project classification exceeds cloud_max_classification.

Usage:
    provider = BedrockFineTuneProvider(region="us-gov-west-1")
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

logger = get_logger("icdev.finetune.bedrock")

# Bedrock status → ICDEV™ status mapping
_STATUS_MAP = {
    "InProgress": "training",
    "Completed": "completed",
    "Failed": "failed",
    "Stopping": "canceled",
    "Stopped": "canceled",
}

# Bedrock models that support fine-tuning
_SUPPORTED_MODELS = {
    "amazon.titan-text-express-v1": "Amazon Titan Text Express",
    "amazon.titan-text-lite-v1": "Amazon Titan Text Lite",
    "cohere.command-text-v14": "Cohere Command",
    "cohere.command-light-text-v14": "Cohere Command Light",
    "meta.llama3-8b-instruct-v1:0": "Meta Llama 3 8B Instruct",
    "meta.llama3-70b-instruct-v1:0": "Meta Llama 3 70B Instruct",
}


class BedrockFineTuneProvider(FineTuneProvider):
    """AWS Bedrock model customization (D-FT-20)."""

    def __init__(
        self,
        region: str = "",
        role_arn: str = "",
        s3_bucket: str = "",
        config: Optional[Dict[str, Any]] = None,
    ):
        cloud_cfg = (config or {}).get("cloud", {}).get("bedrock", {})
        self._region = region or cloud_cfg.get("region", "") or os.environ.get("AWS_DEFAULT_REGION", "us-gov-west-1")
        self._role_arn = role_arn or cloud_cfg.get("role_arn", "") or os.environ.get("ICDEV_BEDROCK_FT_ROLE_ARN", "")
        self._s3_bucket = (
            s3_bucket or cloud_cfg.get("s3_bucket", "") or os.environ.get("ICDEV_BEDROCK_FT_S3_BUCKET", "")
        )
        self._client = None

    def _get_client(self):
        """Lazy-init Bedrock client."""
        if self._client is not None:
            return self._client
        try:
            import boto3

            self._client = boto3.client(
                "bedrock",
                region_name=self._region,
            )
            return self._client
        except ImportError:
            raise ImportError("boto3 package not installed. Install with: pip install boto3")

    def _get_s3_client(self):
        """Get S3 client for dataset upload."""
        try:
            import boto3

            return boto3.client("s3", region_name=self._region)
        except ImportError:
            raise ImportError("boto3 not installed")

    @property
    def provider_name(self) -> str:
        return "bedrock"

    def check_availability(self) -> Dict[str, Any]:
        """Check if Bedrock fine-tuning is available."""
        if not self._role_arn:
            return {
                "available": False,
                "provider": "bedrock",
                "error": "ICDEV_BEDROCK_FT_ROLE_ARN not set",
            }
        if not self._s3_bucket:
            return {
                "available": False,
                "provider": "bedrock",
                "error": "ICDEV_BEDROCK_FT_S3_BUCKET not set",
            }
        try:
            client = self._get_client()
            # List foundation models that support fine-tuning
            response = client.list_foundation_models(
                byCustomizationType="FINE_TUNING",
            )
            models = [m["modelId"] for m in response.get("modelSummaries", [])][:10]
            return {
                "available": True,
                "provider": "bedrock",
                "region": self._region,
                "fine_tunable_models": models,
            }
        except ImportError:
            return {
                "available": False,
                "provider": "bedrock",
                "error": "boto3 not installed",
            }
        except Exception as e:
            return {
                "available": False,
                "provider": "bedrock",
                "error": str(e),
            }

    def start_training(self, request: FineTuneRequest) -> FineTuneStatus:
        """Start a Bedrock model customization job.

        Uploads dataset to S3, creates customization job.
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

        # 1. Upload dataset to S3
        dataset_path = Path(request.dataset_path)
        if not dataset_path.exists():
            return FineTuneStatus(
                job_id=request.job_id,
                status="failed",
                error=f"Dataset file not found: {request.dataset_path}",
            )

        s3_key = f"icdev-finetune/{request.job_id}/training.jsonl"
        try:
            s3 = self._get_s3_client()
            s3.upload_file(str(dataset_path), self._s3_bucket, s3_key)
            s3_uri = f"s3://{self._s3_bucket}/{s3_key}"
            logger.info("Uploaded dataset to %s", s3_uri)
        except Exception as e:
            return FineTuneStatus(
                job_id=request.job_id,
                status="failed",
                error=f"S3 upload failed: {e}",
            )

        # 2. Create customization job
        try:
            base_model = request.base_model
            if base_model not in _SUPPORTED_MODELS:
                # Default to Titan Text Express
                base_model = "amazon.titan-text-express-v1"

            output_s3 = f"s3://{self._s3_bucket}/icdev-finetune/{request.job_id}/output/"
            custom_model_name = f"icdev-ft-{request.job_id[:12]}"

            hyperparams = {
                "epochCount": str(request.epochs),
                "batchSize": str(request.batch_size),
                "learningRate": str(request.learning_rate),
            }

            response = client.create_model_customization_job(
                jobName=f"icdev-{request.job_id}",
                customModelName=custom_model_name,
                roleArn=self._role_arn,
                baseModelIdentifier=base_model,
                customizationType="FINE_TUNING",
                trainingDataConfig={"s3Uri": s3_uri},
                outputDataConfig={"s3Uri": output_s3},
                hyperParameters=hyperparams,
            )

            cloud_job_arn = response.get("jobArn", "")
            logger.info("Created Bedrock customization job: %s", cloud_job_arn)
            return FineTuneStatus(
                job_id=request.job_id,
                status="training",
                cloud_job_id=cloud_job_arn,
            )
        except Exception as e:
            return FineTuneStatus(
                job_id=request.job_id,
                status="failed",
                error=f"Job creation failed: {e}",
            )

    def get_status(self, job_id: str) -> FineTuneStatus:
        """Poll Bedrock customization job status."""
        client = self._get_client()
        try:
            response = client.get_model_customization_job(jobIdentifier=job_id)

            bedrock_status = response.get("status", "Unknown")
            status = FineTuneStatus(
                cloud_job_id=job_id,
                status=_STATUS_MAP.get(bedrock_status, bedrock_status.lower()),
            )

            # Extract metrics
            metrics = response.get("trainingMetrics", {})
            if "trainingLoss" in metrics:
                status.current_loss = metrics["trainingLoss"]

            # Output model
            if bedrock_status == "Completed":
                status.ollama_model_name = response.get("outputModelName", "")

            # Error
            if bedrock_status == "Failed":
                status.error = response.get("failureMessage", "Unknown failure")

            return status
        except Exception as e:
            return FineTuneStatus(
                cloud_job_id=job_id,
                status="failed",
                error=f"Status check failed: {e}",
            )

    def cancel_training(self, job_id: str) -> bool:
        """Cancel a Bedrock customization job."""
        try:
            client = self._get_client()
            client.stop_model_customization_job(jobIdentifier=job_id)
            return True
        except Exception as e:
            logger.warning("Cancel failed for %s: %s", job_id, e)
            return False

    def list_models(self) -> List[Dict[str, Any]]:
        """List Bedrock models available for fine-tuning."""
        try:
            client = self._get_client()
            response = client.list_foundation_models(
                byCustomizationType="FINE_TUNING",
            )
            return [
                {
                    "model_id": m["modelId"],
                    "name": m.get("modelName", m["modelId"]),
                    "provider": "bedrock",
                }
                for m in response.get("modelSummaries", [])
            ]
        except Exception:
            return []

    def validate_dataset(self, dataset_path: str) -> Dict[str, Any]:
        """Validate dataset format for Bedrock fine-tuning.

        Bedrock requires JSONL with {"prompt": ..., "completion": ...} format
        for Titan models, or chat format for newer models.
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
                        # Accept either prompt/completion or messages format
                        has_prompt = "prompt" in obj and "completion" in obj
                        has_messages = "messages" in obj
                        if not has_prompt and not has_messages:
                            errors.append(f"Line {i}: needs 'prompt'+'completion' or 'messages'")
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
