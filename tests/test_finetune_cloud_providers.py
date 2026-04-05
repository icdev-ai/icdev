#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for cloud fine-tuning providers (D-FT-20).

Tests OpenAI, Bedrock, and Azure OpenAI providers with mocked SDKs.
No actual cloud calls — all providers mock their respective SDK clients.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from tools.finetune.provider import FineTuneRequest


# ── OpenAI Provider Tests ─────────────────────────────────────────────


class TestOpenAIProviderName(unittest.TestCase):
    """Test OpenAIFineTuneProvider.provider_name."""

    def test_provider_name(self):
        from tools.finetune.openai_provider import OpenAIFineTuneProvider

        p = OpenAIFineTuneProvider(api_key="test-key")
        self.assertEqual(p.provider_name, "openai")


class TestOpenAICheckAvailability(unittest.TestCase):
    """Test OpenAIFineTuneProvider.check_availability."""

    @patch.dict(os.environ, {}, clear=True)
    def test_no_api_key(self):
        from tools.finetune.openai_provider import OpenAIFineTuneProvider

        p = OpenAIFineTuneProvider(api_key="")
        result = p.check_availability()
        self.assertFalse(result["available"])
        self.assertIn("OPENAI_API_KEY", result["error"])

    @patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"})
    def test_env_api_key_used(self):
        from tools.finetune.openai_provider import OpenAIFineTuneProvider

        p = OpenAIFineTuneProvider()
        self.assertEqual(p._api_key, "sk-test")

    def test_sdk_not_installed(self):
        from tools.finetune.openai_provider import OpenAIFineTuneProvider

        p = OpenAIFineTuneProvider(api_key="sk-test")
        with patch.object(p, "_get_client", side_effect=ImportError("no openai")):
            result = p.check_availability()
            self.assertFalse(result["available"])

    def test_api_error(self):
        from tools.finetune.openai_provider import OpenAIFineTuneProvider

        p = OpenAIFineTuneProvider(api_key="sk-test")
        mock_client = MagicMock()
        mock_client.models.list.side_effect = Exception("connection refused")
        p._client = mock_client
        result = p.check_availability()
        self.assertFalse(result["available"])
        self.assertIn("connection refused", result["error"])

    def test_successful_availability(self):
        from tools.finetune.openai_provider import OpenAIFineTuneProvider

        p = OpenAIFineTuneProvider(api_key="sk-test")
        mock_client = MagicMock()
        mock_model = MagicMock()
        mock_model.id = "gpt-4o-mini-2024-07-18"
        mock_client.models.list.return_value = MagicMock(data=[mock_model])
        p._client = mock_client
        result = p.check_availability()
        self.assertTrue(result["available"])


