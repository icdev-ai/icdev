"""Bedrock Fine-Tuning Pipeline — Track A (Production).

Exports govcon-proposals training pairs as JSONL, uploads to GovCloud S3,
triggers an Amazon Bedrock model customization job, polls until complete,
and saves the resulting model ARN to ft_models table.

Requirements (all on PyPI):
  boto3>=1.35.0   botocore>=1.35.0

Environment variables:
  AWS_DEFAULT_REGION           (default: us-gov-west-1)
  BEDROCK_FINETUNE_ROLE_ARN    IAM role ARN Bedrock assumes for the job
  BEDROCK_FINETUNE_S3_BUCKET   S3 bucket for training data and output
  AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN  (or IAM role)

Usage:
  python tools/finetune/bedrock_ft_pipeline.py \\
    --dataset govcon-proposals \\
    --base-model amazon.nova-lite-v1:0 \\
    --s3-bucket s3://my-govcloud-bucket/finetune/ \\
    [--epochs 3] [--dry-run] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402

# Bedrock-supported chat format for customization jobs
_JSONL_CHAT_TEMPLATE = {
    "prompt": "{system}\n\nHuman: {user}\n\nAssistant:",
    "completion": " {output}",
}

_SUPPORTED_BASE_MODELS = [
    "amazon.nova-lite-v1:0",
    "amazon.nova-micro-v1:0",
    "meta.llama3-8b-instruct-v1:0",
    "amazon.titan-text-express-v1",
]


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


def _pairs_to_jsonl(pairs: list[dict]) -> str:
    lines = []
    for p in pairs:
        prompt = f"{p['system_prompt']}\n\nHuman: {p['user_input']}\n\nAssistant:"
        completion = f" {p['expected_output']}"
        lines.append(json.dumps({"prompt": prompt, "completion": completion}))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# S3 upload
# ---------------------------------------------------------------------------

def _upload_to_s3(content: str, s3_uri: str, region: str) -> str:
    """Upload JSONL content to S3. Returns the S3 URI of the uploaded file."""
    try:
        import boto3  # type: ignore
    except ImportError as exc:
        raise RuntimeError("boto3 is required for Track A. pip install boto3") from exc

    bucket, key_prefix = _parse_s3_uri(s3_uri)
    key = f"{key_prefix.rstrip('/')}/govcon-proposals-{uuid.uuid4().hex[:8]}.jsonl"

    s3 = boto3.client("s3", region_name=region)
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=content.encode("utf-8"),
        ContentType="application/jsonl",
        ServerSideEncryption="aws:kms",
    )
    return f"s3://{bucket}/{key}"


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    uri = uri.lstrip("s3://")
    parts = uri.split("/", 1)
    bucket = parts[0]
    prefix = parts[1] if len(parts) > 1 else ""
    return bucket, prefix


# ---------------------------------------------------------------------------
# Bedrock customization job
# ---------------------------------------------------------------------------

def _create_customization_job(
    base_model_id: str,
    training_s3_uri: str,
    output_s3_uri: str,
    role_arn: str,
    region: str,
    epochs: int,
    learning_rate: float,
    batch_size: int,
    job_name: str,
) -> str:
    """Create Bedrock model customization job. Returns jobArn."""
    try:
        import boto3  # type: ignore
    except ImportError as exc:
        raise RuntimeError("boto3 is required for Track A. pip install boto3") from exc

    client = boto3.client("bedrock", region_name=region)
    response = client.create_model_customization_job(
        jobName=job_name,
        customModelName=f"govcon-proposal-model-{uuid.uuid4().hex[:6]}",
        roleArn=role_arn,
        baseModelIdentifier=base_model_id,
        trainingDataConfig={"s3Uri": training_s3_uri},
        outputDataConfig={"s3Uri": output_s3_uri},
        hyperParameters={
            "epochCount": str(epochs),
            "batchSize": str(batch_size),
            "learningRate": str(learning_rate),
        },
        customizationType="FINE_TUNING",
    )
    return response["jobArn"]


def _poll_job(job_arn: str, region: str, poll_interval: int = 60, max_minutes: int = 720) -> dict:
    """Poll customization job until terminal state. Returns final job dict."""
    try:
        import boto3  # type: ignore
    except ImportError as exc:
        raise RuntimeError("boto3 is required for Track A. pip install boto3") from exc

    client = boto3.client("bedrock", region_name=region)
    terminal_states = {"Completed", "Failed", "Stopped"}
    elapsed = 0
    max_seconds = max_minutes * 60

    print(f"Polling job {job_arn} every {poll_interval}s (max {max_minutes}min)...")
    while elapsed < max_seconds:
        job = client.get_model_customization_job(jobIdentifier=job_arn)
        status = job.get("status", "Unknown")
        print(f"  [{elapsed//60}m] Status: {status}")
        if status in terminal_states:
            return job
        time.sleep(poll_interval)
        elapsed += poll_interval

    raise TimeoutError(f"Job {job_arn} did not complete within {max_minutes} minutes")


# ---------------------------------------------------------------------------
# DB registration
# ---------------------------------------------------------------------------

def _register_ft_job(conn, job_arn: str, dataset_name: str, base_model: str) -> str:
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    # Resolve dataset_id
    ds_row = conn.execute(
        "SELECT id FROM ft_datasets WHERE name = %s LIMIT 1",
        (dataset_name,),
    ).fetchone()
    dataset_id = ds_row[0] if ds_row else None
    conn.execute(
        """
        INSERT INTO ft_training_jobs
            (id, dataset_id, provider, base_model, status, cloud_job_id, classification, created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (job_id, dataset_id, "bedrock", base_model, "training", job_arn, "CUI", now),
    )
    return job_id


