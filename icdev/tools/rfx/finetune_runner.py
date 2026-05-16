#!/usr/bin/env python3
# CUI // SP-PROPIN
"""Unsloth / LoRA fine-tuning job runner.

Launches fine-tuning as a Python subprocess so the Flask dashboard stays
responsive. Job state is tracked in rfx_finetune_jobs (SQLite).

Flow:
  1. launch_job(job_id)  — called by Flask; spawns _worker subprocess
  2. _worker(job_id)     — runs in background; updates rfx_finetune_jobs
  3. build_training_data(job_id) — assembles accepted sections + corpus docs
  4. run_unsloth(...)    — actual LoRA training loop

No Docker required. GPU is recommended but not mandatory (uses 4-bit quant).
Unsloth install: pip install unsloth
"""

import json
import os
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get(
    "GOVPROPOSAL_DB_PATH", str(BASE_DIR / "data" / "govproposal.db")
))
UPLOAD_DIR = BASE_DIR / "data" / "rfx_uploads"
MODELS_DIR = BASE_DIR / "data" / "rfx_models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)
TRAINING_DIR = BASE_DIR / "data" / "rfx_training"
TRAINING_DIR.mkdir(parents=True, exist_ok=True)


def _conn():
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _update_job(job_id: str, **kwargs):
    """Partial update of a finetune job row."""
    if not kwargs:
        return
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [job_id]
    conn = _conn()
    try:
        conn.execute(
            f"UPDATE rfx_finetune_jobs SET {sets} WHERE id = ?", vals
        )
        conn.commit()
    finally:
        conn.close()


def build_training_data(job_id: str) -> Path:
    """Assemble JSONL training file from:
    - Accepted rfx_ai_sections (content_accepted, section_title as prompt)
    - Corpus rfx_documents (doc_type = 'corpus')

    Returns path to the .jsonl file.
    """
    conn = _conn()
    try:
        # Accepted AI sections
        sections = conn.execute("""
            SELECT section_title, volume, content_accepted
            FROM rfx_ai_sections
            WHERE hitl_status IN ('accepted', 'revised')
              AND content_accepted IS NOT NULL AND content_accepted != ''
        """).fetchall()

        # Corpus documents (plain text stored in rfx_documents.content)
        corpus = conn.execute("""
            SELECT filename, content FROM rfx_documents
            WHERE doc_type = 'corpus'
              AND exclude_from_training = 0
              AND content IS NOT NULL AND content != ''
        """).fetchall()
    finally:
        conn.close()

    records = []

    for s in sections:
        prompt = (
            f"Write a compelling government proposal {s['section_title']} "
            f"section for the {(s['volume'] or 'technical').replace('_',' ')} volume:"
        )
        records.append({"text": f"{prompt}\n\n{s['content_accepted']}"})

    for d in corpus:
        # Chunk corpus docs into ~500 word training examples
        words = (d["content"] or "").split()
        for i in range(0, len(words), 450):
            chunk = " ".join(words[i:i + 450])
            if len(chunk) > 100:
                records.append({"text": chunk})

    outfile = TRAINING_DIR / f"{job_id}_train.jsonl"
    with open(outfile, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    _update_job(job_id, training_samples=len(records))
    return outfile


def run_unsloth(
    job_id: str,
    training_file: Path,
    base_model: str = "unsloth/llama-3-8b-bnb-4bit",
    output_dir: Optional[Path] = None,
    lora_rank: int = 16,
    lora_alpha: int = 32,
    epochs: int = 3,
    batch_size: int = 2,
    learning_rate: float = 2e-4,
) -> Path:
    """Run Unsloth LoRA fine-tuning. Returns path to saved adapter.

    Requires: pip install unsloth transformers datasets trl peft
    GPU strongly recommended; falls back to CPU with 4-bit quant disabled.
    """
    try:
        from unsloth import FastLanguageModel
        import torch
        from datasets import Dataset
        from trl import SFTTrainer
        from transformers import TrainingArguments
    except ImportError as e:
        raise RuntimeError(
            f"Unsloth not installed: {e}. Run: pip install unsloth"
        ) from e

    if output_dir is None:
        output_dir = MODELS_DIR / job_id
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load base model with 4-bit quantisation
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=base_model,
        max_seq_length=2048,
        dtype=None,       # auto
        load_in_4bit=True,
    )

    # Add LoRA adapters
    model = FastLanguageModel.get_peft_model(
        model,
        r=lora_rank,
        target_modules=["q_proj", "k_proj", "v_proj",
                        "o_proj", "gate_proj", "up_proj", "down_proj"],
        lora_alpha=lora_alpha,
        lora_dropout=0,
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=42,
    )

    # Load dataset
    records = []
    with open(training_file, encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line.strip())
            if rec.get("text"):
                records.append(rec)
    dataset = Dataset.from_list(records)

    def tokenize_fn(examples):
        return tokenizer(
            examples["text"],
            truncation=True,
            max_length=2048,
            padding=False,
        )

    dataset = dataset.map(tokenize_fn, batched=True)

    training_args = TrainingArguments(
        output_dir=str(output_dir),
        num_train_epochs=epochs,
        per_device_train_batch_size=batch_size,
        gradient_accumulation_steps=4,
        learning_rate=learning_rate,
        fp16=not torch.cuda.is_bf16_supported(),
        bf16=torch.cuda.is_bf16_supported(),
        logging_steps=10,
        save_strategy="epoch",
        warmup_ratio=0.03,
        lr_scheduler_type="linear",
        optim="adamw_8bit",
        report_to="none",
    )

    def progress_callback(trainer_state, **kwargs):
        if hasattr(trainer_state, "epoch"):
            total_ep = training_args.num_train_epochs
            pct = int((trainer_state.epoch / total_ep) * 100)
            _update_job(
                job_id,
                current_epoch=int(trainer_state.epoch),
                total_epochs=total_ep,
                progress_pct=pct,
                train_loss=getattr(trainer_state, "log_history", [{}])[-1].get("loss"),  # noqa: E501
            )

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        dataset_text_field="text",
        max_seq_length=2048,
        args=training_args,
    )
    trainer.train()
    trainer.save_model(str(output_dir))

    return output_dir


