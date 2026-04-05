#!/usr/bin/env python3
# CUI // SP-CTI
"""GPU auto-detection for fine-tuning (D-FT-8).

Detection chain: torch.cuda → nvidia-smi subprocess → CPU fallback.
Training requires GPU; serving via Ollama supports CPU-only.

Usage:
    python tools/finetune/gpu_detector.py --json
    python tools/finetune/gpu_detector.py --human
"""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class GPUInfo:
    """Information about a single GPU."""

    index: int = 0
    name: str = ""
    vram_total_mb: int = 0
    vram_free_mb: int = 0
    vram_used_mb: int = 0
    cuda_version: str = ""
    compute_capability: str = ""
    driver_version: str = ""
    temperature_c: int = 0
    utilization_pct: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def vram_total_gb(self) -> float:
        return round(self.vram_total_mb / 1024, 1)

    @property
    def vram_free_gb(self) -> float:
        return round(self.vram_free_mb / 1024, 1)


@dataclass
class GPUDetectionResult:
    """Result of GPU detection."""

    has_cuda: bool = False
    has_gpu: bool = False
    gpu_count: int = 0
    gpus: List[GPUInfo] = field(default_factory=list)
    total_vram_mb: int = 0
    max_single_gpu_vram_mb: int = 0
    cuda_version: str = ""
    driver_version: str = ""
    detection_method: str = "none"
    can_train: bool = False
    can_serve: bool = True  # Ollama CPU serving always possible
    recommended_batch_size: int = 1
    recommended_lora_rank: int = 8
    multi_gpu_available: bool = False
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d

    @property
    def training_capable(self) -> bool:
        """Check if any GPU has enough VRAM for training (>= 16GB)."""
        return self.max_single_gpu_vram_mb >= 16384

    @property
    def multi_gpu_training_capable(self) -> bool:
        """Check if multi-GPU training is possible (>= 2 GPUs with >= 16GB each)."""
        capable = [g for g in self.gpus if g.vram_total_mb >= 16384]
        return len(capable) >= 2


def _detect_via_torch() -> Optional[GPUDetectionResult]:
    """Detect GPUs via PyTorch CUDA API."""
    try:
        import torch
    except ImportError:
        return None

    if not torch.cuda.is_available():
        return GPUDetectionResult(
            detection_method="torch",
            errors=["torch.cuda.is_available() returned False"],
        )

    gpu_count = torch.cuda.device_count()
    gpus = []
    total_vram = 0
    max_single = 0

    for i in range(gpu_count):
        props = torch.cuda.get_device_properties(i)
        total_mb = getattr(props, "total_memory", getattr(props, "total_mem", 0)) // (1024 * 1024)
        # Get current free memory
        try:
            torch.cuda.set_device(i)
            free_mb = torch.cuda.mem_get_info(i)[0] // (1024 * 1024)
        except Exception:
            free_mb = total_mb

        gpu = GPUInfo(
            index=i,
            name=props.name,
            vram_total_mb=total_mb,
            vram_free_mb=free_mb,
            vram_used_mb=total_mb - free_mb,
            cuda_version=torch.version.cuda or "",
            compute_capability=f"{props.major}.{props.minor}",
        )
        gpus.append(gpu)
        total_vram += total_mb
        max_single = max(max_single, total_mb)

    result = GPUDetectionResult(
        has_cuda=True,
        has_gpu=gpu_count > 0,
        gpu_count=gpu_count,
        gpus=gpus,
        total_vram_mb=total_vram,
        max_single_gpu_vram_mb=max_single,
        cuda_version=torch.version.cuda or "",
        detection_method="torch",
        multi_gpu_available=gpu_count >= 2,
    )
    _set_recommendations(result)
    return result