def _register_ft_model(conn, model_arn: str, job_db_id: str, base_model: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO ft_model_versions
            (id, job_id, model_name, base_model, adapter_path, status, classification, created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            str(uuid.uuid4()), job_db_id,
            "govcon-bedrock-model", base_model,
            model_arn, "created", "CUI", now,
        ),
    )


def _update_ft_job_status(conn, job_db_id: str, status: str, metrics: dict) -> None:
    # Map Bedrock terminal states to ft_training_jobs allowed values
    status_map = {"completed": "completed", "failed": "failed", "stopped": "canceled",
                  "timeout": "failed"}
    db_status = status_map.get(status.lower(), "failed")
    conn.execute(
        "UPDATE ft_training_jobs SET status = %s, error_message = %s WHERE id = %s",
        (db_status, json.dumps(metrics) if db_status != "completed" else None, job_db_id),
    )


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run(
    dataset_name: str = "govcon-proposals",
    base_model: str = "amazon.nova-lite-v1:0",
    s3_bucket: str = "",
    epochs: int = 3,
    learning_rate: float = 0.0001,
    batch_size: int = 4,
    region: str = "",
    dry_run: bool = False,
) -> dict:
    """Run the Bedrock fine-tuning pipeline.

    Returns a result dict with job_arn and status.
    """
    region = region or os.environ.get("AWS_DEFAULT_REGION", "us-gov-west-1")
    role_arn = os.environ.get("BEDROCK_FINETUNE_ROLE_ARN", "")
    s3_bucket = s3_bucket or os.environ.get("BEDROCK_FINETUNE_S3_BUCKET", "")

    if not s3_bucket:
        raise ValueError("S3 bucket required: --s3-bucket or BEDROCK_FINETUNE_S3_BUCKET env var")
    if not role_arn and not dry_run:
        raise ValueError("IAM role ARN required: BEDROCK_FINETUNE_ROLE_ARN env var")

    # 1. Load training pairs
    pairs = _load_training_pairs(dataset_name)
    if not pairs:
        return {"status": "no_data", "dataset": dataset_name, "pairs": 0}

    print(f"Loaded {len(pairs)} training pairs from dataset '{dataset_name}'")

    # 2. Convert to JSONL
    jsonl_content = _pairs_to_jsonl(pairs)

    if dry_run:
        return {
            "dry_run": True,
            "dataset": dataset_name,
            "pairs": len(pairs),
            "base_model": base_model,
            "jsonl_bytes": len(jsonl_content.encode("utf-8")),
            "s3_bucket": s3_bucket,
            "region": region,
        }

    # 3. Upload to S3
    training_s3_uri = _upload_to_s3(jsonl_content, s3_bucket, region)
    output_s3_uri = s3_bucket.rstrip("/") + "/output/"
    print(f"Uploaded training data: {training_s3_uri}")

    # 4. Create Bedrock customization job
    job_name = f"govcon-ft-{uuid.uuid4().hex[:8]}"
    job_arn = _create_customization_job(
        base_model_id=base_model,
        training_s3_uri=training_s3_uri,
        output_s3_uri=output_s3_uri,
        role_arn=role_arn,
        region=region,
        epochs=epochs,
        learning_rate=learning_rate,
        batch_size=batch_size,
        job_name=job_name,
    )
    print(f"Created customization job: {job_arn}")

    # 5. Register job in DB
    conn = get_connection()
    job_db_id = _register_ft_job(conn, job_arn, dataset_name, base_model)

    # 6. Poll until complete
    poll_interval = int(os.environ.get("BEDROCK_POLL_INTERVAL", "60"))
    try:
        final_job = _poll_job(job_arn, region, poll_interval=poll_interval)
    except TimeoutError as e:
        _update_ft_job_status(conn, job_db_id, "timeout", {"error": str(e)})
        return {"status": "timeout", "job_arn": job_arn, "error": str(e)}

    status = final_job.get("status", "Unknown")
    metrics = {
        "trainingMetrics": final_job.get("trainingMetrics", {}),
        "validationMetrics": final_job.get("validationMetrics", {}),
    }

    # 7. Register model ARN if successful
    model_arn = ""
    if status == "Completed":
        model_arn = final_job.get("outputModelArn", "")
        if model_arn:
            _register_ft_model(conn, model_arn, job_db_id, base_model)
            print(f"Model registered: {model_arn}")

    _update_ft_job_status(conn, job_db_id, status.lower(), metrics)

    return {
        "status": status.lower(),
        "job_arn": job_arn,
        "model_arn": model_arn,
        "dataset": dataset_name,
        "pairs": len(pairs),
        "metrics": metrics,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main() -> None:
    parser = argparse.ArgumentParser(description="Bedrock fine-tuning pipeline (Track A)")
    parser.add_argument("--dataset", default="govcon-proposals")
    parser.add_argument("--base-model", default="amazon.nova-lite-v1:0",
                        choices=_SUPPORTED_BASE_MODELS)
    parser.add_argument("--s3-bucket", default="",
                        help="s3://bucket/prefix/ — falls back to BEDROCK_FINETUNE_S3_BUCKET")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--learning-rate", type=float, default=0.0001)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--region", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    result = run(
        dataset_name=args.dataset,
        base_model=args.base_model,
        s3_bucket=args.s3_bucket,
        epochs=args.epochs,
        learning_rate=args.learning_rate,
        batch_size=args.batch_size,
        region=args.region,
        dry_run=args.dry_run,
    )

    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Result: {result.get('status')} — job: {result.get('job_arn', 'n/a')}")
        if result.get("model_arn"):
            print(f"Model ARN: {result['model_arn']}")


if __name__ == "__main__":
    _main()