def _worker(job_id: str):
    """Background worker: runs the full fine-tuning pipeline for a job."""
    now_fn = lambda: datetime.now(timezone.utc).isoformat()  # noqa: E731

    _update_job(job_id, status="running", started_at=now_fn())

    try:
        conn = _conn()
        try:
            row = conn.execute(
                "SELECT * FROM rfx_finetune_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if not row:
                return
            row = dict(row)
        finally:
            conn.close()

        # Build JSONL training corpus
        training_file = build_training_data(job_id)

        # Determine base model
        base_model = row.get("base_model") or "unsloth/llama-3-8b-bnb-4bit"
        lr = float(row.get("learning_rate") or 2e-4)

        output_dir = run_unsloth(
            job_id=job_id,
            training_file=training_file,
            base_model=base_model,
            lora_rank=int(row.get("lora_rank") or 16),
            lora_alpha=int(row.get("lora_alpha") or 32),
            epochs=int(row.get("epochs") or 3),
            batch_size=int(row.get("batch_size") or 2),
            learning_rate=lr,
        )

        _update_job(
            job_id,
            status="completed",
            completed_at=now_fn(),
            progress_pct=100,
            output_model_path=str(output_dir),
            current_epoch=int(row.get("epochs") or 3),
        )

    except Exception as exc:
        _update_job(
            job_id,
            status="failed",
            completed_at=now_fn(),
            error_message=str(exc)[:1000],
        )
        raise


def launch_job(job_id: str) -> None:
    """Spawn the fine-tuning worker as a detached subprocess.

    The worker runs `python -m tools.rfx.finetune_runner <job_id>` in the
    background so the Flask dashboard is not blocked.
    """
    python = sys.executable
    script = str(Path(__file__))
    proc = subprocess.Popen(
        [python, script, job_id],
        cwd=str(BASE_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,   # detach from parent process group
    )
    # Update job with the PID so it can be monitored
    _update_job(job_id, error_message=f"pid:{proc.pid}")


# ── CLI entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python finetune_runner.py <job_id>")
        sys.exit(1)
    _worker(sys.argv[1])
