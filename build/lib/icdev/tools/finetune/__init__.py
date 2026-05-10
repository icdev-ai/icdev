#!/usr/bin/env python3
# CUI // SP-CTI
"""Fine-Tuning & Custom Model Training Subsystem (Phase 64 Extension).

Provides QLoRA fine-tuning via Unsloth (local) and cloud providers
(OpenAI, Bedrock, Azure OpenAI). Fine-tuned models slot into the
two-tier LLM routing as worker-tier replacements.

Key modules:
    provider        — FineTuneProvider ABC + request/status dataclasses
    gpu_detector    — CUDA / VRAM / multi-GPU detection
    provider_factory — Factory with GPU auto-detection
    dataset_manager — Dataset CRUD, versioning, JSONL export
    labeler         — Multi-dimensional labeling backend
    training_engine — Orchestrate full training pipeline
    evaluator       — BLEU / ROUGE-L / perplexity scoring
    promotion_manager — Threshold-based auto-promote + manual override
"""

from __future__ import annotations

__version__ = "0.1.0"