class TestOpenAIStartTraining(unittest.TestCase):
    """Test OpenAIFineTuneProvider.start_training."""

    def setUp(self):
        from tools.finetune.openai_provider import OpenAIFineTuneProvider

        self.provider = OpenAIFineTuneProvider(api_key="sk-test")
        self.mock_client = MagicMock()
        self.provider._client = self.mock_client

        # Create a temp dataset file
        self.tmpdir = tempfile.mkdtemp()
        self.dataset_path = os.path.join(self.tmpdir, "train.jsonl")
        with open(self.dataset_path, "w") as f:
            for i in range(15):
                f.write(
                    json.dumps(
                        {
                            "messages": [
                                {"role": "user", "content": f"q{i}"},
                                {"role": "assistant", "content": f"a{i}"},
                            ]
                        }
                    )
                    + "\n"
                )

    def test_dataset_not_found(self):
        req = FineTuneRequest(
            job_id="job-1",
            dataset_path="/nonexistent/data.jsonl",
        )
        status = self.provider.start_training(req)
        self.assertEqual(status.status, "failed")
        self.assertIn("not found", status.error)

    def test_file_upload_failure(self):
        self.mock_client.files.create.side_effect = Exception("upload error")
        req = FineTuneRequest(
            job_id="job-1",
            dataset_path=self.dataset_path,
        )
        status = self.provider.start_training(req)
        self.assertEqual(status.status, "failed")
        self.assertIn("upload failed", status.error)

    def test_job_creation_failure(self):
        file_resp = MagicMock()
        file_resp.id = "file-abc"
        self.mock_client.files.create.return_value = file_resp
        self.mock_client.fine_tuning.jobs.create.side_effect = Exception("quota exceeded")
        req = FineTuneRequest(
            job_id="job-1",
            dataset_path=self.dataset_path,
        )
        status = self.provider.start_training(req)
        self.assertEqual(status.status, "failed")
        self.assertIn("Job creation failed", status.error)

    def test_successful_start(self):
        file_resp = MagicMock()
        file_resp.id = "file-abc"
        self.mock_client.files.create.return_value = file_resp

        job_resp = MagicMock()
        job_resp.id = "ftjob-123"
        job_resp.status = "queued"
        self.mock_client.fine_tuning.jobs.create.return_value = job_resp

        req = FineTuneRequest(
            job_id="job-1",
            dataset_path=self.dataset_path,
            base_model="gpt-4o-mini-2024-07-18",
            epochs=3,
            learning_rate=0.001,
            batch_size=4,
        )
        status = self.provider.start_training(req)
        self.assertEqual(status.status, "pending")
        self.assertEqual(status.cloud_job_id, "ftjob-123")

    def test_default_model_used_for_non_gpt(self):
        file_resp = MagicMock()
        file_resp.id = "file-abc"
        self.mock_client.files.create.return_value = file_resp

        job_resp = MagicMock()
        job_resp.id = "ftjob-123"
        job_resp.status = "queued"
        self.mock_client.fine_tuning.jobs.create.return_value = job_resp

        req = FineTuneRequest(
            job_id="job-1",
            dataset_path=self.dataset_path,
            base_model="qwen3:latest",  # Not a GPT model
        )
        self.provider.start_training(req)
        call_kwargs = self.mock_client.fine_tuning.jobs.create.call_args
        self.assertEqual(call_kwargs.kwargs["model"], "gpt-4o-mini-2024-07-18")


class TestOpenAIGetStatus(unittest.TestCase):
    """Test OpenAIFineTuneProvider.get_status."""

    def setUp(self):
        from tools.finetune.openai_provider import OpenAIFineTuneProvider

        self.provider = OpenAIFineTuneProvider(api_key="sk-test")
        self.mock_client = MagicMock()
        self.provider._client = self.mock_client

    def test_running_with_metrics(self):
        job = MagicMock()
        job.id = "ftjob-123"
        job.status = "running"
        job.fine_tuned_model = None
        self.mock_client.fine_tuning.jobs.retrieve.return_value = job

        event = MagicMock()
        event.data = {"step": 50, "total_steps": 100, "train_loss": 0.45}
        self.mock_client.fine_tuning.jobs.list_events.return_value = MagicMock(data=[event])

        status = self.provider.get_status("ftjob-123")
        self.assertEqual(status.status, "training")
        self.assertEqual(status.current_step, 50)
        self.assertEqual(status.total_steps, 100)
        self.assertAlmostEqual(status.progress_pct, 50.0)
        self.assertAlmostEqual(status.current_loss, 0.45)

    def test_succeeded(self):
        job = MagicMock()
        job.id = "ftjob-123"
        job.status = "succeeded"
        job.fine_tuned_model = "ft:gpt-4o-mini:org:model:abc123"
        self.mock_client.fine_tuning.jobs.retrieve.return_value = job

        status = self.provider.get_status("ftjob-123")
        self.assertEqual(status.status, "completed")
        self.assertEqual(status.ollama_model_name, "ft:gpt-4o-mini:org:model:abc123")

    def test_failed_with_error(self):
        job = MagicMock()
        job.id = "ftjob-123"
        job.status = "failed"
        job.error = "training diverged"
        job.fine_tuned_model = None
        self.mock_client.fine_tuning.jobs.retrieve.return_value = job

        status = self.provider.get_status("ftjob-123")
        self.assertEqual(status.status, "failed")
        self.assertIn("training diverged", status.error)

    def test_retrieve_error(self):
        self.mock_client.fine_tuning.jobs.retrieve.side_effect = Exception("not found")
        status = self.provider.get_status("ftjob-missing")
        self.assertEqual(status.status, "failed")
        self.assertIn("Status check failed", status.error)