def _detect_via_nvidia_smi() -> Optional[GPUDetectionResult]:
    """Detect GPUs via nvidia-smi subprocess."""
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,memory.total,memory.free,memory.used,"
                "temperature.gpu,utilization.gpu,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            return None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    gpus = []
    total_vram = 0
    max_single = 0
    driver_version = ""

    for line in proc.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 8:
            continue

        try:
            idx = int(parts[0])
            name = parts[1]
            total_mb = int(parts[2])
            free_mb = int(parts[3])
            used_mb = int(parts[4])
            temp = int(parts[5]) if parts[5] else 0
            util = int(parts[6]) if parts[6] else 0
            driver = parts[7]
        except (ValueError, IndexError):
            continue

        gpu = GPUInfo(
            index=idx,
            name=name,
            vram_total_mb=total_mb,
            vram_free_mb=free_mb,
            vram_used_mb=used_mb,
            driver_version=driver,
            temperature_c=temp,
            utilization_pct=util,
        )
        gpus.append(gpu)
        total_vram += total_mb
        max_single = max(max_single, total_mb)
        driver_version = driver

    if not gpus:
        return None

    result = GPUDetectionResult(
        has_cuda=True,
        has_gpu=len(gpus) > 0,
        gpu_count=len(gpus),
        gpus=gpus,
        total_vram_mb=total_vram,
        max_single_gpu_vram_mb=max_single,
        driver_version=driver_version,
        detection_method="nvidia-smi",
        multi_gpu_available=len(gpus) >= 2,
    )
    _set_recommendations(result)
    return result


def _set_recommendations(result: GPUDetectionResult) -> None:
    """Set training recommendations based on VRAM."""
    vram = result.max_single_gpu_vram_mb
    result.can_train = vram >= 7800  # ~8GB minimum for QLoRA (7800 accounts for driver overhead on 8GB cards)

    if vram >= 49152:  # 48GB+
        result.recommended_batch_size = 8
        result.recommended_lora_rank = 64
    elif vram >= 24576:  # 24GB+
        result.recommended_batch_size = 4
        result.recommended_lora_rank = 32
    elif vram >= 16384:  # 16GB+
        result.recommended_batch_size = 2
        result.recommended_lora_rank = 16
    elif vram >= 7800:  # ~8GB (accounts for driver overhead on 8GB cards)
        result.recommended_batch_size = 1
        result.recommended_lora_rank = 8
    else:
        result.recommended_batch_size = 1
        result.recommended_lora_rank = 8
        result.can_train = False


def detect_gpu() -> GPUDetectionResult:
    """Detect GPU capabilities using the best available method.

    Detection chain (D-FT-8):
        1. torch.cuda (most accurate)
        2. nvidia-smi subprocess (no Python deps)
        3. CPU fallback
    """
    # Method 1: PyTorch
    result = _detect_via_torch()
    if result and result.has_gpu:
        return result

    # Method 2: nvidia-smi
    result = _detect_via_nvidia_smi()
    if result and result.has_gpu:
        return result

    # Method 3: CPU fallback
    return GPUDetectionResult(
        detection_method="cpu_fallback",
        can_train=False,
        can_serve=True,
        errors=["No NVIDIA GPU detected. CPU-only serving via Ollama supported."],
    )


def main():
    parser = argparse.ArgumentParser(description="Detect GPU capabilities for fine-tuning")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    args = parser.parse_args()

    result = detect_gpu()

    if args.json or not args.human:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print("=== GPU Detection Results ===")
        print(f"Detection method: {result.detection_method}")
        print(f"CUDA available:   {result.has_cuda}")
        print(f"GPU count:        {result.gpu_count}")
        print(f"Total VRAM:       {result.total_vram_mb} MB ({result.total_vram_mb / 1024:.1f} GB)")
        print(f"Can train:        {result.can_train}")
        print(f"Can serve:        {result.can_serve}")
        print(f"Multi-GPU:        {result.multi_gpu_available}")
        print()
        for gpu in result.gpus:
            print(f"  GPU {gpu.index}: {gpu.name}")
            print(f"    VRAM: {gpu.vram_total_mb} MB total, {gpu.vram_free_mb} MB free")
            if gpu.temperature_c:
                print(f"    Temp: {gpu.temperature_c}°C, Util: {gpu.utilization_pct}%")
        print()
        print(f"Recommended batch size:  {result.recommended_batch_size}")
        print(f"Recommended LoRA rank:   {result.recommended_lora_rank}")
        if result.errors:
            print(f"Errors: {', '.join(result.errors)}")


if __name__ == "__main__":
    main()
