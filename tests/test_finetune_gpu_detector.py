#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for GPU detector (D-FT-8)."""

from __future__ import annotations

from unittest.mock import patch, MagicMock


from tools.finetune.gpu_detector import (
    GPUDetectionResult,
    GPUInfo,
    _detect_via_nvidia_smi,
    _detect_via_torch,
    _set_recommendations,
    detect_gpu,
)


# ---- GPUInfo tests ----


class TestGPUInfo:
    def test_default_values(self):
        gpu = GPUInfo()
        assert gpu.index == 0
        assert gpu.vram_total_mb == 0

    def test_vram_conversion(self):
        gpu = GPUInfo(vram_total_mb=24576, vram_free_mb=20480)
        assert gpu.vram_total_gb == 24.0
        assert gpu.vram_free_gb == 20.0

    def test_to_dict(self):
        gpu = GPUInfo(index=0, name="RTX 4090", vram_total_mb=24576)
        d = gpu.to_dict()
        assert d["name"] == "RTX 4090"
        assert d["vram_total_mb"] == 24576


# ---- GPUDetectionResult tests ----


class TestGPUDetectionResult:
    def test_cpu_fallback_defaults(self):
        r = GPUDetectionResult()
        assert r.has_cuda is False
        assert r.has_gpu is False
        assert r.can_serve is True  # Ollama CPU serving
        assert r.can_train is False

    def test_training_capable_24gb(self):
        r = GPUDetectionResult(max_single_gpu_vram_mb=24576)
        assert r.training_capable is True

    def test_training_not_capable_8gb(self):
        r = GPUDetectionResult(max_single_gpu_vram_mb=8192)
        assert r.training_capable is False  # needs >= 16GB

    def test_multi_gpu_capable(self):
        gpus = [
            GPUInfo(index=0, vram_total_mb=24576),
            GPUInfo(index=1, vram_total_mb=24576),
        ]
        r = GPUDetectionResult(gpus=gpus)
        assert r.multi_gpu_training_capable is True

    def test_multi_gpu_not_capable_mixed(self):
        gpus = [
            GPUInfo(index=0, vram_total_mb=24576),
            GPUInfo(index=1, vram_total_mb=8192),
        ]
        r = GPUDetectionResult(gpus=gpus)
        assert r.multi_gpu_training_capable is False


# ---- Recommendations tests ----


class TestRecommendations:
    def test_48gb_recommendations(self):
        r = GPUDetectionResult(max_single_gpu_vram_mb=49152)
        _set_recommendations(r)
        assert r.recommended_batch_size == 8
        assert r.recommended_lora_rank == 64
        assert r.can_train is True

    def test_24gb_recommendations(self):
        r = GPUDetectionResult(max_single_gpu_vram_mb=24576)
        _set_recommendations(r)
        assert r.recommended_batch_size == 4
        assert r.recommended_lora_rank == 32
        assert r.can_train is True

    def test_16gb_recommendations(self):
        r = GPUDetectionResult(max_single_gpu_vram_mb=16384)
        _set_recommendations(r)
        assert r.recommended_batch_size == 2
        assert r.recommended_lora_rank == 16
        assert r.can_train is True

    def test_8gb_recommendations(self):
        r = GPUDetectionResult(max_single_gpu_vram_mb=8192)
        _set_recommendations(r)
        assert r.recommended_batch_size == 1
        assert r.recommended_lora_rank == 8
        assert r.can_train is True

    def test_4gb_no_training(self):
        r = GPUDetectionResult(max_single_gpu_vram_mb=4096)
        _set_recommendations(r)
        assert r.can_train is False


# ---- Detection method tests ----


class TestDetectViaTorch:
    @patch("tools.finetune.gpu_detector.torch", create=True)
    def test_torch_not_available(self, mock_torch):
        """When torch.cuda.is_available() is False."""
        mock_torch.cuda.is_available.return_value = False
        # Need to re-import to use the mock
        with patch.dict("sys.modules", {"torch": mock_torch}):
            result = _detect_via_torch()
            assert result is not None
            assert result.has_gpu is False

    def test_torch_not_installed(self):
        """When torch is not installed."""
        with patch.dict("sys.modules", {"torch": None}):
            with patch("builtins.__import__", side_effect=ImportError("no torch")):
                result = _detect_via_torch()
                assert result is None


class TestDetectViaNvidiaSmi:
    @patch("subprocess.run")
    def test_nvidia_smi_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="0, NVIDIA RTX 4090, 24564, 20000, 4564, 45, 10, 550.67\n",
        )
        result = _detect_via_nvidia_smi()
        assert result is not None
        assert result.has_gpu is True
        assert result.gpu_count == 1
        assert result.gpus[0].name == "NVIDIA RTX 4090"
        assert result.gpus[0].vram_total_mb == 24564

    @patch("subprocess.run")
    def test_nvidia_smi_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        result = _detect_via_nvidia_smi()
        assert result is None

    @patch("subprocess.run")
    def test_nvidia_smi_multi_gpu(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "0, NVIDIA RTX 4090, 24564, 20000, 4564, 45, 10, 550.67\n"
                "1, NVIDIA RTX 4090, 24564, 22000, 2564, 42, 5, 550.67\n"
            ),
        )
        result = _detect_via_nvidia_smi()
        assert result is not None
        assert result.gpu_count == 2
        assert result.multi_gpu_available is True
        assert result.total_vram_mb == 49128


# ---- Main detect_gpu tests ----


class TestDetectGpu:
    @patch("tools.finetune.gpu_detector._detect_via_torch", return_value=None)
    @patch("tools.finetune.gpu_detector._detect_via_nvidia_smi", return_value=None)
    def test_cpu_fallback(self, mock_smi, mock_torch):
        result = detect_gpu()
        assert result.detection_method == "cpu_fallback"
        assert result.can_train is False
        assert result.can_serve is True

    @patch("tools.finetune.gpu_detector._detect_via_torch")
    def test_torch_preferred(self, mock_torch):
        gpu_result = GPUDetectionResult(
            has_cuda=True,
            has_gpu=True,
            gpu_count=1,
            gpus=[GPUInfo(index=0, name="RTX 4090", vram_total_mb=24576)],
            total_vram_mb=24576,
            max_single_gpu_vram_mb=24576,
            detection_method="torch",
            can_train=True,
        )
        mock_torch.return_value = gpu_result
        result = detect_gpu()
        assert result.detection_method == "torch"
        assert result.has_gpu is True