class TestOpenAICancelAndList(unittest.TestCase):
    """Test OpenAIFineTuneProvider cancel and list operations."""

    def setUp(self):
        from tools.finetune.openai_provider import OpenAIFineTuneProvider

        self.provider = OpenAIFineTuneProvider(api_key="sk-test")
        self.mock_client = MagicMock()
        self.provider._client = self.mock_client

    def test_cancel_success(self):
        self.assertTrue(self.provider.cancel_training("ftjob-123"))
        self.mock_client.fine_tuning.jobs.cancel.assert_called_once_with("ftjob-123")

    def test_cancel_failure(self):
        self.mock_client.fine_tuning.jobs.cancel.side_effect = Exception("err")
        self.assertFalse(self.provider.cancel_training("ftjob-123"))

    def test_list_models(self):
        m1 = MagicMock()
        m1.id = "gpt-4o-mini-2024-07-18"
        m2 = MagicMock()
        m2.id = "text-embedding-ada-002"
        self.mock_client.models.list.return_value = MagicMock(data=[m1, m2])
        models = self.provider.list_models()
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0]["model_id"], "gpt-4o-mini-2024-07-18")

    def test_list_models_error(self):
        self.mock_client.models.list.side_effect = Exception("err")
        self.assertEqual(self.provider.list_models(), [])


