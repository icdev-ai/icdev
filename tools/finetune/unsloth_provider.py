#!/usr/bin/env python3
# CUI // SP-CTI
"""Unsloth local QLoRA fine-tuning provider (D-FT-2).

Invokes Unsloth via subprocess for local GPU training.
Handles 4-bit quantization, LoRA injection, gradient checkpointing,
multi-GPU via accelerate, and GGUF export.

Requires: unsloth, torch, transformers (installed in training venv)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.finetune.provider import FineTuneProvider, FineTuneRequest, FineTuneStatus
from tools.finetune.gpu_detector import GPUDetectionResult, detect_gpu

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class UnslothLocalProvider(FineTuneProvider):
    """Local QLoRA fine-tuning via Unsloth subprocess (D-FT-2)."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        gpu_result: Optional[GPUDetectionResult] = None,
    ):
        self._config = config or {}
        self._gpu = gpu_result

    @property
    def provider_name(self) -> str:
        return "unsloth_local"

    def check_availability(self) -> Dict[str, Any]:
        """Check if Unsloth + GPU are available."""
        gpu = self._gpu or detect_gpu()
        has_unsloth = self._check_unsloth_installed()

        return {
            "available": gpu.can_train and has_unsloth,
            "has_gpu": gpu.has_gpu,
            "gpu_count": gpu.gpu_count,
            "total_vram_mb": gpu.total_vram_mb,
            "can_train": gpu.can_train,
            "has_unsloth": has_unsloth,
            "recommended_batch_size": gpu.recommended_batch_size,
            "recommended_lora_rank": gpu.recommended_lora_rank,
        }

    def _check_unsloth_installed(self) -> bool:
        """Check if unsloth package is available."""
        try:
            proc = subprocess.run(
                [sys.executable, "-c", "import unsloth; print(unsloth.__version__)"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return proc.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def start_training(self, request: FineTuneRequest) -> FineTuneStatus:
        """Start a QLoRA training job via Unsloth subprocess."""
        gpu = self._gpu or detect_gpu()
        if not gpu.can_train:
            return FineTuneStatus(
                job_id=request.job_id,
                status="failed",
                error="No GPU available for training. Minimum 8GB VRAM required.",
            )

        # Prepare output directory
        output_dir = request.output_dir or str(BASE_DIR / "data" / "finetune" / "jobs" / request.job_id)
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # Write training config
        train_config = {
            "dataset_path": request.dataset_path,
            "base_model": request.base_model,
            "lora_rank": request.lora_rank,
            "lora_alpha": request.lora_alpha,
            "lora_dropout": request.lora_dropout,
            "target_modules": request.target_modules,
            "learning_rate": request.learning_rate,
            "epochs": request.epochs,
            "batch_size": request.batch_size,
            "max_seq_length": request.max_seq_length,
            "warmup_steps": request.warmup_steps,
            "scheduler": request.scheduler,
            "quantization": request.quantization,
            "output_dir": output_dir,
            "gpu_count": request.gpu_count,
            "distributed": request.distributed,
            "gradient_checkpointing": self._config.get("local", {}).get("gradient_checkpointing", True),
            "use_flash_attention": self._config.get("local", {}).get("use_flash_attention", True),
        }

        config_path = Path(output_dir) / "train_config.json"
        with open(config_path, "w") as f:
            json.dump(train_config, f, indent=2)

        # Build training command
        cmd = self._build_training_command(train_config, config_path)

        # Launch subprocess
        try:
            log_path = Path(output_dir) / "training.log"
            with open(log_path, "w") as log_file:
                proc = subprocess.Popen(
                    cmd,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    cwd=str(BASE_DIR),
                    env={**os.environ, "PYTHONUNBUFFERED": "1"},
                )

            # Write PID for monitoring
            pid_path = Path(output_dir) / "training.pid"
            with open(pid_path, "w") as f:
                f.write(str(proc.pid))

            return FineTuneStatus(
                job_id=request.job_id,
                status="training",
                progress_pct=0.0,
            )
        except Exception as e:
            return FineTuneStatus(
                job_id=request.job_id,
                status="failed",
                error=f"Failed to start training: {e}",
            )

    def _build_training_command(self, config: Dict[str, Any], config_path: Path) -> List[str]:
        """Build the training subprocess command."""
        script = str(BASE_DIR / "tools" / "finetune" / "_train_worker.py")
        cmd = [sys.executable, script, "--config", str(config_path)]

        # Multi-GPU via accelerate (D-FT-21)
        if config.get("distributed") and config.get("gpu_count", 1) > 1:
            cmd = [
                sys.executable,
                "-m",
                "accelerate",
                "launch",
                "--num_processes",
                str(config["gpu_count"]),
                script,
                "--config",
                str(config_path),
            ]

        return cmd

    def get_status(self, job_id: str) -> FineTuneStatus:
        """Check training job status by reading output files."""
        job_dir = BASE_DIR / "data" / "finetune" / "jobs" / job_id

        if not job_dir.exists():
            return FineTuneStatus(job_id=job_id, status="failed", error="Job directory not found")

        # Check for completion marker
        result_path = job_dir / "training_result.json"
        if result_path.exists():
            with open(result_path) as f:
                result = json.load(f)
            return FineTuneStatus(
                job_id=job_id,
                status=result.get("status", "completed"),
                progress_pct=100.0 if result.get("status") == "completed" else 0.0,
                total_steps=result.get("total_steps", 0),
                current_loss=result.get("final_loss", 0.0),
                loss_history=result.get("loss_history", []),
                elapsed_seconds=result.get("elapsed_seconds", 0),
                adapter_path=result.get("adapter_path", ""),
                error=result.get("error", ""),
            )

        # Check progress file
        progress_path = job_dir / "training_progress.json"
        if progress_path.exists():
            try:
                with open(progress_path) as f:
                    progress = json.load(f)
                return FineTuneStatus(
                    job_id=job_id,
                    status="training",
                    progress_pct=progress.get("progress_pct", 0.0),
                    current_step=progress.get("current_step", 0),
                    total_steps=progress.get("total_steps", 0),
                    current_loss=progress.get("current_loss", 0.0),
                    loss_history=progress.get("loss_history", []),
                    elapsed_seconds=progress.get("elapsed_seconds", 0),
                )
            except (json.JSONDecodeError, IOError):
                pass

        # Check if process is still running
        pid_path = job_dir / "training.pid"
        if pid_path.exists():
            try:
                pid = int(pid_path.read_text().strip())
                os.kill(pid, 0)  # Check if process exists
                return FineTuneStatus(job_id=job_id, status="training")
            except (OSError, ValueError):
                # Process finished without result file = likely crashed
                return FineTuneStatus(
                    job_id=job_id,
                    status="failed",
                    error="Training process terminated unexpectedly. Check training.log",
                )

        return FineTuneStatus(job_id=job_id, status="pending")

    def cancel_training(self, job_id: str) -> bool:
        """Cancel a running training job."""
        job_dir = BASE_DIR / "data" / "finetune" / "jobs" / job_id
        pid_path = job_dir / "training.pid"

        if not pid_path.exists():
            return False

        try:
            pid = int(pid_path.read_text().strip())
            os.kill(pid, 15)  # SIGTERM
            return True
        except (OSError, ValueError):
            return False

    def export_gguf(
        self,
        adapter_path: str,
        output_path: str,
        quantization: str = "q4_k_m",
    ) -> str:
        """Export LoRA adapter to GGUF via Unsloth (D-FT-5)."""
        script = str(BASE_DIR / "tools" / "finetune" / "_export_worker.py")
        cmd = [
            sys.executable,
            script,
            "--adapter-path",
            adapter_path,
            "--output-path",
            output_path,
            "--quantization",
            quantization,
        ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,
                cwd=str(BASE_DIR),
            )
            if proc.returncode == 0:
                result = json.loads(proc.stdout)
                return result.get("gguf_path", output_path)
            else:
                raise RuntimeError(f"GGUF export failed: {proc.stderr}")
        except subprocess.TimeoutExpired:
            raise RuntimeError("GGUF export timed out (10 min)")

    def list_models(self) -> List[Dict[str, Any]]:
        """List available base models for local fine-tuning."""
        models = self._config.get("local", {}).get("base_models", [])
        return [{"model_id": m, "provider": "ollama"} for m in models]
