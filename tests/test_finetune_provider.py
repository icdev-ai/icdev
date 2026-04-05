#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for FineTuneProvider ABC, dataclasses, and factory (D-FT-1)."""

from __future__ import annotations

import json
from typing import Any, Dict

import pytest

from tools.finetune.provider import (
    FineTuneProvider,
    FineTuneRequest,
    FineTuneStatus,
)


# ---- FineTuneRequest tests ----


class TestFineTuneRequest:
    def test_default_values(self):
        req = FineTuneRequest()
        assert req.base_model == "qwen3:latest"
        assert req.lora_rank == 16
        assert req.lora_alpha == 32
        assert req.learning_rate == 2e-4
        assert req.epochs == 3
        assert req.batch_size == 2
        assert req.max_seq_length == 2048
        assert req.classification == "CUI"

    def test_custom_values(self):
        req = FineTuneRequest(
            base_model="llama3.1:8b",
            lora_rank=32,
            learning_rate=1e-4,
            epochs=5,
            gpu_count=2,
            distributed=True,
        )
        assert req.base_model == "llama3.1:8b"
        assert req.lora_rank == 32
        assert req.gpu_count == 2
        assert req.distributed is True

    def test_to_dict(self):
        req = FineTuneRequest(job_id="job-123")
        d = req.to_dict()
        assert isinstance(d, dict)
        assert d["job_id"] == "job-123"
        assert d["base_model"] == "qwen3:latest"
        assert "target_modules" in d

    def test_to_json(self):
        req = FineTuneRequest(job_id="job-123")
        j = req.to_json()
        parsed = json.loads(j)
        assert parsed["job_id"] == "job-123"

    def test_from_dict(self):
        d = {"base_model": "mistral:7b", "lora_rank": 8, "unknown_field": "ignored"}
        req = FineTuneRequest.from_dict(d)
        assert req.base_model == "mistral:7b"
        assert req.lora_rank == 8
        # unknown field ignored
        assert not hasattr(req, "unknown_field") or req.extra_params == {}

    def test_target_modules_default(self):
        req = FineTuneRequest()
        assert "q_proj" in req.target_modules
        assert "v_proj" in req.target_modules
        assert len(req.target_modules) == 7

    def test_extra_params(self):
        req = FineTuneRequest(extra_params={"custom_key": "custom_val"})
        assert req.extra_params["custom_key"] == "custom_val"


# ---- FineTuneStatus tests ----


class TestFineTuneStatus:
    def test_default_status(self):
        status = FineTuneStatus()
        assert status.status == "pending"
        assert status.progress_pct == 0.0
        assert status.is_terminal is False

    def test_terminal_states(self):
        for state in ("completed", "failed", "canceled"):
            s = FineTuneStatus(status=state)
            assert s.is_terminal is True

    def test_non_terminal_states(self):
        for state in ("pending", "training", "exporting"):
            s = FineTuneStatus(status=state)
            assert s.is_terminal is False

    def test_to_dict(self):
        s = FineTuneStatus(job_id="j-1", status="training", current_loss=0.5)
        d = s.to_dict()
        assert d["job_id"] == "j-1"
        assert d["current_loss"] == 0.5

    def test_loss_history(self):
        s = FineTuneStatus(loss_history=[1.0, 0.8, 0.6, 0.4])
        assert len(s.loss_history) == 4
        assert s.loss_history[-1] == 0.4


# ---- FineTuneProvider ABC tests ----


class ConcreteProvider(FineTuneProvider):
    """Test implementation of FineTuneProvider."""

    @property
    def provider_name(self) -> str:
        return "test_provider"

    def start_training(self, request: FineTuneRequest) -> FineTuneStatus:
        return FineTuneStatus(job_id="test-job", status="training")

    def get_status(self, job_id: str) -> FineTuneStatus:
        return FineTuneStatus(job_id=job_id, status="completed")

    def cancel_training(self, job_id: str) -> bool:
        return True

    def check_availability(self) -> Dict[str, Any]:
        return {"available": True, "gpu": True}


class TestFineTuneProviderABC:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            FineTuneProvider()

    def test_concrete_provider(self):
        p = ConcreteProvider()
        assert p.provider_name == "test_provider"

    def test_start_training(self):
        p = ConcreteProvider()
        req = FineTuneRequest(dataset_path="/tmp/data.jsonl")
        status = p.start_training(req)
        assert status.job_id == "test-job"
        assert status.status == "training"

    def test_get_status(self):
        p = ConcreteProvider()
        status = p.get_status("test-job")
        assert status.status == "completed"
        assert status.is_terminal is True

    def test_cancel_training(self):
        p = ConcreteProvider()
        assert p.cancel_training("test-job") is True

    def test_check_availability(self):
        p = ConcreteProvider()
        result = p.check_availability()
        assert result["available"] is True

    def test_export_gguf_not_implemented(self):
        p = ConcreteProvider()
        with pytest.raises(NotImplementedError, match="test_provider"):
            p.export_gguf("/tmp/adapter", "/tmp/output.gguf")

    def test_list_models_default(self):
        p = ConcreteProvider()
        assert p.list_models() == []

    def test_validate_dataset_default(self):
        p = ConcreteProvider()
        result = p.validate_dataset("/tmp/data.jsonl")
        assert result["valid"] is True
        assert result["errors"] == []
