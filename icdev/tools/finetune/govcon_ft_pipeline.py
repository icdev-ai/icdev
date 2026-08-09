"""GovCon Local QLoRA Fine-Tuning Pipeline — Track B (Dev/Demo).

Fine-tunes Qwen2.5-1.5B-Instruct on govcon-proposals training pairs using
4-bit QLoRA (bitsandbytes) on a CUDA GPU. Saves a LoRA adapter (~10 MB).

Requirements (all on PyPI — push to internal mirror):
  transformers>=4.45.0   peft>=0.13.0   trl>=0.11.0
  accelerate>=0.34.0     bitsandbytes>=0.43.0   safetensors>=0.4.0
  torch>=2.3.0+cu121  (use CUDA variant on internal mirror)

Model weights: install qwen2_5_1_5b_instruct_weights from internal mirror
  OR set --model-path to a local directory with safetensors files.

Usage:
  python tools/finetune/govcon_ft_pipeline.py \\
    --dataset govcon-proposals \\
    --model-path /path/to/Qwen2.5-1.5B-Instruct \\
    --output models/govcon-adapter/ \\
    [--epochs 3] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402


# ---------------------------------------------------------------------------
# Training pair loader
# ---------------------------------------------------------------------------

def _load_training_pairs(dataset_name: str) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT e.system_prompt, e.user_input, e.expected_output
        FROM ft_dataset_examples e
        JOIN ft_datasets d ON d.id = e.dataset_id
        WHERE d.name = %s
        ORDER BY e.created_at ASC
        """,
        (dataset_name,),
    ).fetchall()
    return [{"system_prompt": r[0], "user_input": r[1], "expected_output": r[2]}
            for r in rows]


# ---------------------------------------------------------------------------
# Dataset formatting
# ---------------------------------------------------------------------------

def _format_chat(pair: dict, tokenizer) -> str:
    """Format a training pair as a chat template string."""
    messages = [
        {"role": "system", "content": pair["system_prompt"]},
        {"role": "user", "content": pair["user_input"]},
        {"role": "assistant", "content": pair["expected_output"]},
    ]
    return tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=False
    )


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------

def _load_model_and_tokenizer(model_path: str):
    """Load base model with 4-bit QLoRA quantization."""
    try:
        import torch  # type: ignore
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Required packages missing. pip install transformers bitsandbytes torch"
        ) from exc

    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )

    tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)  # nosec B615 — model_path is a controlled local or approved HF repo
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(  # nosec B615 — model_path is a controlled local or approved HF repo
        model_path,
        quantization_config=bnb_config,
        device_map="auto",
        trust_remote_code=True,
        torch_dtype=torch.bfloat16,
    )
    model.config.use_cache = False
    model.config.pretraining_tp = 1

    return model, tokenizer


# ---------------------------------------------------------------------------
# LoRA configuration
# ---------------------------------------------------------------------------

def _apply_lora(model):
    """Wrap model with LoRA adapter for fine-tuning."""
    try:
        from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training  # type: ignore
    except ImportError as exc:
        raise RuntimeError("peft is required. pip install peft") from exc

    model = prepare_model_for_kbit_training(model)

    lora_config = LoraConfig(
        r=16,
        lora_alpha=32,
        lora_dropout=0.05,
        target_modules=["q_proj", "v_proj", "k_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
        bias="none",
        task_type="CAUSAL_LM",
    )

    return get_peft_model(model, lora_config)


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def _train(
    model,
    tokenizer,
    pairs: list[dict],
    output_dir: str,
    epochs: int,
    batch_size: int,
    learning_rate: float,
    max_seq_length: int,
) -> dict:
    """Run SFTTrainer and return evaluation metrics."""
    try:
        from datasets import Dataset  # type: ignore
        from trl import SFTConfig, SFTTrainer  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "trl and datasets are required. pip install trl datasets"
        ) from exc

    formatted = [_format_chat(p, tokenizer) for p in pairs]
    dataset = Dataset.from_dict({"text": formatted})

    split = dataset.train_test_split(test_size=0.15, seed=42)
    train_ds = split["train"]
    eval_ds = split["test"]

    sft_config = SFTConfig(
        output_dir=output_dir,
        num_train_epochs=epochs,
        per_device_train_batch_size=batch_size,
        gradient_accumulation_steps=4,
        gradient_checkpointing=True,
        learning_rate=learning_rate,
        lr_scheduler_type="cosine",
        warmup_steps=10,
        weight_decay=0.01,
        max_grad_norm=1.0,
        logging_steps=10,
        save_steps=100,
        eval_strategy="epoch",
        save_total_limit=2,
        load_best_model_at_end=True,
        report_to="none",
        max_seq_length=max_seq_length,
        packing=False,
        dataset_text_field="text",
        fp16=False,
        bf16=True,
    )

    trainer = SFTTrainer(
        model=model,
        args=sft_config,
        train_dataset=train_ds,
        eval_dataset=eval_ds,
        tokenizer=tokenizer,
    )

    train_result = trainer.train()
    eval_result = trainer.evaluate()

    return {
        "train_loss": train_result.training_loss,
        "eval_loss": eval_result.get("eval_loss", 0.0),
        "train_samples": len(train_ds),
        "eval_samples": len(eval_ds),
        "epochs": epochs,
    }