class TestOpenAIValidateDataset(unittest.TestCase):
    """Test OpenAIFineTuneProvider.validate_dataset."""

    def setUp(self):
        from tools.finetune.openai_provider import OpenAIFineTuneProvider

        self.provider = OpenAIFineTuneProvider(api_key="sk-test")

    def test_file_not_found(self):
        result = self.provider.validate_dataset("/nonexistent/file.jsonl")
        self.assertFalse(result["valid"])

    def test_valid_dataset(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for i in range(15):
                f.write(
                    json.dumps(
                        {
                            "messages": [
                                {"role": "user", "content": f"q{i}"},
                                {"role": "assistant", "content": f"a{i}"},
                            ]
                        }
                    )
                    + "\n"
                )
            path = f.name
        try:
            result = self.provider.validate_dataset(path)
            self.assertTrue(result["valid"])
            self.assertEqual(result["line_count"], 15)
        finally:
            os.unlink(path)

    def test_too_few_examples(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(
                json.dumps(
                    {
                        "messages": [
                            {"role": "user", "content": "q"},
                            {"role": "assistant", "content": "a"},
                        ]
                    }
                )
                + "\n"
            )
            path = f.name
        try:
            result = self.provider.validate_dataset(path)
            self.assertFalse(result["valid"])
            self.assertTrue(any("Too few" in e for e in result["errors"]))
        finally:
            os.unlink(path)

    def test_missing_messages_key(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for i in range(15):
                f.write(json.dumps({"prompt": "q", "completion": "a"}) + "\n")
            path = f.name
        try:
            result = self.provider.validate_dataset(path)
            self.assertFalse(result["valid"])
            self.assertTrue(any("missing 'messages'" in e for e in result["errors"]))
        finally:
            os.unlink(path)


# ── Bedrock Provider Tests ────────────────────────────────────────────


class TestBedrockProviderName(unittest.TestCase):
    def test_provider_name(self):
        from tools.finetune.bedrock_provider import BedrockFineTuneProvider

        p = BedrockFineTuneProvider()
        self.assertEqual(p.provider_name, "bedrock")


class TestBedrockCheckAvailability(unittest.TestCase):
    def test_no_role_arn(self):
        from tools.finetune.bedrock_provider import BedrockFineTuneProvider

        p = BedrockFineTuneProvider(role_arn="", s3_bucket="bucket")
        result = p.check_availability()
        self.assertFalse(result["available"])
        self.assertIn("ROLE_ARN", result["error"])

    def test_no_s3_bucket(self):
        from tools.finetune.bedrock_provider import BedrockFineTuneProvider

        p = BedrockFineTuneProvider(role_arn="arn:aws:iam::role/ft", s3_bucket="")
        result = p.check_availability()
        self.assertFalse(result["available"])
        self.assertIn("S3_BUCKET", result["error"])

    def test_successful_availability(self):
        from tools.finetune.bedrock_provider import BedrockFineTuneProvider

        p = BedrockFineTuneProvider(role_arn="arn:aws:iam::role/ft", s3_bucket="my-bucket")
        mock_client = MagicMock()
        mock_client.list_foundation_models.return_value = {
            "modelSummaries": [
                {"modelId": "amazon.titan-text-express-v1"},
            ]
        }
        p._client = mock_client
        result = p.check_availability()
        self.assertTrue(result["available"])
        self.assertEqual(result["region"], p._region)

    def test_api_error(self):
        from tools.finetune.bedrock_provider import BedrockFineTuneProvider

        p = BedrockFineTuneProvider(role_arn="arn:aws:iam::role/ft", s3_bucket="my-bucket")
        mock_client = MagicMock()
        mock_client.list_foundation_models.side_effect = Exception("access denied")
        p._client = mock_client
        result = p.check_availability()
        self.assertFalse(result["available"])


class TestBedrockStartTraining(unittest.TestCase):
    def setUp(self):
        from tools.finetune.bedrock_provider import BedrockFineTuneProvider

        self.provider = BedrockFineTuneProvider(
            role_arn="arn:aws:iam::role/ft",
            s3_bucket="my-bucket",
            region="us-gov-west-1",
        )
        self.mock_client = MagicMock()
        self.provider._client = self.mock_client

        self.tmpdir = tempfile.mkdtemp()
        self.dataset_path = os.path.join(self.tmpdir, "train.jsonl")
        with open(self.dataset_path, "w") as f:
            for i in range(15):
                f.write(
                    json.dumps(
                        {
                            "prompt": f"Question {i}",
                            "completion": f"Answer {i}",
                        }
                    )
                    + "\n"
                )

    def test_cui_boundary_secret(self):
        """D-FT-4: SECRET classification blocked from cloud fine-tuning."""
        req = FineTuneRequest(
            job_id="job-1",
            dataset_path=self.dataset_path,
            classification="SECRET",
        )
        status = self.provider.start_training(req)
        self.assertEqual(status.status, "failed")
        self.assertIn("SECRET", status.error)
        self.assertIn("local provider", status.error)

    def test_cui_boundary_top_secret(self):
        """D-FT-4: TOP_SECRET classification blocked."""
        req = FineTuneRequest(
            job_id="job-1",
            dataset_path=self.dataset_path,
            classification="TOP_SECRET",
        )
        status = self.provider.start_training(req)
        self.assertEqual(status.status, "failed")

    def test_dataset_not_found(self):
        req = FineTuneRequest(
            job_id="job-1",
            dataset_path="/nonexistent/data.jsonl",
        )
        status = self.provider.start_training(req)
        self.assertEqual(status.status, "failed")
        self.assertIn("not found", status.error)

    def test_s3_upload_failure(self):
        mock_s3 = MagicMock()
        mock_s3.upload_file.side_effect = Exception("access denied")
        with patch.object(self.provider, "_get_s3_client", return_value=mock_s3):
            req = FineTuneRequest(
                job_id="job-1",
                dataset_path=self.dataset_path,
            )
            status = self.provider.start_training(req)
            self.assertEqual(status.status, "failed")
            self.assertIn("S3 upload failed", status.error)

    def test_job_creation_failure(self):
        mock_s3 = MagicMock()
        with patch.object(self.provider, "_get_s3_client", return_value=mock_s3):
            self.mock_client.create_model_customization_job.side_effect = Exception("quota exceeded")
            req = FineTuneRequest(
                job_id="job-1",
                dataset_path=self.dataset_path,
            )
            status = self.provider.start_training(req)
            self.assertEqual(status.status, "failed")
            self.assertIn("Job creation failed", status.error)

    def test_successful_start(self):
        mock_s3 = MagicMock()
        with patch.object(self.provider, "_get_s3_client", return_value=mock_s3):
            self.mock_client.create_model_customization_job.return_value = {
                "jobArn": "arn:aws:bedrock:us-gov-west-1:123:model-customization-job/icdev-job-1"
            }
            req = FineTuneRequest(
                job_id="job-1",
                dataset_path=self.dataset_path,
                base_model="amazon.titan-text-express-v1",
                epochs=3,
                batch_size=2,
                learning_rate=2e-4,
            )
            status = self.provider.start_training(req)
            self.assertEqual(status.status, "training")
            self.assertIn("arn:aws:bedrock", status.cloud_job_id)

    def test_unsupported_model_defaults_to_titan(self):
        mock_s3 = MagicMock()
        with patch.object(self.provider, "_get_s3_client", return_value=mock_s3):
            self.mock_client.create_model_customization_job.return_value = {"jobArn": "arn:aws:bedrock:job/1"}
            req = FineTuneRequest(
                job_id="job-1",
                dataset_path=self.dataset_path,
                base_model="qwen3:latest",
            )
            self.provider.start_training(req)
            call_kwargs = self.mock_client.create_model_customization_job.call_args
            self.assertEqual(
                call_kwargs.kwargs["baseModelIdentifier"],
                "amazon.titan-text-express-v1",
            )


class TestBedrockGetStatus(unittest.TestCase):
    def setUp(self):
        from tools.finetune.bedrock_provider import BedrockFineTuneProvider

        self.provider = BedrockFineTuneProvider(role_arn="arn:aws:iam::role/ft", s3_bucket="my-bucket")
        self.mock_client = MagicMock()
        self.provider._client = self.mock_client

    def test_in_progress(self):
        self.mock_client.get_model_customization_job.return_value = {
            "status": "InProgress",
            "trainingMetrics": {"trainingLoss": 0.32},
        }
        status = self.provider.get_status("arn:aws:bedrock:job/1")
        self.assertEqual(status.status, "training")
        self.assertAlmostEqual(status.current_loss, 0.32)

    def test_completed(self):
        self.mock_client.get_model_customization_job.return_value = {
            "status": "Completed",
            "outputModelName": "icdev-ft-abc123",
        }
        status = self.provider.get_status("arn:aws:bedrock:job/1")
        self.assertEqual(status.status, "completed")
        self.assertEqual(status.ollama_model_name, "icdev-ft-abc123")

    def test_failed(self):
        self.mock_client.get_model_customization_job.return_value = {
            "status": "Failed",
            "failureMessage": "invalid training data",
        }
        status = self.provider.get_status("arn:aws:bedrock:job/1")
        self.assertEqual(status.status, "failed")
        self.assertIn("invalid training data", status.error)

    def test_stopped(self):
        self.mock_client.get_model_customization_job.return_value = {
            "status": "Stopped",
        }
        status = self.provider.get_status("arn:aws:bedrock:job/1")
        self.assertEqual(status.status, "canceled")

    def test_retrieve_error(self):
        self.mock_client.get_model_customization_job.side_effect = Exception("not found")
        status = self.provider.get_status("arn:aws:bedrock:job/missing")
        self.assertEqual(status.status, "failed")
        self.assertIn("Status check failed", status.error)


class TestBedrockCancelAndList(unittest.TestCase):
    def setUp(self):
        from tools.finetune.bedrock_provider import BedrockFineTuneProvider

        self.provider = BedrockFineTuneProvider(role_arn="arn:aws:iam::role/ft", s3_bucket="my-bucket")
        self.mock_client = MagicMock()
        self.provider._client = self.mock_client

    def test_cancel_success(self):
        self.assertTrue(self.provider.cancel_training("arn:aws:bedrock:job/1"))

    def test_cancel_failure(self):
        self.mock_client.stop_model_customization_job.side_effect = Exception("err")
        self.assertFalse(self.provider.cancel_training("arn:aws:bedrock:job/1"))

    def test_list_models(self):
        self.mock_client.list_foundation_models.return_value = {
            "modelSummaries": [
                {"modelId": "amazon.titan-text-express-v1", "modelName": "Titan Express"},
                {"modelId": "meta.llama3-8b-instruct-v1:0", "modelName": "Llama 3 8B"},
            ]
        }
        models = self.provider.list_models()
        self.assertEqual(len(models), 2)
        self.assertEqual(models[0]["provider"], "bedrock")

    def test_list_models_error(self):
        self.mock_client.list_foundation_models.side_effect = Exception("err")
        self.assertEqual(self.provider.list_models(), [])


class TestBedrockValidateDataset(unittest.TestCase):
    def setUp(self):
        from tools.finetune.bedrock_provider import BedrockFineTuneProvider

        self.provider = BedrockFineTuneProvider()

    def test_file_not_found(self):
        result = self.provider.validate_dataset("/nonexistent/file.jsonl")
        self.assertFalse(result["valid"])

    def test_valid_prompt_completion(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for i in range(15):
                f.write(
                    json.dumps(
                        {
                            "prompt": f"Question {i}",
                            "completion": f"Answer {i}",
                        }
                    )
                    + "\n"
                )
            path = f.name
        try:
            result = self.provider.validate_dataset(path)
            self.assertTrue(result["valid"])
        finally:
            os.unlink(path)

    def test_valid_messages_format(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for i in range(15):
                f.write(
                    json.dumps(
                        {
                            "messages": [
                                {"role": "user", "content": f"q{i}"},
                                {"role": "assistant", "content": f"a{i}"},
                            ]
                        }
                    )
                    + "\n"
                )
            path = f.name
        try:
            result = self.provider.validate_dataset(path)
            self.assertTrue(result["valid"])
        finally:
            os.unlink(path)

    def test_invalid_format(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for i in range(15):
                f.write(json.dumps({"text": f"line {i}"}) + "\n")
            path = f.name
        try:
            result = self.provider.validate_dataset(path)
            self.assertFalse(result["valid"])
        finally:
            os.unlink(path)


# ── Azure OpenAI Provider Tests ───────────────────────────────────────


class TestAzureProviderName(unittest.TestCase):
    def test_provider_name(self):
        from tools.finetune.azure_provider import AzureOpenAIFineTuneProvider

        p = AzureOpenAIFineTuneProvider(api_key="test", endpoint="https://test.openai.azure.com")
        self.assertEqual(p.provider_name, "azure_openai")


class TestAzureCheckAvailability(unittest.TestCase):
    def test_no_api_key(self):
        from tools.finetune.azure_provider import AzureOpenAIFineTuneProvider

        p = AzureOpenAIFineTuneProvider(api_key="", endpoint="https://test.openai.azure.com")
        result = p.check_availability()
        self.assertFalse(result["available"])
        self.assertIn("API_KEY", result["error"])

    def test_no_endpoint(self):
        from tools.finetune.azure_provider import AzureOpenAIFineTuneProvider

        p = AzureOpenAIFineTuneProvider(api_key="test-key", endpoint="")
        result = p.check_availability()
        self.assertFalse(result["available"])
        self.assertIn("ENDPOINT", result["error"])

    def test_successful_availability(self):
        from tools.finetune.azure_provider import AzureOpenAIFineTuneProvider

        p = AzureOpenAIFineTuneProvider(api_key="test-key", endpoint="https://test.openai.azure.com")
        mock_client = MagicMock()
        mock_model = MagicMock()
        mock_model.id = "gpt-4o-mini-2024-07-18"
        mock_client.models.list.return_value = MagicMock(data=[mock_model])
        p._client = mock_client
        result = p.check_availability()
        self.assertTrue(result["available"])
        self.assertEqual(result["endpoint"], "https://test.openai.azure.com")

    def test_api_error(self):
        from tools.finetune.azure_provider import AzureOpenAIFineTuneProvider

        p = AzureOpenAIFineTuneProvider(api_key="test-key", endpoint="https://test.openai.azure.com")
        mock_client = MagicMock()
        mock_client.models.list.side_effect = Exception("unauthorized")
        p._client = mock_client
        result = p.check_availability()
        self.assertFalse(result["available"])


class TestAzureStartTraining(unittest.TestCase):
    def setUp(self):
        from tools.finetune.azure_provider import AzureOpenAIFineTuneProvider

        self.provider = AzureOpenAIFineTuneProvider(api_key="test-key", endpoint="https://test.openai.azure.com")
        self.mock_client = MagicMock()
        self.provider._client = self.mock_client

        self.tmpdir = tempfile.mkdtemp()
        self.dataset_path = os.path.join(self.tmpdir, "train.jsonl")
        with open(self.dataset_path, "w") as f:
            for i in range(15):
                f.write(
                    json.dumps(
                        {
                            "messages": [
                                {"role": "user", "content": f"q{i}"},
                                {"role": "assistant", "content": f"a{i}"},
                            ]
                        }
                    )
                    + "\n"
                )

    def test_cui_boundary_secret(self):
        """D-FT-4: SECRET classification blocked from cloud."""
        req = FineTuneRequest(
            job_id="job-1",
            dataset_path=self.dataset_path,
            classification="SECRET",
        )
        status = self.provider.start_training(req)
        self.assertEqual(status.status, "failed")
        self.assertIn("SECRET", status.error)

    def test_cui_boundary_top_secret(self):
        req = FineTuneRequest(
            job_id="job-1",
            dataset_path=self.dataset_path,
            classification="TOP_SECRET",
        )
        status = self.provider.start_training(req)
        self.assertEqual(status.status, "failed")

    def test_dataset_not_found(self):
        req = FineTuneRequest(
            job_id="job-1",
            dataset_path="/nonexistent/data.jsonl",
        )
        status = self.provider.start_training(req)
        self.assertEqual(status.status, "failed")
        self.assertIn("not found", status.error)

    def test_successful_start(self):
        file_resp = MagicMock()
        file_resp.id = "file-azure-abc"
        self.mock_client.files.create.return_value = file_resp

        job_resp = MagicMock()
        job_resp.id = "ftjob-azure-123"
        job_resp.status = "queued"
        self.mock_client.fine_tuning.jobs.create.return_value = job_resp

        req = FineTuneRequest(
            job_id="job-1",
            dataset_path=self.dataset_path,
            base_model="gpt-4o-mini-2024-07-18",
        )
        status = self.provider.start_training(req)
        self.assertEqual(status.status, "pending")
        self.assertEqual(status.cloud_job_id, "ftjob-azure-123")

    def test_file_upload_failure(self):
        self.mock_client.files.create.side_effect = Exception("upload error")
        req = FineTuneRequest(
            job_id="job-1",
            dataset_path=self.dataset_path,
        )
        status = self.provider.start_training(req)
        self.assertEqual(status.status, "failed")
        self.assertIn("upload failed", status.error)

    def test_unsupported_model_defaults(self):
        file_resp = MagicMock()
        file_resp.id = "file-abc"
        self.mock_client.files.create.return_value = file_resp

        job_resp = MagicMock()
        job_resp.id = "ftjob-123"
        job_resp.status = "queued"
        self.mock_client.fine_tuning.jobs.create.return_value = job_resp

        req = FineTuneRequest(
            job_id="job-1",
            dataset_path=self.dataset_path,
            base_model="qwen3:latest",
        )
        self.provider.start_training(req)
        call_kwargs = self.mock_client.fine_tuning.jobs.create.call_args
        self.assertEqual(call_kwargs.kwargs["model"], "gpt-4o-mini-2024-07-18")

    def test_cui_allowed_for_cui_classification(self):
        """CUI classification should NOT be blocked from cloud."""
        file_resp = MagicMock()
        file_resp.id = "file-abc"
        self.mock_client.files.create.return_value = file_resp

        job_resp = MagicMock()
        job_resp.id = "ftjob-123"
        job_resp.status = "queued"
        self.mock_client.fine_tuning.jobs.create.return_value = job_resp

        req = FineTuneRequest(
            job_id="job-1",
            dataset_path=self.dataset_path,
            classification="CUI",
        )
        status = self.provider.start_training(req)
        self.assertEqual(status.status, "pending")


class TestAzureGetStatus(unittest.TestCase):
    def setUp(self):
        from tools.finetune.azure_provider import AzureOpenAIFineTuneProvider

        self.provider = AzureOpenAIFineTuneProvider(api_key="test-key", endpoint="https://test.openai.azure.com")
        self.mock_client = MagicMock()
        self.provider._client = self.mock_client

    def test_running_with_metrics(self):
        job = MagicMock()
        job.id = "ftjob-azure-123"
        job.status = "running"
        job.fine_tuned_model = None
        self.mock_client.fine_tuning.jobs.retrieve.return_value = job

        event = MagicMock()
        event.data = {"step": 25, "total_steps": 50, "train_loss": 0.55}
        self.mock_client.fine_tuning.jobs.list_events.return_value = MagicMock(data=[event])

        status = self.provider.get_status("ftjob-azure-123")
        self.assertEqual(status.status, "training")
        self.assertAlmostEqual(status.progress_pct, 50.0)

    def test_succeeded(self):
        job = MagicMock()
        job.id = "ftjob-azure-123"
        job.status = "succeeded"
        job.fine_tuned_model = "gpt-4o-mini-ft-abc"
        self.mock_client.fine_tuning.jobs.retrieve.return_value = job

        status = self.provider.get_status("ftjob-azure-123")
        self.assertEqual(status.status, "completed")
        self.assertEqual(status.ollama_model_name, "gpt-4o-mini-ft-abc")

    def test_failed(self):
        job = MagicMock()
        job.id = "ftjob-azure-123"
        job.status = "failed"
        job.error = "data format error"
        job.fine_tuned_model = None
        self.mock_client.fine_tuning.jobs.retrieve.return_value = job

        status = self.provider.get_status("ftjob-azure-123")
        self.assertEqual(status.status, "failed")
        self.assertIn("data format error", status.error)

    def test_retrieve_error(self):
        self.mock_client.fine_tuning.jobs.retrieve.side_effect = Exception("not found")
        status = self.provider.get_status("ftjob-missing")
        self.assertEqual(status.status, "failed")


class TestAzureCancelAndList(unittest.TestCase):
    def setUp(self):
        from tools.finetune.azure_provider import AzureOpenAIFineTuneProvider

        self.provider = AzureOpenAIFineTuneProvider(api_key="test-key", endpoint="https://test.openai.azure.com")
        self.mock_client = MagicMock()
        self.provider._client = self.mock_client

    def test_cancel_success(self):
        self.assertTrue(self.provider.cancel_training("ftjob-123"))

    def test_cancel_failure(self):
        self.mock_client.fine_tuning.jobs.cancel.side_effect = Exception("err")
        self.assertFalse(self.provider.cancel_training("ftjob-123"))

    def test_list_models(self):
        m1 = MagicMock()
        m1.id = "gpt-4o-mini-2024-07-18"
        m2 = MagicMock()
        m2.id = "text-embedding-3-small"
        self.mock_client.models.list.return_value = MagicMock(data=[m1, m2])
        models = self.provider.list_models()
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0]["provider"], "azure_openai")

    def test_list_models_error(self):
        self.mock_client.models.list.side_effect = Exception("err")
        self.assertEqual(self.provider.list_models(), [])


class TestAzureValidateDataset(unittest.TestCase):
    def setUp(self):
        from tools.finetune.azure_provider import AzureOpenAIFineTuneProvider

        self.provider = AzureOpenAIFineTuneProvider(api_key="test-key", endpoint="https://test.openai.azure.com")

    def test_file_not_found(self):
        result = self.provider.validate_dataset("/nonexistent/file.jsonl")
        self.assertFalse(result["valid"])

    def test_valid_dataset(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for i in range(15):
                f.write(
                    json.dumps(
                        {
                            "messages": [
                                {"role": "user", "content": f"q{i}"},
                                {"role": "assistant", "content": f"a{i}"},
                            ]
                        }
                    )
                    + "\n"
                )
            path = f.name
        try:
            result = self.provider.validate_dataset(path)
            self.assertTrue(result["valid"])
        finally:
            os.unlink(path)

    def test_invalid_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write("not json\n" * 15)
            path = f.name
        try:
            result = self.provider.validate_dataset(path)
            self.assertFalse(result["valid"])
            self.assertTrue(any("invalid JSON" in e for e in result["errors"]))
        finally:
            os.unlink(path)


# ── Provider Factory Tests ────────────────────────────────────────────


class TestProviderFactoryCloudProviders(unittest.TestCase):
    """Test that cloud providers register correctly in the factory."""

    def test_openai_registered(self):
        from tools.finetune.provider_factory import get_provider

        p = get_provider("openai")
        self.assertEqual(p.provider_name, "openai")

    def test_bedrock_registered(self):
        from tools.finetune.provider_factory import get_provider

        p = get_provider("bedrock")
        self.assertEqual(p.provider_name, "bedrock")

    def test_azure_openai_registered(self):
        from tools.finetune.provider_factory import get_provider

        p = get_provider("azure_openai")
        self.assertEqual(p.provider_name, "azure_openai")


if __name__ == "__main__":
    unittest.main()
