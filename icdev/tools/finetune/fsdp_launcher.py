#!/usr/bin/env python3
# CUI // SP-CTI
"""FSDP multi-GPU training launcher for fine-tuning jobs.

Wires PyTorch FSDP (Fully Sharded Data Parallel) into the ICDEV fine-tuning pipeline.
Activated when FineTuneRequest.distributed=True and gpu_count > 1.

Inspired by DeepSpec's base_trainer.py FSDP setup with hybrid sharding strategies,
BF16 precision, gradient accumulation, and stateless resumable samplers.

Configuration (args/finetune_config.yaml → fsdp section):
    fsdp:
      sharding_strategy: "full_shard"   # full_shard | shard_grad_op | no_shard | hybrid_shard
      precision: "bf16"                 # bf16 | fp16 | fp32
      gradient_accumulation_steps: 4
      cpu_offload: false
      activation_checkpointing: false
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("icdev.finetune.fsdp_launcher")

BASE_DIR = Path(__file__).resolve().parent.parent.parent

_SHARDING_STRATEGIES = {
    "full_shard": "FULL_SHARD",
    "shard_grad_op": "SHARD_GRAD_OP",
    "no_shard": "NO_SHARD",
    "hybrid_shard": "HYBRID_SHARD",
}


class FSDPUnavailable(Exception):
    """Raised when FSDP is requested but torch.distributed is not installed."""


@dataclass
class FSDPConfig:
    """Configuration for FSDP training launch."""

    sharding_strategy: str = "full_shard"
    precision: str = "bf16"
    gradient_accumulation_steps: int = 4
    cpu_offload: bool = False
    activation_checkpointing: bool = False
    bucket_cap_mb: int = 25
    min_num_params: int = 100_000


class FSDPLauncher:
    """Builds and launches distributed FSDP training subprocesses."""

    def __init__(self, config: FSDPConfig, gpu_count: int = 1) -> None:
        self.config = config
        self.gpu_count = gpu_count

    def is_available(self) -> bool:
        """Return True if torch.distributed is present and gpu_count > 1."""
        if self.gpu_count <= 1:
            return False
        try:
            import torch.distributed  # noqa: F401

            return True
        except ImportError:
            return False

    def _detect_launcher(self) -> str:
        """Return the preferred distributed launcher module name."""
        try:
            # Newer PyTorch (>=1.10) ships torchrun via torch.distributed.run
            import torch.distributed.run  # noqa: F401

            return "torch.distributed.run"
        except (ImportError, ModuleNotFoundError):
            pass
        try:
            import torch.distributed.launch  # noqa: F401

            return "torch.distributed.launch"
        except (ImportError, ModuleNotFoundError):
            pass
        # Final fallback — let subprocess surface the error
        return "torch.distributed.run"

    def build_launch_command(self, script_path: str, script_args: list[str]) -> list[str]:
        """Construct torchrun / torch.distributed.run launch command."""
        launcher = self._detect_launcher()
        cmd = [
            sys.executable,
            "-m",
            launcher,
            "--standalone",
            f"--nproc_per_node={self.gpu_count}",
            script_path,
        ]
        cmd.extend(script_args)
        return cmd

    def wrap_model(self, model: Any, *, mixed_precision: bool = True) -> Any:
        """Wrap model with FSDP if torch is available; return model unchanged otherwise.

        Attaches a .fsdp_config attribute to the returned object for serialization.
        """
        try:
            import torch
            from torch.distributed.fsdp import FullyShardedDataParallel as FSDP
            from torch.distributed.fsdp import MixedPrecision

            kwargs: dict[str, Any] = {
                "device_id": torch.cuda.current_device() if torch.cuda.is_available() else None,
            }

            if mixed_precision and self.config.precision in ("bf16", "fp16"):
                dtype = torch.bfloat16 if self.config.precision == "bf16" else torch.float16
                kwargs["mixed_precision"] = MixedPrecision(
                    param_dtype=dtype,
                    reduce_dtype=dtype,
                    buffer_dtype=dtype,
                )

            wrapped = FSDP(model, **kwargs)
            wrapped.fsdp_config = self.config  # type: ignore[attr-defined]
            return wrapped
        except ImportError:
            logger.warning("torch/FSDP not available; returning model unwrapped")
            model.fsdp_config = self.config  # type: ignore[attr-defined]
            return model

    def launch_training(self, request: Any, training_script: str) -> dict[str, Any]:
        """Build launch command and start training subprocess (non-blocking).

        Returns a status dict with job_id, pid, command, status, and error.
        """
        if not self.is_available():
            raise FSDPUnavailable(
                "FSDP training requires torch.distributed and gpu_count > 1. "
                f"gpu_count={self.gpu_count}, torch available={self._torch_present()}"
            )

        job_id = f"fsdp-{uuid.uuid4().hex[:12]}"
        script_args = [
            "--job-id", getattr(request, "job_id", job_id),
            "--dataset-path", getattr(request, "dataset_path", ""),
            "--base-model", getattr(request, "base_model", ""),
            "--output-dir", getattr(request, "output_dir", ""),
            "--epochs", str(getattr(request, "epochs", 3)),
            "--batch-size", str(getattr(request, "batch_size", 2)),
            "--lora-rank", str(getattr(request, "lora_rank", 16)),
            "--gradient-accumulation-steps", str(self.config.gradient_accumulation_steps),
            "--precision", self.config.precision,
            "--sharding-strategy", self.config.sharding_strategy,
        ]

        cmd = self.build_launch_command(training_script, script_args)

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            logger.info("FSDP launcher started: pid=%s cmd=%s", proc.pid, " ".join(cmd))
            return {
                "job_id": job_id,
                "pid": proc.pid,
                "command": " ".join(cmd),
                "status": "launched",
                "error": "",
            }
        except Exception as exc:
            logger.error("FSDP launch failed: %s", exc)
            return {
                "job_id": job_id,
                "pid": -1,
                "command": " ".join(cmd),
                "status": "failed",
                "error": str(exc),
            }

    def _torch_present(self) -> bool:
        try:
            import torch  # noqa: F401

            return True
        except ImportError:
            return False


def get_fsdp_config(config_path: str | None = None) -> FSDPConfig:
    """Load FSDP config from args/finetune_config.yaml; fall back to defaults."""
    path = Path(config_path) if config_path else BASE_DIR / "args" / "finetune_config.yaml"
    if not path.exists():
        return FSDPConfig()
    try:
        import yaml

        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        section = raw.get("fsdp", {})
        return FSDPConfig(
            sharding_strategy=section.get("sharding_strategy", "full_shard"),
            precision=section.get("precision", "bf16"),
            gradient_accumulation_steps=int(section.get("gradient_accumulation_steps", 4)),
            cpu_offload=bool(section.get("cpu_offload", False)),
            activation_checkpointing=bool(section.get("activation_checkpointing", False)),
            bucket_cap_mb=int(section.get("bucket_cap_mb", 25)),
            min_num_params=int(section.get("min_num_params", 100_000)),
        )
    except Exception as exc:
        logger.warning("Failed to load FSDP config from %s: %s; using defaults", path, exc)
        return FSDPConfig()


# ── CLI ───────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="FSDP multi-GPU launcher")
    parser.add_argument("--check", action="store_true", help="Print FSDP availability and config")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args()

    if args.check:
        cfg = get_fsdp_config()
        try:
            import torch
            import torch.distributed  # noqa: F401

            torch_available = True
            torch_version = torch.__version__
        except ImportError:
            torch_available = False
            torch_version = ""

        result = {
            "torch_available": torch_available,
            "torch_version": torch_version,
            "fsdp_available": torch_available,
            "config": {
                "sharding_strategy": cfg.sharding_strategy,
                "precision": cfg.precision,
                "gradient_accumulation_steps": cfg.gradient_accumulation_steps,
                "cpu_offload": cfg.cpu_offload,
                "activation_checkpointing": cfg.activation_checkpointing,
                "bucket_cap_mb": cfg.bucket_cap_mb,
                "min_num_params": cfg.min_num_params,
            },
        }
        print(json.dumps(result, indent=2))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