# ---------------------------------------------------------------------------
# DB registration
# ---------------------------------------------------------------------------

def _register_adapter(conn, adapter_path: str, base_model: str, metrics: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO ft_model_versions
            (id, model_name, base_model, adapter_path, status, eval_custom, classification, created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            str(uuid.uuid4()),
            "govcon-proposal-adapter",
            base_model,
            adapter_path,
            "created",
            json.dumps(metrics),
            "CUI",
            now,
        ),
    )


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run(
    dataset_name: str = "govcon-proposals",
    model_path: str = "",
    output_dir: str = "models/govcon-adapter",
    epochs: int = 3,
    batch_size: int = 2,
    learning_rate: float = 0.0002,
    max_seq_length: int = 2048,
    dry_run: bool = False,
) -> dict:
    """Run the local QLoRA fine-tuning pipeline.

    Returns a result dict with metrics and adapter_path.
    """
    if not model_path:
        # Try to find weights from package installed via internal mirror
        try:
            import qwen_weights  # type: ignore
            model_path = qwen_weights.get_model_path()
        except ImportError:
            return {
                "status": "error",
                "error": (
                    "model_path required. Install qwen2_5_1_5b_instruct_weights from "
                    "internal mirror or pass --model-path."
                ),
            }

    # Load training pairs
    pairs = _load_training_pairs(dataset_name)
    if not pairs:
        return {"status": "no_data", "dataset": dataset_name, "pairs": 0}

    print(f"Loaded {len(pairs)} training pairs from dataset '{dataset_name}'")
    print(f"Base model: {model_path}")

    if dry_run:
        return {
            "dry_run": True,
            "dataset": dataset_name,
            "pairs": len(pairs),
            "model_path": model_path,
            "output_dir": output_dir,
            "epochs": epochs,
        }

    # Load model + tokenizer
    print("Loading model with 4-bit QLoRA quantization...")
    model, tokenizer = _load_model_and_tokenizer(model_path)

    # Apply LoRA
    model = _apply_lora(model)
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    print(f"Trainable parameters: {trainable:,} / {total:,} ({100*trainable/total:.2f}%)")

    # Train
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    print(f"Training for {epochs} epochs → {output_dir}")
    metrics = _train(
        model=model,
        tokenizer=tokenizer,
        pairs=pairs,
        output_dir=output_dir,
        epochs=epochs,
        batch_size=batch_size,
        learning_rate=learning_rate,
        max_seq_length=max_seq_length,
    )

    # Save adapter
    model.save_pretrained(output_dir)
    tokenizer.save_pretrained(output_dir)

    # Save eval metrics JSON
    metrics_path = Path(output_dir) / "eval_metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8", newline="")
    print(f"Adapter saved: {output_dir}")
    print(f"Train loss: {metrics['train_loss']:.4f}  Eval loss: {metrics['eval_loss']:.4f}")

    # Register in DB
    conn = get_connection()
    _register_adapter(conn, str(Path(output_dir).resolve()), model_path, metrics)

    return {
        "status": "completed",
        "dataset": dataset_name,
        "pairs": len(pairs),
        "adapter_path": output_dir,
        "metrics": metrics,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main() -> None:
    parser = argparse.ArgumentParser(description="Local QLoRA fine-tuning pipeline (Track B)")
    parser.add_argument("--dataset", default="govcon-proposals")
    parser.add_argument("--model-path", default="",
                        help="Path to base model weights (or install qwen2_5_1_5b_instruct_weights)")
    parser.add_argument("--output", default="models/govcon-adapter")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--learning-rate", type=float, default=0.0002)
    parser.add_argument("--max-seq-length", type=int, default=2048)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    result = run(
        dataset_name=args.dataset,
        model_path=args.model_path,
        output_dir=args.output,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        max_seq_length=args.max_seq_length,
        dry_run=args.dry_run,
    )

    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        status = result.get("status", "unknown")
        print(f"Status: {status}")
        if "metrics" in result:
            m = result["metrics"]
            print(f"Train loss: {m.get('train_loss', 'n/a'):.4f}  "
                  f"Eval loss: {m.get('eval_loss', 'n/a'):.4f}")
        if "adapter_path" in result:
            print(f"Adapter: {result['adapter_path']}")


if __name__ == "__main__":
    _main()
